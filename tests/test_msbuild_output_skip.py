"""`obj/` and `bin/` beside a .NET project file are MSBuild output, not corpus.

Found on a production ASP.NET Web Forms app with no `.gitignore`: the walk indexed
`obj/Release/Package/PackageTmp/` and `obj/Release/AspnetCompileMerge/Source/`,
which are COPIES of the same source, so every symbol in them competed with its own
original in ranking.

⚠ The load-bearing test in this file is
`test_bin_without_dotnet_project_is_still_indexed`. `obj/` is close to
unambiguous, but `bin/` holds committed hand-written entrypoints in Node, Ruby and
Go projects, so a name-only rule would delete real source. The project-file marker
is the difference between asserting the property and asserting one instance of it.
"""

from pathlib import Path

import pytest

from jcodemunch_mcp.security import (
    is_dotnet_project_dir,
    is_msbuild_output_directory,
)
from jcodemunch_mcp.tools.index_folder import discover_local_files


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("marker", ["App.csproj", "Legacy.vbproj", "Tool.fsproj"])
@pytest.mark.parametrize("dir_name", ["obj", "bin", "OBJ", "Bin"])
def test_output_dir_beside_any_dotnet_project_marker(dir_name, marker):
    assert is_msbuild_output_directory(dir_name, [marker, "Program.cs"]) is True


@pytest.mark.parametrize("solution", ["Solution.sln", "Solution.slnx"])
@pytest.mark.parametrize("dir_name", ["obj", "bin"])
def test_a_solution_file_alone_is_not_a_marker(dir_name, solution):
    """MSBuild writes bin/ and obj/ beside the PROJECT, not the solution. A solution
    file only says the directory is a solution root."""
    assert is_msbuild_output_directory(dir_name, [solution, "deploy.py"]) is False


@pytest.mark.parametrize("dir_name", ["obj", "bin"])
def test_output_dir_without_marker_is_not_output(dir_name):
    """A bare obj/ or bin/ with no .NET project beside it is left alone."""
    assert is_msbuild_output_directory(dir_name, ["package.json", "index.js"]) is False


@pytest.mark.parametrize("dir_name", ["src", "lib", "binaries", "object", "objects"])
def test_unrelated_names_are_never_output(dir_name):
    """Substring lookalikes must not match -- `binaries` is not `bin`."""
    assert is_msbuild_output_directory(dir_name, ["App.csproj"]) is False


def test_marker_detection_is_case_insensitive_and_ignores_other_files():
    assert is_dotnet_project_dir(["APP.CSPROJ"]) is True
    assert is_dotnet_project_dir(["notes.txt", "Makefile"]) is False
    assert is_dotnet_project_dir([]) is False


def test_csproj_user_suffix_does_not_count_as_a_marker():
    """`.csproj.user` is per-developer IDE state, not a project file. It ends with
    `.user`, so a naive `in` check would still see `.csproj` inside it."""
    assert is_dotnet_project_dir(["App.csproj.user"]) is False


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


def _dotnet_project(root):
    """A .NET project with real source plus a publish-shaped copy under obj/."""
    (root / "App.csproj").write_text("<Project />", encoding="utf-8")
    (root / "Program.cs").write_text("class Program { void Main() {} }", encoding="utf-8")

    published = root / "obj" / "Release" / "Package" / "PackageTmp"
    published.mkdir(parents=True)
    (published / "Program.cs").write_text(
        "class Program { void Main() {} }", encoding="utf-8"
    )

    compiled = root / "bin"
    compiled.mkdir()
    (compiled / "Generated.cs").write_text("class Generated {}", encoding="utf-8")
    return root


def test_msbuild_output_is_pruned_and_counted(tmp_path):
    files, warnings, skip_counts = discover_local_files(_dotnet_project(tmp_path))

    names = {Path(f).name for f in files}
    assert "Program.cs" in names
    assert "Generated.cs" not in names, "bin/ leaked into the corpus"

    parts = {part for f in files for part in Path(f).parts}
    assert "obj" not in parts and "bin" not in parts

    # obj/ and bin/ are each pruned once, at the project root.
    assert skip_counts["msbuild_output"] == 2
    assert warnings == []


