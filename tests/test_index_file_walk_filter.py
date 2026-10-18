"""L-142: `index_file` excludes exactly what the full walk excludes.

`index_file` applied one rule of the discovery filter (`is_secret_file`) and
none of the others, so a manual call or the PostToolUse `index-file` hook (which
filters on extension only before spawning) indexed a file under `node_modules/`,
`.next/`, a `CACHEDIR.TAG` directory, a gitignored path, MSBuild output, a NuGet
restore tree or a virtualenv's `site-packages`, answered `success: True`, and the
next full walk deleted it.

Every case is built BEFORE the folder index, so the test first proves the walk
excludes the file, then that `index_file` refuses it with the walk's own reason.
A control file at the same depth is accepted, so a refusal of everything fails.
"""

from pathlib import Path

import pytest

from jcodemunch_mcp.security import CACHEDIR_TAG_SIGNATURE
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.index_file import index_file
from jcodemunch_mcp.tools.index_folder import index_folder


def _write(p: Path, text: str = "def f():\n    return 1\n") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _build_tree(root: Path) -> dict[str, tuple[Path, str]]:
    """Each excluded file, keyed by case, with the walk's skip reason."""
    cases: dict[str, tuple[Path, str]] = {}

    cases["node_modules"] = (_write(root / "node_modules" / "pkg" / "index.js",
                                    "function f() { return 1 }\n"), "skip_dir")
    cases["next_build"] = (_write(root / ".next" / "server" / "page.js",
                                  "function f() { return 1 }\n"), "skip_dir")

    tagged = root / "scratch_cache"
    tagged.mkdir()
    (tagged / "CACHEDIR.TAG").write_bytes(CACHEDIR_TAG_SIGNATURE + b"\n")
    cases["cachedir_tag"] = (_write(tagged / "gen.py"), "cache_dir")

    # Not a skip-list name (`generated` is one), so only the .gitignore excludes it.
    _write(root / ".gitignore", "emitted/\n")
    cases["gitignore_root"] = (_write(root / "emitted" / "out.py"), "gitignore")
    # A .gitignore below the root: the walk reads every one it descends past.
    _write(root / "pkg" / ".gitignore", "local_only.py\n")
    cases["gitignore_nested"] = (_write(root / "pkg" / "local_only.py"), "gitignore")

    _write(root / "dotnet" / "App.csproj", "<Project />\n")
    cases["msbuild_obj"] = (_write(root / "dotnet" / "obj" / "Gen.cs",
                                   "class Gen { }\n"), "msbuild_output")

    nupkg_dir = root / "packages" / "Foo.1.0.0"
    nupkg_dir.mkdir(parents=True)
    (nupkg_dir / "Foo.1.0.0.nupkg").write_bytes(b"PK")
    cases["nuget_packages"] = (_write(nupkg_dir / "content" / "x.cs",
                                      "class X { }\n"), "nuget_packages")

    _write(root / "env" / "pyvenv.cfg", "home = /usr/bin\n")
    cases["virtualenv"] = (_write(root / "env" / "Lib" / "site-packages" / "m.py"),
                           "virtualenv")

    _write(root / ".jcodemunch.jsonc",
           '{"extra_ignore_patterns": ["vendored/"], "max_file_size": 4096}\n')
    cases["extra_ignore"] = (_write(root / "vendored" / "v.py"), "extra_ignore")
    cases["too_large"] = (_write(root / "big.py", "x = 1\n" * 2000), "too_large")

    return cases


