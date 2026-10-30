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
    """The env var reaches the middleware through the config loader, as it
    does in `main()`, which calls `load_config` before starting a server."""

    @staticmethod
    def _env(tmp_path, monkeypatch, value):
        from jcodemunch_mcp import config as config_module
        if value is None:
            monkeypatch.delenv("JCODEMUNCH_RATE_LIMIT", raising=False)
        else:
            monkeypatch.setenv("JCODEMUNCH_RATE_LIMIT", value)
        config_module.load_config(storage_path=str(tmp_path), create_missing=False)

    def test_returns_none_when_limit_zero(self, tmp_path, monkeypatch):
        self._env(tmp_path, monkeypatch, "0")
        assert _make_rate_limit_middleware() is None

    def test_returns_none_when_env_unset(self, tmp_path, monkeypatch):
        self._env(tmp_path, monkeypatch, None)
        assert _make_rate_limit_middleware() is None

    def test_returns_none_for_invalid_value(self, tmp_path, monkeypatch):
        self._env(tmp_path, monkeypatch, "not_a_number")
        assert _make_rate_limit_middleware() is None

    def test_returns_none_for_negative_value(self, tmp_path, monkeypatch):
        self._env(tmp_path, monkeypatch, "-5")
        assert _make_rate_limit_middleware() is None

    def test_returns_middleware_when_limit_positive(self, tmp_path, monkeypatch):
        starlette = pytest.importorskip("starlette", reason="starlette not installed")
        self._env(tmp_path, monkeypatch, "10")
        mw = _make_rate_limit_middleware()
        assert mw is not None

    def test_returns_middleware_for_limit_one(self, tmp_path, monkeypatch):
        pytest.importorskip("starlette", reason="starlette not installed")
        self._env(tmp_path, monkeypatch, "1")
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
    """`rate_limit` in config.jsonc was documented and displayed but never read.

    Each case goes through the real loader on a config file in a scratch
    directory: the file wins, and JCODEMUNCH_RATE_LIMIT fills the key only when
    the file leaves it unset (CONFIGURATION.md). That is also the order
    `jcodemunch-mcp config` displays, so the shown limit is the enforced one.
    """

    @staticmethod
    def _load(tmp_path, monkeypatch, file_value=None, env_value=None):
        from jcodemunch_mcp import config as config_module
        body = "{}" if file_value is None else '{"rate_limit": %s}' % file_value
        (tmp_path / "config.jsonc").write_text(body, encoding="utf-8")
        if env_value is None:
            monkeypatch.delenv("JCODEMUNCH_RATE_LIMIT", raising=False)
        else:
            monkeypatch.setenv("JCODEMUNCH_RATE_LIMIT", env_value)
        config_module.load_config(storage_path=str(tmp_path), create_missing=False)

    def test_config_key_alone_turns_the_limit_on(self, tmp_path, monkeypatch):
        pytest.importorskip("starlette", reason="starlette not installed")
        self._load(tmp_path, monkeypatch, file_value=30)
        assert _make_rate_limit_middleware() is not None

    def test_config_key_zero_leaves_it_off(self, tmp_path, monkeypatch):
        self._load(tmp_path, monkeypatch, file_value=0)
        assert _make_rate_limit_middleware() is None

    def test_the_config_file_wins_over_the_env_var(self, tmp_path, monkeypatch):
        """`config` shows the file's value with source [config]; the server
        must enforce that same value, not the env var's 0."""
        pytest.importorskip("starlette", reason="starlette not installed")
        self._load(tmp_path, monkeypatch, file_value=30, env_value="0")
        assert _make_rate_limit_middleware() is not None

    @pytest.mark.parametrize("env_value", ["", "not_a_number"])
    def test_an_empty_or_garbage_env_var_does_not_turn_a_configured_limit_off(
        self, tmp_path, monkeypatch, env_value
    ):
        pytest.importorskip("starlette", reason="starlette not installed")
        self._load(tmp_path, monkeypatch, file_value=30, env_value=env_value)
        assert _make_rate_limit_middleware() is not None

    def test_the_env_var_fills_the_key_when_the_file_leaves_it_unset(self, tmp_path, monkeypatch):
        pytest.importorskip("starlette", reason="starlette not installed")
        self._load(tmp_path, monkeypatch, env_value="10")
        assert _make_rate_limit_middleware() is not None
