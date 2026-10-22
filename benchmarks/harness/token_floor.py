"""Floor verdicts for the token benchmark (STANDARD criterion 2).

Pure functions over the run's `results` (what `benchmark_repo` returns) and
the committed reference (`benchmarks/jcm_reference.json`), so the logic is
testable offline (tests/test_token_benchmark_floor.py) while the number is
only ever produced by a real run on the pinned corpora (rules R1-R6, R20+:
never hand-typed, never estimated).

Two thresholds, read from harness/thresholds.json (the only copy):

  token.grand_ratio_vs_grep   sum(grep-top-3 tokens) / sum(jcm tokens) over every
                              valid task-run, must stay >= floor
  token.per_repo_rise_max     for each repo with a reference row, (this run's jcm
                              total - committed) / committed, must stay <= floor.
                              A DOWNWARD move is not a failure: it is the
                              re-sync warning the workflow already prints.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import thresholds as T  # noqa: E402


def grand_ratio_vs_grep(results: list[dict]) -> float | None:
    grep = jcm = 0
    for r in results:
        if "error" in r:
            continue
        for t in r.get("tasks", []):
            if "error" in t or t.get("grep_baseline_tokens") is None:
                continue
            grep += int(t["grep_baseline_tokens"])
            jcm += int(t["jmunch_tokens"])
    if jcm == 0:
        return None
    return round(grep / jcm, 2)


def per_repo_rise(results: list[dict], reference: dict | None) -> dict[str, float]:
    out: dict[str, float] = {}
    if not reference:
        return out
    for r in results:
        if "error" in r:
            continue
        ref = reference.get("repos", {}).get(r["repo"])
        if not ref or not ref.get("jmunch_total_tokens"):
            continue
        now = sum(int(t["jmunch_tokens"]) for t in r.get("tasks", []) if "error" not in t)
        out[r["repo"]] = round((now - ref["jmunch_total_tokens"]) / ref["jmunch_total_tokens"], 4)
    return out


def verdicts(
    results: list[dict],
    reference: dict | None,
    accept_rise: frozenset = frozenset(),
) -> tuple[bool, list[str]]:
    """Return (all_pass, lines). Lines are the harness's one-per-threshold format.

    ``accept_rise`` names repos whose rise a maintainer has approved while the
    committed reference is being replaced (`run_benchmark.py --reference
    --accept-rise REPO`). Their rise is printed and not judged; every other
    repo, and ``token.grand_ratio_vs_grep``, are judged as always.
    """
    ok = True
    lines = []
    ratio = grand_ratio_vs_grep(results)
    if ratio is None:
        lines.append("token.grand_ratio_vs_grep: no valid task-runs; cannot establish (UNKNOWN is not a pass)")
        ok = False
    else:
        line = T.verdict_line("token.grand_ratio_vs_grep", ratio)
        lines.append(line)
        ok = ok and line.endswith("PASS")
    rises = per_repo_rise(results, reference)
    accepted = {r: v for r, v in rises.items() if r in accept_rise}
    judged = {r: v for r, v in rises.items() if r not in accept_rise}
    if accepted:
        lines.append(f"token.per_repo_rise_max: rise accepted for re-baselining, not judged: {accepted}")
    if judged:
        worst = max(judged.values())
        line = T.verdict_line("token.per_repo_rise_max", worst)
        lines.append(line + f"   per repo: {judged}")
        ok = ok and line.endswith("PASS")
    elif accepted:
        pass
    else:
        lines.append("token.per_repo_rise_max: no reference row to compare against (first run); not a failure")
    return ok, lines
