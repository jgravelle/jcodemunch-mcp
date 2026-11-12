"""A framework profile's ignore patterns hold on every route that adds files.

#1398 put the profile's defaults below the project's own patterns on the full
walk and in `index_file`. Two other routes put files into an index and never
read the profile at all: the watcher's fast path (`index_folder` with
`changed_paths=`) merged only the config and per-call patterns, and `refresh`
discovered its corpus with `discover_local_files` and no profile, then
re-parsed what it found through `paths=`. Either one put an express project's
`coverage/` report back into an index the full walk had kept it out of.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from jcodemunch_mcp.reindex_state import WatcherChange
from jcodemunch_mcp.tools.index_folder import index_folder


def _repo(root: Path) -> Path:
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps({"dependencies": {"express": "^4.0.0"}}), encoding="utf-8"
    )
    (root / ".jcodemunch.jsonc").write_text(
        '{ "extra_ignore_patterns": ["!tools/coverage/**"] }', encoding="utf-8"
    )
    (root / "src").mkdir()
    (root / "src" / "app.js").write_text(
        "function app() { return 1; }\n", encoding="utf-8"
    )
    (root / "coverage").mkdir()
    (root / "coverage" / "report.js").write_text(
        "function generated() { return 2; }\n", encoding="utf-8"
    )
    (root / "tools" / "coverage").mkdir(parents=True)
    (root / "tools" / "coverage" / "run.js").write_text(
        "function run() { return 3; }\n", encoding="utf-8"
    )
    return root


def _files(store: Path) -> set[str]:
    db = next(store.rglob("*.db"))
    conn = sqlite3.connect(db)
    try:
        return {p for (p,) in conn.execute("SELECT path FROM files")}
    finally:
        conn.close()


def _fresh_index(tmp_path: Path) -> tuple[Path, Path]:
    root = _repo(tmp_path / "repo")
    store = tmp_path / "store"
    r = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert r.get("success"), r
    files = _files(store)
    assert "coverage/report.js" not in files and "src/app.js" in files
    return root, store


def _watch(root: Path, store: Path, rel: str) -> dict:
    target = root / rel
    target.write_text("function edited() { return 9; }\n", encoding="utf-8")
    return index_folder(
        str(root),
        use_ai_summaries=False,
        storage_path=str(store),
        incremental=True,
        changed_paths=[WatcherChange("added", str(target.resolve()), "__cache_miss__")],
    )


def test_the_watcher_fast_path_honours_a_profile_default(tmp_path):
    root, store = _fresh_index(tmp_path)
    _watch(root, store, "coverage/more.js")
    assert "coverage/more.js" not in _files(store), (
        "express's coverage/ default must hold on the watcher's fast path"
    )


def test_the_watcher_fast_path_keeps_a_project_reinclude(tmp_path):
    root, store = _fresh_index(tmp_path)
    _watch(root, store, "tools/coverage/more.js")
    assert "tools/coverage/more.js" in _files(store)


def test_refresh_discovers_the_corpus_the_full_walk_indexes(tmp_path):
    from jcodemunch_mcp.tools.refresh import _discover

    root, store = _fresh_index(tmp_path)
    discovered = set(_discover(str(root)))
    assert "coverage/report.js" not in discovered, (
        "refresh must not discover (and then re-parse) a file the walk's profile excludes"
    )
    assert {"src/app.js", "tools/coverage/run.js"} <= discovered
    assert discovered == _files(store)


def test_a_paths_directory_entry_honours_a_profile_default(tmp_path):
    # The subtree walk is rooted at the listed directory; the profile's
    # patterns are root-relative and are matched against the index root.
    root = _repo(tmp_path / "repo")
    store = tmp_path / "store"
    r = index_folder(
        str(root), use_ai_summaries=False, storage_path=str(store), paths=["."]
    )
    assert r.get("success"), r
    files = _files(store)
    assert "coverage/report.js" not in files
    assert {"src/app.js", "tools/coverage/run.js"} <= files


def test_a_paths_file_entry_is_the_callers_choice(tmp_path):
    # A FILE named in paths= is not refused by the profile, as skip-dir rules
    # are not applied to one.
    root = _repo(tmp_path / "repo")
    store = tmp_path / "store"
    r = index_folder(
        str(root),
        use_ai_summaries=False,
        storage_path=str(store),
        paths=["coverage/report.js"],
    )
    assert r.get("success"), r
    assert "coverage/report.js" in _files(store)


def test_a_paths_directory_under_a_git_root_uses_the_full_walks_profile(tmp_path):
    # After the git-root retarget the profile is detected at the git root,
    # not at the indexed subdirectory; a paths= directory must use that one.
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(
        json.dumps({"dependencies": {"express": "^4.0.0"}}), encoding="utf-8"
    )
    web = repo / "web"
    (web / "coverage").mkdir(parents=True)
    (web / "coverage" / "x.js").write_text("function x() {}\n", encoding="utf-8")
    (web / "a.js").write_text("function a() {}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    walked = {}
    for kind, kw in (("full", {}), ("paths", {"paths": ["."]})):
        store = tmp_path / f"store_{kind}"
        r = index_folder(
            str(web), use_ai_summaries=False, storage_path=str(store), **kw
        )
        assert r.get("success"), r
        walked[kind] = _files(store)
    assert not any(f.endswith("coverage/x.js") for f in walked["full"])
    assert walked["paths"] == walked["full"]


def test_a_per_call_negation_still_beats_the_profile_on_a_paths_directory(tmp_path):
    root = _repo(tmp_path / "repo")
    (root / "coverage" / "keep.js").write_text("function keep() {}\n", encoding="utf-8")
    walked = {}
    for kind, kw in (("full", {}), ("paths", {"paths": ["."]})):
        store = tmp_path / f"store_{kind}"
        r = index_folder(
            str(root),
            use_ai_summaries=False,
            storage_path=str(store),
            extra_ignore_patterns=["!coverage/keep.js"],
            **kw,
        )
        assert r.get("success"), r
        walked[kind] = _files(store)
    assert "coverage/keep.js" in walked["full"]
    assert walked["paths"] == walked["full"]
