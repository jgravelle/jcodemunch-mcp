"""A destructured JS/TS binding declares names, and every one of them (#751).

`const { a, b } = obj` is ordinary module surface -- `const { useState } =
React`, `const { readFile } = require('fs/promises')`, and
`export const { GET, POST } = handlers`, which is public API. None of it was
indexed: `_js_declarator_names` required the declarator's `name` field to be an
`identifier`, and a pattern spells that field `object_pattern` or
`array_pattern`, so the declarator was declined and the file indexed with none
of its exports.

⚠⚠ **A pattern NESTS, so the fix is a recursive walk with an explicit accept
list, not a second `identifier` check.** Each nesting is a separate naming
decision and half of them bind something other than the name written first:
`{ a: renamed }` binds `renamed`, `{ a: { b } }` binds `b` ALONE, `[, second]`
has a hole, `{ ...rest }` binds through a `rest_pattern`. A walk that collected
every `identifier` under the pattern would publish `a` for the first two -- a
name that is a property of the right-hand object and is bound to nothing.

⚠⚠ **The same pattern node types appear in a PARAMETER position**, where they
are locals: `function f({ a }) {}` must yield `f` and nothing else. That is not
a special case in the walk, it is the reason the walk is reached only from a
binding declaration; `test_a_parameter_pattern_binds_no_module_symbol` is what
fails if the walk is ever called from the wrong place.

⚠ Three languages, always parametrized -- a fix applied to one spec reaches
half the product (#698).
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file

LANGUAGES = [("javascript", "a.js"), ("typescript", "a.ts"), ("tsx", "a.tsx")]


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """`parse_file` consults the config gate first; patch `jcodemunch_mcp.config`.

    The parser's import of the gate is function-local, so a patch on the parser
    module's namespace resolves nothing (the `cli/policy.py` monkeypatch trap).
    """
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _pairs(source: str, language: str, filename: str) -> set[tuple[str, str]]:
    return {(s.name, s.kind) for s in parse_file(source, filename, language)}


def _names(source: str, language: str, filename: str) -> set[str]:
    return {s.name for s in parse_file(source, filename, language)}


# ---------------------------------------------------------------------------
# The reported case
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language,filename", LANGUAGES)
def test_an_object_pattern_binds_every_name(language, filename):
    """#751's first reported line, verbatim."""
    assert _pairs("const { a, b } = obj;\n", language, filename) == {
        ("a", "constant"),
        ("b", "constant"),
    }


@pytest.mark.parametrize("language,filename", LANGUAGES)
def test_an_array_pattern_binds_every_name(language, filename):
    """#751's second reported line, verbatim. `let` keeps #741's kind."""
    assert _pairs("let [c, d] = arr;\n", language, filename) == {
        ("c", "variable"),
        ("d", "variable"),
    }


# ---------------------------------------------------------------------------
# Each nesting is a separate naming decision
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language,filename", LANGUAGES)
@pytest.mark.parametrize("source,bound,unbound", [
    # `{ a: renamed }` binds the VALUE side. `a` is a property of `obj`.
    ("const { a: renamed } = obj;\n", {"renamed"}, {"a"}),
    # A nested pattern binds only its leaves; `a` is a path, not a binding.
    ("const { a: { b } } = obj;\n", {"b"}, {"a"}),
    ("const { a: [x, y] } = obj;\n", {"x", "y"}, {"a"}),
    # A default binds the name and nothing from the default expression.
    ("const { a = 1 } = obj;\n", {"a"}, {"1"}),
    ("const { a = fallback } = obj;\n", {"a"}, {"fallback"}),
    # A hole binds nothing at its position.
    ("const [, second] = arr;\n", {"second"}, set()),
    # A rest element binds through `rest_pattern`.
    ("const { a, ...rest } = obj;\n", {"a", "rest"}, set()),
    ("const [head, ...tail] = arr;\n", {"head", "tail"}, set()),
    # Shorthand with a default, the single most common React spelling.
    ("const { loading = false, data } = useQuery();\n", {"loading", "data"}, set()),
    # Deep mixed nesting.
    ("const { a: { b: [c, { d }] } } = obj;\n", {"c", "d"}, {"a", "b"}),
])
def test_a_nested_pattern_binds_its_leaves_and_not_its_path(
    language, filename, source, bound, unbound
):
    """⚠⚠ The `unbound` column is the half a leaf-collecting walk gets wrong.

    Every name in it is written in the source and bound to nothing: a key on the
    right-hand object, or an intermediate node on the path to a real binding.
    Publishing one is FABRICATION -- a symbol whose name resolves to no
    declaration, which is worse than the absence this closes, because an absence
    is visible as a missing search result and a fabrication is not.
    """
    got = _names(source, language, filename)
    assert bound <= got, f"missing {bound - got}"
    assert not (unbound & got), f"fabricated {unbound & got}"


