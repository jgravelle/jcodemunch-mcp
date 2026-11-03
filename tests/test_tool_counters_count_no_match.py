"""The tool counters say how many calls found nothing (BACKLOG 74).

Reported by a long-time customer (item 5 of their report): `_tool_counters.json`
counted a search for a name nothing carries as one more ordinary call, so the
usage figures `diagnose` reports overstated the calls that answered anything.
1.108.409 (#1308) marks such an answer in its body with `negative_evidence`,
the channel a default install keeps. The counters now read that marker and
keep a third per-tool count, `no_match`, beside `calls` and `tokens_returned`.
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


def _search(repo: str, query: str) -> dict:
    from jcodemunch_mcp import server
    from jcodemunch_mcp.tools.search_symbols import _result_cache

    _result_cache.clear()
    out = asyncio.run(server.call_tool("search_symbols", {"repo": repo, "query": query, "format": "json"}))
    return json.loads(out[0].text)


def test_the_dispatcher_counts_an_answer_that_carries_the_marker(store, repo):
    found = _search(repo, "symbol_table")
    missed = _search(repo, "zzqqxxNotARealSymbolAtAll")
    nothing = _search(repo, "qqqqzzzzxxxx")
    assert "negative_evidence" not in found
    assert missed.get("negative_evidence"), "the query must carry #1308's marker for this test to mean anything"
    assert nothing.get("negative_evidence")
    row = _counters(store)["tools"]["search_symbols"]
    assert row["calls"] == 3
    assert row["no_match"] == 2


def test_a_tool_whose_answer_has_no_marker_counts_none(store):
    from jcodemunch_mcp import server

    asyncio.run(server.call_tool("list_repos", {}))
    assert _counters(store)["tools"]["list_repos"]["no_match"] == 0


def test_diagnose_reports_the_count_and_its_list_allows_it(store, repo):
    from jcodemunch_mcp import diagnose

    _search(repo, "zzqqxxNotARealSymbolAtAll")
    tool_counters.flush()
    report = diagnose.build_report(storage_path=str(store), policy_paths={}, known_tools=("search_symbols",))
    diagnose.check_allowed(report, known_tools=("search_symbols",))
    assert report["tools"]["by_tool"]["search_symbols"]["no_match"] == 1
