"""A failing test on a watched run files an issue, not just a failing Floor.

`scripts/open_regression_issue.py` matched only the four-column Floor row
(`| `id` | crit | floor | observed | **FAIL** |`). The harness writes a failed
check (`check.pytest_exit`, `check.ruff_src`, a corpus checksum) as a
three-column row in the same summary, and its own comment says a reader of
FAIL rows should see a failed check exactly as it sees a failed Floor. The
filer never did: nightly run 42754071420 (windows 3.12) failed a test, and the
filer printed "no FAIL rows in the summary; nothing to file".

Every summary here is written by the harness's own `_Tee.summary_markdown`,
so the producer and the filer cannot drift apart unseen.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
from pathlib import Path

from harness import __main__ as harness_main

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "open_regression_issue", ROOT / "scripts" / "open_regression_issue.py"
)
filer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(filer)

FAILED_ID = "tests/test_example.py::test_it_fails"


def _summary(
    *,
    floor_fail: bool,
    test_fail: bool,
    failed: int = 1,
    detail: str = "exit 1: 1 failed, 20 passed in 9.0s",
    pytest_runs: int = 1,
) -> str:
    """A summary written the way a tier writes it: through `_check` (which
    sanitises the detail) and `_failed_id_lines` (which caps the id list)."""
    tee = harness_main._Tee(io.StringIO())
    verdict = "FAIL" if floor_fail else "PASS"
    with contextlib.redirect_stdout(tee):
        print(
            f"suite.full_seconds                       crit N1  floor <= 360          observed 400.0       {verdict}"
        )
        for _ in range(pytest_runs):
            if test_fail:
                harness_main._check("check.pytest_exit", False, detail)
                out = "".join(
                    f"FAILED {FAILED_ID}{'' if i == 0 else i} - AssertionError\n"
                    for i in range(failed)
                )
                for line in harness_main._failed_id_lines(out):
                    print(line)
            else:
                harness_main._check(
                    "check.pytest_exit", True, "exit 0: 21 passed in 9.0s"
                )
        harness_main._check("check.ruff_src", True, "exit 0: All checks passed!")
    return tee.summary_markdown("harness full", ok=not (floor_fail or test_fail))


def _titles(text: str) -> list[str]:
    return [
        t
        for t, _ in filer.issues(
            text, prefix="drift (windows-latest, 3.12)", range_="abc", run_url="u"
        )
    ]


def test_a_failed_test_files_an_issue_naming_the_check():
    assert _titles(_summary(floor_fail=False, test_fail=True)) == [
        "drift (windows-latest, 3.12): check.pytest_exit on main"
    ]


def test_the_issue_body_names_the_failed_test():
    [(_, body)] = filer.issues(
        _summary(floor_fail=False, test_fail=True),
        prefix="p",
        range_="abc",
        run_url="u",
    )
    assert FAILED_ID in body
    assert "exit 1: 1 failed" in body


def test_a_failed_floor_still_files_as_before():
    [(title, body)] = filer.issues(
        _summary(floor_fail=True, test_fail=False),
        prefix="p",
        range_="abc",
        run_url="u",
    )
    assert title == "p: suite.full_seconds on main"
    assert "| `suite.full_seconds` | N1 | <= 360 | 400.0 |" in body


def test_both_file_one_issue_each():
    assert sorted(_titles(_summary(floor_fail=True, test_fail=True))) == [
        "drift (windows-latest, 3.12): check.pytest_exit on main",
        "drift (windows-latest, 3.12): suite.full_seconds on main",
    ]


def test_a_clean_run_files_nothing(capsys, tmp_path):
    assert _titles(_summary(floor_fail=False, test_fail=False)) == []
    p = tmp_path / "s.md"
    p.write_text(_summary(floor_fail=False, test_fail=False), encoding="utf-8")
    assert filer.main(["--summary", str(p), "--dry-run"]) == 0
    assert "nothing to file" in capsys.readouterr().out


def test_dry_run_on_a_failed_test_says_it_would_file(capsys, tmp_path):
    p = tmp_path / "s.md"
    p.write_text(_summary(floor_fail=False, test_fail=True), encoding="utf-8")
    assert (
        filer.main(["--summary", str(p), "--dry-run", "--title-prefix", "drift"]) == 0
    )
    assert "would file: drift: check.pytest_exit on main" in capsys.readouterr().out


def test_a_detail_with_a_pipe_still_files():
    # `_check_line` turns `|` into `/`; the filer's check-row match relies on it.
    [(title, body)] = filer.issues(
        _summary(floor_fail=False, test_fail=True, detail="exit 1: a | b"),
        prefix="p",
        range_="abc",
        run_url="u",
    )
    assert title == "p: check.pytest_exit on main"
    assert "exit 1: a / b" in body


def test_the_cap_disclosure_reaches_the_issue():
    cap = harness_main._FAILED_ID_CAP
    [(_, body)] = filer.issues(
        _summary(floor_fail=False, test_fail=True, failed=cap + 5),
        prefix="p",
        range_="abc",
        run_url="u",
    )
    assert "... 5 more failures; see the log" in body


def test_two_pytest_runs_in_one_summary_file_one_issue():
    # The harness appends to `--summary`; one title must not be filed twice.
    assert _titles(_summary(floor_fail=False, test_fail=True, pytest_runs=2)) == [
        "drift (windows-latest, 3.12): check.pytest_exit on main"
    ]
