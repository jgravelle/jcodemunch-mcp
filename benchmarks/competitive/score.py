"""Scoring of the competitive tier (docs/competitive/DESIGN.md s5).

purpose:  per (axis, tool, corpus): median of three runs, the spread, our
          value on the same row, the delta, the band and whether the gap is
          meaningful; F1 per task with the field's line tolerance
invokes:  nothing outside the standard library
produces: plain dicts the runner writes into the result file
refuses:  a band from fewer than three runs (a one-run comparison has no
          spread and therefore no band: harness DESIGN s5)
pinned:   n/a
fairness: a row is unstable when its own spread exceeds UNSTABLE_FRACTION
          of its own median, judged BEFORE the band is built, so an
          unstable row cannot widen the band it is then measured against
          (the first draft did exactly that and called a 50/100/300 triple
          stable); the band is max(5%% of our median, 3x the larger
          spread) over stable rows only; F1 counts an expected hit once,
          and a cited line that matches nothing counts against precision.
"""

from __future__ import annotations

import statistics
from typing import Iterable, Optional

RATIO_AXES = ("tokens_per_task", "calls_per_task", "latency_call_ms", "index_cold_seconds", "tools_list_tokens")
DIFF_AXES = ("f1_P1", "f1_P2", "f1_P4", "f1_P5")
UNSTABLE_FRACTION = 0.10  # DESIGN s5.1


def f1(cited: Iterable[tuple[str, int]], expected: Iterable[tuple[str, int]], tolerance: int,
       cites_all: bool = False, corpus_lines: int = 0) -> Optional[float]:
    """ONE-TO-ONE matching (DESIGN s5.1): each expected line is matched to the
    nearest still-unmatched cited line within the tolerance, so two citations
    beside one expected line count as one hit and one stray, and one citation
    cannot credit two expected lines. Many-to-many matching (the first draft)
    paid a tool for citing densely near a hit, which grep does by construction
    (review, finding 2). A read-all answer cites every line of the corpus:
    recall 1, precision = expected over corpus lines."""
    exp = list(expected)
    if not exp:
        return None
    if cites_all:
        if corpus_lines <= 0:
            return 0.0
        precision = min(1.0, len(exp) / corpus_lines)
        return round(2 * precision / (precision + 1.0), 4)
    cit = set(cited)
    if not cit:
        return 0.0
    free = set(cit)
    matched = 0
    for ef, el in exp:
        candidates = sorted((abs(c[1] - el), c) for c in free if c[0] == ef and abs(c[1] - el) <= tolerance)
        if candidates:
            free.discard(candidates[0][1])
            matched += 1
    precision = matched / len(cit)
    recall = matched / len(exp)
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 4)


def median_spread(values: list[float]) -> tuple[Optional[float], Optional[float]]:
    xs = [v for v in values if v is not None]
    if not xs:
        return None, None
    return statistics.median(xs), (max(xs) - min(xs))


def _stable(median: float, spread: float) -> bool:
    """Spread within UNSTABLE_FRACTION of the row's OWN median; a zero median
    is stable only with a zero spread."""
    if median == 0:
        return spread == 0
    return spread <= UNSTABLE_FRACTION * abs(median)


def band(jcm_median: float, spread_tool: float, spread_jcm: float) -> float:
    return max(0.05 * abs(jcm_median), 3.0 * max(spread_tool, spread_jcm))


def compare(axis: str, tool_vals: list[float], jcm_vals: list[float]) -> dict:
    """One row. `runs` is the raw triple so a reader can recompute."""
    tm, ts = median_spread(tool_vals)
    jm, js = median_spread(jcm_vals)
    row = {"axis": axis, "runs": tool_vals, "measured": tm, "spread": ts, "jcm": jm, "jcm_spread": js,
           "delta": None, "band": None, "meaningful": False, "stable": None, "note": ""}
    if tm is None or jm is None:
        row["note"] = "NOT COMPARABLE"
        return row
    if len([v for v in tool_vals if v is not None]) < 3 or len([v for v in jcm_vals if v is not None]) < 3:
        row["note"] = "fewer than three runs: no band (harness DESIGN s5)"
        row["delta"] = (tm / jm if jm else None) if axis in RATIO_AXES else round(tm - jm, 4)
        return row
    row["stable"] = _stable(tm, ts) and _stable(jm, js)
    b = band(jm, ts, js)
    row["band"] = round(b, 4)
    if axis in RATIO_AXES:
        row["delta"] = round(tm / jm, 4) if jm else None
    else:
        row["delta"] = round(tm - jm, 4)
    row["meaningful"] = bool(row["stable"] and abs(tm - jm) > b)
    if not row["stable"]:
        row["note"] = "unstable: a spread exceeds 10% of its own median"
    return row
