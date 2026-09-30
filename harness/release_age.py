"""How long has an unreleased change waited?  Floor `release.unreleased_max_hours` (N8).

jjg ruled a release cadence on 2026-09-30, after 1.108.320 shipped 14 days
and 124 PRs behind 1.108.319: every fix loop ended at a merge line and no
step asked whether it was time to ship (CLAUDE.md Standing lesson 09-30).

The age runs from the commit at which `CHANGELOG.md`'s `[Unreleased]` block
last went from empty to non-empty on the ref's first-parent chain, by
committer time. In the normal flow that is the first change after the last
`v*` tag. It stays right when a release commit has merged and its tag is not
yet pushed: dating from the tag would date from the release commit itself.

⚠⚠ UNKNOWN is never a pass. A shallow history or a git failure, where the
gate applies, returns `hours=None` and `verdict()` is False.
⚠ An empty block in the WORKING TREE reads 0, so a release cut in progress is
never refused by the gate that asks for it.
⚠ On GitHub only a `pull_request` run evaluates. `main.yml`, `nightly.yml` and
`release.yml` would otherwise fail on lateness they cannot fix, and a
dispatched release blocked by a cadence gate is the gate inverted.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from harness import thresholds as T

TID = "release.unreleased_max_hours"
CHANGELOG = "CHANGELOG.md"
_HEAD = "## [Unreleased]"


@dataclass(frozen=True)
class Age:
    hours: float | None
    commit: str | None
    ref: str | None
    basis: str
    applicable: bool


def has_entry(text: str | None) -> bool:
    """True when `[Unreleased]` holds any non-blank line before the next `## [`."""
    if not text:
        return False
    at = text.find(_HEAD)
    if at < 0:
        return False
    for line in text[at + len(_HEAD) :].splitlines()[1:]:
        if line.startswith("## ["):
            return False
        if line.strip():
            return True
    return False


def applies(env: Mapping[str, str]) -> tuple[bool, str]:
    if not env.get("GITHUB_ACTIONS"):
        return True, "local"
    event = env.get("GITHUB_EVENT_NAME", "")
    if event == "pull_request":
        return True, "pull_request"
    return (
        False,
        f"not evaluated: {event or 'unknown'} run (pull_request runs and local runs only)",
    )


def _git(root: Path, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return 1, str(exc)
    return p.returncode, p.stdout


def pick_ref(root: Path, env: Mapping[str, str]) -> str:
    """HEAD on a pull_request run (the merge ref); origin/main locally when it resolves.

    Locally a branch's own entry has not reached anyone, so its clock starts
    at merge; measuring origin/main asks how long MAIN has held unreleased work.
    """
    if env.get("GITHUB_ACTIONS"):
        return "HEAD"
    rc, _ = _git(root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main")
    return "origin/main" if rc == 0 else "HEAD"


def _show(root: Path, rev: str) -> str | None:
    rc, out = _git(root, "show", f"{rev}:{CHANGELOG}")
    return out if rc == 0 else None


def measure(
    root: Path | None = None,
    *,
    ref: str | None = None,
    now: float | None = None,
    env: Mapping[str, str] | None = None,
) -> Age:
    root = root or T.REPO_ROOT
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    ok, why = applies(env)
    if not ok:
        return Age(None, None, None, why, False)
    ref = ref or pick_ref(root, env)

    try:
        tree = (root / CHANGELOG).read_text(encoding="utf-8")
    except OSError as exc:
        return Age(None, None, ref, f"unknown: {CHANGELOG} unreadable ({exc})", True)
    if not has_entry(tree):
        return Age(0.0, None, ref, "nothing unreleased in the working tree", True)

    rc, shallow = _git(root, "rev-parse", "--is-shallow-repository")
    if rc != 0:
        return Age(None, None, ref, "unknown: git rev-parse failed", True)
    if shallow.strip() == "true":
        return Age(
            None, None, ref, "unknown: shallow history cannot date the entry", True
        )
    if not has_entry(_show(root, ref)):
        return Age(0.0, None, ref, f"no entry on {ref} yet", True)

    rc, revs = _git(root, "rev-list", "--first-parent", ref, "--", CHANGELOG)
    if rc != 0:
        return Age(None, None, ref, f"unknown: git rev-list {ref} failed", True)
    start = None
    for sha in revs.split():
        start = sha
        parent = _git(root, "rev-parse", "--verify", "--quiet", f"{sha}^1")
        if parent[0] != 0 or not has_entry(_show(root, f"{sha}^1")):
            break
    if start is None:
        return Age(
            None, None, ref, f"unknown: no commit on {ref} touches {CHANGELOG}", True
        )
    rc, ct = _git(root, "show", "-s", "--format=%ct", start)
    if rc != 0 or not ct.strip().isdigit():
        return Age(None, start, ref, "unknown: no committer time", True)
    hours = round(max(0.0, now - int(ct.strip())) / 3600, 2)
    return Age(
        hours,
        start,
        ref,
        f"first entry since the last release at {start[:10]} on {ref}",
        True,
    )


def verdict(age: Age) -> bool | None:
    """None when not applicable; False when applicable and unmeasured."""
    if not age.applicable:
        return None
    if age.hours is None:
        return False
    return T.passes(TID, age.hours)


def describe(age: Age) -> str:
    if not age.applicable:
        return f"{TID:<40} {age.basis}"
    ok = verdict(age)
    obs = "UNKNOWN" if age.hours is None else age.hours
    e = T.get(TID)
    line = (
        f"{TID:<40} crit {e['criterion']:<3} floor {e['comparator']} {e['floor']!s:<12} "
        f"observed {obs!s:<12} {'PASS' if ok else 'FAIL'}  ({age.basis})"
    )
    if not ok:
        line += "\n   an unreleased change has waited past the release cadence: run /release"
    return line
