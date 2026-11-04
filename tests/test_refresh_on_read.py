"""A read re-indexes the files that changed since the index was built.

After a rebase, a branch switch or an edit made outside the agent, the index
still held the old code, and a search served it. Users wrote rules telling
their agents to re-index by hand. With ``refresh_on_read`` on, a read tool
first asks git which indexed files changed. If there are few, it re-indexes
exactly those, inside a time budget. Otherwise it answers from the index as
it is and says why, naming the call that fixes it. A check runs at most once
per repository per cooldown.
"""

from __future__ import annotations

import json
import subprocess
import time

import pytest

from jcodemunch_mcp import refresh_on_read as ror
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture(autouse=True)
def _fresh_cooldowns():
    ror.reset_cooldowns()
    yield
    ror.reset_cooldowns()


@pytest.fixture
def indexed(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "app.py").write_text("def handle_order(x):\n    return x + 1\n")
    (src / "util.py").write_text("def handle_refund(x):\n    return x - 1\n")
    _git(src, "init", "-q")
    _git(src, "add", ".")
    _git(src, "commit", "-qm", "one")
    store = tmp_path / "store"
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True
    time.sleep(1.1)  # later mtimes than the index recorded
    return src, res["repo"], str(store)


def _names(repo, store, query):
    return {
        r["name"] for r in search_symbols(repo, query, storage_path=store)["results"]
    }


def test_a_renaming_commit_is_refreshed_before_the_answer(indexed):
    src, repo, store = indexed
    (src / "app.py").write_text("def process_order(x):\n    return x + 2\n")
    _git(src, "add", ".")
    _git(src, "commit", "-qm", "rename")
    block = ror.refresh_before_read(repo, storage_path=store)
    assert block == {"refreshed": 1}
    assert "process_order" in _names(repo, store, "process_order")
    assert "handle_order" not in _names(repo, store, "handle_order")


def test_an_uncommitted_edit_and_a_new_file_are_refreshed(indexed):
    src, repo, store = indexed
    (src / "util.py").write_text("def issue_refund(x):\n    return x - 2\n")
    (src / "new.py").write_text("def brand_new():\n    return 0\n")
    block = ror.refresh_before_read(repo, storage_path=store)
    assert block == {"refreshed": 2}
    assert "issue_refund" in _names(repo, store, "issue_refund")
    assert "brand_new" in _names(repo, store, "brand_new")


def test_a_deleted_file_leaves_the_index(indexed):
    src, repo, store = indexed
    (src / "util.py").unlink()
    assert ror.refresh_before_read(repo, storage_path=store) == {"refreshed": 1}
    assert "handle_refund" not in _names(repo, store, "handle_refund")


def test_nothing_changed_returns_nothing(indexed):
    _, repo, store = indexed
    assert ror.refresh_before_read(repo, storage_path=store) is None


def test_a_second_call_inside_the_cooldown_runs_no_git(indexed, monkeypatch):
    src, repo, store = indexed
    assert ror.refresh_before_read(repo, storage_path=store) is None
    calls = []
    real = subprocess.run
    monkeypatch.setattr(
        ror.subprocess, "run", lambda *a, **k: calls.append(a) or real(*a, **k)
    )
    (src / "app.py").write_text("def changed_later(x):\n    return x\n")
    assert ror.refresh_before_read(repo, storage_path=store) is None
    assert calls == []


def test_too_many_files_answers_unrepaired_and_names_the_call(indexed, monkeypatch):
    src, repo, store = indexed
    monkeypatch.setattr(ror, "REFRESH_MAX_FILES", 1)
    (src / "app.py").write_text("def a2(x):\n    return x\n")
    (src / "util.py").write_text("def b2(x):\n    return x\n")
    block = ror.refresh_before_read(repo, storage_path=store)
    assert block["refreshed"] == 0
    assert block["stale_files"] == 2
    assert block["reason"] == "too_many_files"
    assert block["call"] == "index_folder"
    assert block["args"]["incremental"] is True
    assert "handle_order" in _names(repo, store, "handle_order")  # nothing written


