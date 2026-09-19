"""A Vue or Svelte `<script>` block's bindings, and what kind each carries (#752).

A component's module-scope state is where its logic lives, and almost none of it
was indexed. `_parse_vue_symbols` and `_parse_svelte_symbols` matched specific
FRAMEWORK shapes -- a Svelte 4 `export let` prop, a rune, a Vue macro call --
and emitted them through a local helper that hardcoded `kind="constant"`. An
ordinary `let count = 0` matched no framework shape and fell through; the shapes
that did match were published as constants whatever keyword declared them.

⚠⚠ **A Svelte prop was the worst case of the wrong kind: it is the MOST mutable
binding in the file.** The parent component assigns it. Publishing `export let
name` as `constant` is the defect #741 fixed for plain JS and #732 refused for
Kotlin `var`, surviving in a third extractor because these two reach the kind
decision by their own route -- which is also why the `constant_patterns` sweep
that found the JS family and Kotlin could not see them.

⚠⚠ **The verdict on a prop, which #752 left open: a prop is `property`.** It is
a declared input on a component these extractors ALREADY model as a class (every
script binding carries the component symbol as its `parent`), and `property` is
this vocabulary's word for declared member state (#732, #743). `constant` is
false, and `variable` would erase the distinction between an input the parent
sets and internal state the component owns. Vue's `const props =
defineProps([...])` stays `constant`: that binding is an ordinary `const` holding
the props object, and the individual props inside the macro argument are not
script bindings -- they are out of this issue's scope and stay absent.

⚠⚠ **These extractors are the `inline` half of #724's grammar inventory**, so
nothing here is gated by the spec-keyed tests that cover the JS family. Every
assertion goes through `parse_file`.

⚠ `svelte` is DISABLED in this developer box's config, so an unpatched probe
returns `[]` and reads exactly like a grammar failure. The gate is patched per
test; see `this box disables nim in config` -- svelte is the second language.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.symbols import KIND_ORDER, VALID_KINDS

SVELTE_4 = (
    "<script>\n"
    "  export let name;\n"
    "  let count = 0;\n"
    "  const MAX = 5;\n"
    "  var legacy = 1;\n"
    "</script>\n"
)

VUE_SETUP = (
    "<script setup>\n"
    "const props = defineProps(['a'])\n"
    "let count = 0\n"
    "const MAX = 5\n"
    "var legacy = 1\n"
    "</script>\n"
)


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _pairs(source: str, filename: str, language: str) -> set[tuple[str, str]]:
    return {(s.name, s.kind) for s in parse_file(source, filename, language)}


def _kinds(source: str, filename: str, language: str, name: str) -> list[str]:
    return [s.kind for s in parse_file(source, filename, language) if s.name == name]


# ---------------------------------------------------------------------------
# The reported cases, verbatim
# ---------------------------------------------------------------------------

def test_a_svelte_script_indexes_every_binding():
    """#752's first reported block. Before: `{('C','class'), ('name','constant')}`."""
    assert _pairs(SVELTE_4, "C.svelte", "svelte") == {
        ("C", "class"),
        ("name", "property"),
        ("count", "variable"),
        ("MAX", "constant"),
        ("legacy", "variable"),
    }


def test_a_vue_script_indexes_every_binding():
    """#752's second reported block. Before: `{('C','class'), ('props','constant')}`."""
    assert _pairs(VUE_SETUP, "C.vue", "vue") == {
        ("C", "class"),
        ("props", "constant"),
        ("count", "variable"),
        ("MAX", "constant"),
        ("legacy", "variable"),
    }


# ---------------------------------------------------------------------------
# The kind, which is the half that was wrong rather than absent
# ---------------------------------------------------------------------------

def test_an_exported_destructuring_is_not_a_prop_declaration():
    """⚠ Svelte declares a prop with a PLAIN IDENTIFIER. `export let { p1, p2 }
    = obj` is an exported destructuring, not two prop declarations, and the
    first fix published both as props because it decided per STATEMENT instead
    of per declarator.
    """
    source = "<script>\n  export let { p1, p2 } = obj;\n  export let real;\n</script>\n"
    pairs = _pairs(source, "C.svelte", "svelte")
    assert ("p1", "variable") in pairs
    assert ("p2", "variable") in pairs
    assert ("real", "property") in pairs
    assert ("p1", "property") not in pairs


def test_a_svelte_4_prop_is_not_a_constant():
    """The parent assigns it. `constant` is the one kind it cannot be."""
    assert _kinds(SVELTE_4, "C.svelte", "svelte", "name") == ["property"]


