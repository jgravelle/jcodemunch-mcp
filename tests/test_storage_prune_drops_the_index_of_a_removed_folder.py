"""`jcodemunch-mcp storage prune` drops the index of a folder that is gone (V2-BACKLOG row 25).

A store that only the `index` CLI writes to is opened by no server, so the
server-start sweep never runs on it. One index per worktree then stays after
the worktree is removed. The command lists them and deletes with `--yes`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from jcodemunch_mcp.cli import storage_prune as sp
from jcodemunch_mcp.cli import storage_report as sr
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.index_folder import index_folder


def _index(tmp_path: Path, name: str, store: Path) -> tuple[str, Path]:
    src = tmp_path / name
    src.mkdir()
    (src / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"], r
    return r["repo"], src


@pytest.fixture
def two(tmp_path: Path):
    """Two indexed folders; the second is then removed."""
    store = tmp_path / "store"
    kept, _ = _index(tmp_path, "kept", store)
    lane, lane_dir = _index(tmp_path, "lane", store)
    shutil.rmtree(lane_dir)
    return store, kept, lane, lane_dir


def _repos(store: Path) -> set[str]:
    s = IndexStore(base_path=str(store))
    try:
        return {r["repo"] for r in s.list_repos()}
    finally:
        s.close()


def test_the_listing_names_the_removed_folder_and_deletes_nothing(two):
    store, kept, lane, lane_dir = two
    rep = sp.prune(str(store))
    assert rep["dry_run"] is True and rep["deleted"] == 0
    assert [(r["repo"], r["state"], r["deleted"]) for r in rep["orphans"]] == [(lane, sp.GONE, False)]
    assert rep["orphans"][0]["source_root"] and rep["orphans"][0]["bytes"] > 0
    assert _repos(store) == {kept, lane}


def test_yes_deletes_the_index_of_the_removed_folder_and_keeps_the_other(two):
    store, kept, lane, _ = two
    before = sr.storage_report(str(store), top=-1)["total_bytes"]
    rep = sp.prune(str(store), delete=True)
    assert rep["deleted"] == 1 and rep["failed"] == 0
    assert rep["orphans"][0]["deleted"] is True
    assert _repos(store) == {kept}
    after = sr.storage_report(str(store), top=-1)["total_bytes"]
    assert before - after == rep["freed_bytes"] > 0


def test_nothing_to_prune_when_every_folder_is_there(tmp_path):
    store = tmp_path / "store"
    _index(tmp_path, "kept", store)
    rep = sp.prune(str(store), delete=True)
    assert rep["orphans"] == [] and rep["deleted"] == 0


def test_a_store_that_does_not_exist_is_not_created(tmp_path):
    store = tmp_path / "nowhere"
    rep = sp.prune(str(store), delete=True)
    assert rep["exists"] is False and not store.exists()


def test_a_folder_on_an_absent_drive_is_unreachable_and_kept(two, monkeypatch):
    store, kept, lane, _ = two
    monkeypatch.setattr(sp, "folder_state", lambda root: sp.UNREACHABLE)
    rep = sp.prune(str(store), delete=True)
    assert rep["deleted"] == 0 and rep["kept_unreachable"] == 2
    assert {r["state"] for r in rep["orphans"]} == {sp.UNREACHABLE}
    assert _repos(store) == {kept, lane}


def test_include_unreachable_drops_those_too(two, monkeypatch):
    store, kept, lane, lane_dir = two
    real = sp.folder_state
    monkeypatch.setattr(
        sp, "folder_state", lambda root: sp.UNREACHABLE if Path(root) == lane_dir else real(root)
    )
    rep = sp.prune(str(store), delete=True, include_unreachable=True)
    assert rep["deleted"] == 1 and rep["kept_unreachable"] == 0
    assert _repos(store) == {kept}


def test_a_folder_that_came_back_before_the_delete_is_kept(two, monkeypatch):
    store, kept, lane, _ = two
    answers = iter([sp.PRESENT, sp.GONE, sp.PRESENT])  # kept; lane listed; lane re-checked

    monkeypatch.setattr(sp, "folder_state", lambda root: next(answers))
    rep = sp.prune(str(store), delete=True)
    assert rep["deleted"] == 0 and rep["failed"] == 0
    assert rep["orphans"][0]["state"] == sp.PRESENT
    assert _repos(store) == {kept, lane}


def test_a_remote_index_and_a_starter_pack_are_never_candidates(two, monkeypatch):
    store, kept, lane, _ = two
    entries = [
        {"repo": "github/owner-repo", "source_root": ""},
        {"repo": "local/pack-1234", "source_root": "/tmp/jcm-pack-clones/nodejs"},
    ]
    monkeypatch.setattr(IndexStore, "list_repos", lambda self: entries)
    rep = sp.prune(str(store), delete=True)
    assert rep["orphans"] == [] and rep["deleted"] == 0


@pytest.mark.parametrize(
    "root, existing, want",
    [
        ("Z:\\work\\lane", {"Z:\\"}, sp.GONE),
        ("Z:\\work\\lane", set(), sp.UNREACHABLE),
        ("\\\\nas\\share\\lane", set(), sp.UNREACHABLE),
        ("/home/u/lane", {"/home/u", "/home", "/"}, sp.GONE),
        ("/Volumes/Disk/lane", {"/Volumes", "/"}, sp.UNREACHABLE),
        ("/Volumes/Disk/lane", {"/Volumes/Disk", "/Volumes", "/"}, sp.GONE),
        ("/media/u/disk/lane", {"/media/u", "/media", "/"}, sp.UNREACHABLE),
        ("/mnt/x/lane", {"/mnt", "/"}, sp.UNREACHABLE),
    ],
)
def test_a_missing_folder_is_gone_only_when_its_volume_is_there(monkeypatch, root, existing, want):
    monkeypatch.setattr(sp.os.path, "isdir", lambda p: False)
    monkeypatch.setattr(sp, "_exists", lambda p: p in existing)
    assert sp.folder_state(root) == want


def test_the_cli_lists_then_deletes_with_yes(two):
    store, kept, lane, _ = two

    def cli(*argv):
        return subprocess.run(
            [sys.executable, "-m", "jcodemunch_mcp.server", "storage", *argv],
            capture_output=True, text=True, timeout=120,
            env={**__import__("os").environ, "CODE_INDEX_PATH": str(store)},
        )

    listed = cli("prune", "--json")
    assert listed.returncode == 0, listed.stderr
    assert [r["repo"] for r in json.loads(listed.stdout)["orphans"]] == [lane]
    assert _repos(store) == {kept, lane}

    text = cli("prune")
    assert "Nothing was deleted" in text.stdout and "storage prune --yes" in text.stdout

    done = cli("prune", "--yes", "--json")
    assert done.returncode == 0 and json.loads(done.stdout)["deleted"] == 1
    assert _repos(store) == {kept}


def test_yes_without_prune_is_refused_and_the_report_still_runs(two):
    store, *_ = two
    assert sr.main(["--yes"], storage_path=str(store)) == 2
    assert sr.main([], storage_path=str(store)) == 0
