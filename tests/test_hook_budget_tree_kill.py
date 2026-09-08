"""A hook past its budget must EXIT past its budget, not wait on a grandchild.

`.claude/hooks/_common.run_budgeted` bounded the child with
`subprocess.run(timeout=)`. On a timeout that kills the child only (`uv`),
and then waits on the pipes, which the grandchild (`python -m harness`)
still holds; on Windows the wait lasted as long as the harness did (188 s on
2026-09-08), the hook overran the runner's 160 s backstop, the runner killed
it, and `git commit` proceeded with no verdict, no `fast.md` and no warning.
Twice in one day (docs/workflows/FINDINGS.md W-42). A hook killed from the
outside is neither `ok()` nor `block()`, and the runner reads silence as
consent, so the budget has to hold from the inside: the whole process TREE
dies at the deadline, and a summary written BEFORE the run reads as FAIL until
the run replaces it.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude" / "hooks"


@pytest.fixture(scope="module")
def common():
    sys.path.insert(0, str(HOOKS))
    try:
        spec = importlib.util.spec_from_file_location("_wf_common", HOOKS / "_common.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(HOOKS))


# A child that spawns a grandchild holding the inherited stdout for 30 s, then
# waits on it: the shape of `uv run python -m harness`.
_CHILD = (
    "import subprocess, sys, time; "
    "p = subprocess.Popen([sys.executable, '-c', "
    "'import time, sys; sys.stdout.write(\"grandchild\"); sys.stdout.flush(); time.sleep(30)']); "
    "p.wait()"
)


def test_a_timed_out_child_does_not_hold_the_hook_through_its_grandchild(common):
    budget = common.Budget(3)
    t0 = time.perf_counter()
    rc, out = common.run_budgeted([sys.executable, "-c", _CHILD], budget)
    elapsed = time.perf_counter() - t0
    assert rc is None, (rc, out)
    # The old runner returned only when the grandchild's 30 s sleep ended.
    assert elapsed < 12, f"run_budgeted took {elapsed:.1f}s past a 3 s budget"


def test_a_pending_summary_reads_as_fail_until_the_run_replaces_it(common, tmp_path):
    summary = tmp_path / "fast.md"
    common.write_pending_summary(summary, "pre_commit")
    text = summary.read_text(encoding="utf-8")
    assert "HARNESS FAIL" in text and "NOT RUN" in text
    # `harness --summary` APPENDS (W-20); the settle step drops the sentinel.
    with summary.open("a", encoding="utf-8") as fh:
        fh.write("\n## harness fast: PASS\n| `x` | N1 | <= 1 | 0 | PASS |\n")
    common.settle_summary(summary)
    settled = summary.read_text(encoding="utf-8")
    assert "NOT RUN" not in settled and "HARNESS FAIL" not in settled
    assert "## harness fast: PASS" in settled


def test_a_killed_hook_leaves_the_sentinel_and_the_checklist_reads_it_unmet(common, tmp_path):
    summary = tmp_path / "fast.md"
    common.write_pending_summary(summary, "pre_commit")
    # No settle: the hook died. dod_checklist's judge must call this a FAIL.
    sys.path.insert(0, str(HOOKS))
    try:
        spec = importlib.util.spec_from_file_location("_wf_dod", HOOKS / "dod_checklist.py")
        dod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dod)
    finally:
        sys.path.remove(str(HOOKS))
    assert dod.harness_pass(summary.read_text(encoding="utf-8")) is False


def test_the_runner_backstop_covers_the_budget_and_the_kill_path(common):
    """settings.json's timeout is the budget + the kill ceiling + 10 s (DESIGN section 4).

    Nothing bound it before: 160 s over a 150 s budget looked right and left no
    room for the kill path, which is how the runner got to kill the hook.
    """
    import json

    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    hooks = [
        h
        for group in settings["hooks"]["PreToolUse"]
        for h in group["hooks"]
        if "pre_commit.py" in h["command"]
    ]
    assert len(hooks) == 1, hooks
    src = (HOOKS / "pre_commit.py").read_text(encoding="utf-8")
    import re

    budget = int(re.search(r"^BUDGET_SECONDS = (\d+)", src, re.M).group(1))
    assert hooks[0]["timeout"] >= budget + common.KILL_CEILING + 10, (hooks[0]["timeout"], budget)
