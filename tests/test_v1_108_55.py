"""v1.108.55 — issue batch: jcm#327, jcm#328, jcm#329.

#327: the turn-budget idle-gap reset only fired lazily inside record_output
(post-dispatch), so every reader that runs during tool execution — plan_turn's
budget advisor via percent_used(), should_compact() — reported the previous
turn's exhausted counter after an idle gap. The reset now fires in every
reader.

#328: token_budget packing charged each symbol's source-body byte_length even
in compact/standard mode, admitting "as many rows as fit budget_bytes of
source code" (84 rows observed for max_results=18) and reporting tokens_used
that described code nobody received. Packing and tokens_used now charge the
actual per-row payload cost. Also: indexes built without an AI summarizer
persisted the signature (truncated) as the summary, duplicating it in every
standard row; such echo-summaries are now emitted empty.

#329: search_text's 200-char regex cap (and 500-char plain cap) were
undocumented, and the rejection ran AFTER pre-dispatch strict-freshness +
auto-watch work (29s observed). Validation is factored into
validate_query_args() and hoisted into call_tool ahead of that work.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols
from jcodemunch_mcp.tools.search_text import validate_query_args
from jcodemunch_mcp.tools.turn_budget import TurnBudget

BYTES_PER_TOKEN = 4


def _seed_repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    body = "\n".join(f"    x{i} = {i}" for i in range(80))  # hefty source body
    code = ""
    for name in [
        "alpha", "alphabet", "alphanumeric", "alphonse", "alpine", "album",
        "alpha_one", "alpha_two", "alpha_three", "alpha_four",
    ]:
        code += f"def {name}(arg_one, arg_two, arg_three):\n{body}\n\n"
    (src / "a.py").write_text(code)
    idx = index_folder(path=str(tmp_path), use_ai_summaries=False, storage_path=str(tmp_path / "idx"))
    return idx["repo"], str(tmp_path / "idx")


# ---------------------------------------------------------------------------
# jcm#327 — turn budget resets in every reader
# ---------------------------------------------------------------------------

class TestTurnBudgetReaderReset:
    def _exhausted_budget(self) -> TurnBudget:
        tb = TurnBudget(turn_budget_tokens=20000, turn_gap_seconds=30.0)
        tb.record_output(28674)  # the field report's exhausted counter
        return tb

    def test_percent_used_resets_after_idle_gap(self):
        tb = self._exhausted_budget()
        assert tb.percent_used() > 1.4  # 143.4% — stale reading pre-fix
        tb._last_call_ts -= 120  # simulate a 2-minute idle gap
        assert tb.percent_used() == 0.0

    def test_should_compact_resets_after_idle_gap(self):
        tb = self._exhausted_budget()
        assert tb.should_compact() is True
        tb._last_call_ts -= 120
        assert tb.should_compact() is False

    def test_reader_reset_does_not_mark_activity(self):
        # Two readers after the gap must BOTH see the fresh turn: the reset
        # must not advance _last_call_ts (only record_output marks activity).
        tb = self._exhausted_budget()
        tb._last_call_ts -= 120
        assert tb.percent_used() == 0.0
        assert tb.percent_used() == 0.0
        assert tb.should_compact() is False

    def test_within_turn_counter_still_accumulates(self):
        tb = TurnBudget(turn_budget_tokens=20000, turn_gap_seconds=30.0)
        tb.record_output(5000)
        tb.record_output(5000)
        assert tb.percent_used() == pytest.approx(0.5)

    def test_record_output_reset_unchanged(self):
        tb = self._exhausted_budget()
        tb._last_call_ts -= 120
        info = tb.record_output(1000)
        assert info["turn_tokens_used"] == 1000


# ---------------------------------------------------------------------------
# jcm#328 — token_budget charges payload, summary echo dropped
# ---------------------------------------------------------------------------

class TestTokenBudgetPayloadPacking:
    def test_compact_payload_tracks_budget(self, tmp_path):
        repo, storage = _seed_repo(tmp_path)
        budget = 200  # tokens — small enough to force packing decisions
        result = search_symbols(
            repo=repo, query="alpha", detail_level="compact",
            token_budget=budget, max_results=5, storage_path=storage,
        )
        assert "error" not in result
        rows = result.get("results", [])
        assert rows, "expected at least one packed row"
        payload = sum(len(json.dumps(e, default=str)) for e in rows)
        assert payload <= budget * BYTES_PER_TOKEN, (
            f"compact payload {payload}B exceeds budget {budget * BYTES_PER_TOKEN}B"
        )
        # Pre-fix, each ~hefty symbol charged its source byte_length, so a
        # 200-token budget admitted at most 1 row despite ~70B compact rows.
        assert len(rows) > 1, "packer still charging source-body bytes, not payload"

    def test_tokens_used_reflects_payload(self, tmp_path):
        repo, storage = _seed_repo(tmp_path)
        budget = 200
        result = search_symbols(
            repo=repo, query="alpha", detail_level="compact",
            token_budget=budget, max_results=5, storage_path=storage,
        )
        rows = result.get("results", [])
        # Annotations (_freshness, match_type, ...) are added AFTER the meta
        # is computed, so the response rows are an upper bound on what the
        # packer charged.
        payload_upper = sum(len(json.dumps(e, default=str)) for e in rows)
        meta = result.get("_meta", {})
        assert 0 < meta.get("tokens_used", 0) <= payload_upper // BYTES_PER_TOKEN
        assert meta.get("tokens_used", 10**9) <= budget
        # Pre-fix tokens_used was source-body bytes — vastly larger.
        source_tokens = sum(e.get("byte_length", 0) for e in rows) // BYTES_PER_TOKEN
        assert meta.get("tokens_used") < source_tokens

    def test_full_mode_packing_unchanged(self, tmp_path):
        # §1.2 contract: full mode still packs on materialized byte_length.
        repo, storage = _seed_repo(tmp_path)
        budget = 500
        result = search_symbols(
            repo=repo, query="alpha", detail_level="full",
            token_budget=budget, max_results=20, storage_path=storage,
        )
        assert "error" not in result
        used_bytes = sum(e.get("byte_length", 0) for e in result.get("results", []))
        assert used_bytes <= budget * BYTES_PER_TOKEN

    def test_fallback_summary_not_duplicated(self, tmp_path):
        # Index built with use_ai_summaries=False → function summaries are
        # signature_fallback output (sig[:120]). Standard rows must not echo it.
        repo, storage = _seed_repo(tmp_path)
        result = search_symbols(
            repo=repo, query="alpha", detail_level="standard",
            max_results=10, storage_path=storage,
        )
        assert result.get("result_count", 0) > 0
        for entry in result["results"]:
            sig = entry.get("signature", "")
            summary = entry.get("summary", "")
            assert summary != sig[:120] or summary == "", (
                f"summary echoes signature for {entry.get('name')}: {summary!r}"
            )


# ---------------------------------------------------------------------------
# jcm#329 — search_text argument validation: documented + pre-dispatch
# ---------------------------------------------------------------------------

class TestSearchTextValidation:
    def test_long_regex_rejected(self):
        err = validate_query_args("a" * 204, is_regex=True)
        assert err is not None and "204" in err["error"] and "200" in err["error"]

    def test_long_plain_query_rejected(self):
        err = validate_query_args("a" * 501, is_regex=False)
        assert err is not None and "500" in err["error"]

    def test_nested_quantifier_rejected(self):
        err = validate_query_args("(a+)+", is_regex=True)
        assert err is not None and "quantifier" in err["error"]

    def test_invalid_regex_rejected(self):
        err = validate_query_args("[unclosed", is_regex=True)
        assert err is not None and "Invalid regex" in err["error"]

    def test_valid_args_pass(self):
        assert validate_query_args("estimateToken|tokenEstimat", is_regex=True) is None
        assert validate_query_args("plain text", is_regex=False) is None

    def test_call_tool_rejects_before_predispatch_work(self, monkeypatch):
        """An over-long regex must be rejected before strict-freshness waits
        and auto-watch reindexing ever run (the 29s field failure)."""
        import jcodemunch_mcp.server as srv

        touched = []

        def fake_freshness(*a, **k):
            touched.append("freshness")

        async def fake_auto_watch(*a, **k):
            touched.append("auto_watch")

        monkeypatch.setattr(srv, "await_freshness_if_strict", fake_freshness)
        monkeypatch.setattr(srv, "_auto_watch_if_needed", fake_auto_watch)

        out = asyncio.run(srv.call_tool(
            "search_text",
            {"repo": "some/repo", "query": "a" * 204, "is_regex": True},
        ))
        text = out.content[0].text  # arg-validation error → CallToolResult (isError, F-P01)
        assert out.is_error is True
        assert "Regex too long" in text
        assert touched == [], f"pre-dispatch work ran before validation: {touched}"
