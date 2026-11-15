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


@pytest.mark.parametrize("spelling", ["watcher_change", "legacy_tuple"])
def test_a_watcher_save_while_a_parser_upgrade_is_due_keeps_every_file(
    cli_built, monkeypatch, spelling
):
    """The fast path steps aside for the upgrade and the full walk runs with the
    event still attached: it keeps the files the cap skipped and still re-reads
    the one the event names, although that one is past the cap."""
    from jcodemunch_mcp.reindex_state import WatcherChange
    from jcodemunch_mcp.tools import index_folder as mod

    root, repo = cli_built
    before = getattr(_load(repo), "parser_generation", None)
    _raise_parser_generation(monkeypatch, before)
    p = root / "pkg" / "m5.py"
    p.write_text(
        "def fn5():"
        + chr(10)
        + "    return 55"
        + chr(10)
        + chr(10)
        + "def added5():"
        + chr(10)
        + "    return 1"
        + chr(10),
        encoding="utf-8",
    )
    change = (
        WatcherChange("modified", str(p), "__cache_miss__")
        if spelling == "watcher_change"
        else ("modified", str(p))
    )
    res = _in_call(root, incremental=True, changed_paths=[change])
    assert len(_files(repo)) == N, (
        "a watcher save with an upgrade due dropped the files past the cap"
    )
    assert "added5" in _names(repo), "the edit to a file past the cap was dropped"
    assert mod.PARSER_UPGRADE_WARNING not in res.get("warnings", [])
    assert getattr(_load(repo), "parser_generation", None) == before


def _raise_parser_generation(monkeypatch, before) -> None:
    """Make a parser upgrade due through the real gate: every reader of the
    constant sees one more than the stored generation, so a save that stamps
    the generation records a value the tests can tell apart."""
    from jcodemunch_mcp.storage import index_store, sqlite_store
    from jcodemunch_mcp.tools import index_folder as mod

    bumped = int(before or 0) + 1
    monkeypatch.setattr(index_store, "PARSER_GENERATION", bumped)
    monkeypatch.setattr(sqlite_store, "_PARSER_GENERATION", bumped)
    monkeypatch.setattr(mod, "PARSER_GENERATION", bumped)


def _coverage(repo: str) -> dict:
    from jcodemunch_mcp.storage import IndexStore

    owner, name = repo.split("/", 1)
    return IndexStore()._sqlite.get_coverage(owner, name)


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
    assert any("jcodemunch-mcp index" in w for w in res.get("warnings", []))


def test_a_carrying_rebuild_with_an_upgrade_due_keeps_the_older_generation(
    tree, monkeypatch
):
    """The full save that keeps files past the cap records the generation those
    files were parsed by, so the upgrade stays due."""
    root = _git_tree(tree)
    repo = _cli_index(root / "pkg")
    before = getattr(_load(repo), "parser_generation", None)
    _raise_parser_generation(monkeypatch, before)
    _in_call(root)
    assert len(_files(repo)) == N
    assert getattr(_load(repo), "parser_generation", None) == before


def test_coverage_counts_the_kept_files_as_present(tree):
    """The coverage record and the cap status describe the same index: one that
    holds every file, so neither says the cap withheld any."""
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    root = _git_tree(tree)
    repo = _cli_index(root / "pkg")
    _in_call(root)  # the carrying full save
    cov = _coverage(repo)
    assert cov.get("files_indexed") == N, cov
    assert "file_limit" not in (cov.get("withheld") or {}), cov
    assert cov.get("complete") is True, cov
    assert "truncation_warning" not in resolve_repo(str(root))
    _edit(root / "pkg" / "m0.py", "added0")
    res = _in_call(root)  # the incremental path over the same index
    assert res.get("incremental") is True or res.get("performed_incremental") is True, (
        res
    )
    cov = _coverage(repo)
    assert cov.get("files_indexed") == N, cov
    assert cov.get("complete") is True, cov