@pytest.fixture
def indexed_tree(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    store = tmp_path / "store"
    control = _write(root / "pkg" / "kept.py")
    cases = _build_tree(root)
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True, res
    s = IndexStore(base_path=str(store))
    owner, name = s.list_repos()[0]["repo"].split("/", 1)
    walked = set(s.load_index(owner, name).source_files)
    return root, store, control, cases, walked, (owner, name)


def _rel(root: Path, p: Path) -> str:
    return p.resolve().relative_to(root.resolve()).as_posix()


def test_the_walk_excludes_every_case(indexed_tree):
    # Precondition, so a case the walk ACCEPTS cannot make index_file look wrong.
    root, _store, control, cases, walked, _ = indexed_tree
    assert _rel(root, control) in walked
    for case, (path, _reason) in cases.items():
        assert _rel(root, path) not in walked, case


@pytest.mark.parametrize("case", [
    "node_modules", "next_build", "cachedir_tag", "gitignore_root",
    "gitignore_nested", "msbuild_obj", "nuget_packages", "virtualenv",
    "extra_ignore", "too_large",
])
def test_index_file_refuses_what_the_walk_excludes(indexed_tree, case):
    root, store, _control, cases, _walked, (owner, name) = indexed_tree
    path, reason = cases[case]
    res = index_file(path=str(path), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is False, res
    assert res.get("skipped") == reason, res
    assert res.get("error"), res
    after = IndexStore(base_path=str(store)).load_index(owner, name)
    assert _rel(root, path) not in set(after.source_files)


def test_control_file_is_still_indexed(indexed_tree):
    # Non-vacuity: a new ordinary file beside the excluded ones is accepted.
    root, store, _control, _cases, _walked, (owner, name) = indexed_tree
    fresh = _write(root / "pkg" / "fresh.py")
    res = index_file(path=str(fresh), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True, res
    assert res["is_new"] is True
    after = IndexStore(base_path=str(store)).load_index(owner, name)
    assert "pkg/fresh.py" in set(after.source_files)


# ── Review round 1: inputs the walk assembles outside `_should_index_file` ──

def _index_and_files(folder: Path, store: Path) -> tuple[tuple[str, str], set[str]]:
    res = index_folder(str(folder), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True, res
    s = IndexStore(base_path=str(store))
    owner, name = s.list_repos()[0]["repo"].split("/", 1)
    return (owner, name), set(s.load_index(owner, name).source_files)


def test_framework_profile_ignore_pattern_is_applied(tmp_path):
    # The walk merges the detected profile's patterns (Spring Boot: `src/test/`)
    # into its ignore patterns at its call site, outside the predicate.
    root = tmp_path / "svc"
    _write(root / "build.gradle", "plugins { id 'org.springframework.boot' }\n")
    _write(root / "src" / "main" / "java" / "App.java", "class App { }\n")
    store = tmp_path / "store"
    (owner, name), walked = _index_and_files(root, store)
    assert "src/main/java/App.java" in walked
    test_file = _write(root / "src" / "test" / "java" / "AppTest.java", "class AppTest { }\n")
    res = index_file(path=str(test_file), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is False, res
    assert res.get("skipped") == "extra_ignore", res


def _git_init(root: Path) -> None:
    import subprocess
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)


def test_subdir_walk_reads_gitignores_from_its_own_root(tmp_path):
    # `index_folder(<repo>/sub)` walks `sub` only, so the git root's .gitignore
    # does not reach it; a file that walk indexed must stay indexable.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    _write(repo / ".gitignore", "gen/\n")
    target = _write(repo / "sub" / "gen" / "x.py")
    store = tmp_path / "store"
    (owner, name), walked = _index_and_files(repo / "sub", store)
    assert "sub/gen/x.py" in walked, walked
    _write(target, "def g():\n    return 2\n")
    res = index_file(path=str(target), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True, res


def test_subdir_walk_reads_its_own_project_config(tmp_path):
    # The walk reads `.jcodemunch.jsonc` from the folder it was given.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    _write(repo / "sub" / ".jcodemunch.jsonc", '{"extra_ignore_patterns": ["drop/"]}\n')
    _write(repo / "sub" / "keep.py")
    target = _write(repo / "sub" / "drop" / "y.py")
    store = tmp_path / "store"
    (owner, name), walked = _index_and_files(repo / "sub", store)
    assert "sub/keep.py" in walked and "sub/drop/y.py" not in walked, walked
    res = index_file(path=str(target), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is False, res
    assert res.get("skipped") == "extra_ignore", res


# ── A refusal names the setting that re-admits the file, or says none does ──

@pytest.mark.parametrize("case,setting", [
    ("cachedir_tag", "respect_cachedir_tag"),
    ("virtualenv", "skip_virtualenvs"),
    ("too_large", "max_file_size"),
    ("node_modules", "exclude_skip_directories"),
])
def test_refusal_names_the_override(indexed_tree, case, setting):
    _root, store, _control, cases, _walked, _ = indexed_tree
    path, _reason = cases[case]
    res = index_file(path=str(path), use_ai_summaries=False, storage_path=str(store))
    assert setting in res["error"], res["error"]
    assert "No setting re-admits" not in res["error"]


def test_refusal_says_when_no_setting_readmits(indexed_tree):
    _root, store, _control, cases, _walked, _ = indexed_tree
    path, _reason = cases["gitignore_root"]
    res = index_file(path=str(path), use_ai_summaries=False, storage_path=str(store))
    assert "No setting re-admits a file excluded this way." in res["error"], res["error"]


def test_the_named_override_readmits_on_both_routes(tmp_path):
    # The override the refusal names must work for the walk AND index_file.
    root = tmp_path / "proj"
    _write(root / "keep.py")
    _write(root / ".jcodemunch.jsonc", '{"respect_cachedir_tag": false}\n')
    tagged = root / "scratch_cache"
    tagged.mkdir()
    (tagged / "CACHEDIR.TAG").write_bytes(CACHEDIR_TAG_SIGNATURE + b"\n")
    target = _write(tagged / "gen.py")
    store = tmp_path / "store"
    (owner, name), walked = _index_and_files(root, store)
    assert "scratch_cache/gen.py" in walked, walked
    _write(target, "def g():\n    return 2\n")
    res = index_file(path=str(target), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True, res
