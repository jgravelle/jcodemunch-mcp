"""Measure menu()/route() recall over the live jCodeMunch catalog.

Answers one question: when a user phrases a task in their own words, does the
Counter's front door propose the action that answers it?

This gates whether ``tool_surface=counter`` can become the default. The Counter
avoids most of the resident schema tokens -- the exact share is printed at the
top of every run, computed from ``benchmarks/schema_baseline.json`` -- and the
cost it trades against is recall, because an action ``route`` never proposes is
functionally absent. That cost has never been measured.

⚠ This docstring asserted a hand-typed figure until 2026-08-14, when the arm was
measured for the first time and came back lower. The number is now READ from the
frozen baseline rather than written here, per maintenance practice #4: this file
is a recall harness, so a schema-cost figure in it is a transcription with no run
behind it and nothing that fails when it drifts. The old literal is deliberately
not repeated in this note -- ``tests/test_schema_budget.py`` fails on any
schema-saving percentage appearing in this file, including a historical one.
⚠⚠ The direction is why it mattered: overstating the saving makes the recall
trade this harness exists to price look cheaper than it is.

Two paths are measured separately because they are different code:

  menu(query)  -> counter.search_catalog(rows, query, limit)
                  Pure idf-weighted lexical ranking over name + description.

  route(task)  -> counter.classify_intent(task, names)  [curated regex rules]
                  falling back to search_catalog(rows, task, 3) when no rule
                  fires (server.py, the ``if not recs`` branch). The fallback
                  is capped at 3, so route's ceiling on unmatched tasks is
                  recall@3 by construction -- not a tunable in this harness.

LEAKAGE: a corpus written by paraphrasing tool descriptions scores high for the
wrong reason. Every query carries a measured leakage score so that failure mode
is visible in the output rather than asserted in a comment.

Run:
    PYTHONPATH=src python benchmarks/route_recall/run_route_recall.py
    PYTHONPATH=src python benchmarks/route_recall/run_route_recall.py --write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "queries.json"
RESULTS = HERE / "results.json"

SCHEMA_BASELINE = HERE.parent / "schema_baseline.json"

MENU_KS = (1, 3, 5, 10)
ROUTE_KS = (1, 3)
MENU_LIMIT = 10
ROUTE_FALLBACK_LIMIT = 3  # mirrors server.py; changing it here measures fiction


def _load_catalog():
    """Live catalog rows + names, straight from the server. No fixtures."""
    sys.argv = [sys.argv[0]]  # server module inspects argv on import
    from jcodemunch_mcp import counter as _counter
    from jcodemunch_mcp.server import _catalog_names, _catalog_rows

    return _counter, _catalog_rows(), sorted(_catalog_names())


def _schema_saving() -> str:
    """What the Counter buys, read from the frozen baseline rather than typed.

    The recall this harness measures is the price of that saving, so the two
    belong on screen together. A missing or partial baseline returns a plain
    'not measured' -- it must never fall back to a remembered figure, which is
    the exact defect this replaced (the docstring said ~98%; it is lower).
    """
    try:
        base = json.loads(SCHEMA_BASELINE.read_text(encoding="utf-8"))
        full, counter = base["full_full"], base["counter_full"]
    except (OSError, ValueError, KeyError):
        return ("schema saving: not measured -- run "
                "benchmarks/harness/capture_schema_baseline.py")
    return (
        f"schema saving: {100 * (full - counter) / full:.1f}% "
        f"({counter} tokens at counter vs {full} at the default surface, "
        f"cl100k_base, from {SCHEMA_BASELINE.name})"
    )


def _rank_of_target(ordered_actions, targets):
    """1-based rank of the first acceptable action, or None if absent."""
    for i, action in enumerate(ordered_actions, start=1):
        if action in targets:
            return i
    return None


def _leakage(counter, query, targets, desc_by_name):
    """How much of the target's own vocabulary the query hands back.

    name_overlap is the damning one: the fraction of the target action's own
    name tokens that appear verbatim in the query. A query containing the tool's
    name is not measuring retrieval, it is measuring an exact-match lookup.
    """
    qt = set(counter._tokens(query))
    worst_name = 0.0
    worst_desc = 0.0
    for t in targets:
        name_toks = set(counter._tokens(t))
        desc_toks = set(counter._tokens(desc_by_name.get(t, "")))
        if name_toks:
            worst_name = max(worst_name, len(qt & name_toks) / len(name_toks))
        if qt:
            worst_desc = max(worst_desc, len(qt & desc_toks) / len(qt))
    return round(worst_name, 3), round(worst_desc, 3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write results.json")
    ap.add_argument(
        "--corpus", default=str(CORPUS),
        help="query corpus to score (default queries.json; holdout.json is the "
             "HELD-OUT set frozen before the routing fixes)",
    )
    ap.add_argument(
        "--out", default=None,
        help="results path for --write (default results.json, or "
             "<corpus stem>_results.json for a non-default corpus)",
    )
    args = ap.parse_args()

    print(_schema_saving())

    counter, rows, names = _load_catalog()
    name_set = set(names)
    desc_by_name = {r["action"]: r.get("_description", r.get("summary", "")) for r in rows}

    corpus_path = Path(args.corpus)
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    queries = corpus["queries"]
    if corpus_path != CORPUS:
        print(f"corpus: {corpus_path.name}  ({len(queries)} queries)")

    # ---- Validate the corpus BEFORE measuring anything ---------------------
    # A misspelled target silently scores 0 and reads as a router failure. That
    # is a corpus bug wearing a result's clothes, so it is fatal, not a warning.
    bad = []
    for entry in queries:
        for t in entry["targets"]:
            if t not in name_set:
                bad.append((entry["q"], t))
    if bad:
        print("CORPUS INVALID -- target actions absent from the live catalog:")
        for q, t in bad:
            print(f"  {t!r}  (query: {q!r})")
        print("\nFix the corpus. Refusing to report a recall number built on it.")
        return 2

    per_query = []
    for entry in queries:
        q, targets = entry["q"], set(entry["targets"])

        menu_hits = [r["action"] for r in counter.search_catalog(rows, q, MENU_LIMIT)]
        menu_rank = _rank_of_target(menu_hits, targets)

        recs = [r["action"] for r in counter.classify_intent(q, names)]
        rule_fired = bool(recs)
        if not recs:  # mirrors server.py's fallback branch
            recs = [
                r["action"]
                for r in counter.search_catalog(rows, q, ROUTE_FALLBACK_LIMIT)
            ]
        route_rank = _rank_of_target(recs, targets)

        leak_name, leak_desc = _leakage(counter, q, targets, desc_by_name)
        per_query.append(
            {
                "query": q,
                "group": entry.get("group", "?"),
                "targets": sorted(targets),
                "menu_rank": menu_rank,
                "route_rank": route_rank,
                "route_path": "rule" if rule_fired else "fallback",
                "menu_top3": menu_hits[:3],
                "route_proposed": recs,
                "leak_name": leak_name,
                "leak_desc": leak_desc,
            }
        )

    n = len(per_query)

    def _recall(field, k):
        hit = sum(1 for r in per_query if r[field] is not None and r[field] <= k)
        return round(100.0 * hit / n, 1)

    summary = {
        "queries": n,
        "catalog_actions": len(names),
        "menu_recall": {f"@{k}": _recall("menu_rank", k) for k in MENU_KS},
        "route_recall": {f"@{k}": _recall("route_rank", k) for k in ROUTE_KS},
        "route_path_split": {
            "rule": sum(1 for r in per_query if r["route_path"] == "rule"),
            "fallback": sum(1 for r in per_query if r["route_path"] == "fallback"),
        },
        "route_misses": [r["query"] for r in per_query if r["route_rank"] is None],
        "menu_misses": [r["query"] for r in per_query if r["menu_rank"] is None],
        "leakage": {
            "mean_name_overlap": round(sum(r["leak_name"] for r in per_query) / n, 3),
            "mean_desc_overlap": round(sum(r["leak_desc"] for r in per_query) / n, 3),
            "queries_containing_target_name_token": sum(
                1 for r in per_query if r["leak_name"] > 0.0
            ),
            "high_leak_queries": [
                r["query"] for r in per_query if r["leak_name"] >= 0.5
            ],
        },
    }

    print(json.dumps(summary, indent=2))

    # Per-group recall -- shows WHERE the router is blind, not just how often.
    groups = sorted({r["group"] for r in per_query})
    print("\nper-group route recall@3 / menu recall@3:")
    for g in groups:
        rs = [r for r in per_query if r["group"] == g]
        rr = sum(1 for r in rs if r["route_rank"] is not None and r["route_rank"] <= 3)
        mr = sum(1 for r in rs if r["menu_rank"] is not None and r["menu_rank"] <= 3)
        print(f"  {g:10s} n={len(rs):2d}  route {100*rr/len(rs):5.1f}%   menu {100*mr/len(rs):5.1f}%")

    if args.write:
        if args.out:
            out = Path(args.out)
        elif corpus_path == CORPUS:
            out = RESULTS
        else:
            out = HERE / f"{corpus_path.stem}_results.json"
        out.write_text(
            json.dumps({"summary": summary, "per_query": per_query}, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
