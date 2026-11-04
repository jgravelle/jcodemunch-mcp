"""Why was this action not proposed? A deterministic route counterfactual.

The 2026-08-02 deep-research report named this as the strongest genuinely-new
idea in its portfolio, and the reason is measurable rather than aesthetic:
``benchmarks/route_recall/results.json`` records that ``route`` misses
elementary intents -- "show me the body of this function", "I need this function
plus everything it imports", "is this name used anywhere at all or can I drop
it" -- and an evaluator can see THAT they missed while having no production-grade
account of WHY. A recall number you cannot decompose is a number you cannot fix.

This module converts a miss into a named gate.

## What it is not

Not a debug log, and not a second scorer. Every gate here is evaluated with the
same ``counter`` functions the live front door uses -- ``_query_tokens``,
``_idf_weights``, ``score_action``, ``_INTENT_RULES`` -- so an explanation that
disagrees with production is a bug in this file, not a difference of opinion. A
test asserts the reconstruction matches ``classify_intent`` + ``search_catalog``
on every benchmark query.

⚠ **Not registered as an MCP tool**, and deliberately. The report's own
recommendation in the same document is a moratorium on new top-level actions
until route recall improves; adding a 92nd action to explain why the 91st is
hard to reach would be self-refuting. It is importable, tested, and driven by
the benchmark harness -- which is the workflow the report says it has to feed to
matter at all.

## The gate order is the pipeline order

``_handle_route`` does exactly this, and the order is load-bearing because the
FIRST gate that excludes a candidate is the only one worth reporting -- the
others are downstream of a decision already made:

  1. the action is not in the live catalog at all       -> ``catalog_absent``
  2. the query reduces to nothing scorable              -> ``empty_query``
  3. a curated rule proposed it                         -> returned
  4. a curated rule proposed something ELSE, and rules
     short-circuit the lexical fallback entirely        -> ``rule_preempted``
  5. no query token appears in its name or description  -> ``no_lexical_overlap``
  6. it scored, but below the cutoff                    -> ``ranked_below_cutoff``
  7. otherwise                                          -> returned

⚠⚠ **Gate 4 is the one that did not exist before this module.** ``route`` runs
the fallback ONLY when ``classify_intent`` returns nothing. So a query that
trips any rule never scores the rest of the catalog, and an action that would
have ranked first lexically is not "ranked below cutoff" -- it was never
scored. Reading that miss as a ranking problem sends you to tune weights that
were never consulted.
"""

from __future__ import annotations

from typing import Iterable, Optional

from .. import counter as _counter

#: Outcome of the reconstruction. `returned` means the action WAS proposed.
RETURNED = "returned"
CATALOG_ABSENT = "catalog_absent"
EMPTY_QUERY = "empty_query"
RULE_PREEMPTED = "rule_preempted"
NO_LEXICAL_OVERLAP = "no_lexical_overlap"
RANKED_BELOW_CUTOFF = "ranked_below_cutoff"

#: Gates in pipeline order. A caller reporting more than the first one is
#: reporting consequences, not causes.
GATES: tuple[str, ...] = (
    CATALOG_ABSENT,
    EMPTY_QUERY,
    RULE_PREEMPTED,
    NO_LEXICAL_OVERLAP,
    RANKED_BELOW_CUTOFF,
)

_EXPERIMENTS = {
    CATALOG_ABSENT: (
        "The action is not callable under the active tool surface. Ranking "
        "cannot reach it at any weight. Compare the surface the guidance was "
        "generated against with the one the server exposes (#397)."
    ),
    EMPTY_QUERY: (
        "Every token was a stopword, so there was nothing to rank on. Only a "
        "curated rule can serve this phrasing."
    ),
    RULE_PREEMPTED: (
        "A curated rule fired, and `route` runs the lexical fallback only when "
        "NO rule matches. This action was never scored. Do not tune weights "
        "for this miss -- either add a rule for the intent, or narrow the "
        "pattern that claimed it."
    ),
    NO_LEXICAL_OVERLAP: (
        "The query shares no token with this action's name or description, so "
        "no reweighting can rescue it -- the score is structurally zero. Add "
        "the user's vocabulary to the description, or add a curated rule. This "
        "is the no-stemming residual disclosed in v1.108.213."
    ),
    RANKED_BELOW_CUTOFF: (
        "It scored but lost. This is the one gate where ranking work helps: "
        "compare the per-token contributions against the winner below."
    ),
}


