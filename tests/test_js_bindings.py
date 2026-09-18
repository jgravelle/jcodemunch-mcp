"""JS/TS/TSX binding declarations: `const`, `let` and `var` (#741, #742).

Two reported defects, one decision behind both — what kind a JS binding carries:

- **#741** a mutable `let` was indexed as `kind="constant"`. `const` and `let`
  are the SAME node type (`lexical_declaration`) and the constant channel
  matched it with no keyword check, so every `let` in every JS/TS file claimed
  to be immutable. The grammar names the keyword in a `kind` FIELD, which is
  the authority this now asks.
- **#742** a `var` yielded no symbol at all. The grammar spells it
  `variable_declaration`, a node type no spec named — #698's shape, one concept
  spelled as two node types with the spec naming one.

⚠⚠ **Outcome-shaped on purpose: every assertion goes through `parse_file` and
names a `(name, kind)` pair, never a node type.** A test that asserts
`variable_patterns` contains `"variable_declaration"` passes on a channel that
is read by nothing — the `type_patterns` shape (#725), and the reason #735 wrote
its own tests this way.

⚠ A destructured binding was a KNOWN GAP here, pinned by
`test_a_destructuring_pattern_is_a_known_separate_gap` so it was disclosed
rather than assumed absent. That test was written to FAIL when the gap closed
and it did (#751); the walk that closed it, and the nine nesting shapes its two
samples could not express, are `tests/test_js_destructured_bindings.py`.

⚠ Three languages, always parametrized. `TYPESCRIPT_SPEC` and `TSX_SPEC` carry
the same entries as `JAVASCRIPT_SPEC`, and a fix applied to one spec reaches
half the product (#698).
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.symbols import KIND_ORDER, VALID_KINDS

LANGUAGES = [("javascript", "a.js"), ("typescript", "a.ts"), ("tsx", "a.tsx")]


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """`parse_file` consults the config gate first.

    ⚠ Patch `jcodemunch_mcp.config`, never the parser module: `parse_file`'s
    import of the gate is function-local, so a patch on the parser's namespace
    resolves nothing (the `cli/policy.py` monkeypatch trap).
    """
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _pairs(source: str, language: str, filename: str) -> set[tuple[str, str]]:
    return {(s.name, s.kind) for s in parse_file(source, filename, language)}


def _named(source: str, language: str, filename: str, name: str) -> list[str]:
    return [s.kind for s in parse_file(source, filename, language) if s.name == name]


# ---------------------------------------------------------------------------
# #741 — the keyword decides the kind, and neither the name nor the node type can
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language,filename", LANGUAGES)
def test_a_mutable_let_is_not_a_constant(language, filename):
    """#741, the reported case, verbatim."""
    pairs = _pairs("const MAX = 1;\nlet counter = 0;\n", language, filename)
    assert ("MAX", "constant") in pairs
    assert ("counter", "variable") in pairs
    assert ("counter", "constant") not in pairs


@pytest.mark.parametrize("language,filename", LANGUAGES)
def test_the_name_is_not_the_discriminator_in_either_direction(language, filename):
    """A SCREAMING_CASE `let` is still mutable; a lowercase `const` is still not.

    Both halves were reported. A fix that reached for a capitalisation
    heuristic would get the first case wrong in the other direction, which is
    the incoherence #732 removed from Kotlin.
    """
    source = "let MUTABLE_CAP = 5;\nconst config = { a: 1 };\n"
    pairs = _pairs(source, language, filename)
    assert ("MUTABLE_CAP", "variable") in pairs
    assert ("config", "constant") in pairs


# ---------------------------------------------------------------------------
# #742 — `var` is a second spelling of the same concept
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language,filename", LANGUAGES)
def test_a_var_declaration_is_a_symbol(language, filename):
    """#742, the reported case, verbatim."""
    source = "const MAX = 1;\nlet counter = 0;\nvar legacy = 2;\n"
    pairs = _pairs(source, language, filename)
    assert ("MAX", "constant") in pairs
    assert ("counter", "variable") in pairs
    assert ("legacy", "variable") in pairs


@pytest.mark.parametrize("language,filename", LANGUAGES)
def test_a_file_written_entirely_in_var_is_not_empty(language, filename):
    """The user-visible complaint: a pre-ES6 module indexed with no bindings."""
    source = "var a = 1;\nvar b = 2;\nvar c = 3;\n"
    pairs = _pairs(source, language, filename)
    assert pairs == {("a", "variable"), ("b", "variable"), ("c", "variable")}


