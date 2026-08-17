"""Daily rollup in the savings meter (v1.108.136).

The lifetime meter is the authoritative savings record, but it stored only one
number, so any windowed view ("today", "this month") had to fall back to
transcript scans — which miss cleared history and model conservatively (observed
four orders of magnitude under the meter on a heavy install). The flush now also
credits each batch to a per-local-day bucket in `_savings.json`'s `daily` map.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest


@pytest.fixture()
def tracker(monkeypatch):
    from jcodemunch_mcp.storage import token_tracker as tt

    monkeypatch.setattr(tt, "_share_savings", lambda *a, **k: None)
    monkeypatch.setattr(tt._config, "get", lambda key, default=None, repo=None: default)
    return tt


def _read(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "_savings.json").read_text())


def _today() -> str:
    return datetime.datetime.now().date().isoformat()


class TestDailyRollup:
    def test_flush_credits_todays_bucket(self, tracker, tmp_path):
        st = tracker._State()
        st.add(1200, str(tmp_path))
        st.flush()
        data = _read(tmp_path)
        assert data["total_tokens_saved"] == 1200
        assert data["daily"] == {_today(): 1200}

    def test_repeat_flushes_accumulate_into_the_same_day(self, tracker, tmp_path):
        st = tracker._State()
        st.add(1000, str(tmp_path))
        st.flush()
        st.add(700, str(tmp_path))
        st.flush()
        data = _read(tmp_path)
        assert data["daily"][_today()] == 1700
        assert data["total_tokens_saved"] == 1700

    def test_daily_survives_and_extends_across_processes(self, tracker, tmp_path):
        """A second _State (fresh process) must add to yesterday's history, not
        clobber it — the flush is read-modify-write over the shared file."""
        yesterday = (datetime.datetime.now().date() - datetime.timedelta(days=1)).isoformat()
        (tmp_path / "_savings.json").write_text(
            json.dumps({"total_tokens_saved": 500, "daily": {yesterday: 500}})
        )
        st = tracker._State()
        st.add(300, str(tmp_path))
        st.flush()
        data = _read(tmp_path)
        assert data["daily"] == {yesterday: 500, _today(): 300}
        assert data["total_tokens_saved"] == 800

    def test_zero_delta_flush_writes_no_bucket(self, tracker, tmp_path):
        st = tracker._State()
        st.add(100, str(tmp_path))
        st.flush()
        st.flush()  # nothing new — must not mint an empty/duplicate entry
        assert _read(tmp_path)["daily"][_today()] == 100

    def test_oldest_days_prune_at_the_cap(self, tracker, tmp_path):
        base = datetime.date(2020, 1, 1)
        stale = {
            (base + datetime.timedelta(days=i)).isoformat(): 1
            for i in range(tracker._DAILY_MAX_DAYS)
        }
        (tmp_path / "_savings.json").write_text(
            json.dumps({"total_tokens_saved": len(stale), "daily": stale})
        )
        st = tracker._State()
        st.add(50, str(tmp_path))
        st.flush()
        daily = _read(tmp_path)["daily"]
        assert len(daily) == tracker._DAILY_MAX_DAYS
        assert _today() in daily
        assert base.isoformat() not in daily  # the oldest day made room

    def test_corrupt_daily_shape_is_replaced_not_fatal(self, tracker, tmp_path):
        (tmp_path / "_savings.json").write_text(
            json.dumps({"total_tokens_saved": 10, "daily": "garbage"})
        )
        st = tracker._State()
        st.add(40, str(tmp_path))
        st.flush()
        assert _read(tmp_path)["daily"] == {_today(): 40}
