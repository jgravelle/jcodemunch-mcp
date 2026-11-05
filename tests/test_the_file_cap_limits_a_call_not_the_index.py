"""The file cap limits what one call parses, not what the index keeps.

`max_folder_files` exists because an agent's `index_folder` runs inside one tool
call and must finish before the client's timeout. The command-line paths
(`init`, `jcodemunch-mcp index`) have no such timeout, so they get their own,
higher cap (`cli_max_folder_files`).

That split is only safe if a capped call never shrinks an index built past its
cap. A full in-call re-index diffs the files it discovered against everything
stored, so before this a file dropped by the cap counted as deleted, and a
forced rebuild replaced the whole index with the capped walk. Both now keep
those files. A file that is gone from disk or newly excluded still leaves.

The fixture holds 8 files under `pkg/`, an in-call cap of 3 and a CLI cap of
10, so the in-call walk keeps `m0`-`m2` and drops `m3`-`m7`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import jcodemunch_mcp.config as config_module

N = 8


def _load(repo: str):
    from jcodemunch_mcp.storage import IndexStore

    owner, name = repo.split("/", 1)
    return IndexStore().load_index(owner, name)


def _files(repo: str) -> set:
    return set(_load(repo).source_files)


def _names(repo: str) -> set:
    return {s["name"] for s in _load(repo).symbols}


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / ".idx"))
    monkeypatch.setenv("JCODEMUNCH_USE_AI_SUMMARIES", "false")
    monkeypatch.delenv("JCODEMUNCH_MAX_FOLDER_FILES", raising=False)
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    for i in range(N):
        (root / "pkg" / f"m{i}.py").write_text(
            f"def fn{i}():\n    return {i}\n", encoding="utf-8"
        )
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_folder_files", 3)
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "cli_max_folder_files", 10)
    return root


@pytest.fixture
def cli_built(tree):
    """An index built by the CLI's `init` path: every file, past the in-call cap."""
    from jcodemunch_mcp.cli.init import run_index
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    cwd = Path.cwd()
    import os

    os.chdir(tree)
    try:
        msg = run_index()
    finally:
        os.chdir(cwd)
    repo = resolve_repo(str(tree))["repo"]
    assert len(_files(repo)) == N, f"precondition: init indexed all {N} files ({msg})"
    return tree, repo


def _in_call(root: Path, **kw) -> dict:
    from jcodemunch_mcp.tools.index_folder import index_folder

    return index_folder(str(root), use_ai_summaries=False, **kw)


# --- the CLI paths use the CLI cap ---------------------------------------


def test_init_indexes_past_the_in_call_cap(cli_built):
    _root, repo = cli_built
    assert len(_files(repo)) == N


def test_the_index_subcommand_indexes_past_the_in_call_cap(tree, capsys):
    """`main` reloads config from CODE_INDEX_PATH, so the caps go in that file;
    set only in memory they are reset to the defaults and the test cannot fail."""
    import os

    from jcodemunch_mcp import server
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    store = Path(os.environ["CODE_INDEX_PATH"])
    store.mkdir(parents=True, exist_ok=True)
    (store / "config.jsonc").write_text(
        json.dumps({"max_folder_files": 3, "cli_max_folder_files": 10}),
        encoding="utf-8",
    )
    try:
        server.main(["index", str(tree)])
    except SystemExit as exc:  # the subcommand may exit 0 after printing
        assert not exc.code, exc.code
    assert len(_files(resolve_repo(str(tree))["repo"])) == N


def test_a_cli_cap_below_the_in_call_cap_does_not_lower_it(tree, monkeypatch):
    """The CLI cap only ever raises: the CLI uses the larger of the two."""
    from jcodemunch_mcp.security import get_cli_max_folder_files

    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "cli_max_folder_files", 2)
    assert get_cli_max_folder_files() == 3


