"""Idle index-cache TTL + process presence registry (jcm#375 follow-up).

A user found 25+ live jcodemunch instances on one box against a single ~140MB
index store, ages to 1d15h, mostly stdio servers their client never reaped at
session end. Reaping the day-plus-old ones freed ~17 GB. The same underlying
fact showed up in #370 as a single worker at ~16 GiB: a hydrated CodeIndex is
large and was released only under LRU pressure, never on idle.

Two changes, plus one bug the second uncovered:

1. An OPT-IN idle TTL on the index cache. It must stay off by default: cold
   hydration of that 665k-symbol index was measured at 7.5 to 11.4 minutes, so
   evicting during a quiet spell would hand the next query that bill.
2. A presence registry so the sprawl is visible at all.
3. `_is_pid_alive` reported dead Windows processes as alive whenever any handle
   to them remained open, which is the normal state for a spawned child.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import process_registry as registry
from jcodemunch_mcp.storage import sqlite_store as store
from jcodemunch_mcp.storage.process_locks import _is_pid_alive


class _FakeIndex:
    """Stand-in for a hydrated CodeIndex; identity is all these tests need."""


@pytest.fixture(autouse=True)
def _clean_cache_and_env(monkeypatch):
    monkeypatch.delenv("JCODEMUNCH_INDEX_CACHE_TTL", raising=False)
    store._index_cache.clear()
    yield
    store._index_cache.clear()


class _Clock:
    """A monotonic clock the test advances by hand.

    F-22 (docs/harness/FINDINGS.md): the TTL tests slept real time with a
    0.2 s margin, and one gap on a loaded windows runner ran past the TTL, so
    a hot entry read as idle. The cache reads `time.monotonic()` through its
    module's `time` name; this stands in for that name and forwards every
    other attribute to the real module. The tests now assert the MECHANISM
    (last_used is refreshed on a hit) with no wall-clock in the loop.
    """

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __getattr__(self, name):  # anything but monotonic is the real module
        return getattr(time, name)


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(store, "time", c)
    return c


# --- TTL ------------------------------------------------------------------

def test_ttl_is_disabled_by_default():
    """The load-bearing default. On would trade one user's bug for another's."""
    assert store._cache_ttl_seconds() == 0.0


@pytest.mark.parametrize("raw", ["", "abc", "-5", "0", "nan-ish"])
def test_ttl_garbage_and_non_positive_values_disable(monkeypatch, raw):
    monkeypatch.setenv("JCODEMUNCH_INDEX_CACHE_TTL", raw)
    assert store._cache_ttl_seconds() == 0.0


def test_ttl_parses_a_positive_value(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_INDEX_CACHE_TTL", "900")
    assert store._cache_ttl_seconds() == 900.0


def test_disabled_ttl_retains_an_idle_entry(monkeypatch, clock):
    """Today's behavior, which must be byte-identical for anyone not opting in."""
    monkeypatch.setenv("JCODEMUNCH_INDEX_CACHE_TTL", "0")
    store._cache_put("o", "n", 1, _FakeIndex())
    clock.advance(3600.0)
    assert store._cache_get("o", "n", 1) is not None


def test_enabled_ttl_evicts_an_idle_entry(monkeypatch, clock):
    monkeypatch.setenv("JCODEMUNCH_INDEX_CACHE_TTL", "10")
    store._cache_put("o", "n", 1, _FakeIndex())
    assert store._cache_get("o", "n", 1) is not None, "should be cached immediately"
    clock.advance(10.5)
    assert store._cache_get("o", "n", 1) is None, "should be evicted once idle"


def test_active_use_keeps_an_entry_alive(monkeypatch, clock):
    """A hot index must never be evicted by the TTL. Each hit refreshes it."""
    monkeypatch.setenv("JCODEMUNCH_INDEX_CACHE_TTL", "10")
    store._cache_put("o", "n", 1, _FakeIndex())
    for _ in range(6):
        clock.advance(9.0)
        assert store._cache_get("o", "n", 1) is not None
    # 54 s elapsed against a 10 s TTL; only the refresh on each hit saved it.
    clock.advance(10.5)
    assert store._cache_get("o", "n", 1) is None, "and it still evicts once idle"


def test_ttl_eviction_does_not_disturb_a_recently_used_sibling(monkeypatch, clock):
    monkeypatch.setenv("JCODEMUNCH_INDEX_CACHE_TTL", "10")
    store._cache_put("old", "n", 1, _FakeIndex())
    clock.advance(10.5)
    store._cache_put("new", "n", 1, _FakeIndex())
    assert store._cache_get("new", "n", 1) is not None
    assert store._cache_get("old", "n", 1) is None


# --- process liveness -----------------------------------------------------

