"""A NuGet `packages/` restore tree is vendored dependency data, not corpus.

Measured on this box across 36 directories literally named `packages`: 26 are NuGet
restore trees and 10 are hand-written source.

⚠ On a solution whose `.gitignore` lists `packages`, this rule changes WHICH rule
excluded those files and not WHAT is indexed -- measured at zero file difference.
Where it moves the corpus is a solution with no such entry: two on this box went
9286 -> 7091 files (24%) and 1322 -> 750 (43%). Same caveat `skip_msbuild_output`
carries, and the reason `test_restore_tree_is_pruned_and_counted` asserts the
`nuget_packages` COUNTER rather than only a file-count delta.

⚠ The load-bearing test in this file is `test_flutter_shaped_packages_dir_is_kept`.
`packages/` is a legitimate hand-written source directory in other ecosystems
(Flutter's monorepo is `packages/flutter`, `packages/flutter_test`), so a name-only
rule would delete real source. The `.nupkg` artifact marker -- a file NuGet itself
wrote, under the same name as its directory -- is the difference between asserting
the property and asserting one instance of it.

⚠ Two layouts are deliberately NOT matched and have tests saying so, because both
err toward indexing: a v2 tree whose `.nupkg` files were stripped after restore,
and the v3 global cache which nests the artifact one level deeper.
"""

from pathlib import Path

import pytest

from jcodemunch_mcp import config as _config
from jcodemunch_mcp.security import (
    get_skip_nuget_packages,
    is_nuget_packages_directory,
)
from jcodemunch_mcp.tools.index_folder import (
    _build_index_filters,
    _build_skip_dirs_regex,
    _should_index_file,
    discover_local_files,
)


def _restored_package(packages: Path, ident: str, *, artifact: bool = True) -> Path:
    """One package as NuGet's classic v2 restore writes it."""
    pkg = packages / ident
    (pkg / "lib" / "net45").mkdir(parents=True)
    (pkg / "lib" / "net45" / f"{ident}.dll").write_bytes(b"MZ")
    if artifact:
        (pkg / f"{ident}.nupkg").write_bytes(b"PK\x03\x04")
    return pkg


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------


def test_v2_restore_layout_is_a_packages_dir(tmp_path):
    packages = tmp_path / "packages"
    _restored_package(packages, "Newtonsoft.Json.6.0.3")
    assert is_nuget_packages_directory("packages", packages) is True


@pytest.mark.parametrize("dir_name", ["packages", "Packages", "PACKAGES"])
def test_directory_name_match_is_case_insensitive(tmp_path, dir_name):
    packages = tmp_path / dir_name
    _restored_package(packages, "NLog.4.2.2")
    assert is_nuget_packages_directory(dir_name, packages) is True


def test_flutter_shaped_packages_dir_is_kept(tmp_path):
    """THE guard. Flutter, Dart and JS monorepos put real source under packages/.

    Without the artifact marker this rule would silently delete an entire
    monorepo's source, which is strictly worse than the vendored-dependency
    problem it exists to fix.
    """
    packages = tmp_path / "packages"
    for member in ("flutter", "flutter_test", "flutter_tools"):
        (packages / member / "lib").mkdir(parents=True)
        (packages / member / "lib" / "main.dart").write_text("void main() {}", encoding="utf-8")
    assert is_nuget_packages_directory("packages", packages) is False


@pytest.mark.parametrize(
    "dir_name", ["src", "package", "packages_old", "mypackages", "lib"]
)
def test_unrelated_names_are_never_a_packages_dir(tmp_path, dir_name):
    """Substring lookalikes must not match -- `mypackages` is not `packages`."""
    d = tmp_path / dir_name
    _restored_package(d, "NLog.4.2.2")
    assert is_nuget_packages_directory(dir_name, d) is False