def test_the_duplicate_source_copy_is_what_gets_dropped(tmp_path):
    """The harm was the same symbol indexed twice, so assert on the count."""
    files, _, _ = discover_local_files(_dotnet_project(tmp_path))
    program_copies = [
        f for f in files if Path(f).name == "Program.cs"
    ]
    assert len(program_copies) == 1


def test_bin_without_dotnet_project_is_still_indexed(tmp_path):
    """THE guard. A Node/Ruby/Go `bin/` holds real hand-written entrypoints.

    Without the project-file marker this rule would silently delete them, which is
    strictly worse than the duplicate-source problem it exists to fix.
    """
    (tmp_path / "package.json").write_text('{"name":"cli"}', encoding="utf-8")
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "cli.js").write_text("#!/usr/bin/env node\nmain();", encoding="utf-8")

    files, _, skip_counts = discover_local_files(tmp_path)

    names = {Path(f).name for f in files}
    assert "cli.js" in names, "a committed bin/ entrypoint was dropped"
    assert skip_counts["msbuild_output"] == 0


def test_obj_in_a_non_dotnet_project_is_left_alone(tmp_path):
    """Same reasoning as bin/, applied to obj/: no marker, no prune."""
    (tmp_path / "go.mod").write_text("module x", encoding="utf-8")
    obj = tmp_path / "obj"
    obj.mkdir()
    (obj / "helper.go").write_text("package obj", encoding="utf-8")

    files, _, skip_counts = discover_local_files(tmp_path)

    assert {Path(f).name for f in files} >= {"helper.go"}
    assert skip_counts["msbuild_output"] == 0


def test_nested_project_output_is_pruned_per_project(tmp_path):
    """A solution with two projects prunes each project's own output."""
    (tmp_path / "Solution.sln").write_text("Microsoft Visual Studio Solution File", encoding="utf-8")
    for name in ("Web", "Api"):
        proj = tmp_path / name
        proj.mkdir()
        (proj / f"{name}.csproj").write_text("<Project />", encoding="utf-8")
        (proj / f"{name}.cs").write_text(f"class {name} {{}}", encoding="utf-8")
        (proj / "obj").mkdir()
        (proj / "obj" / f"{name}.copy.cs").write_text(f"class {name} {{}}", encoding="utf-8")

    files, _, skip_counts = discover_local_files(tmp_path)

    names = {Path(f).name for f in files}
    assert {"Web.cs", "Api.cs"} <= names
    assert not any(n.endswith(".copy.cs") for n in names)
    assert skip_counts["msbuild_output"] == 2


def test_bin_beside_a_solution_file_is_indexed_in_a_mixed_layout(tmp_path):
    """A solution root is not a project root. `bin/` beside `Mixed.sln` holds a
    hand-written script; the project's own output lives under `src/App/`. Pruning
    the root `bin/` here would delete real source and let an absence claim stand
    over it, so only the project directory's output goes."""
    (tmp_path / "Mixed.sln").write_text(
        "Microsoft Visual Studio Solution File", encoding="utf-8"
    )
    scripts = tmp_path / "bin"
    scripts.mkdir()
    (scripts / "deploy.py").write_text("def deploy():\n    pass\n", encoding="utf-8")

    app = tmp_path / "src" / "App"
    app.mkdir(parents=True)
    (app / "App.csproj").write_text("<Project />", encoding="utf-8")
    (app / "Program.cs").write_text("class Program {}", encoding="utf-8")
    (app / "obj").mkdir()
    (app / "obj" / "Program.copy.cs").write_text("class Program {}", encoding="utf-8")

    files, _, skip_counts = discover_local_files(tmp_path)

    names = {Path(f).name for f in files}
    assert "deploy.py" in names, "a hand-written bin/ beside a .sln was pruned"
    assert "Program.cs" in names
    assert "Program.copy.cs" not in names
    assert skip_counts["msbuild_output"] == 1


