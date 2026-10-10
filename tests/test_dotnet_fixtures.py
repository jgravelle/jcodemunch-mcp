"""Parse real .NET fixtures (see fixtures/dotnet/SOURCES.md).

Some tests pin current weaknesses, not desired results, so changes show up.
"""

from pathlib import Path

import pytest

from jcodemunch_mcp.parser import parse_file
from jcodemunch_mcp.parser.languages import get_language_for_path

FIXTURES = Path(__file__).parent / "fixtures" / "dotnet"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8-sig")


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

VENDORED = [
    "webforms_default.aspx",
    "webforms_default.aspx.cs",
    "webforms_site.master",
    "webforms_site.master.designer.cs",
    "webforms_viewswitcher.ascx",
    "webforms_viewswitcher.ascx.cs",
    "webforms_global.asax",
    "webforms_web.config",
    "oldstyle_aspnetapp.csproj",
    "wpf_dialogbox_mainwindow.xaml",
    "wpf_wizard_resources.resx",
]

HAND_WRITTEN = ["webforms_handlers.aspx", "webforms_handlers.aspx.cs"]


@pytest.mark.parametrize("name", VENDORED)
def test_vendored_fixture_names_its_source_commit_and_licence(name):
    text = _read(name)
    head = text[:1200]
    assert "Source: https://github.com/microsoft/" in head, name
    assert "MIT License" in head, name
    assert "/blob/main/" not in head, f"{name} pins a branch, not a commit"
    sha = head.split("/blob/")[1].split("/")[0]
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), sha


@pytest.mark.parametrize("name", HAND_WRITTEN)
def test_hand_written_fixtures_say_so(name):
    head = _read(name)[:600]
    assert "HAND-WRITTEN" in head, name
    assert "No upstream source" in head, name


def test_sources_md_documents_every_fixture():
    doc = (FIXTURES / "SOURCES.md").read_text(encoding="utf-8")
    for name in VENDORED + HAND_WRITTEN:
        assert name in doc, f"{name} is undocumented in SOURCES.md"
    on_disk = {
        p.name for p in FIXTURES.iterdir() if p.is_file() and p.name != "SOURCES.md"
    }
    assert on_disk == set(VENDORED + HAND_WRITTEN), (
        "fixture directory and SOURCES.md disagree"
    )


# ---------------------------------------------------------------------------
# Extension mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("wpf_dialogbox_mainwindow.xaml", "xml"),
        ("wpf_wizard_resources.resx", "xml"),
        ("oldstyle_aspnetapp.csproj", "xml"),
        ("webforms_default.aspx.cs", "csharp"),
        ("webforms_site.master.designer.cs", "csharp"),
    ],
)
def test_fixture_paths_resolve_to_expected_language(name, expected):
    assert get_language_for_path(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "webforms_default.aspx",
        "webforms_site.master",
        "webforms_viewswitcher.ascx",
        "webforms_global.asax",
        "webforms_handlers.aspx",
    ],
)
def test_web_forms_extensions_now_resolve_to_aspx(name):
    assert get_language_for_path(name) == "aspx"


def test_dotnet_config_extension_remains_unmapped():
    """web.config holds plaintext credentials that redaction does not cover."""
    assert get_language_for_path("webforms_web.config") is None


# ---------------------------------------------------------------------------
# The xml extractor against real .NET XML
# ---------------------------------------------------------------------------


def test_resx_extracts_named_resources_properly():
    symbols = parse_file(_read("wpf_wizard_resources.resx"), "Resources.resx", "xml")
    names = {s.name for s in symbols}
    assert len(symbols) > 1
    assert "root" in names
    assert len(names - {"root"}) >= 1


def test_xaml_parses_without_raising_but_yields_almost_nothing(name="wpf_dialogbox_mainwindow.xaml"):
    """Pins a weakness: x:Name and Click= wiring are invisible to the xml extractor."""
    source = _read(name)
    assert 'Click="fileOpen_Click"' in source
    assert 'Name="editFindMenuItem"' in source
    assert 'x:Class="DialogBox.MainWindow"' in source

    symbols = parse_file(source, "MainWindow.xaml", "xml")
    assert symbols, "must not regress to zero symbols or raise"
    extracted = {s.name for s in symbols}
    assert "Window" in extracted
    assert not any("_Click" in n for n in extracted)
    assert "editFindMenuItem" not in extracted