def test_a_held_write_lock_answers_unrepaired_without_waiting(indexed, monkeypatch):
    src, repo, store = indexed
    (src / "app.py").write_text("def a3(x):\n    return x\n")
    monkeypatch.setattr(ror, "_index_write_locked", lambda *_a: True)
    started = time.monotonic()
    block = ror.refresh_before_read(repo, storage_path=store)
    assert time.monotonic() - started < 1.0
    assert block["reason"] == "locked"
    assert block["refreshed"] == 0


def test_a_refresh_past_its_budget_says_so(indexed, monkeypatch):
    src, repo, store = indexed
    (src / "app.py").write_text("def a4(x):\n    return x\n")
    monkeypatch.setattr(ror, "REFRESH_BUDGET_SECONDS", 0.05)
    real = ror._reindex

    def slow(*a, **k):
        time.sleep(0.5)
        return real(*a, **k)

    started = []

    def counted(*a, **k):
        started.append(1)
        return slow(*a, **k)

    monkeypatch.setattr(ror, "_reindex", counted)
    block = ror.refresh_before_read(repo, storage_path=store)
    # The re-index is still running: its outcome is unknown, so no count of
    # zero and no call that would start a second write.
    assert block["reason"] == "in_progress"
    assert block["refreshed"] is None
    assert "call" not in block
    # A check while it runs starts no second writer, even past the cooldown.
    with ror._lock:
        ror._last_check.clear()
    again = ror.refresh_before_read(repo, storage_path=store)
    assert again["reason"] == "in_progress"
    assert started == [1]
    deadline = time.monotonic() + 10
    while ror._in_flight and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not ror._in_flight
    assert "a4" in _names(repo, store, "a4")


def test_a_file_the_indexer_never_takes_is_not_stale(indexed):
    """A log or a binary in the tree is not indexed, so it is never counted or re-indexed."""
    src, repo, store = indexed
    (src / "run.log").write_text("started\n")
    (src / "blob.bin").write_bytes(bytes(range(256)) * 4)
    assert ror.refresh_before_read(repo, storage_path=store) is None
    ror.reset_cooldowns()
    assert ror.refresh_before_read(repo, storage_path=store) is None


def test_many_files_the_indexer_never_takes_do_not_flag_the_index(indexed, monkeypatch):
    """Before: 60 such files read `too_many_files`, and `index_folder` could not clear it."""
    src, repo, store = indexed
    monkeypatch.setattr(ror, "REFRESH_MAX_FILES", 5)
    for i in range(60):
        (src / f"out{i}.log").write_text("x\n")
    assert ror.refresh_before_read(repo, storage_path=store) is None


def test_too_many_files_says_the_count_is_a_floor(indexed, monkeypatch):
    src, repo, store = indexed
    monkeypatch.setattr(ror, "REFRESH_MAX_FILES", 1)
    for i in range(4):
        (src / f"new{i}.py").write_text(f"def n{i}():\n    return {i}\n")
    block = ror.refresh_before_read(repo, storage_path=store)
    assert block["reason"] == "too_many_files"
    assert block["stale_files_basis"] == "at_least"
    assert block["stale_files"] > 1


def test_a_check_reads_metadata_and_never_hydrates_the_index(indexed, monkeypatch):
    from jcodemunch_mcp.storage import IndexStore

    src, repo, store = indexed
    loads = []
    real = IndexStore.load_index

    def counted(self, *a, **k):
        loads.append(a)
        return real(self, *a, **k)

    monkeypatch.setattr(IndexStore, "load_index", counted)
    monkeypatch.setattr(ror, "_reindex", lambda *a, **k: {"success": True})
    (src / "app.py").write_text("def a5(x):\n    return x\n")
    assert ror.refresh_before_read(repo, storage_path=store) == {"refreshed": 1}
    assert loads == []


