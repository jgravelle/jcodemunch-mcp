"""Guards from the review of #1012 (ASP.NET Web Forms, @outoftheblue9).

Four defects the review reproduced on the PR's tree, each with the twin that
must keep working:

- two regexes re-read a long run of name characters from every position, in
  one C call no parse budget interrupts (100 KB took minutes);
- `<%@ Import Namespace="Utils" %>` resolved as a path with extension
  expansion, so an unimported `utils.js` in any ancestor directory read as
  used and `find_dead_code` stopped reporting it;
- `get_dead_code_v2` reported files held back for a .NET reason under
  `dynamic_import_boundary`, with no site, in a repository holding no dynamic
  import;
- the `runat="server"` gate had a test that passed with the gate removed.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.imports import _aspx_handler_names, extract_imports, resolve_specifier
from jcodemunch_mcp.tools.index_folder import index_folder

# Sized so the quadratic forms take minutes and the linear ones milliseconds:
# the bound below is not a timing budget, it separates the two by orders of
# magnitude on any machine.
RUN = 200_000
NOT_QUADRATIC_SECONDS = 20.0

HOSTILE = {
    "unclosed_directive_name": "<%@" + "a" * RUN,
    "directive_attribute_run": "<%@ Page " + "a" * RUN + " %>",
    "directive_dotted_run": "<%@ Page " + "a.b:c-" * (RUN // 6) + " %>",
    "control_attribute_run": '<asp:Button runat="server" ' + "a" * RUN + " />",
}


@pytest.mark.parametrize("shape", sorted(HOSTILE))
def test_import_extraction_reads_a_long_name_run_once(shape):
    start = time.perf_counter()
    extract_imports(HOSTILE[shape], "App/x.aspx", "aspx")
    assert time.perf_counter() - start < NOT_QUADRATIC_SECONDS


@pytest.mark.parametrize("shape", sorted(HOSTILE))
def test_symbol_extraction_reads_a_long_name_run_once(shape):
    start = time.perf_counter()
    parse_file(HOSTILE[shape], "App/x.aspx", "aspx")
    assert time.perf_counter() - start < NOT_QUADRATIC_SECONDS


def test_the_anchored_patterns_still_read_every_attribute():
    """The twin: the anchors drop no attribute a directive really carries."""
    edges = extract_imports(
        '<%@ Page Language="C#" CodeBehind="A.aspx.cs" Inherits="N.A" MasterPageFile="~/Site.Master" %>\n'
        '<%@ Register Src="~/Controls/H.ascx" TagPrefix="uc" TagName="H" %>\n',
        "App/A.aspx", "aspx",
    )
    specs = {e["specifier"] for e in edges}
    assert {"A.aspx.cs", "Site.Master", "Controls/H.ascx"} <= specs, specs
    inherits = next(e for e in edges if e["specifier"] == "A.aspx.cs" and not e.get("aspx_binding"))
    assert inherits["names"] == ["N.A"]


FILES = frozenset({
    "Web/utils.js", "Web/App/index.js", "Web/models.py", "models.py", "Utils.py",
    "Web/Admin/P.aspx", "Web/Admin/P.aspx.cs", "Web/Site.Master",
})


@pytest.mark.parametrize("namespace", ["Utils", "App", "models", "System.Data", "Web"])
def test_a_namespace_import_resolves_to_no_file(namespace):
    assert resolve_specifier(namespace, "Web/Admin/P.aspx", FILES) is None


@pytest.mark.parametrize(
    "specifier, expected",
    [("P.aspx.cs", "Web/Admin/P.aspx.cs"), ("p.ASPX.cs", "Web/Admin/P.aspx.cs"), ("Site.Master", "Web/Site.Master")],
)
def test_a_markup_path_still_resolves_as_written_in_any_ancestor(specifier, expected):
    assert resolve_specifier(specifier, "Web/Admin/P.aspx", FILES) == expected


@pytest.fixture(scope="module")
def namespace_repo(tmp_path_factory) -> tuple[str, str]:
    root: Path = tmp_path_factory.mktemp("webforms_namespace")
    files = {
        root / "Web" / "Web.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web" />\n',
        root / "Web" / "Admin" / "P.aspx": (
            '<%@ Page Language="C#" CodeBehind="P.aspx.cs" Inherits="Web.Admin.P" %>\n'
            '<%@ Import Namespace="Utils" %>\n<%@ Import Namespace="App" %>\n'
        ),
        root / "Web" / "Admin" / "P.aspx.cs": (
            "namespace Web.Admin { public partial class P : System.Web.UI.Page { } }\n"
        ),
        root / "Web" / "utils.js": "export function deadJsHelper() { return 1; }\n",
        root / "Web" / "App" / "index.js": "export function deadIndexHelper() { return 2; }\n",
        root / "Web" / "other.js": "export function deadOther() { return 3; }\n",
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    storage = str(root / ".index")
    result = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    return result["repo"], storage


def test_a_namespace_import_keeps_no_unrelated_file_alive(namespace_repo):
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    repo_id, storage = namespace_repo
    out = find_dead_code(repo_id, min_confidence=0.0, storage_path=storage)
    dead = {d["file"] for d in out.get("dead_files", [])}
    # All three are unimported; the namespace names must not single two out.
    assert {"Web/utils.js", "Web/App/index.js", "Web/other.js"} <= dead, sorted(dead)
    # The twin: the page's real codebehind is still reached through the page.
    assert "Web/Admin/P.aspx.cs" not in dead


@pytest.fixture(scope="module")
def unseen_repo(tmp_path_factory) -> tuple[str, str]:
    """A Web Forms project with a control nothing registers, and no Python at all."""
    root: Path = tmp_path_factory.mktemp("webforms_unseen")
    files = {
        root / "App" / "App.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web" />\n',
        root / "App" / "Default.aspx": '<%@ Page Language="C#" CodeBehind="Default.aspx.cs" Inherits="App._Default" %>\n',
        root / "App" / "Default.aspx.cs": (
            "namespace App { public partial class _Default : System.Web.UI.Page {\n"
            "    protected void Page_Load(object sender, System.EventArgs e) { }\n} }\n"
        ),
        root / "App" / "Orphan.ascx": '<%@ Control Language="C#" CodeBehind="Orphan.ascx.cs" Inherits="App.Orphan" %>\n',
        root / "App" / "Orphan.ascx.cs": (
            "namespace App { public partial class Orphan : System.Web.UI.UserControl {\n"
            "    protected void Render_Zzz(object sender, System.EventArgs e) { }\n} }\n"
        ),
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    storage = str(root / ".index")
    result = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    return result["repo"], storage


def test_v2_names_the_dotnet_reason_and_no_dynamic_import_that_is_not_there(unseen_repo):
    from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2

    repo_id, storage = unseen_repo
    out = get_dead_code_v2(repo_id, min_confidence=0.0, storage_path=storage)
    assert "dynamic_import_boundary" not in out, out.get("dynamic_import_boundary")
    block = out.get("unseen_consumer_boundary")
    assert block, sorted(out)
    # The orphan control, and the codebehind it imports.
    assert block["files"] >= 1 and block["symbols"] >= 1, block
    assert set(block["files_by_kind"]) <= {"build_consumed", "runtime_loadable", "imported_by_one"}, block
    assert sum(block["files_by_kind"].values()) == block["files"], block
    assert "runtime" in block["note"] and "dynamic import" not in block["note"]


def test_v2_still_leaves_signal_one_undecided_for_an_unregistered_control(unseen_repo):
    """The behaviour the block reports: with the .NET roots dropped from the
    undecided set, `unreachable_file` votes on the orphan's codebehind."""
    from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2

    repo_id, storage = unseen_repo
    out = get_dead_code_v2(repo_id, min_confidence=0.0, storage_path=storage)
    orphan = [d for d in out.get("dead_symbols", []) if "Orphan.ascx.cs" in d["id"]]
    assert orphan, [d["id"] for d in out.get("dead_symbols", [])]
    for row in orphan:
        assert "unreachable_file" not in row.get("signals", []), row


@pytest.mark.parametrize(
    "markup",
    [
        '<button onclick="Bare_Name">go</button>',
        '<input type="submit" onclick="Bare_Name" />',
        '<body onload="Bare_Name">',
        '<asp:Button ID="b" OnClick="Bare_Name" />',
    ],
    ids=["button", "input", "body", "server_tag_without_runat"],
)
def test_a_handler_on_an_element_without_runat_server_is_no_edge(markup):
    """A bare method NAME, so only the `runat` gate can refuse it."""
    assert _aspx_handler_names(markup) == []


def test_a_client_side_handler_on_a_server_control_is_no_edge():
    markup = '<asp:Button ID="b" runat="server" OnClientClick="Client_Name" OnClick="Server_Name" />'
    assert _aspx_handler_names(markup) == ["Server_Name"]


def test_the_same_bare_name_is_an_edge_once_the_control_runs_at_the_server():
    assert _aspx_handler_names('<button runat="server" onclick="Bare_Name">go</button>') == ["Bare_Name"]
