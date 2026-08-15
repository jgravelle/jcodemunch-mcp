"""The perf-db connection cache must key on the resolved path.

``_ensure_perf_db_locked`` documents its cache as "Keyed by resolved path", but
``_perf_db_path`` returned ``root / _PERF_DB_FILE`` unresolved, so the key was
whatever spelling the caller happened to use. Three consequences, one per test:

* Aliases of one directory each got their own process-lifetime connection, and
  ``_perf_conns`` has no cap and no eviction.
* A RELATIVE spelling made the key depend on the process CWD. After a chdir the
  same key names a different database, and ``_perf_conn_usable``'s ``exists()``
  probe consults the new location while the cached connection still points at
  the old file -- so a row recorded for one store is written into another's.
  That is the failure v1.108.188 fixed for the writers; an unresolved cache key
  reintroduces it one layer down.
* The latency sink takes it too, by a shorter route: ``call_tool`` hands the raw
  ``storage_path`` argument to ``record_latency`` with no ``IndexStore`` in
  between. ``analyze_perf`` reads ``tool_calls`` and ``ranking_events`` through
  one base path, so a split between them is worse than either alone.
"""

import os
import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import token_tracker


@pytest.fixture
def perf_enabled(monkeypatch):
    real_get = token_tracker._config.get

    def fake_get(key, default=None, **kwargs):
        if key == "perf_telemetry_enabled":
            return True
        return real_get(key, default, **kwargs)

    monkeypatch.setattr(token_tracker._config, "get", fake_get)


def _rows(db: Path, table: str) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _ranking_rows(db: Path) -> int:
    return _rows(db, "ranking_events")


def test_aliases_of_one_store_share_one_cached_connection(perf_enabled, tmp_path):
    store = tmp_path / "store"
    (store / "nested").mkdir(parents=True)

    state = token_tracker._State()
    # The no-argument exit is the other half of the fix and the writes below never
    # reach it: they all pass an explicit base_path. `self._base_path` keeps whatever
    # spelling the first `_ensure_loaded` was handed, so it needs its own assertion or
    # reverting that one `.resolve()` leaves this file green.
    state._base_path = str(store / "nested" / "..")
    assert state._perf_db_path() == (store / "telemetry.db").resolve()

    try:
        for i, spelling in enumerate((str(store), str(store / "nested" / ".."))):
            state.record_ranking_event(
                tool="t", repo="local/demo", query=f"q-{i}",
                returned_ids=["a"], base_path=spelling,
            )
        assert len(state._perf_conns) == 1, (
            f"one directory, {len(state._perf_conns)} cached connections: "
            f"{sorted(state._perf_conns)}"
        )
        assert _ranking_rows(store / "telemetry.db") == 2
    finally:
        state.close_perf_dbs()


def test_relative_spelling_does_not_write_into_another_store(perf_enabled, tmp_path):
    project_a, project_b = tmp_path / "project_a", tmp_path / "project_b"
    (project_a / "store").mkdir(parents=True)
    (project_b / "store").mkdir(parents=True)

    state = token_tracker._State()
    cwd = os.getcwd()
    try:
        for project, query in (
            (project_a, "q-a1"), (project_b, "q-b1"), (project_a, "q-a2"),
        ):
            os.chdir(project)
            state.record_ranking_event(
                tool="t", repo="local/demo", query=query,
                returned_ids=["a"], base_path="store",
            )
    finally:
        os.chdir(cwd)
        state.close_perf_dbs()

    assert _ranking_rows(project_a / "store" / "telemetry.db") == 2
    assert _ranking_rows(project_b / "store" / "telemetry.db") == 1


def test_relative_spelling_does_not_misroute_latency_rows(perf_enabled, tmp_path):
    """Same defect on the latency sink, which reaches the cache more directly.

    ``call_tool`` passes the raw ``storage_path`` argument straight through to
    ``record_latency``, with no ``IndexStore`` normalising it on the way, so
    ``tool_calls`` takes the same misrouting as ``ranking_events``. Its own
    docstring is the reason this matters: ``analyze_perf`` reads BOTH tables
    through one base path.
    """
    project_a, project_b = tmp_path / "project_a", tmp_path / "project_b"
    (project_a / "store").mkdir(parents=True)
    (project_b / "store").mkdir(parents=True)

    state = token_tracker._State()
    cwd = os.getcwd()
    try:
        for project, tool in (
            (project_a, "search_symbols"), (project_b, "find_references"),
            (project_a, "get_file_outline"),
        ):
            os.chdir(project)
            state.record_latency(tool, 1.0, ok=True, repo="local/demo",
                                 base_path="store")
    finally:
        os.chdir(cwd)
        state.close_perf_dbs()

    assert _rows(project_a / "store" / "telemetry.db", "tool_calls") == 2
    assert _rows(project_b / "store" / "telemetry.db", "tool_calls") == 1
