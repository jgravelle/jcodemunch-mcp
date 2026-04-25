"""Tests for v1.74.0 perf telemetry foundation."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import token_tracker as tt
from jcodemunch_mcp.tools.analyze_perf import analyze_perf


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch, tmp_path):
    """Each test gets a fresh _State and a tmp storage dir."""
    fresh = tt._State()
    fresh._base_path = str(tmp_path)
    monkeypatch.setattr(tt, "_state", fresh)
    yield


class TestLatencyRing:
    def test_record_and_stats(self):
        for ms in (10.0, 20.0, 30.0, 40.0, 50.0):
            tt.record_tool_latency("search_symbols", ms, ok=True)
        stats = tt.latency_stats()
        assert "search_symbols" in stats
        s = stats["search_symbols"]
        assert s["count"] == 5
        assert s["p50_ms"] == pytest.approx(30.0)
        assert s["max_ms"] == 50.0
        assert s["errors"] == 0
        assert s["error_rate"] == 0.0

    def test_error_tracking(self):
        tt.record_tool_latency("search_symbols", 5.0, ok=True)
        tt.record_tool_latency("search_symbols", 5.0, ok=False)
        tt.record_tool_latency("search_symbols", 5.0, ok=False)
        stats = tt.latency_stats()
        assert stats["search_symbols"]["errors"] == 2
        assert stats["search_symbols"]["error_rate"] == pytest.approx(2 / 3, abs=1e-3)

    def test_ring_caps_at_default(self):
        # Push more than _LATENCY_RING_DEFAULT entries; ring must not grow.
        for i in range(tt._LATENCY_RING_DEFAULT + 100):
            tt.record_tool_latency("plan_turn", float(i), ok=True)
        stats = tt.latency_stats()
        assert stats["plan_turn"]["count"] == tt._LATENCY_RING_DEFAULT


class TestPerfDbSink:
    def test_disabled_by_default_no_db_written(self, tmp_path):
        tt.record_tool_latency("search_symbols", 12.0, ok=True)
        # No sink => telemetry.db should not exist.
        assert not (tmp_path / "telemetry.db").exists()

    def test_enabled_writes_rows(self, monkeypatch, tmp_path):
        from jcodemunch_mcp import config as _config
        monkeypatch.setattr(_config, "get", lambda key, default=None: True if key == "perf_telemetry_enabled" else default)
        tt.record_tool_latency("search_symbols", 12.0, ok=True, repo="local/x")
        tt.record_tool_latency("search_symbols", 99.0, ok=False, repo="local/x")
        db = tmp_path / "telemetry.db"
        assert db.exists()
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT tool, duration_ms, ok, repo FROM tool_calls ORDER BY ts ASC"
        ).fetchall()
        conn.close()
        assert len(rows) == 2
        assert rows[0] == ("search_symbols", 12.0, 1, "local/x")
        assert rows[1] == ("search_symbols", 99.0, 0, "local/x")


class TestAnalyzePerfTool:
    def test_session_window_uses_in_memory(self):
        for ms in (10.0, 20.0, 30.0):
            tt.record_tool_latency("search_symbols", ms)
        out = analyze_perf(window="session")
        assert out["window"] == "session"
        assert "search_symbols" in out["in_memory_session"]
        assert out["persisted"] == {}
        # ranked list pulls from in-memory when window=session
        names = [item["tool"] for item in out["slowest_by_p95"]]
        assert "search_symbols" in names

    def test_invalid_window(self):
        out = analyze_perf(window="forever")
        assert "error" in out

    def test_persisted_window_reads_db(self, monkeypatch, tmp_path):
        from jcodemunch_mcp import config as _config
        monkeypatch.setattr(_config, "get", lambda key, default=None: True if key == "perf_telemetry_enabled" else default)
        tt.record_tool_latency("get_symbol_source", 5.0, repo="r1")
        tt.record_tool_latency("get_symbol_source", 25.0, repo="r1")
        out = analyze_perf(window="all", storage_path=str(tmp_path))
        assert out["window"] == "all"
        assert out["persisted_meta"]["source"] == "telemetry.db"
        assert out["persisted_meta"]["rows"] == 2
        assert "get_symbol_source" in out["persisted"]
        s = out["persisted"]["get_symbol_source"]
        assert s["count"] == 2
        assert s["max_ms"] == 25.0


class TestSessionStatsIncludesLatency:
    def test_latency_per_tool_present_in_session_stats(self):
        tt.record_tool_latency("search_symbols", 7.0)
        stats = tt._state.session_stats(base_path=None)
        assert "latency_per_tool" in stats
        assert "search_symbols" in stats["latency_per_tool"]


class TestServerRegistration:
    def test_analyze_perf_in_canonical(self):
        from jcodemunch_mcp.server import _CANONICAL_TOOL_NAMES
        assert "analyze_perf" in _CANONICAL_TOOL_NAMES

    def test_analyze_perf_in_standard_tier(self):
        from jcodemunch_mcp.server import _TOOL_TIER_STANDARD
        assert "analyze_perf" in _TOOL_TIER_STANDARD

    def test_analyze_perf_in_default_bundle(self):
        from jcodemunch_mcp.config import DEFAULTS
        assert "analyze_perf" in DEFAULTS["tool_tier_bundles"]["standard"]

    def test_analyze_perf_in_template_all_tools(self):
        from jcodemunch_mcp.config import generate_template
        text = generate_template()
        assert "analyze_perf" in text
