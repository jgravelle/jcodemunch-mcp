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
import re
import subprocess
import threading
import time
from pathlib import Path

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
    real = ror._reindex
    release = threading.Event()
    started = []

    def held(*a, **k):
        started.append(1)
        release.wait(10)  # the re-index outlives the budget until the test says so
        return real(*a, **k)

    monkeypatch.setattr(ror, "_reindex", held)
    t0 = time.monotonic()
    block = ror.refresh_before_read(repo, storage_path=store)
    waited = time.monotonic() - t0
    # The re-index is still running: its outcome is unknown, so no count of
    # zero and no call that would start a second write. The count of stale
    # files is known, since this call listed them.
    assert block == {"reason": "in_progress", "refreshed": None, "stale_files": 1}
    # The caller was held for the budget, no longer: git and the walk rule
    # spent part of it, the wait took the rest.
    assert waited < ror.REFRESH_BUDGET_SECONDS + 1.0
    # A check while it runs starts no second writer, even past the cooldown,
    # and carries no count: it did not list the files.
    with ror._lock:
        ror._last_check.clear()
    again = ror.refresh_before_read(repo, storage_path=store)
    assert again == {"reason": "in_progress", "refreshed": None}
    assert started == [1]
    release.set()
    deadline = time.monotonic() + 10
    while ror._in_flight and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not ror._in_flight
    assert "a4" in _names(repo, store, "a4")


def test_a_second_read_racing_the_in_flight_mark_gets_no_count(indexed, monkeypatch):
    """The docs promise a concurrent read `in_progress` with no count; the gap
    between the lock check and the in-flight mark used to return one."""
    src, repo, store = indexed
    (src / "app.py").write_text("def a4b(x):\n    return x\n")
    monkeypatch.setattr(ror, "_reindex", lambda *a, **k: {"success": True})
    key_seen = []
    real_locked = ror._index_write_locked

    def mark_in_flight_first(*a):
        # Another read wins the race while this one is between the lock
        # check and `_in_flight.add`.
        with ror._lock:
            key_seen.extend(ror._last_check)
            ror._in_flight.add(key_seen[0])
        return real_locked(*a)

    monkeypatch.setattr(ror, "_index_write_locked", mark_in_flight_first)
    block = ror.refresh_before_read(repo, storage_path=store)
    assert block == {"reason": "in_progress", "refreshed": None}
    assert "stale_files" not in block
    ror.reset_cooldowns()


def test_a_failed_reindex_claims_no_count(indexed, monkeypatch):
    """`index_folder` may have committed part of the set before raising, so
    `refreshed` is unknown, never a verified zero."""
    src, repo, store = indexed
    (src / "app.py").write_text("def a6(x):\n    return x\n")

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ror, "_reindex", boom)
    block = ror.refresh_before_read(repo, storage_path=store)
    assert block["reason"] == "failed"
    assert block["refreshed"] is None
    assert block["stale_files"] == 1
    assert block["call"] == "index_folder"


def test_git_past_the_budget_answers_undetermined_inside_the_budget(
    indexed, monkeypatch
):
    """The budget bounds the whole check, git included: a git that does not
    answer in time is cut at the deadline and the answer never reads as
    "nothing stale"."""
    src, repo, store = indexed
    (src / "app.py").write_text("def a7(x):\n    return x\n")
    monkeypatch.setattr(ror, "REFRESH_BUDGET_SECONDS", 0.3)
    real = subprocess.run

    def slow_git(cmd, *a, **k):
        if "diff" in cmd:
            time.sleep(0.6)  # past the deadline; the real call's timeout cuts it
        return real(cmd, *a, **k)

    monkeypatch.setattr(ror.subprocess, "run", slow_git)
    t0 = time.monotonic()
    block = ror.refresh_before_read(repo, storage_path=store)
    assert time.monotonic() - t0 < 1.5
    assert block == {"reason": "undetermined", "refreshed": None, "unchecked": "budget"}
    assert "handle_order" in _names(repo, store, "handle_order")  # nothing written


