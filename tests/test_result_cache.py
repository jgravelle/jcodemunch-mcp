"""Tests for session-level LRU result cache (token_tracker + tool instrumentation)."""

import types
import pytest
from jcodemunch_mcp.storage.token_tracker import (
    result_cache_get,
    result_cache_put,
    result_cache_invalidate,
    result_cache_stats,
    _state,
    _RESULT_CACHE_MAXSIZE,
)

# The shared cache answers only for the index load it was filled from
# (LEDGER L-330); one stable stand-in object plays that index here.
_IDX = types.SimpleNamespace()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear():
    """Clear cache and hit/miss counters between tests."""
    result_cache_invalidate()
    with _state._lock:
        _state._cache_hits.clear()
        _state._cache_misses.clear()


# ---------------------------------------------------------------------------
# Basic get/put/miss
# ---------------------------------------------------------------------------

class TestResultCacheBasics:

    def setup_method(self):
        _clear()

    def test_miss_returns_none(self):
        assert result_cache_get("get_blast_radius", "owner/repo", ("sym", 1, 0, False, False), index=_IDX) is None

    def test_put_then_get(self):
        payload = {"symbol": "foo", "confirmed": [], "_meta": {"timing_ms": 42.0}}
        result_cache_put("get_blast_radius", "owner/repo", ("sym", 1, 0, False, False), payload, index=_IDX)
        got = result_cache_get("get_blast_radius", "owner/repo", ("sym", 1, 0, False, False), index=_IDX)
        # ⚠ `==`, not `is`. The cache hands back an isolated copy as of #572 —
        # identity was the defect written down as a contract, and the reasons
        # are in tests/test_result_cache_isolation.py.
        assert got == payload

    def test_different_tools_dont_collide(self):
        a = {"tool": "a"}
        b = {"tool": "b"}
        result_cache_put("tool_a", "r/r", ("k",), a, index=_IDX)
        result_cache_put("tool_b", "r/r", ("k",), b, index=_IDX)
        assert result_cache_get("tool_a", "r/r", ("k",), index=_IDX) == a
        assert result_cache_get("tool_b", "r/r", ("k",), index=_IDX) == b

    def test_different_repos_dont_collide(self):
        a = {"repo": "r1"}
        b = {"repo": "r2"}
        result_cache_put("get_blast_radius", "owner/r1", ("sym",), a, index=_IDX)
        result_cache_put("get_blast_radius", "owner/r2", ("sym",), b, index=_IDX)
        assert result_cache_get("get_blast_radius", "owner/r1", ("sym",), index=_IDX) == a
        assert result_cache_get("get_blast_radius", "owner/r2", ("sym",), index=_IDX) == b

    def test_different_specific_keys_dont_collide(self):
        a = {"depth": 1}
        b = {"depth": 2}
        result_cache_put("get_blast_radius", "o/r", ("sym", 1, 0, False, False), a, index=_IDX)
        result_cache_put("get_blast_radius", "o/r", ("sym", 2, 0, False, False), b, index=_IDX)
        assert result_cache_get("get_blast_radius", "o/r", ("sym", 1, 0, False, False), index=_IDX) == a
        assert result_cache_get("get_blast_radius", "o/r", ("sym", 2, 0, False, False), index=_IDX) == b


# ---------------------------------------------------------------------------
# Hit/miss counters and stats
# ---------------------------------------------------------------------------

class TestResultCacheStats:

    def setup_method(self):
        _clear()

    def test_miss_increments_misses(self):
        result_cache_get("get_blast_radius", "o/r", ("sym",), index=_IDX)
        stats = result_cache_stats()
        assert stats["total_misses"] == 1
        assert stats["total_hits"] == 0
        assert stats["hit_rate"] == 0.0

    def test_hit_increments_hits(self):
        result_cache_put("get_blast_radius", "o/r", ("sym",), {"x": 1}, index=_IDX)
        result_cache_get("get_blast_radius", "o/r", ("sym",), index=_IDX)
        stats = result_cache_stats()
        assert stats["total_hits"] == 1
        assert stats["total_misses"] == 0
        assert stats["hit_rate"] == 1.0

    def test_mixed_hit_rate(self):
        result_cache_put("get_blast_radius", "o/r", ("sym",), {"x": 1}, index=_IDX)
        result_cache_get("get_blast_radius", "o/r", ("sym",), index=_IDX)   # hit
        result_cache_get("get_blast_radius", "o/r", ("other",), index=_IDX) # miss
        stats = result_cache_stats()
        assert stats["total_hits"] == 1
        assert stats["total_misses"] == 1
        assert stats["hit_rate"] == 0.5

    def test_by_tool_breakdown(self):
        result_cache_put("find_references", "o/r", ("id",), {"refs": []}, index=_IDX)
        result_cache_get("find_references", "o/r", ("id",), index=_IDX)   # hit
        result_cache_get("find_references", "o/r", ("nope",), index=_IDX) # miss
        stats = result_cache_stats()
        assert "find_references" in stats["by_tool"]
        t = stats["by_tool"]["find_references"]
        assert t["hits"] == 1
        assert t["misses"] == 1
        assert t["hit_rate"] == 0.5

    def test_cached_entries_count(self):
        result_cache_put("tool", "o/r", ("a",), {}, index=_IDX)
        result_cache_put("tool", "o/r", ("b",), {}, index=_IDX)
        stats = result_cache_stats()
        assert stats["cached_entries"] == 2


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------

