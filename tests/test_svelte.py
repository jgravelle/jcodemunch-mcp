"""Focused Svelte (.svelte) regression tests.

Blueprint: tests/test_astro.py. Svelte SFCs are parsed by mirroring the
tree-sitter Vue parser — each <script> block's raw_text is re-parsed with the
JS/TS grammar, so symbols carry language="svelte" (parity with Vue's "vue").
"""

from pathlib import Path

from jcodemunch_mcp.parser import parse_file
from jcodemunch_mcp.parser.imports import extract_imports
from jcodemunch_mcp.parser.languages import (
    LANGUAGE_EXTENSIONS,
    LANGUAGE_REGISTRY,
    get_language_for_path,
)
from jcodemunch_mcp.tools.index_folder import discover_local_files


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "svelte"


def _read_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Registry wiring
# ---------------------------------------------------------------------------

def test_svelte_extension_and_registry_present():
    assert LANGUAGE_EXTENSIONS.get(".svelte") == "svelte"
    assert "svelte" in LANGUAGE_REGISTRY
    assert get_language_for_path("src/lib/Counter.svelte") == "svelte"


# ---------------------------------------------------------------------------
# 2. Synthetic component symbol
# ---------------------------------------------------------------------------

def test_svelte_component_symbol_created():
    symbols = parse_file("<script>let n = 0;</script>\n<div />", "src/Button.svelte", "svelte")
    comp = [s for s in symbols if s.kind == "class" and s.name == "Button"]
    assert len(comp) == 1
    assert comp[0].line == 1
    assert comp[0].signature == "component Button"


def test_svelte_no_script_returns_empty():
    # A markup-only .svelte file has no <script> → no symbols (mirrors Vue).
    assert parse_file("<h1>hi</h1>\n", "src/Static.svelte", "svelte") == []


# ---------------------------------------------------------------------------
# 3. Svelte 5 runes
# ---------------------------------------------------------------------------

def test_svelte5_runes_carry_the_keyword_kind():
    """The name said `become_constants`, which was the defect (#752)."""
    src = (
        "<script>\n"
        "  let count = $state(0);\n"
        "  let doubled = $derived(count * 2);\n"
        "  let big = $derived.by(() => count * 100);\n"
        "</script>\n"
    )
    by_name = {s.name: s for s in parse_file(src, "src/R.svelte", "svelte")}
    # ⚠ #752: the KEYWORD decides, not the rune. All three are declared with
    # `let`, and a rune-keyed table would be a fourth transcription of #741's
    # rule -- `$state` is reached by `let` and `$derived` by either.
    assert by_name["count"].kind == "variable"
    assert by_name["doubled"].kind == "variable"
    # $derived.by member form still resolves to the rune.
    assert by_name["big"].kind == "variable"
    assert {s.name for s in parse_file(
        "<script>\n  const doubled = $derived(1);\n</script>\n", "src/D.svelte", "svelte"
    ) if s.kind == "constant"} == {"doubled"}


def test_svelte5_destructured_props_each_surface():
    src = (
        "<script>\n"
        "  let { name, count = 0, ...rest } = $props();\n"
        "</script>\n"
    )
    names = {s.name for s in parse_file(src, "src/P.svelte", "svelte")}
    assert "name" in names
    assert "count" in names
    assert "rest" not in names  # ...rest is not a named prop


# ---------------------------------------------------------------------------
# 4. Svelte 4 props
# ---------------------------------------------------------------------------

def test_svelte4_export_let_is_a_prop_and_export_const_is_not():
    """⚠⚠ #752 corrected the FIRST half and this test carried the SECOND.

    `export let` is a prop: the parent assigns it, so `constant` was the one
    kind it could not be. `export const` is a readonly export Svelte does NOT
    let the parent set -- it is not a prop at all, and the first draft of the
    fix made it one. The keyword tells them apart, so both come off the same
    authority (#741).
    """
    src = "<script>\n  export let title;\n  export const MAX = 5;\n</script>\n"
    by_name = {s.name: s for s in parse_file(src, "src/Old.svelte", "svelte")}
    assert by_name["title"].kind == "property"
    assert by_name["MAX"].kind == "constant"


def test_svelte4_reactive_label_is_constant():
    src = "<script>\n  let count = $state(1);\n  $: doubled = count * 2;\n</script>\n"
    by_name = {s.name: s for s in parse_file(src, "src/Label.svelte", "svelte")}
    assert by_name["doubled"].kind == "constant"