def _edit(path: Path, name: str) -> None:
    path.write_text(
        path.read_text(encoding="utf-8")
        + chr(10)
        + "def "
        + name
        + "():"
        + chr(10)
        + "    return 1"
        + chr(10),
        encoding="utf-8",
    )


def test_an_agent_reindex_with_an_edit_counts_the_kept_files_as_present(cli_built):
    """The agent's ordinary re-index: one edit inside the cap, the files past it
    kept. The coverage record says the index is complete, so absence answers
    are not refused for it."""
    root, repo = cli_built
    _edit(root / "pkg" / "m0.py", "added0")
    _in_call(root)
    assert "added0" in _names(repo)
    cov = _coverage(repo)
    assert cov.get("files_indexed") == N, cov
    assert "file_limit" not in (cov.get("withheld") or {}), cov
    assert cov.get("complete") is True, cov


def test_watcher_events_naming_every_file_past_the_cap_clear_the_cap_status(
    cli_built, monkeypatch
):
    """When the events name every file the cap cut, all are parsed now: the
    stored cap status says nothing is missing, matching the index."""
    from jcodemunch_mcp.reindex_state import WatcherChange
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    root, repo = cli_built
    _raise_parser_generation(
        monkeypatch, getattr(_load(repo), "parser_generation", None)
    )
    changes = []
    for i in range(3, N):
        f = root / "pkg" / f"m{i}.py"
        _edit(f, f"added{i}")
        changes.append(WatcherChange("modified", str(f), "__cache_miss__"))
    res = _in_call(root, incremental=True, changed_paths=changes)
    assert len(_files(repo)) == N
    assert {f"added{i}" for i in range(3, N)} <= _names(repo)
    assert not res.get("truncated"), res
    assert "truncation_warning" not in resolve_repo(str(root))


def test_a_first_subdir_walk_with_incremental_false_says_what_it_kept(tree):
    root = _git_tree(tree)
    repo = _cli_index(root)
    res = _in_call(root / "pkg", incremental=False)
    assert len(_files(repo)) == N
    assert any("jcodemunch-mcp index" in w for w in res.get("warnings", [])), res.get(
        "warnings"
    )


def test_a_subdir_walk_still_drops_a_file_deleted_on_disk(tree):
    root = _git_tree(tree)
    repo = _cli_index(root)
    (root / "pkg" / "m6.py").unlink()
    _in_call(root / "pkg")
    assert "pkg/m6.py" not in _files(repo)
    assert len(_files(repo)) == N - 1


def test_an_explicit_full_reindex_under_the_cap_names_no_wrong_cause(cli_built):
    root, repo = cli_built
    res = _in_call(root, incremental=False)
    assert len(_files(repo)) == N
    kept = [w for w in res.get("warnings", []) if "jcodemunch-mcp index" in w]
    assert kept, res.get("warnings")
    assert not any("cache invalidated" in w for w in kept), kept


def _write_module(path: Path, i: int) -> None:
    path.write_text(
        "def fn" + str(i) + "():" + chr(10) + "    return " + str(i) + chr(10),
        encoding="utf-8",
    )


def test_new_files_past_the_cap_with_no_edit_are_reported_missing(cli_built):
    """No file the index holds changed, but two new ones sit past the cap: the
    stored cap status and coverage say the index is partial, as it is."""
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    root, repo = cli_built
    for i in (N, N + 1):
        _write_module(root / "pkg" / f"m{i}.py", i)
    _in_call(root)
    assert len(_files(repo)) == N
    cap = _load(repo).file_cap_status
    assert cap.get("truncated") is True and cap.get("files_skipped_cap") == 2, cap
    assert cap.get("files_discovered") == N + 2 and cap.get("files_indexed") == N, cap
    assert _coverage(repo).get("complete") is False, _coverage(repo)
    assert "truncation_warning" in resolve_repo(str(root))