class TestResultCacheInvalidation:

    def setup_method(self):
        _clear()

    def test_invalidate_all(self):
        result_cache_put("get_blast_radius", "o/r1", ("s",), {"x": 1}, index=_IDX)
        result_cache_put("get_blast_radius", "o/r2", ("s",), {"x": 2}, index=_IDX)
        evicted = result_cache_invalidate()
        assert evicted == 2
        assert result_cache_get("get_blast_radius", "o/r1", ("s",), index=_IDX) is None
        assert result_cache_get("get_blast_radius", "o/r2", ("s",), index=_IDX) is None

    def test_invalidate_specific_repo(self):
        result_cache_put("get_blast_radius", "o/r1", ("s",), {"x": 1}, index=_IDX)
        result_cache_put("get_blast_radius", "o/r2", ("s",), {"x": 2}, index=_IDX)
        evicted = result_cache_invalidate("o/r1")
        assert evicted == 1
        assert result_cache_get("get_blast_radius", "o/r1", ("s",), index=_IDX) is None
        assert result_cache_get("get_blast_radius", "o/r2", ("s",), index=_IDX) is not None

    def test_invalidate_nonexistent_repo_returns_zero(self):
        result_cache_put("get_blast_radius", "o/r1", ("s",), {"x": 1}, index=_IDX)
        evicted = result_cache_invalidate("o/does_not_exist")
        assert evicted == 0
        assert result_cache_get("get_blast_radius", "o/r1", ("s",), index=_IDX) is not None

    def test_invalidate_empty_cache_returns_zero(self):
        assert result_cache_invalidate() == 0

    def test_multi_tool_repo_invalidation(self):
        result_cache_put("get_blast_radius", "o/r", ("s1",), {"a": 1}, index=_IDX)
        result_cache_put("find_references", "o/r", ("id",), {"b": 2}, index=_IDX)
        result_cache_put("get_blast_radius", "o/other", ("s1",), {"c": 3}, index=_IDX)
        evicted = result_cache_invalidate("o/r")
        assert evicted == 2
        assert result_cache_get("get_blast_radius", "o/r", ("s1",), index=_IDX) is None
        assert result_cache_get("find_references", "o/r", ("id",), index=_IDX) is None
        assert result_cache_get("get_blast_radius", "o/other", ("s1",), index=_IDX) is not None


# ---------------------------------------------------------------------------
# LRU eviction at maxsize
# ---------------------------------------------------------------------------

class TestResultCacheLRUEviction:

    def setup_method(self):
        _clear()

    def test_evicts_oldest_when_full(self):
        # Fill to capacity
        for i in range(_RESULT_CACHE_MAXSIZE):
            result_cache_put("tool", "o/r", (str(i),), {"i": i}, index=_IDX)
        # Oldest entry (key "0") should still be present
        assert result_cache_get("tool", "o/r", ("0",), index=_IDX) is not None
        # Now add one more to trigger eviction
        result_cache_put("tool", "o/r", ("overflow",), {"overflow": True}, index=_IDX)
        stats = result_cache_stats()
        assert stats["cached_entries"] == _RESULT_CACHE_MAXSIZE

    def test_lru_refresh_on_get_delays_eviction(self):
        # Fill to just under capacity
        for i in range(_RESULT_CACHE_MAXSIZE - 1):
            result_cache_put("tool", "o/r", (str(i),), {"i": i}, index=_IDX)
        # Access entry "0" to make it most recently used
        result_cache_get("tool", "o/r", ("0",), index=_IDX)
        # Add 2 more entries to push over capacity (entry "1" should be evicted first)
        result_cache_put("tool", "o/r", ("extra_a",), {}, index=_IDX)
        result_cache_put("tool", "o/r", ("extra_b",), {}, index=_IDX)
        # Entry "0" should survive (was recently accessed); entry "1" should be evicted
        assert result_cache_get("tool", "o/r", ("0",), index=_IDX) is not None
        assert result_cache_get("tool", "o/r", ("1",), index=_IDX) is None


# ---------------------------------------------------------------------------
# session_stats includes result_cache field
# ---------------------------------------------------------------------------

class TestSessionStatsIncludesCache:

    def setup_method(self):
        _clear()

    def test_result_cache_in_stats(self):
        result_cache_put("get_blast_radius", "o/r", ("s",), {"x": 1}, index=_IDX)
        result_cache_get("get_blast_radius", "o/r", ("s",), index=_IDX)   # hit
        result_cache_get("get_blast_radius", "o/r", ("miss",), index=_IDX) # miss

        from jcodemunch_mcp.storage.token_tracker import get_session_stats
        stats = get_session_stats()
        assert "result_cache" in stats
        rc = stats["result_cache"]
        assert rc["total_hits"] == 1
        assert rc["total_misses"] == 1
        assert rc["hit_rate"] == 0.5
        assert rc["cached_entries"] == 1