# ---------------------------------------------------------------------------
# 5. Functions and TS types
# ---------------------------------------------------------------------------

def test_svelte_function_and_ts_type():
    src = (
        '<script lang="ts">\n'
        "  interface Props { label: string; }\n"
        "  function greet(name: string): string { return name; }\n"
        "  class Helper {}\n"
        "</script>\n"
    )
    by_name = {s.name: s for s in parse_file(src, "src/F.svelte", "svelte")}
    assert by_name["Props"].kind == "type"
    assert by_name["greet"].kind == "function"
    assert by_name["Helper"].kind == "class"


# ---------------------------------------------------------------------------
# 6. Two script blocks (instance + module)
# ---------------------------------------------------------------------------

def test_svelte_module_and_instance_scripts_both_parsed():
    symbols = parse_file(_read_fixture("Counter.svelte"), "src/Counter.svelte", "svelte")
    by_name = {s.name: s for s in symbols}

    # module block export
    assert "prerender" in by_name
    assert by_name["prerender"].line == 2  # inside <script context="module">
    # instance block symbols
    assert "count" in by_name
    assert "increment" in by_name
    assert by_name["increment"].kind == "function"
    # instance-block line offset is well past the module block
    assert by_name["count"].line > by_name["prerender"].line


# ---------------------------------------------------------------------------
# 7. Symbols carry language="svelte"
# ---------------------------------------------------------------------------

def test_svelte_symbols_report_svelte_language():
    symbols = parse_file(_read_fixture("Counter.svelte"), "src/Counter.svelte", "svelte")
    assert symbols  # non-empty
    # Parity with Vue: language is the SFC language, NOT the inner typescript.
    assert all(s.language == "svelte" for s in symbols)


# ---------------------------------------------------------------------------
# 8. Import extraction
# ---------------------------------------------------------------------------

def test_svelte_imports_include_esm_and_component_usage():
    imports = extract_imports(_read_fixture("Counter.svelte"), "src/Counter.svelte", "svelte")
    specifiers = {edge["specifier"] for edge in imports}

    # ESM imports from <script>
    assert "./Display.svelte" in specifiers
    assert "svelte" in specifiers
    # Component used in markup but not imported → synthetic edge
    assert "UserBadge" in specifiers

    # <Display /> is already imported → not duplicated as a synthetic edge
    display_edges = [e for e in imports if e["specifier"] == "Display"]
    assert display_edges == []


def test_svelte_component_in_comment_is_not_an_edge():
    imports = extract_imports(_read_fixture("Counter.svelte"), "src/Counter.svelte", "svelte")
    specifiers = {edge["specifier"] for edge in imports}
    assert "Ghost" not in specifiers


def test_svelte_ts_generics_are_not_component_edges():
    # `<script>` TS generics (identity<T>, Array<Item>, Writable<AppState>) must
    # NOT be misread as component tags — the tag scan strips <script>/<style> bodies.
    content = (
        '<script lang="ts">\n'
        "  import { writable, type Writable } from 'svelte/store';\n"
        "  function identity<T>(x: T): T { return x; }\n"
        "  let store: Writable<AppState> = writable();\n"
        "  const items: Array<Item> = [];\n"
        "</script>\n"
        "<main>\n"
        "  <NavBar />\n"
        "</main>\n"
    )
    specifiers = {edge["specifier"] for edge in extract_imports(content, "src/Gen.svelte", "svelte")}
    # Real import + real markup component survive.
    assert "svelte/store" in specifiers
    assert "NavBar" in specifiers
    # Generic type params / type args must not become synthetic import edges.
    assert "T" not in specifiers
    assert "AppState" not in specifiers
    assert "Item" not in specifiers


# ---------------------------------------------------------------------------
# 9. End-to-end discovery
# ---------------------------------------------------------------------------

def test_discovery_indexes_svelte_not_wrong_extension(tmp_path):
    (tmp_path / "Counter.svelte").write_text(_read_fixture("Counter.svelte"), encoding="utf-8")
    (tmp_path / "plain.ts").write_text("export const k = 1;\n", encoding="utf-8")

    files, _warnings, skip_counts = discover_local_files(tmp_path.resolve())
    names = {p.name for p in files}

    assert "Counter.svelte" in names
    assert "plain.ts" in names
    assert skip_counts.get("wrong_extension", 0) == 0
