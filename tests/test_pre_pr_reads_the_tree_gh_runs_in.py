"""pre_pr reads the full-tier stamp of the tree `gh pr create` runs in (jjg, 2026-10-19).

W-30 made the hooks follow the payload's `cwd` into a worktree. The Bash tool
resets its cwd to the session folder on every call, so a PR opened from a
worktree is `cd <worktree> && gh pr create ...` with the payload's `cwd` still
the live checkout. pre_pr then read the LIVE checkout's stamp: the PR was
refused when that checkout had no stamp, and when the live checkout carried a
valid stamp of its own, the worktree's PR passed on a tree nobody had tested.
So branch work had to happen in the live checkout, which every session serves
as an editable install.

The rule, as ruled: the stamp is read from the git toplevel of the directory
the command `cd`s into before `gh pr create`; it must match that tree; the
checklist and fast verdict come from the same tree; there is no fallback to
the session's tree. A `cd` target that is not a checkout carrying
`.claude/hooks` refuses.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude" / "hooks"


def _clone(dst: Path, branch: str) -> Path:
    subprocess.run(
        ["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(dst)], check=True
    )
    subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=dst, check=True)
    hooks = dst / ".claude" / "hooks"
    if hooks.exists():
        shutil.rmtree(hooks)
    shutil.copytree(HOOKS, hooks, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def _tree(checkout: Path) -> str:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, r'%s'); import _common; print(_common.tree_id())"
            % (checkout / ".claude" / "hooks"),
        ],
        capture_output=True,
        text=True,
        cwd=checkout,
        check=True,
    ).stdout.strip()


def _stamp(checkout: Path, tree: str | None = None) -> None:
    """A passing full tier, fast tier and checklist on `checkout` (for `tree`, default its own)."""
    evidence = checkout / ".claude" / "state" / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (checkout / ".claude" / "state" / "full-tier.json").write_text(
        json.dumps({"tree": tree or _tree(checkout), "ok": True, "date": "x"}),
        encoding="utf-8",
    )
    (evidence / "checklist.md").write_text("| 1 | x | met | y |\n", encoding="utf-8")
    (evidence / "fast.md").write_text(
        "## harness fast: PASS\n\nHARNESS PASS\n", encoding="utf-8"
    )


def _unstamp(checkout: Path) -> None:
    state = checkout / ".claude" / "state"
    if state.exists():
        shutil.rmtree(state)


def _pre_pr(command: str, session_cwd: Path) -> subprocess.CompletedProcess:
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(session_cwd),
    }
    return subprocess.run(
        [sys.executable, str(HOOKS / "pre_pr.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=session_cwd,
    )


@pytest.fixture(scope="module")
def trees(tmp_path_factory):
    base = tmp_path_factory.mktemp("prepr")
    live = _clone(base / "live", "feat/live")
    wt = _clone(base / "wt dir", "feat/wt")  # a space: the path must survive quoting
    return live, wt


CREATE = 'GITHUB_TOKEN="" gh pr ' + "create --title x --body-file b.md"


def test_a_pr_from_a_worktree_reads_the_worktrees_stamp(trees):
    live, wt = trees
    _unstamp(live)
    _stamp(wt)
    r = _pre_pr(f'cd "{wt.as_posix()}" && {CREATE}', live)
    assert r.returncode == 0, r.stderr


def test_the_session_trees_stamp_never_stands_in_for_the_worktrees(trees):
    """No fallback: a valid live stamp does not pass a worktree whose own stamp is for another tree."""
    live, wt = trees
    _stamp(live)
    _stamp(wt, tree="0" * 40)
    r = _pre_pr(f'cd "{wt.as_posix()}" && {CREATE}', live)
    assert r.returncode == 2 and "DIFFERENT tree" in r.stderr, r.stderr


def test_a_worktree_with_no_stamp_refuses_even_when_the_session_tree_has_one(trees):
    live, wt = trees
    _stamp(live)
    _unstamp(wt)
    r = _pre_pr(f"cd '{wt.as_posix()}' && {CREATE}", live)
    assert r.returncode == 2 and "no full-tier stamp" in r.stderr, r.stderr


def test_a_cd_into_something_that_is_not_a_checkout_refuses(trees, tmp_path):
    live, _ = trees
    _stamp(live)
    r = _pre_pr(f'cd "{tmp_path.as_posix()}" && {CREATE}', live)
    assert r.returncode == 2 and "not a checkout" in r.stderr, r.stderr


def test_a_relative_cd_resolves_against_the_session_cwd(trees):
    live, wt = trees
    _unstamp(live)
    _stamp(wt)
    rel = Path("..") / wt.name
    r = _pre_pr(f'cd "{rel.as_posix()}" && {CREATE}', live)
    assert r.returncode == 0, r.stderr


def test_the_last_cd_before_the_create_is_the_one_that_counts(trees):
    live, wt = trees
    _unstamp(live)
    _stamp(wt)
    r = _pre_pr(f'cd "{live.as_posix()}" && cd "{wt.as_posix()}" && {CREATE}', live)
    assert r.returncode == 0, r.stderr
    _stamp(live)
    _unstamp(wt)
    r = _pre_pr(f'cd "{live.as_posix()}" && cd "{wt.as_posix()}" && {CREATE}', live)
    assert r.returncode == 2 and "no full-tier stamp" in r.stderr, r.stderr


def test_without_a_cd_the_session_tree_is_still_the_one_read(trees):
    live, wt = trees
    _stamp(live)
    _unstamp(wt)
    r = _pre_pr(CREATE, live)
    assert r.returncode == 0, r.stderr
    _unstamp(live)
    r = _pre_pr(CREATE, live)
    assert r.returncode == 2 and "no full-tier stamp" in r.stderr, r.stderr
