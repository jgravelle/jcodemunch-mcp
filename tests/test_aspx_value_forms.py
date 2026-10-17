"""Web Forms attribute values in every form ASP.NET accepts, and `.xsd`/`.wsdl`
capped in every repository, with .NET evidence choosing only the kind."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.imports import _aspx_handler_names, extract_imports
from jcodemunch_mcp.tools import _entry_points
from jcodemunch_mcp.tools.index_folder import index_folder

FORMS = {
    "double": '"{}"',
    "single": "'{}'",
    "unquoted": "{}",
}


def _q(form: str, value: str) -> str:
    return FORMS[form].format(value)


@pytest.mark.parametrize("form", sorted(FORMS))
def test_a_directive_names_its_codebehind_in_every_value_form(form):
    page = f"<%@ Page Language={_q(form, 'C#')} CodeFile={_q(form, 'Q.aspx.cs')} Inherits={_q(form, 'App.Q')} %>\n"
    edges = extract_imports(page, "App/Q.aspx", "aspx")
    code = [e for e in edges if e["specifier"] == "Q.aspx.cs"]
    assert code and code[0]["names"] == ["App.Q"], edges


@pytest.mark.parametrize("form", sorted(FORMS))
def test_a_server_control_handler_is_read_in_every_value_form(form):
    markup = f"<asp:Button ID={_q(form, 'b')} runat={_q(form, 'server')} OnClick={_q(form, 'Save_Click')} />"
    assert _aspx_handler_names(markup) == ["Save_Click"]


@pytest.mark.parametrize("form", sorted(FORMS))
def test_a_server_control_is_a_symbol_in_every_value_form(form):
    page = (
        '<%@ Page Language="C#" %>\n'
        f"<asp:Button ID={_q(form, 'SaveButton')} runat={_q(form, 'server')} />\n"
    )
    names = {s.name for s in parse_file(page, "App/Q.aspx", "aspx")}
    assert "SaveButton" in names, names


@pytest.mark.parametrize("form", sorted(FORMS))
def test_a_server_script_is_reparsed_in_every_value_form(form):
    page = (
        '<%@ Page Language="C#" %>\n'
        f"<script runat={_q(form, 'server')}>\nvoid Hello_Zzz() {{ }}\n</script>\n"
    )
    names = {s.name for s in parse_file(page, "App/Q.aspx", "aspx")}
    assert "Hello_Zzz" in names, names


WRONG_RUNAT = {
    "prefixed_name": 'data-runat="server"',
    "longer_value": "runat=serverless",
    "other_value": "runat=client",
    "hyphenated_value": "runat=server-x",
}


@pytest.mark.parametrize(
    "markup",
    [
        '<asp:Button data-runat="server" OnClick="Bare_Name" />',
        "<asp:Button runat=serverless OnClick=Bare_Name />",
        "<asp:Button runat=client OnClick=Bare_Name />",
        "<asp:Button runat=server-x OnClick=Bare_Name />",
    ],
    ids=["prefixed_name", "longer_value", "other_value", "hyphenated_value"],
)
def test_only_a_runat_attribute_whose_value_is_server_opens_the_gate(markup):
    assert _aspx_handler_names(markup) == []


@pytest.mark.parametrize("gate", sorted(WRONG_RUNAT))
def test_the_extractor_gate_admits_only_runat_server(gate):
    page = (
        '<%@ Page Language="C#" %>\n'
        f'<asp:Button {WRONG_RUNAT[gate]} ID="Foo" />\n'
        f"<script {WRONG_RUNAT[gate]}>\nvoid Zz_Q() {{ }}\n</script>\n"
    )
    names = {s.name for s in parse_file(page, "App/Q.aspx", "aspx")}
    assert not {"Foo", "Zz_Q"} & names, names


def test_a_quoted_value_containing_an_equals_sign_is_one_value():
    page = '<%@ Page CodeFile="Q.aspx.cs" Inherits=\'App.Q\' Title="a=b c=d" %>\n'
    edges = extract_imports(page, "App/Q.aspx", "aspx")
    assert [e["names"] for e in edges if e["specifier"] == "Q.aspx.cs"] == [["App.Q"]]


@pytest.mark.parametrize("title", ['Title=""', "Title="], ids=["quoted", "empty_unquoted"])
def test_an_empty_value_does_not_take_the_next_attribute(title):
    page = f'<%@ Page {title} CodeFile="Q.aspx.cs" Inherits="App.Q" %>\n'
    edges = extract_imports(page, "App/Q.aspx", "aspx")
    assert [e["names"] for e in edges if e["specifier"] == "Q.aspx.cs"] == [["App.Q"]], edges


@pytest.mark.parametrize("id_attr", ['ID=""', "ID="], ids=["quoted", "empty_unquoted"])
def test_an_empty_control_id_names_no_control(id_attr):
    page = f'<%@ Page Language="C#" %>\n<asp:Button {id_attr} runat="server" />\n'
    names = {s.name for s in parse_file(page, "App/Q.aspx", "aspx")}
    assert "runat" not in names, names


@pytest.mark.parametrize("end", [" />", "/>"], ids=["spaced", "adjacent"])
def test_an_unquoted_tag_value_stops_at_the_tag_end(end):
    markup = f"<asp:Button ID=Save runat=server OnClick=Save_Click{end}"
    assert _aspx_handler_names(markup) == ["Save_Click"]
    page = f'<%@ Page Language="C#" %>\n<asp:Button runat=server ID=Save{end}\n'
    assert "Save" in {s.name for s in parse_file(page, "App/Q.aspx", "aspx")}


def test_an_unquoted_directive_value_may_be_a_path():
    edges = extract_imports("<%@ Page CodeFile=Sub/Q.aspx.cs %>\n", "App/Q.aspx", "aspx")
    assert "Sub/Q.aspx.cs" in {e["specifier"] for e in edges}, edges


QUOTED_TEXT = {
    "handler_in_a_value": '<asp:Button ID="b" runat="server" ToolTip="use OnClick=Evil_Click please" />',
    "runat_in_a_value": '<asp:Button ID="b" ToolTip="set runat=server here" OnClick="Save_Click" />',
    "handler_in_single_quotes": "<asp:Button ID=b runat=server ToolTip='OnClick=Evil_Click ok' />",
    "quoted_handler_in_a_value": """<asp:Button ID="b" runat="server" ToolTip='use OnClick="Evil_Click"' />""",
    "quoted_runat_in_a_value": """<asp:Button ID="b" ToolTip='x runat="server"' OnClick="Save_Click" />""",
}


@pytest.mark.parametrize("shape", sorted(QUOTED_TEXT))
def test_text_inside_a_quoted_value_is_no_attribute(shape):
    assert _aspx_handler_names(QUOTED_TEXT[shape]) == []


def test_text_inside_a_quoted_value_opens_no_extractor_gate():
    page = (
        '<%@ Page Language="C#" %>\n'
        '<asp:Button ID="b" ToolTip="set runat=server here" />\n'
        """<asp:Button ID="c" ToolTip='x runat="server"' />\n"""
        "<script title='a runat=server b'>\nvoid Zz_Q() { }\n</script>\n"
    )
    names = {s.name for s in parse_file(page, "App/Q.aspx", "aspx")}
    assert not {"b", "c", "Zz_Q"} & names, names


def test_quotes_inside_an_inline_block_do_not_shift_the_attributes():
    markup = (
        '<asp:HyperLink ID="lnk" NavigateUrl="<%# "x.aspx?id=" + Eval("Id") %>" '
        'runat="server" OnLoad="Lnk_Load" />'
    )
    assert _aspx_handler_names(markup) == ["Lnk_Load"]
    nugget = '<asp:Button ID="b" runat="server" Text="<%# " OnClick=Evil_Click " %>" />'
    assert _aspx_handler_names(nugget) == []
    page = '<%@ Page Language="C#" %>' + chr(10) + markup + chr(10)
    names = {s.name for s in parse_file(page, "App/Q.aspx", "aspx")}
    assert "lnk" in names and not any("Eval" in n for n in names), names


RUN = 200_000
NOT_QUADRATIC_SECONDS = 20.0
# Handlers are read only after a code directive, so each tag shape carries one.
CODE_DIRECTIVE = '<%@ Page CodeFile="x.aspx.cs" Inherits="A.B" %>'

HOSTILE = {
    "unquoted_pairs": "<%@ Page " + "a=b " * (RUN // 4) + "%>",
    "chained_equals": "<%@ Page " + "a=" * (RUN // 2) + " %>",
    "bare_value_without_delimiter": "<%@ Page a= " + "b" * RUN + "=c %>",
}
TAG_SHAPES = {
    "unclosed_single_quote": "<asp:Button runat=server a='" + "b" * RUN + " />",
    "single_quote_run": "<asp:Button runat=server " + "a='b' " * (RUN // 6) + "/>",
    "unquoted_event_run": "<asp:Button runat=server " + "OnClick=X " * (RUN // 10) + "/>",
    "empty_unquoted_run": "<asp:Button runat=server " + "a= " * (RUN // 3) + "/>",
    "inline_block_run": "<asp:Button runat=server " + "a=<%b%> " * (RUN // 8) + "/>",
}
HOSTILE.update({name: CODE_DIRECTIVE + shape for name, shape in TAG_SHAPES.items()})


@pytest.mark.parametrize("shape", sorted(HOSTILE))
def test_import_extraction_stays_linear_on_every_value_form(shape):
    start = time.perf_counter()
    extract_imports(HOSTILE[shape], "App/x.aspx", "aspx")
    assert time.perf_counter() - start < NOT_QUADRATIC_SECONDS


@pytest.mark.parametrize("shape", sorted(TAG_SHAPES))
def test_every_tag_shape_reaches_the_handler_reader(shape, monkeypatch):
    from jcodemunch_mcp.parser import imports

    calls = []
    real = imports._aspx_handler_names
    monkeypatch.setattr(imports, "_aspx_handler_names", lambda c: calls.append(1) or real(c))
    extract_imports(HOSTILE[shape], "App/x.aspx", "aspx")
    assert calls, shape


@pytest.mark.parametrize("shape", sorted(HOSTILE))
def test_symbol_extraction_stays_linear_on_every_value_form(shape):
    start = time.perf_counter()
    parse_file(HOSTILE[shape], "App/x.aspx", "aspx")
    assert time.perf_counter() - start < NOT_QUADRATIC_SECONDS


# --- .xsd / .wsdl -----------------------------------------------------------


@pytest.mark.parametrize("name", ["schema.xsd", "service.wsdl"])
def test_a_shared_format_is_capped_in_every_repository(name):
    assert _entry_points.unseen_consumer(f"res/{name}", dotnet=True) == "build_consumed"
    assert _entry_points.unseen_consumer(f"res/{name}", dotnet=False) == "schema_consumer"


@pytest.mark.parametrize("name", ["Strings.resx", "App.xaml", "build.targets", "Menu.ascx", "Site.master"])
def test_a_dotnet_only_format_is_capped_without_evidence(name):
    assert _entry_points.unseen_consumer(f"App/{name}", dotnet=False) is not None


def test_dotnet_evidence_is_dotnet_source_a_dotnet_only_format_or_a_host_invoked_file():
    assert _entry_points.has_dotnet_evidence(["src/Main.java", "App/App.csproj"]) is True
    assert _entry_points.has_dotnet_evidence(["src/Main.java", "Web/Default.aspx"]) is True
    assert _entry_points.has_dotnet_evidence(["src/Main.java", "lib/Util.cs"]) is True
    assert _entry_points.has_dotnet_evidence(["res/schema.xsd", "res/Strings.resx"]) is True
    assert _entry_points.has_dotnet_evidence(["ui/Menu.ascx", "res/schema.xsd"]) is True
    assert _entry_points.has_dotnet_evidence(["src/Main.java", "res/schema.xsd", "pom.xml"]) is False
    assert _entry_points.has_dotnet_evidence([]) is False


def _index(root: Path, files: dict[str, str]) -> tuple[str, str]:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    storage = str(root / ".index")
    result = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    return result["repo"], storage


_SCHEMA = (
    '<?xml version="1.0"?>\n'
    '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">\n'
    '  <xs:element name="order" type="xs:string"/>\n'
    "</xs:schema>\n"
)


_JAVA = "public class App { public static void main(String[] a) { } }\n"
_DOTNET = "namespace App { public static class Program { public static void Main() { } } }\n"
_CONTROL = '<%@ Control Language="C#" %>\n<asp:Label ID="l" runat="server" />\n'

# Trees whose schema `main` capped and an evidence-gated cap did not (review of
# #1067): each keeps its cap, and only the kind follows the evidence.
SCHEMA_TREES = {
    "dotnet_project": ({
        "App/App.csproj": '<Project Sdk="Microsoft.NET.Sdk" />\n',
        "App/Program.cs": _DOTNET,
        "Schemas/orders.xsd": _SCHEMA,
    }, "Schemas/orders.xsd", "build_consumed"),
    "vb_subtree": ({
        "Module1.vb": "Module Module1\nEnd Module\n",
        "Resources.resx": "<root />\n",
        "OrdersDataSet.xsd": _SCHEMA,
    }, "OrdersDataSet.xsd", "build_consumed"),
    "controls_only": ({
        "Menu.ascx": _CONTROL,
        "Strings.resx": "<root />\n",
        "App_Code/orders.xsd": _SCHEMA,
    }, "App_Code/orders.xsd", "build_consumed"),
    "schemas_only": ({"orders.xsd": _SCHEMA}, "orders.xsd", "schema_consumer"),
    # LEDGER L-184: .NET trees the evidence list missed, and a non-.NET tree it misread.
    "razor_only": ({"Pages/Index.cshtml": "@page\n<h1>Welcome</h1>\n", "Data/orders.xsd": _SCHEMA},
                   "Data/orders.xsd", "build_consumed"),
    "blazor_only": ({"Shared/Nav.razor": "<nav>Home</nav>\n", "Data/orders.xsd": _SCHEMA},
                    "Data/orders.xsd", "build_consumed"),
    "fsharp_script": ({"build.fsx": 'printfn "hi"\n', "Data/orders.xsd": _SCHEMA},
                      "Data/orders.xsd", "build_consumed"),
    "maven_glsl_shader": ({
        "pom.xml": "<project><modelVersion>4.0.0</modelVersion></project>\n",
        "src/main/java/App.java": _JAVA,
        "src/main/resources/shader.fs": "void main() { gl_FragColor = vec4(1.0); }\n",
        "src/main/resources/orders.xsd": _SCHEMA,
    }, "src/main/resources/orders.xsd", "schema_consumer"),
    "java_no_build": ({
        "src/main/java/App.java": _JAVA,
        "src/main/resources/orders.xsd": _SCHEMA,
    }, "src/main/resources/orders.xsd", "schema_consumer"),
    "maven": ({
        "pom.xml": "<project><modelVersion>4.0.0</modelVersion></project>\n",
        "src/main/java/App.java": _JAVA,
        "src/main/resources/orders.xsd": _SCHEMA,
    }, "src/main/resources/orders.xsd", "schema_consumer"),
    "ant": ({
        "build.xml": '<project name="app" default="jar" />\n',
        "src/App.java": _JAVA,
        "schema/orders.xsd": _SCHEMA,
    }, "schema/orders.xsd", "schema_consumer"),
}


@pytest.fixture(scope="module")
def schema_repos(tmp_path_factory) -> dict[str, tuple[str, str]]:
    return {
        name: _index(tmp_path_factory.mktemp(name), files)
        for name, (files, _, _) in SCHEMA_TREES.items()
    }


def _schema_row(out: dict, path: str) -> dict:
    rows = {d["file"]: d for d in out.get("dead_files", [])}
    assert path in rows, sorted(rows)
    return rows[path]


@pytest.mark.parametrize("tree", sorted(SCHEMA_TREES))
def test_find_dead_code_caps_a_schema_in_every_tree(tree, schema_repos):
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    _, path, kind = SCHEMA_TREES[tree]
    repo, storage = schema_repos[tree]
    row = _schema_row(find_dead_code(repo, min_confidence=0.0, storage_path=storage), path)
    assert kind in row.get("confidence_capped_by", []), row
    assert row["confidence"] < 1.0, row
    default = find_dead_code(repo, storage_path=storage)
    assert path not in {d["file"] for d in default.get("dead_files", [])}, default
    assert default.get(f"{kind}_withheld", 0) >= 1, default.get("analysis_notes")


@pytest.mark.parametrize("tree", sorted(SCHEMA_TREES))
def test_check_delete_safe_never_clears_a_schema(tree, schema_repos):
    from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe

    _, _, kind = SCHEMA_TREES[tree]
    repo, storage = schema_repos[tree]
    verdict = check_delete_safe(repo, "order", storage_path=storage)
    assert verdict["verdict"] == "dynamic_import_boundary", verdict
    assert kind in [b.get("kind") for b in verdict.get("blockers", [])], verdict
    project_file_advice = "project file" in verdict["recommended_action"]
    assert project_file_advice == (kind == "build_consumed"), verdict["recommended_action"]


def test_existing_indexes_reparse_web_forms_pages():
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 20


@pytest.mark.parametrize("form", sorted(FORMS))
def test_plan_refactoring_finds_a_web_forms_definition_in_every_value_form(form):
    import re

    from jcodemunch_mcp.tools.plan_refactoring import _DEF_PATTERNS
    from jcodemunch_mcp.word_rule import fill_name_template

    def found(name: str, text: str) -> bool:
        return bool(re.match(fill_name_template(_DEF_PATTERNS["aspx"].pattern, name), text))

    assert found("SaveButton", f"<asp:Button ID={_q(form, 'SaveButton')} runat=server />")
    assert found("Q", f"<%@ Page Inherits={_q(form, 'App.Q')} %>")
    assert not found("Save", f"<asp:Button ID={_q(form, 'SaveButton')} runat=server />")
