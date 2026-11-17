"""Turn route_recall misses into a named-gate defect list.

``run_route_recall.py`` measures THAT route missed. This says WHY, using the
same ``counter`` functions the live front door uses, so the two artifacts cannot
drift into disagreeing about the same run.

The distinction that makes it worth running: a recall number treats every miss
as one kind of failure, and they are not. A miss where the action was never
scored (a curated rule fired for something else and rules short-circuit the
lexical fallback) looks identical in the recall figure to a miss where it scored
and lost by a point. Tuning weights fixes the second and cannot touch the first.

Run:
    PYTHONPATH=src python benchmarks/route_recall/explain_misses.py
    PYTHONPATH=src python benchmarks/route_recall/explain_misses.py --write
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results.json"
OUT = HERE / "explanations.json"

#: What each gate means for the person reading the list, in fix-cost order.
GATE_ADVICE = {
    "rule_preempted": (
        "NEVER SCORED. A curated rule claimed the query for a different action "
        "and the lexical fallback did not run. Ranking work cannot touch these."
    ),
    "no_lexical_overlap": (
        "STRUCTURALLY UNREACHABLE. Zero shared tokens with the action's name or "
        "description, so the score is zero at any weight. Needs vocabulary in "
        "the description, or a curated rule."
    ),
    "ranked_below_cutoff": (
        "SCORED AND LOST. The only bucket where ranking work helps."
    ),
    "catalog_absent": (
        "NOT CALLABLE under the active surface. A guidance/surface mismatch "
        "(#397), not a retrieval problem."
    ),
    "empty_query": (
        "NOTHING TO RANK. Every token was a stopword; only a rule can serve it."
    ),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help=f"write {OUT.name}")
    ap.add_argument("--results", default=str(RESULTS))
    args = ap.parse_args()

    results_path = Path(args.results)
    if not results_path.exists():
        print(f"no results at {results_path}; run run_route_recall.py --write first")
        return 1

    sys.argv = [sys.argv[0]]  # server module inspects argv on import
    from jcodemunch_mcp.investigator import explain_misses
    from jcodemunch_mcp.server import _catalog_names, _catalog_rows

    data = json.loads(results_path.read_text(encoding="utf-8"))
    out = explain_misses(
        data["per_query"], catalog_rows=_catalog_rows(), catalog_names=_catalog_names()
    )

    total = out["explained"]
    print(f"route misses explained: {total}\n")
    grouped = defaultdict(list)
    for e in out["explanations"]:
        grouped[e["outcome"]].append(e)

    for gate in ("rule_preempted", "no_lexical_overlap", "ranked_below_cutoff",
                 "catalog_absent", "empty_query"):
        rows = grouped.get(gate)
        if not rows:
            continue
        pct = 100.0 * len(rows) / total if total else 0.0
        print(f"## {gate}  -  {len(rows)}/{total} ({pct:.0f}%)")
        print(f"   {GATE_ADVICE[gate]}\n")
        for e in rows:
            extra = ""
            if gate == "rule_preempted":
                extra = f"  [claimed by {e['preempted_by']}]"
            elif gate == "ranked_below_cutoff":
                extra = f"  [rank {e['rank']}, lost to {e['beaten_by']['action']}]"
            print(f"   - {e['task']}")
            print(f"       want {e['expected_action']}{extra}")
        print()

    # An unexplained miss is the one result that would invalidate the tool.
    unexplained = [e for e in out["explanations"] if e["outcome"] not in GATE_ADVICE]
    if unexplained:
        print(f"WARNING: {len(unexplained)} miss(es) landed on no known gate")
        return 2

    if args.write:
        OUT.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
