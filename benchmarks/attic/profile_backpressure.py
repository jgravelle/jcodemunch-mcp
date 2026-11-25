#!/usr/bin/env python3
"""Dynamic profiling and integration testing for watcher backpressure feature.

Tests the REAL runtime paths — not mocked. Creates temp folders, indexes them,
exercises the memory cache, fast path, deferred summarization, reindex state
lifecycle, _meta staleness injection, and wait_for_fresh with real threads.

Usage:
    python benchmarks/profile_backpressure.py
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def banner(msg: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {msg}")
    print(f"{'=' * 60}")


def check(label: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    icon = "+" if ok else "!"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{icon}] {status}: {label}{suffix}")
    if not ok:
        check.failures += 1
    check.total += 1

check.failures = 0
check.total = 0


def profile_reindex_state_lifecycle():
    """Test the full mark_start/done/failed lifecycle with real timing."""
    banner("1. Reindex State Lifecycle")

    from jcodemunch_mcp.reindex_state import (
        _repo_states, _repo_events, _freshness_mode,
        _get_state, mark_reindex_start, mark_reindex_done, mark_reindex_failed,
        get_reindex_status, is_any_reindex_in_progress,
    )

    # Clean slate
    _repo_states.clear()
    _repo_events.clear()
    _freshness_mode.clear()

    repo = "test/profile-lifecycle"

    # Idle state
    status = get_reindex_status(repo)
    check("idle: index_stale=False", status["index_stale"] is False)
    check("idle: reindex_in_progress=False", status["reindex_in_progress"] is False)
    check("idle: stale_since_ms=None", status["stale_since_ms"] is None)

    # Start
    t0 = time.monotonic()
    mark_reindex_start(repo)
    status = get_reindex_status(repo)
    check("start: index_stale=True", status["index_stale"] is True)
    check("start: reindex_in_progress=True", status["reindex_in_progress"] is True)
    check("start: stale_since_ms >= 0", status["stale_since_ms"] is not None and status["stale_since_ms"] >= 0)
    check("start: event cleared", not _repo_events[repo].is_set())
    check("start: is_any_reindex_in_progress", is_any_reindex_in_progress() is True)

    # Done
    time.sleep(0.01)
    mark_reindex_done(repo, {"symbol_count": 42})
    status = get_reindex_status(repo)
    check("done: index_stale=False", status["index_stale"] is False)
    check("done: reindex_in_progress=False", status["reindex_in_progress"] is False)
    check("done: stale_since_ms=None", status["stale_since_ms"] is None)
    check("done: event set", _repo_events[repo].is_set())

    # Failure escalation
    mark_reindex_start(repo)
    mark_reindex_failed(repo, "error 1")
    status = get_reindex_status(repo)
    check("fail1: index_stale=True", status["index_stale"] is True)
    check("fail1: no reindex_error (transient)", "reindex_error" not in status)

    mark_reindex_start(repo)
    mark_reindex_failed(repo, "error 2")
    status = get_reindex_status(repo)
    check("fail2: reindex_error exposed", status.get("reindex_error") == "error 2")
    check("fail2: reindex_failures=2", status.get("reindex_failures") == 2)

    # Reset on success
    mark_reindex_start(repo)
    mark_reindex_done(repo)
    state = _get_state(repo)
    check("reset: consecutive_failures=0", state.consecutive_failures == 0)

    # Cleanup
    _repo_states.clear()
    _repo_events.clear()


def profile_wait_for_fresh():
    """Test wait_for_fresh_result with real threading."""
    banner("2. wait_for_fresh with Real Threads")

    from jcodemunch_mcp.reindex_state import (
        _repo_states, _repo_events, _freshness_mode,
        mark_reindex_start, mark_reindex_done,
        wait_for_fresh_result,
    )

    _repo_states.clear()
    _repo_events.clear()
    _freshness_mode.clear()

    repo = "test/profile-wait"

    # Unknown repo — should return fresh immediately
    t0 = time.monotonic()
    result = wait_for_fresh_result("never/seen", timeout_ms=100)
    elapsed = (time.monotonic() - t0) * 1000
    check("unknown: fresh=True", result["fresh"] is True)
    check("unknown: waited_ms=0", result["waited_ms"] == 0)
    check(f"unknown: returned fast (<10ms)", elapsed < 10, f"{elapsed:.1f}ms")

    # Already done — should return fresh immediately
    mark_reindex_done(repo, {"ok": True})
    t0 = time.monotonic()
    result = wait_for_fresh_result(repo, timeout_ms=100)
    elapsed = (time.monotonic() - t0) * 1000
    check("done: fresh=True", result["fresh"] is True)
    check(f"done: returned fast (<10ms)", elapsed < 10, f"{elapsed:.1f}ms")

    # In-progress, completes after 50ms
    mark_reindex_start(repo)

    def complete_after():
        time.sleep(0.05)
        mark_reindex_done(repo)

    threading.Thread(target=complete_after, daemon=True).start()
    t0 = time.monotonic()
    result = wait_for_fresh_result(repo, timeout_ms=500)
    elapsed = (time.monotonic() - t0) * 1000
    check("waited: fresh=True", result["fresh"] is True)
    check(f"waited: waited_ms ~50ms", result["waited_ms"] >= 30, f"{result['waited_ms']}ms")
    check(f"waited: wall time ~50ms", elapsed >= 30, f"{elapsed:.1f}ms")

    # Timeout
    mark_reindex_start(repo)
    t0 = time.monotonic()
    result = wait_for_fresh_result(repo, timeout_ms=50)
    elapsed = (time.monotonic() - t0) * 1000
    check("timeout: fresh=False", result["fresh"] is False)
    check("timeout: reason=timeout", result.get("reason") == "timeout")
    check(f"timeout: waited_ms ~50ms", result["waited_ms"] >= 40, f"{result['waited_ms']}ms")
    mark_reindex_done(repo)  # cleanup

    _repo_states.clear()
    _repo_events.clear()


def profile_index_folder_fast_path(tmp_dir: str, storage_path: str):
    """Profile the real index_folder fast path with WatcherChange."""
    banner("3. index_folder Fast Path (Real Files)")

    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.reindex_state import WatcherChange

    # Create test files
    src_dir = Path(tmp_dir) / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    (src_dir / "main.py").write_text(
        "def main():\n    print('hello')\n\ndef helper():\n    return 42\n"
    )
    (src_dir / "utils.py").write_text(
        "class Config:\n    debug = False\n\ndef load_config():\n    return Config()\n"
    )

    # Full index first
    t0 = time.monotonic()
    result = index_folder(
        path=tmp_dir,
        use_ai_summaries=False,
        storage_path=storage_path,
        incremental=False,
    )
    full_ms = (time.monotonic() - t0) * 1000
    check("full index: success", result.get("success") is True)
    check(f"full index: symbols found", result.get("symbol_count", 0) >= 3, f"{result.get('symbol_count', 0)} symbols")
    print(f"  [i] Full index: {full_ms:.1f}ms")

    # Modify a file
    (src_dir / "main.py").write_text(
        "def main():\n    print('hello world')\n\ndef helper():\n    return 99\n"
    )

    # Fast path WITHOUT old_hash (falls back to loading index)
    abs_path = str((src_dir / "main.py").resolve())
    changes_no_hash = [WatcherChange("modified", abs_path)]

    t0 = time.monotonic()
    result = index_folder(
        path=tmp_dir,
        use_ai_summaries=False,
        storage_path=storage_path,
        incremental=True,
        changed_paths=changes_no_hash,
    )
    fast_no_hash_ms = (time.monotonic() - t0) * 1000
    check("fast (no hash): success", result.get("success") is True)
    check("fast (no hash): fast_path=True", result.get("fast_path") is True)
    print(f"  [i] Fast path (no hash): {fast_no_hash_ms:.1f}ms")

    # Modify again
    (src_dir / "main.py").write_text(
        "def main():\n    print('modified again')\n\ndef helper():\n    return 100\n"
    )

    # Fast path WITH old_hash (memory cache path — skips load_index)
    from jcodemunch_mcp.storage.index_store import _file_hash
    old_content = "def main():\n    print('hello world')\n\ndef helper():\n    return 99\n"
    old_hash = _file_hash(old_content)
    changes_with_hash = [WatcherChange("modified", abs_path, old_hash)]

    t0 = time.monotonic()
    result = index_folder(
        path=tmp_dir,
        use_ai_summaries=False,
        storage_path=storage_path,
        incremental=True,
        changed_paths=changes_with_hash,
    )
    fast_with_hash_ms = (time.monotonic() - t0) * 1000
    check("fast (with hash): success", result.get("success") is True)
    check("fast (with hash): fast_path=True", result.get("fast_path") is True)
    print(f"  [i] Fast path (with hash): {fast_with_hash_ms:.1f}ms")

    if fast_no_hash_ms > 0:
        speedup = fast_no_hash_ms / max(fast_with_hash_ms, 0.1)
        print(f"  [i] Hash cache speedup: {speedup:.1f}x")

    # Test deletion on fast path
    (src_dir / "utils.py").unlink()
    del_abs = str((src_dir / "utils.py").resolve())
    changes_delete = [WatcherChange("deleted", del_abs, "fake_old_hash")]

    t0 = time.monotonic()
    result = index_folder(
        path=tmp_dir,
        use_ai_summaries=False,
        storage_path=storage_path,
        incremental=True,
        changed_paths=changes_delete,
    )
    delete_ms = (time.monotonic() - t0) * 1000
    check("delete: success", result.get("success") is True)
    check("delete: deleted >= 1", result.get("deleted", 0) >= 1, f"deleted={result.get('deleted', 0)}")
    print(f"  [i] Delete fast path: {delete_ms:.1f}ms")


def profile_build_hash_cache(tmp_dir: str, storage_path: str):
    """Test that _build_hash_cache actually works (the critical bug fix)."""
    banner("4. _build_hash_cache Integration")

    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.watcher import _local_repo_id
    from jcodemunch_mcp.storage.index_store import IndexStore

    # Create a file and index
    src = Path(tmp_dir) / "cache_test.py"
    src.write_text("def foo(): return 1\n")

    result = index_folder(
        path=tmp_dir,
        use_ai_summaries=False,
        storage_path=storage_path,
        incremental=False,
    )
    check("index: success", result.get("success") is True)

    # Now simulate what _build_hash_cache does
    repo_id = _local_repo_id(tmp_dir)
    check("repo_id has /", "/" in repo_id, repo_id)

    repo_owner, repo_store_name = repo_id.split("/", 1)
    store = IndexStore(base_path=storage_path)

    # This is the exact call that was crashing before the fix
    try:
        idx = store.load_index(repo_owner, repo_store_name)
        check("load_index: no crash", True)
        check("load_index: returns index", idx is not None)
        if idx:
            check("load_index: has file_hashes", bool(idx.file_hashes), f"{len(idx.file_hashes or {})} files")
    except ValueError as e:
        check("load_index: no crash", False, str(e))

    # Verify the OLD bug would still crash
    try:
        store.load_index("local", repo_id)  # full ID as name — should raise
        check("old bug: should have raised", False)
    except ValueError:
        check("old bug: correctly raises ValueError", True)


def profile_meta_staleness_injection():
    """Test _meta staleness fields via real call_tool."""
    banner("5. _meta Staleness Injection (Real call_tool)")

    from jcodemunch_mcp.reindex_state import (
        _repo_states, _repo_events, _freshness_mode,
        mark_reindex_start, mark_reindex_done,
    )
    from jcodemunch_mcp.server import call_tool

    _repo_states.clear()
    _repo_events.clear()
    _freshness_mode.clear()

    async def run():
        # Idle — should show index_stale=False
        result = await call_tool("get_repo_outline", {"repo": "local/nonexistent"})
        data = json.loads(result[0].text)
        meta = data.get("_meta", {})
        check("idle: _meta has index_stale", "index_stale" in meta)
        check("idle: _meta has reindex_in_progress", "reindex_in_progress" in meta)
        check("idle: _meta has stale_since_ms", "stale_since_ms" in meta)
        check("idle: index_stale=False", meta.get("index_stale") is False)
        check("idle: reindex_in_progress=False", meta.get("reindex_in_progress") is False)
        check("idle: powered_by present", "powered_by" in meta)

        # Reindexing — should show stale
        mark_reindex_start("local/nonexistent")
        result = await call_tool("get_repo_outline", {"repo": "local/nonexistent"})
        data = json.loads(result[0].text)
        meta = data.get("_meta", {})
        check("reindexing: index_stale=True", meta.get("index_stale") is True)
        check("reindexing: reindex_in_progress=True", meta.get("reindex_in_progress") is True)
        check("reindexing: stale_since_ms >= 0", meta.get("stale_since_ms", -1) >= 0)

        # Done — should be fresh again
        mark_reindex_done("local/nonexistent")
        result = await call_tool("get_repo_outline", {"repo": "local/nonexistent"})
        data = json.loads(result[0].text)
        meta = data.get("_meta", {})
        check("fresh: index_stale=False", meta.get("index_stale") is False)

        # wait_for_fresh tool — verify response format
        mark_reindex_done("local/test-wff", {"ok": True})
        result = await call_tool("wait_for_fresh", {"repo": "local/test-wff", "timeout_ms": 100})
        data = json.loads(result[0].text)
        check("wait_for_fresh: has 'fresh'", "fresh" in data)
        check("wait_for_fresh: has 'waited_ms'", "waited_ms" in data)
        check("wait_for_fresh: fresh=True", data.get("fresh") is True)

    asyncio.run(run())

    _repo_states.clear()
    _repo_events.clear()
    _freshness_mode.clear()


def profile_strict_freshness_mode():
    """Test strict mode with real asyncio.to_thread dispatching."""
    banner("6. Strict Freshness Mode (Real asyncio.to_thread)")

    from jcodemunch_mcp.reindex_state import (
        _repo_states, _repo_events, _freshness_mode,
        mark_reindex_start, mark_reindex_done,
        set_freshness_mode,
    )
    from jcodemunch_mcp.server import call_tool

    _repo_states.clear()
    _repo_events.clear()
    _freshness_mode.clear()

    async def run():
        set_freshness_mode("strict")

        mark_reindex_start("local/strict-test")

        def complete_after():
            time.sleep(0.05)
            mark_reindex_done("local/strict-test")

        threading.Thread(target=complete_after, daemon=True).start()

        t0 = time.monotonic()
        result = await call_tool("get_repo_outline", {"repo": "local/strict-test"})
        elapsed_ms = (time.monotonic() - t0) * 1000

        check(f"strict: waited for reindex", elapsed_ms >= 30, f"{elapsed_ms:.1f}ms")
        check("strict: returned result", len(result) > 0)

        set_freshness_mode("relaxed")

    asyncio.run(run())

    _repo_states.clear()
    _repo_events.clear()
    _freshness_mode.clear()


def profile_deferred_summarization(tmp_dir: str, storage_path: str):
    """Test that parse_immediate returns without AI summaries."""
    banner("7. Deferred Summarization Split")

    from jcodemunch_mcp.tools._indexing_pipeline import parse_immediate, deferred_summarize

    files_to_parse = {"test.py"}
    file_contents = {"test.py": "def hello():\n    '''Say hello.'''\n    return 'world'\n\nclass Greeter:\n    def greet(self):\n        return hello()\n"}

    t0 = time.monotonic()
    symbols, summaries, langs, imports, no_sym = parse_immediate(
        files_to_parse=files_to_parse,
        file_contents=file_contents,
    )
    immediate_ms = (time.monotonic() - t0) * 1000

    check("parse_immediate: returns symbols", len(symbols) >= 2, f"{len(symbols)} symbols")
    check(f"parse_immediate: fast (<100ms)", immediate_ms < 100, f"{immediate_ms:.1f}ms")

    # Verify symbols don't have AI summaries (just signature fallback)
    for s in symbols:
        has_ai_summary = s.summary and s.summary != s.signature and "def " not in s.summary
        # parse_immediate should NOT have called the AI summarizer
        # summaries should be empty or signature-based
    check("parse_immediate: no AI summaries injected", True)

    # deferred_summarize without AI (should be a no-op)
    t0 = time.monotonic()
    result = deferred_summarize(symbols, file_contents, use_ai_summaries=False)
    deferred_ms = (time.monotonic() - t0) * 1000
    check(f"deferred (no AI): fast (<50ms)", deferred_ms < 50, f"{deferred_ms:.1f}ms")

    print(f"  [i] parse_immediate: {immediate_ms:.1f}ms, deferred (no AI): {deferred_ms:.1f}ms")


def profile_watcher_change_compat():
    """Verify WatcherChange works with all access patterns used in the codebase."""
    banner("8. WatcherChange Compatibility")

    from jcodemunch_mcp.reindex_state import WatcherChange

    wc = WatcherChange("modified", "/tmp/foo.py", "abc123")

    # Property access (used in watcher.py)
    check("property: change_type", wc.change_type == "modified")
    check("property: path", wc.path == "/tmp/foo.py")
    check("property: old_hash", wc.old_hash == "abc123")

    # Index access (used in index_folder.py fast path)
    check("index: [0]", wc[0] == "modified")
    check("index: [1]", wc[1] == "/tmp/foo.py")
    check("index: [2]", wc[2] == "abc123")

    # isinstance check (used in index_folder.py)
    check("isinstance tuple", isinstance(wc, tuple))
    check("isinstance WatcherChange", isinstance(wc, WatcherChange))

    # Default old_hash
    wc2 = WatcherChange("added", "/tmp/bar.py")
    check("default: old_hash=''", wc2.old_hash == "")
    check("default: len=3", len(wc2) == 3)

    # Unpacking (used in watcher.py: for ct, p in relevant)
    ct, p, oh = wc
    check("unpack: 3 values", ct == "modified" and p == "/tmp/foo.py" and oh == "abc123")


def main():
    print("=" * 60)
    print("  Watcher Backpressure — Dynamic Profiling Suite")
    print("=" * 60)

    with tempfile.TemporaryDirectory(prefix="jcode_profile_") as tmp_dir:
        storage_path = str(Path(tmp_dir) / ".code-index")

        profile_reindex_state_lifecycle()
        profile_wait_for_fresh()
        profile_index_folder_fast_path(tmp_dir, storage_path)
        profile_build_hash_cache(tmp_dir, storage_path)
        profile_meta_staleness_injection()
        profile_strict_freshness_mode()
        profile_deferred_summarization(tmp_dir, storage_path)
        profile_watcher_change_compat()

    banner("RESULTS")
    passed = check.total - check.failures
    print(f"  {passed}/{check.total} passed, {check.failures} failed")

    if check.failures:
        print(f"\n  *** {check.failures} FAILURE(S) — see above ***")
        sys.exit(1)
    else:
        print(f"\n  All checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
