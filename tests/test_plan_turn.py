"""Tests for plan_turn (Feature 3)."""

import pytest
from pathlib import Path


class TestPlanTurn:
    """Tests for plan_turn function."""

    def test_high_confidence_for_exact_match(self, tmp_path: Path):
        """Query for existing symbol returns high/medium confidence."""
        from jcodemunch_mcp.tools.plan_turn import plan_turn
        from tests.conftest_helpers import create_mini_index

        repo, storage_path = create_mini_index(tmp_path)
        result = plan_turn(repo=repo, query="my_func", storage_path=storage_path)

        assert result["confidence"] in ("high", "medium")
        assert len(result["recommended_symbols"]) >= 1

    def test_low_confidence_for_nonexistent(self, tmp_path: Path):
        """Query for nonexistent returns low confidence with gap_analysis."""
        from jcodemunch_mcp.tools.plan_turn import plan_turn
        from tests.conftest_helpers import create_mini_index

        repo, storage_path = create_mini_index(tmp_path)
        result = plan_turn(
            repo=repo,
            query="nonexistent_xyz_redis_cache",
            storage_path=storage_path,
        )

        assert result["confidence"] == "low"
        gap_lower = result["gap_analysis"].lower()
        assert "creat" in gap_lower or "not found" in gap_lower or "no symbols" in gap_lower

    def test_recommended_symbols_have_required_fields(self, tmp_path: Path):
        """Each recommended symbol has id, name, file, line."""
        from jcodemunch_mcp.tools.plan_turn import plan_turn
        from tests.conftest_helpers import create_mini_index

        repo, storage_path = create_mini_index(tmp_path)
        result = plan_turn(repo=repo, query="my_func", storage_path=storage_path)

        for sym in result["recommended_symbols"]:
            assert "id" in sym
            assert "name" in sym
            assert "file" in sym
            assert "line" in sym

    def test_recommended_files_is_list(self, tmp_path: Path):
        """recommended_files is a list."""
        from jcodemunch_mcp.tools.plan_turn import plan_turn
        from tests.conftest_helpers import create_mini_index

        repo, storage_path = create_mini_index(tmp_path)
        result = plan_turn(repo=repo, query="my_func", storage_path=storage_path)

        assert isinstance(result["recommended_files"], list)

    def test_max_recommended_limits_symbols(self, tmp_path: Path):
        """max_recommended=1 limits recommended_symbols to <= 1."""
        from jcodemunch_mcp.tools.plan_turn import plan_turn
        from tests.conftest_helpers import create_mini_index

        repo, storage_path = create_mini_index(tmp_path)
        result = plan_turn(
            repo=repo,
            query="my_func",
            max_recommended=1,
            storage_path=storage_path,
        )

        assert len(result["recommended_symbols"]) <= 1

    def test_tied_scores_do_not_crash(self, tmp_path: Path):
        """Equal-scored symbols should not crash heap ranking."""
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp.tools.plan_turn import plan_turn

        (tmp_path / "alpha.py").write_text(
            "def helper() -> int:\n"
            "    '''Shared helper.'''\n"
            "    return 1\n",
            encoding="utf-8",
        )
        (tmp_path / "beta.py").write_text(
            "def helper() -> int:\n"
            "    '''Shared helper.'''\n"
            "    return 2\n",
            encoding="utf-8",
        )
        storage_path = str(tmp_path / "idx")
        result = index_folder(path=str(tmp_path), use_ai_summaries=False, storage_path=storage_path)

        planned = plan_turn(
            repo=result["repo"],
            query="helper",
            max_recommended=2,
            storage_path=storage_path,
        )

        assert len(planned["recommended_symbols"]) == 2
        assert {sym["file"] for sym in planned["recommended_symbols"]} == {"alpha.py", "beta.py"}

    def test_max_supplementary_reads_varies(self, tmp_path: Path):
        """Low confidence >= high confidence for max_supplementary_reads."""
        from jcodemunch_mcp.tools.plan_turn import plan_turn
        from tests.conftest_helpers import create_mini_index

        repo, storage_path = create_mini_index(tmp_path)

        # High confidence case
        result_high = plan_turn(repo=repo, query="my_func", storage_path=storage_path)

        # Low confidence case
        result_low = plan_turn(
            repo=repo,
            query="nonexistent_xyz",
            storage_path=storage_path,
        )

        # Low confidence should suggest MORE reads, not fewer
        assert result_low["max_supplementary_reads"] >= result_high["max_supplementary_reads"]

    def test_gap_analysis_is_string(self, tmp_path: Path):
        """gap_analysis is a non-empty string."""
        from jcodemunch_mcp.tools.plan_turn import plan_turn
        from tests.conftest_helpers import create_mini_index

        repo, storage_path = create_mini_index(tmp_path)
        result = plan_turn(repo=repo, query="anything", storage_path=storage_path)

        assert isinstance(result["gap_analysis"], str)
        assert len(result["gap_analysis"]) > 0

    def test_invalid_repo_returns_error(self):
        """Invalid repo returns error dict."""
        from jcodemunch_mcp.tools.plan_turn import plan_turn

        result = plan_turn(repo="nonexistent/repo", query="test", storage_path=None)
        assert "error" in result

    def test_has_meta_timing(self, tmp_path: Path):
        """Result has _meta with timing_ms."""
        from jcodemunch_mcp.tools.plan_turn import plan_turn
        from tests.conftest_helpers import create_mini_index

        repo, storage_path = create_mini_index(tmp_path)
        result = plan_turn(repo=repo, query="my_func", storage_path=storage_path)

        assert "_meta" in result
        assert "timing_ms" in result["_meta"]

    def test_insertion_candidates_on_low_confidence(self, tmp_path: Path):
        """Low confidence should include insertion_candidates."""
        from jcodemunch_mcp.tools.plan_turn import plan_turn
        from tests.conftest_helpers import create_mini_index

        repo, storage_path = create_mini_index(tmp_path)
        result = plan_turn(repo=repo, query="nonexistent_xyz_feature", storage_path=storage_path)

        assert result["confidence"] in ("low", "none")
        if "insertion_candidates" in result:
            for c in result["insertion_candidates"]:
                assert "file" in c
                assert "centrality_score" in c

    def test_prior_evidence_stops_repeat_search(self, tmp_path: Path):
        """A published absence for THIS repo still stops the repeat search (#205).

        ⚠ Rewritten for #711. This test used to inject `record_search(query, 0)`
        alone -- a query string and an integer, with no repository and no
        verdict -- and assert the stop fired. That could only pass while the
        defect existed: the same unscoped record made `plan_turn` deny, in one
        repository, the existence of symbols it was returning from another.
        The mechanism it asserted was the bug; the OUTCOME it asserted is the
        feature, so the outcome is kept and the evidence now arrives through
        the channel that carries a repository. `tests/test_plan_turn_absence_is_repo_scoped.py`
        holds the rest of the rule.
        """
        import time

        from jcodemunch_mcp.tools.plan_turn import plan_turn
        from jcodemunch_mcp.tools.session_journal import get_journal
        from tests.conftest_helpers import create_mini_index

        repo, storage_path = create_mini_index(tmp_path)

        journal = get_journal()
        journal.record_search("nonexistent_xyz_feature", result_count=0)
        journal.record_negative_evidence({
            "query": "nonexistent_xyz_feature",
            "repo": repo,
            "verdict": "no_implementation_found",
            "scanned_symbols": 5,
            "timestamp": time.time(),
        })

        result = plan_turn(repo=repo, query="nonexistent_xyz_feature", storage_path=storage_path)
        assert result["confidence"] == "none"
        assert "prior_evidence" in result
        assert result["prior_evidence"]["previously_searched"] is True
        assert result["prior_evidence"]["times_searched"] >= 1
        assert result["max_supplementary_reads"] == 0

    def test_confidence_none_is_valid(self, tmp_path: Path):
        """'none' is a valid confidence level (stronger than 'low')."""
        from jcodemunch_mcp.tools.plan_turn import plan_turn
        from jcodemunch_mcp.tools.session_journal import get_journal
        from tests.conftest_helpers import create_mini_index

        repo, storage_path = create_mini_index(tmp_path)
        journal = get_journal()
        journal.record_search("zzz_totally_fake_query", result_count=0)

        result = plan_turn(repo=repo, query="zzz_totally_fake_query", storage_path=storage_path)
        assert result["confidence"] in ("none", "low")

    def test_exact_camel_case_query_ranks_matching_symbol_first(self, tmp_path: Path):
        """Exact camelCase queries should surface the matching method first."""
        from jcodemunch_mcp.storage.sqlite_store import _cache_clear
        from jcodemunch_mcp.tools.plan_turn import plan_turn
        from tests.conftest_helpers import create_exact_match_index

        repo, storage_path = create_exact_match_index(tmp_path)
        _cache_clear()

        result = plan_turn(repo=repo, query="renderRow", storage_path=storage_path)

        assert result["recommended_symbols"]
        assert result["recommended_symbols"][0]["name"] == "renderRow"


class TestPlanTurnActionField:
    """Tests for action field in plan_turn on low/none confidence."""

    def test_action_field_on_low_confidence(self, tmp_path: Path):
        """plan_turn returns action=STOP_AND_REPORT_GAP on low confidence."""
        from jcodemunch_mcp.tools.plan_turn import plan_turn
        from tests.conftest_helpers import create_mini_index

        repo, storage_path = create_mini_index(tmp_path)

        result = plan_turn(
            repo=repo,
            query="completely_nonexistent_xyz",
            storage_path=storage_path,
        )

        assert result["confidence"] in ("low", "none")
        assert result.get("action") == "STOP_AND_REPORT_GAP"

    def test_no_action_field_on_high_confidence(self, tmp_path: Path):
        """plan_turn does NOT include action field on high confidence."""
        from jcodemunch_mcp.tools.plan_turn import plan_turn
        from tests.conftest_helpers import create_mini_index

        repo, storage_path = create_mini_index(tmp_path)

        result = plan_turn(
            repo=repo,
            query="my_func",
            storage_path=storage_path,
        )

        assert "action" not in result
