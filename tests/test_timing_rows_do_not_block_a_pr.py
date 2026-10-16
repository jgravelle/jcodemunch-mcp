"""A tier whose ONLY failures are timing rows does not block a PR (jjg, 2026-10-16).

jjg ruled that CI judges every timing row: a busy laptop misses the wall-clock
Floors for reasons the change did not cause, and the hooks were refusing PRs
whose every test passed. The rule lives in `_common.timing_only_failures` and
is read by `run_full.py` (the stamp), `pre_pr.py` (the fast tier) and
`dod_checklist.harness_pass` (rows 2 and 10).

⚠ The load-bearing tests are the ones that KEEP blocking. A timing rule that
also let a red pytest, a non-timing Floor or a killed run through would be the
hooks switched off with a nicer name. Timing rows are read from
`harness/thresholds.json` by unit, so a new timing Floor is covered on arrival
and a cadence (`hours`) or CI-config (`minutes`) Floor never is.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / ".claude" / "hooks"
sys.path.insert(0, str(HOOKS))

import _common  # noqa: E402
import dod_checklist  # noqa: E402


def _summary(*rows: tuple[str, str], pytest_line: str = "17595 passed, 25 skipped in 377.54s") -> str:
    body = "\n".join(f"| `{rid}` | N1 | <= 1 | 2 | **{verdict}** |" for rid, verdict in rows)
    failed = any(v == "FAIL" for _, v in rows)
    return (
        f"## harness full: {'FAIL' if failed else 'PASS'}\n\n"
        "| threshold | criterion | floor | observed | verdict |\n|---|---|---|---|---|\n"
        f"{body}\n\n```\n{pytest_line}\n```\n" + ("\nHARNESS FAIL\n" if failed else "")
    )


# ---------------------------------------------------------------------------
# Which rows are timing rows
# ---------------------------------------------------------------------------


def test_timing_ids_come_from_the_thresholds_file():
    ids = _common.timing_threshold_ids()
    assert {"suite.full_seconds", "suite.fast_seconds", "index.one_file_reindex_ms"} <= ids


@pytest.mark.parametrize("rid", ["release.unreleased_max_hours", "ci.test_job_timeout_minutes", "coverage.min"])
def test_cadence_config_and_quality_rows_are_not_timing_rows(rid):
    assert rid not in _common.timing_threshold_ids()


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def test_a_timing_only_failure_names_its_rows():
    s = _summary(("coverage.min", "PASS"), ("suite.full_seconds", "FAIL"), ("suite.fast_seconds", "FAIL"))
    assert _common.timing_only_failures(s) == ["suite.full_seconds", "suite.fast_seconds"]


def test_a_passing_summary_has_nothing_to_defer():
    assert _common.timing_only_failures(_summary(("suite.full_seconds", "PASS"))) == []


@pytest.mark.parametrize(
    "rows",
    [
        (("suite.full_seconds", "FAIL"), ("coverage.min", "FAIL")),
        (("suite.fast_skips_max", "FAIL"),),
        (("release.unreleased_max_hours", "FAIL"),),
    ],
)
def test_any_non_timing_failure_blocks(rows):
    assert _common.timing_only_failures(_summary(*rows)) == []


@pytest.mark.parametrize("pytest_line", ["2 failed, 17593 passed in 326.64s", "1 error in 0.96s", "3 errors in 1.0s"])
def test_a_red_pytest_blocks_even_beside_a_timing_row(pytest_line):
    s = _summary(("suite.full_seconds", "FAIL"), pytest_line=pytest_line)
    assert _common.timing_only_failures(s) == []


def test_a_fail_with_no_fail_row_blocks():
    """UNKNOWN blocks: a `HARNESS FAIL` whose cause is not in the table."""
    assert _common.timing_only_failures("## harness full: FAIL\n\nHARNESS FAIL\n") == []


def test_a_killed_run_blocks():
    """The pending block a killed hook leaves behind (W-42) is never a timing pass."""
    pending = f"## harness fast: {_common.PENDING_MARK}\n\nHARNESS FAIL\n"
    assert _common.timing_only_failures(pending) == []


@pytest.mark.parametrize("summary", [None, ""])
def test_no_summary_blocks(summary):
    assert _common.timing_only_failures(summary) == []


def test_an_unreadable_thresholds_file_blocks(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "REPO", tmp_path)
    assert _common.timing_only_failures(_summary(("suite.full_seconds", "FAIL"))) == []


# ---------------------------------------------------------------------------
# The checklist reads the same rule
# ---------------------------------------------------------------------------


def test_checklist_counts_a_timing_only_failure_as_a_pass():
    assert dod_checklist.harness_pass(_summary(("suite.full_seconds", "FAIL"))) is True


def test_checklist_still_fails_a_real_failure():
    assert dod_checklist.harness_pass(_summary(("coverage.min", "FAIL"))) is False


def test_checklist_still_reports_an_absent_summary_as_unknown():
    assert dod_checklist.harness_pass(None) is None
