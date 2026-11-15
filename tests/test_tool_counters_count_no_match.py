"""The tool counters say how many calls found nothing.

Reported by a long-time customer (item 5 of their report): `_tool_counters.json`
counted a search for a name nothing carries as one more ordinary call, so the
usage figures `diagnose` reports overstated the calls that answered anything.
1.108.409 (#1308) marks such an answer in its body with `negative_evidence`,
the channel a default install keeps. The counters now read that marker and
keep a third per-tool count, `no_match`, beside `calls` and `tokens_returned`.

⚠ The marker is not the test: #1308 also sets it on answers that RETURNED rows
(`no_exact_match`, `low_confidence_matches`), so only an absence verdict counts
(review, 2026-11-15).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import tool_counters


@pytest.fixture()
def store(tmp_path, monkeypatch):
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("CODE_INDEX_PATH", str(root))
    monkeypatch.setattr(tool_counters, "_state", tool_counters._State())
    return root


def _counters(root: Path) -> dict:
    tool_counters.flush()
    return json.loads((root / tool_counters.FILE).read_text(encoding="utf-8"))


def test_record_counts_a_no_match_beside_the_call(store):
    tool_counters.record("search_symbols", 40, str(store), no_match=True)
    tool_counters.record("search_symbols", 40, str(store))
    row = _counters(store)["tools"]["search_symbols"]
    assert row == {"calls": 2, "tokens_returned": 20, "no_match": 1}


def test_a_file_written_before_the_count_reads_as_zero_and_keeps_adding(store):
    (store / tool_counters.FILE).write_text(
        json.dumps({"version": 1, "since": "2026-10-01", "estimator": "bytes/4",
                    "tools": {"search_symbols": {"calls": 5, "tokens_returned": 9}}}),
        encoding="utf-8",
    )
    assert tool_counters.read(str(store))["tools"]["search_symbols"]["no_match"] == 0
    tool_counters.record("search_symbols", 0, str(store), no_match=True)
    row = _counters(store)["tools"]["search_symbols"]
    assert row == {"calls": 6, "tokens_returned": 9, "no_match": 1}


def test_a_count_that_could_not_be_written_keeps_its_no_match(store, monkeypatch):
    tool_counters.record("search_symbols", 0, str(store), no_match=True)
    with monkeypatch.context() as m:
        m.setattr(tool_counters, "_merge_into_file", lambda *a, **k: False)
        tool_counters.flush()
    assert _counters(store)["tools"]["search_symbols"]["no_match"] == 1


@pytest.fixture()
def repo(store, tmp_path):
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.search_symbols import _result_cache

    proj = tmp_path / "proj"
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "table.py").write_text(
        "def symbol_table():\n    return {}\n\n\ndef all_items():\n    return []\n", encoding="utf-8"
    )
    res = index_folder(str(proj), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True
    _result_cache.clear()
    yield res["repo"]
    _result_cache.clear()


ABSENT = "qqqqzzzzxxxx"  # no row at all: `no_implementation_found`
FUZZY = "zzqqxxNotARealSymbolAtAll"  # rows returned, none exact: `no_exact_match`


def _search(repo: str, query: str, fmt: str = "json", clear: bool = True) -> dict:
    from jcodemunch_mcp import server
    from jcodemunch_mcp.tools.search_symbols import _result_cache

    if clear:
        _result_cache.clear()
    out = asyncio.run(server.call_tool("search_symbols", {"repo": repo, "query": query, "format": fmt}))
    text = out[0].text
    return json.loads(text) if fmt == "json" else {"text": text}


def test_only_an_answer_that_found_nothing_counts(store, repo):
    found = _search(repo, "symbol_table")
    fuzzy = _search(repo, FUZZY)
    nothing = _search(repo, ABSENT)
    assert "negative_evidence" not in found
    # The fixture must hold both shapes, or the test cannot tell them apart.
    assert fuzzy["negative_evidence"]["verdict"] == "no_exact_match" and fuzzy["results"]
    assert nothing["negative_evidence"]["verdict"] == "no_implementation_found" and not nothing["results"]
    row = _counters(store)["tools"]["search_symbols"]
    assert row["calls"] == 3
    assert row["no_match"] == 1


@pytest.mark.parametrize(
    "evidence, counts",
    [
        ({"verdict": "no_implementation_found"}, True),
        ({"verdict": "no_exact_match"}, False),
        ({"verdict": "low_confidence_matches"}, False),
        ({}, False),
        (None, False),
        ("no_implementation_found", False),
    ],
)
def test_the_rule_is_the_absence_verdict(evidence, counts):
    from jcodemunch_mcp import server

    assert server._is_absence_answer({"negative_evidence": evidence}) is counts


def test_a_compact_answer_is_counted_like_a_json_one(store, repo):
    out = _search(repo, ABSENT, fmt="compact")
    assert out["text"]
    assert _counters(store)["tools"]["search_symbols"]["no_match"] == 1


def test_a_cached_answer_is_counted_again(store, repo):
    _search(repo, ABSENT)
    _search(repo, ABSENT, clear=False)
    row = _counters(store)["tools"]["search_symbols"]
    assert row["calls"] == 2 and row["no_match"] == 2


def test_a_relayed_call_counts_its_no_match_under_the_tool_that_answered(store, repo):
    from jcodemunch_mcp import server
    from jcodemunch_mcp.tools.search_symbols import _result_cache

    _result_cache.clear()
    asyncio.run(server.call_tool("order", {"action": "search_symbols", "args": {"repo": repo, "query": ABSENT}}))
    tools = _counters(store)["tools"]
    assert tools["search_symbols"]["no_match"] == 1
    assert tools["order"]["no_match"] == 0


def test_a_refused_answer_is_not_a_no_match(store, repo, monkeypatch):
    from jcodemunch_mcp import server

    monkeypatch.setattr(server, "_enforce_response_cap", lambda name, result: server._error_call_result("refused"))
    _search_raw = asyncio.run(server.call_tool("search_symbols", {"repo": repo, "query": ABSENT, "format": "json"}))
    assert getattr(_search_raw, "isError", False)
    assert _counters(store)["tools"]["search_symbols"]["no_match"] == 0


def test_a_tool_whose_answer_has_no_marker_counts_none(store):
    from jcodemunch_mcp import server

    asyncio.run(server.call_tool("list_repos", {}))
    assert _counters(store)["tools"]["list_repos"]["no_match"] == 0


def test_diagnose_reports_the_count_and_its_list_allows_it(store, repo):
    from jcodemunch_mcp import diagnose

    _search(repo, ABSENT)
    tool_counters.flush()
    report = diagnose.build_report(storage_path=str(store), policy_paths={}, known_tools=("search_symbols",))
    diagnose.check_allowed(report, known_tools=("search_symbols",))
    assert report["tools"]["by_tool"]["search_symbols"]["no_match"] == 1
