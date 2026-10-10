"""ASP.NET Web Forms as its own language: extension mapping, symbols, and import edges."""

from pathlib import Path

import pytest

from jcodemunch_mcp.parser import parse_file
from jcodemunch_mcp.parser.imports import extract_imports
from jcodemunch_mcp.parser.languages import (
    LANGUAGE_EXTENSIONS,
    LANGUAGE_REGISTRY,
    get_language_for_path,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dotnet"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8-sig")


def _symbols(name: str):
    return parse_file(_read(name), name, "aspx")


def _imports(name: str):
    return extract_imports(_read(name), name, "aspx")


# ---------------------------------------------------------------------------
# The split itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ext", [".aspx", ".ascx", ".master", ".asax", ".ashx", ".asmx"]
)
def test_web_forms_extensions_map_to_aspx(ext):
    assert LANGUAGE_EXTENSIONS[ext] == "aspx"
    assert get_language_for_path(f"Views/Page{ext}") == "aspx"


def test_aspx_is_a_registered_language_distinct_from_razor():
    assert "aspx" in LANGUAGE_REGISTRY
    assert "razor" in LANGUAGE_REGISTRY
    assert LANGUAGE_REGISTRY["aspx"] is not LANGUAGE_REGISTRY["razor"]


def test_razor_extensions_are_untouched_by_the_split():
    assert get_language_for_path("Views/Index.cshtml") == "razor"
    assert get_language_for_path("Pages/Counter.razor") == "razor"


def test_a_migration_can_filter_the_two_apart():
    legacy = get_language_for_path("Legacy/Default.aspx")
    modern = get_language_for_path("Components/Counter.razor")
    assert legacy == "aspx" and modern == "razor"
    assert legacy != modern


def test_codebehind_still_resolves_to_csharp():
    assert get_language_for_path("Admin/Orders.aspx.cs") == "csharp"
    assert get_language_for_path("Admin/Orders.aspx.designer.cs") == "csharp"
    assert get_language_for_path("Site.Master.cs") == "csharp"


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------


def test_page_directive_yields_a_page_class_symbol():
    symbols = _symbols("webforms_default.aspx")
    page = next(s for s in symbols if s.kind == "class")
    assert page.language == "aspx"
    assert page.signature.startswith("page ")
    assert page.qualified_name == "aspnetapp._Default"


def test_control_and_master_directives_are_distinguished():
    control = next(s for s in _symbols("webforms_viewswitcher.ascx") if s.kind == "class")
    master = next(s for s in _symbols("webforms_site.master") if s.kind == "class")
    assert control.signature.startswith("control ")
    assert master.signature.startswith("master ")


def test_global_asax_is_recognised_as_an_application_directive():
    app = next(s for s in _symbols("webforms_global.asax") if s.kind == "class")
    assert app.signature.startswith("application ")


def test_the_codebehind_pairing_becomes_a_searchable_symbol():
    symbols = _symbols("webforms_default.aspx")
    pairing = next(s for s in symbols if s.name == "aspnetapp._Default")
    assert pairing.kind == "constant"
    assert "Inherits=" in pairing.signature


def test_server_controls_become_constants():
    symbols = _symbols("webforms_handlers.aspx")
    names = {s.name for s in symbols if s.kind == "constant"}
    assert {"btnSave", "btnFind", "btnDelete", "ddlRegion", "gvOrders"} <= names


def test_controls_without_runat_server_are_ignored():
    source = (
        '<%@ Page Language="C#" %>\n'
        '<asp:Button ID="real" runat="server" />\n'
        '<my:Widget ID="clientOnly" />\n'
    )
    names = {s.name for s in parse_file(source, "p.aspx", "aspx")}
    assert "real" in names
    assert "clientOnly" not in names


def test_custom_tag_prefixes_are_supported():
    source = (
        '<%@ Page Language="C#" %>\n'
        '<%@ Register TagPrefix="uc" TagName="Nav" Src="~/Controls/Nav.ascx" %>\n'
        '<uc:Nav ID="mainNav" runat="server" />\n'
    )
    symbols = parse_file(source, "p.aspx", "aspx")
    names = {s.name for s in symbols}
    assert "mainNav" in names, "a custom-prefixed server control must be extracted"
    assert "uc" in names, "the registered tag prefix should be recorded"


def test_inline_server_script_is_reparsed_as_csharp():
    source = (
        '<%@ Page Language="C#" %>\n'
        '<script runat="server">\n'
        "    protected void DoWork(object sender, EventArgs e) { }\n"
        "</script>\n"
    )
    names = {s.name for s in parse_file(source, "p.aspx", "aspx")}
    assert "DoWork" in names


def test_handler_names_are_not_symbols():
    """Handlers are references to codebehind definitions, so not symbols here."""
    names = {s.name for s in _symbols("webforms_handlers.aspx")}
    assert "btnSave_Click" not in names
    assert "btnFind_Click" not in names
    assert not any(n.endswith("_Click") for n in names)


def test_client_side_hooks_are_not_mistaken_for_anything():
    names = {s.name for s in _symbols("webforms_handlers.aspx")}
    assert "confirmDelete" not in names
    assert "btnDelete" in names


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


def test_codebehind_edge_is_emitted():
    edges = _imports("webforms_default.aspx")
    specs = {e["specifier"] for e in edges}
    assert "Default.aspx.cs" in specs


def test_inherits_rides_as_a_name_not_a_specifier():
    edge = next(e for e in _imports("webforms_default.aspx") if e["specifier"].endswith(".cs"))
    assert "aspnetapp._Default" in edge["names"]
    specs = {e["specifier"] for e in _imports("webforms_default.aspx")}
    assert "aspnetapp._Default" not in specs


def test_master_page_edge_is_emitted_with_app_root_stripped():
    edges = _imports("webforms_default.aspx")
    specs = {e["specifier"] for e in edges}
    assert "Site.Master" in specs
    assert not any(s.startswith("~/") for s in specs)


def test_codefile_spelling_is_handled_as_well_as_codebehind():
    source = '<%@ Page Language="C#" CodeFile="Thing.aspx.cs" Inherits="Thing" %>\n'
    specs = {e["specifier"] for e in extract_imports(source, "Thing.aspx", "aspx")}
    assert "Thing.aspx.cs" in specs


def test_registered_user_control_becomes_an_edge():
    source = (
        '<%@ Page Language="C#" %>\n'
        '<%@ Register TagPrefix="uc" TagName="Nav" Src="~/Controls/Nav.ascx" %>\n'
    )
    specs = {e["specifier"] for e in extract_imports(source, "p.aspx", "aspx")}
    assert "Controls/Nav.ascx" in specs


def test_namespace_import_is_names_only():
    source = '<%@ Page Language="C#" %>\n<%@ Import Namespace="System.Data" %>\n'
    edges = extract_imports(source, "p.aspx", "aspx")
    ns = next(e for e in edges if e["specifier"] == "System.Data")
    assert ns["names"] == ["System.Data"]


def test_import_extraction_is_registered_for_aspx_not_razor():
    from jcodemunch_mcp.parser.imports import _LANGUAGE_EXTRACTORS

    assert "aspx" in _LANGUAGE_EXTRACTORS
    assert "razor" not in _LANGUAGE_EXTRACTORS


def test_a_file_with_no_directives_still_parses():
    symbols = parse_file("<div>plain markup</div>", "frag.ascx", "aspx")
    assert len(symbols) >= 1
    assert symbols[0].kind == "class"
