"""Tests for reindex_state module."""

import threading
import time

import pytest

from jcodemunch_mcp.reindex_state import (
    _get_state, _freshness_mode, _repo_states, _repo_events,
    mark_reindex_start, mark_reindex_done, mark_reindex_failed,
    get_reindex_status, is_any_reindex_in_progress,
    set_freshness_mode, get_freshness_mode, await_freshness_if_strict,
    wait_for_fresh_result,
)


@pytest.fixture(autouse=True)
def reset_state():
    """Reset module-level state before each test."""
    _repo_states.clear()
    _repo_events.clear()
    _freshness_mode.clear()
    yield
    _repo_states.clear()
    _repo_events.clear()
    _freshness_mode.clear()


class TestRepoStateCreation:
    def test_get_state_creates_new_state(self):
        state = _get_state("test/repo")
        assert state is not None
        assert state.reindexing is False
        assert state.stale_since is None
        assert state.consecutive_failures == 0
        assert state.deferred_generation == 0
        assert "test/repo" in _repo_events
        assert _repo_events["test/repo"].is_set()

    def test_get_state_returns_same_instance(self):
        state1 = _get_state("test/repo")
        state2 = _get_state("test/repo")
        assert state1 is state2

    def test_get_state_different_repos_are_independent(self):
        state1 = _get_state("repo/a")
        state2 = _get_state("repo/b")
        assert state1 is not state2


class TestMarkReindexStart:
    def test_sets_reindex_in_progress(self):
        mark_reindex_start("test/repo")
        status = get_reindex_status("test/repo")
        assert status["reindex_in_progress"] is True
        assert status["index_stale"] is True

    def test_clears_event(self):
        mark_reindex_start("test/repo")
        assert not _repo_events["test/repo"].is_set()

    def test_sets_stale_since(self):
        mark_reindex_start("test/repo")
        status = get_reindex_status("test/repo")
        assert status["stale_since_ms"] is not None
        assert status["stale_since_ms"] >= 0

    def test_increments_deferred_generation(self):
        state = _get_state("test/repo")
        gen_before = state.deferred_generation
        mark_reindex_start("test/repo")
        assert state.deferred_generation == gen_before + 1

    def test_stale_since_not_overwritten_on_consecutive_starts(self):
        """If mark_reindex_start is called again without done, stale_since stays at the first value."""
        mark_reindex_start("test/repo")
        first_stale = _repo_states["test/repo"].stale_since
        time.sleep(0.01)
        mark_reindex_start("test/repo")  # second start without done
        second_stale = _repo_states["test/repo"].stale_since
        assert second_stale == first_stale  # must not be updated


class TestMarkReindexDone:
    def test_clears_in_progress_and_stale(self):
        mark_reindex_start("test/repo")
        mark_reindex_done("test/repo")
        status = get_reindex_status("test/repo")
        assert status["reindex_in_progress"] is False
        assert status["index_stale"] is False
        assert status["stale_since_ms"] is None

    def test_sets_event(self):
        mark_reindex_start("test/repo")
        mark_reindex_done("test/repo")
        assert _repo_events["test/repo"].is_set()

    def test_resets_consecutive_failures(self):
        mark_reindex_start("test/repo")
        mark_reindex_failed("test/repo", "error 1")
        mark_reindex_start("test/repo")
        mark_reindex_failed("test/repo", "error 2")
        mark_reindex_start("test/repo")
        mark_reindex_done("test/repo")
        assert _repo_states["test/repo"].consecutive_failures == 0

    def test_clears_error_on_success(self):
        mark_reindex_start("test/repo")
        mark_reindex_failed("test/repo", "some error")
        mark_reindex_start("test/repo")
        mark_reindex_done("test/repo")
        status = get_reindex_status("test/repo")
        assert "reindex_error" not in status


