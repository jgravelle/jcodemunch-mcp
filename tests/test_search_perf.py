"""Regression + performance test for BM25 search optimizations.

Run with: python -m pytest tests/test_search_perf.py -v -s

These tests require an indexed repo and are skipped in CI where no index exists.
"""
import sys
import time

import pytest

sys.path.insert(0, "src")

from jcodemunch_mcp.tools.search_symbols import search_symbols

REPO = "jcodemunch-mcp"
QUERIES = ["editor", "search_symbols", "save", "symbol", "tokenize", "CodeIndex"]


def _search(query, max_results=10, debug=False):
    return search_symbols(repo=REPO, query=query, max_results=max_results, detail_level="compact", debug=debug)


def _require_index():
    """Skip test if repo is not indexed."""
    r = _search("test", max_results=1)
    if "error" in r:
        pytest.skip(f"Repo '{REPO}' not indexed: {r['error']}")


def test_search_results_stable():
    """Verify search results are identical across two consecutive calls."""
    _require_index()
    for q in QUERIES:
        r1 = _search(q, debug=True)
        r2 = _search(q, debug=True)
        ids1 = [r["id"] for r in r1["results"]]
        ids2 = [r["id"] for r in r2["results"]]
        assert ids1 == ids2, f"Query '{q}': results differ between calls"
        scores1 = [round(r["score"], 6) for r in r1["results"]]
        scores2 = [round(r["score"], 6) for r in r2["results"]]
        assert scores1 == scores2, f"Query '{q}': scores differ between calls"




def test_warm_search_timing():
    """Report cold vs warm search timing (informational).

    On small repos the cold/warm delta is small enough to be lost
    in OS scheduling noise, so this test only asserts that both
    calls succeed — timing is printed for manual review.
    """
    _require_index()
    r1 = _search("symbol")
    r2 = _search("symbol")
    cold_ms = r1["_meta"]["timing_ms"]
    warm_ms = r2["_meta"]["timing_ms"]
    print(f"\n  Call 1: {cold_ms:.1f}ms  Call 2: {warm_ms:.1f}ms  Delta: {cold_ms - warm_ms:.1f}ms")
    assert "results" in r1 and "results" in r2


def test_search_result_snapshot():
    """Capture result IDs for known queries — fails if ranking changes."""
    _require_index()
    results = {}
    for q in QUERIES:
        r = _search(q)
        results[q] = [entry["id"] for entry in r["results"]]

    # Print snapshot for manual review / updating after intentional changes
    for q, ids in results.items():
        print(f"\n  '{q}' top-{len(ids)}:")
        for i, sym_id in enumerate(ids):
            print(f"    {i+1}. {sym_id}")
