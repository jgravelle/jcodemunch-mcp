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

import asyncio
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

    monkeypatch.setattr(ror, "_reindex", slow)
    block = ror.refresh_before_read(repo, storage_path=store)
    assert block["reason"] == "budget"
    assert block["refreshed"] == 0


def test_a_folder_that_is_not_a_git_repo_is_left_alone(tmp_path):
    src = tmp_path / "plain"
    src.mkdir()
    (src / "a.py").write_text("def f():\n    return 1\n")
    store = tmp_path / "store"
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    (src / "a.py").write_text("def g():\n    return 2\n")
    assert ror.refresh_before_read(res["repo"], storage_path=str(store)) is None


def test_the_block_survives_every_compact_encoder():
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
