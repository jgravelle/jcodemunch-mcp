"""Tests for Razor (.cshtml / .razor) mixed-language symbol extraction."""

from pathlib import Path

from jcodemunch_mcp.parser import parse_file
from jcodemunch_mcp.parser.languages import LANGUAGE_EXTENSIONS, get_language_for_path
from jcodemunch_mcp.tools.index_folder import discover_local_files, index_folder


FIXTURE = Path(__file__).parent / "fixtures" / "razor" / "sample.cshtml"
PUBLIC_FIXTURE = Path(__file__).parent / "fixtures" / "razor" / "dotnet_aspnetcore_layout.cshtml"
BLAZOR_FIXTURE = Path(__file__).parent / "fixtures" / "razor" / "Counter.razor"


def _load():
    return FIXTURE.read_text(encoding="utf-8")


def _symbols():
    return parse_file(_load(), "Views/Profile/Index.cshtml", "razor")


def _load_public():
    return PUBLIC_FIXTURE.read_text(encoding="utf-8")


def test_cshtml_extension_detected():
    assert get_language_for_path("Views/Profile/Index.cshtml") == "razor"


def test_cshtml_extension_in_registry():
    assert LANGUAGE_EXTENSIONS[".cshtml"] == "razor"


def test_parse_razor_mixed_symbols():
    symbols = _symbols()

    view = next((s for s in symbols if s.name == "Index" and s.kind == "class"), None)
    assert view is not None
    assert view.language == "razor"

    csharp_method = next((s for s in symbols if s.name == "FullName"), None)
    assert csharp_method is not None
    assert csharp_method.kind == "method"
    assert csharp_method.language == "csharp"
    assert csharp_method.qualified_name == "Index.FullName"
    assert csharp_method.parent == view.id

    csharp_field = next((s for s in symbols if s.name == "Count"), None)
    assert csharp_field is not None
    # ⚠ A Razor `@functions` block is parsed AS C#, so it inherits #770: this
    # local was already named `csharp_field` and asserted `constant`.
    assert csharp_field.kind == "field"
    assert csharp_field.language == "csharp"
    assert csharp_field.parent == view.id

    html_id = next((s for s in symbols if s.name == "profile-card"), None)
    assert html_id is not None
    assert html_id.kind == "constant"
    assert html_id.language == "razor"
    assert html_id.parent == view.id

    external_script = next((s for s in symbols if s.name == "profile.js"), None)
    assert external_script is not None
    assert external_script.kind == "function"
    assert external_script.language == "razor"
    assert external_script.parent == view.id

    inline_js = next((s for s in symbols if s.name == "toggleCard"), None)
    assert inline_js is not None
    assert inline_js.kind == "function"
    assert inline_js.language == "javascript"
    assert inline_js.qualified_name == "Index.toggleCard"
    assert inline_js.parent == view.id

    style = next((s for s in symbols if s.name == "style_1"), None)
    assert style is not None
    assert style.kind == "constant"
    assert style.language == "razor"
    assert style.parent == view.id


def test_parse_razor_code_block_handles_nested_braces_and_strings():
    source = """\
@code {
    public string Render()
    {
        var json = "{ \\"enabled\\": true }";
        if (json.Contains("{"))
        {
            return json;
        }
        return string.Empty;
    }
}
"""

    symbols = parse_file(source, "Views/Shared/Editor.cshtml", "razor")

    view = next((s for s in symbols if s.name == "Editor" and s.kind == "class"), None)
    assert view is not None

    render = next((s for s in symbols if s.name == "Render"), None)
    assert render is not None
    assert render.kind == "method"
    assert render.language == "csharp"
    assert render.parent == view.id
    assert render.line >= 2
    assert render.end_line >= render.line


def test_discover_local_files_includes_cshtml(tmp_path):
    view = tmp_path / "Views" / "Home"
    view.mkdir(parents=True)
    (view / "Index.cshtml").write_text("<div id='hero'></div>", encoding="utf-8")

    files, warnings, skip_counts = discover_local_files(tmp_path)
    paths = {Path(f).name for f in files}

    assert "Index.cshtml" in paths
    assert warnings == []
    assert skip_counts["wrong_extension"] == 0


def test_index_folder_parses_razor_symbols(tmp_path):
    root = tmp_path / "site"
    views = root / "Views" / "Home"
    views.mkdir(parents=True)
    (views / "Index.cshtml").write_text(_load(), encoding="utf-8")

    result = index_folder(str(root), use_ai_summaries=False, storage_path=str(tmp_path / "store"))

    assert result["success"] is True
    assert result["file_count"] == 1
    assert result["languages"]["razor"] == 1
    assert result["symbol_count"] >= 6
    assert "Views/Home/Index.cshtml" in result["files"]