# ---------------------------------------------------------------------------
# The same node types in a position that binds a LOCAL
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language,filename", LANGUAGES)
@pytest.mark.parametrize("source,expected", [
    ("function f({ a }) { return a; }\n", {"f"}),
    ("function f([a, b]) { return a + b; }\n", {"f"}),
    ("function f({ a: { b } }) { return b; }\n", {"f"}),
])
def test_a_parameter_pattern_binds_no_module_symbol(language, filename, source, expected):
    """⚠⚠ `object_pattern` in a PARAMETER is a local, and it is the same node
    type this change taught the binder to walk.

    This is the test that fails if the pattern walk is ever reached from
    somewhere other than a binding declaration -- which is the only thing
    keeping the two apart, since the node types are identical.
    """
    assert _names(source, language, filename) == expected


@pytest.mark.parametrize("language,filename", LANGUAGES)
@pytest.mark.parametrize("source", [
    "for (const [k, v] of m) { use(k, v); }\n",
    "for (const { id } of rows) { use(id); }\n",
])
def test_a_for_of_pattern_is_a_local(language, filename, source):
    """The member-parent gate (#741) already excluded this, and a pattern walk
    must not route around it: a `for` header is not a module scope.
    """
    assert _names(source, language, filename) == set()


@pytest.mark.parametrize("language,filename", LANGUAGES)
def test_a_block_scoped_pattern_is_not_a_module_binding(language, filename):
    """#742's leak, in the pattern spelling: a bare block is not a symbol, so
    the `_walk_tree` scope gate cannot see it and `js_binding_is_member` must.
    """
    assert _names("if (x) { const { blocky } = obj; }\n", language, filename) == set()


# ---------------------------------------------------------------------------
# The exported form, which is the reason this is a public-API defect
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language,filename", LANGUAGES)
def test_an_exported_destructured_binding_is_indexed(language, filename):
    """`export const { GET, POST } = handlers` is a Next.js route's entire
    public surface. A file whose exports are all destructured indexed with none
    of them.
    """
    assert _pairs("export const { GET, POST } = handlers;\n", language, filename) == {
        ("GET", "constant"),
        ("POST", "constant"),
    }


@pytest.mark.parametrize("language,filename", LANGUAGES)
def test_var_destructuring_carries_the_variable_kind(language, filename):
    """`var` is a third node type (`variable_declaration`, #742) and the pattern
    walk must be reached from it too -- the #698 shape, where one concept is
    spelled as two node types and a fix names one.
    """
    assert _pairs("var { v1, v2 } = obj;\n", language, filename) == {
        ("v1", "variable"),
        ("v2", "variable"),
    }


# ---------------------------------------------------------------------------
# The consequence: a destructured import is a symbol, and it CROWDS
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language,filename", LANGUAGES)
def test_a_destructured_import_binding_is_a_symbol(language, filename):
    """`const { readFile } = require('fs/promises')` is named in #751 as a case
    that must index. It does.
    """
    source = "const { readFile } = require('fs/promises');\n"
    assert _pairs(source, language, filename) == {("readFile", "constant")}


