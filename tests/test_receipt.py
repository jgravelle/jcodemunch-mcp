"""Tests for the receipt CLI helper (v1.85.0)."""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest

from jcodemunch_mcp.cli.receipt import (
    _BYTES_PER_TOKEN,
    _DEFAULT_MULTIPLIER,
    _MODEL_PRICES_USD_PER_MTOK,
    _TOOL_MULTIPLIERS,
    _result_byte_length,
    aggregate,
    dollar_savings,
    iter_calls,
    lifetime_meter,
    render_csv,
    render_explain,
    render_json,
    render_text,
)


def _write_session(path: Path, events: list[dict]) -> None:
    """Write a synthetic Claude transcript file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def _make_call(tool: str, tu_id: str, result_text: str, ts: str = "2026-05-09T12:00:00Z") -> list[dict]:
    """Synthesize a (tool_use, tool_result) pair as two transcript events."""
    return [
        {
            "type": "assistant",
            "timestamp": ts,
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": tu_id, "name": tool, "input": {}},
                ],
            },
        },
        {
            "type": "user",
            "timestamp": ts,
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tu_id, "content": result_text},
                ],
            },
        },
    ]


class TestResultByteLength:
    def test_string_content(self):
        assert _result_byte_length("hello") == 5

    def test_text_blocks(self):
        content = [
            {"type": "text", "text": "abc"},
            {"type": "text", "text": "defg"},
        ]
        assert _result_byte_length(content) == 7

    def test_non_text_blocks_ignored(self):
        content = [
            {"type": "image", "source": "..."},
            {"type": "text", "text": "ok"},
        ]
        assert _result_byte_length(content) == 2

    def test_none_and_empty(self):
        assert _result_byte_length(None) == 0
        assert _result_byte_length([]) == 0
        assert _result_byte_length("") == 0


class TestIterCalls:
    def test_pairs_tool_use_with_result(self, tmp_path: Path):
        events = _make_call(
            "mcp__jcodemunch__search_symbols",
            "tu_1",
            "x" * 400,
        )
        _write_session(tmp_path / "session1.jsonl", events)

        calls = list(iter_calls(tmp_path))
        assert len(calls) == 1
        assert calls[0]["tool"] == "search_symbols"
        # 400 bytes / 4 bytes/token = 100 tokens.
        assert calls[0]["result_tokens"] == 100

    def test_ignores_non_jcodemunch_tools(self, tmp_path: Path):
        events = (
            _make_call("Read", "tu_1", "irrelevant")
            + _make_call("mcp__claude_ai_Notion__create", "tu_2", "irrelevant")
            + _make_call("mcp__jcodemunch__resolve_repo", "tu_3", "result")
        )
        _write_session(tmp_path / "session.jsonl", events)
        calls = list(iter_calls(tmp_path))
        assert len(calls) == 1
        assert calls[0]["tool"] == "resolve_repo"

    def test_handles_orphan_tool_use_without_result(self, tmp_path: Path):
        """Tool calls that never got a result are silently dropped."""
        events = [
            {
                "type": "assistant",
                "timestamp": "2026-05-09T12:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_orphan", "name": "mcp__jcodemunch__search_symbols", "input": {}},
                    ],
                },
            },
            # No matching tool_result
        ]
        _write_session(tmp_path / "session.jsonl", events)
        assert list(iter_calls(tmp_path)) == []

    def test_tolerates_corrupt_lines(self, tmp_path: Path):
        path = tmp_path / "session.jsonl"
        events = _make_call("mcp__jcodemunch__find_references", "tu_1", "result")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for ev in events[:1]:
                f.write(json.dumps(ev) + "\n")
            f.write("not valid json\n")
            for ev in events[1:]:
                f.write(json.dumps(ev) + "\n")
        calls = list(iter_calls(tmp_path))
        assert len(calls) == 1
        assert calls[0]["tool"] == "find_references"

    def test_returns_empty_for_missing_root(self, tmp_path: Path):
        assert list(iter_calls(tmp_path / "does-not-exist")) == []


class TestAggregate:
    def test_applies_per_tool_multipliers(self):
        # search_symbols multiplier is 20×.
        calls = [
            {"tool": "search_symbols", "result_tokens": 100, "timestamp": "", "session_file": "x"},
            {"tool": "search_symbols", "result_tokens": 200, "timestamp": "", "session_file": "x"},
        ]
        agg = aggregate(calls)
        assert agg["totals"]["calls"] == 2
        assert agg["totals"]["actual_tokens"] == 300
        assert agg["totals"]["baseline_tokens"] == 300 * 20
        assert agg["totals"]["savings_tokens"] == 300 * 19  # baseline - actual

    def test_default_multiplier_for_unknown_tools(self):
        calls = [{"tool": "made_up_tool", "result_tokens": 100, "timestamp": "", "session_file": "x"}]
        agg = aggregate(calls)
        assert agg["totals"]["baseline_tokens"] == 100 * _DEFAULT_MULTIPLIER
        assert agg["per_tool"]["made_up_tool"]["calls"] == 1

    def test_empty_calls_returns_zeros(self):
        agg = aggregate([])
        assert agg["totals"]["calls"] == 0
        assert agg["totals"]["savings_tokens"] == 0


class TestDollarSavings:
    def test_sonnet_rate(self):
        # Sonnet 5 = $2/MTok input → 1M tokens = $2. ⚠ NOT $3: that was the
        # increase scheduled for 2026-09-01 and cancelled the day before, and
        # the superseded Sonnet 4.6's rate. See the ⚠⚠ note on the table.
        assert dollar_savings(1_000_000, "sonnet") == pytest.approx(2.0)

    def test_opus_rate(self):
        # Opus 4.8 / 4.7 / 4.6 = $5/MTok input (retired 4.0/4.1 were $15).
        assert dollar_savings(1_000_000, "opus") == pytest.approx(5.0)

    def test_haiku_rate(self):
        assert dollar_savings(1_000_000, "haiku") == pytest.approx(1.0)

    def test_fable_rate(self):
        assert dollar_savings(1_000_000, "fable") == pytest.approx(10.0)

    def test_unknown_model_zero(self):
        assert dollar_savings(1_000_000, "made-up") == 0.0


class TestRenderText:
    def _simple_agg(self):
        return aggregate([
            {"tool": "search_symbols", "result_tokens": 1000, "timestamp": "", "session_file": "x"},
            {"tool": "find_references", "result_tokens": 500, "timestamp": "", "session_file": "x"},
        ])

    def test_includes_dollar_headline(self):
        agg = self._simple_agg()
        out = render_text(agg, days=30, model="sonnet")
        assert "Sonnet pricing" in out
        # search_symbols: 1000 × 20 = 20000; find_references: 500 × 25 = 12500.
        # savings = (20000 - 1000) + (12500 - 500) = 31000.
        # $2/MTok × 31000 / 1e6 = $0.062 → rounds to $0.06.
        # ⚠ A DERIVED figure, so it moves whenever the rate does — and it is
        # invisible to a search for the rate's name, which is how it survived
        # the same pass that found the two literal 3.0s. Keep the arithmetic
        # in the comment; it is what makes the update mechanical instead of
        # a guess at what the renderer now prints.
        assert "$0.06" in out

    def test_empty_data_message(self):
        out = render_text(aggregate([]), days=30, model="sonnet")
        assert "No jcodemunch tool calls found" in out

    def test_top_tools_table(self):
        out = render_text(self._simple_agg(), days=30, model="sonnet")
        assert "search_symbols" in out
        assert "find_references" in out


class TestExplain:
    def test_lists_every_known_tool(self):
        out = render_explain()
        for tool in _TOOL_MULTIPLIERS:
            assert tool in out

    def test_includes_default_multiplier(self):
        out = render_explain()
        assert f"{_DEFAULT_MULTIPLIER}" in out


class TestExports:
    def _agg(self):
        return aggregate([
            {"tool": "search_symbols", "result_tokens": 1000, "timestamp": "", "session_file": "x"},
        ])

    def test_csv_has_header_and_row(self):
        out = render_csv(self._agg())
        # L-180 appended `savings_label`; the five columns before it are unchanged.
        assert out.splitlines()[0] == "tool,calls,actual_tokens,baseline_tokens,savings_tokens,savings_label"
        assert "search_symbols" in out

    def test_json_payload_shape(self):
        out = render_json(self._agg(), model="sonnet")
        payload = json.loads(out)
        assert payload["model"] == "sonnet"
        assert "savings_usd" in payload
        assert payload["totals"]["calls"] == 1
        assert "search_symbols" in payload["per_tool"]


class TestModelPriceTable:
    # Pinned to platform.claude.com/docs/en/about-claude/pricing as of
    # 2026-09-01. Update this table AND the source table in cli/receipt.py
    # together when the price list changes.
    # ⚠⚠ This restatement is the POINT -- a pin that imports the value it
    # checks asserts nothing. It is also why the wrong sonnet rate survived:
    # both copies said 3.0 and agreed with each other, so the suite was green
    # against a rate the vendor never charged for the model named beside it.
    # **Re-read the source page when touching this, never the other copy.**
    _EXPECTED_RATES = {
        "fable": 10.0,
        "opus": 5.0,
        "sonnet": 2.0,   # Sonnet 5. The $3 here until 2026-09-01 was Sonnet 4.6's.
        "haiku": 1.0,
    }

    def test_known_models_present(self):
        for m in ("fable", "sonnet", "opus", "haiku"):
            assert m in _MODEL_PRICES_USD_PER_MTOK
            assert _MODEL_PRICES_USD_PER_MTOK[m] > 0

    def test_rates_match_dated_source(self):
        for model, rate in self._EXPECTED_RATES.items():
            assert _MODEL_PRICES_USD_PER_MTOK[model] == pytest.approx(rate)

    def test_opus_more_expensive_than_sonnet(self):
        assert _MODEL_PRICES_USD_PER_MTOK["opus"] > _MODEL_PRICES_USD_PER_MTOK["sonnet"]

    def test_haiku_cheaper_than_sonnet(self):
        assert _MODEL_PRICES_USD_PER_MTOK["haiku"] < _MODEL_PRICES_USD_PER_MTOK["sonnet"]


class TestLifetimeMeter:
    def test_reads_savings_file(self, tmp_path: Path):
        (tmp_path / "_savings.json").write_text(
            json.dumps({"total_tokens_saved": 34_317_586_613, "anon_id": "abc"}),
            encoding="utf-8",
        )
        m = lifetime_meter(root=tmp_path)
        assert m["total_tokens_saved"] == 34_317_586_613
        assert m["anon_id"] == "abc"

    def test_missing_file_returns_none(self, tmp_path: Path):
        assert lifetime_meter(root=tmp_path) is None

    def test_zero_returns_none(self, tmp_path: Path):
        (tmp_path / "_savings.json").write_text('{"total_tokens_saved": 0}', encoding="utf-8")
        assert lifetime_meter(root=tmp_path) is None

    def test_corrupt_file_returns_none(self, tmp_path: Path):
        (tmp_path / "_savings.json").write_text("not json", encoding="utf-8")
        assert lifetime_meter(root=tmp_path) is None

    def _agg(self):
        return aggregate([
            {"tool": "search_symbols", "result_tokens": 1000, "timestamp": "", "session_file": "x"},
        ])

    def test_render_text_includes_lifetime_at_input_rate(self):
        meter = {"total_tokens_saved": 34_317_586_613, "anon_id": "x"}
        out = render_text(self._agg(), days=0, model="opus", meter=meter)
        assert "Lifetime savings" in out
        assert "34,317,586,613" in out
        # 34.3B tokens x $5/MTok (Opus INPUT) = $171,587.93 — not the $25 output rate.
        assert "$171,587.93" in out

    def test_render_text_no_meter_omits_lifetime(self):
        out = render_text(self._agg(), days=0, model="opus", meter=None)
        assert "Lifetime savings" not in out

    def test_empty_transcripts_still_surfaces_meter(self):
        meter = {"total_tokens_saved": 34_317_586_613, "anon_id": "x"}
        out = render_text(aggregate([]), days=30, model="opus", meter=meter)
        assert "No jcodemunch tool calls found" in out
        assert "Lifetime savings" in out
        assert "34,317,586,613" in out

    def test_render_json_includes_lifetime(self):
        meter = {"total_tokens_saved": 1_000_000, "anon_id": "x"}
        payload = json.loads(render_json(self._agg(), model="opus", meter=meter))
        assert payload["lifetime"]["tokens_saved"] == 1_000_000
        assert payload["lifetime"]["usd"] == pytest.approx(5.0)

    def test_render_json_no_meter_omits_lifetime(self):
        payload = json.loads(render_json(self._agg(), model="opus", meter=None))
        assert "lifetime" not in payload


class TestServerReceiptModelChoices:
    """The `receipt`/`org-report` subparsers in server.py must derive their
    --model choices from the price table, not hardcode a subset. Guards the
    v1.108.131 regression where `fable` was in the table but rejected by the
    CLI dispatcher's stale hardcoded {sonnet,opus,haiku}."""

    @pytest.mark.parametrize("model", sorted(_MODEL_PRICES_USD_PER_MTOK))
    def test_server_cli_accepts_every_priced_model(self, model: str):
        import subprocess
        import sys

        r = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.argv=['jcm','receipt','--model',"
                f"'{model}','--days','1']; "
                "from jcodemunch_mcp.server import main; main()",
            ],
            capture_output=True,
            text=True,
        )
        combined = r.stdout + r.stderr
        # argparse exits 2 with "invalid choice" for an unlisted --model.
        assert "invalid choice" not in combined, f"{model}: {combined}"
        assert r.returncode != 2, f"{model} exit 2: {combined}"
