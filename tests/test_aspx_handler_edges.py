"""Web Forms markup -> codebehind edges: On*= handlers, designer partials, and path resolution."""

import posixpath
import time

import pytest

from jcodemunch_mcp.parser.imports import (
    _ASPX_LIFECYCLE_METHODS,
    _aspx_handler_names,
    extract_imports,
    resolve_specifier,
)

PAGE = """\
<%@ Page Language="C#" AutoEventWireup="true" CodeBehind="Orders.aspx.cs"
    Inherits="ContosoWeb.Admin.Orders" MasterPageFile="~/Site.Master" %>
<asp:Button ID="btnRun" runat="server" Height="30px"
  onclick="btnRun_Click" Text="RUN" />
<asp:Button ID="btnSave" runat="server" OnClick="btnSave_Click" />
<asp:GridView ID="Grid" runat="server" OnRowCommand="Grid_RowCommand" />
"""


def _edges(content, path="Admin/Orders.aspx"):
    return extract_imports(content, path, "aspx")


def _by_binding(edges, binding):
    return [e for e in edges if e.get("aspx_binding") == binding]


def _names(edges, binding):
    out = set()
    for e in _by_binding(edges, binding):
        out.update(e["names"])
    return out


# ---------------------------------------------------------------------------
# markup_event: On*= handlers
# ---------------------------------------------------------------------------


def test_handler_on_a_multiline_tag_is_found():
    assert "btnRun_Click" in _aspx_handler_names(PAGE)


@pytest.mark.parametrize(
    "tag",
    [
        """<asp:Button Text='<%# Eval("x") %>' runat="server" OnClick="Foo_Click" />""",
        """<asp:Button runat="server" Text='<%= Title %>' OnClick="Foo_Click" />""",
        """<asp:LinkButton runat="server"
             CommandArgument='<%# Eval("Id") %>' OnCommand="Foo_Click" />""",
    ],
)
def test_a_handler_after_an_inline_expression_is_found(tag):
    """The `%>` of an expression inside an attribute does not end the tag."""
    assert "Foo_Click" in _aspx_handler_names(tag)


@pytest.mark.parametrize(
    "content",
    [
        "Price < " + "<%= x %>" * 26,       # exponential before the fix: seconds, not ms
        "<asp:Label runat=\"server\" Text='<% " * 4000,  # unclosed inline blocks
    ],
    ids=["lone_lt_before_blocks", "unclosed_blocks"],
)
def test_tag_scan_stays_linear_on_adversarial_markup(content):
    start = time.perf_counter()
    _aspx_handler_names(content)
    assert time.perf_counter() - start < 2.0


def test_import_extraction_stays_linear_with_many_directives():
    content = '<%@ Page CodeBehind="a.aspx.cs" %>\n' * 3000  # ~100 KB
    start = time.perf_counter()
    extract_imports(content, "App/a.aspx", "aspx")
    assert time.perf_counter() - start < 2.0


@pytest.mark.parametrize(
    "content",
    ["<a:b " * 20000, '<script runat="server">' * 5000, "<script " * 20000,
     '<asp:Label runat="server" Text=\'<% ' * 4000],
    ids=["unclosed_controls", "unclosed_server_scripts", "unclosed_script_tags", "unclosed_blocks"],
)
def test_symbol_extraction_stays_linear_on_adversarial_markup(content):
    from jcodemunch_mcp.parser.extractor import parse_file

    start = time.perf_counter()
    parse_file(content, "App/x.aspx", "aspx")
    assert time.perf_counter() - start < 3.0


def test_a_control_with_an_inline_expression_keeps_its_symbol():
    from jcodemunch_mcp.parser.extractor import parse_file

    page = """<asp:Label Text='<%# Eval("x") %>' ID="lblName" runat="server" />\n"""
    names = {s.name for s in parse_file(page, "App/x.aspx", "aspx")}
    assert "lblName" in names, names