def test_dead_child_is_not_alive_while_its_handle_is_held():
    """The bug the registry uncovered.

    On Windows a PID stays queryable via OpenProcess while ANY handle to it is
    open, and Popen holds exactly such a handle. So a killed process read as
    alive, which would make a crashed server's lock look permanently held and
    would count freed memory as still in use.
    """
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert _is_pid_alive(proc.pid) is True
        proc.kill()
        proc.wait()
        # Deliberately keep `proc` referenced so the OS handle stays open.
        assert _is_pid_alive(proc.pid) is False
    finally:
        if proc.poll() is None:  # pragma: no cover - defensive
            proc.kill()
            proc.wait()


def test_own_pid_is_alive():
    assert _is_pid_alive(os.getpid()) is True


# --- presence registry ----------------------------------------------------

def test_register_then_unregister_round_trip(tmp_path):
    path = registry.register("stdio", "9.9.9", str(tmp_path))
    assert path is not None and Path(path).exists()
    entries = registry.live_processes(str(tmp_path))
    assert [e.pid for e in entries] == [os.getpid()]
    assert entries[0].transport == "stdio"
    assert entries[0].version == "9.9.9"

    registry.unregister()
    assert registry.live_processes(str(tmp_path)) == []


def test_missing_registry_dir_is_not_an_error(tmp_path):
    assert registry.live_processes(str(tmp_path / "nope")) == []


def test_dead_entries_are_pruned(tmp_path):
    """A hard kill never runs unregister, which is why readers verify liveness."""
    stale = tmp_path / registry._DIR_NAME
    stale.mkdir(parents=True)
    (stale / "999999.json").write_text(
        '{"pid": 999999, "client_id": "ghost", "transport": "stdio",'
        ' "version": "0", "started_at": "2020-01-01T00:00:00+00:00"}',
        encoding="utf-8",
    )
    assert registry.live_processes(str(tmp_path)) == []
    assert not (stale / "999999.json").exists(), "dead entry should be pruned"


def test_corrupt_entry_is_pruned_not_raised(tmp_path):
    bad = tmp_path / registry._DIR_NAME
    bad.mkdir(parents=True)
    (bad / "1.json").write_text("{not json", encoding="utf-8")
    assert registry.live_processes(str(tmp_path)) == []
    assert not (bad / "1.json").exists()


def test_sprawl_report_excludes_self_from_others(tmp_path):
    registry.register("stdio", "9.9.9", str(tmp_path))
    try:
        report = registry.sprawl_report(str(tmp_path))
        assert report["live"] == 1
        assert report["others"] == 0, "this process is not sprawl"
        assert report["this_pid"] == os.getpid()
        assert "hint" not in report, "a single process must stay quiet"
    finally:
        registry.unregister()


def test_sprawl_hint_appears_only_past_the_threshold(tmp_path):
    """Fabricate live entries by reusing this PID, which is genuinely alive."""
    directory = tmp_path / registry._DIR_NAME
    directory.mkdir(parents=True)
    me = os.getpid()
    for i in range(registry._SPRAWL_HINT_THRESHOLD):
        # Distinct filenames, all pointing at a live PID.
        (directory / f"{me}_{i}.json").write_text(
            f'{{"pid": {me}, "client_id": "c{i}", "transport": "stdio",'
            f' "version": "0", "started_at": "2020-01-01T00:00:00+00:00"}}',
            encoding="utf-8",
        )
    report = registry.sprawl_report(str(tmp_path))
    assert report["live"] >= registry._SPRAWL_HINT_THRESHOLD
    assert "hint" in report
    assert "JCODEMUNCH_INDEX_CACHE_TTL" in report["hint"], (
        "the hint must name the lever, not just the count"
    )


def test_session_stats_carries_the_processes_block(tmp_path):
    from jcodemunch_mcp.tools.get_session_stats import get_session_stats

    registry.register("stdio", "9.9.9", str(tmp_path))
    try:
        result = get_session_stats(storage_path=str(tmp_path))
        assert "processes" in result
        assert result["processes"]["this_pid"] == os.getpid()
    finally:
        registry.unregister()


def test_session_stats_survives_a_broken_registry(monkeypatch, tmp_path):
    """Diagnostics must never be able to fail the call they ride on."""
    from jcodemunch_mcp.tools import get_session_stats as module

    def boom(*_a, **_k):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(
        "jcodemunch_mcp.storage.process_registry.sprawl_report", boom
    )
    result = module.get_session_stats(storage_path=str(tmp_path))
    # The real payload still arrives intact...
    assert "session_calls" in result
    assert "total_tokens_saved" in result
    assert "savings_provenance" in result
    # ...and the block that blew up is omitted rather than half-written.
    assert "processes" not in result
