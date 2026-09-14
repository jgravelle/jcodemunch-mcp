"""main.yml's weekly results PR opens at most once per date.

The job ran on every push to main that fell on a Monday. Merging the Monday
results PR is itself a push to main on a Monday, so it opened a second PR for
the same date (#684 -> #688, 2026-09-14), and every later Monday merge failed
the job, because `git push` of an existing `harness-bot/results-<date>` branch
is rejected (main.yml runs on 406a654b and 1bf251c5 both red).

These tests EXECUTE the step's shell under bash with stub `git` and `gh`, so
the property is checked on what the runner does, not on how the text is spelled.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
MAIN_YML = ROOT / ".github" / "workflows" / "main.yml"
STEP_PREFIX = "Open the results PR"


def _bash() -> str:
    for cand in (r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")):
        if cand and Path(cand).is_file() and "system32" not in cand.lower():
            return cand
    raise AssertionError("bash is required to execute the step (Git Bash on Windows)")


def _step_run() -> str:
    doc = yaml.safe_load(MAIN_YML.read_text(encoding="utf-8"))
    steps = doc["jobs"]["weekly-results-pr"]["steps"]
    runs = [s["run"] for s in steps if str(s.get("name", "")).startswith(STEP_PREFIX)]
    assert len(runs) == 1, f"expected one step named {STEP_PREFIX!r}, found {len(runs)}"
    # `${{ ... }}` is expanded by Actions before bash sees it.
    return re.sub(r"\$\{\{[^}]*\}\}", "X", runs[0])


# `gh pr list` answers with the number of results PRs for the date (any state);
# `git ls-remote --exit-code` succeeds only when the branch exists; `git push`
# of an existing branch is rejected, as it is on the runner.
_GIT = """#!/usr/bin/env bash
echo "git $*" >> "$STUB_LOG"
case "$1" in
  diff) exit 1 ;;
  ls-remote) [ "$STUB_BRANCH_EXISTS" = 1 ] && { echo "sha refs/heads/x"; exit 0; } || exit 2 ;;
  push) [ "$STUB_BRANCH_EXISTS" = 1 ] && { echo "rejected" >&2; exit 1; } || exit 0 ;;
esac
exit 0
"""
_GH = """#!/usr/bin/env bash
echo "gh $*" >> "$STUB_LOG"
if [ "$1 $2" = "pr list" ]; then echo "$STUB_PRS"; fi
exit 0
"""


def _execute(tmp: Path, prs: int, branch_exists: bool) -> tuple[int, str]:
    bindir = tmp / "bin"
    bindir.mkdir()
    for name, body in (("git", _GIT), ("gh", _GH)):
        (bindir / name).write_text(body, encoding="utf-8", newline="\n")
    log = tmp / "calls.log"
    log.write_text("", encoding="utf-8")
    script = tmp / "step.sh"
    script.write_text(
        # `$PWD/bin`, not an absolute Windows path: `C:/...` splits at the colon in PATH.
        'export PATH="$PWD/bin:$PATH"\nchmod +x bin/git bin/gh\n'
        + _step_run().replace("\r\n", "\n"),
        encoding="utf-8",
        newline="\n",
    )
    env = dict(os.environ)
    env.update(
        STUB_LOG=log.as_posix(),
        STUB_PRS=str(prs),
        STUB_BRANCH_EXISTS="1" if branch_exists else "0",
        GH_TOKEN="stub",
    )
    proc = subprocess.run(
        [_bash(), "-e", script.as_posix()], cwd=tmp, env=env, capture_output=True, text=True, timeout=60
    )
    return proc.returncode, log.read_text(encoding="utf-8")


def test_first_run_of_the_day_opens_the_pr(tmp_path: Path) -> None:
    rc, calls = _execute(tmp_path, prs=0, branch_exists=False)
    assert rc == 0, calls
    assert "git push" in calls
    assert "gh pr create" in calls


@pytest.mark.parametrize("state", ["open", "merged or closed"])
def test_a_results_pr_for_the_date_already_exists(tmp_path: Path, state: str) -> None:
    # The branch is deleted on merge, so only the PR query can see a merged one.
    rc, calls = _execute(tmp_path, prs=1, branch_exists=(state == "open"))
    assert rc == 0, calls
    assert "git push" not in calls
    assert "gh pr create" not in calls


def test_a_leftover_branch_without_a_pr_does_not_fail_the_job(tmp_path: Path) -> None:
    rc, calls = _execute(tmp_path, prs=0, branch_exists=True)
    assert rc == 0, calls
    assert "gh pr create" not in calls