def _score_rows(qt: list[str], rows: list[dict]) -> list[tuple[float, dict]]:
    """Every row scored exactly as ``search_catalog`` scores it, unsorted."""
    weights = _counter._idf_weights(qt, rows)
    return [
        (
            _counter.score_action(
                qt, r["action"], r.get("_description", r.get("summary", "")), weights
            ),
            r,
        )
        for r in rows
    ]


def _token_contributions(
    qt: list[str], row: dict, weights: dict
) -> list[dict]:
    """Per-token account of a single action's score.

    Mirrors ``score_action``'s branches one for one. A token that contributed
    nothing is still listed -- "this word did not help" is the finding, and
    dropping the zero rows is what makes a ranking look inexplicable.
    """
    name_l = row["action"].lower()
    name_toks = set(_counter._tokens(row["action"]))
    desc = row.get("_description", row.get("summary", ""))
    desc_toks = set(_counter._tokens(desc))
    out = []
    for t in qt:
        w = weights.get(t, 1.0)
        if t == name_l:
            channel, points = "name_exact", 10.0 * w
        elif t in name_toks:
            channel, points = "name_word", 4.0 * w
        elif len(t) >= _counter._MIN_SUBSTRING_LEN and t in name_l:
            channel, points = "name_fragment", 1.5 * w
        else:
            channel, points = None, 0.0
        if t in desc_toks:
            channel = f"{channel}+description" if channel else "description"
            points += 1.0 * w
        out.append({
            "token": t,
            "idf": round(w, 3),
            "channel": channel or "none",
            "points": round(points, 3),
        })
    return out