def test_a_destructured_import_does_not_displace_what_it_imports():
    """⚠⚠ **Found by the FULL tier, not by the touched files.**
    `tests/test_call_graph_ast.py::test_js_call_hierarchy` broke: it looked up
    `process` by bare name across the index, and since this change
    `const { process } = require('./service')` in `main.js` is ALSO a symbol
    named `process`, so the lookup became ambiguous and answered with the
    import.

    ⚠ The new symbol is correct -- #751 names this spelling explicitly -- and
    the crowding is the real cost of indexing it, the #699 shape one axis over.
    What must hold is that both survive with distinct ids and files, so a
    consumer can tell them apart; the test that broke now selects by file.
    """
    importer = parse_file(
        "const { process } = require('./service');\n", "main.js", "javascript"
    )
    defining = parse_file("function process() { return 1; }\n", "service.js", "javascript")

    imported = [s for s in importer if s.name == "process"]
    defined = [s for s in defining if s.name == "process"]
    assert len(imported) == 1 and len(defined) == 1
    assert imported[0].kind == "constant"
    assert defined[0].kind == "function"
    assert imported[0].id != defined[0].id
    assert imported[0].file != defined[0].file


# ---------------------------------------------------------------------------
# The depth cap, which is a silent drop and therefore needs a boundary
# ---------------------------------------------------------------------------

def _nested(depth: int) -> str:
    """`const { a: { a: ... { z } ... } } = o;` nested `depth` levels."""
    return "const " + "{ a: " * depth + "{ z }" + " }" * depth + " = o;\n"


@pytest.mark.parametrize("depth,found", [(1, True), (8, True), (15, True), (16, False)])
def test_the_pattern_depth_cap_is_where_it_says_it_is(depth, found):
    """⚠⚠ Past `_MAX_BINDING_PATTERN_DEPTH` the walk returns NOTHING, silently
    -- which is the absence this change exists to close, one depth over.

    ⚠⚠ **The boundary is MEASURED, and it is not the constant.**
    `_MAX_BINDING_PATTERN_DEPTH` is 32 and the last depth that resolves is
    **15**, because one level of `{ a: ... }` costs TWO walk steps
    (`object_pattern` then `pair_pattern`). A test asserting 32 would have been
    written from the constant rather than from the behaviour, and would have
    passed while describing the wrong number.

    The cap is a stack guard, not a rule about JavaScript, and 15 levels of
    nesting does not occur in real code. It is pinned anyway because an unpinned
    silent drop is indistinguishable from the defect, and because a later change
    to the constant would otherwise move the boundary unobserved.
    """
    assert (_names(_nested(depth), "javascript", "a.js") == {"z"}) is found


# ---------------------------------------------------------------------------
# Blast radius: what was already right stays right
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language,filename", LANGUAGES)
def test_plain_bindings_are_unchanged(language, filename):
    assert _pairs("const plain = 1;\nlet mut = 2;\nvar old = 3;\n", language, filename) == {
        ("plain", "constant"),
        ("mut", "variable"),
        ("old", "variable"),
    }


@pytest.mark.parametrize("language,filename", LANGUAGES)
def test_a_function_valued_binding_is_still_one_symbol(language, filename):
    """`_extract_variable_function` owns `const fn = () => {}` and emits it as a
    `function`; the binding channels decline it so one declaration does not get
    two symbols under two kinds. A pattern walk must not reopen that.
    """
    pairs = _pairs("const fn = () => {};\n", language, filename)
    assert ("fn", "function") in pairs
    assert ("fn", "constant") not in pairs
    assert ("fn", "variable") not in pairs


@pytest.mark.parametrize("language,filename", LANGUAGES)
def test_multiple_declarators_still_all_bind(language, filename):
    """`const A = 1, B = 2` is ONE node and TWO declarations (#428's lesson,
    which JS got in #741). A mixed line proves the walk did not replace the
    per-declarator loop with a per-node one.
    """
    assert _pairs("const A = 1, { b } = obj, [c] = arr;\n", language, filename) == {
        ("A", "constant"),
        ("b", "constant"),
        ("c", "constant"),
    }