def test_the_walk_rule_stops_at_the_deadline_and_says_so(indexed, monkeypatch):
    """The per-path walk rule shares the budget: at the deadline it stops and
    the rest is reported unchecked, never as "nothing stale"."""
    from pathlib import Path

    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools import index_file as _if

    src, repo, store = indexed
    _refused_tree(src, 40)
    owner, name = repo.split("/", 1)
    index = IndexStore(base_path=store).open_selective(owner, name, symbol_ids=())
    real = _if._walk_refusal
    asked = []

    def slow_rule(*a, **k):
        asked.append(1)
        time.sleep(0.05)
        return real(*a, **k)

    monkeypatch.setattr(_if, "_walk_refusal", slow_rule)
    cands = dict.fromkeys(f"node_modules/m{i}.js" for i in range(40))
    out, complete = ror._indexable(Path(src), index, cands, time.monotonic() + 0.3)
    assert out == []
    assert complete is False
    assert 0 < len(asked) < 40, "the deadline cut the walk rule short"


def test_the_package_json_prescan_runs_once_per_check(indexed, monkeypatch):
    """The walk rule pre-scans the tree for every file over the size cap; a
    check with several such files paid the scan each time (0.7 s each here)."""
    from jcodemunch_mcp.tools import index_folder as _ifo

    src, repo, store = indexed
    monkeypatch.setitem(
        __import__("jcodemunch_mcp.config", fromlist=["x"])._GLOBAL_CONFIG,
        "max_file_size",
        100,
    )
    for i in range(3):
        (src / f"big{i}.py").write_text("x = 1\n" * 40)
    scans = []
    real = _ifo._scan_package_json_forced_paths
    monkeypatch.setattr(
        _ifo,
        "_scan_package_json_forced_paths",
        lambda root: scans.append(root) or real(root),
    )
    ror.refresh_before_read(repo, storage_path=store)
    assert len(scans) == 1


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
async def test_an_answer_cached_after_the_workers_eviction_does_not_survive(
    indexed, monkeypatch
):
    """Review: on the `in_progress` path the tool reads the pre-write index, the
    worker commits and the store evicts, and THEN the tool's `cache_put` lands,
    so the stale answer survived in the shared LRU. The order is pinned, not
    raced: the fake tool runs the eviction itself between its read and its put.
    Closed in the dispatcher, below every cached tool: an eviction during the
    call evicts again when the call ends."""
    from jcodemunch_mcp import config as cfg
    from jcodemunch_mcp.server import call_tool
    from jcodemunch_mcp.storage import result_cache_get, result_cache_put
    import importlib

    from jcodemunch_mcp.storage import sqlite_store

    _fr = importlib.import_module("jcodemunch_mcp.tools.find_references")

    src, repo, store = indexed
    monkeypatch.setenv("CODE_INDEX_PATH", store)
    monkeypatch.setitem(cfg._GLOBAL_CONFIG, "server_output", "raw")
    monkeypatch.setitem(cfg._GLOBAL_CONFIG, "refresh_on_read", True)
    monkeypatch.setattr(ror, "refresh_before_read", lambda *a, **k: ror._in_progress(1))
    owner, name = repo.split("/", 1)
    key = ("helper",)
    order = []

    def tool_reads_then_worker_commits_then_tool_caches(**kw):
        assert result_cache_get("find_references", repo, key) is None
        order.append("tool read the old index")
        sqlite_store._evict_result_cache(owner, name)  # the worker's commit
        order.append("worker committed and evicted")
        stale = {"references": [{"file": "old.py"}], "total": 1}
        result_cache_put("find_references", repo, key, stale)
        order.append("tool cached its answer")
        return stale

    monkeypatch.setattr(
        _fr, "find_references", tool_reads_then_worker_commits_then_tool_caches
    )
    await call_tool("find_references", {"repo": repo, "identifier": "helper"})
    assert order == [
        "tool read the old index",
        "worker committed and evicted",
        "tool cached its answer",
    ]
    assert result_cache_get("find_references", repo, key) is None, (
        "an answer computed from the pre-write index survived the write's eviction"
    )


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


def _refused_tree(src, n):
    """`n` files the walk refuses (an un-ignored `node_modules/`), all untracked."""
    (src / "node_modules").mkdir()
    for i in range(n):
        (src / "node_modules" / f"m{i}.js").write_text("module.exports = 1\n")


