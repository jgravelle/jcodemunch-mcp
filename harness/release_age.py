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
⚠ A release cut in the WORKING TREE (the block emptied under a version heading
the ref lacks) reads 0, so the release commit is never refused by the gate
that asks for it. Any other empty tree measures the ref.
⚠ The ref is main as users receive it: the upstream remote's `main` locally
(found by URL, never assumed to be `origin`), the test merge's first parent
on a pull_request run. A branch's own entry has reached nobody.
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
UPSTREAM = "jgravelle/jcodemunch-mcp"
# A squash merge is stamped by GitHub's clock; minutes of disagreement with
# this one are skew, more is a date nobody can trust and reads UNKNOWN.
_SKEW_SECONDS = 300


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


def _remote_for_upstream(root: Path) -> str | None:
    """The remote whose URL names this repository, not whichever is called `origin`.

    A contributor's `origin` is their fork, whose `main` can be any age.
    """
    rc, out = _git(root, "remote", "-v")
    if rc != 0:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            url = parts[1].lower().removesuffix(".git").rstrip("/")
            if url.endswith(("/" + UPSTREAM, ":" + UPSTREAM)):
                return parts[0]
    return None


def _resolves(root: Path, rev: str) -> bool:
    return _git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")[0] == 0


def pick_ref(root: Path, env: Mapping[str, str]) -> str:
    """The main branch as users will receive it.

    On a pull_request run HEAD is GitHub's test merge; its first parent is
    main, and the PR's own entry has not reached anyone. Locally it is the
    upstream remote's main (by URL), else `origin/main`, else HEAD.
    """
    if env.get("GITHUB_ACTIONS"):
        rc, out = _git(root, "rev-list", "--parents", "-n", "1", "HEAD")
        return "HEAD^1" if rc == 0 and len(out.split()) == 3 else "HEAD"
    remote = _remote_for_upstream(root)
    for cand in ([f"{remote}/main"] if remote else []) + ["origin/main"]:
        if _resolves(root, f"refs/remotes/{cand}"):
            return cand
    return "HEAD"


def _read(root: Path, rev: str) -> tuple[str, str | None]:
    """("ok", text) | ("absent", None) | ("error", None).

    Only a commit that resolves and genuinely lacks the file is "absent"; any
    other failure is UNKNOWN and must never read as an empty block.
    """
    if not _resolves(root, rev):
        return "error", None
    rc, listed = _git(root, "ls-tree", "--name-only", rev, "--", CHANGELOG)
    if rc != 0:
        return "error", None
    if not listed.strip():
        return "absent", None
    rc, text = _git(root, "show", f"{rev}:{CHANGELOG}")
    return ("ok", text) if rc == 0 else ("error", None)


def _versions(text: str) -> set[str]:
    return {
        ln.split("]", 1)[0]
        for ln in text.splitlines()
        if ln.startswith("## [") and not ln.startswith(_HEAD)
    }


def _fetch_note(root: Path, ref: str, now: float) -> str:
    if ref.startswith("HEAD"):
        return ""
    rc, path = _git(root, "rev-parse", "--git-path", "FETCH_HEAD")
    try:
        mtime = (root / path.strip()).stat().st_mtime if rc == 0 else None
    except OSError:
        mtime = None
    if mtime is None:
        return f"; {ref} never fetched here"
    return f"; {ref} fetched {round((now - mtime) / 3600, 1)} h ago"


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
    note = _fetch_note(root, ref, now)

    def unknown(why: str, commit: str | None = None) -> Age:
        return Age(None, commit, ref, f"unknown: {why}{note}", True)

    status, on_ref = _read(root, ref)
    if status == "error":
        return unknown(f"cannot read {CHANGELOG} at {ref}")
    if not has_entry(on_ref):
        return Age(0.0, None, ref, f"nothing unreleased on {ref}{note}", True)

    try:
        tree = (root / CHANGELOG).read_text(encoding="utf-8")
    except OSError as exc:
        return unknown(f"{CHANGELOG} unreadable ({exc})")
    # A release cut: the block is emptied under a version heading the ref lacks.
    # Only that case reads 0 -- an empty block on a branch cut before main's
    # entries is not a release and must not hide main's age.
    if not has_entry(tree) and _versions(tree) - _versions(on_ref):
        return Age(0.0, None, ref, "release cut in the working tree", True)

    rc, shallow = _git(root, "rev-parse", "--is-shallow-repository")
    if rc != 0:
        return unknown("git rev-parse failed")
    if shallow.strip() == "true":
        return unknown("shallow history cannot date the entry")

    rc, log = _git(
        root, "log", "--first-parent", "--format=%H %ct", ref, "--", CHANGELOG
    )
    if rc != 0:
        return unknown(f"git log {ref} failed")
    floor = T.floor(TID)
    start = ct = None
    settled = False
    for row in log.splitlines():
        sha, _, stamp = row.partition(" ")
        if not stamp.strip().isdigit():
            return unknown("no committer time", sha)
        start, ct = sha, int(stamp)
        if (now - ct) / 3600 > floor:
            settled = True  # older still is only later: the verdict is decided
            break
        rc, parents = _git(root, "rev-list", "--parents", "-n", "1", sha)
        if rc != 0:
            return unknown(f"cannot list the parents of {sha[:10]}", sha)
        if len(parents.split()) < 2:
            break  # a root commit: nothing before it
        pstatus, parent = _read(root, f"{sha}^1")
        if pstatus == "error":
            return unknown(f"cannot read {CHANGELOG} at {sha[:10]}^1", sha)
        if pstatus == "absent" or not has_entry(parent):
            break
    if start is None:
        return unknown(f"no commit on {ref} touches {CHANGELOG}")
    if now < ct - _SKEW_SECONDS:
        return unknown(f"clock skew: {start[:10]} is dated in the future", start)
    if now < ct:  # seconds of skew between this clock and the committer's
        ct = now
    hours = round((now - ct) / 3600, 2)
    what = (
        "an entry waiting since at least"
        if settled
        else "first entry since the last release at"
    )
    return Age(hours, start, ref, f"{what} {start[:10]} on {ref}{note}", True)


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
        line += (
            "\n   an unreleased change has waited past the release cadence: run /release"
            " (fetch first if the ref is stale; RUNBOOK section 1 names the escape path)"
        )
    return line