def test_artifact_name_must_equal_the_directory_name(tmp_path):
    """NuGet writes `<Id>.<Version>/<Id>.<Version>.nupkg`. A differently-named
    .nupkg beside it is not that layout, and matching on the suffix alone would
    reduce this to a name-shaped guess about the contents."""
    packages = tmp_path / "packages"
    pkg = packages / "Newtonsoft.Json.6.0.3"
    pkg.mkdir(parents=True)
    (pkg / "something-else.nupkg").write_bytes(b"PK\x03\x04")
    assert is_nuget_packages_directory("packages", packages) is False


def test_artifact_must_be_a_file_not_a_directory(tmp_path):
    packages = tmp_path / "packages"
    (packages / "NLog.4.2.2" / "NLog.4.2.2.nupkg").mkdir(parents=True)
    assert is_nuget_packages_directory("packages", packages) is False


def test_one_restored_package_among_many_is_enough(tmp_path):
    """Measured: one real dir had 48 children and 47 artifacts. Requiring ALL of
    them would have left that tree indexed."""
    packages = tmp_path / "packages"
    for i in range(5):
        _restored_package(packages, f"Stripped.1.0.{i}", artifact=False)
    _restored_package(packages, "NLog.4.2.2")
    assert is_nuget_packages_directory("packages", packages) is True


def test_empty_packages_dir_is_not_matched(tmp_path):
    packages = tmp_path / "packages"
    packages.mkdir()
    assert is_nuget_packages_directory("packages", packages) is False


def test_missing_path_never_raises(tmp_path):
    assert is_nuget_packages_directory("packages", tmp_path / "gone") is False


def test_stripped_v2_layout_is_a_known_false_negative(tmp_path):
    """Measured on a real DNN module tree: children named `<Id>.<Version>` holding
    only `lib/`, artifacts removed after restore. Not matched, deliberately -- the
    remaining content is DLLs, which `is_binary_extension` already drops, and
    loosening to the `<Id>.<SemVer>` NAME would key on a convention rather than on
    evidence the writer left."""
    packages = tmp_path / "packages"
    for ident in ("Castle.Core.4.3.1", "CsvHelper.2.16.0.0"):
        _restored_package(packages, ident, artifact=False)
    assert is_nuget_packages_directory("packages", packages) is False


def test_v3_global_cache_layout_is_a_known_false_negative(tmp_path):
    """`~/.nuget/packages` nests as `<id>/<version>/<id>.<version>.nupkg`, one
    level deeper than v2. Not matched, and erring toward indexing is the right
    direction for a layout that normally sits outside a repo anyway."""
    packages = tmp_path / "packages"
    pkg = packages / "bootstrapblazor" / "10.3.2"
    pkg.mkdir(parents=True)
    (pkg / "bootstrapblazor.10.3.2.nupkg").write_bytes(b"PK\x03\x04")
    assert is_nuget_packages_directory("packages", packages) is False


def test_probe_stats_no_more_children_than_the_limit(tmp_path, monkeypatch):
    """The probe costs one stat per child directory, so a `packages/` dir that is
    NOT a restore tree pays that cost for nothing. Asserts the BOUND rather than
    the verdict, because a verdict test would depend on `scandir` ordering, which
    is name-ordered on NTFS and hash-ordered elsewhere.
    """
    from jcodemunch_mcp import security

    packages = tmp_path / "packages"
    for i in range(security._NUGET_PROBE_LIMIT * 2):
        (packages / f"member{i:04d}").mkdir(parents=True)

    calls = []
    real_isfile = security.os.path.isfile
    monkeypatch.setattr(
        security.os.path,
        "isfile",
        lambda p: (calls.append(p), real_isfile(p))[1],
    )

    assert is_nuget_packages_directory("packages", packages) is False
    assert len(calls) <= security._NUGET_PROBE_LIMIT


def test_giving_up_errs_toward_indexing(tmp_path):
    """The safe direction: an unrecognised restore tree is indexed, which is
    today's behaviour, where a wrongly pruned source tree loses real code."""
    packages = tmp_path / "packages"
    (packages / "unrecognised").mkdir(parents=True)
    assert is_nuget_packages_directory("packages", packages) is False