def test_past_the_refusal_cap_a_tracked_edit_is_still_repaired(indexed, monkeypatch):
    """Review round 2: past the cap the check returned nothing and dropped the edit."""
    src, repo, store = indexed
    monkeypatch.setattr(ror, "_MAX_REFUSALS_CHECKED", 20)
    (src / "app.py").write_text("def renamed_fn(x):\n    return x\n")
    _refused_tree(src, 40)
    block = ror.refresh_before_read(repo, storage_path=store)
    assert block == {"refreshed": 1, "unchecked": "not_indexed_paths"}
    assert "renamed_fn" in _names(repo, store, "renamed_fn")


def test_past_the_refusal_cap_with_nothing_else_the_answer_is_undetermined(
    indexed, monkeypatch
):
    """A check that could not ask every path never reads as "nothing stale"."""
    src, repo, store = indexed
    monkeypatch.setattr(ror, "_MAX_REFUSALS_CHECKED", 20)
    _refused_tree(src, 40)
    block = ror.refresh_before_read(repo, storage_path=store)
    assert block["reason"] == "undetermined"
    assert block["refreshed"] is None
    assert "call" not in block
    assert ".gitignore" in block["hint"]


_ROOT = Path(__file__).resolve().parents[1]


def _passages() -> dict[str, str]:
    """Every prose copy of the refresh-on-read figures, by file."""
    changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(
        r"^### Added - a read re-indexes.*?(?=^## |^### )", changelog, re.S | re.M
    )
    assert m, "the CHANGELOG entry is gone"
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    r = re.search(
        r"[^.]*On a git repository, a read tool[^\n]*?`refresh_on_read: false` turns that off\.",
        readme,
    )
    assert r, "the README disclosure sentence is gone"
    security = (_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    s = re.search(r"^- \*\*Refresh on read.*?$", security, re.M)
    assert s, "the SECURITY.md bullet is gone"
    configuration = (_ROOT / "CONFIGURATION.md").read_text(encoding="utf-8")
    c = re.search(r"^\| `refresh_on_read` \|.*$", configuration, re.M)
    assert c, "the CONFIGURATION.md row is gone"
    from jcodemunch_mcp.config import generate_template

    t = re.search(
        r'  // "refresh_on_read": true,\n(?:  //   .*\n)+', generate_template()
    )
    assert t, "the config template block is gone"
    return {
        "CHANGELOG.md": m.group(0),
        "README.md": r.group(0),
        "SECURITY.md": s.group(0),
        "CONFIGURATION.md": c.group(0),
        "config.py template": t.group(0),
    }


def test_every_figure_in_the_prose_is_a_module_constant():
    """The cooldown, the file limit, the budget and the refusal cap are
    restated in five places; each copy must be the module's value, and the
    first three must be present everywhere. A measured timing is written as a
    decimal (0.8 s) and a file count with a thousands comma (1,549); neither
    reads as a constant here, nor does an env var's `=0`."""
    allowed = {
        int(ror.REFRESH_COOLDOWN_SECONDS),
        ror.REFRESH_MAX_FILES,
        int(ror.REFRESH_BUDGET_SECONDS),
        ror._MAX_REFUSALS_CHECKED,
    }
    required = {
        str(int(ror.REFRESH_COOLDOWN_SECONDS)),
        str(ror.REFRESH_MAX_FILES),
        str(int(ror.REFRESH_BUDGET_SECONDS)),
    }
    for where, text in _passages().items():
        found = {int(n) for n in re.findall(r"(?<![\w.=])(?<!\d,)(\d+)(?![\w.])(?!,\d)", text)}
        assert found <= allowed, (
            f"{where} states {sorted(found - allowed)}, which no constant holds"
        )
        assert required <= set(re.findall(r"(?<![\w.=])(?<!\d,)(\d+)(?![\w.])(?!,\d)", text)), (
            f"{where} omits one of the cooldown, the file limit or the budget"
        )


def test_the_figure_check_sees_a_drifted_copy(monkeypatch):
    monkeypatch.setattr(ror, "REFRESH_MAX_FILES", 51)
    with pytest.raises(AssertionError, match="no constant holds"):
        test_every_figure_in_the_prose_is_a_module_constant()
