"""Web Forms attribute values in every form ASP.NET accepts, and `.xsd`/`.wsdl`
capped only in a repository with .NET evidence."""

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


RUN = 200_000
NOT_QUADRATIC_SECONDS = 20.0

HOSTILE = {
    "unquoted_pairs": "<%@ Page " + "a=b " * (RUN // 4) + "%>",
    "chained_equals": "<%@ Page " + "a=" * (RUN // 2) + " %>",
    "unclosed_single_quote": "<asp:Button runat=server a='" + "b" * RUN + " />",
    "single_quote_run": "<asp:Button runat=server " + "a='b' " * (RUN // 6) + "/>",
    "unquoted_event_run": "<asp:Button runat=server " + "OnClick=X " * (RUN // 10) + "/>",
    "empty_unquoted_run": "<asp:Button runat=server " + "a= " * (RUN // 3) + "/>",
    "bare_value_without_delimiter": "<%@ Page a= " + "b" * RUN + "=c %>",
}


@pytest.mark.parametrize("shape", sorted(HOSTILE))
def test_import_extraction_stays_linear_on_every_value_form(shape):
    start = time.perf_counter()
    extract_imports(HOSTILE[shape], "App/x.aspx", "aspx")
    assert time.perf_counter() - start < NOT_QUADRATIC_SECONDS


@pytest.mark.parametrize("shape", sorted(HOSTILE))
def test_symbol_extraction_stays_linear_on_every_value_form(shape):
    start = time.perf_counter()
    parse_file(HOSTILE[shape], "App/x.aspx", "aspx")
    assert time.perf_counter() - start < NOT_QUADRATIC_SECONDS


# --- .xsd / .wsdl -----------------------------------------------------------


@pytest.mark.parametrize("name", ["schema.xsd", "service.wsdl"])
def test_a_shared_format_is_capped_only_with_dotnet_evidence(name):
    assert _entry_points.unseen_consumer(f"res/{name}", dotnet=False, jvm=False) is None
    assert _entry_points.unseen_consumer(f"res/{name}", dotnet=True, jvm=False) == "build_consumed"


@pytest.mark.parametrize("name", ["schema.xsd", "service.wsdl"])
def test_a_shared_format_takes_the_neutral_cap_with_a_jvm_build(name):
    assert _entry_points.unseen_consumer(f"res/{name}", dotnet=False, jvm=True) == "schema_consumer"
    assert _entry_points.unseen_consumer(f"res/{name}", dotnet=True, jvm=True) == "build_consumed"


@pytest.mark.parametrize("name", ["Strings.resx", "App.xaml", "build.targets", "Menu.ascx", "Site.master"])
def test_a_dotnet_only_format_is_capped_without_evidence(name):
    assert _entry_points.unseen_consumer(f"App/{name}", dotnet=False, jvm=False) is not None


def test_a_jvm_build_is_a_maven_or_gradle_build_file():
    assert _entry_points.has_jvm_build(["svc/pom.xml"]) is True
    assert _entry_points.has_jvm_build(["build.gradle"]) is True
    assert _entry_points.has_jvm_build(["app/build.gradle.kts"]) is True
    assert _entry_points.has_jvm_build(["src/Main.java", "res/schema.xsd", "conf/settings.xml"]) is False
    assert _entry_points.has_jvm_build([]) is False


def test_dotnet_evidence_is_dotnet_source_or_a_file_iis_or_msbuild_invokes():
    assert _entry_points.has_dotnet_evidence(["src/Main.java", "App/App.csproj"]) is True
    assert _entry_points.has_dotnet_evidence(["src/Main.java", "Web/Default.aspx"]) is True
    assert _entry_points.has_dotnet_evidence(["src/Main.java", "res/schema.xsd", "res/Strings.resx"]) is False
    assert _entry_points.has_dotnet_evidence(["src/Main.java", "lib/Util.cs"]) is True
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


@pytest.fixture(scope="module")
def java_repo(tmp_path_factory) -> tuple[str, str]:
    return _index(tmp_path_factory.mktemp("java_xsd"), {
        "src/main/java/App.java": "public class App { public static void main(String[] a) { } }\n",
        "src/main/resources/orders.xsd": _SCHEMA,
    })


@pytest.fixture(scope="module")
def maven_repo(tmp_path_factory) -> tuple[str, str]:
    return _index(tmp_path_factory.mktemp("maven_xsd"), {
        "pom.xml": "<project><modelVersion>4.0.0</modelVersion></project>\n",
        "src/main/java/App.java": "public class App { public static void main(String[] a) { } }\n",
        "src/main/resources/orders.xsd": _SCHEMA,
    })


@pytest.fixture(scope="module")
def dotnet_repo(tmp_path_factory) -> tuple[str, str]:
    return _index(tmp_path_factory.mktemp("dotnet_xsd"), {
        "App/App.csproj": '<Project Sdk="Microsoft.NET.Sdk" />\n',
        "App/Program.cs": "namespace App { public static class Program { public static void Main() { } } }\n",
        "Schemas/orders.xsd": _SCHEMA,
    })


def _schema_row(out: dict, path: str) -> dict:
    rows = {d["file"]: d for d in out.get("dead_files", [])}
    assert path in rows, sorted(rows)
    return rows[path]


def test_find_dead_code_caps_a_schema_only_in_a_dotnet_repo(java_repo, dotnet_repo):
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    java = find_dead_code(java_repo[0], min_confidence=0.0, storage_path=java_repo[1])
    dotnet = find_dead_code(dotnet_repo[0], min_confidence=0.0, storage_path=dotnet_repo[1])
    java_row = _schema_row(java, "src/main/resources/orders.xsd")
    dotnet_row = _schema_row(dotnet, "Schemas/orders.xsd")
    assert "build_consumed" not in java_row.get("confidence_capped_by", []), java_row
    assert "build_consumed" in dotnet_row.get("confidence_capped_by", []), dotnet_row


def test_check_delete_safe_names_build_consumed_only_in_a_dotnet_repo(java_repo, dotnet_repo):
    from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe

    java = check_delete_safe(java_repo[0], "order", storage_path=java_repo[1])
    dotnet = check_delete_safe(dotnet_repo[0], "order", storage_path=dotnet_repo[1])
    assert "build_consumed" not in [b.get("kind") for b in java.get("blockers", [])], java
    assert java["verdict"] != "dynamic_import_boundary", java
    assert "build_consumed" in [b.get("kind") for b in dotnet.get("blockers", [])], dotnet


def test_a_maven_build_keeps_a_schema_capped_under_the_neutral_kind(maven_repo):
    from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    found = find_dead_code(maven_repo[0], min_confidence=0.0, storage_path=maven_repo[1])
    row = _schema_row(found, "src/main/resources/orders.xsd")
    caps = row.get("confidence_capped_by", [])
    assert "schema_consumer" in caps and "build_consumed" not in caps, row
    assert row["confidence"] < 1.0, row
    default = find_dead_code(maven_repo[0], storage_path=maven_repo[1])
    assert default.get("schema_consumer_withheld", 0) >= 1, default.get("analysis_notes")

    verdict = check_delete_safe(maven_repo[0], "order", storage_path=maven_repo[1])
    kinds = [b.get("kind") for b in verdict.get("blockers", [])]
    assert "schema_consumer" in kinds and "build_consumed" not in kinds, verdict
    assert "Maven or Gradle" in verdict["recommended_action"], verdict


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
