"""L-180: a text-search-style tool earns no savings credit in either model.

A text-search-style tool is one whose answer IS the matching lines, which an
agent with Grep would get just as well. J's ruling (2026-10-17): no credit for
these until a measurement supports one, in the ``receipt`` CLI and in the live
meter, and the receipt's line is ``Modeled gross savings``.

Every assertion here is per tool, never a total: a total can stay plausible
while one tool keeps a credit.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

import pytest

from jcodemunch_mcp import savings_credit
from jcodemunch_mcp.cli import receipt
from jcodemunch_mcp.storage import token_tracker

TEXT_SEARCH = sorted(savings_credit.TEXT_SEARCH_STYLE_TOOLS)


def test_the_two_named_tools_are_text_search_style():
    assert {"search_text", "check_references"} <= set(TEXT_SEARCH)


def test_every_entry_names_its_reason():
    for table in (savings_credit.TEXT_SEARCH_STYLE_TOOLS, savings_credit.NOT_TEXT_SEARCH_STYLE):
        for tool, reason in table.items():
            assert isinstance(reason, str) and len(reason.split()) >= 6, tool


# --- the receipt -----------------------------------------------------------


@pytest.mark.parametrize("tool", TEXT_SEARCH)
def test_receipt_credits_nothing(tool):
    agg = receipt.aggregate([{"tool": tool, "result_tokens": 1000, "timestamp": "", "session_file": "x"}])
    bucket = agg["per_tool"][tool]
    assert bucket["savings_tokens"] == 0
    assert bucket["baseline_tokens"] == bucket["actual_tokens"] == 1000


@pytest.mark.parametrize("tool", TEXT_SEARCH)
def test_receipt_by_day_credits_nothing(tool):
    rows = receipt.aggregate_by_day(
        [{"tool": tool, "result_tokens": 1000, "timestamp": "2026-10-17T12:00:00Z"}], model="opus"
    )
    assert [r["savings_tokens"] for r in rows] == [0]


@pytest.mark.parametrize("tool", TEXT_SEARCH)
def test_no_multiplier_is_listed_for_a_text_search_tool(tool):
    # A listed multiplier is a credit someone can read off --explain; the
    # ruling is that none exists until a measurement supports one.
    assert tool not in receipt._TOOL_MULTIPLIERS


def test_a_credited_tool_still_earns_its_multiplier():
    # Non-vacuity: the zero is per tool, not a broken aggregate.
    agg = receipt.aggregate([{"tool": "search_symbols", "result_tokens": 1000, "timestamp": "", "session_file": "x"}])
    assert agg["per_tool"]["search_symbols"]["savings_tokens"] == int(1000 * receipt._TOOL_MULTIPLIERS["search_symbols"]) - 1000


def _transcript(path: Path, name: str, tool_input: dict, result_text: str) -> None:
    events = [
        {
            "type": "assistant",
            "timestamp": "2026-10-17T12:00:00Z",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu1", "name": name, "input": tool_input},
            ]},
        },
        {
            "type": "user",
            "timestamp": "2026-10-17T12:00:00Z",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu1", "content": result_text},
            ]},
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


@pytest.mark.parametrize("tool", TEXT_SEARCH)
def test_a_text_search_through_the_front_door_credits_nothing(tmp_path, tool):
    # On the `counter` surface the call is `order(action=...)`; without
    # reading the action the receipt priced it at the default multiplier.
    _transcript(tmp_path / "p" / "s.jsonl", "mcp__jcodemunch__order",
                {"action": tool, "args": {"repo": "r", "query": "x"}}, "y" * 4000)
    agg = receipt.aggregate(receipt.iter_calls(tmp_path))
    assert agg["totals"]["calls"] == 1
    assert agg["totals"]["savings_tokens"] == 0


def test_an_order_for_a_credited_tool_keeps_its_credit(tmp_path):
    _transcript(tmp_path / "p" / "s.jsonl", "mcp__jcodemunch__order",
                {"action": "search_symbols", "args": {"repo": "r", "query": "x"}}, "y" * 4000)
    agg = receipt.aggregate(receipt.iter_calls(tmp_path))
    # Only a text-search action is re-priced; every other `order` keeps the
    # default multiplier it had before L-180 (moving it is outside the ruling).
    actual = 4000 // receipt._BYTES_PER_TOKEN
    assert agg["totals"]["savings_tokens"] == int(actual * receipt._DEFAULT_MULTIPLIER) - actual


def _route_result(action: str) -> list[dict]:
    # server._handle_route's head block, then the routed tool's own output.
    head = json.dumps({"tool": "route", "task": "t", "executed_action": action, "args": {}},
                      separators=(",", ":"))
    return [{"type": "text", "text": head}, {"type": "text", "text": "y" * 4000}]


@pytest.mark.parametrize("tool", TEXT_SEARCH)
def test_a_routed_text_search_credits_nothing(tmp_path, tool):
    # route(execute=true) names the action only in its RESULT, never its input.
    _transcript(tmp_path / "p" / "s.jsonl", "mcp__jcodemunch__route",
                {"task": "where is foo used", "execute": True}, _route_result(tool))
    agg = receipt.aggregate(receipt.iter_calls(tmp_path))
    assert agg["totals"]["calls"] == 1
    assert agg["totals"]["savings_tokens"] == 0


def test_a_routed_credited_tool_keeps_the_default(tmp_path):
    _transcript(tmp_path / "p" / "s.jsonl", "mcp__jcodemunch__route",
                {"task": "find foo", "execute": True}, _route_result("search_symbols"))
    agg = receipt.aggregate(receipt.iter_calls(tmp_path))
    actual = agg["totals"]["actual_tokens"]
    assert agg["totals"]["savings_tokens"] == int(actual * receipt._DEFAULT_MULTIPLIER) - actual


# --- the label -------------------------------------------------------------


def _agg():
    return receipt.aggregate([
        {"tool": "search_symbols", "result_tokens": 1000, "timestamp": "", "session_file": "x"},
        {"tool": "search_text", "result_tokens": 1000, "timestamp": "", "session_file": "x"},
    ])


def test_text_line_reads_modeled_gross_savings():
    out = receipt.render_text(_agg(), days=30, model="opus")
    assert "Modeled gross savings:" in out
    assert "Net savings" not in out


def test_json_carries_the_label():
    payload = json.loads(receipt.render_json(_agg(), model="opus"))
    assert payload["savings_label"] == "Modeled gross savings"
    assert payload["no_credit_tools"] == dict(savings_credit.TEXT_SEARCH_STYLE_TOOLS)


def test_csv_carries_the_label():
    rows = list(csv.DictReader(io.StringIO(receipt.render_csv(_agg()))))
    assert rows and {r["savings_label"] for r in rows} == {"Modeled gross savings"}
    assert {r["tool"]: r["savings_tokens"] for r in rows}["search_text"] == "0"


def test_explain_names_the_label_and_every_zero_credit_tool():
    out = receipt.render_explain()
    assert "Modeled gross savings" in out
    for tool in TEXT_SEARCH:
        # 1.0x, not 0.0x: --explain prints baseline = actual x multiplier, so a
        # reader applying the printed formula must get a saving of zero.
        assert re.search(rf"^\s+{tool}\s+1\.0×\s+no credit:", out, re.M), tool
        assert receipt._multiplier(tool) == 1.0


# --- the live meter --------------------------------------------------------


@pytest.mark.parametrize("tool", TEXT_SEARCH)
def test_record_savings_refuses_credit(monkeypatch, tool):
    seen = []
    monkeypatch.setattr(token_tracker._state, "add", lambda d, b, t=None: seen.append((d, t)) or 0)
    token_tracker.record_savings(5000, tool_name=tool)
    # The call is still counted (the tool name reaches the ledger); only the
    # credit is refused.
    assert seen == [(0, tool)]


def test_record_savings_keeps_credit_for_other_tools(monkeypatch):
    seen = []
    monkeypatch.setattr(token_tracker._state, "add", lambda d, b, t=None: seen.append((d, t)) or 0)
    token_tracker.record_savings(5000, tool_name="search_symbols")
    assert seen == [(5000, "search_symbols")]


def test_search_text_reports_no_saving(tmp_path, monkeypatch):
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.search_text import search_text

    src = tmp_path / "src"
    src.mkdir()
    # A large file with one match: the old meter credited its whole size.
    body = "x = 1\n" * 5000 + "needle = 2\n"
    (src / "big.py").write_text(body, encoding="utf-8")
    store = str(tmp_path / "idx")
    repo = index_folder(path=str(src), use_ai_summaries=False, storage_path=store)["repo"]

    # An index_folder index stores no file sizes (L-179), so on it the old
    # meter already credited 0 and this test could not fail. Serve the index
    # with the size a remote index stores, which is where the old credit came from.
    import jcodemunch_mcp.tools.search_text as st_mod

    class _SizedStore(st_mod.IndexStore):
        def load_index(self, *a, **kw):
            index = super().load_index(*a, **kw)
            if index is not None:
                index.file_sizes = {"big.py": len(body.encode("utf-8"))}
            return index

    monkeypatch.setattr(st_mod, "IndexStore", _SizedStore)

    seen = []
    monkeypatch.setattr(token_tracker._state, "add", lambda d, b, t=None: seen.append((d, t)) or 0)
    result = search_text(repo=repo, query="needle", storage_path=store)
    assert result["result_count"] == 1
    assert result["_meta"]["tokens_saved"] == 0
    assert seen == [(0, "search_text")]


# --- the guard -------------------------------------------------------------


def _catalog():
    from jcodemunch_mcp import server
    return {t.name: (t.description or "") for t in server._raw_catalog_tools()}


def test_a_tool_that_reads_like_a_text_search_is_classified():
    # A new tool whose description reads like a text search cannot pick up the
    # default multiplier silently: it must be decided, with a reason, here.
    catalog = _catalog()
    flagged = {n for n, d in catalog.items() if savings_credit.TEXT_SEARCH_MARKERS.search(d)}
    decided = set(savings_credit.TEXT_SEARCH_STYLE_TOOLS) | set(savings_credit.NOT_TEXT_SEARCH_STYLE)
    assert flagged, "the marker pattern flags nothing; the guard would be vacuous"
    assert set(TEXT_SEARCH) <= flagged, "a text-search tool the markers miss weakens the guard"
    assert sorted(flagged - decided) == []


def test_the_two_tables_are_disjoint_and_name_real_tools():
    catalog = _catalog()
    text, other = set(savings_credit.TEXT_SEARCH_STYLE_TOOLS), set(savings_credit.NOT_TEXT_SEARCH_STYLE)
    assert not text & other
    assert sorted((text | other) - set(catalog)) == []