def test_a_rune_prop_is_not_a_constant():
    """`let { a, b } = $props()` is the Svelte 5 spelling of the same input."""
    source = "<script>\n  let { a, b } = $props();\n</script>\n"
    assert _pairs(source, "C.svelte", "svelte") == {
        ("C", "class"),
        ("a", "property"),
        ("b", "property"),
    }


def test_rune_state_is_mutable_and_derived_is_not():
    """⚠ The keyword decides, not the rune: `$state` is reached by `let` and
    `$derived` by `const`, so asking the shared `js_binding_is_constant` gets
    both right and a per-rune table would be a fourth transcription of #741.
    """
    source = (
        "<script>\n"
        "  let count = $state(0);\n"
        "  const doubled = $derived(count * 2);\n"
        "</script>\n"
    )
    assert _pairs(source, "C.svelte", "svelte") == {
        ("C", "class"),
        ("count", "variable"),
        ("doubled", "constant"),
    }


def test_a_vue_reactive_binding_keeps_its_keyword_kind():
    source = (
        "<script setup>\n"
        "const total = computed(() => 1)\n"
        "let live = ref(0)\n"
        "</script>\n"
    )
    assert _pairs(source, "C.vue", "vue") == {
        ("C", "class"),
        ("total", "constant"),
        ("live", "variable"),
    }


# ---------------------------------------------------------------------------
# The destructuring walk (#751) reaches these extractors too
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename,language,open_tag", [
    ("C.svelte", "svelte", "<script>"),
    ("C.vue", "vue", "<script setup>"),
])
def test_a_destructured_script_binding_is_indexed(filename, language, open_tag):
    """⚠ The same recursive pattern walk, asked by a third and fourth caller.
    A per-extractor copy is how #751 would come back here alone.
    """
    source = f"{open_tag}\nconst {{ x, y }} = point;\nlet [p, q] = pair;\n</script>\n"
    pairs = _pairs(source, filename, language)
    assert ("x", "constant") in pairs
    assert ("y", "constant") in pairs
    assert ("p", "variable") in pairs
    assert ("q", "variable") in pairs


@pytest.mark.parametrize("filename,language,open_tag", [
    ("C.svelte", "svelte", "<script>"),
    ("C.vue", "vue", "<script setup>"),
])
@pytest.mark.parametrize("body,fires_on_a_missing_gate", [
    ("if (x) { const blocky = 1; }", True),
    ("try { const caught = 1; } catch (e) { report(e); }", True),
    # ⚠ Measured: this one passes with the gate REMOVED too -- a `for...of`
    # header is not a `lexical_declaration` child of the block, so it never
    # reached the widened branch. Kept as a true assertion about the spelling,
    # recorded as NOT load-bearing for the gate so nobody reads three rows of
    # coverage where there are two.
    ("for (const item of list) { use(item); }", False),
])
def test_a_block_scoped_script_binding_is_not_a_component_symbol(
    filename, language, open_tag, body, fires_on_a_missing_gate
):
    """⚠⚠ Widening these branches from "framework shapes only" to "every
    binding" REMOVED the accident that kept locals out.

    The old gate was the rune / macro-call check: a block-scoped `const` had no
    rune on its right-hand side, so it fell through and was never published.
    Asking for every binding means the locality rule has to be asked
    EXPLICITLY, which is `js_binding_is_member` -- #741's member gate, reused
    rather than re-derived.

    ⚠ This row was MISSING from the first version of this file and a planted
    removal of both gates passed all seventeen of its tests. The blast-radius
    rows could not see it because they only assert what IS published.
    """
    source = f"{open_tag}\n{body}\n</script>\n"
    names = {s.name for s in parse_file(source, filename, language)}
    assert names == {filename.split(".")[0]}, f"leaked {names}"


@pytest.mark.parametrize("filename,language,open_tag", [
    ("C.svelte", "svelte", "<script>"),
    ("C.vue", "vue", "<script setup>"),
])
def test_a_script_parameter_pattern_binds_no_symbol(filename, language, open_tag):
    """The anti-fabrication row, in the framework position."""
    source = f"{open_tag}\nfunction f({{ a }}) {{ return a; }}\n</script>\n"
    names = {s.name for s in parse_file(source, filename, language)}
    assert "a" not in names


# ---------------------------------------------------------------------------
# Blast radius: the component itself, and what the extractors already did
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source,filename,language", [
    (SVELTE_4, "C.svelte", "svelte"),
    (VUE_SETUP, "C.vue", "vue"),
])
def test_every_binding_is_parented_to_the_component(source, filename, language):
    """The component class is the parent, which is what makes `property` the
    right word for a prop rather than an invention.
    """
    syms = parse_file(source, filename, language)
    comp = next(s for s in syms if s.kind == "class")
    assert comp.parent is None
    assert all(s.parent == comp.id for s in syms if s is not comp)


