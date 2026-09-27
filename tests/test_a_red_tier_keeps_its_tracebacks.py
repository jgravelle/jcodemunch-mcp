"""A red tier keeps WHY it failed, not only which test (harness FINDINGS F-26).

F-35 made `full.md` name the failed ids. The reason still went nowhere:
`run_full.py` kept pytest's summary, the tier printed the last 6,000
characters to a console nobody logs, and the coverage table sits between the
tracebacks and that tail. `test_removing_watched_root_fails[polling]` failed
twice in the local full tier with no error text surviving either run; the
third failure, on CI, was diagnosed in minutes because the job log kept it.

`--failures FILE` writes pytest's FAILURES / ERRORS / short-summary sections
verbatim when a pytest tier is red, and `run_full.py` always passes it. The
second item: `run_full.py --workers N` caps xdist (a loaded box killed two
full runs for memory) and the stamp RECORDS the cap, so a capped run's wall
clock is never read as the uncapped one.
"""

from __future__ import annotations

import ast
import importlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / ".claude" / "hooks"))

hm = importlib.import_module("harness.__main__")
run_full = importlib.import_module("run_full")

_COVERAGE = "\n".join(
    f"src/jcodemunch_mcp/mod_{i}.py           {i * 7}     {i}    92%" for i in range(300)
)
_RED = f"""============================= test session starts =============================
collected 13071 items
....................F.............E...........................
=================================== ERRORS ====================================
________________ ERROR at setup of test_sdist_excludes_state _________________
E   OSError: could not fetch the build backend
================================== FAILURES ===================================
___________________ test_removing_watched_root_fails[polling] ___________________
tests\\test_watcher_symlink_safety.py:293: in test_removing_watched_root_fails
    await asyncio.wait_for(consume(), 5)
E   _rust_notify.WatchfilesRustInternalError: error in underlying watcher: Access is denied. (os error 5)
=============================== warnings summary ===============================
tests/test_x.py::test_y
  DeprecationWarning: old
---------- coverage: platform win32, python 3.13.7-final-0 -----------
Name                                     Stmts   Miss  Cover
{_COVERAGE}
TOTAL                                      190234  34210    82%
=========================== short test summary info ===========================
FAILED tests/test_watcher_symlink_safety.py::test_removing_watched_root_fails[polling] - WatchfilesRustInternalError('error in underlying watcher: Access is denied. (os error 5)')
ERROR tests/test_sdist_exclusions.py::test_sdist_excludes_state - OSError: could not fetch the build backend
1 failed, 13069 passed, 25 skipped, 1 error in 494.65s (0:08:14)
"""


def test_the_report_keeps_the_traceback_and_the_reason():
    report = hm._failure_report(_RED)

    assert "Access is denied. (os error 5)" in report
    assert "tests\\test_watcher_symlink_safety.py:293" in report
    assert "WatchfilesRustInternalError('error in underlying watcher" in report
    assert "OSError: could not fetch the build backend" in report
    assert "1 failed, 13069 passed" in report


def test_the_report_drops_the_coverage_table_and_the_warnings():
    report = hm._failure_report(_RED)

    assert "mod_17.py" not in report and "TOTAL" not in report
    assert "DeprecationWarning" not in report


def test_a_green_run_has_no_report():
    green = "....\n13071 passed, 25 skipped in 200.00s\n"

    assert hm._failure_report(green) == ""