# ---------------------------------------------------------------------------
# One declaration, N names
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language,filename", LANGUAGES)
@pytest.mark.parametrize(
    "source,expected",
    [
        ("const A = 1, B = 2;\n", {("A", "constant"), ("B", "constant")}),
        ("let p = 1, q = 2;\n", {("p", "variable"), ("q", "variable")}),
        ("var r = 1, s = 2;\n", {("r", "variable"), ("s", "variable")}),
        ("const A = 1, B = 2, C = 3;\n",
         {("A", "constant"), ("B", "constant"), ("C", "constant")}),
    ],
)
def test_every_name_a_declaration_binds_is_a_symbol(language, filename, source, expected):
    """⚠⚠ `const A = 1, B = 2;` is ONE node and TWO declarations.

    The JS branch returned on the first `variable_declarator`, so `B` was
    dropped in silence — found while fixing #741 and #742, in the function both
    reports name. It is the same drop #428 fixed for Go, Bash, PHP and Java
    multi-declarator forms and #735 fixed again for Java fields, so the reason
    for asserting it here is that every other N-name language already has it.
    """
    assert _pairs(source, language, filename) == expected


# ---------------------------------------------------------------------------
# Locality: the gate is an allowlist of MEMBER parents
# ---------------------------------------------------------------------------

_LOCAL_SCOPES = [
    ("function body", "function f() { %s }"),
    ("arrow body", "const g = () => { %s };"),
    ("method body", "class C { m() { %s } }"),
    ("bare block", "{ %s }"),
    ("if body", "if (1) { %s }"),
    ("else body", "if (1) {} else { %s }"),
    ("for body", "for (;;) { %s }"),
    ("while body", "while (0) { %s }"),
    ("try body", "try { %s } catch (e) {}"),
    ("catch body", "try {} catch (e) { %s }"),
    ("switch case", "switch (1) { case 1: { %s } }"),
    ("class static block", "class C { static { %s } }"),
]


@pytest.mark.parametrize("language,filename", LANGUAGES)
@pytest.mark.parametrize("scope,template", _LOCAL_SCOPES, ids=[s for s, _ in _LOCAL_SCOPES])
@pytest.mark.parametrize("decl", ["const LOCAL_C = 1;", "let local_l = 1;", "var local_v = 1;"])
def test_a_local_binding_is_not_a_module_symbol(language, filename, scope, template, decl):
    """⚠⚠ A block is not a scope boundary the old gate could see.

    The constant channel keeps locals out with `parent_symbol is None`, and a
    bare block, an `if` body and a `for` body are not symbols — so at file
    scope `if (x) { const BLOCKY = 1; }` published a block-scoped local as
    module state, and today's `let`/`var` fix would have published two more
    spellings of it. Exactly #732's round-3 defect in Kotlin: the scope gate
    alone cannot answer this, so the declaration's own PARENT NODE is asked.
    """
    name = decl.split()[1]
    assert _named(template % decl, language, filename, name) == []


@pytest.mark.parametrize("language,filename", LANGUAGES)
@pytest.mark.parametrize(
    "scope,source,name",
    [
        ("file scope", "let top = 1;", "top"),
        ("export", "export let ex = 1;", "ex"),
        ("export var", "export var exv = 1;", "exv"),
        ("export const", "export const EXC = 1;", "EXC"),
    ],
)
def test_a_module_level_binding_is_a_symbol(language, filename, scope, source, name):
    """The other direction of the allowlist, asserted as firmly (#447's rule).

    A locality predicate that fails closed on an unlisted parent is only safe
    while the listed ones are complete, so each member parent has a case here.
    """
    assert _named(source, language, filename, name) != []