class TestMarkReindexFailed:
    def test_first_failure_no_error_in_status(self):
        """First failure: index_stale=True but no error details exposed."""
        mark_reindex_start("test/repo")
        mark_reindex_failed("test/repo", "parse error")
        status = get_reindex_status("test/repo")
        assert status["reindex_in_progress"] is False
        assert status["index_stale"] is True
        assert "reindex_error" not in status

    def test_second_failure_exposes_error(self):
        """2nd+ consecutive failure: reindex_error and reindex_failures exposed."""
        mark_reindex_start("test/repo")
        mark_reindex_failed("test/repo", "error 1")
        mark_reindex_start("test/repo")
        mark_reindex_failed("test/repo", "error 2")
        status = get_reindex_status("test/repo")
        assert status["index_stale"] is True
        assert status["reindex_error"] == "error 2"
        assert status["reindex_failures"] == 2

    def test_sets_event_so_waiters_unblock(self):
        """Failed reindex must set the event so wait_for_fresh_result unblocks."""
        mark_reindex_start("test/repo")
        mark_reindex_failed("test/repo", "disk full")
        assert _repo_events["test/repo"].is_set()

    def test_stale_since_preserved_on_failure(self):
        """stale_since should NOT be cleared after a failure."""
        mark_reindex_start("test/repo")
        mark_reindex_failed("test/repo", "disk full")
        status = get_reindex_status("test/repo")
        assert status["stale_since_ms"] is not None


class TestGetReindexStatus:
    def test_retry_entries_of_two_watch_tasks_on_one_repo_are_separate(self, monkeypatch):
        from types import SimpleNamespace
        from jcodemunch_mcp import reindex_state
        from jcodemunch_mcp.reindex_state import set_reindex_retry

        monkeypatch.setattr(reindex_state, "time", SimpleNamespace(monotonic=lambda: 100.0))
        mark_reindex_start("test/repo")
        mark_reindex_failed("test/repo", "writer busy")
        set_reindex_retry("test/repo", 140.0, 2, owner="/repo/a")
        set_reindex_retry("test/repo", 110.0, 1, owner="/repo/b")
        status = get_reindex_status("test/repo")
        assert status["retry_in_ms"] == 10000
        assert status["retry_attempts"] == 3

        set_reindex_retry("test/repo", None, owner="/repo/b")
        mark_reindex_done("test/repo")
        status = get_reindex_status("test/repo")
        assert status["retry_in_ms"] == 40000
        assert status["retry_attempts"] == 2
        assert status["index_stale"] is True
        assert status["last_reindex_error"] == "writer busy"

        set_reindex_retry("test/repo", None, owner="/repo/a")
        mark_reindex_done("test/repo")
        assert get_reindex_status("test/repo") == {
            "index_stale": False, "reindex_in_progress": False, "stale_since_ms": None,
        }

    def test_pending_work_age_and_last_error_survive_a_retry_but_not_success(self, monkeypatch):
        from types import SimpleNamespace
        from jcodemunch_mcp import reindex_state

        now = 10.0
        monkeypatch.setattr(reindex_state, "time", SimpleNamespace(monotonic=lambda: now))
        mark_reindex_start("test/repo")
        now = 12.0
        mark_reindex_failed("test/repo", "writer busy")
        status = get_reindex_status("test/repo")
        assert status["last_reindex_error"] == "writer busy"
        assert status["stale_since_ms"] == 2000

        now = 20.0
        mark_reindex_start("test/repo")
        now = 70.0
        status = get_reindex_status("test/repo")
        assert status["reindex_in_progress"] is True
        assert status["last_reindex_error"] == "writer busy"
        assert status["stale_since_ms"] == 60000
        assert status["reindex_elapsed_ms"] == 50000
        assert "retry_in_ms" not in status

        mark_reindex_done("test/repo")
        now += 365 * 24 * 60 * 60
        status = get_reindex_status("test/repo")
        assert status == {
            "index_stale": False, "reindex_in_progress": False, "stale_since_ms": None,
        }

    def test_idle_state(self):
        status = get_reindex_status("test/repo")
        assert status["reindex_in_progress"] is False
        assert status["index_stale"] is False
        assert status["stale_since_ms"] is None

    def test_in_progress_state(self):
        mark_reindex_start("test/repo")
        status = get_reindex_status("test/repo")
        assert status["reindex_in_progress"] is True
        assert status["index_stale"] is True

    def test_is_any_reindex_in_progress(self):
        assert is_any_reindex_in_progress() is False
        mark_reindex_start("repo/a")
        mark_reindex_start("repo/b")
        assert is_any_reindex_in_progress() is True
        mark_reindex_done("repo/a")
        mark_reindex_done("repo/b")
        assert is_any_reindex_in_progress() is False


