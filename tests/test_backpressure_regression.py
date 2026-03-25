"""Regression guards for watcher backpressure and index freshness.

These tests protect critical invariants that were broken during initial
implementation and fixed during review. Each test name includes
'_regression_' to signal that removing or weakening it requires
understanding WHY the invariant exists.

If a test here fails, read the docstring before "fixing" it — the
docstring explains the bug it prevents.
"""

import asyncio
import json
import threading
import time

import pytest

from jcodemunch_mcp.reindex_state import (
    _repo_states, _repo_events, _freshness_mode,
    _get_state,
    mark_reindex_start, mark_reindex_done, mark_reindex_failed,
    get_reindex_status, wait_for_fresh_result,
    set_freshness_mode, await_freshness_if_strict,
    WatcherChange,
)


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset all module-level state before and after each test."""
    _repo_states.clear()
    _repo_events.clear()
    _freshness_mode.clear()
    yield
    _repo_states.clear()
    _repo_events.clear()
    _freshness_mode.clear()


# ── _meta contract ──────────────────────────────────────────────────────────


class TestMetaContractRegression:
    """Protect the _meta staleness API contract that agents depend on."""

    @pytest.mark.asyncio
    async def test_regression_meta_has_all_staleness_keys(self):
        """Bug: _meta originally had 'reindexing' instead of 'reindex_in_progress',
        and was missing 'stale_since_ms' entirely. Agents trained on the spec
        could not find the fields they expected."""
        from jcodemunch_mcp.server import call_tool

        result = await call_tool("get_repo_outline", {"repo": "local/nonexistent"})
        data = json.loads(result[0].text)
        meta = data["_meta"]

        assert "index_stale" in meta, "_meta missing 'index_stale'"
        assert "reindex_in_progress" in meta, "_meta missing 'reindex_in_progress'"
        assert "stale_since_ms" in meta, "_meta missing 'stale_since_ms'"

    @pytest.mark.asyncio
    async def test_regression_index_stale_false_when_idle(self):
        """Bug: index_stale was computed as 'reindexing or reindex_finished'.
        reindex_finished was set True by mark_reindex_done and never cleared,
        so index_stale was permanently True after the first reindex.
        This made every response look stale even when the index was fresh."""
        from jcodemunch_mcp.server import call_tool

        mark_reindex_done("local/test-idle")
        result = await call_tool("get_repo_outline", {"repo": "local/test-idle"})
        data = json.loads(result[0].text)

        assert data["_meta"]["index_stale"] is False, (
            "index_stale must be False after successful reindex — "
            "if True, the 'permanently stale' bug has regressed"
        )

    @pytest.mark.asyncio
    async def test_regression_index_stale_true_during_reindex(self):
        """Complement of the above: index_stale MUST be True while reindexing."""
        from jcodemunch_mcp.server import call_tool

        mark_reindex_start("local/test-active")
        result = await call_tool("get_repo_outline", {"repo": "local/test-active"})
        data = json.loads(result[0].text)

        assert data["_meta"]["index_stale"] is True
        assert data["_meta"]["reindex_in_progress"] is True


# ── wait_for_fresh response format ──────────────────────────────────────────


class TestWaitForFreshContractRegression:
    """Protect the wait_for_fresh response schema."""

    def test_regression_response_has_fresh_and_waited_ms(self):
        """Bug: original implementation returned {"status": "fresh", ...}
        instead of {"fresh": true, "waited_ms": 0}. Agents checking
        result.fresh would get undefined."""
        mark_reindex_done("local/test")
        result = wait_for_fresh_result("local/test", timeout_ms=100)

        assert "fresh" in result, "Response must have 'fresh' key (not 'status')"
        assert "waited_ms" in result, "Response must have 'waited_ms' key"
        assert isinstance(result["fresh"], bool), "'fresh' must be a boolean"
        assert isinstance(result["waited_ms"], int), "'waited_ms' must be an integer"

    def test_regression_unknown_repo_no_phantom_state(self):
        """Bug: wait_for_fresh_result called _get_state(repo) for unknown repos,
        creating a _RepoState and threading.Event that were never cleaned up.
        This caused unbounded memory growth from typos."""
        wait_for_fresh_result("typo/never-indexed", timeout_ms=10)

        assert "typo/never-indexed" not in _repo_states, (
            "wait_for_fresh must NOT create state for unknown repos"
        )

    def test_regression_timeout_returns_reason(self):
        """Timeout must return fresh=False with reason='timeout'."""
        mark_reindex_start("local/slow")
        result = wait_for_fresh_result("local/slow", timeout_ms=10)

        assert result["fresh"] is False
        assert result["reason"] == "timeout"
        mark_reindex_done("local/slow")


# ── Failure escalation ──────────────────────────────────────────────────────


class TestFailureEscalationRegression:
    """Protect the 'transient tolerance' policy for reindex failures."""

    def test_regression_first_failure_hides_error(self):
        """Bug: original implementation exposed reindex_error immediately on
        the first failure. The spec requires transient tolerance — error
        details only on the 2nd+ consecutive failure."""
        mark_reindex_start("local/fail")
        mark_reindex_failed("local/fail", "transient error")
        status = get_reindex_status("local/fail")

        assert status["index_stale"] is True, "Index must be stale after failure"
        assert "reindex_error" not in status, (
            "reindex_error must NOT appear on 1st failure (transient tolerance)"
        )

    def test_regression_second_failure_exposes_error(self):
        """2nd consecutive failure must expose error details."""
        mark_reindex_start("local/fail")
        mark_reindex_failed("local/fail", "error 1")
        mark_reindex_start("local/fail")
        mark_reindex_failed("local/fail", "error 2")
        status = get_reindex_status("local/fail")

        assert "reindex_error" in status, "reindex_error must appear on 2nd failure"
        assert "reindex_failures" in status, "reindex_failures must appear on 2nd failure"
        assert status["reindex_failures"] == 2

    def test_regression_success_resets_failure_counter(self):
        """A successful reindex must reset consecutive_failures to 0."""
        mark_reindex_start("local/fail")
        mark_reindex_failed("local/fail", "err")
        mark_reindex_start("local/fail")
        mark_reindex_failed("local/fail", "err")
        # Now succeed
        mark_reindex_start("local/fail")
        mark_reindex_done("local/fail")

        state = _get_state("local/fail")
        assert state.consecutive_failures == 0, (
            "consecutive_failures must reset to 0 on success"
        )


# ── Strict mode event-loop safety ──────────────────────────────────────────


class TestStrictModeEventLoopRegression:
    """Protect against blocking the asyncio event loop in strict mode."""

    @pytest.mark.asyncio
    async def test_regression_strict_mode_does_not_deadlock(self):
        """Bug: await_freshness_if_strict was called directly from async code
        (not via asyncio.to_thread). threading.Event.wait() blocked the event
        loop, freezing the entire MCP server.

        This test runs strict mode from inside asyncio.run. If the bug
        regresses, this test will deadlock and timeout."""
        set_freshness_mode("strict")
        mark_reindex_start("local/deadlock-test")

        def complete_soon():
            time.sleep(0.03)
            mark_reindex_done("local/deadlock-test")

        threading.Thread(target=complete_soon, daemon=True).start()

        from jcodemunch_mcp.server import call_tool
        # If await_freshness_if_strict blocks the loop, this never returns
        result = await asyncio.wait_for(
            call_tool("get_repo_outline", {"repo": "local/deadlock-test"}),
            timeout=2.0,  # 2s — generous; should complete in ~30ms
        )
        assert len(result) > 0
        set_freshness_mode("relaxed")


# ── stale_since invariant ───────────────────────────────────────────────────


class TestStaleSinceRegression:

    def test_regression_stale_since_cleared_on_success(self):
        """stale_since must be None after a successful reindex."""
        mark_reindex_start("local/stale")
        assert _get_state("local/stale").stale_since is not None
        mark_reindex_done("local/stale")
        assert _get_state("local/stale").stale_since is None

    def test_regression_stale_since_preserved_on_failure(self):
        """stale_since must NOT be cleared on failure — index IS still stale."""
        mark_reindex_start("local/stale")
        original = _get_state("local/stale").stale_since
        mark_reindex_failed("local/stale", "error")
        assert _get_state("local/stale").stale_since == original

    def test_regression_stale_since_not_overwritten_on_consecutive_starts(self):
        """If mark_reindex_start is called twice without done, stale_since
        must keep the FIRST value — not update to a newer timestamp."""
        mark_reindex_start("local/stale")
        first = _get_state("local/stale").stale_since
        time.sleep(0.01)
        mark_reindex_start("local/stale")
        assert _get_state("local/stale").stale_since == first


# ── Deleted files on memory-cache fast path ─────────────────────────────────


class TestDeletedFilesFastPathRegression:

    def test_regression_deleted_file_with_old_hash(self, tmp_path):
        """Bug: when use_memory_hash_cache=True, existing_index was None.
        The deletion check 'if existing_index is not None and ...' always
        evaluated to False, silently dropping deleted files from the index."""
        from jcodemunch_mcp.tools.index_folder import index_folder

        test_file = tmp_path / "victim.py"
        test_file.write_text("def victim(): pass\n")
        storage = str(tmp_path / ".idx")

        # Index it
        r = index_folder(path=str(tmp_path), use_ai_summaries=False,
                         storage_path=storage, incremental=False)
        assert r["success"]

        # Delete and send WatcherChange with old_hash (memory cache path)
        abs_path = str(test_file.resolve())
        test_file.unlink()
        changes = [WatcherChange("deleted", abs_path, "fake_old_hash")]

        r = index_folder(path=str(tmp_path), use_ai_summaries=False,
                         storage_path=storage, incremental=True,
                         changed_paths=changes)
        assert r["success"]
        assert r.get("deleted", 0) >= 1, (
            f"Deleted file must be removed from index on memory-cache path, got {r}"
        )


# ── _build_hash_cache repo_id split ────────────────────────────────────────


class TestBuildHashCacheRegression:

    def test_regression_load_index_rejects_unsplit_repo_id(self, tmp_path):
        """Bug: _local_repo_id returns 'local/name-hash' but
        store.load_index(owner, name) rejects '/' in name.
        _build_hash_cache was crashing on EVERY call — the entire
        memory hash cache feature never worked."""
        from jcodemunch_mcp.storage.index_store import IndexStore
        from jcodemunch_mcp.watcher import _local_repo_id

        repo_id = _local_repo_id(str(tmp_path))
        store = IndexStore(base_path=str(tmp_path / ".idx"))

        # The OLD bug: passing full repo_id as name
        with pytest.raises(ValueError, match="Path separator"):
            store.load_index("local", repo_id)

    def test_regression_load_index_works_with_split(self, tmp_path):
        """The fix: split repo_id into (owner, store_name)."""
        from jcodemunch_mcp.storage.index_store import IndexStore
        from jcodemunch_mcp.watcher import _local_repo_id

        repo_id = _local_repo_id(str(tmp_path))
        owner, store_name = repo_id.split("/", 1)
        store = IndexStore(base_path=str(tmp_path / ".idx"))

        # Must not raise
        result = store.load_index(owner, store_name)
        assert result is None  # no index, but no crash


# ── _EXCLUDED_FROM_STRICT completeness ──────────────────────────────────────


class TestExcludedFromStrictRegression:

    def test_regression_index_file_excluded_from_strict(self):
        """Bug: index_file was missing from _EXCLUDED_FROM_STRICT.
        In strict mode, calling index_file would wait for a reindex
        to complete before executing — wrong for a write tool."""
        from jcodemunch_mcp.server import _EXCLUDED_FROM_STRICT

        assert "index_file" in _EXCLUDED_FROM_STRICT, (
            "index_file is a write tool — must be excluded from strict freshness wait"
        )

    def test_regression_all_write_tools_excluded(self):
        """All write/index tools must be excluded from strict wait."""
        from jcodemunch_mcp.server import _EXCLUDED_FROM_STRICT

        write_tools = {"index_repo", "index_folder", "index_file", "invalidate_cache"}
        for tool in write_tools:
            assert tool in _EXCLUDED_FROM_STRICT, f"{tool} must be in _EXCLUDED_FROM_STRICT"

    def test_regression_wait_for_fresh_excluded(self):
        """wait_for_fresh must not wait for itself."""
        from jcodemunch_mcp.server import _EXCLUDED_FROM_STRICT

        assert "wait_for_fresh" in _EXCLUDED_FROM_STRICT


# ── WatcherChange backward compatibility ────────────────────────────────────


class TestWatcherChangeRegression:

    def test_regression_tuple_index_access(self):
        """index_folder fast path uses wc[0], wc[1], wc[2] for backward compat.
        If WatcherChange stops being a tuple, the fast path breaks."""
        wc = WatcherChange("modified", "/tmp/f.py", "hash")
        assert wc[0] == "modified"
        assert wc[1] == "/tmp/f.py"
        assert wc[2] == "hash"

    def test_regression_isinstance_tuple(self):
        """isinstance(wc, tuple) is checked in index_folder.py."""
        wc = WatcherChange("added", "/tmp/f.py")
        assert isinstance(wc, tuple)

    def test_regression_default_old_hash_empty_string(self):
        """old_hash default must be '' (empty string), not None.
        The fast path checks 'if c.old_hash' — None would be falsy
        but could cause issues in string comparisons."""
        wc = WatcherChange("added", "/tmp/f.py")
        assert wc.old_hash == ""
        assert isinstance(wc.old_hash, str)