def test_a_folder_that_is_not_a_git_repo_is_left_alone(tmp_path):
    src = tmp_path / "plain"
    src.mkdir()
    (src / "a.py").write_text("def f():\n    return 1\n")
    store = tmp_path / "store"
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    (src / "a.py").write_text("def g():\n    return 2\n")
    assert ror.refresh_before_read(res["repo"], storage_path=str(store)) is None


def test_the_block_is_a_universal_body_key():
    """A universal body key falls back to JSON when an encoder cannot carry it."""
    from jcodemunch_mcp.encoding.schema_driven import is_universal_body_key

    assert is_universal_body_key("index_refresh")


def test_the_switch_is_on_by_default():
    from jcodemunch_mcp import config as cfg

    assert cfg.DEFAULTS["refresh_on_read"] is True


@pytest.mark.asyncio
async def test_the_dispatcher_attaches_the_block_only_when_switched_on(
    indexed, monkeypatch
):
    from jcodemunch_mcp import config as cfg
    from jcodemunch_mcp.server import call_tool

    src, repo, store = indexed
    monkeypatch.setenv("CODE_INDEX_PATH", store)
    (src / "app.py").write_text("def process_order(x):\n    return x + 2\n")

    async def body(query):
        res = await call_tool("search_symbols", {"repo": repo, "query": query})
        content = res.content if hasattr(res, "content") else res
        return (
            json.loads(content[0].text)
            if content[0].text.lstrip().startswith("{")
            else content[0].text
        )

    monkeypatch.setitem(cfg._GLOBAL_CONFIG, "refresh_on_read", False)
    off = await body("handle_order")
    assert "index_refresh" not in str(off)
    monkeypatch.setitem(cfg._GLOBAL_CONFIG, "refresh_on_read", True)
    monkeypatch.setitem(cfg._GLOBAL_CONFIG, "server_output", "raw")
    on = await body("process_order")
    assert on["index_refresh"] == {"refreshed": 1}
    assert {r["name"] for r in on["results"]} >= {"process_order"}


@pytest.mark.asyncio
async def test_a_cached_answer_does_not_survive_the_refresh(tmp_path, monkeypatch):
    """Before: `find_references` served its cached pre-refresh answer beside `refreshed: 1`."""
    from jcodemunch_mcp import config as cfg
    from jcodemunch_mcp.server import call_tool

    src = tmp_path / "proj"
    src.mkdir()
    (src / "lib.py").write_text("def helper():\n    return 1\n")
    (src / "a.py").write_text("from lib import helper\n")
    _git(src, "init", "-q")
    _git(src, "add", ".")
    _git(src, "commit", "-qm", "one")
    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    repo = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))[
        "repo"
    ]
    monkeypatch.setitem(cfg._GLOBAL_CONFIG, "server_output", "raw")
    monkeypatch.setitem(cfg._GLOBAL_CONFIG, "refresh_on_read", True)

    async def files():
        res = await call_tool("find_references", {"repo": repo, "identifier": "helper"})
        content = res.content if hasattr(res, "content") else res
        body = json.loads(content[0].text)
        return body, {r.get("file") for r in body.get("references", [])}

    _, first = await files()
    assert first == {"a.py"}
    time.sleep(1.1)
    (src / "b.py").write_text("from lib import helper\n")
    ror.reset_cooldowns()
    body, second = await files()
    assert body.get("index_refresh") == {"refreshed": 1}
    assert second == {"a.py", "b.py"}


@pytest.mark.asyncio
async def test_a_disabled_tool_writes_nothing(indexed, monkeypatch):
    from jcodemunch_mcp import config as cfg
    from jcodemunch_mcp.server import call_tool

    src, repo, store = indexed
    monkeypatch.setenv("CODE_INDEX_PATH", store)
    monkeypatch.setitem(cfg._GLOBAL_CONFIG, "refresh_on_read", True)
    calls = []
    monkeypatch.setattr(ror, "refresh_before_read", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(
        cfg, "is_tool_disabled", lambda name, repo=None: name == "search_symbols"
    )
    await call_tool("search_symbols", {"repo": repo, "query": "handle_order"})
    assert calls == []