def test_a_promoted_watcher_file_moves_from_skipped_to_indexed(tree, monkeypatch):
    """An agent-built capped index, an upgrade due and one event past the cap:
    the stored numbers describe the index, 4 of 8, not 3 of 7."""
    from jcodemunch_mcp.reindex_state import WatcherChange

    repo = _in_call(tree)["repo"]
    assert len(_files(repo)) == 3, "precondition"
    _raise_parser_generation(
        monkeypatch, getattr(_load(repo), "parser_generation", None)
    )
    p = tree / "pkg" / "m6.py"
    _edit(p, "added6")
    _in_call(
        tree,
        incremental=True,
        changed_paths=[WatcherChange("modified", str(p), "__cache_miss__")],
    )
    assert len(_files(repo)) == 4
    cap = _load(repo).file_cap_status
    assert cap.get("files_discovered") == N, cap
    assert cap.get("files_indexed") == 4, cap
    assert cap.get("files_skipped_cap") == N - 4, cap


# --- a kept file counts as present only while it is unchanged -------------


def _absence_is_refused(repo: str, needle: str) -> bool:
    from jcodemunch_mcp.tools.search_text import search_text

    res = search_text(repo=repo, query=needle)
    assert not res.get("result_count"), "precondition: the needle is not indexed"
    return bool(((res.get("_meta") or {}).get("verdict") or {}).get("absence_refused"))


def test_an_edit_past_the_cap_with_nothing_else_changed_refuses_absence(cli_built):
    """The agent re-index sees no change inside its cap (the no-change exit) and
    keeps m6 as last parsed. m6 now holds a name the index lacks, so a search
    must not call that name absent."""
    root, repo = cli_built
    _edit(root / "pkg" / "m6.py", "added_past_cap")
    res = _in_call(root)
    assert any("1 of them changed" in w for w in res.get("warnings") or []), res
    assert _coverage(repo).get("complete") is False, _coverage(repo)
    assert _load(repo).file_cap_status.get("truncated") is True
    assert _absence_is_refused(repo, "added_past_cap")


def test_an_edit_past_the_cap_beside_an_edit_inside_it_refuses_absence(cli_built):
    """The incremental save route: one edit inside the cap, one past it."""
    root, repo = cli_built
    _edit(root / "pkg" / "m0.py", "added0")
    _edit(root / "pkg" / "m6.py", "added_past_cap")
    res = _in_call(root)
    assert "added0" in _names(repo)
    assert any("1 of them changed" in w for w in res.get("warnings") or []), res
    cov = _coverage(repo)
    assert cov.get("complete") is False and cov.get("files_indexed") == N - 1, cov
    assert _absence_is_refused(repo, "added_past_cap")


def test_an_edit_past_the_cap_under_a_forced_rebuild_refuses_absence(cli_built):
    """The full-save carrying route."""
    from jcodemunch_mcp.tools.invalidate_cache import _force_full_reindex

    root, repo = cli_built
    _edit(root / "pkg" / "m6.py", "added_past_cap")
    _force_full_reindex.add(repo)
    try:
        res = _in_call(root, incremental=False)
    finally:
        _force_full_reindex.discard(repo)
    assert len(_files(repo)) == N
    assert any("1 of them changed" in w for w in res.get("warnings") or []), res
    assert _coverage(repo).get("complete") is False, _coverage(repo)
    assert _absence_is_refused(repo, "added_past_cap")


def test_unchanged_kept_files_still_count_as_present_and_the_carry_warns(cli_built):
    """The control: nothing past the cap changed, so coverage stays complete,
    and the carry is still named in the result."""
    root, repo = cli_built
    _edit(root / "pkg" / "m0.py", "added0")
    res = _in_call(root)
    assert any("kept the files past the cap" in w for w in res.get("warnings") or []), (
        res
    )
    assert not any("changed on disk" in w for w in res.get("warnings") or []), res
    assert _coverage(repo).get("complete") is True, _coverage(repo)
    res = _in_call(root)  # the no-change exit carries too
    assert any("kept the files past the cap" in w for w in res.get("warnings") or []), (
        res
    )


# --- the CLI cap is named where it binds ---------------------------------


