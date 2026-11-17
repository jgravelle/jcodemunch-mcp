"""Score the `task` strings agents actually emit, not the words users type.

Answers the question #398 ended on and #422 supplied the inputs for: nothing
sends a user's words to ``route``. The model calls it, and ``task`` is whatever
that model chose to write. So a human-phrased corpus measures ``route`` against
wording it never receives. This harness measures the wording it does receive.

Method:

  1. Take unseen user-level requests (rknighton's jcm-route-benchmark-corpus,
     MIT-0, labeled with the action each should resolve to).
  2. Hand each to a FRESH agent that sees only the request and the three-tool
     counter front door -- no catalog, no labels, no other request.
  3. Record the ``task`` string it emits.
  4. Score those strings through the same path ``route`` takes.

Scoring mirrors server.py's ``_handle_route``: ``classify_intent`` (curated
regex rules) falling back to ``search_catalog(rows, task, 3)`` when no rule
fires. The fallback cap of 3 is route's ceiling on unmatched tasks by
construction, not a tunable here.

TWO NUMBERS, NOT ONE. The corpus concentrates 83.2% of its labels on
``search_text`` + ``search_symbols``, and its author flags that boundary as the
weakest part of the labeling. A single accuracy figure would be dominated by
that one judgment call, so ``family`` (either search action counts) and
``within-family`` (the exact one) are reported separately.

THE FLOOR IS THE HEADLINE. On a corpus this concentrated, a constant guess
scores well. The sample's own blind floor is computed here and printed beside
every result; a number below it is worse than answering the same thing every
time. Do NOT compare these figures to queries.json / holdout.json, whose blind
floors are 5.1% and 6.8% -- those corpora are near-uniform coverage matrices and
a different difficulty regime entirely.

Run:
    PYTHONPATH=src python benchmarks/route_recall/run_emitted_task.py
    PYTHONPATH=src python benchmarks/route_recall/run_emitted_task.py --write
"""

from __future__ import annotations

import argparse
import itertools
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = HERE / "emitted_task_cases.json"
RESULTS = HERE / "emitted_task_results.json"

ROUTE_KS = (1, 3)
ROUTE_FALLBACK_LIMIT = 3  # mirrors server.py; changing it here measures fiction
SEARCH_FAMILY = frozenset({"search_text", "search_symbols"})


def _load_catalog():
    """Live catalog rows + names, straight from the server. No fixtures."""
    sys.argv = [sys.argv[0]]  # server module inspects argv on import
    from jcodemunch_mcp import counter as _counter
    from jcodemunch_mcp.server import _catalog_names, _catalog_rows

    return _counter, _catalog_rows(), sorted(_catalog_names())


def _route_actions(counter, rows, names, task):
    """The ordered actions route would recommend for *task*."""
    recs = counter.classify_intent(task, names)
    if not recs:
        recs = [{"action": r["action"]}
                for r in counter.search_catalog(rows, task, ROUTE_FALLBACK_LIMIT)]
    return [r["action"] for r in recs]


def _rank_of_target(ordered, targets):
    """1-based rank of the first acceptable action, or None if absent."""
    for i, action in enumerate(ordered, start=1):
        if action in targets:
            return i
    return None


