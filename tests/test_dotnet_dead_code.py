"""Dead-code tools on a Web Forms project: host-invoked files are roots.

Each "not reported" case has a twin that must still be reported, so a fix that
marks everything live fails here too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.tools.index_folder import index_folder

_CSPROJ = """<?xml version="1.0" encoding="utf-8"?>
<Project ToolsVersion="15.0" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <PropertyGroup>
    <RootNamespace>ContosoWeb</RootNamespace>
  </PropertyGroup>
  <ItemGroup>
    <Content Include="Default.aspx" />
    <Compile Include="Default.aspx.cs" />
    <EmbeddedResource Include="Strings.resx" />
  </ItemGroup>
</Project>
"""

_PAGE = """<%@ Page Language="C#" AutoEventWireup="true" CodeBehind="Default.aspx.cs" Inherits="ContosoWeb._Default" %>
<%@ Register Src="~/Controls/Header.ascx" TagPrefix="uc" TagName="Header" %>
<html><body><form runat="server">
  <asp:Button ID="btnSave" runat="server" Text="Save" OnClick="btnSave_Click" />
</form></body></html>
"""

_PAGE_CS = """namespace ContosoWeb
{
    public partial class _Default : System.Web.UI.Page
    {
        protected void Page_Load(object sender, System.EventArgs e) { }

        protected void btnSave_Click(object sender, System.EventArgs e) { }
    }
}
"""

_GLOBAL = """<%@ Application Codebehind="Global.asax.cs" Inherits="ContosoWeb.Global" Language="C#" %>
"""

_GLOBAL_CS = """namespace ContosoWeb
{
    public class Global : System.Web.HttpApplication
    {
        protected void Application_Start(object sender, System.EventArgs e) { }
    }
}
"""

_HANDLER = """<%@ WebHandler Language="C#" Class="ContosoWeb.Ping" %>
"""

_ORPHAN_ASCX = """<%@ Control Language="C#" AutoEventWireup="true" CodeBehind="Orphan.ascx.cs" Inherits="ContosoWeb.Orphan" %>
<asp:Label ID="lbl" runat="server" />
"""

_ORPHAN_ASCX_CS = """namespace ContosoWeb
{
    public partial class Orphan : System.Web.UI.UserControl
    {
        protected void Page_Load(object sender, System.EventArgs e) { }
    }
}
"""

_MASTER = """<%@ Master Language="C#" %>
<html><body><asp:ContentPlaceHolder ID="Main" runat="server" /></body></html>
"""

_PROPS = """<Project>
  <PropertyGroup><LangVersion>latest</LangVersion></PropertyGroup>
</Project>
"""

_RESX = """<?xml version="1.0" encoding="utf-8"?>
<root>
  <data name="Greeting" xml:space="preserve"><value>Hello</value></data>
</root>
"""


@pytest.fixture(scope="module")
def webforms_repo(tmp_path_factory) -> tuple[str, str]:
    root: Path = tmp_path_factory.mktemp("webforms_app")
    app = root / "ContosoWeb"
    app.mkdir()
    files = {
        app / "ContosoWeb.csproj": _CSPROJ,
        app / "Default.aspx": _PAGE,
        app / "Default.aspx.cs": _PAGE_CS,
        app / "Global.asax": _GLOBAL,
        app / "Global.asax.cs": _GLOBAL_CS,
        app / "Ping.ashx": _HANDLER,
        app / "Orphan.ascx": _ORPHAN_ASCX,
        app / "Orphan.ascx.cs": _ORPHAN_ASCX_CS,
        app / "Unused.master": _MASTER,
        app / "Controls" / "Header.ascx": '<%@ Control Language="C#" %>\n<p>header</p>\n',
        app / "Strings.resx": _RESX,
        root / "Directory.Build.props": _PROPS,
        root / "data" / "orphan.json": '{"unused": true}\n',
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    storage = str(root / ".index")
    result = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    return result["repo"], storage


def _dead_files(out: dict) -> dict[str, dict]:
    return {d["file"]: d for d in out.get("dead_files", [])}


def test_the_fixture_is_indexed_with_its_page_edge(webforms_repo):
    """Non-vacuity: without these files every "not reported" check passes trivially."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools._utils import resolve_repo

    repo_id, storage = webforms_repo
    owner, name = resolve_repo(repo_id, storage)
    index = IndexStore(base_path=storage).load_index(owner, name)
    files = set(index.source_files)
    for f in (
        "ContosoWeb/ContosoWeb.csproj", "ContosoWeb/Default.aspx",
        "ContosoWeb/Default.aspx.cs", "ContosoWeb/Global.asax",
        "ContosoWeb/Ping.ashx", "ContosoWeb/Orphan.ascx",
        "ContosoWeb/Unused.master", "ContosoWeb/Strings.resx",
        "Directory.Build.props", "data/orphan.json",
    ):
        assert f in files, f"{f} was not indexed: {sorted(files)}"


@pytest.mark.parametrize(
    "path",
    [
        "ContosoWeb/Default.aspx",
        "ContosoWeb/Global.asax",
        "ContosoWeb/Ping.ashx",
        "ContosoWeb/ContosoWeb.csproj",
        "Directory.Build.props",
    ],
)
def test_host_invoked_files_are_not_reported(webforms_repo, path):
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    repo_id, storage = webforms_repo
    dead = _dead_files(find_dead_code(repo_id, min_confidence=0.0, storage_path=storage))
    assert path not in dead, f"{path} reported dead: {dead.get(path)}"