def test_the_index_subcommand_cap_warning_names_the_cli_cap(tree, capsys):
    """Raising max_folder_files would also raise the agent's in-call cap, so the
    warning after `index` names the key that bound it."""
    import os

    from jcodemunch_mcp import server

    store = Path(os.environ["CODE_INDEX_PATH"])
    store.mkdir(parents=True, exist_ok=True)
    (store / "config.jsonc").write_text(
        json.dumps({"max_folder_files": 3, "cli_max_folder_files": 5}),
        encoding="utf-8",
    )
    try:
        server.main(["index", str(tree)])
    except SystemExit as exc:
        assert not exc.code, exc.code
    err = capsys.readouterr().err
    assert "cli_max_folder_files" in err, err
    assert "JCODEMUNCH_MAX_FOLDER_FILES" not in err, err


def test_the_cli_cap_is_a_corpus_eligibility_key():
    """It decides which files enter a CLI-built index, so two installs that
    differ only there must not share a capability certificate."""
    from jcodemunch_mcp.evidence.capability import ELIGIBILITY_KEYS

    assert "cli_max_folder_files" in ELIGIBILITY_KEYS


@pytest.mark.parametrize(
    ("in_call", "cli", "expect"),
    [
        (3, 10, (10, "cli_max_folder_files")),
        (12, 10, (12, "max_folder_files")),
        (3, 30000, (3, "max_folder_files")),
    ],
)
def test_the_cli_cap_names_the_key_that_set_it(tree, monkeypatch, in_call, cli, expect):
    """The warning raises the key that bound: the CLI key, an in-call cap above
    it, or a user's cap with the CLI key left at its default."""
    from jcodemunch_mcp.security import get_cli_folder_cap, get_cli_max_folder_files

    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_folder_files", in_call)
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "cli_max_folder_files", cli)
    assert get_cli_folder_cap(str(tree)) == expect
    assert get_cli_max_folder_files(repo=str(tree)) == expect[0]


# --- every reader of the cap status names the cap that applies ------------


def _answer_line(repo: str):
    from jcodemunch_mcp.retrieval.verdict import (
        index_truncation_meta,
        index_truncation_warning,
    )

    return index_truncation_warning(index_truncation_meta(_load(repo).file_cap_status))


def test_a_changed_kept_file_names_the_command_that_re_reads_it(cli_built):
    """The CLI cap covers the repo, so the step is to run it, in the result, in
    resolve_repo and in a search's answer line alike."""
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    root, repo = cli_built
    _edit(root / "pkg" / "m6.py", "added_past_cap")
    res = _in_call(root)
    step = "Run `jcodemunch-mcp index` to re-parse every file."
    assert any(step in w for w in res.get("warnings") or []), res
    assert step in resolve_repo(str(root))["truncation_warning"]
    assert step in _answer_line(repo)


def test_a_cli_cap_below_the_repo_says_to_raise_it_before_running_it(
    cli_built, monkeypatch
):
    """The user lowers max_folder_files and leaves the CLI key at its default, so
    max_folder_files binds the command line too. Running `index` alone would
    keep m6 stale and the advice would loop: it names the key and the count."""
    from jcodemunch_mcp.security import get_cli_folder_cap
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    root, repo = cli_built
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_folder_files", 3)
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "cli_max_folder_files", 30000)
    cap, key = get_cli_folder_cap(str(root))
    assert (cap, key) == (3, "max_folder_files"), "precondition"
    _edit(root / "pkg" / "m6.py", "added_past_cap")
    res = _in_call(root, max_folder_files=cap, cap_key=key)  # what `init` does
    step = f"to at least {N}, then run `jcodemunch-mcp index`."
    advice = (
        "Raise max_folder_files in config.jsonc (or set JCODEMUNCH_MAX_FOLDER_FILES) "
    )
    assert any(advice + step in w for w in res.get("warnings") or []), res
    assert not any("to re-parse every file" in w for w in res.get("warnings") or [])
    assert advice + step in resolve_repo(str(root))["truncation_warning"]
    assert step in _answer_line(repo)


