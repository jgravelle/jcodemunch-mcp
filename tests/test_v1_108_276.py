"""v1.108.276 — ranking-ledger write path: #441 true result count, #442 connection reuse.

Both from @rknighton's batch against the same write path .272 touched.

⚠ The two halves are tested separately even though they share one function, per
one-issue-one-verdict: #441 is about what a row RECORDS, #442 about what a write
COSTS, and either could be reverted without the other.
"""

import json
import os
import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp import config as _config
from jcodemunch_mcp.storage import token_tracker


@pytest.fixture
def telemetry_on():
    prev = _config._GLOBAL_CONFIG.get("perf_telemetry_enabled")
    _config._GLOBAL_CONFIG["perf_telemetry_enabled"] = True
    try:
        yield
    finally:
        if prev is None:
            _config._GLOBAL_CONFIG.pop("perf_telemetry_enabled", None)
        else:
            _config._GLOBAL_CONFIG["perf_telemetry_enabled"] = prev


@pytest.fixture
def tracker():
    # ⚠ NOT named `state`: that collides with the `_State` replay query and
    # outranks the real class (see jcm #458). Renamed to unblock; the ranking
    # behaviour is filed, not fixed.
    st = token_tracker._State()
    try:
        yield st
    finally:
        st.close_perf_dbs()


def _rows(store, columns="*"):
    conn = sqlite3.connect(str(store / "telemetry.db"))
    try:
        return conn.execute(f"SELECT {columns} FROM ranking_events").fetchall()
    finally:
        conn.close()


def _columns(store):
    conn = sqlite3.connect(str(store / "telemetry.db"))
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(ranking_events)")]
    finally:
        conn.close()


def _write(state, store, ids, **kw):
    state.record_ranking_event(
        tool=kw.get("tool", "search_symbols"),
        repo="local/demo",
        query=kw.get("query", "q"),
        returned_ids=ids,
        top1_score=3.0,
        top2_score=2.0,
        confidence=1.0,
        identity_hit=True,
        base_path=str(store),
    )


# ---------------------------------------------------------------------------
# #441 — the true result count
# ---------------------------------------------------------------------------


class TestReturnedCount:
    def test_column_exists(self, telemetry_on, tracker, tmp_path):
        _write(tracker, tmp_path, ["a"])
        assert "returned_count" in _columns(tmp_path)

    def test_truncated_row_records_the_true_size(self, telemetry_on, tracker, tmp_path):
        """The defect in one assertion: 60 results in, 50 stored, 60 recorded."""
        _write(tracker, tmp_path, [f"id{i}" for i in range(60)])
        (stored, count), = _rows(tmp_path, "returned_ids, returned_count")
        assert len(json.loads(stored)) == 50, "the id cap itself is unchanged"
        assert count == 60

    def test_a_complete_row_is_distinguishable_from_a_truncated_one(
        self, telemetry_on, tracker, tmp_path
    ):
        """⚠ THE point of #441. Both rows store 50 ids; only the count separates them.

        Without this column the two are byte-identical in every stored field, so
        an analysis could neither exclude the truncated row nor say what it lost.
        """
        _write(tracker, tmp_path, [f"a{i}" for i in range(50)], query="exactly-fifty")
        _write(tracker, tmp_path, [f"b{i}" for i in range(500)], query="five-hundred")

        rows = _rows(tmp_path, "query, returned_ids, returned_count")
        by_query = {q: (json.loads(ids), n) for q, ids, n in rows}

        assert len(by_query["exactly-fifty"][0]) == len(by_query["five-hundred"][0]) == 50
        assert by_query["exactly-fifty"][1] == 50
        assert by_query["five-hundred"][1] == 500
        assert by_query["exactly-fifty"][1] != by_query["five-hundred"][1]

    def test_untruncated_row_count_equals_stored_length(
        self, telemetry_on, tracker, tmp_path
    ):
        _write(tracker, tmp_path, ["a", "b", "c"])
        (stored, count), = _rows(tmp_path, "returned_ids, returned_count")
        assert count == len(json.loads(stored)) == 3

    def test_empty_result_set_records_zero_not_null(
        self, telemetry_on, tracker, tmp_path
    ):
        """0 is a measurement; NULL means "not recorded". They must not collide,
        because NULL is what pre-fix rows carry."""
        _write(tracker, tmp_path, [])
        (count,), = _rows(tmp_path, "returned_count")
        assert count == 0
        assert count is not None

    def test_a_generator_is_not_consumed_by_counting_it(
        self, telemetry_on, tracker, tmp_path
    ):
        """⚠ returned_ids is typed as an iterable. Counting before serialising
        would exhaust a generator and store [], which regret and ledger_trust
        both read as a genuine "returned nothing"."""
        _write(tracker, tmp_path, (f"g{i}" for i in range(5)))
        (stored, count), = _rows(tmp_path, "returned_ids, returned_count")
        assert json.loads(stored) == ["g0", "g1", "g2", "g3", "g4"]
        assert count == 5

    def test_preexisting_rows_keep_null_and_are_not_backfilled(
        self, telemetry_on, tracker, tmp_path
    ):
        """⚠⚠ A pre-fix row must read UNKNOWN, never "count == len(returned_ids)".

        Inferring the count from the stored length is exactly the mistake #441
        was filed about (the reporter made it himself in Discussion #430 and
        caught it on re-verification). The migration must not manufacture it.
        Same treatment ledger_trust gives its unseparable history.
        """
        db = tmp_path / "telemetry.db"
        conn = sqlite3.connect(str(db), isolation_level=None)
        try:
            conn.execute(
                "CREATE TABLE ranking_events ("
                " ts REAL NOT NULL, repo TEXT, tool TEXT NOT NULL,"
                " query_hash TEXT NOT NULL, query TEXT NOT NULL,"
                " returned_ids TEXT NOT NULL, top1_score REAL, top2_score REAL,"
                " confidence REAL, semantic_used INTEGER NOT NULL,"
                " identity_hit INTEGER NOT NULL, repo_is_stale INTEGER NOT NULL)"
            )
            conn.execute(
                "INSERT INTO ranking_events VALUES (0,'local/demo','old','h',"
                "'legacy',?,3.0,2.0,1.0,0,1,0)",
                (json.dumps([f"x{i}" for i in range(50)]),),
            )
        finally:
            conn.close()

        _write(tracker, tmp_path, ["new"], query="post-fix")

        rows = _rows(tmp_path, "query, returned_ids, returned_count")
        legacy = [r for r in rows if r[0] == "legacy"][0]
        assert legacy[2] is None, "a pre-fix row must stay UNKNOWN"
        assert len(json.loads(legacy[1])) == 50, "and it keeps its 50 stored ids"
        fresh = [r for r in rows if r[0] == "post-fix"][0]
        assert fresh[2] == 1

    def test_migration_is_idempotent(self, telemetry_on, tracker, tmp_path):
        for i in range(3):
            _write(tracker, tmp_path, ["a"], query=f"q{i}")
            tracker.close_perf_dbs()  # force reopen, re-running the migration path
        assert _columns(tmp_path).count("returned_count") == 1
        assert len(_rows(tmp_path)) == 3


