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


# The stubs model the parts of `gh` and `git` the step depends on, so the step
# must ask the right question to get the right answer (a stub that answered any
# `pr list` passed with `--state all` removed, which IS #688):
# - `gh pr list` returns only PRs whose head is exactly `--head`, filtered by
#   `--state` (default `open`), from `STUB_PRS` (`<branch>:<STATE>` words);
#   `--jq` is refused unless it is the projection this stub implements;
# - `git ls-remote --exit-code` succeeds only when the branch exists;
# - `git push` of an existing branch is rejected, as it is on the runner.
_GIT = """#!/usr/bin/env bash
echo "git $*" >> "$STUB_LOG"
case "$1" in
  diff) exit 1 ;;
  ls-remote) [ "$STUB_BRANCH_EXISTS" = 1 ] && { echo "sha refs/heads/x"; exit 0; } || exit 2 ;;
  push) [ "$STUB_BRANCH_EXISTS" = 1 ] && { echo "rejected" >&2; exit 1; } || exit 0 ;;
esac
exit 0
"""
_GH = r"""#!/usr/bin/env bash
echo "gh $*" >> "$STUB_LOG"
[ "$1 $2" = "pr list" ] || exit 0
shift 2
state=open; head=""; jq=""
while [ $# -gt 0 ]; do
  case "$1" in
    --state) state="$2"; shift ;;
    --head) head="$2"; shift ;;
    --jq) jq="$2"; shift ;;
  esac
  shift
done
[ "$jq" = ".[].state" ] || { echo "stub gh: unsupported --jq '$jq'" >&2; exit 3; }
for pr in $STUB_PRS; do
  b="${pr%:*}"; s="${pr##*:}"
  [ "$b" = "$head" ] || continue
  case "$state" in
    all) ;;
    open) [ "$s" = OPEN ] || continue ;;
    merged) [ "$s" = MERGED ] || continue ;;
    closed) [ "$s" = CLOSED ] || continue ;;
  esac
  echo "$s"
done
exit 0
"""


def _execute(tmp: Path, prs: str, branch_exists: bool) -> tuple[int, str, str]:
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
        STUB_PRS=prs,
        STUB_BRANCH_EXISTS="1" if branch_exists else "0",
        GH_TOKEN="stub",
    )
    proc = subprocess.run(
        [_bash(), "-e", script.as_posix()], cwd=tmp, env=env, capture_output=True, text=True, timeout=60
    )
    return proc.returncode, log.read_text(encoding="utf-8"), proc.stdout + proc.stderr


def _today() -> str:
    from datetime import datetime, timezone

    return "harness-bot/results-" + datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_first_run_of_the_day_opens_the_pr(tmp_path: Path) -> None:
    rc, calls, out = _execute(tmp_path, prs="", branch_exists=False)
    assert rc == 0, out
    assert "git push" in calls
    assert "gh pr create" in calls


@pytest.mark.parametrize(
    ("state", "branch_exists"),
    [("OPEN", True), ("MERGED", False)],  # the branch is deleted on merge
)
def test_an_open_or_merged_pr_for_the_date_opens_nothing(tmp_path: Path, state: str, branch_exists: bool) -> None:
    rc, calls, out = _execute(tmp_path, prs=f"{_today()}:{state}", branch_exists=branch_exists)
    assert rc == 0, out
    assert "git push" not in calls
    assert "gh pr create" not in calls


def test_a_closed_pr_for_the_date_does_not_block_a_dispatch(tmp_path: Path) -> None:
    # RUNBOOK section 7's recovery: close the PR, delete the branch, dispatch.
    rc, calls, out = _execute(tmp_path, prs=f"{_today()}:CLOSED", branch_exists=False)
    assert rc == 0, out
    assert "gh pr create" in calls


def test_a_merged_pr_for_another_date_does_not_count(tmp_path: Path) -> None:
    rc, calls, out = _execute(tmp_path, prs="harness-bot/results-2000-01-03:MERGED", branch_exists=False)
    assert rc == 0, out
    assert "gh pr create" in calls


def test_a_leftover_branch_without_a_pr_warns_and_does_not_fail_the_job(tmp_path: Path) -> None:
    rc, calls, out = _execute(tmp_path, prs="", branch_exists=True)
    assert rc == 0, out
    assert "gh pr create" not in calls
    assert "::warning::" in out, out