def test_the_index_subcommand_result_names_the_cli_cap(tree, capsys):
    """The result JSON and resolve_repo say what stderr says: the CLI cap set
    the limit, so its key is the one to raise."""
    import os

    from jcodemunch_mcp import server
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    store = Path(os.environ["CODE_INDEX_PATH"])
    store.mkdir(parents=True, exist_ok=True)
    (store / "config.jsonc").write_text(
        json.dumps({"max_folder_files": 3, "cli_max_folder_files": 5}),
        encoding="utf-8",
    )
    try:
        server.main(["index", str(tree)])
    except SystemExit as exc:
        assert not exc.code, exc.code
    out = capsys.readouterr().out
    res = json.loads(out[out.index("{") :])
    cap_lines = [w for w in res.get("warnings") or [] if "File cap reached" in w]
    assert cap_lines and "(cli_max_folder_files=5)" in cap_lines[0], res
    assert "Raise cli_max_folder_files in config.jsonc" in cap_lines[0]
    assert "JCODEMUNCH_MAX_FOLDER_FILES" not in cap_lines[0]
    warn = resolve_repo(str(tree))["truncation_warning"]
    assert "(cli_max_folder_files=5)" in warn and "Raise cli_max_folder_files" in warn


def test_an_agent_capped_index_keeps_its_advice_word_for_word(tree):
    """The plain case: no CLI index, no kept files. Its text does not move."""
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    repo = _in_call(tree)["repo"]
    assert "cap_key" not in _load(repo).file_cap_status
    assert resolve_repo(str(tree))["truncation_warning"].endswith(
        "Raise max_folder_files in config.jsonc (or set "
        "JCODEMUNCH_MAX_FOLDER_FILES) and re-index."
    )
    assert _answer_line(repo).endswith(
        "Raise max_folder_files in config.jsonc, or set "
        "JCODEMUNCH_MAX_FOLDER_FILES, and re-index to search the rest."
    )


# --- an agent's explicit paths= list over a CLI-built index ------------------


@pytest.mark.parametrize("listed", [["."], ["pkg"], ["pkg/"]])
def test_an_agent_paths_call_keeps_the_files_its_cap_cut(cli_built, listed):
    """`paths=` is an MCP argument. Its resolver cut the list at the in-call cap
    and threw the rest away, so the rescope read every file past the cap as
    deleted: 8 stored, 3 left."""
    root, repo = cli_built
    res = _in_call(root, paths=listed)
    assert len(_files(repo)) == N, (
        f"paths={listed} shrank a CLI-built index to {len(_files(repo))} files"
    )
    assert not res.get("deleted"), res.get("deleted")


def test_an_agent_paths_call_still_drops_a_listed_file_deleted_on_disk(cli_built):
    root, repo = cli_built
    (root / "pkg" / "m6.py").unlink()
    _in_call(root, paths=["pkg"])
    assert "pkg/m6.py" not in _files(repo)
    assert len(_files(repo)) == N - 1


def test_the_paths_resolver_reports_what_its_cap_cut(tree):
    from jcodemunch_mcp.tools.index_folder import resolve_explicit_paths

    dropped: list = []
    files, _w, skips, _r = resolve_explicit_paths(
        tree, ["pkg", "pkg/m7.py"], max_files=3, cap_dropped=dropped
    )
    assert len(files) == 3
    assert len(dropped) == N - 3
    assert skips.get("file_limit") == N - 3
    assert not {f.resolve() for f in files} & {f.resolve() for f in dropped}


# --- every cap message names the key that set it ------------------------------


def test_init_names_the_key_that_capped_it(tree, monkeypatch):
    from jcodemunch_mcp.cli.init import run_index
    import os

    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "cli_max_folder_files", 5)
    cwd = Path.cwd()
    os.chdir(tree)
    try:
        msg = run_index()
    finally:
        os.chdir(cwd)
    assert "cli_max_folder_files = 5" in msg, msg
    assert "raise cli_max_folder_files in config.jsonc" in msg, msg


def test_the_kept_files_warning_names_the_in_call_key(cli_built):
    root, _repo = cli_built
    warnings = " ".join(_in_call(root).get("warnings") or [])
    assert "file cap (max_folder_files = 3)" in warnings, warnings
