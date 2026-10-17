"""Which evidence makes a repository read as .NET for the schema cap's advice (LEDGER L-184).

`has_dotnet_evidence` picks only the KIND of an unseen-consumer cap and so its
advice, never whether a schema is capped. It missed a Razor or Blazor tree,
which carries no `.cs`, so its `.xsd` got the neutral `schema_consumer`
advice. And it read any `.fs` as F#, so a GLSL fragment shader made a Maven
repository .NET and its schema got the project-file advice.

It reads `index.source_files`, so a spelling the index does not carry (`.vb`,
`.vbhtml`, `.sln`, `.vcxproj`) cannot be evidence; that is L-184's residue.

The end-to-end trees (find_dead_code, check_delete_safe) are in
`tests/test_aspx_value_forms.py` SCHEMA_TREES.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.tools import _entry_points
from jcodemunch_mcp.tools.index_folder import index_folder

DOTNET = {
    "razor page": ["Pages/Index.cshtml", "orders.xsd"],
    "blazor component": ["Shared/Nav.razor", "orders.xsd"],
    "f# project": ["Lib/Lib.fsproj", "Lib/Library.fs", "orders.xsd"],
    "vb project": ["App/App.vbproj", "orders.xsd"],
    "f# script": ["build.fsx", "orders.xsd"],
    "f# signature": ["Lib/Library.fsi", "orders.xsd"],
    "c# source, as before": ["Program.cs", "orders.xsd"],
}

OTHER_STACKS = {
    "glsl fragment shader": ["pom.xml", "src/main/java/App.java", "src/main/resources/shader.fs", "orders.xsd"],
    "forth source": ["words.fs", "orders.xsd"],
    "schemas only, as before": ["orders.xsd"],
    "plain c++": ["CMakeLists.txt", "main.cpp", "orders.xsd"],
}


@pytest.mark.parametrize("case", sorted(DOTNET))
def test_dotnet_evidence_is_found(case):
    files = DOTNET[case]
    assert _entry_points.has_dotnet_evidence(files) is True
    assert _entry_points.unseen_consumer(files[-1], dotnet=True) == "build_consumed"


@pytest.mark.parametrize("case", sorted(OTHER_STACKS))
def test_no_dotnet_evidence_where_there_is_none(case):
    files = OTHER_STACKS[case]
    assert _entry_points.has_dotnet_evidence(files) is False
    assert _entry_points.unseen_consumer(files[-1], dotnet=False) == "schema_consumer"


_SCHEMA = (
    '<?xml version="1.0"?>\n<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">\n'
    '  <xs:element name="order" type="xs:string"/>\n</xs:schema>\n'
)


@pytest.fixture(scope="module")
def build_items_repo(tmp_path_factory) -> tuple[str, str]:
    root: Path = tmp_path_factory.mktemp("build_items")
    files = {
        "App/App.csproj": '<Project Sdk="Microsoft.NET.Sdk" />\n',
        "App/Program.cs": "namespace App { public static class Program { public static void Main() { } } }\n",
        "App/Orders.xsd": _SCHEMA,
        # Every shape that could give an unseen-consumer file an edge or a function: a schema
        # that includes and imports another, a props file that imports a project, a targets file
        # with an inline C# task, and a XAML control with inline code and a wired handler.
        "App/Invoice.xsd": (
            '<?xml version="1.0"?>\n<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">\n'
            '  <xs:include schemaLocation="Orders.xsd"/>\n'
            '  <xs:import namespace="urn:common" schemaLocation="Common.xsd"/>\n</xs:schema>\n'
        ),
        "App/Common.xsd": _SCHEMA,
        "App/Strings.resx": '<root><data name="Greeting"><value>Hi</value></data></root>\n',
        "App/Shared.props": '<Project><Import Project="Build.targets" /></Project>\n',
        "App/Build.targets": (
            '<Project><UsingTask TaskName="Hello" TaskFactory="RoslynCodeTaskFactory"'
            ' AssemblyFile="$(MSBuildToolsPath)\\Microsoft.Build.Tasks.Core.dll">\n'
            '  <Task><Code Type="Fragment" Language="cs"><![CDATA[ void Run() { Log.LogMessage("hi"); } ]]></Code></Task>\n'
            '</UsingTask><Target Name="Gen"><Hello /></Target></Project>\n'
        ),
        "App/Ctl.xaml": (
            '<UserControl x:Class="App.Ctl" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">\n'
            '  <Button x:Name="Ok" Click="Ok_Click"/>\n'
            '  <x:Code><![CDATA[ void Ok_Click(object s, object e) { } ]]></x:Code>\n</UserControl>\n'
        ),
        "App/Ctl.xaml.cs": "namespace App { public partial class Ctl { void Helper() { } } }\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    storage = str(root / ".index")
    r = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    assert r.get("success"), r
    return r["repo"], storage


def test_v2_has_nothing_to_decide_for_a_schema_or_build_item(build_items_repo):
    """Why `get_dead_code_v2`'s cap call site changes no verdict for these kinds.

    v2 judges functions and methods only, and a schema, resource or MSBuild
    file holds none and imports nothing, so neither the file nor anything it
    imports reaches the undecided set. The call site acts on `.ascx`/`.master`
    (`tests/test_aspx_review_guards.py`). If XML-family files ever gain a
    function or an import edge, this fails, and the cap starts to matter here.
    """
    from jcodemunch_mcp.storage.index_store import IndexStore
    from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2

    repo, storage = build_items_repo
    owner, name = repo.split("/", 1)
    index = IndexStore(base_path=storage).load_index(owner, name)
    capped = {f for f in index.source_files if _entry_points.unseen_consumer(f, dotnet=True)}
    assert capped == {
        "App/Orders.xsd", "App/Invoice.xsd", "App/Common.xsd", "App/Strings.resx",
        "App/Shared.props", "App/Build.targets", "App/Ctl.xaml",
    }, capped
    functions = [s for s in index.symbols if s.get("file") in capped and s.get("kind") in ("function", "method")]
    assert functions == [], functions
    imported = {i["specifier"] for f in capped for i in (index.imports or {}).get(f, [])}
    assert imported == set(), imported

    out = get_dead_code_v2(repo, min_confidence=0.0, storage_path=storage)
    assert "unseen_consumer_boundary" not in out, out.get("unseen_consumer_boundary")
    assert not [d for d in out.get("dead_symbols", []) if d.get("file") in capped]
