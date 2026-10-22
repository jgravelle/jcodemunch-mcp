"""The token benchmark's floor mode (STANDARD criterion 2), offline.

The number itself can only come from a network run on the pinned corpora;
this pins the VERDICT logic against synthetic results so the bench tier's
`--floor` cannot rot silently, and against the committed reference so the
floors are known to be cleared by the last published measurement.

Non-vacuity: every floor is exercised in BOTH directions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "benchmarks" / "harness"))

from harness import thresholds as T  # noqa: E402
import token_floor as F  # noqa: E402


def _results(jcm_per_task: int, grep_per_task: int, repo="expressjs/express", n=5):
    return [{"repo": repo, "tasks": [{"jmunch_tokens": jcm_per_task, "grep_baseline_tokens": grep_per_task} for _ in range(n)]}]


def test_ratio_floor_passes_and_fails_in_both_directions():
    floor = T.floor("token.grand_ratio_vs_grep")
    ok, lines = F.verdicts(_results(100, int(100 * floor) + 1), None)
    assert ok and any(l.endswith("PASS") for l in lines), lines
    ok, lines = F.verdicts(_results(100, int(100 * floor) - 1), None)
    assert not ok and any(l.endswith("FAIL") for l in lines), lines


def test_per_repo_rise_fails_only_on_an_upward_move():
    rise = T.floor("token.per_repo_rise_max")
    ref = {"repos": {"expressjs/express": {"jmunch_total_tokens": 1000}}}
    grep = 10_000_000
    ok, lines = F.verdicts(_results(int(1000 * (1 + rise) / 5) + 1, grep), ref)
    assert not ok, lines
    ok, lines = F.verdicts(_results(int(1000 * (1 + rise) / 5) - 1, grep), ref)
    assert ok, lines
    # DOWNWARD move (our favour) is a pass; the workflow's re-sync warning covers it
    ok, lines = F.verdicts(_results(100, grep), ref)
    assert ok, lines


def test_no_valid_runs_is_not_a_pass():
    ok, lines = F.verdicts([{"repo": "x", "error": "not indexed"}], None)
    assert not ok and "UNKNOWN" in lines[0]


def test_committed_reference_clears_the_ratio_floor():
    """The last published run (results.md grand summary) must itself clear the
    floor, or the floor is not conservative. Read from the artifact, never typed."""
    ref = json.loads((REPO / "benchmarks" / "jcm_reference.json").read_text(encoding="utf-8"))
    md = (REPO / "benchmarks" / "results.md").read_text(encoding="utf-8")
    import re
    g = re.search(r"^\|\s*Baseline B total, grep-top-3\s*\|\s*([\d,]+)\s*\|", md, re.M)
    j = re.search(r"^\|\s*jMunch total\s*\|\s*([\d,]+)\s*\|", md, re.M)
    assert g and j, "results.md Grand Summary rows not found"
    grep_total, jcm_total = int(g.group(1).replace(",", "")), int(j.group(1).replace(",", ""))
    assert jcm_total == ref["grand"]["jmunch_tokens"], "results.md and jcm_reference.json disagree on jcm tokens"
    T.assert_passes("token.grand_ratio_vs_grep", round(grep_total / jcm_total, 2), context="committed benchmarks/results.md")


# --- an approved rise while the reference is replaced --------------------------------


def _two_repos(express_jcm: int, fastapi_jcm: int, grep: int = 10_000_000):
    return _results(express_jcm, grep) + _results(fastapi_jcm, grep, repo="fastapi/fastapi")


def test_an_accepted_rise_is_printed_not_judged():
    rise = T.floor("token.per_repo_rise_max")
    ref = {"repos": {"expressjs/express": {"jmunch_total_tokens": 1000},
                     "fastapi/fastapi": {"jmunch_total_tokens": 1000}}}
    over = int(1000 * (1 + rise) / 5) + 50
    results = _two_repos(200, over)
    ok, lines = F.verdicts(results, ref)
    assert not ok  # non-vacuity: without acceptance the fastapi rise fails
    ok, lines = F.verdicts(results, ref, frozenset({"fastapi/fastapi"}))
    assert ok, lines
    assert any("accepted" in l and "fastapi/fastapi" in l for l in lines), lines


def test_accepting_one_repo_still_judges_the_others():
    rise = T.floor("token.per_repo_rise_max")
    ref = {"repos": {"expressjs/express": {"jmunch_total_tokens": 1000},
                     "fastapi/fastapi": {"jmunch_total_tokens": 1000}}}
    over = int(1000 * (1 + rise) / 5) + 50
    ok, lines = F.verdicts(_two_repos(over, over), ref, frozenset({"fastapi/fastapi"}))
    assert not ok and any("FAIL" in l and "expressjs/express" in l for l in lines), lines


def test_accepting_a_rise_does_not_waive_the_grep_ratio():
    floor = T.floor("token.grand_ratio_vs_grep")
    ref = {"repos": {"expressjs/express": {"jmunch_total_tokens": 1000}}}
    ok, lines = F.verdicts(_results(100, int(100 * floor) - 1), ref, frozenset({"expressjs/express"}))
    assert not ok, lines


def test_accept_rise_without_reference_is_refused(monkeypatch):
    import run_benchmark as R

    monkeypatch.setattr(sys, "argv", ["run_benchmark.py", "--floor", "--accept-rise", "fastapi/fastapi"])
    assert R.main() == 2