@pytest.mark.parametrize("language,filename", [("typescript", "a.ts"), ("tsx", "a.tsx")])
@pytest.mark.parametrize(
    "scope,source,name",
    [
        ("ambient declaration", "declare const AMB: string;", "AMB"),
        ("namespace body", "namespace NS { let ni = 1; }", "ni"),
        ("declared module body", 'declare module "m" { let mi = 1; }', "mi"),
        # ⚠⚠ `declare global` is the THIRD block owner and the one this file
        # first shipped without a case for: deleting `ambient_declaration` from
        # `_JS_BINDING_MEMBER_BLOCK_OWNERS` left the whole suite green, while
        # every other member position failed loudly. A global augmentation is
        # the shape a `.d.ts` is written in, so the regression it hides drops
        # real declared surface — quietly, because the allowlist fails closed.
        # Found in review.
        ("declare global body", "declare global { let gi = 1; }", "gi"),
        ("declare global const", "declare global { const GC = 1; }", "GC"),
    ],
)
def test_a_typescript_module_level_parent_is_also_a_member_parent(
    language, filename, scope, source, name
):
    """TS spells three more module-level positions, and two of them nest.

    ⚠⚠ A namespace body is a `statement_block` — the SAME node type as a
    function body — so a parent-type allowlist alone cannot tell them apart and
    the grandparent decides. That is why this file has its own parametrization
    rather than a shared one: the discriminator differs by position, and the
    cases that matter are the ones the JS grammar has no shape for.
    """
    assert _named(source, language, filename, name) != []


def test_the_member_parent_gate_is_an_allowlist():
    """⚠⚠ The DIRECTION is the property, and it is what #732 round 3 corrected.

    A denylist of local spellings fails OPEN: an unnamed block form publishes a
    local as module state, which moves published grades. An allowlist fails
    CLOSED: an unnamed member position is the pre-fix status quo for that
    position and nothing lies. `_JS_BINDING_MEMBER_PARENTS` is a frozenset of
    parent node types, so a reader can see the direction without running
    anything.
    """
    from jcodemunch_mcp.parser import extractor

    assert isinstance(extractor._JS_BINDING_MEMBER_PARENTS, frozenset)
    assert "program" in extractor._JS_BINDING_MEMBER_PARENTS
    assert "statement_block" not in extractor._JS_BINDING_MEMBER_PARENTS


# ---------------------------------------------------------------------------
# The two channels are disjoint, and the keyword is the one answer both ask
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language,filename", LANGUAGES)
@pytest.mark.parametrize("decl,name", [
    ("const ONE = 1;", "ONE"),
    ("let two = 2;", "two"),
    ("var three = 3;", "three"),
])
def test_a_declaration_is_emitted_exactly_once(language, filename, decl, name):
    """⚠⚠ `lexical_declaration` is in `constant_patterns` AND `variable_patterns`.

    `_walk_tree` runs the two channels INDEPENDENTLY on the same node, not as
    an `elif`, so two channels deciding separately emit one `const` twice —
    #735's trap in Java, and the reason `js_binding_is_constant` is one shared
    predicate both channels ask rather than a rule transcribed on each side.
    """
    assert _named(decl, language, filename, name) == [
        "constant" if decl.startswith("const") else "variable"
    ]


@pytest.mark.parametrize("language,filename", LANGUAGES)
def test_a_function_valued_binding_stays_a_function(language, filename):
    """Arrow and function expressions belong to `_extract_variable_function`.

    Both channels must keep declining them, or `const fn = () => 1` gains a
    second symbol under a different kind and the same name.
    """
    source = "const fn = () => 1;\nlet fn2 = function () {};\nvar fn3 = function* () {};\n"
    pairs = _pairs(source, language, filename)
    assert ("fn", "function") in pairs
    assert ("fn", "constant") not in pairs
    assert ("fn", "variable") not in pairs
    assert ("fn2", "variable") not in pairs
    assert ("fn3", "variable") not in pairs


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------

def test_variable_is_a_valid_kind():
    """A kind absent from `KIND_ORDER` is refused by `search_symbols`'
    `kind_filter not in VALID_KINDS` check and omitted from the published enum,
    which derives from the same tuple (#571, #732). Emitting one would make the
    symbols unfilterable by the kind they carry.
    """
    assert "variable" in VALID_KINDS
    assert "variable" in KIND_ORDER


def test_the_kind_is_new_and_the_tuple_grew_at_the_end():
    """`KIND_ORDER` is append-only because it is PUBLISHED, in cached bytes.

    The `kind` enum in `search_symbols`' `inputSchema` derives from this tuple
    and sits in the cached prefix of every request, so inserting `variable`
    anywhere but the end rewrites bytes every client already holds — a
    full-rate cache write for every user, for a reorder that buys nothing.
    """
    assert KIND_ORDER[-1] == "variable"
