"""A JS/TS class field initializer no longer donates its members to the class.

    class Host {
      handlers = { onDone(){} };
      real(){}
    }

used to yield `Host.onDone` as a `method` -- a member `Host` does not declare.
The tree-sitter JS grammar spells object-literal shorthand and a real class
method with the SAME node type (`method_definition`); the only discriminator is
the parent, and a field initializer was not treated as a boundary. A free
`function_declaration` inside an arrow initializer had the same symptom by a
second, independent route: `parent_is_container` survived into the initializer
and promoted it to a method.

Sharpest consequence, and the reason this is worse than a spurious name: when a
phantom COLLIDES with a real method, the disambiguator hands `~1` to the phantom
and `~2` to the real one, so a real symbol's id depended on the phantom
existing. `test_collision_no_longer_shifts_the_real_method_id` pins that.

⚠ Bucket B is DELIBERATELY untouched: an object literal inside a FUNCTION keeps
`pluginCreator.prepare`. That is ordinary lexical nesting -- the same shape
Python already emits for `Host.real.inner` -- not a defect. Reclassifying it
would move ~190 symbols in Next.js alone for a debatable gain.

⚠ Measured, not assumed: over 2,670 files of NestJS/Next.js/React the class
attribution bucket occurred ZERO times, and re-measured here over the local
JS/TS corpus it changes zero symbols. The fix RENAMES rare symbols and removes
none, which is why it ships without a `PARSER_GENERATION` bump -- see
`test_fix_renames_and_never_removes`.
"""
from __future__ import annotations

import pytest

from jcodemunch_mcp.parser import extractor as ex
from jcodemunch_mcp.parser.extractor import parse_file

JS_LANGS = [("phantom.js", "javascript"), ("phantom.ts", "typescript"), ("phantom.tsx", "tsx")]


def _qnames(src: str, filename: str, language: str) -> dict[str, str]:
    """qualified_name -> kind for every symbol in *src*."""
    return {s.qualified_name: s.kind for s in parse_file(src, filename, language)}


# --------------------------------------------------------------------------
# Bucket A -- object-literal shorthand in a class field initializer
# --------------------------------------------------------------------------

@pytest.mark.parametrize("filename,language", JS_LANGS)
def test_object_literal_method_is_not_a_class_member(filename, language):
    src = "class Host { handlers = { onDone(){ return 1 } }; real(){ return 3 } }"
    names = _qnames(src, filename, language)

    assert "Host.onDone" not in names, (
        "`Host.onDone` claims a member the class does not declare"
    )
    assert "Host.handlers.onDone" in names, "the method should nest under its field"
    assert names["Host.real"] == "method", "a declared class method must be unaffected"


@pytest.mark.parametrize("filename,language", JS_LANGS)
def test_grammar_field_name_difference_is_handled(filename, language):
    """⚠ JS exposes the field's key as `property`, TS/TSX as `name`.

    Reading only one leaves the other language silently unfixed, which is
    exactly what happened first: the JS case passed while TS and TSX still
    emitted phantoms. Hence the parametrisation across all three.
    """
    names = _qnames("class H { h = { d(){} }; r(){} }", filename, language)
    assert "H.d" not in names
    assert "H.h.d" in names


# --------------------------------------------------------------------------
# Bucket C -- a free function promoted by the container flag
# --------------------------------------------------------------------------

@pytest.mark.parametrize("filename,language", JS_LANGS)
def test_free_function_in_field_initializer_is_not_promoted(filename, language):
    src = "class T { cb = () => { function innerFn(){ return 0 } } }"
    names = _qnames(src, filename, language)

    assert "T.innerFn" not in names, "a free function is not a class method"
    assert names.get("T.cb.innerFn") == "function", (
        "it should stay a function, nested under the field it lives in"
    )


# --------------------------------------------------------------------------
# The id shift
# --------------------------------------------------------------------------

def test_collision_no_longer_shifts_the_real_method_id():
    """Before the fix these shared a qualified name and were disambiguated
    `~1` (phantom) and `~2` (real), making the real method's id depend on the
    phantom. Distinct names mean no disambiguator runs at all."""
    src = "class Host { handlers = { real(){} }; real(){ return 2 } }"
    ids = [s.id for s in parse_file(src, "phantom.js", "javascript")]

    assert "phantom.js::Host.real#method" in ids
    assert not any("~" in i for i in ids), f"disambiguator still fired: {ids}"