# ---------------------------------------------------------------------------
# #442 — connection reuse
# ---------------------------------------------------------------------------


class TestConnectionReuse:
    def test_second_event_opens_no_new_connection(
        self, telemetry_on, tracker, tmp_path, monkeypatch
    ):
        """The measurable claim, asserted as a COUNT rather than as a duration.

        A timing assertion would be flaky on CI; the number of opens is exact and
        is what the fix actually changes.
        """
        opens = []
        real_connect = token_tracker.sqlite3.connect

        def counting(*a, **kw):
            opens.append(a[0] if a else kw.get("database"))
            return real_connect(*a, **kw)

        monkeypatch.setattr(token_tracker.sqlite3, "connect", counting)

        for i in range(10):
            _write(tracker, tmp_path, ["a"], query=f"q{i}")

        assert len(opens) == 1, f"expected one open for ten events, got {len(opens)}"
        assert len(_rows(tmp_path)) == 10

    def test_no_ddl_replay_after_the_first_event(
        self, telemetry_on, tracker, tmp_path, monkeypatch
    ):
        ddl = []
        real_connect = token_tracker.sqlite3.connect

        def tracing(*a, **kw):
            conn = real_connect(*a, **kw)
            conn.set_trace_callback(
                lambda sql: ddl.append(sql)
                if sql.lstrip().upper().startswith(("CREATE TABLE", "CREATE INDEX", "ALTER TABLE"))
                else None
            )
            return conn

        monkeypatch.setattr(token_tracker.sqlite3, "connect", tracing)

        _write(tracker, tmp_path, ["a"], query="first")
        after_first = len(ddl)
        for i in range(5):
            _write(tracker, tmp_path, ["a"], query=f"more{i}")

        assert after_first > 0, "the first event must still create the schema"
        assert len(ddl) == after_first, "later events must replay no DDL"

    def test_distinct_base_paths_get_distinct_connections(
        self, telemetry_on, tracker, tmp_path
    ):
        """⚠ base_path selects a different database per call, so the cache is
        keyed by resolved path. A single cached connection would send one store's
        rows to another -- the exact defect .188 fixed."""
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        _write(tracker, a, ["a1"], query="to-a")
        _write(tracker, b, ["b1"], query="to-b")

        assert [r[0] for r in _rows(a, "query")] == ["to-a"]
        assert [r[0] for r in _rows(b, "query")] == ["to-b"]
        assert len(tracker._perf_conns) == 2

    def test_a_closed_cached_connection_does_not_poison_the_cache(
        self, telemetry_on, tracker, tmp_path
    ):
        """⚠⚠ Found by the benchmark, not by design. Before validation, closing a
        cached connection left a dead handle that every later caller received --
        so one stray close() disabled telemetry for the process while every write
        still reported success."""
        _write(tracker, tmp_path, ["a"], query="first")
        for conn in tracker._perf_conns.values():
            conn.close()  # simulate a stray close, e.g. an old-contract caller

        _write(tracker, tmp_path, ["b"], query="after-close")
        assert [r[0] for r in _rows(tmp_path, "query")] == ["first", "after-close"]

    def test_missing_file_makes_a_cached_connection_unusable(
        self, telemetry_on, tracker, tmp_path
    ):
        """The orphan guard as a unit, portable to every platform.

        ⚠ A liveness probe alone cannot catch this: the connection is perfectly
        healthy, it is the FILE that is gone. Only the exists() half returns
        False here, so this test fails if that half is removed as redundant.
        """
        _write(tracker, tmp_path, ["a"])
        db = tmp_path / "telemetry.db"
        (conn,) = list(tracker._perf_conns.values())

        assert tracker._perf_conn_usable(conn, db) is True
        assert tracker._perf_conn_usable(conn, tmp_path / "gone.db") is False

    @pytest.mark.skipif(
        os.name == "nt",
        reason=(
            "Windows refuses to unlink a file with an open handle, so the "
            "orphaned-inode case cannot arise there at all. The guard is "
            "load-bearing on POSIX only; see the unit test above for the "
            "portable half."
        ),
    )
    def test_a_deleted_database_is_recreated_rather_than_written_into_the_void(
        self, telemetry_on, tracker, tmp_path
    ):
        """⚠⚠ The regression caching would otherwise introduce. On POSIX, SQLite
        keeps writing happily to an unlinked inode, so rows land nowhere and
        nothing raises. Pre-caching, the next event simply recreated the file."""
        _write(tracker, tmp_path, ["a"], query="before-delete")
        assert (tmp_path / "telemetry.db").exists()

        for leftover in sorted(tmp_path.glob("telemetry.db*")):
            leftover.unlink()

        _write(tracker, tmp_path, ["c"], query="after-orphan")
        assert (tmp_path / "telemetry.db").exists(), "the file must come back"
        assert [r[0] for r in _rows(tmp_path, "query")] == ["after-orphan"]

    def test_close_perf_dbs_reports_and_clears(self, telemetry_on, tracker, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        _write(tracker, a, ["x"])
        _write(tracker, b, ["y"])
        assert tracker.close_perf_dbs() == 2
        assert tracker._perf_conns == {}
        assert tracker.close_perf_dbs() == 0, "closing twice must be safe"

    def test_writes_still_work_from_another_thread(
        self, telemetry_on, tracker, tmp_path
    ):
        """⚠ check_same_thread=False is required because searches dispatch via
        asyncio.to_thread, so a cached connection outlives its opening thread.
        It is safe only because every caller holds _State._lock."""
        import threading

        _write(tracker, tmp_path, ["main"], query="from-main")
        errors = []

        def worker():
            try:
                _write(tracker, tmp_path, ["other"], query="from-thread")
            except BaseException as exc:  # noqa: BLE001 - recorded, re-raised below
                errors.append(exc)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert not errors, f"cross-thread write raised: {errors}"
        assert sorted(r[0] for r in _rows(tmp_path, "query")) == [
            "from-main",
            "from-thread",
        ]

    def test_telemetry_disabled_writes_nothing_and_opens_nothing(
        self, tracker, tmp_path
    ):
        """Scope control: the whole change is unreachable on a default install."""
        _config._GLOBAL_CONFIG["perf_telemetry_enabled"] = False
        _write(tracker, tmp_path, ["a"])
        assert not (tmp_path / "telemetry.db").exists()
        assert tracker._perf_conns == {}


class TestPublicSurface:
    def test_module_level_close_is_exported_and_safe_when_nothing_is_open(self):
        assert token_tracker.close_perf_dbs() >= 0

    def test_close_is_registered_at_exit_before_flush(self):
        """⚠ atexit is LIFO, so the closer must be registered FIRST to run LAST --
        registered second, it would shut the database before the final flush,
        whose _persist_session_yield_locked writes to it.

        The atexit registry is not portably introspectable, so this pins the
        ordering at the source level. Weaker than executing it, and it is what
        keeps a later reorder from silently dropping the last session's row.
        """
        src = Path(token_tracker.__file__).read_text(encoding="utf-8")
        close_at = src.index("atexit.register(_state.close_perf_dbs)")
        flush_at = src.index("atexit.register(_state.flush)")
        assert close_at < flush_at, (
            "close_perf_dbs must be registered BEFORE flush; atexit runs LIFO, so "
            "this ordering makes flush run first and the close last"
        )