def test_a_percent_sign_in_a_directive_keeps_its_edges():
    page = '<%@ Page Title="50% off" CodeBehind="X.aspx.cs" Inherits="A.X" %>\n'
    edges = extract_imports(page, "App/X.aspx", "aspx")
    assert "X.aspx.cs" in {e["specifier"] for e in edges}


def test_handler_attribute_casing_does_not_matter():
    found = _aspx_handler_names(PAGE)
    assert {"btnRun_Click", "btnSave_Click"} <= set(found)


@pytest.mark.parametrize(
    "attr",
    [
        "OnRowCommand", "OnSelectedIndexChanged", "OnCheckedChanged",
        "OnPageIndexChanging", "OnItemCommand", "onsorting", "onmenuitemclick",
        "OnAuthenticate", "OnPreRender", "OnInit",
    ],
)
def test_every_event_attribute_spelling_in_the_corpus(attr):
    markup = f'<asp:Thing ID="t" runat="server" {attr}="Handler_Name" />'
    assert _aspx_handler_names(markup) == ["Handler_Name"]


def test_onclient_attributes_are_not_codebehind():
    """OnClient* handlers are JavaScript, not codebehind methods."""
    markup = (
        '<asp:Button ID="b" runat="server" OnClientClick="confirmIt" '
        'OnClientCancel="cancelIt" OnClick="Real_Click" />'
    )
    assert _aspx_handler_names(markup) == ["Real_Click"]


def test_plain_html_element_handlers_are_ignored():
    assert _aspx_handler_names('<input type="text" onclick="doThing()" />') == []
    assert _aspx_handler_names('<body onload="init()">') == []


@pytest.mark.parametrize(
    "value",
    ['<%# Eval("Col") %>', "doThing()", "a.b.c", "", "  ", "return false;", "1bad"],
)
def test_only_a_bare_method_name_becomes_an_edge(value):
    markup = f'<asp:Button ID="b" runat="server" OnClick=\'{value}\' />'
    assert _aspx_handler_names(markup.replace("'", '"')) == []


def test_duplicate_handlers_are_emitted_once():
    markup = (
        '<asp:Button ID="a" runat="server" OnClick="Shared_Click" />'
        '<asp:Button ID="b" runat="server" OnClick="Shared_Click" />'
    )
    assert _aspx_handler_names(markup) == ["Shared_Click"]


def test_handler_edge_targets_the_codebehind_file():
    edges = _edges(PAGE)
    handler_edges = _by_binding(edges, "markup_event")
    assert len(handler_edges) == 1
    assert handler_edges[0]["specifier"] == "Orders.aspx.cs"
    assert _names(edges, "markup_event") == {
        "btnRun_Click", "btnSave_Click", "Grid_RowCommand"
    }


def test_inherits_edge_is_unchanged_by_the_new_edges():
    edges = _edges(PAGE)
    plain = [
        e for e in edges
        if e["specifier"] == "Orders.aspx.cs" and "aspx_binding" not in e
    ]
    assert plain == [
        {"specifier": "Orders.aspx.cs", "names": ["ContosoWeb.Admin.Orders"]}
    ]


# ---------------------------------------------------------------------------
# AutoEventWireup lifecycle: deliberately not emitted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", _ASPX_LIFECYCLE_METHODS)
def test_no_lifecycle_edge_is_synthesized(method):
    assert method not in _names(_edges(PAGE), "framework_invoked")


def test_autoeventwireup_true_emits_no_edges_at_all():
    assert _by_binding(_edges(PAGE), "framework_invoked") == []


def test_only_names_present_in_the_markup_text_are_emitted():
    emitted = {n for e in _edges(PAGE) for n in e["names"]}
    for name in emitted:
        assert name in PAGE, f"{name} was emitted but appears nowhere in the markup"


def test_page_load_is_a_known_remaining_gap():
    assert "Page_Load" not in {n for e in _edges(PAGE) for n in e["names"]}


# ---------------------------------------------------------------------------
# designer_partial: the generated half of the partial class
# ---------------------------------------------------------------------------


