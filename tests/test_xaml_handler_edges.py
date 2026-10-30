"""XAML markup -> code-behind edges (LEDGER L-202).

A WPF/UWP/Avalonia/MAUI view wires event handlers in markup
(`<Button Click="Ok_Click"/>`) and names its code-behind class with `x:Class`.
Nothing read that, so the `.xaml` file had no edge to `Ctl.xaml.cs` and
`get_dead_code_v2` published `Ok_Click` as dead beside a truly unused method.
Web Forms markup got the same edge in #1012; this is its XAML twin.

The handler names ride on an edge to the code-behind file, and the dead-code
tools match an edge's names against the TARGET file's symbols only, so a
property value that happens to be an identifier (`HorizontalAlignment="Center"`)
can rescue a code-behind member of that name and nothing anywhere else. The
decoy test below pins that scope.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.parser.imports import extract_imports, resolve_specifier
from jcodemunch_mcp.tools.index_folder import index_folder

VIEW = """\
<UserControl x:Class="App.Views.Ctl"
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Loaded="Ctl_Loaded">
  <StackPanel x:Name="Root" HorizontalAlignment="Center">
    <Button Name="OkButton" Click="Ok_Click" Content="OK" IsDefault="True"/>
    <TextBox Text="{Binding Title}" TextChanged="Title_Changed"
             Mouse.MouseDown="Title_MouseDown"/>
    <Button Command="{Binding SaveCommand}" Content="Save as..."/>
  </StackPanel>
  <UserControl.Resources>
    <Style TargetType="Button">
      <EventSetter Event="MouseEnter" Handler="Any_MouseEnter"/>
    </Style>
  </UserControl.Resources>