def test_parse_public_razor_layout_fixture():
    symbols = parse_file(_load_public(), "Views/Shared/_Layout.cshtml", "razor")

    view = next((s for s in symbols if s.name == "_Layout" and s.kind == "class"), None)
    assert view is not None
    assert view.language == "razor"

    scripts = {s.name: s for s in symbols if s.kind == "function"}
    assert "jquery.min.js" in scripts
    assert "bootstrap.bundle.min.js" in scripts
    assert "site.js" in scripts

    assert scripts["jquery.min.js"].parent == view.id
    assert scripts["bootstrap.bundle.min.js"].parent == view.id
    assert scripts["site.js"].parent == view.id


# ---------------------------------------------------------------------------
# .razor (Blazor) tests
# ---------------------------------------------------------------------------

def _load_blazor():
    return BLAZOR_FIXTURE.read_text(encoding="utf-8")


def _blazor_symbols():
    return parse_file(_load_blazor(), "Pages/Counter.razor", "razor")


def test_razor_extension_detected():
    assert get_language_for_path("Pages/Counter.razor") == "razor"


def test_razor_extension_in_registry():
    assert LANGUAGE_EXTENSIONS[".razor"] == "razor"


def test_blazor_component_symbol():
    symbols = _blazor_symbols()
    component = next((s for s in symbols if s.name == "Counter" and s.kind == "class"), None)
    assert component is not None
    assert component.language == "razor"
    assert component.line == 1


def test_blazor_page_routes():
    symbols = _blazor_symbols()
    routes = {s.name: s for s in symbols if s.signature.startswith("@page ")}
    assert "/counter" in routes
    assert "/counter/{StartingCount:int}" in routes
    assert routes["/counter"].kind == "constant"
    assert routes["/counter"].language == "razor"


def test_blazor_inject_directives():
    symbols = _blazor_symbols()
    injects = {s.name: s for s in symbols if s.signature.startswith("@inject ")}
    assert "Logger" in injects
    assert "Navigation" in injects
    assert injects["Logger"].signature == "@inject ILogger<Counter> Logger"
    assert injects["Navigation"].signature == "@inject NavigationManager Navigation"
    assert injects["Logger"].kind == "constant"


def test_blazor_code_block_csharp_members():
    symbols = _blazor_symbols()
    component = next(s for s in symbols if s.name == "Counter" and s.kind == "class")

    on_init = next((s for s in symbols if s.name == "OnInitialized"), None)
    assert on_init is not None
    assert on_init.language == "csharp"
    assert on_init.parent == component.id

    increment = next((s for s in symbols if s.name == "IncrementCount"), None)
    assert increment is not None
    assert increment.language == "csharp"
    assert increment.parent == component.id


def test_blazor_html_ids():
    symbols = _blazor_symbols()
    ids = {s.name for s in symbols if s.kind == "constant" and s.language == "razor" and not s.signature.startswith(("@page", "@inject"))}
    assert "page-title" in ids
    assert "current-count" in ids
    assert "increment-btn" in ids


def test_blazor_style_block():
    symbols = _blazor_symbols()
    style = next((s for s in symbols if s.name == "style_1"), None)
    assert style is not None
    assert style.kind == "constant"
    assert style.language == "razor"


def test_discover_local_files_includes_razor(tmp_path):
    pages = tmp_path / "Pages"
    pages.mkdir(parents=True)
    (pages / "Counter.razor").write_text(_load_blazor(), encoding="utf-8")

    files, warnings, skip_counts = discover_local_files(tmp_path)
    paths = {Path(f).name for f in files}

    assert "Counter.razor" in paths
    assert skip_counts["wrong_extension"] == 0


def test_index_folder_parses_blazor_symbols(tmp_path):
    root = tmp_path / "app"
    pages = root / "Pages"
    pages.mkdir(parents=True)
    (pages / "Counter.razor").write_text(_load_blazor(), encoding="utf-8")

    result = index_folder(str(root), use_ai_summaries=False, storage_path=str(tmp_path / "store"))

    assert result["success"] is True
    assert result["file_count"] == 1
    assert result["languages"]["razor"] == 1
    assert result["symbol_count"] >= 5
    assert "Pages/Counter.razor" in result["files"]