def test_a_cap_the_user_set_without_a_cli_cap_still_binds_the_cli(tree, monkeypatch):
    """A user who lowered max_folder_files before the CLI had its own cap keeps
    it: the CLI cap rises above it only when the user set the CLI cap too."""
    from jcodemunch_mcp.security import (
        DEFAULT_CLI_MAX_FOLDER_FILES,
        get_cli_max_folder_files,
    )

    monkeypatch.setitem(
        config_module._GLOBAL_CONFIG,
        "cli_max_folder_files",
        DEFAULT_CLI_MAX_FOLDER_FILES,
    )
    assert get_cli_max_folder_files() == 3


def test_a_project_cap_without_a_cli_cap_binds_init(tree, monkeypatch):
    """The project file is read before the CLI picks its cap, not after."""
    from jcodemunch_mcp.security import (
        DEFAULT_CLI_MAX_FOLDER_FILES,
        DEFAULT_MAX_FOLDER_FILES,
    )
    from jcodemunch_mcp.cli.init import run_index
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo
    import os

    monkeypatch.setitem(
        config_module._GLOBAL_CONFIG, "max_folder_files", DEFAULT_MAX_FOLDER_FILES
    )
    monkeypatch.setitem(
        config_module._GLOBAL_CONFIG,
        "cli_max_folder_files",
        DEFAULT_CLI_MAX_FOLDER_FILES,
    )
    (tree / ".jcodemunch.jsonc").write_text('{"max_folder_files": 2}', encoding="utf-8")
    cwd = Path.cwd()
    os.chdir(tree)
    try:
        run_index()
    finally:
        os.chdir(cwd)
    assert len(_files(resolve_repo(str(tree))["repo"])) == 2


def test_the_default_in_call_cap_gets_the_cli_default(tree, monkeypatch):
    from jcodemunch_mcp.security import (
        DEFAULT_CLI_MAX_FOLDER_FILES,
        DEFAULT_MAX_FOLDER_FILES,
        get_cli_max_folder_files,
    )

    monkeypatch.setitem(
        config_module._GLOBAL_CONFIG, "max_folder_files", DEFAULT_MAX_FOLDER_FILES
    )
    monkeypatch.setitem(
        config_module._GLOBAL_CONFIG,
        "cli_max_folder_files",
        DEFAULT_CLI_MAX_FOLDER_FILES,
    )
    assert get_cli_max_folder_files() == DEFAULT_CLI_MAX_FOLDER_FILES


def test_an_agent_call_on_a_new_repo_still_stops_at_the_in_call_cap(tree):
    """Control: the in-call cap still bounds the work one call does."""
    res = _in_call(tree)
    assert res["files_indexed"] == 3 and res["truncated"] is True
    assert len(_files(res["repo"])) == 3


# --- an in-call run never shrinks a CLI-built index -----------------------


def test_an_agent_full_reindex_keeps_every_file(cli_built):
    root, repo = cli_built
    (root / "pkg" / "m0.py").write_text(
        "def fn0():\n    return 0\n\ndef added0():\n    return 1\n", encoding="utf-8"
    )
    _in_call(root)
    assert len(_files(repo)) == N, (
        "an agent's full re-index dropped files the CLI indexed past the cap"
    )
    assert "added0" in _names(repo), "the change inside the cap was still indexed"


def test_a_file_deleted_on_disk_past_the_cap_still_leaves(cli_built):
    root, repo = cli_built
    (root / "pkg" / "m6.py").unlink()
    _in_call(root)
    assert "pkg/m6.py" not in _files(repo)
    assert len(_files(repo)) == N - 1


def test_a_file_newly_gitignored_past_the_cap_still_leaves(cli_built):
    root, repo = cli_built
    (root / ".gitignore").write_text("pkg/m7.py\n", encoding="utf-8")
    _in_call(root)
    assert "pkg/m7.py" not in _files(repo)
    assert len(_files(repo)) == N - 1