class TestFreshnessMode:
    @pytest.fixture(autouse=True)
    def _reset_freshness_mode(self):
        """Reset freshness mode after each test."""
        set_freshness_mode("relaxed")
        yield
        set_freshness_mode("relaxed")

    def test_default_freshness_is_relaxed(self):
        assert get_freshness_mode() == "relaxed"

    def test_set_freshness_mode_strict(self):
        set_freshness_mode("strict")
        assert get_freshness_mode() == "strict"

    def test_set_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid freshness mode"):
            set_freshness_mode("lazy")

    def test_await_relaxed_returns_immediately(self):
        mark_reindex_start("test/repo")
        result = await_freshness_if_strict("test/repo", timeout_ms=50)
        assert result is True

    def test_await_strict_blocks_until_done(self):
        set_freshness_mode("strict")
        mark_reindex_start("test/repo")

        def complete_after_delay():
            time.sleep(0.05)
            mark_reindex_done("test/repo")

        t = threading.Thread(target=complete_after_delay, daemon=True)
        t.start()
        t0 = time.monotonic()
        await_freshness_if_strict("test/repo", timeout_ms=500)
        elapsed = time.monotonic() - t0
        t.join()
        assert elapsed >= 0.03

    def test_await_strict_respects_custom_timeout(self):
        """A short custom timeout should expire before reindex completes."""
        set_freshness_mode("strict")
        mark_reindex_start("test/repo2")
        t0 = time.monotonic()
        # Reindex never completes; timeout_ms=50 should expire quickly
        await_freshness_if_strict("test/repo2", timeout_ms=50)
        elapsed = time.monotonic() - t0
        # Should have returned after ~50ms, not blocked indefinitely
        assert elapsed < 0.5
        # Cleanup: unblock the event so other tests aren't affected
        mark_reindex_done("test/repo2")


class TestWaitForFreshResult:
    def test_returns_fresh_when_unknown_repo(self):
        """Unknown repo returns fresh immediately (no stale data exists)."""
        result = wait_for_fresh_result("never/seen", timeout_ms=100)
        assert result["fresh"] is True
        assert result["waited_ms"] == 0

    def test_returns_fresh_after_done(self):
        mark_reindex_done("test/repo", {"symbol_count": 100})
        result = wait_for_fresh_result("test/repo", timeout_ms=100)
        assert result["fresh"] is True
        assert "waited_ms" in result

    def test_returns_fresh_after_waiting(self):
        mark_reindex_start("test/repo")

        def complete_after():
            time.sleep(0.05)
            mark_reindex_done("test/repo")

        threading.Thread(target=complete_after, daemon=True).start()
        result = wait_for_fresh_result("test/repo", timeout_ms=500)
        assert result["fresh"] is True
        assert result["waited_ms"] >= 30

    def test_returns_timeout_when_reindex_never_finishes(self):
        mark_reindex_start("test/repo")
        result = wait_for_fresh_result("test/repo", timeout_ms=50)
        assert result["fresh"] is False
        assert result["reason"] == "timeout"
        assert result["waited_ms"] >= 40

    def test_first_failure_returns_fresh(self):
        """First failure: waiters still get 'fresh' (transient tolerance)."""
        mark_reindex_start("test/repo")
        mark_reindex_failed("test/repo", "disk full")
        result = wait_for_fresh_result("test/repo", timeout_ms=100)
        assert result["fresh"] is True

    def test_persistent_failure_returns_error(self):
        """2nd+ consecutive failure: returns reindex_failed reason."""
        mark_reindex_start("test/repo")
        mark_reindex_failed("test/repo", "error 1")
        mark_reindex_start("test/repo")
        mark_reindex_failed("test/repo", "error 2")
        result = wait_for_fresh_result("test/repo", timeout_ms=100)
        assert result["fresh"] is False
        assert result["reason"] == "reindex_failed"
        assert result["reindex_error"] == "error 2"
        assert result["reindex_failures"] == 2