def test_a_pages_codebehind_is_reachable_through_the_page(webforms_repo):
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    repo_id, storage = webforms_repo
    dead = _dead_files(find_dead_code(repo_id, min_confidence=0.0, storage_path=storage))
    assert "ContosoWeb/Default.aspx.cs" not in dead, dead.get("ContosoWeb/Default.aspx.cs")


def test_an_unreferenced_non_dotnet_file_is_still_reported_at_full_confidence(webforms_repo):
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    repo_id, storage = webforms_repo
    dead = _dead_files(find_dead_code(repo_id, min_confidence=0.0, storage_path=storage))
    row = dead.get("data/orphan.json")
    assert row is not None and row["reason"] == "zero_importers", sorted(dead)
    assert not row.get("confidence_capped_by"), row


def test_a_registered_user_control_is_live(webforms_repo):
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    repo_id, storage = webforms_repo
    dead = _dead_files(find_dead_code(repo_id, min_confidence=0.0, storage_path=storage))
    assert "ContosoWeb/Controls/Header.ascx" not in dead


@pytest.mark.parametrize(
    "path",
    ["ContosoWeb/Orphan.ascx", "ContosoWeb/Unused.master", "ContosoWeb/Orphan.ascx.cs"],
)
def test_unreferenced_controls_and_masters_are_capped_not_proven_dead(webforms_repo, path):
    """LoadControl, a CMS's stored control path or MasterPageFile set in code load these unseen."""
    from jcodemunch_mcp.tools._corpus_adequacy import UNPROVEN_CEILING
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    repo_id, storage = webforms_repo
    dead = _dead_files(find_dead_code(repo_id, min_confidence=0.0, storage_path=storage))
    assert path in dead, f"{path} not listed at min_confidence=0: {sorted(dead)}"
    assert dead[path]["confidence"] <= UNPROVEN_CEILING
    assert "runtime_loadable" in dead[path]["confidence_capped_by"]


def test_unreferenced_controls_are_withheld_at_the_default_threshold_and_counted(webforms_repo):
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    repo_id, storage = webforms_repo
    out = find_dead_code(repo_id, storage_path=storage)
    assert "ContosoWeb/Orphan.ascx" not in _dead_files(out)
    assert out.get("runtime_loadable_withheld", 0) >= 2, out.get("analysis_notes")


def test_build_consumed_files_are_capped_and_the_cap_is_named(webforms_repo):
    from jcodemunch_mcp.tools._corpus_adequacy import UNPROVEN_CEILING
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    repo_id, storage = webforms_repo
    out = find_dead_code(repo_id, min_confidence=0.0, storage_path=storage)
    row = _dead_files(out).get("ContosoWeb/Strings.resx")
    assert row is not None, "a capped file is still listed at min_confidence=0"
    assert row["confidence"] <= UNPROVEN_CEILING
    assert row["uncapped_confidence"] == 1.0
    assert "build_consumed" in row["confidence_capped_by"]


def test_build_consumed_files_are_withheld_at_the_default_threshold_and_counted(webforms_repo):
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    repo_id, storage = webforms_repo
    out = find_dead_code(repo_id, storage_path=storage)
    assert "ContosoWeb/Strings.resx" not in _dead_files(out)
    assert out.get("build_consumed_withheld", 0) >= 1, out.get("analysis_notes")


def _symbol_id(repo_id, storage, file, name):
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools._utils import resolve_repo

    owner, repo_name = resolve_repo(repo_id, storage)
    index = IndexStore(base_path=storage).load_index(owner, repo_name)
    ids = [s["id"] for s in index.symbols if s.get("file") == file and s.get("name") == name]
    assert ids, f"no {name} in {file}: {[s.get('name') for s in index.symbols if s.get('file') == file]}"
    return ids[0]


@pytest.mark.parametrize(
    "file,name",
    [("ContosoWeb/Orphan.ascx", "Orphan"), ("ContosoWeb/Strings.resx", "Greeting")],
)
def test_delete_preflight_does_not_certify_a_capped_file(webforms_repo, file, name):
    """The cap must reach the tool that acts on it, not only the report."""
    from jcodemunch_mcp.tools._corpus_adequacy import UNPROVEN_CEILING
    from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe

    repo_id, storage = webforms_repo
    sid = _symbol_id(repo_id, storage, file, name)
    out = check_delete_safe(repo_id, sid, cross_repo=False, include_runtime=False, storage_path=storage)
    assert out["verdict"] == "dynamic_import_boundary", out
    assert out["confidence"] <= UNPROVEN_CEILING
    action = out["recommended_action"]
    assert "dynamic import" not in action and "loaders" not in action, action
    assert "LoadControl" in action, action


def test_v2_treats_host_invoked_files_as_entry_points(webforms_repo):
    from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2

    repo_id, storage = webforms_repo
    out = get_dead_code_v2(repo_id, min_confidence=0.0, storage_path=storage)
    diag = out.get("_meta", {}).get("signal_diagnostics", {})
    assert diag.get("entry_points_detected", 0) >= 4, diag
    flagged = {
        d.get("symbol_id", ""): d.get("signals", [])
        for d in out.get("dead_symbols", [])
    }
    for sid, signals in flagged.items():
        if "Default.aspx.cs" in sid or "Orphan.ascx.cs" in sid:
            assert "unreachable_file" not in signals, (sid, signals)