def test_a_forced_rebuild_keeps_every_file_and_says_why(cli_built):
    from jcodemunch_mcp.tools.invalidate_cache import _force_full_reindex

    root, repo = cli_built
    _force_full_reindex.add(repo)
    try:
        res = _in_call(root)
    finally:
        _force_full_reindex.discard(repo)
    assert len(_files(repo)) == N, (
        "a forced rebuild under the in-call cap replaced the CLI-built index"
    )
    assert any("jcodemunch-mcp index" in w for w in res.get("warnings", [])), res.get(
        "warnings"
    )


def test_a_parser_upgrade_under_the_cap_keeps_every_file_and_keeps_the_upgrade_due(
    cli_built, monkeypatch
):
    """The rebuild a release forces: the files stay, and the stored generation is
    not stamped as upgraded, because only the capped part was re-parsed."""
    from jcodemunch_mcp.tools import index_folder as mod

    from jcodemunch_mcp.storage import sqlite_store

    root, repo = cli_built
    before = getattr(_load(repo), "parser_generation", None)
    monkeypatch.setattr(mod, "_needs_parser_upgrade", lambda idx: True)
    # A save that stamps the generation now records a higher one, so the
    # assertion below can see a stamp.
    monkeypatch.setattr(sqlite_store, "_PARSER_GENERATION", int(before or 0) + 1)
    res = _in_call(root)
    assert len(_files(repo)) == N
    assert mod.PARSER_UPGRADE_WARNING not in res.get("warnings", []), (
        "it says it re-parsed every file"
    )
    assert any("jcodemunch-mcp index" in w for w in res.get("warnings", []))
    assert getattr(_load(repo), "parser_generation", None) == before


def test_a_forced_rebuild_of_an_index_within_the_cap_is_still_a_rebuild(tree):
    """Control: the fallback applies only when the index holds files the cap cut."""
    from jcodemunch_mcp.tools.invalidate_cache import _force_full_reindex

    repo = _in_call(tree)["repo"]
    _force_full_reindex.add(repo)
    try:
        res = _in_call(tree)
    finally:
        _force_full_reindex.discard(repo)
    assert not any("jcodemunch-mcp index" in w for w in res.get("warnings", []))
    assert len(_files(repo)) == 3


def test_a_capped_call_reports_what_the_index_holds(cli_built):
    """The index result and resolve_repo describe the stored index, not the capped walk."""
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    root, repo = cli_built
    res = _in_call(root)
    assert not res.get("truncated"), res
    assert "truncation_warning" not in resolve_repo(str(root))


def test_an_agent_reindex_after_the_cap_is_lowered_keeps_every_file(tree, monkeypatch):
    """The prune itself, reproducible without the CLI: an index built under a
    higher cap, then an agent's full re-index under the lower one."""
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_folder_files", 10)
    repo = _in_call(tree)["repo"]
    assert len(_files(repo)) == N, "precondition"
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_folder_files", 3)
    _in_call(tree)
    assert len(_files(repo)) == N, "a capped re-index deleted the files its cap skipped"


# --- the watcher and the hook keep them too --------------------------------


def test_a_watcher_save_with_discovery_keeps_every_file(cli_built):
    root, repo = cli_built
    (root / "pkg" / "m5.py").write_text(
        "def fn5():\n    return 5\n\ndef added5():\n    return 1\n", encoding="utf-8"
    )
    _in_call(root, incremental=True, changed_paths=None)
    assert len(_files(repo)) == N


def test_a_watcher_save_of_a_file_past_the_cap_reparses_it_and_keeps_the_rest(
    cli_built,
):
    from jcodemunch_mcp.reindex_state import WatcherChange

    root, repo = cli_built
    p = root / "pkg" / "m5.py"
    p.write_text(
        "def fn5():\n    return 5\n\ndef added5():\n    return 1\n", encoding="utf-8"
    )
    _in_call(
        root,
        incremental=True,
        changed_paths=[WatcherChange("modified", str(p), "__cache_miss__")],
    )
    assert len(_files(repo)) == N
    assert "added5" in _names(repo)


