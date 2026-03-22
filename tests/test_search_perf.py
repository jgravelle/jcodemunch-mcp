"""Regression + performance test for BM25 search optimizations.

Run with: python -m pytest tests/test_search_perf.py -v -s
"""
import sys
import time

sys.path.insert(0, "src")

from jcodemunch_mcp.tools.search_symbols import search_symbols

REPO = "jcodemunch-mcp"
QUERIES = ["editor", "search_symbols", "save", "symbol", "tokenize", "CodeIndex"]


def _search(query, max_results=10):
    return search_symbols(repo=REPO, query=query, max_results=max_results, detail_level="compact")


def test_search_results_stable():
    """Verify search results are identical across two consecutive calls."""
    for q in QUERIES:
        r1 = _search(q)
        r2 = _search(q)
        ids1 = [r["id"] for r in r1["results"]]
        ids2 = [r["id"] for r in r2["results"]]
        assert ids1 == ids2, f"Query '{q}': results differ between calls"
        scores1 = [round(r["score"], 6) for r in r1["results"]]
        scores2 = [round(r["score"], 6) for r in r2["results"]]
        assert scores1 == scores2, f"Query '{q}': scores differ between calls"


def test_warm_search_faster_than_cold():
    """Second search should be faster than first (cache hit)."""
    # Cold
    t0 = time.perf_counter()
    _search("symbol")
    cold_ms = (time.perf_counter() - t0) * 1000

    # Warm
    t0 = time.perf_counter()
    _search("symbol")
    warm_ms = (time.perf_counter() - t0) * 1000

    print(f"\n  Cold: {cold_ms:.1f}ms  Warm: {warm_ms:.1f}ms  Delta: {cold_ms - warm_ms:.1f}ms")
    assert warm_ms < cold_ms, f"Warm ({warm_ms:.1f}ms) not faster than cold ({cold_ms:.1f}ms)"


def test_search_result_snapshot():
    """Capture result IDs for known queries — fails if ranking changes."""
    results = {}
    for q in QUERIES:
        r = _search(q)
        results[q] = [entry["id"] for entry in r["results"]]

    # Print snapshot for manual review / updating after intentional changes
    for q, ids in results.items():
        print(f"\n  '{q}' top-{len(ids)}:")
        for i, sym_id in enumerate(ids):
            print(f"    {i+1}. {sym_id}")
