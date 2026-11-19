"""Tests for v1.75.0 retrieval confidence (retrieval/confidence.py)."""

from __future__ import annotations

import pytest

from jcodemunch_mcp.retrieval.confidence import (
    attach_confidence,
    compute_confidence,
)


class TestComputeConfidence:
    def test_empty_results_yield_zero(self):
        out = compute_confidence([])
        assert out["confidence"] == 0.0

    def test_single_strong_result_is_high(self):
        out = compute_confidence([{"score": 12.0}])
        # gap is 1.0 (top1 dominates with no top2), strength saturates
        assert out["confidence"] >= 0.7

    def test_two_close_results_lowers_confidence(self):
        tight = compute_confidence([{"score": 5.0}, {"score": 4.9}])
        wide = compute_confidence([{"score": 5.0}, {"score": 0.5}])
        assert wide["confidence"] > tight["confidence"]

    def test_stale_index_lowers_confidence(self):
        fresh = compute_confidence([{"score": 8.0}], is_stale=False)
        stale = compute_confidence([{"score": 8.0}], is_stale=True)
        assert fresh["confidence"] > stale["confidence"]

    def test_identity_match_boosts_confidence(self):
        unknown = compute_confidence([{"score": 5.0}])
        known_identity = compute_confidence(
            [{"score": 5.0}], has_identity_match=True
        )
        assert known_identity["confidence"] > unknown["confidence"]

    def test_components_returned(self):
        out = compute_confidence([{"score": 5.0}, {"score": 1.0}])
        comps = out["components"]
        assert {"gap", "strength", "identity", "freshness"} <= set(comps)
        assert 0 <= comps["gap"] <= 1
        assert 0 <= comps["strength"] <= 1


class TestAttachConfidence:
    def test_default_reads_results_field(self):
        result = {"results": [{"score": 10.0}, {"score": 1.0}], "_meta": {}}
        out = attach_confidence(result)
        assert "confidence" in out["_meta"]
        assert 0.0 <= out["_meta"]["confidence"] <= 1.0

    def test_explicit_score_list_overrides_results(self):
        result = {"results": [], "_meta": {}}
        out = attach_confidence(
            result, scored_results=[{"score": 8.0}]
        )
        assert out["_meta"]["confidence"] > 0

    def test_components_included_when_requested(self):
        result = {"results": [{"score": 5.0}], "_meta": {}}
        attach_confidence(result, include_components=True)
        assert "confidence_components" in result["_meta"]


class TestSearchSymbolsAttachesConfidence:
    def test_search_symbols_carries_confidence_meta(self, tmp_path):
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp.tools.search_symbols import search_symbols

        src = tmp_path / "src"
        src.mkdir()
        store = tmp_path / "store"
        store.mkdir()
        (src / "auth.py").write_text(
            "def authenticate_user():\n    pass\n\n"
            "def deauthenticate_user():\n    pass\n"
        )
        r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert r["success"] is True
        out = search_symbols(
            repo=r["repo"],
            query="authenticate_user",
            storage_path=str(store),
        )
        assert "_meta" in out
        assert "confidence" in out["_meta"]
        assert 0.0 <= out["_meta"]["confidence"] <= 1.0


class TestAnalyzePerfBaselineCompare:
    def test_missing_baseline_is_handled_gracefully(self):
        from jcodemunch_mcp.tools.analyze_perf import analyze_perf
        out = analyze_perf(window="session", compare_release="0.0.0-nonexistent")
        assert out["baseline_meta"]["found"] is False

    def test_baseline_diff_with_synthetic_baseline(self, tmp_path, monkeypatch):
        # Create a fake baseline directory at tmp_path and monkeypatch
        # _baseline_path to point at it.
        from jcodemunch_mcp.tools import analyze_perf as ap
        from jcodemunch_mcp.storage import token_tracker as tt

        # Reset state and seed some session data
        fresh = tt._State()
        fresh._base_path = str(tmp_path)
        monkeypatch.setattr(tt, "_state", fresh)
        for ms in (10.0, 20.0, 30.0):
            tt.record_tool_latency("search_symbols", ms, ok=True)

        baseline = {
            "version": "9.9.9",
            "captured_at": "2026-01-01T00:00:00Z",
            "session": {"session_calls": 1, "session_tokens_saved": 100, "session_duration_s": 1.0},
            "tools": {
                "search_symbols": {
                    "calls": 10,
                    "tokens_saved": 5000,
                    "p50_ms": 5.0,
                    "p95_ms": 10.0,
                }
            },
        }
        baseline_dir = tmp_path / "benchmarks" / "token_baselines"
        baseline_dir.mkdir(parents=True)
        (baseline_dir / "v9.9.9.json").write_text(__import__("json").dumps(baseline))

        monkeypatch.setattr(ap, "_baseline_path", lambda v: baseline_dir / f"v{v}.json")

        out = ap.analyze_perf(window="session", compare_release="9.9.9")
        assert out["baseline_meta"]["found"] is True
        assert "search_symbols" in out["baseline_diff"]
        diff = out["baseline_diff"]["search_symbols"]
        # 3 calls now vs 10 in baseline → -7
        assert diff["calls_delta"] == -7


class TestServerSchemaHasCompareRelease:
    @pytest.mark.asyncio
    async def test_analyze_perf_schema_has_compare_release(self):
        from jcodemunch_mcp.server import list_tools

        tools = await list_tools()
        target = next((t for t in tools if t.name == "analyze_perf"), None)
        assert target is not None
        props = target.input_schema["properties"]
        assert "compare_release" in props