def test_a_svelte_reactive_declaration_is_still_indexed():
    """`$: doubled = count * 2` is an assignment, not a binding declaration, so
    it has no keyword to ask. It keeps the kind it had.
    """
    source = "<script>\n  let count = 0;\n  $: doubled = count * 2;\n</script>\n"
    assert ("doubled", "constant") in _pairs(source, "C.svelte", "svelte")


def test_a_svelte_typescript_block_still_indexes_types():
    source = (
        '<script lang="ts">\n'
        "  interface P { a: string }\n"
        "  let count: number = 0;\n"
        "</script>\n"
    )
    pairs = _pairs(source, "C.svelte", "svelte")
    assert ("P", "type") in pairs
    assert ("count", "variable") in pairs


def test_a_script_function_is_not_rebound_as_a_binding():
    source = "<script>\n  const fn = () => {};\n</script>\n"
    pairs = _pairs(source, "C.svelte", "svelte")
    assert ("fn", "constant") not in pairs
    assert ("fn", "variable") not in pairs


@pytest.mark.parametrize("source,expected", [
    ("<script>\n  export const load = async ({ params }) => {};\n</script>\n",
     ("load", "constant")),
    ("<script>\n  export const handler = function () {};\n</script>\n",
     ("handler", "constant")),
    # An exported `let` holding a callback is a prop like any other `export let`.
    ("<script>\n  export let onClick = () => {};\n</script>\n", ("onClick", "property")),
])
def test_an_exported_function_valued_binding_is_still_a_symbol(source, expected):
    """⚠⚠ **A regression this PR introduced and review caught, measured on both
    refs.** The first draft copied the JS binder's "decline a function-valued
    declarator" line into the Svelte export branch. In the JS binder that is a
    HAND-OFF -- `_extract_variable_function` emits it as a `function`. This
    walker has no such branch, so the line deleted the symbol outright:
    `export const load = async () => {}` -- SvelteKit's `load`, a module's whole
    API -- was `('load', 'constant')` on `origin/main` and NOTHING at HEAD.

    ⚠ The lesson is that borrowing a guard also borrows the owner it assumes,
    and that an absence introduced by an absence-closing change is exactly the
    kind nobody goes looking for.
    """
    assert expected in _pairs(source, "C.svelte", "svelte")


def test_a_local_function_binding_is_a_disclosed_gap():
    """⚠ A LOCAL `const fn = () => {}` in a script block yields no symbol, and
    did not on `origin/main` either -- measured, both refs.

    Pinned so it is disclosed rather than assumed absent, and so the asymmetry
    with the exported form above is written down: nothing here emits a
    function-valued declarator as a `function`, and emitting it as a
    `constant` would name a function with a data kind.
    """
    for source in (
        "<script>\n  const fn = () => {};\n</script>\n",
        "<script>\n  const fn = function () {};\n</script>\n",
    ):
        assert {s.name for s in parse_file(source, "C.svelte", "svelte")} == {"C"}


# ---------------------------------------------------------------------------
# The third framework, which needed no code change
# ---------------------------------------------------------------------------

def test_astro_frontmatter_inherited_the_fix_with_no_astro_change():
    """⚠⚠ Astro was ALREADY asking the shared binder, so #751's pattern walk
    reached it for free and #752's kind rule was never wrong there.

    That is the whole argument for fixing this one layer down rather than in
    each extractor: the difference between Astro and the other two is not the
    framework, it is that Vue and Svelte each kept their own copy of the kind
    decision and the name walk. This test is what fails if a later change gives
    Astro a fourth copy.
    """
    source = (
        "---\n"
        "const { title } = Astro.props;\n"
        "let count = 0;\n"
        "const MAX = 5;\n"
        "var legacy = 1;\n"
        "---\n"
        "<h1>{title}</h1>\n"
    )
    pairs = _pairs(source, "C.astro", "astro")
    assert ("title", "constant") in pairs
    assert ("count", "variable") in pairs
    assert ("MAX", "constant") in pairs
    assert ("legacy", "variable") in pairs


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------

def test_property_is_a_valid_kind():
    """A kind absent from `KIND_ORDER` is refused by `search_symbols`'
    `kind_filter not in VALID_KINDS` check and omitted from the published enum
    (#571, #732), so these symbols would be unfilterable by the kind they carry.
    """
    assert "property" in VALID_KINDS
    assert "property" in KIND_ORDER
