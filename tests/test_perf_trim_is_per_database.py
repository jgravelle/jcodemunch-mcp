"""One telemetry database must not spend another's trim (#476).

`_perf_rows_since_trim` was a single int on the `_State` process singleton
while the trim it triggers runs on `conn` — the connection belonging to
whichever store made the 1000th write. With two stores alternating, every
store's writes advanced a counter toward a trim spent somewhere else, so one
`tool_calls` table was never trimmed and grew past `perf_telemetry_max_rows`.

⚠ Severity is low and stated as such in the report: telemetry is opt-in and
local-only, nothing is misrouted, and a single-store install cannot reach it.
The cost is disk.

⚠⚠ These tests drive `_persist_perf_locked` with an explicit `base_path` per
store and assert on the COUNTER MAP, not on row counts after 1000 writes.
Writing 2000 rows to two SQLite databases to observe one trim would be slow and
would couple the test to the trim interval; the defect is that the bookkeeping
is not per-database, and that is observable directly.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.storage import token_tracker


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    """A fresh _State with perf telemetry on and both stores under tmp_path."""
    monkeypatch.setattr(token_tracker._config, "get", lambda key, default=None, **kw: (
        True if key == "perf_telemetry_enabled" else default
    ))
    state = token_tracker._State()
    yield state
    state.close_perf_dbs()


def _store(tmp_path, name):
    p = tmp_path / name
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def test_two_stores_count_their_own_rows(tracker, tmp_path):
    a = _store(tmp_path, "store-a")
    b = _store(tmp_path, "store-b")

    with tracker._lock:
        for _ in range(3):
            tracker._persist_perf_locked("search_symbols", 1.0, True, None, base_path=a)
        for _ in range(2):
            tracker._persist_perf_locked("search_symbols", 1.0, True, None, base_path=b)

    counts = tracker._perf_rows_since_trim
    assert isinstance(counts, dict), (
        "the trim counter is process-wide; one store's writes advance the "
        "counter that triggers another store's trim (#476)"
    )
    # Two distinct databases, counted separately: 3 and 2, never a shared 5.
    assert sorted(counts.values()) == [2, 3], counts
    assert len(counts) == 2, counts


def test_the_counter_is_keyed_like_the_connection_cache(tracker, tmp_path):
    """The key must be the one the connection is cached under.

    A counter keyed on anything else — the raw `base_path` string, say — is the
    same defect in a new key, because two spellings of one directory would then
    each get their own budget toward a trim on one shared table. v1.108.280
    resolved that spelling problem for the connection cache; the counter has to
    inherit it rather than re-open it.
    """
    a = _store(tmp_path, "store-a")

    with tracker._lock:
        tracker._persist_perf_locked("search_symbols", 1.0, True, None, base_path=a)

    assert set(tracker._perf_rows_since_trim) == set(tracker._perf_conns), (
        "counter keys and connection-cache keys have diverged; they must name "
        "the same databases by the same resolved path"
    )


def test_a_relative_and_absolute_spelling_share_one_budget(tracker, tmp_path, monkeypatch):
    """Two spellings of ONE directory are one database, so one counter."""
    a = _store(tmp_path, "store-a")
    monkeypatch.chdir(tmp_path)

    with tracker._lock:
        tracker._persist_perf_locked("search_symbols", 1.0, True, None, base_path=a)
        tracker._persist_perf_locked("search_symbols", 1.0, True, None, base_path="store-a")

    assert len(tracker._perf_rows_since_trim) == 1, (
        f"one directory produced {len(tracker._perf_rows_since_trim)} counters: "
        f"{tracker._perf_rows_since_trim}"
    )
    assert list(tracker._perf_rows_since_trim.values()) == [2]


def test_close_drops_the_counters_with_the_connections(tracker, tmp_path):
    """Lifetimes match, so a key cannot outlive the store it names."""
    a = _store(tmp_path, "store-a")
    with tracker._lock:
        tracker._persist_perf_locked("search_symbols", 1.0, True, None, base_path=a)
    assert tracker._perf_rows_since_trim

    tracker.close_perf_dbs()
    assert tracker._perf_rows_since_trim == {}
    assert tracker._perf_conns == {}