</UserControl>
"""


def _edges(content, path="App/Views/Ctl.xaml"):
    return extract_imports(content, path, "xml")


def _names(edges):
    out = set()
    for e in edges:
        out.update(e["names"])
    return out


def test_a_view_gets_an_edge_to_its_code_behind():
    specs = {e["specifier"] for e in _edges(VIEW)}
    assert "Ctl.xaml.cs" in specs


def test_markup_handlers_and_the_class_ride_on_the_edge():
    names = _names(_edges(VIEW))
    assert {"Ctl", "Ctl_Loaded", "Ok_Click", "Title_Changed",
            "Title_MouseDown", "Any_MouseEnter"} <= names


@pytest.mark.parametrize("value", ["Root", "OkButton", "True", "Title", "SaveCommand", "Button"])
def test_names_bindings_and_literals_are_not_handlers(value):
    """`x:Name`/`Name` declare fields, `{Binding X}` names a view-model member,
    `True` is a literal, and `TargetType` names a type."""
    assert value not in _names(_edges(VIEW))


def test_a_value_that_is_not_a_bare_identifier_is_not_a_handler():
    assert not any(" " in n or "." in n for n in _names(_edges(VIEW)))


def test_markup_without_x_class_has_no_code_behind_edge():
    dictionary = (
        '<ResourceDictionary xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">\n'
        '  <Style TargetType="Button"><EventSetter Event="Click" Handler="X_Click"/></Style>\n'
        "</ResourceDictionary>\n"
    )
    assert _edges(dictionary) == []


def test_plain_xml_gets_no_edges():
    assert extract_imports('<root x:Class="A.B" Click="Ok_Click"/>', "data/config.xml", "xml") == []


SOURCES = {
    "App/Views/Ctl.xaml",
    "App/Views/Ctl.xaml.cs",
    "Ctl.xaml.cs",  # a decoy at repo root
}


def test_the_code_behind_sibling_resolves():
    assert resolve_specifier("Ctl.xaml.cs", "App/Views/Ctl.xaml", SOURCES) == "App/Views/Ctl.xaml.cs"


def test_resolution_is_case_insensitive_like_the_build():
    sources = {"App/Views/Ctl.xaml", "App/Views/ctl.xaml.cs"}
    assert resolve_specifier("Ctl.xaml.cs", "App/Views/Ctl.xaml", sources) == "App/Views/ctl.xaml.cs"


def test_a_missing_code_behind_stays_unresolved():
    assert resolve_specifier("Nope.xaml.cs", "App/Views/Ctl.xaml", SOURCES) is None


def test_a_same_named_file_in_an_ancestor_is_not_the_code_behind():
    """Unlike Web Forms paths, a code-behind is never app-root relative."""
    sources = {"App/Views/Ctl.xaml", "Ctl.xaml.cs", "App/Ctl.xaml.cs"}
    assert resolve_specifier("Ctl.xaml.cs", "App/Views/Ctl.xaml", sources) is None


def test_a_commented_out_element_names_no_handler():
    """The second element inside one comment is a tag of its own to a scanner
    that does not know the comment is there."""
    view = VIEW.replace(
        "<StackPanel",
        '<!-- <TextBlock Text="old"/>\n       <Button Click="Old_Click"/> -->\n  <StackPanel',
    )
    assert "Old_Click" not in _names(_edges(view))


# ---------------------------------------------------------------------------
# End to end: what the dead-code tools publish
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def wpf_repo(tmp_path_factory) -> tuple[str, str]:
    root: Path = tmp_path_factory.mktemp("wpf_handlers")
    files = {
        root / "App" / "App.csproj": (
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
            "<UseWPF>true</UseWPF></PropertyGroup></Project>\n"
        ),
        root / "App" / "Ctl.xaml": (
            '<UserControl x:Class="App.Ctl"\n'
            '  xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"\n'
            '  xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">\n'
            '  <Button Click="Ok_Click" HorizontalAlignment="Center" Content="OK"/>\n'
            "</UserControl>\n"
        ),
        root / "App" / "Ctl.xaml.cs": (
            "namespace App { public partial class Ctl : System.Windows.Controls.UserControl {\n"
            "    public Ctl() { InitializeComponent(); }\n"
            "    private void Ok_Click(object sender, System.Windows.RoutedEventArgs e) { }\n"
            "    private void Never_Used(object sender, System.Windows.RoutedEventArgs e) { }\n"
            "} }\n"
        ),
        # A decoy: `Center` is a property value in the markup, and this unused
        # method of that name lives in ANOTHER file, so the edge must not reach it.
        root / "Lib" / "Util.cs": (
            "namespace Lib { public static class Util {\n"
            "    public static void Center() { }\n"
            "} }\n"
        ),
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    storage = str(root / ".index")
    result = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    return result["repo"], storage


def _v2_rows(repo_id, storage):
    from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2

    out = get_dead_code_v2(repo_id, min_confidence=0.0, storage_path=storage)
    return {d["name"]: d for d in out.get("dead_symbols", [])}


def _v2(repo_id, storage):
    return {n: d["confidence"] for n, d in _v2_rows(repo_id, storage).items()}


def test_v2_does_not_report_a_markup_wired_handler(wpf_repo):
    dead = _v2(*wpf_repo)
    assert dead.get("Ok_Click", 0.0) == 0.0, dead


def test_v2_still_lists_the_unused_method_beside_it(wpf_repo):
    """The twin: an unused method in the same code-behind is still reported."""
    assert "Never_Used" in _v2(*wpf_repo)


def test_a_property_value_keeps_nothing_in_another_file_alive(wpf_repo):
    """`Center` is in the markup's names; the decoy of that name in another file
    keeps every signal it fires. Its CONFIDENCE is not asserted: v2 stops
    counting a signal that fires on nearly every symbol, and in a repo this
    small, two live symbols move that line for everyone."""
    row = _v2_rows(*wpf_repo).get("Center")
    assert row is not None
    assert {"unreachable_file", "no_callers"} <= set(row["signals"]), row


def test_find_dead_code_no_longer_calls_the_handler_provably_dead(wpf_repo):
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    repo_id, storage = wpf_repo
    out = find_dead_code(repo_id, min_confidence=0.0, storage_path=storage)
    conf = {d["symbol_id"]: d["confidence"] for d in out.get("dead_symbols", [])}
    handler = [c for sid, c in conf.items() if sid.endswith("::Ctl.Ok_Click#method")]
    assert all(c < 0.8 for c in handler), conf