def test_an_oversized_report_is_cut_and_says_so():
    huge = "=== FAILURES ===\n" + ("E   x\n" * (hm._FAILURE_REPORT_MAX_CHARS // 3))
    report = hm._failure_report(huge)

    assert len(report) <= hm._FAILURE_REPORT_MAX_CHARS + 200
    assert "characters cut" in report


def test_main_writes_the_report_on_red_and_removes_a_stale_one_on_green(tmp_path, monkeypatch):
    target = tmp_path / "full-failures.txt"

    def red(_a):
        hm._record_failure_report(_RED)
        return 1

    monkeypatch.setattr(hm, "_dispatch", red)
    assert hm.main(["full", "--failures", str(target)]) == 1
    assert "Access is denied" in target.read_text(encoding="utf-8")

    monkeypatch.setattr(hm, "_dispatch", lambda _a: 0)
    assert hm.main(["full", "--failures", str(target)]) == 0
    assert not target.exists(), "a green run must not leave the last red run's report behind"


def _fake_run_full(tmp_path, monkeypatch, argv, env=None):
    seen = {}

    def call(cmd, cwd=None, env=None):
        seen["cmd"], seen["env"] = cmd, env
        return 0

    monkeypatch.setattr(run_full, "STAMP", tmp_path / "full-tier.json")
    monkeypatch.setattr(run_full, "EVIDENCE", tmp_path / "evidence")
    monkeypatch.setattr(run_full.subprocess, "call", call)
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    rc = run_full.main(argv)
    stamp = json.loads((tmp_path / "full-tier.json").read_text(encoding="utf-8"))
    return rc, seen, stamp


def test_run_full_always_asks_for_the_failure_report(tmp_path, monkeypatch):
    _, seen, _ = _fake_run_full(tmp_path, monkeypatch, [])

    at = seen["cmd"].index("--failures")
    assert seen["cmd"][at + 1] == str(tmp_path / "evidence" / "full-failures.txt")


def test_run_full_workers_caps_xdist_and_the_stamp_records_it(tmp_path, monkeypatch):
    _, seen, stamp = _fake_run_full(tmp_path, monkeypatch, ["--workers", "6"])

    assert seen["env"]["PYTEST_XDIST_AUTO_NUM_WORKERS"] == "6"
    assert "--workers" not in seen["cmd"] and "6" not in seen["cmd"]
    assert stamp["workers"] == "6"


def test_an_uncapped_run_records_auto_and_a_cap_from_the_env_is_recorded(tmp_path, monkeypatch):
    _, _, stamp = _fake_run_full(tmp_path, monkeypatch, [])
    assert stamp["workers"] == "auto"

    _, _, stamp = _fake_run_full(tmp_path, monkeypatch, [], env={"PYTEST_XDIST_AUTO_NUM_WORKERS": "4"})
    assert stamp["workers"] == "4"


#: The shape the PINNED pytest-cov (7.x) prints, with a test that printed its
#: own `=== banner ===` into captured stdout and a second failure after it.
#: Taken from a real `pytest -n 2 --dist loadfile --tb=short --cov` run
#: (review round 1: ending a section on any `=` line dropped `test_b`).
_COV7_BANNER = """.FFE
=================================== ERRORS ====================================
_________________________ ERROR at setup of test_err __________________________
[gw0] win32 -- Python 3.12.4 C:\\py\\python.exe
test_banner.py:6: in broken
    raise OSError("setup went wrong")
E   OSError: setup went wrong
================================== FAILURES ===================================
___________________________________ test_a ____________________________________
[gw0] win32 -- Python 3.12.4 C:\\py\\python.exe
test_banner.py:15: in test_a
    assert 1 == 2, "first reason"
E   AssertionError: first reason
E   assert 1 == 2
---------------------------- Captured stdout call -----------------------------
========== banner ==========
___________________________________ test_b ____________________________________
[gw0] win32 -- Python 3.12.4 C:\\py\\python.exe
test_banner.py:19: in test_b
    assert 3 == 4, "second reason"
E   AssertionError: second reason
E   assert 3 == 4
=============================== tests coverage ================================
_______________ coverage: platform win32, python 3.12.4-final-0 _______________

Name                                                  Stmts   Miss  Cover
-------------------------------------------------------------------------
src\\jcodemunch_mcp\\tools\\get_runtime_coverage.py        54     54     0%
TOTAL                                                  190234  34210    82%
=========================== short test summary info ===========================
FAILED test_banner.py::test_a - AssertionError: first reason
FAILED test_banner.py::test_b - AssertionError: second reason
ERROR test_banner.py::test_err - OSError: setup went wrong
2 failed, 1 passed, 1 error in 5.19s
"""


def test_a_banner_in_captured_output_does_not_end_the_section():
    report = hm._failure_report(_COV7_BANNER)

    assert "========== banner ==========" in report
    assert "test_banner.py:19: in test_b" in report
    assert "E   AssertionError: second reason" in report


def test_the_pytest_cov_7_coverage_table_is_dropped():
    report = hm._failure_report(_COV7_BANNER)

    assert "tests coverage" not in report
    assert "get_runtime_coverage.py" not in report and "TOTAL" not in report
    assert "FAILED test_banner.py::test_b - AssertionError: second reason" in report


def test_a_red_run_with_no_failures_section_keeps_its_tail(tmp_path, monkeypatch):
    """An INTERNALERROR or a coverage-floor failure prints no FAILURES; the
    file must still exist and carry the reason."""
    internal = "collected 3 items\nINTERNALERROR> Traceback\nINTERNALERROR> KeyError: 'x'\n"
    target = tmp_path / "full-failures.txt"

    def red(_a):
        hm._record_failure_report(internal)
        return 1

    monkeypatch.setattr(hm, "_dispatch", red)
    assert hm.main(["full", "--failures", str(target)]) == 1
    text = target.read_text(encoding="utf-8")
    assert "INTERNALERROR> KeyError: 'x'" in text
    assert "no FAILURES or ERRORS section" in text


@pytest.mark.parametrize("argv", [["--workers"], ["--workers="], ["--workers", "0"], ["--workers", "\u00b2"]])
def test_run_full_refuses_a_workers_flag_that_would_set_no_cap(argv):
    with pytest.raises(SystemExit, match="positive integer"):
        run_full._split_workers(argv)


def test_run_full_writes_the_cap_into_full_md(tmp_path, monkeypatch):
    _fake_run_full(tmp_path, monkeypatch, ["--workers=6"])

    assert "xdist workers: 6" in (tmp_path / "evidence" / "full.md").read_text(encoding="utf-8")


def test_the_commit_hook_asks_for_the_fast_tier_report_too():
    """pre_commit shows a 12-line console tail; the tracebacks go to a file."""
    src = (REPO / ".claude" / "hooks" / "pre_commit.py").read_text(encoding="utf-8")
    calls = [
        n
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "run_budgeted"
    ]
    harness_args = [
        [e.value for e in c.args[0].elts if isinstance(e, ast.Constant)]
        for c in calls
        if isinstance(c.args[0], ast.List)
    ]
    fast = [a for a in harness_args if "harness" in a and "fast" in a]

    assert fast and "--failures" in fast[0]
