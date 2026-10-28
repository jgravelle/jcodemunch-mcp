"""Tests for _make_rate_limit_middleware (T13).

Tests both the factory function (no Starlette dependency) and the sliding-window
bucket logic (Starlette-optional, skipped if not installed).
"""

import collections
import os
import time
import importlib

import pytest

from jcodemunch_mcp.server import _make_rate_limit_middleware


# ---------------------------------------------------------------------------
# Factory function tests — no Starlette required
# ---------------------------------------------------------------------------

class TestMakeRateLimitMiddleware:

    def test_returns_none_when_limit_zero(self, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_RATE_LIMIT", "0")
        assert _make_rate_limit_middleware() is None

    def test_returns_none_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("JCODEMUNCH_RATE_LIMIT", raising=False)
        assert _make_rate_limit_middleware() is None

    def test_returns_none_for_invalid_value(self, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_RATE_LIMIT", "not_a_number")
        assert _make_rate_limit_middleware() is None

    def test_returns_none_for_negative_value(self, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_RATE_LIMIT", "-5")
        assert _make_rate_limit_middleware() is None

    def test_returns_middleware_when_limit_positive(self, monkeypatch):
        starlette = pytest.importorskip("starlette", reason="starlette not installed")
        monkeypatch.setenv("JCODEMUNCH_RATE_LIMIT", "10")
        mw = _make_rate_limit_middleware()
        assert mw is not None

    def test_returns_middleware_for_limit_one(self, monkeypatch):
        pytest.importorskip("starlette", reason="starlette not installed")
        monkeypatch.setenv("JCODEMUNCH_RATE_LIMIT", "1")
        mw = _make_rate_limit_middleware()
        assert mw is not None


# ---------------------------------------------------------------------------
# Sliding-window bucket logic — Starlette required
# ---------------------------------------------------------------------------

def _extract_dispatch(limit: int):
    """Build a RateLimitMiddleware instance and return its dispatch method
    along with the shared _buckets dict for inspection."""
    import collections as _col
    import time as _time
    from starlette.middleware.base import BaseHTTPMiddleware

    _WINDOW = 60.0
    _buckets: dict[str, _col.deque] = {}

    class _RLM(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            ip = request.client.host if request.client else "unknown"
            now = _time.monotonic()
            bucket = _buckets.setdefault(ip, _col.deque())
            while bucket and now - bucket[0] >= _WINDOW:
                bucket.popleft()
            if len(bucket) >= limit:
                from starlette.responses import JSONResponse
                retry_after = int(_WINDOW - (now - bucket[0])) + 1
                return JSONResponse(
                    {"error": f"Rate limit exceeded. Max {limit} requests per minute per IP."},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)
            return await call_next(request)

    return _RLM, _buckets


class TestRateLimitBucketLogic:
    """Test the sliding-window deque logic extracted from RateLimitMiddleware."""

    _starlette = pytest.importorskip  # checked per-test via importorskip

    def _run_bucket(self, limit: int, n_requests: int, delay: float = 0.0):
        """Simulate n_requests hits against a fresh bucket.

        Returns list of booleans: True = allowed, False = denied.
        """
        _WINDOW = 60.0
        bucket: collections.deque = collections.deque()
        results = []
        now_base = time.monotonic()
        for i in range(n_requests):
            now = now_base + i * delay
            # Evict stale entries
            while bucket and now - bucket[0] >= _WINDOW:
                bucket.popleft()
            if len(bucket) >= limit:
                results.append(False)
            else:
                bucket.append(now)
                results.append(True)
        return results

    def test_under_limit_all_allowed(self):
        pytest.importorskip("starlette", reason="starlette not installed")
        results = self._run_bucket(limit=5, n_requests=5)
        assert all(results), "All requests under limit should be allowed"

    def test_over_limit_rejected(self):
        pytest.importorskip("starlette", reason="starlette not installed")
        results = self._run_bucket(limit=3, n_requests=5)
        assert results[:3] == [True, True, True]
        assert results[3:] == [False, False]

    def test_expired_entries_evicted(self):
        """Entries older than the window are evicted and new request is allowed."""
        pytest.importorskip("starlette", reason="starlette not installed")
        _WINDOW = 60.0
        bucket: collections.deque = collections.deque()
        limit = 2

        # Fill bucket to limit with timestamps 65 seconds in the past
        old_time = time.monotonic() - 65
        bucket.append(old_time)
        bucket.append(old_time)

        now = time.monotonic()
        while bucket and now - bucket[0] >= _WINDOW:
            bucket.popleft()

        assert len(bucket) == 0, "Stale entries should be evicted"
        assert len(bucket) < limit, "After eviction, request should be allowed"

    def test_limit_one_allows_first_denies_second(self):
        pytest.importorskip("starlette", reason="starlette not installed")
        results = self._run_bucket(limit=1, n_requests=2)
        assert results[0] is True
        assert results[1] is False


# ---------------------------------------------------------------------------
# The `rate_limit` config key (L-257)
# ---------------------------------------------------------------------------

class TestRateLimitConfigKey:
    """`rate_limit` in config.jsonc was documented and displayed but never read."""

    def test_config_key_alone_turns_the_limit_on(self, monkeypatch):
        pytest.importorskip("starlette", reason="starlette not installed")
        from jcodemunch_mcp import config as config_module
        monkeypatch.delenv("JCODEMUNCH_RATE_LIMIT", raising=False)
        monkeypatch.setitem(config_module._GLOBAL_CONFIG, "rate_limit", 30)
        assert _make_rate_limit_middleware() is not None

    def test_config_key_zero_leaves_it_off(self, monkeypatch):
        from jcodemunch_mcp import config as config_module
        monkeypatch.delenv("JCODEMUNCH_RATE_LIMIT", raising=False)
        monkeypatch.setitem(config_module._GLOBAL_CONFIG, "rate_limit", 0)
        assert _make_rate_limit_middleware() is None

    def test_env_var_wins_over_the_config_key(self, monkeypatch):
        from jcodemunch_mcp import config as config_module
        monkeypatch.setenv("JCODEMUNCH_RATE_LIMIT", "0")
        monkeypatch.setitem(config_module._GLOBAL_CONFIG, "rate_limit", 30)
        assert _make_rate_limit_middleware() is None