def test_a_hook_save_of_a_file_past_the_cap_reparses_it_and_keeps_the_rest(cli_built):
    from jcodemunch_mcp.tools.index_file import index_file

    root, repo = cli_built
    p = root / "pkg" / "m6.py"
    p.write_text(
        "def fn6():\n    return 6\n\ndef added6():\n    return 1\n", encoding="utf-8"
    )
    res = index_file(str(p), use_ai_summaries=False)
    assert res.get("success") is not False, json.dumps(res)[:300]
    assert len(_files(repo)) == N
    assert "added6" in _names(repo)


def test_a_watcher_save_while_a_parser_upgrade_is_due_keeps_every_file(
    cli_built, monkeypatch
):
    """The fast path steps aside for the upgrade and the full walk runs with the
    event still attached: it must keep the files the cap skipped too."""
    from jcodemunch_mcp.reindex_state import WatcherChange
    from jcodemunch_mcp.storage import sqlite_store
    from jcodemunch_mcp.tools import index_folder as mod

    root, repo = cli_built
    before = getattr(_load(repo), "parser_generation", None)
    monkeypatch.setattr(mod, "_needs_parser_upgrade", lambda idx: True)
    monkeypatch.setattr(sqlite_store, "_PARSER_GENERATION", int(before or 0) + 1)
    p = root / "pkg" / "m5.py"
    p.write_text("def fn5():" + chr(10) + "    return 55" + chr(10), encoding="utf-8")
    res = _in_call(
        root,
        incremental=True,
        changed_paths=[WatcherChange("modified", str(p), "__cache_miss__")],
    )
    assert len(_files(repo)) == N, (
        "a watcher save with an upgrade due dropped the files past the cap"
    )
    assert mod.PARSER_UPGRADE_WARNING not in res.get("warnings", [])
    assert getattr(_load(repo), "parser_generation", None) == before


def _git_tree(tree):
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.fail("git is required for the git-root cases")
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    return tree


def _cli_index(path: Path) -> str:
    from jcodemunch_mcp.security import get_cli_max_folder_files

    return _in_call(path, max_folder_files=get_cli_max_folder_files(repo=str(path)))[
        "repo"
    ]


def test_an_agent_subdir_walk_keeps_the_files_the_cap_skipped_under_it(tree):
    """A subdir walk of a git-root index replaces the files inside the subdir
    with what it parsed; the ones its cap cut stay."""
    root = _git_tree(tree)
    repo = _cli_index(root)
    assert len(_files(repo)) == N, "precondition"
    _in_call(root / "pkg")
    assert len(_files(repo)) == N, "a capped subdir walk dropped the files past the cap"


def test_the_root_marker_rebuild_keeps_the_files_the_cap_skipped(tree):
    """A CLI index built from a subdir, then an agent's walk from the root: the
    one-off rebuild that marks the index full-root keeps what its cap cut."""
    root = _git_tree(tree)
    repo = _cli_index(root / "pkg")
    assert len(_files(repo)) == N, "precondition"
    res = _in_call(root)
    assert res["repo"] == repo
    assert len(_files(repo)) == N, (
        "the root-marker rebuild dropped the files past the cap"
    )


def test_a_subdir_walk_still_drops_a_file_deleted_on_disk(tree):
    root = _git_tree(tree)
    repo = _cli_index(root)
    (root / "pkg" / "m6.py").unlink()
    _in_call(root / "pkg")
    assert "pkg/m6.py" not in _files(repo)
    assert len(_files(repo)) == N - 1


def test_an_explicit_full_reindex_under_the_cap_says_it_was_asked_for(cli_built):
    root, repo = cli_built
    res = _in_call(root, incremental=False)
    assert len(_files(repo)) == N
    assert any("A full re-index was asked for" in w for w in res.get("warnings", [])), (
        res.get("warnings")
    )
