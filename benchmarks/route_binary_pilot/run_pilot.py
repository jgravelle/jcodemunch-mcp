"""Run the registered predicate against the grounded pilot corpus.

Reports, for every figure, the k-matched bar — a baseline gets as many guesses
as the system it is the floor for, which is the error `run_emitted_task.py` made
and this suite is not repeating.

⚠⚠ **The ablation is the load-bearing measurement, not the headline accuracy.**
Class-S tasks come from docstrings and share vocabulary with the symbol names
around them; class-T tasks are prose. So H3 can look good because the two
classes were drawn from different kinds of text, which is an artifact of
construction rather than an inference about the repository. Two runs separate
them:

    full    — vocabulary = every indexed symbol name in the repo
    ablated — vocabulary MINUS the parts of each case's own target name

If accuracy collapses under ablation, the predicate was reading the target's own
name out of the task and the result is leakage. If it survives, the signal is
the repository's vocabulary at large, which is what H3 actually claims.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import predicate as P  # noqa: E402

from jcodemunch_mcp.storage import IndexStore  # noqa: E402

CASES = HERE / "cases.json"
RESULTS = HERE / "results.json"


def _wilson(hits: int, n: int, z: float = 1.96):
    """Wilson score interval. A point estimate at n=60 without one invites the
    reader to treat noise as an effect."""
    if n == 0:
        return (0.0, 0.0)
    p = hits / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round((centre - half) * 100, 1), round((centre + half) * 100, 1))


def _binom_p(hits: int, n: int, p0: float = 0.5) -> float:
    """Two-sided exact binomial p against p0."""
    def pmf(k):
        return math.comb(n, k) * p0 ** k * (1 - p0) ** (n - k)
    obs = pmf(hits)
    return round(sum(pmf(k) for k in range(n + 1) if pmf(k) <= obs + 1e-12), 4)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    payload = json.loads(CASES.read_text(encoding="utf-8"))
    cases = payload["cases"]
    store = IndexStore()

    vocab_by_repo = {}
    for meta in payload["repos"]:
        owner, name = meta["repo"].split("/")
        index = store.load_index(owner, name)
        if index is None:
            raise SystemExit(f"{meta['repo']} is not indexed")
        vocab_by_repo[meta["repo"]] = P.symbol_vocabulary(
            s["name"] for s in index.symbols if s.get("name")
        )

    rows = []
    for c in cases:
        vocab = vocab_by_repo[c["repo"]]
        own = P._parts(c["target"]) if c["target_kind"] == "symbol" else set()
        ablated = vocab - own
        rows.append({
            "repo": c["repo"],
            "gold": c["gold"],
            "task": c["task"],
            "target": c["target"],
            "target_kind": c["target_kind"],
            "pred_full": P.predict(c["task"], vocab),
            "pred_ablated": P.predict(c["task"], ablated),
            "matched_full": P.matched_tokens(c["task"], vocab),
            "matched_is_own_name": sorted(
                set(t.lower() for t in P.matched_tokens(c["task"], vocab)) & own
            ),
        })

    n = len(rows)

    def acc(field, subset=None):
        pool = [r for r in rows if subset is None or r["gold"] == subset]
        hits = sum(1 for r in pool if r[field] == r["gold"])
        return hits, len(pool), round(hits / len(pool) * 100, 1) if pool else 0.0

    # A constant answer is the k-matched bar at k=1: the predicate emits one
    # class, so its floor may emit one class.
    const = max(
        (sum(1 for r in rows if r["gold"] == g), g)
        for g in ("search_symbols", "search_text")
    )
    floor_pct = round(const[0] / n * 100, 1)

    h_full, n_full, a_full = acc("pred_full")
    h_abl, _, a_abl = acc("pred_ablated")

    summary = {
        "n": n,
        "balanced": {g: sum(1 for r in rows if r["gold"] == g)
                     for g in ("search_symbols", "search_text")},
        "blind_floor": {"answer": const[1], "pct": floor_pct,
                        "note": "best constant single class; the k-matched bar "
                                "for a predicate that emits one class"},
        "full_vocabulary": {
            "accuracy_pct": a_full,
            "vs_floor_pts": round(a_full - floor_pct, 1),
            "wilson95": _wilson(h_full, n_full),
            "binomial_p_vs_50": _binom_p(h_full, n_full),
            "by_class": {g: acc("pred_full", g)[2] for g in ("search_symbols", "search_text")},
        },
        "ablated_own_name": {
            "accuracy_pct": a_abl,
            "vs_floor_pts": round(a_abl - floor_pct, 1),
            "wilson95": _wilson(h_abl, n_full),
            "binomial_p_vs_50": _binom_p(h_abl, n_full),
            "by_class": {g: acc("pred_ablated", g)[2] for g in ("search_symbols", "search_text")},
            "note": "vocabulary minus the parts of each case's OWN target name. "
                    "A collapse here means the predicate was reading the target "
                    "out of the task, i.e. leakage, and the run is discarded.",
        },
        "leakage": {
            "class_S_cases_matching_own_name": sum(
                1 for r in rows if r["target_kind"] == "symbol" and r["matched_is_own_name"]
            ),
            "class_S_total": sum(1 for r in rows if r["target_kind"] == "symbol"),
        },
        "protocol": "benchmarks/route_binary_pilot/PROTOCOL.md",
        "reading": "A negative is decisive and kills the corpus project. A "
                   "positive certifies nothing: these tasks are synthetic "
                   "paraphrase, a third distribution distinct from both the "
                   "human corpus and agent-emitted wording.",
    }

    print(json.dumps(summary, indent=2))
    if args.write:
        RESULTS.write_text(
            json.dumps({"summary": summary, "per_case": rows}, indent=1) + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {RESULTS}")
    return 0


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