def test_skip_is_an_ordinary_exclusion_not_a_withheld_reason(tmp_path):
    """Build output is derived data, so it defines the corpus rather than being a
    file we refused -- same class as `cache_dir` and `gitignore`. If this ever
    becomes withheld, absence claims break on every .NET project."""
    from jcodemunch_mcp.tools.index_folder import WITHHELD_SKIP_REASONS

    assert "msbuild_output" not in WITHHELD_SKIP_REASONS


def test_explicit_false_disables_and_garbage_does_not(tmp_path, monkeypatch):
    """Mirrors `get_respect_cachedir_tag`: a typo must not re-admit build output."""
    from jcodemunch_mcp import security

    calls = {}

    def fake_get(key, default=None, repo=None):
        calls["key"] = key
        return calls["value"]

    monkeypatch.setattr(security._config, "get", fake_get)

    calls["value"] = False
    assert security.get_skip_msbuild_output() is False
    for garbage in (True, None, "no", 0, "", "false"):
        calls["value"] = garbage
        assert security.get_skip_msbuild_output() is True, garbage


# ---------------------------------------------------------------------------
# `.vs/` — Visual Studio per-solution machine state
# ---------------------------------------------------------------------------


def test_vs_directory_is_skipped(tmp_path):
    """`.vs/` survived the obj/bin prune because it is not build output.

    `*.dtbcache.json` is the file that reached the corpus, since `.json` is an
    indexed extension. Unlike obj/bin this needs no marker: nothing in `.vs/` is
    hand-written.
    """
    (tmp_path / "App.csproj").write_text("<Project />", encoding="utf-8")
    (tmp_path / "Program.cs").write_text("class P {}", encoding="utf-8")
    vs = tmp_path / ".vs"
    vs.mkdir()
    (vs / "App.csproj.dtbcache.json").write_text('{"cache":1}', encoding="utf-8")

    files, _, skip_counts = discover_local_files(tmp_path)

    names = {Path(f).name for f in files}
    assert "Program.cs" in names
    assert "App.csproj.dtbcache.json" not in names
    assert skip_counts["skip_dir"] >= 1
    # counted as an ordinary name-based skip, not as msbuild output
    assert skip_counts["msbuild_output"] == 0


def test_vs_is_skipped_without_a_dotnet_project_present(tmp_path):
    """No marker required -- contrast with obj/bin, which need one."""
    (tmp_path / "notes.py").write_text("x = 1", encoding="utf-8")
    vs = tmp_path / ".vs"
    vs.mkdir()
    (vs / "state.json").write_text("{}", encoding="utf-8")

    files, _, _ = discover_local_files(tmp_path)
    assert {Path(f).name for f in files} == {"notes.py"}


def test_editor_config_dirs_are_not_skipped(tmp_path):
    """`.idea/` and `.vscode/` hold committed, hand-edited config a user may want
    to search. Adding them alongside `.vs` would be a different decision, so this
    pins that it was not made."""
    from jcodemunch_mcp.security import _SKIP_DIRECTORY_NAMES

    assert ".vs" in _SKIP_DIRECTORY_NAMES
    assert ".idea" not in _SKIP_DIRECTORY_NAMES
    assert ".vscode" not in _SKIP_DIRECTORY_NAMES


def test_vs_skip_is_removable_per_project():
    """Every name-based skip must stay recoverable via `exclude_skip_directories`,
    the escape hatch v1.108.234 established for this class of addition."""
    from jcodemunch_mcp import security

    original = security._config.get
    try:
        security._config.get = lambda k, d=None, repo=None: (
            [".vs"] if k == "exclude_skip_directories" else original(k, d)
        )
        assert ".vs" not in security.get_skip_directories()
    finally:
        security._config.get = original
