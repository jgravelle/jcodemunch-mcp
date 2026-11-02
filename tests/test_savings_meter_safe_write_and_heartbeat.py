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

import json
import logging
import os
import pathlib
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
            '{"total_tokens_saved": 34317586613, "daily": {"2026-10-29": 5'  # truncated
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
                b._flush_locked(lock_wait=0.0)  # B runs inside A's read-to-write window
            return text

        _file(tmp_path).write_text(
            json.dumps({"total_tokens_saved": 1000}), encoding="utf-8"
        )
        monkeypatch.setattr(pathlib.Path, "read_text", inside_a)
        a._flush_locked(lock_wait=0.0)
        monkeypatch.setattr(pathlib.Path, "read_text", real)
        assert fired, "the interleaving did not happen; the test proves nothing"
        assert b._unflushed == 7, "B could not take the lock and kept its batch"
        assert _ledger(tmp_path)["total_tokens_saved"] == 1100
        b._flush_locked(lock_wait=0.0)
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
        st._flush_locked(lock_wait=0.0)
        assert _ledger(tmp_path)["total_tokens_saved"] == 40
        assert st._unflushed == 5
        (tmp_path / tt._SAVINGS_LOCK_FILE).unlink()
        st._flush_locked(lock_wait=0.0)
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
        "total_tokens_saved": 34_317_586_613,
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
        st._flush_locked(lock_wait=0.0)
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
        st._flush_locked(lock_wait=0.0)
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