def explain_route(
    task: str,
    expected_action: str,
    *,
    catalog_rows: list[dict],
    catalog_names: Optional[Iterable[str]] = None,
    limit: int = 3,
) -> dict:
    """Name the first gate that kept ``expected_action`` out of ``route(task)``.

    ``catalog_rows`` are menu-shaped rows (``server._catalog_rows()``).
    ``catalog_names`` defaults to the actions present in those rows.

    Read-only and side-effect free: it scores, it does not dispatch.
    """
    names = set(catalog_names) if catalog_names is not None else {
        r["action"] for r in catalog_rows
    }
    rows = [r for r in catalog_rows if r["action"] not in _counter.FRONT_DOOR]
    result: dict = {
        "task": task,
        "expected_action": expected_action,
        "decision_path": [],
    }

    def gate(name: str, **detail) -> dict:
        result["outcome"] = name
        result["first_excluding_gate"] = None if name == RETURNED else name
        if name != RETURNED:
            result["experiment"] = _EXPERIMENTS[name]
        result.update(detail)
        return result

    # 1 -- callable at all?
    result["decision_path"].append("catalog")
    if expected_action not in names:
        return gate(CATALOG_ABSENT, catalog_size=len(names))

    # 2/3 -- curated rules. `classify_intent` returns EVERY matching rule whose
    # action is live, primary first, and route uses the fallback only when that
    # list is empty.
    result["decision_path"].append("intent_rules")
    recs = _counter.classify_intent(task, names)
    rule_actions = [r["action"] for r in recs]
    result["rule_matches"] = rule_actions
    if expected_action in rule_actions:
        return gate(
            RETURNED,
            path="rule",
            rank=rule_actions.index(expected_action) + 1,
        )
    if rule_actions:
        # ⚠ The gate that did not exist before this module. The fallback never
        # ran, so this action carries no score -- and calling it "ranked below
        # cutoff" would send a reader to tune weights nothing consulted.
        return gate(
            RULE_PREEMPTED,
            preempted_by=rule_actions[0],
            scored=False,
        )

    # 4 -- anything to rank on?
    result["decision_path"].append("tokenize")
    # The fallback strips host prefixes and pins a name-shaped query's tools
    # first (R&D item 185); read the same rule, never a copy of it.
    task = _counter._strip_host_prefixes(task)
    pinned = [r["action"] for r in _counter.named_rows(task, rows)]
    if pinned:
        result["named"] = pinned
    qt = _counter._query_tokens(task)
    result["query_tokens"] = qt
    if not qt and not pinned:
        return gate(EMPTY_QUERY, raw_tokens=_counter._tokens(task))

    # 5/6/7 -- the lexical fallback.
    result["decision_path"].append("lexical_fallback")
    weights = _counter._idf_weights(qt, rows)
    scored = [(s, r) for s, r in _score_rows(qt, rows)]
    by_action = {r["action"]: s for s, r in scored}
    mine = by_action.get(expected_action, 0.0)
    result["score"] = round(mine, 3)

    ranked = sorted(
        [(s, r) for s, r in scored if s > 0 and r["action"] not in pinned],
        key=lambda x: (-x[0], x[1]["action"]),
    )
    order = pinned + [r["action"] for _, r in ranked]
    row = next((r for r in rows if r["action"] == expected_action), None)
    if row is not None:
        result["contributions"] = _token_contributions(qt, row, weights)

    if mine <= 0 and expected_action not in pinned:
        return gate(
            NO_LEXICAL_OVERLAP,
            scored=True,
            rank=None,
            top=order[:limit],
        )

    rank = order.index(expected_action) + 1
    if rank > limit:
        winner = next(r for r in rows if r["action"] == order[0])
        out = gate(
            RANKED_BELOW_CUTOFF,
            rank=rank,
            cutoff=limit,
            top=order[:limit],
            beaten_by={
                "action": winner["action"],
                "score": round(by_action.get(winner["action"], 0.0), 3),
                **({"named": True} if winner["action"] in pinned else {}),
                "contributions": _token_contributions(qt, winner, weights),
            },
        )
        if expected_action in pinned:
            # The query named this target too, behind `limit` other names: the
            # order named plus the limit cut it, not its score. An UNNAMED
            # target behind a named winner lost on score and keeps the default
            # text (review round 3).
            out["experiment"] = (
                "The query named the tools ahead of this one; it was cut by the "
                "order named and the limit, not by its score. Name it earlier "
                "or raise the limit."
            )
        return out
    return gate(RETURNED, path="fallback", rank=rank, top=order[:limit])


def explain_misses(
    per_query: list[dict],
    *,
    catalog_rows: list[dict],
    catalog_names: Optional[Iterable[str]] = None,
    limit: int = 3,
) -> dict:
    """Explain every route miss in a ``route_recall`` results file.

    Takes the ``per_query`` rows verbatim so the harness stays the producer and
    this stays the explainer -- two artifacts disagreeing about what missed is
    the same defect in a different costume.
    """
    explanations = []
    for entry in per_query:
        if entry.get("route_rank") is not None:
            continue
        for target in entry.get("targets") or []:
            explanations.append(
                explain_route(
                    entry.get("query", ""),
                    target,
                    catalog_rows=catalog_rows,
                    catalog_names=catalog_names,
                    limit=limit,
                )
            )
    by_gate: dict[str, int] = {}
    for e in explanations:
        by_gate[e["outcome"]] = by_gate.get(e["outcome"], 0) + 1
    return {
        "explained": len(explanations),
        "by_gate": dict(sorted(by_gate.items(), key=lambda kv: -kv[1])),
        "explanations": explanations,
    }