def test_designer_sibling_gets_an_edge():
    edges = _by_binding(_edges(PAGE), "designer_partial")
    assert [e["specifier"] for e in edges] == ["Orders.aspx.designer.cs"]


def test_designer_edge_only_for_a_cs_codebehind():
    page = '<%@ Page CodeBehind="Default.aspx.vb" Inherits="App.Default" %>'
    assert _by_binding(_edges(page), "designer_partial") == []


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

SOURCES = {
    "ContosoWeb/Admin/Orders.aspx",
    "ContosoWeb/Admin/Orders.aspx.cs",
    "ContosoWeb/Admin/Orders.aspx.designer.cs",
    "ContosoWeb/Site.Master",
    "src/app.py",
    "Orders.aspx.cs",  # a decoy at repo root
}
IMPORTER = "ContosoWeb/Admin/Orders.aspx"


def test_codebehind_sibling_resolves():
    assert (
        resolve_specifier("Orders.aspx.cs", IMPORTER, SOURCES)
        == "ContosoWeb/Admin/Orders.aspx.cs"
    )


def test_sibling_wins_over_a_repo_root_file_of_the_same_name():
    assert posixpath.dirname(
        resolve_specifier("Orders.aspx.cs", IMPORTER, SOURCES)
    ) == "ContosoWeb/Admin"


def test_designer_sibling_resolves():
    assert (
        resolve_specifier("Orders.aspx.designer.cs", IMPORTER, SOURCES)
        == "ContosoWeb/Admin/Orders.aspx.designer.cs"
    )


def test_app_root_relative_master_resolves_via_an_ancestor():
    assert (
        resolve_specifier("Site.Master", IMPORTER, SOURCES)
        == "ContosoWeb/Site.Master"
    )


def test_unresolvable_specifier_stays_none():
    assert resolve_specifier("NoSuch.aspx.cs", IMPORTER, SOURCES) is None


@pytest.mark.parametrize("importer", ["src/app.py", "src/app.ts", "src/app.go"])
def test_non_markup_importers_are_untouched(importer):
    assert resolve_specifier("Orders.aspx.cs", importer, SOURCES) in (
        None, "Orders.aspx.cs",
    )


@pytest.mark.parametrize("files", [set, frozenset])
def test_markup_paths_resolve_case_insensitively(files):
    """ASP.NET paths are case-insensitive: `codebehind="Site.master.cs"` names `Site.Master.cs`."""
    sources = files({"App/Site.Master", "App/Site.Master.cs", "App/Admin/Orders.aspx"})
    assert resolve_specifier("Site.master.cs", "App/Site.Master", sources) == "App/Site.Master.cs"
    assert resolve_specifier("site.master", "App/Admin/Orders.aspx", sources) == "App/Site.Master"


def test_an_exact_case_match_wins_over_a_case_insensitive_one():
    sources = {"App/Site.Master", "App/site.master.cs", "App/Site.master.cs"}
    assert resolve_specifier("Site.master.cs", "App/Site.Master", sources) == "App/Site.master.cs"


def test_a_sibling_case_match_wins_over_an_exact_ancestor_match():
    sources = {"App/Admin/Orders.aspx", "App/Admin/orders.aspx.cs", "App/Orders.aspx.cs"}
    assert (
        resolve_specifier("Orders.aspx.cs", "App/Admin/Orders.aspx", sources)
        == "App/Admin/orders.aspx.cs"
    )


def test_non_markup_importers_stay_case_sensitive():
    assert resolve_specifier("Utils.py", "src/app.py", {"src/utils.py"}) is None


def test_every_markup_extension_resolves_its_codebehind():
    for ext in (".aspx", ".ascx", ".master", ".asax", ".ashx", ".asmx"):
        sources = {f"App/Thing{ext}", f"App/Thing{ext}.cs"}
        assert (
            resolve_specifier(f"Thing{ext}.cs", f"App/Thing{ext}", sources)
            == f"App/Thing{ext}.cs"
        )