# ---------------------------------------------------------------------------
# The config flag
# ---------------------------------------------------------------------------


def test_flag_defaults_to_on():
    assert get_skip_nuget_packages() is True


@pytest.mark.parametrize("value", [None, "false", 0, "", "garbage", []])
def test_only_an_explicit_false_disables_it(monkeypatch, value):
    """A typo must not silently re-admit a vendored dependency tree. Same rule as
    `get_respect_cachedir_tag` and `get_skip_msbuild_output`."""
    monkeypatch.setattr(
        _config, "get", lambda key, default=None, repo=None: value
    )
    assert get_skip_nuget_packages() is True


def test_explicit_false_disables_it(monkeypatch):
    monkeypatch.setattr(
        _config, "get", lambda key, default=None, repo=None: False
    )
    assert get_skip_nuget_packages() is False


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


def _solution_with_restore(root: Path) -> Path:
    """A .NET solution with real source plus a NuGet restore tree beside it."""
    (root / "App.sln").write_text("Microsoft Visual Studio Solution File", encoding="utf-8")
    (root / "Program.cs").write_text("class Program {}", encoding="utf-8")

    packages = root / "packages"
    _restored_package(packages, "Newtonsoft.Json.6.0.3")
    # What actually hurts: restored packages ship vendored source.
    content = packages / "jQuery.3.4.1" / "Content" / "Scripts"
    content.mkdir(parents=True)
    (content / "jquery.js").write_text("var jQuery = {};", encoding="utf-8")
    (packages / "jQuery.3.4.1" / "jQuery.3.4.1.nupkg").write_bytes(b"PK\x03\x04")
    return root


def test_restore_tree_is_pruned_and_counted(tmp_path):
    files, warnings, skip_counts = discover_local_files(_solution_with_restore(tmp_path))

    names = {Path(f).name for f in files}
    assert "Program.cs" in names
    assert "jquery.js" not in names, "vendored package source leaked into the corpus"

    assert "packages" not in {part for f in files for part in Path(f).parts}
    assert skip_counts["nuget_packages"] == 1
    assert warnings == []


def test_source_packages_dir_survives_the_walk(tmp_path):
    """The walk half of the flutter guard."""
    (tmp_path / "pubspec.yaml").write_text("name: app", encoding="utf-8")
    member = tmp_path / "packages" / "flutter_test" / "lib"
    member.mkdir(parents=True)
    (member / "main.dart").write_text("void main() {}", encoding="utf-8")

    files, _, skip_counts = discover_local_files(tmp_path)

    assert "main.dart" in {Path(f).name for f in files}
    assert skip_counts["nuget_packages"] == 0


def test_flag_off_re_admits_the_restore_tree(tmp_path, monkeypatch):
    """Patches the config read rather than the env var: the env fallback is
    resolved once at `load_config()` time, so setting it after the fact would
    assert nothing and pass for the wrong reason."""
    from jcodemunch_mcp import security

    original = security._config.get
    monkeypatch.setattr(
        security._config,
        "get",
        lambda k, d=None, repo=None: (
            False if k == "skip_nuget_packages" else original(k, d, repo=repo)
        ),
    )

    files, _, skip_counts = discover_local_files(_solution_with_restore(tmp_path))

    assert "jquery.js" in {Path(f).name for f in files}
    assert skip_counts["nuget_packages"] == 0


def test_nuget_packages_is_not_a_withheld_reason():
    """Restored packages are re-downloadable derived data by the writer's own
    layout, so this is the corpus being DEFINED -- absence over what remains stays
    citable. Contrast `too_large`, where the file is real, current and wanted."""
    from jcodemunch_mcp.tools.index_folder import WITHHELD_SKIP_REASONS

    assert "nuget_packages" not in WITHHELD_SKIP_REASONS


# ---------------------------------------------------------------------------
# The watcher fast path -- third discovery entry point
# ---------------------------------------------------------------------------
#
# ⚠ `_should_index_file`'s own docstring says any new filter MUST land here so
# both routes apply it. `skip_msbuild_output` shipped without doing that, so a
# watchfiles event for a file appearing under `obj/` re-admitted it by the back
# door. Both rules are pinned here now.


