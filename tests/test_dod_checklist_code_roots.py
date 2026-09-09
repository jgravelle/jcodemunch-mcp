"""Checklist row 1 grades the red/green pair whenever one exists, whatever the path.

`dod_checklist.py` read row 1 as `n.a.: no change under src/, harness/ or
scripts/` for a PR whose code root was `benchmarks/` (competitive PR 3c),
`.claude/hooks/` (W-42, W-41) or `tests/` alone, so three reviewers in one day
graded the row from `red.txt`/`green.txt` by hand and wrote "the row is not
evidence" (docs/workflows/FINDINGS.md W-38). A red/green pair on disk is the
evidence the row exists to read; the path list decided whether to look.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude" / "hooks"


@pytest.fixture(scope="module")
def dod():
    sys.path.insert(0, str(HOOKS))
    try:
        spec = importlib.util.spec_from_file_location("_wf_dod", HOOKS / "dod_checklist.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(HOOKS))


RED = "collected 3 items\n3 failed in 1.0s\nEXIT=1\n"
GREEN = "3 passed in 1.0s\nEXIT=0\n"


def test_a_red_green_pair_is_graded_whatever_the_path(dod):
    for changed in (
        [".claude/hooks/_common.py", "tests/test_hook_budget_tree_kill.py"],
        ["benchmarks/competitive/adapters/x.py", "tests/test_x.py"],
        ["tests/test_only.py"],
        ["src/jcodemunch_mcp/tools/x.py", "tests/test_x.py"],
    ):
        verdict, ev = dod.row1_verdict(changed, RED, GREEN)
        assert verdict == "met", (changed, verdict, ev)


def test_a_code_root_change_without_the_pair_is_unmet_not_na(dod):
    for changed in (
        [".claude/hooks/deny_guard.py"],
        ["benchmarks/competitive/run.py"],
        ["tests/test_x.py"],
        ["harness/thresholds.json"],
        ["src/jcodemunch_mcp/server.py"],
    ):
        verdict, _ = dod.row1_verdict(changed, None, None)
        assert verdict == "unmet", changed


def test_a_docs_only_change_without_the_pair_is_na(dod):
    verdict, ev = dod.row1_verdict(["docs/workflows/FINDINGS.md", "CHANGELOG.md", "README.md"], None, None)
    assert verdict == "n.a."
    assert "code root" in ev


def test_a_pair_that_does_not_go_red_then_green_is_unmet(dod):
    verdict, _ = dod.row1_verdict(["src/x.py"], "3 passed\nEXIT=0\n", GREEN)
    assert verdict == "unmet"
    verdict, _ = dod.row1_verdict(["src/x.py"], RED, "1 failed\nEXIT=1\n")
    assert verdict == "unmet"


def test_the_code_roots_name_the_hooks_and_benchmarks(dod):
    assert {"src/", "harness/", "scripts/", "benchmarks/", "tests/", ".claude/hooks/"} <= set(dod.CODE_ROOTS)