def test_oldstyle_csproj_declares_the_codebehind_pairing_we_cannot_yet_read():
    """Pins a gap: the `<DependentUpon>` pairing is in the file but not the symbols."""
    source = _read("oldstyle_aspnetapp.csproj")
    assert "<DependentUpon>Default.aspx</DependentUpon>" in source
    assert '<Compile Include="Default.aspx.cs">' in source

    symbols = parse_file(source, "aspnetapp.csproj", "xml")
    extracted = {s.name for s in symbols}
    assert "Project" in extracted
    assert not any("Default.aspx" in n for n in extracted)


def test_web_config_would_yield_real_wiring_if_it_were_mapped():
    """Parsed directly, bypassing the extension map."""
    symbols = parse_file(_read("webforms_web.config"), "Web.config", "xml")
    names = {s.name for s in symbols}
    assert "configuration" in names
    assert len(symbols) > 3, "web.config carries name=/key= entries the extractor reads"


# ---------------------------------------------------------------------------
# C# XML doc comments
# ---------------------------------------------------------------------------


def test_designer_control_fields_extract_the_xml_doc_tag_as_the_summary():
    """Pins a defect: the summary is the literal `<summary>`, not the doc text."""
    source = _read("webforms_site.master.designer.cs")
    assert "/// <summary>" in source
    assert "/// HeadContent control." in source

    symbols = parse_file(source, "Site.Master.designer.cs", "csharp")
    fields = [s for s in symbols if s.name == "HeadContent"]
    assert fields, "the generated control field should be extracted"

    summary = (fields[0].summary or "").strip()
    docstring = (fields[0].docstring or "").strip()
    blob = summary + " " + docstring
    assert "<summary>" in blob, (
        "the defect appears to be fixed -- if so, assert the real text instead"
    )
    assert "HeadContent control." not in summary, (
        "summary now carries the real doc text; update this test, the bug is fixed"
    )


def test_designer_files_are_mostly_generated_field_noise():
    symbols = parse_file(
        _read("webforms_site.master.designer.cs"), "Site.Master.designer.cs", "csharp"
    )
    kinds = {s.kind for s in symbols if s.name != "SiteMaster"}
    assert kinds <= {"constant", "field", "property"}, kinds
    assert not any(s.kind == "method" for s in symbols)


# ---------------------------------------------------------------------------
# The hand-written handler fixture
# ---------------------------------------------------------------------------


def test_handler_fixture_contains_every_miscount_case():
    """Guards the fixture's awkward cases against a later tidy-up."""
    markup = _read("webforms_handlers.aspx")

    assert 'OnClick="btnSave_Click"' in markup
    assert 'onclick="btnFind_Click"' in markup
    assert 'OnClientClick="return confirmDelete();"' in markup
    assert 'OnSelectedIndexChanged="ddlRegion_SelectedIndexChanged"' in markup
    assert 'OnRowCommand="gvOrders_RowCommand"' in markup
    # attribute names containing "on" that a boundary-less scan would capture
    assert 'ButtonType="Button"' in markup
    assert 'HorizontalAlign="Center"' in markup
    assert 'ControlToValidate="btnSave"' in markup
    assert 'OnClick="btnGhost_Click"' in markup


def test_handler_fixture_codebehind_defines_all_but_the_ghost():
    code = _read("webforms_handlers.aspx.cs")
    for handler in (
        "btnSave_Click",
        "btnFind_Click",
        "btnDelete_Click",
        "ddlRegion_SelectedIndexChanged",
        "gvOrders_RowCommand",
        "gvOrders_RowDataBound",
    ):
        assert f"void {handler}(" in code, handler
    # Check for the definition: the bare name appears in the fixture's header comment.
    assert "void btnGhost_Click(" not in code, "the ghost handler must stay undefined"
    assert "void Page_Load(" in code and "void Page_Init(" in code


def test_both_halves_of_the_handler_edge_are_now_in_the_corpus():
    """Both the codebehind definitions and the referencing markup are indexable."""
    symbols = parse_file(
        _read("webforms_handlers.aspx.cs"), "webforms_handlers.aspx.cs", "csharp"
    )
    names = {s.name for s in symbols}
    assert {"btnSave_Click", "btnFind_Click", "Page_Load"} <= names

    assert get_language_for_path("webforms_handlers.aspx") == "aspx"
    markup = _read("webforms_handlers.aspx")
    assert 'OnClick="btnSave_Click"' in markup
    assert parse_file(markup, "webforms_handlers.aspx", "aspx"), (
        "markup must yield symbols so the file is a first-class index entry"
    )