def _fast_cfg(root: Path):
    return _build_index_filters(
        root=root.resolve(), skip_dirs_regex=_build_skip_dirs_regex()
    )


def test_fast_path_refuses_a_file_inside_a_restore_tree(tmp_path):
    root = _solution_with_restore(tmp_path)
    target = root / "packages" / "jQuery.3.4.1" / "Content" / "Scripts" / "jquery.js"

    ok, reason, _rel, _warning = _should_index_file(target, _fast_cfg(root))

    assert ok is False
    assert reason == "nuget_packages"


def test_fast_path_refuses_a_file_inside_msbuild_output(tmp_path):
    """Regression: this route had no MSBuild rule at all."""
    (tmp_path / "App.csproj").write_text("<Project />", encoding="utf-8")
    published = tmp_path / "obj" / "Release" / "Package" / "PackageTmp"
    published.mkdir(parents=True)
    (published / "Program.cs").write_text("class Program {}", encoding="utf-8")

    ok, reason, _rel, _warning = _should_index_file(
        published / "Program.cs", _fast_cfg(tmp_path)
    )

    assert ok is False
    assert reason == "msbuild_output"


def test_fast_path_keeps_a_source_packages_member(tmp_path):
    (tmp_path / "pubspec.yaml").write_text("name: app", encoding="utf-8")
    member = tmp_path / "packages" / "flutter_test" / "lib"
    member.mkdir(parents=True)
    (member / "main.dart").write_text("void main() {}", encoding="utf-8")

    ok, _reason, _rel, _warning = _should_index_file(
        member / "main.dart", _fast_cfg(tmp_path)
    )

    assert ok is True


def test_fast_path_keeps_a_node_bin_entrypoint(tmp_path):
    """The flutter guard's MSBuild twin, on the route that previously had no rule."""
    (tmp_path / "package.json").write_text('{"name":"cli"}', encoding="utf-8")
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "cli.js").write_text("main();", encoding="utf-8")

    ok, _reason, _rel, _warning = _should_index_file(
        binary / "cli.js", _fast_cfg(tmp_path)
    )

    assert ok is True


@pytest.mark.parametrize(
    "field, target_rel",
    [
        ("skip_nuget_packages", "packages/jQuery.3.4.1/Content/Scripts/jquery.js"),
        ("skip_msbuild_output", "obj/Program.cs"),
    ],
)
def test_fast_path_honours_each_flag_independently(tmp_path, field, target_rel):
    root = _solution_with_restore(tmp_path)
    (root / "App.csproj").write_text("<Project />", encoding="utf-8")
    (root / "obj").mkdir()
    (root / "obj" / "Program.cs").write_text("class Program {}", encoding="utf-8")

    cfg = _build_index_filters(
        root=root.resolve(),
        skip_dirs_regex=_build_skip_dirs_regex(),
        **{field: False},
    )
    ok, _reason, _rel, _warning = _should_index_file(root / target_rel, cfg)

    assert ok is True


# ---------------------------------------------------------------------------
# Explicit paths -- second entry point, deliberately uncovered
# ---------------------------------------------------------------------------


def test_explicit_paths_can_still_name_a_file_inside_a_restore_tree(tmp_path):
    """`resolve_explicit_paths` already opts past gitignore, skip-dirs and
    CACHEDIR.TAG so a caller can name a generated file ON PURPOSE. These two rules
    follow it rather than becoming the one exclusion that route cannot escape."""
    from jcodemunch_mcp.tools.index_folder import resolve_explicit_paths

    root = _solution_with_restore(tmp_path)
    target = root / "packages" / "jQuery.3.4.1" / "Content" / "Scripts" / "jquery.js"

    files, _warnings, _skips, _requested = resolve_explicit_paths(
        root, [str(target.relative_to(root))], 2000
    )

    assert [Path(f).name for f in files] == ["jquery.js"]