# --------------------------------------------------------------------------
# Non-vacuity in the other direction: what must NOT change
# --------------------------------------------------------------------------

def test_object_literal_in_a_function_is_left_alone():
    """Bucket B. Not a defect; an over-broad fix would break ~190 real symbols."""
    names = _qnames(
        "function pluginCreator(){ return { prepare(){ return 1 } } }",
        "phantom.js",
        "javascript",
    )
    assert names.get("pluginCreator.prepare") == "method"


def test_object_literal_in_a_method_body_is_left_alone():
    names = _qnames(
        "class Ok { m(){ const o = { inner(){} }; return o } }", "phantom.js", "javascript"
    )
    assert names.get("Ok.m.inner") == "method"
    assert names.get("Ok.m") == "method"


@pytest.mark.parametrize("filename,language", JS_LANGS)
def test_declared_members_of_every_shape_survive(filename, language):
    """A fix that suppressed real members would pass every absence test above."""
    src = "class P { a(){} static c(){} get d(){} set e(v){} }"
    names = _qnames(src, filename, language)
    for member in ("P.a", "P.c", "P.d", "P.e"):
        assert names.get(member) == "method", f"{member} missing: {names}"


# --------------------------------------------------------------------------
# Edge cases in the field key itself
# --------------------------------------------------------------------------

def test_computed_and_private_field_keys_still_block_class_attribution():
    """Neither key is a resolvable identifier, and both are kept verbatim --
    the same treatment a computed METHOD name already gets. What matters is
    that the class does not absorb the members."""
    computed = _qnames("class C { [key] = { z(){} }; ok(){} }", "p.js", "javascript")
    assert "C.z" not in computed
    assert "C.[key].z" in computed
    assert computed.get("C.ok") == "method"

    private = _qnames("class C { #secret = { z(){} }; ok(){} }", "p.js", "javascript")
    assert "C.z" not in private
    assert "C.#secret.z" in private


# --------------------------------------------------------------------------
# The property that lets this ship without a PARSER_GENERATION bump
# --------------------------------------------------------------------------

def test_fix_renames_and_never_removes():
    """⚠⚠ This test carries the RELEASE argument, not just behaviour.

    A parser change that DELETES symbols needs a `PARSER_GENERATION` bump,
    because existing indexes keep rows no incremental path will revisit. This
    fix deletes nothing -- the same symbols come back under corrected names --
    and the corrected shape occurs zero times in 2,670 files of real framework
    code. Forcing every user a full re-parse to rename nothing is the bad half
    of that trade.

    If this ever starts failing, the fix has begun removing symbols and the
    no-bump decision has to be revisited before release.
    """
    src = (
        "class Host {\n"
        "  handlers = { onDone(){ return 1 }, onFail(){ return 2 } };\n"
        "  cb = () => { function innerFn(){ return 0 }; return innerFn };\n"
        "  real(){ return 3 }\n"
        "}\n"
    )
    after = parse_file(src, "planted.ts", "typescript")

    saved = ex._JS_CLASS_FIELD_NODE_TYPES
    try:
        ex._JS_CLASS_FIELD_NODE_TYPES = frozenset()  # the pre-fix walker
        before = parse_file(src, "planted.ts", "typescript")
    finally:
        ex._JS_CLASS_FIELD_NODE_TYPES = saved

    # 7 since #781, which indexes the two FIELDS themselves (`handlers`, `cb`)
    # on both sides of the toggle; it was 5. The property is the equality: the
    # #571 walker renames what a field holds and removes nothing.
    assert len(before) == len(after) == 7, (
        f"symbol COUNT changed: {len(before)} -> {len(after)}. A removal means "
        f"this needs a PARSER_GENERATION bump."
    )
    # Non-vacuity: the toggle must actually move something, or the assertion
    # above would hold for a fix that does nothing at all.
    assert {s.qualified_name for s in before} != {s.qualified_name for s in after}