def _leakage(counter, text, targets, desc_by_name):
    """How much of the target's own vocabulary the emitted string hands back.

    Pre-registered rather than filtered. An emitted string that names its own
    target action is measuring exact-match lookup, not routing -- and unlike a
    human corpus this cannot be waved off as author bias, because under the
    counter surface the model never saw the catalog to copy from. A high score
    here is a finding about the front door, not about the corpus.
    """
    qt = set(counter._tokens(text))
    worst_name = worst_desc = 0.0
    for t in targets:
        name_toks = set(counter._tokens(t))
        desc_toks = set(counter._tokens(desc_by_name.get(t, "")))
        if name_toks:
            worst_name = max(worst_name, len(qt & name_toks) / len(name_toks))
        if qt and desc_toks:
            worst_desc = max(worst_desc, len(qt & desc_toks) / len(qt))
    return round(worst_name, 3), round(worst_desc, 3)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="Write results JSON.")
    ap.add_argument("--cases", type=Path, default=CASES)
    args = ap.parse_args()

    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = payload["cases"]
    counter, rows, names = _load_catalog()
    desc_by_name = {r["action"]: r.get("summary", "") for r in rows}

    n = len(cases)
    exact = collections.Counter()
    strict = collections.Counter()
    family = collections.Counter()
    within_n = within_hit = 0
    per_case = []
    leak_names = []
    off_catalog = []
    rule_fired = 0
    depth = collections.Counter()
    rank1_picks = collections.Counter()

    _ordered_by_case: dict = {}
    for c in cases:
        gold = c["gold_primary"]
        targets = {gold} | set(c.get("gold_alts") or ())
        recs = counter.classify_intent(c["emitted_task"], set(names))
        rule_fired += 1 if recs else 0
        depth[len(recs) if recs else ROUTE_FALLBACK_LIMIT] += 1
        ordered = _route_actions(counter, rows, names, c["emitted_task"])
        # ⚠ Sliced to the same 3 the per_case row records, so the availability
        # figures below describe WHAT THE CALLER WAS SHOWN rather than the full
        # internal ranking. Reporting the untruncated list would credit route
        # with options the response never carried.
        _ordered_by_case[c["case_id"]] = ordered[:3]
        if ordered:
            rank1_picks[ordered[0]] += 1

        # Strict = gold_primary only. Reported beside the generous rule because
        # a conclusion that flips with the scoring rule is not a conclusion.
        for k in ROUTE_KS:
            if _rank_of_target(ordered, {gold}) is not None \
                    and _rank_of_target(ordered, {gold}) <= k:
                strict[k] += 1

        rank = _rank_of_target(ordered, targets)
        for k in ROUTE_KS:
            if rank is not None and rank <= k:
                exact[k] += 1

        # Family: did route land anywhere in the search family when gold is in it?
        fam_rank = _rank_of_target(ordered, SEARCH_FAMILY) if gold in SEARCH_FAMILY else rank
        for k in ROUTE_KS:
            if fam_rank is not None and fam_rank <= k:
                family[k] += 1

        # Within-family: of the cases route put in the right family at rank 1,
        # how often did it pick the exact labeled member?
        if gold in SEARCH_FAMILY and ordered and ordered[0] in SEARCH_FAMILY:
            within_n += 1
            if ordered[0] == gold:
                within_hit += 1

        ln, ld = _leakage(counter, c["emitted_task"], targets, desc_by_name)
        leak_names.append(ln)
        if ordered and ordered[0] not in names:
            off_catalog.append(c["case_id"])

        per_case.append({
            "case_id": c["case_id"],
            "gold_primary": gold,
            "gold_alts": c.get("gold_alts") or [],
            "emitted_task": c["emitted_task"],
            "route_actions": ordered[:3],
            "rank": rank,
            "leak_name": ln,
            "leak_desc": ld,
        })

    prim = collections.Counter(c["gold_primary"] for c in cases)
    fam_share = sum(prim[a] for a in SEARCH_FAMILY)

    _pair = set(SEARCH_FAMILY) & {"search_text", "search_symbols"}
    _pair_cases = [c for c in cases if c["gold_primary"] in _pair]
    _pair_n = len(_pair_cases)
    _pair_both = sum(1 for c in _pair_cases if _pair <= set(_ordered_by_case.get(c["case_id"], [])))
    _pair_gold_in = sum(1 for c in _pair_cases
                        if c["gold_primary"] in _ordered_by_case.get(c["case_id"], []))

    # EVERY metric needs its own floor. Reporting one floor beside two metrics
    # is how a result gets read as a win on the metric that has no bar. The
    # family metric's floor is much higher than the exact metric's, because a
    # constant search action lands in the family on every family-labeled case.
    def _best_constant():
        best_exact = ("", -1)
        best_family = ("", -1)
        best_strict = ("", -1)
        for a in names:
            st = sum(1 for c in cases if a == c["gold_primary"])
            if st > best_strict[1]:
                best_strict = (a, st)
            ex = sum(1 for c in cases
                     if a in ({c["gold_primary"]} | set(c.get("gold_alts") or ())))
            fam = sum(1 for c in cases
                      if (c["gold_primary"] in SEARCH_FAMILY and a in SEARCH_FAMILY)
                      or (c["gold_primary"] not in SEARCH_FAMILY
                          and a in ({c["gold_primary"]} | set(c.get("gold_alts") or ()))))
            if ex > best_exact[1]:
                best_exact = (a, ex)
            if fam > best_family[1]:
                best_family = (a, fam)
        return best_exact, best_family, best_strict

    # ⚠⚠ A CONSTANT ANSWER IS ALLOWED AS MANY GUESSES AS THE SYSTEM IT IS THE
    # FLOOR FOR. The single-action floor above is the right bar for @1 and the
    # WRONG bar for @3: it compares route's three guesses against a baseline
    # holding one. Correcting it moves strict@3 from +17.5 against the 1-set
    # floor to -30.0 against the 3-set floor, i.e. @3 does not rescue this
    # result, it deepens it. Computed here so the number cannot be re-derived
    # by hand and cannot drift (Maintenance Practice 4).
    def _best_constant_kset(k, hits):
        """Best constant k-action answer under a per-case hit predicate."""
        best = ((), -1)
        for combo in itertools.combinations(sorted(label_actions), k):
            s = set(combo)
            c = sum(1 for case in cases if hits(case, s))
            if c > best[1]:
                best = (combo, c)
        return list(best[0]), round(best[1] / n * 100, 1)

    # Candidates are the LABELLED actions, not the whole catalog: a constant
    # answer naming an action no case labels can never score, and including all
    # 91 makes the search 100x slower for the same result.
    label_actions = {c["gold_primary"] for c in cases}
    for c in cases:
        label_actions |= set(c.get("gold_alts") or ())

    def _hit_strict(case, s):
        return case["gold_primary"] in s

    def _hit_exact(case, s):
        return bool(({case["gold_primary"]} | set(case.get("gold_alts") or ())) & s)

    def _hit_family(case, s):
        if case["gold_primary"] in SEARCH_FAMILY:
            return bool(s & set(SEARCH_FAMILY))
        return _hit_exact(case, s)

    (fl_ex_a, fl_ex_n), (fl_fam_a, fl_fam_n), (fl_st_a, fl_st_n) = _best_constant()
    floor_strict = round(fl_st_n / n * 100, 1)
    floor_action, floor_hits = fl_ex_a, fl_ex_n
    floor = round(floor_hits / n * 100, 1)
    floor_family = round(fl_fam_n / n * 100, 1)

    summary = {
        "cases": n,
        "catalog_actions": len(names),
        "sample": {
            "source": payload.get("source"),
            "seed": payload.get("seed"),
            "pool": payload.get("pool"),
            "emitting_model": payload.get("emitting_model"),
        },
        "blind_floor": {
            "strict": {"action": fl_st_a, "pct": floor_strict},
            "exact": {"action": floor_action, "pct": floor},
            "family": {"action": fl_fam_a, "pct": floor_family},
            "note": "best single constant answer, per metric. A result at or below its own "
                    "floor is not routing. The floors differ sharply, so no number may be "
                    "quoted against another metric's bar.",
        },
        # ⚠⚠ CORRECTED 2026-08-21. This block used to argue that the floor is
        # RANK-INVARIANT -- a constant answer emits one action, so it scores the
        # same at @1 and @3 -- and concluded that route@3-vs-floor was "the fair
        # comparison and the one that matters". **That is wrong, and it was
        # wrong in route's favour.** A baseline must be allowed as many guesses
        # as the system it is the floor for. Against the best constant 3-SET,
        # strict@3 is -30.0 rather than +17.5 and exact@3 is -30.0 against a
        # 3-set that scores 100%. Both are reported below; quote the k-matched
        # pair, never a @3 result against a 1-set floor.
        "blind_floor_kset": {
            "strict": dict(zip(("actions", "pct"), _best_constant_kset(3, _hit_strict))),
            "exact": dict(zip(("actions", "pct"), _best_constant_kset(3, _hit_exact))),
            "family": dict(zip(("actions", "pct"), _best_constant_kset(3, _hit_family))),
            "k": 3,
            "note": "best constant 3-ACTION answer, the k-matched bar for any @3 "
                    "figure. A corpus whose best constant 3-set scores 100% cannot "
                    "discriminate a router from a fixed list at that k.",
        },
        "vs_kset_floor_pts": {
            "strict@3": round(strict[3] / n * 100 - _best_constant_kset(3, _hit_strict)[1], 1),
            "exact@3": round(exact[3] / n * 100 - _best_constant_kset(3, _hit_exact)[1], 1),
            "family@3": round(family[3] / n * 100 - _best_constant_kset(3, _hit_family)[1], 1),
        },
        "vs_floor_pts": {
            "strict@1": round(strict[1] / n * 100 - floor_strict, 1),
            "strict@3": round(strict[3] / n * 100 - floor_strict, 1),
            "exact@1": round(exact[1] / n * 100 - floor, 1),
            "exact@3": round(exact[3] / n * 100 - floor, 1),
            "family@1": round(family[1] / n * 100 - floor_family, 1),
            "family@3": round(family[3] / n * 100 - floor_family, 1),
        },
        "route_recall_strict": {f"@{k}": round(strict[k] / n * 100, 1) for k in ROUTE_KS},
        "why": {
            "curated_rule_fired": rule_fired,
            "catalog_fallback": n - rule_fired,
            "recommendations_returned": dict(sorted(depth.items())),
            "rank1_picks": dict(rank1_picks.most_common()),
            "note": "A curated rule returns ONE action, so @3 cannot recover from a wrong @1 "
                    "on those cases -- which is why @3 barely moves. The single dominant rule "
                    "is /find|locate|where is|look up|search for|definition of/ -> "
                    "search_symbols, and agent-emitted task strings almost all open with "
                    "'find'. Human wording varies far more, so this collapse is specific to "
                    "the emitted distribution and invisible in the human-phrased corpora.",
        },
        # ⚠⚠ WHAT THE 52.2% WITHIN-FAMILY FIGURE ACTUALLY MEANS. On most
        # pair-gold cases route returns BOTH search_text and search_symbols, so
        # the binary does not need deciding -- the caller has both candidates and
        # picking between two returned options costs nothing. `within_family` and
        # every @1 figure score that as failure, which is `@1` penalising a router
        # for being honest about ambiguity.
        #
        # ⚠ The residual is a DIFFERENT failure and the larger prize: cases where
        # neither search action was offered at all, i.e. route went to the wrong
        # neighbourhood rather than mis-ranking a pair. Reported separately
        # because a single "wrong" number fuses the two and hides which one is
        # worth work.
        "pair_availability": {
            "pair_gold_cases": _pair_n,
            "both_offered": _pair_both,
            "both_offered_pct": round(_pair_both / _pair_n * 100, 1) if _pair_n else 0.0,
            "gold_offered_anywhere": _pair_gold_in,
            "gold_offered_anywhere_pct": round(_pair_gold_in / _pair_n * 100, 1) if _pair_n else 0.0,
            "neither_offered": _pair_n - _pair_gold_in,
            "note": "cases whose gold is search_text or search_symbols. "
                    "`both_offered` is where the decision is already made for "
                    "the caller; `neither_offered` is route recommending the "
                    "wrong neighbourhood, which no tie-break can fix.",
        },
        "label_concentration": {
            "search_family_share_pct": round(fam_share / n * 100, 1),
            "distinct_primary_actions": len(prim),
        },
        "route_recall_exact": {f"@{k}": round(exact[k] / n * 100, 1) for k in ROUTE_KS},
        "route_recall_family": {f"@{k}": round(family[k] / n * 100, 1) for k in ROUTE_KS},
        "within_family": {
            "n": within_n,
            "correct": within_hit,
            "pct": round(within_hit / within_n * 100, 1) if within_n else None,
            "note": "of cases routed into the search family at rank 1, how often the exact labeled member was picked",
        },
        "leakage": {
            "mean_name_overlap": round(sum(leak_names) / n, 3),
            "emitted_containing_target_name_token": sum(1 for x in leak_names if x > 0),
        },
        "misses": [p["case_id"] for p in per_case if p["rank"] is None],
    }
    if off_catalog:
        summary["off_catalog_first_choice"] = off_catalog

    out = {"summary": summary, "per_case": per_case}
    print(json.dumps(summary, indent=2))
    if args.write:
        RESULTS.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
        print(f"\nwrote {RESULTS}")
    return 0


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
