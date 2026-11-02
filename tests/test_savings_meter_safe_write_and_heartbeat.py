"""The savings meter's write is safe, it stamps every measured batch, and a
stopped meter is labeled as stopped wherever its total is shown.

Reported by a long-time customer on 1.108.407: `_savings.json`'s daily entries
stopped at one date and the lifetime total froze, while `_tool_counters.json`
and `_session_live.json` kept updating, and the dashboard showed the frozen
figure as current. Two writer defects and one reader defect:

  - A batch that earned 0 wrote nothing, so a meter that measured 0 and a meter
    that measured nothing left the same file. Every flush that carried a call
    now writes `last_flush_at` and `measured_days[day] = {calls, saved}`.
  - The read-modify-write was unsafe: a failed read became `{}` (the next write
    wiped the lifetime history), the write truncated the file in place, there
    was no cross-process lock, and a failed write was logged at debug. A read
    that fails now REFUSES the write and keeps the batch; the write goes
    through a temp file and `os.replace` under the counters module's lock.
  - The readers presented the total with no word on whether it was still being
    measured. `lifetime_status` / `lifetime_measured_through` /
    `lifetime_stale_since` ride beside it now, and UNKNOWN is never `current`.

⚠ Every interleaving here is PINNED (a hook runs the second writer inside the
first one's read-to-write window), never a sleep and a hope.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import savings_meter as sm
from jcodemunch_mcp.storage import token_tracker as tt
from jcodemunch_mcp.storage import tool_counters


@pytest.fixture(autouse=True)
def _no_share(monkeypatch):
    # Nothing here may reach the community meter.
    monkeypatch.setattr(tt, "_share_savings", lambda *a, **k: None)


def _fresh() -> "tt._State":
    return tt._State()


def _file(root: Path) -> Path:
    return root / "_savings.json"


def _ledger(root: Path) -> dict:
    return json.loads(_file(root).read_text(encoding="utf-8"))


def _today() -> str:
    return datetime.now().date().isoformat()


# ---------------------------------------------------------------------------
# 1. The heartbeat: a 0 is a measurement
# ---------------------------------------------------------------------------


class TestHeartbeat:
    def test_calls_counted_before_load_go_to_the_root_they_were_counted_under(
        self, tmp_path, monkeypatch
    ):
        # Review round 2: the exit flush loaded the meter at whatever root was
        # in force AT EXIT, so a CODE_INDEX_PATH restored before atexit (as
        # conftest does) sent the calls to another store, the developer's real
        # one in a test run (Practice 8).
        counted = tmp_path / "counted"
        later = tmp_path / "later"
        monkeypatch.setenv("CODE_INDEX_PATH", str(counted))
        st = _fresh()
        for _ in range(3):
            st.note_call()
        assert not st._loaded, "the meter must still be unloaded for this to test the exit path"
        monkeypatch.setenv("CODE_INDEX_PATH", str(later))
        st.flush()
        assert not _file(later).exists(), "calls were written to the root in force at flush"
        assert _ledger(counted)[sm.MEASURED_DAYS][_today()]["calls"] == 3

    def test_a_zero_credit_batch_still_stamps_the_file(self, tmp_path):
        st = _fresh()
        for _ in range(tt._FLUSH_INTERVAL):
            st.add(0, str(tmp_path), "search_text")
        data = _ledger(tmp_path)
        stamp = sm.parse_stamp(data[sm.LAST_FLUSH_AT])
        assert stamp is not None, "last_flush_at must be an aware ISO timestamp"
        assert abs((datetime.now(timezone.utc) - stamp).total_seconds()) < 120
        assert data[sm.MEASURED_DAYS] == {
            _today(): {"calls": tt._FLUSH_INTERVAL, "saved": 0}
        }
        # Existing readers keep their meaning: nothing was earned, so `daily`
        # gains no entry and the total stays 0.
        assert "daily" not in data
        assert data["total_tokens_saved"] == 0

    def test_measured_days_accumulate_calls_and_saved(self, tmp_path):
        st = _fresh()
        for _ in range(3):
            st.add(0, str(tmp_path), "search_text")
        for _ in range(3):
            st.add(50, str(tmp_path), "search_symbols")
        data = _ledger(tmp_path)
        assert data[sm.MEASURED_DAYS][_today()] == {"calls": 6, "saved": 150}
        assert data["daily"][_today()] == 150
        assert data["total_tokens_saved"] == 150

    def test_measured_days_are_capped_like_daily(self, tmp_path):
        old = {
            f"2020-01-01+{i}": {"calls": 1, "saved": 0}
            for i in range(tt._DAILY_MAX_DAYS + 20)
        }
        _file(tmp_path).write_text(
            json.dumps({"total_tokens_saved": 5, sm.MEASURED_DAYS: old}),
            encoding="utf-8",
        )
        st = _fresh()
        for _ in range(3):
            st.add(0, str(tmp_path), "search_text")
        days = _ledger(tmp_path)[sm.MEASURED_DAYS]
        assert len(days) == tt._DAILY_MAX_DAYS
        assert _today() in days

    def test_a_dispatched_call_is_counted_once_by_the_dispatcher(self, tmp_path):
        """Inside a dispatched call the tool's `record_savings` does not count the
        call; the dispatcher's `note_call` does, so a call that never reaches the
        meter (most tools) still proves the meter is alive, and one that does is
        not counted twice."""
        st = _fresh()
        st._ensure_loaded(str(tmp_path))
        for _ in range(2):
            token = tt.begin_call_context()
            try:
                st.add(10, str(tmp_path), "search_symbols")
            finally:
                tt.end_call_context(token)
            st.note_call()
        st.note_call()  # a tool that records no savings
        st.flush()
        assert _ledger(tmp_path)[sm.MEASURED_DAYS][_today()] == {
            "calls": 3,
            "saved": 20,
        }

    def test_the_dispatcher_calls_note_call(self):
        import ast

        src = (Path(tt.__file__).resolve().parent.parent / "server.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(src)
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "call_tool"
        )
        imported = {
            (alias.asname or alias.name)
            for n in ast.walk(fn)
            if isinstance(n, ast.ImportFrom)
            and (n.module or "").endswith("token_tracker")
            for alias in n.names
            if alias.name == "note_call"
        }
        called = {
            n.func.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert imported & called, (
            "every dispatched call must reach the savings heartbeat (token_tracker.note_call)"
        )


# ---------------------------------------------------------------------------
# 2. A read that fails refuses the write
# ---------------------------------------------------------------------------


class TestRefusedRead:
    def test_a_corrupt_file_is_not_wiped_and_the_batch_is_kept(self, tmp_path, caplog):
        corrupt = (
            '{"total_tokens_saved": 1000000000, "daily": {"2026-10-29": 5'  # truncated
        )
        _file(tmp_path).write_text(corrupt, encoding="utf-8")
        st = _fresh()
        with caplog.at_level(logging.WARNING, logger=tt.logger.name):
            for _ in range(3):
                st.add(100, str(tmp_path), "search_symbols")
        assert _file(tmp_path).read_text(encoding="utf-8") == corrupt, (
            "a file that could not be read is never replaced"
        )
        assert st._unflushed == 300, "the batch stays in memory for the next flush"
        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            "a refused write is a WARNING"
        )
        # Once the file reads again, the kept batch is added to its history.
        _file(tmp_path).write_text(
            json.dumps({"total_tokens_saved": 1000}), encoding="utf-8"
        )
        st.flush()
        assert _ledger(tmp_path)["total_tokens_saved"] == 1300
        assert st._unflushed == 0

    def test_a_file_that_is_not_an_object_is_refused(self, tmp_path):
        _file(tmp_path).write_text("[1, 2, 3]", encoding="utf-8")
        st = _fresh()
        for _ in range(3):
            st.add(100, str(tmp_path), "search_symbols")
        assert _file(tmp_path).read_text(encoding="utf-8") == "[1, 2, 3]"
        assert st._unflushed == 300

    def test_an_unreadable_file_is_refused(self, tmp_path, monkeypatch):
        _file(tmp_path).write_text(
            json.dumps({"total_tokens_saved": 777}), encoding="utf-8"
        )
        st = _fresh()
        st._ensure_loaded(str(tmp_path))
        real = pathlib.Path.read_text

        def deny(self, *a, **k):
            if self.name == "_savings.json":
                raise PermissionError("held by another program")
            return real(self, *a, **k)

        monkeypatch.setattr(pathlib.Path, "read_text", deny)
        for _ in range(3):
            st.add(100, str(tmp_path), "search_symbols")
        monkeypatch.setattr(pathlib.Path, "read_text", real)
        assert _ledger(tmp_path)["total_tokens_saved"] == 777
        assert st._unflushed == 300

    def test_a_missing_file_is_still_a_fresh_start(self, tmp_path):
        st = _fresh()
        for _ in range(3):
            st.add(10, str(tmp_path), "search_symbols")
        assert _ledger(tmp_path)["total_tokens_saved"] == 30


# ---------------------------------------------------------------------------
# 3. The write itself
# ---------------------------------------------------------------------------


class TestWrite:
    def test_the_target_is_never_written_in_place(self, tmp_path, monkeypatch):
        """A reader must never see a half-written `_savings.json`: the bytes go to
        a temp file and `os.replace` swaps it in."""
        real = pathlib.Path.write_text

        def refuse_target(self, *a, **k):
            if self.name == "_savings.json":
                raise AssertionError("_savings.json written in place")
            return real(self, *a, **k)

        monkeypatch.setattr(pathlib.Path, "write_text", refuse_target)
        st = _fresh()
        for _ in range(3):
            st.add(10, str(tmp_path), "search_symbols")
        monkeypatch.setattr(pathlib.Path, "write_text", real)
        assert _ledger(tmp_path)["total_tokens_saved"] == 30
        assert not list(tmp_path.glob("_savings.json.*.tmp")), (
            "no temp file is left behind"
        )

    def test_a_failed_write_warns_keeps_the_batch_and_the_old_file(
        self, tmp_path, monkeypatch, caplog
    ):
        _file(tmp_path).write_text(
            json.dumps({"total_tokens_saved": 500}), encoding="utf-8"
        )
        st = _fresh()
        st._ensure_loaded(str(tmp_path))

        def full_disk(*a, **k):
            raise OSError(28, "No space left on device")

        real_replace = tt.os.replace
        monkeypatch.setattr(tt.os, "replace", full_disk)
        with caplog.at_level(logging.WARNING, logger=tt.logger.name):
            for _ in range(3):
                st.add(10, str(tmp_path), "search_symbols")
        monkeypatch.setattr(tt.os, "replace", real_replace)
        assert _ledger(tmp_path)["total_tokens_saved"] == 500
        assert st._unflushed == 30
        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            "a write failure is a WARNING, never debug"
        )
        assert not list(tmp_path.glob("_savings.json.*.tmp"))
        st.flush()
        assert _ledger(tmp_path)["total_tokens_saved"] == 530

    def test_the_store_root_honors_code_index_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
        assert tt._savings_path() == tmp_path / "_savings.json"
        assert Path(tool_counters.storage_root()) == tmp_path, (
            "one root helper for both stores"
        )

    def test_the_root_in_force_when_counted_is_the_one_written(
        self, tmp_path, monkeypatch
    ):
        """Maintenance Practice 8: the last flush runs at interpreter exit, after a
        test has restored its environment."""
        counted, later = tmp_path / "counted", tmp_path / "later"
        counted.mkdir()
        later.mkdir()
        monkeypatch.setenv("CODE_INDEX_PATH", str(counted))
        st = _fresh()
        st.add(10, None, "search_symbols")
        monkeypatch.setenv("CODE_INDEX_PATH", str(later))
        st.flush()
        assert _ledger(counted)["total_tokens_saved"] == 10
        assert not _file(later).exists()


# ---------------------------------------------------------------------------
# 4. Two writers
# ---------------------------------------------------------------------------


class TestTwoWriters:
    def test_a_second_writer_inside_the_first_ones_window_loses_nothing(
        self, tmp_path, monkeypatch
    ):
        """PINNED: process B tries to flush while process A has read the file and
        not yet replaced it. Without the lock, B's write lands and A's replace
        erases it."""
        a, b = _fresh(), _fresh()
        a._ensure_loaded(str(tmp_path))
        b._ensure_loaded(str(tmp_path))
        a._unflushed, a._total = 100, 100
        b._unflushed, b._total = 7, 7
        a._hb_calls = b._hb_calls = 1
        real = pathlib.Path.read_text
        fired = []

        def inside_a(self, *args, **kw):
            text = real(self, *args, **kw)
            if self.name == "_savings.json" and not fired:
                fired.append(True)
                b._flush(lock_wait=0.0)  # B runs inside A's read-to-write window
            return text

        _file(tmp_path).write_text(
            json.dumps({"total_tokens_saved": 1000}), encoding="utf-8"
        )
        monkeypatch.setattr(pathlib.Path, "read_text", inside_a)
        a._flush(lock_wait=0.0)
        monkeypatch.setattr(pathlib.Path, "read_text", real)
        assert fired, "the interleaving did not happen; the test proves nothing"
        assert b._unflushed == 7, "B could not take the lock and kept its batch"
        assert _ledger(tmp_path)["total_tokens_saved"] == 1100
        b._flush(lock_wait=0.0)
        assert _ledger(tmp_path)["total_tokens_saved"] == 1107, (
            "both processes' batches are counted"
        )

    def test_a_held_lock_defers_the_write(self, tmp_path):
        _file(tmp_path).write_text(
            json.dumps({"total_tokens_saved": 40}), encoding="utf-8"
        )
        (tmp_path / tt._SAVINGS_LOCK_FILE).write_text(
            "", encoding="utf-8"
        )  # another server holds it
        st = _fresh()
        st._ensure_loaded(str(tmp_path))
        st._unflushed, st._hb_calls = 5, 1
        st._flush(lock_wait=0.0)
        assert _ledger(tmp_path)["total_tokens_saved"] == 40
        assert st._unflushed == 5
        (tmp_path / tt._SAVINGS_LOCK_FILE).unlink()
        st._flush(lock_wait=0.0)
        assert _ledger(tmp_path)["total_tokens_saved"] == 45

    def test_a_change_between_load_and_flush_is_merged_not_overwritten(self, tmp_path):
        _file(tmp_path).write_text(
            json.dumps({"total_tokens_saved": 100}), encoding="utf-8"
        )
        st = _fresh()
        st._ensure_loaded(str(tmp_path))
        _file(tmp_path).write_text(
            json.dumps({"total_tokens_saved": 500}), encoding="utf-8"
        )  # another server
        for _ in range(3):
            st.add(10, str(tmp_path), "search_symbols")
        assert _ledger(tmp_path)["total_tokens_saved"] == 530
        assert st.get_total(str(tmp_path)) == 530, (
            "the in-memory total follows the file it merged into"
        )


# ---------------------------------------------------------------------------
# 5. The label
# ---------------------------------------------------------------------------

_OLD = "2026-10-29T12:00:00+00:00"


def _write_meter(root: Path, **extra) -> None:
    data = {
        "total_tokens_saved": 1_000_000_000,
        "anon_id": "abc",
        "daily": {"2026-10-28": 9, "2026-10-29": 5},
    }
    data.update(extra)
    _file(root).write_text(json.dumps(data), encoding="utf-8")


def _counters_touched(root: Path, when: datetime) -> None:
    path = root / tool_counters.FILE
    path.write_text(
        json.dumps(
            {"version": 1, "since": "2026-01-01", "estimator": "bytes/4", "tools": {}}
        ),
        encoding="utf-8",
    )
    ts = when.timestamp()
    os.utime(path, (ts, ts))


class TestLabel:
    def test_calls_counted_after_the_last_stamp_are_stale(self, tmp_path):
        _write_meter(tmp_path, last_flush_at=_OLD)
        _counters_touched(tmp_path, datetime(2026, 11, 1, 12, tzinfo=timezone.utc))
        got = sm.label_for_root(tmp_path, _ledger(tmp_path))
        assert got == {
            sm.STATUS: "stale",
            sm.MEASURED_THROUGH: "2026-10-29T12:00:00Z",
            sm.STALE_SINCE: "2026-10-29",
        }
        assert sm.describe(got) == "unmeasured since 2026-10-29"

    def test_no_later_calls_is_current(self, tmp_path):
        _write_meter(tmp_path, last_flush_at=_OLD)
        _counters_touched(tmp_path, datetime(2026, 10, 29, 12, 1, tzinfo=timezone.utc))
        got = sm.label_for_root(tmp_path, _ledger(tmp_path))
        assert got[sm.STATUS] == "current" and got[sm.STALE_SINCE] is None
        assert sm.describe(got) is None

    def test_a_file_with_no_heartbeat_is_stale_since_its_last_day(self, tmp_path):
        _write_meter(tmp_path)
        got = sm.label_for_root(tmp_path, _ledger(tmp_path))
        assert got == {
            sm.STATUS: "stale",
            sm.MEASURED_THROUGH: None,
            sm.STALE_SINCE: "2026-10-29",
        }

    def test_unknown_is_never_current(self, tmp_path):
        assert sm.label(None)[sm.STATUS] == "unknown"  # file exists, not read
        assert (
            sm.label({"total_tokens_saved": 5})[sm.STATUS] == "unknown"
        )  # no stamp, no date
        assert (
            sm.label({"total_tokens_saved": 5, sm.LAST_FLUSH_AT: "yesterday"})[
                sm.STATUS
            ]
            == "unknown"
        )
        unread_counters = sm.label(
            {"total_tokens_saved": 5, sm.LAST_FLUSH_AT: _OLD}, ("unknown", None)
        )
        assert unread_counters[sm.STATUS] == "unknown"
        assert sm.describe(unread_counters) == "measurement state unknown"


class TestDisplaySurfaces:
    def test_receipt_carries_and_prints_the_label(self, tmp_path):
        from jcodemunch_mcp.cli import receipt

        _write_meter(tmp_path, last_flush_at=_OLD)
        _counters_touched(tmp_path, datetime(2026, 11, 1, 12, tzinfo=timezone.utc))
        meter = receipt.lifetime_meter(root=tmp_path)
        assert meter[sm.STATUS] == "stale" and meter[sm.STALE_SINCE] == "2026-10-29"
        text = receipt.render_text(
            receipt.aggregate([]), days=30, model="opus", meter=meter
        )
        assert "unmeasured since 2026-10-29" in text
        payload = json.loads(
            receipt.render_json(receipt.aggregate([]), model="opus", meter=meter)
        )
        assert payload["lifetime"][sm.STATUS] == "stale"
        assert payload["lifetime"][sm.STALE_SINCE] == "2026-10-29"
        assert payload["lifetime"][sm.MEASURED_THROUGH] == "2026-10-29T12:00:00Z"

    def test_receipt_says_nothing_extra_for_a_current_meter(self, tmp_path):
        from jcodemunch_mcp.cli import receipt

        now = datetime.now(timezone.utc)
        _write_meter(tmp_path, last_flush_at=now.isoformat())
        _counters_touched(tmp_path, now - timedelta(seconds=5))
        meter = receipt.lifetime_meter(root=tmp_path)
        assert meter[sm.STATUS] == "current"
        text = receipt.render_text(
            receipt.aggregate([]), days=30, model="opus", meter=meter
        )
        assert "unmeasured" not in text and "unknown" not in text

    def test_diagnose_carries_the_label_and_passes_its_allowlist(self, tmp_path):
        from jcodemunch_mcp import diagnose

        _write_meter(tmp_path, last_flush_at=_OLD)
        _counters_touched(tmp_path, datetime(2026, 11, 1, 12, tzinfo=timezone.utc))
        tools = diagnose.build_report(storage_path=str(tmp_path), policy_paths={})[
            "tools"
        ]
        assert tools[sm.STATUS] == "stale"
        assert tools[sm.STALE_SINCE] == "2026-10-29"
        assert tools[sm.MEASURED_THROUGH] == "2026-10-29T12:00:00Z"

    def test_diagnose_unreadable_savings_is_unknown(self, tmp_path):
        from jcodemunch_mcp import diagnose

        _file(tmp_path).write_text("not json", encoding="utf-8")
        tools = diagnose.build_report(storage_path=str(tmp_path), policy_paths={})[
            "tools"
        ]
        assert tools[sm.STATUS] == "unknown"

    def test_session_stats_report_a_meter_whose_writes_are_refused(self, tmp_path):
        _write_meter(tmp_path, last_flush_at=_OLD)
        (tmp_path / tt._SAVINGS_LOCK_FILE).write_text(
            "", encoding="utf-8"
        )  # held, and fresh
        st = _fresh()
        st._ensure_loaded(str(tmp_path))
        st._unflushed, st._hb_calls = 5, 1
        for attempt in range(1, tt._BUSY_DEFERRALS_BEFORE_STALE):
            st._flush(lock_wait=0.0)
            assert st.session_stats(str(tmp_path))[sm.STATUS] == "current", (
                f"deferral {attempt}: one server briefly holding the file is not a stopped meter"
            )
        st._flush(lock_wait=0.0)
        stats = st.session_stats(str(tmp_path))
        assert stats[sm.STATUS] == "stale"
        assert stats[sm.STALE_SINCE] == "2026-10-29"
        assert stats[sm.MEASURED_THROUGH] == "2026-10-29T12:00:00Z"
        on_disk = json.loads(
            (tmp_path / "session_stats.json").read_text(encoding="utf-8")
        )
        assert on_disk[sm.STATUS] == "stale", (
            "session_stats.json carries the label for external dashboards"
        )
        (tmp_path / tt._SAVINGS_LOCK_FILE).unlink()
        st._flush(lock_wait=0.0)
        stats = st.session_stats(str(tmp_path))
        assert stats[sm.STATUS] == "current" and stats[sm.STALE_SINCE] is None
        assert sm.parse_stamp(stats[sm.MEASURED_THROUGH]) > sm.parse_stamp(_OLD)

    def test_session_stats_of_an_unreadable_history_are_unknown(self, tmp_path):
        _file(tmp_path).write_text("{broken", encoding="utf-8")
        st = _fresh()
        for _ in range(3):
            st.add(10, str(tmp_path), "search_symbols")
        assert st.session_stats(str(tmp_path))[sm.STATUS] == "unknown"

    def test_get_session_stats_carries_the_label(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tt, "_state", _fresh())
        for _ in range(3):
            tt.record_savings(10, str(tmp_path), "search_symbols")
        stats = tt.get_session_stats(str(tmp_path))
        assert stats[sm.STATUS] == "current"
        assert sm.parse_stamp(stats[sm.MEASURED_THROUGH]) is not None


def test_security_md_discloses_the_new_fields_and_the_lock():
    text = (Path(__file__).resolve().parent.parent / "SECURITY.md").read_text(
        encoding="utf-8"
    )
    for needle in ("last_flush_at", "measured_days", "_savings.lock"):
        assert needle in text, (
            f"SECURITY.md's background-behavior list must name {needle}"
        )


def test_the_label_module_imports_nothing_from_the_network():
    src = Path(sm.__file__).read_text(encoding="utf-8")
    for mod in ("urllib", "http", "socket", "requests", "httpx"):
        assert f"import {mod}" not in src and f"from {mod}" not in src


# ---------------------------------------------------------------------------
# 6. The state lock is never held across disk I/O, and exit cannot deadlock
# ---------------------------------------------------------------------------


class _SpyLock:
    """A real lock that says when someone starts waiting on it."""

    def __init__(self):
        self._inner = threading.Lock()
        self.waiting = threading.Event()

    def acquire(self, blocking=True, timeout=-1):
        if self._inner.acquire(blocking=False):
            return True
        if not blocking:
            return False
        self.waiting.set()
        return self._inner.acquire(True, timeout)

    def release(self):
        self._inner.release()

    def locked(self):
        return self._inner.locked()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


class TestStateLock:
    def test_the_file_write_runs_without_the_state_lock(self, tmp_path, monkeypatch):
        """While `_merge_batch` reads the file, another thread can take `_lock`."""
        st = _fresh()
        st._ensure_loaded(str(tmp_path))
        st._unflushed, st._hb_calls = 3, 1
        real = pathlib.Path.read_text
        seen = []

        def probe(self, *a, **k):
            if self.name == "_savings.json":
                seen.append(st._lock.acquire(blocking=False))
                if seen[-1]:
                    st._lock.release()
            return real(self, *a, **k)

        monkeypatch.setattr(pathlib.Path, "read_text", probe)
        st._flush(lock_wait=0.0)
        monkeypatch.setattr(pathlib.Path, "read_text", real)
        assert seen == [True], "the state lock was held across the savings-file read"
        assert _ledger(tmp_path)["total_tokens_saved"] == 3

    def test_note_call_never_takes_the_state_lock(self, tmp_path):
        st = _fresh()
        st._ensure_loaded(str(tmp_path))
        done = threading.Event()
        with st._lock:  # held, as by a flush or a signal handler's frame
            t = threading.Thread(target=lambda: (st.note_call(), done.set()))
            t.start()
            assert done.wait(5), "note_call waited on the state lock"
        t.join(5)
        assert len(st._dispatch_ticks) == 1

    def test_exit_flush_waits_for_a_lock_another_thread_holds_then_writes(
        self, tmp_path
    ):
        """PINNED: the exit flush is waiting on `_lock` (the spy says so) before
        the holder lets go; it then completes and writes."""
        st = _fresh()
        st._lock = _SpyLock()
        st._ensure_loaded(str(tmp_path))
        st._unflushed, st._hb_calls = 9, 1
        st._lock.acquire()
        t = threading.Thread(target=st.flush)
        t.start()
        assert st._lock.waiting.wait(5), "the exit flush never reached the state lock"
        st._lock.release()
        t.join(10)
        assert not t.is_alive()
        assert _ledger(tmp_path)["total_tokens_saved"] == 9

    def test_exit_flush_against_a_lock_the_same_thread_holds_returns(
        self, tmp_path, caplog
    ):
        """A signal handled inside a locked region runs `flush` on the thread that
        holds `_lock`. A blocking acquire there never returns."""
        st = _fresh()
        st._ensure_loaded(str(tmp_path))
        st._unflushed, st._hb_calls = 9, 1
        returned = threading.Event()

        def holder_then_flush():
            # The SAME thread holds the lock and then flushes, as a signal
            # handler does. A daemon, so a deadlock fails this test instead
            # of hanging the run.
            st._lock.acquire()
            st.flush()
            returned.set()

        with caplog.at_level(logging.WARNING, logger=tt.logger.name):
            threading.Thread(target=holder_then_flush, daemon=True).start()
            assert returned.wait(20), "flush deadlocked on a lock its own thread holds"
        assert any("busy" in r.getMessage() for r in caplog.records)

    def test_a_real_interpreter_exits_with_the_state_lock_held(self, tmp_path):
        """The atexit flush runs while the main thread still holds `_lock`; the
        process must still exit, and promptly."""
        src_root = Path(tt.__file__).resolve().parents[2]
        script = (
            "from jcodemunch_mcp.storage import token_tracker as tt\n"
            "tt.record_savings(5, None, 'search_symbols')\n"
            "tt._state._lock.acquire()\n"
        )
        env = {
            **os.environ,
            "PYTHONPATH": str(src_root),
            "CODE_INDEX_PATH": str(tmp_path),
            "JCODEMUNCH_SHARE_SAVINGS": "0",
        }
        t0 = time.monotonic()
        done = subprocess.run(
            [sys.executable, "-c", script], env=env, timeout=60, capture_output=True
        )
        assert done.returncode == 0, done.stderr.decode(errors="replace")[-2000:]
        assert time.monotonic() - t0 < 50


# ---------------------------------------------------------------------------
# 7. The heartbeat thread and the dispatcher entry points
# ---------------------------------------------------------------------------


class TestHeartbeatThread:
    def test_twenty_dispatched_calls_start_a_writer_that_finishes(self, tmp_path):
        st = _fresh()
        st._ensure_loaded(str(tmp_path))
        for _ in range(tt._HEARTBEAT_FLUSH_CALLS):
            st.note_call()
        assert st._hb_thread is not None, "no heartbeat writer was started"
        st._hb_thread.join(10)
        assert not st._hb_thread.is_alive(), (
            "the heartbeat writer must finish (it is joined at exit)"
        )
        assert _ledger(tmp_path)[sm.MEASURED_DAYS][_today()] == {
            "calls": tt._HEARTBEAT_FLUSH_CALLS,
            "saved": 0,
        }
        assert not st._hb_thread.daemon

    def test_the_first_call_after_the_interval_starts_a_writer(self, tmp_path):
        st = _fresh()
        st._ensure_loaded(str(tmp_path))
        st._last_flush_mono -= tt._HEARTBEAT_FLUSH_SECONDS + 1
        st.note_call()
        assert st._hb_thread is not None
        st._hb_thread.join(10)
        assert not st._hb_thread.is_alive()
        assert _ledger(tmp_path)[sm.MEASURED_DAYS][_today()]["calls"] == 1


class TestDispatcherEntryPoints:
    @pytest.fixture()
    def store(self, tmp_path, monkeypatch):
        root = tmp_path / "store"
        root.mkdir()
        monkeypatch.setenv("CODE_INDEX_PATH", str(root))
        monkeypatch.setattr(tt, "_state", _fresh())
        monkeypatch.setattr(tool_counters, "_state", tool_counters._State())
        return root

    @pytest.mark.parametrize(
        "tool,args",
        [
            ("order", {"action": "list_repos", "args": {}}),
            ("route", {"task": "list the indexed repositories"}),
        ],
    )
    def test_a_front_door_call_is_one_measured_call(self, store, tool, args):
        from jcodemunch_mcp import server

        asyncio.run(server.call_tool(tool, args))
        tt._state.flush()
        assert _ledger(store)[sm.MEASURED_DAYS][_today()]["calls"] == 1, (
            "a relayed call is one client call: the outer frame counts it, the inner one does not"
        )

    def test_the_lifetime_total_in_meta_is_labeled_when_not_current(self, monkeypatch):
        from jcodemunch_mcp import server
        from jcodemunch_mcp.encoding.schema_driven import UNIVERSAL_META_JSON

        st = _fresh()
        monkeypatch.setattr(tt, "_state", st)
        stale = {
            sm.STATUS: "stale",
            sm.MEASURED_THROUGH: "2026-10-29T12:00:00Z",
            sm.STALE_SINCE: "2026-10-29",
        }
        st._label = dict(stale)
        result = {
            "_meta": {"total_tokens_saved": 5},
            "results": [{"_meta": {"total_tokens_saved": 5}}, {}],
        }
        server._label_lifetime_total(result)
        assert result["_meta"]["lifetime_meter"] == stale
        assert result["results"][0]["_meta"]["lifetime_meter"] == stale
        assert "_meta" not in result["results"][1]
        st._label = {
            sm.STATUS: "current",
            sm.MEASURED_THROUGH: "2026-11-02T00:00:00Z",
            sm.STALE_SINCE: None,
        }
        healthy = {"_meta": {"total_tokens_saved": 5}}
        server._label_lifetime_total(healthy)
        assert healthy == {"_meta": {"total_tokens_saved": 5}}, (
            "a current meter's responses are unchanged"
        )
        assert "lifetime_meter" in UNIVERSAL_META_JSON, (
            "a compact encoder must not drop the label and keep the total"
        )


def test_a_stale_temp_file_from_a_killed_process_is_removed_at_load(tmp_path):
    old = tmp_path / "_savings.json.4242.tmp"
    young = tmp_path / "_savings.json.4243.tmp"
    other = tmp_path / "_savings.json.notapid.tmp"
    for f in (old, young, other):
        f.write_text("{}", encoding="utf-8")
    past = time.time() - 3600
    os.utime(old, (past, past))
    os.utime(other, (past, past))
    _fresh()._ensure_loaded(str(tmp_path))
    assert not old.exists()
    assert young.exists(), (
        "a temp file under a minute old may belong to a write in progress"
    )
    assert other.exists(), "only the writer's own name pattern is removed"


def test_a_float_total_is_accepted_as_receipt_reads_it(tmp_path):
    _file(tmp_path).write_text(
        json.dumps({"total_tokens_saved": 1000.0}), encoding="utf-8"
    )
    st = _fresh()
    for _ in range(3):
        st.add(10, str(tmp_path), "search_symbols")
    assert _ledger(tmp_path)["total_tokens_saved"] == 1030


def test_the_root_rule_is_the_index_stores(tmp_path, monkeypatch):
    from jcodemunch_mcp.storage.sqlite_store import _default_base_path

    monkeypatch.setenv("CODE_INDEX_PATH", "~/jcm-root-rule-probe")
    assert Path(tool_counters.storage_root()) == _default_base_path(), (
        "CODE_INDEX_PATH=~/x: one place for both"
    )
    assert "~" not in tool_counters.storage_root()
