"""Release pre-flight: may HEAD be released?  `uv run python scripts/release_preflight.py [--version X.Y.Z] [--no-harness] [--offline]`

Read-only. Exit 0 only when every check below is PASS. Each check prints one
line, harness-style: name, observed, PASS|FAIL|SKIP. Anything the script
cannot establish is a FAIL, never a pass (UNKNOWN blocks; the same rule as
`has_any()` and the harness Floors).

Why it exists (ENFORCEMENT-PLAN item 3): four consecutive releases
(1.108.259-.262) shipped on a RED build because the local suite was green and
nobody read CI. The required status checks on `main` gate merges; nothing
gated the release step, which is the irreversible one (PyPI cannot be
re-uploaded). This script is the gate, and it reads CI rather than trusting
a local run.

Checks
  branch        on `main`, tree clean, HEAD == origin/main after a fetch
  ci            main.yml's witnesses on HEAD (`main: harness full`, `main: harness
                bench (online)`) concluded success and no run of any other name
                failed; the release workflow's own jobs are ignored; under --ci
                it waits up to CI_WAIT_SECONDS for runs still going; at the
                deadline only a Nightly run may still be going (it is named,
                and files its own drift issue); anything else running fails.
                The PR gate
                itself is guaranteed by branch protection (enforce_admins on)
  pins          every version pin site agrees (pyproject, server.json x2,
                .claude-plugin/plugin.json, whatsnew.json current + entries[0],
                uv.lock name-scoped line) and equals --version when given
  changelog     CHANGELOG.md has a heading for that version
  tag           `v<version>` exists neither locally nor on origin
  pypi          the version is not already on PyPI (network; --offline skips)
  prs           no open contributor PR is MERGEABLE + CLEAN (policy 3b: those
                merge BEFORE our release commit), unless the maintainer's latest
                review requests changes on its current head (C-25: named, not held)
  lint          `ruff check src/` clean (CI runs it; a local pytest does not)
  harness       `python -m harness fast` PASS (--no-harness skips; ~50 s)

GitHub is read through `gh` with GITHUB_TOKEN cleared so the keyring token is
used (the env one is a limited PAT).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SLUG = "jgravelle/jcodemunch-mcp"
OWNER = "jgravelle"
PIN_SITES = (
    "pyproject.toml",
    "server.json",
    ".claude-plugin/plugin.json",
    "whatsnew.json",
    "uv.lock",
)


def _run(cmd: list[str], *, gh: bool = False) -> tuple[int, str]:
    env = dict(os.environ)
    if gh:
        env["GITHUB_TOKEN"] = ""
    p = subprocess.run(
        cmd,
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _gh_json(path: str, *extra: str):
    rc, out = _run(["gh", "api", path, *extra], gh=True)
    if rc != 0:
        raise RuntimeError(out.strip()[-400:])
    return json.loads(out)


# ---------------------------------------------------------------- pure checks


def read_pins(root: Path = REPO) -> dict[str, str | None]:
    """Every pin site's version, None where the site could not be read."""
    pins: dict[str, str | None] = {}
    try:
        # No tomllib: the project supports 3.10 (the first CI run of this file failed there).
        m = re.search(
            r'^version = "([^"]+)"',
            (root / "pyproject.toml").read_text(encoding="utf-8"),
            re.M,
        )
        pins["pyproject.toml"] = m.group(1) if m else None
    except Exception:
        pins["pyproject.toml"] = None
    try:
        sj = json.loads((root / "server.json").read_text(encoding="utf-8"))
        pins["server.json:version"] = sj.get("version")
        pkgs = sj.get("packages") or []
        pins["server.json:packages[0].version"] = (
            pkgs[0].get("version") if pkgs else None
        )
    except Exception:
        pins["server.json:version"] = pins["server.json:packages[0].version"] = None
    try:
        pins[".claude-plugin/plugin.json"] = json.loads(
            (root / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
        ).get("version")
    except Exception:
        pins[".claude-plugin/plugin.json"] = None
    try:
        wn = json.loads((root / "whatsnew.json").read_text(encoding="utf-8"))
        pins["whatsnew.json:current"] = wn.get("current")
        entries = wn.get("entries") or []
        pins["whatsnew.json:entries[0].version"] = (
            entries[0].get("version") if entries else None
        )
    except Exception:
        pins["whatsnew.json:current"] = pins["whatsnew.json:entries[0].version"] = None
    try:
        lock = (root / "uv.lock").read_text(encoding="utf-8")
        m = re.search(r'^name = "jcodemunch-mcp"\nversion = "([^"]+)"', lock, re.M)
        pins["uv.lock"] = m.group(1) if m else None
    except Exception:
        pins["uv.lock"] = None
    return pins


def pins_verdict(pins: dict[str, str | None], want: str | None) -> tuple[bool, str]:
    values = set(pins.values())
    if None in values:
        missing = [k for k, v in pins.items() if v is None]
        return False, f"unreadable pin site(s): {', '.join(missing)}"
    if len(values) != 1:
        return False, "pin sites disagree: " + ", ".join(
            f"{k}={v}" for k, v in pins.items()
        )
    (v,) = values
    if want and v != want:
        return False, f"pins say {v}, --version says {want}"
    return True, v


def changelog_has(version: str, text: str) -> bool:
    return (
        re.search(rf"^##\s*\[?{re.escape(version)}\]?(?=\s|$)", text, re.M) is not None
    )


def mergeable_contributor_prs(prs: list[dict]) -> list[str]:
    return [
        f"#{p['number']} {p['author']['login']}"
        for p in prs
        if p.get("author", {}).get("login") != OWNER
        and p.get("mergeable") == "MERGEABLE"
        and p.get("mergeStateStatus") == "CLEAN"
    ]


def changes_requested_on_head(reviews: list[dict], head_sha: str) -> bool:
    """True when the maintainer's latest deciding review asks for changes on ``head_sha``.

    CLEAN says nothing about review, so a PR with a posted changes-requested
    review held every release until its author revised it: the coupling policy
    2e forbids. Only the maintainer's request lifts the hold, only a deciding
    review (a comment decides nothing) counts, and only on the current head: a
    new push may be the revision, so it puts the PR back in line.
    """
    deciding = sorted(
        (
            r
            for r in reviews
            if (r.get("user") or {}).get("login") == OWNER
            and r.get("state") in ("CHANGES_REQUESTED", "APPROVED", "DISMISSED")
        ),
        key=lambda r: r.get("submitted_at") or "",
    )
    if not deciding:
        return False
    latest = deciding[-1]
    return (
        latest.get("state") == "CHANGES_REQUESTED"
        and latest.get("commit_id") == head_sha
    )


# ------------------------------------------------------------------ live runs


def check_branch(ci: bool = False) -> tuple[bool, str]:
    rc, br = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    br = br.strip()
    if br != "main" and not (ci and br == "HEAD"):
        return False, f"on {br!r}, releases cut from main"
    rc, st = _run(["git", "status", "--porcelain"])
    if st.strip():
        return False, f"tree not clean ({len(st.strip().splitlines())} entries)"
    rc, out = _run(["git", "fetch", "origin", "main", "--quiet"])
    if rc != 0:
        return False, "git fetch failed: " + out.strip()[-200:]
    _, head = _run(["git", "rev-parse", "HEAD"])
    _, remote = _run(["git", "rev-parse", "origin/main"])
    if head.strip() != remote.strip():
        return (
            False,
            f"HEAD {head.strip()[:7]} != origin/main {remote.strip()[:7]} (push first; CI is read on the pushed commit)",
        )
    return True, f"main, clean, pushed ({head.strip()[:7]})"


# The witnesses that run ON a main commit (main.yml). The PR gate's jobs ran on
# the PR's merge ref, a different SHA, and branch protection (enforce_admins on)
# is what guarantees they passed; a main commit carries only these.
MAIN_WITNESSES = ("main: harness full (ubuntu, 3.12)", "main: harness bench (online)")
IGNORED_PREFIXES = (
    "release: ",
)  # the release workflow's own jobs, in progress by definition
PENDING = ("queued", "in_progress", "waiting", "pending", "requested")
# How long the pre-flight waits for runs still going on HEAD. It must end inside
# release.yml's pre-flight `timeout-minutes` with room for the other checks:
# 1.108.400's first dispatch (2026-10-28) waited 20 min inside a 15-min job
# and was cancelled with no verdict. tests/test_release_preflight.py binds it.
CI_WAIT_SECONDS = 600
# Runs that may still be going when the wait ends. Nightly runs its matrix on
# main's HEAD at 04:00 UTC, a windows leg can outlast the whole pre-flight, and
# a Nightly failure opens its own `drift` issue (nightly.yml). Nothing else is
# excused: CodeQL (security.yml) has no other gate after the release.
EXCUSED_AT_DEADLINE = ("nightly: ",)


def main_witness_verdict(
    check_runs: list[dict], final: bool = False
) -> tuple[bool, str | None, str]:
    """(ok, still_pending_or_None, message) for HEAD on main (docs/cicd/FINDINGS.md C-13, C-14).

    Nothing reaches `main` without the PR gate (branch protection with
    `enforce_admins`), so on a main commit the question is whether the
    witnesses that DO run there concluded success: `main.yml`'s full and
    online-bench jobs, plus no failed run of any other name. The release
    workflow's own jobs are ignored (they are running). An in-progress
    witness is reported as pending so the caller can wait; UNKNOWN blocks.

    With ``final`` (the wait is over), a Nightly run still going is named in
    the message and does not hold the release (EXCUSED_AT_DEADLINE). Any other
    run still going fails, a witness included, and so does a run whose state is
    unknown. Locally (no wait) ``final`` holds on the first read, so a local
    pre-flight passes over a running Nightly the same way.
    """
    runs = [
        r for r in check_runs if not str(r.get("name", "")).startswith(IGNORED_PREFIXES)
    ]
    if not runs:
        return False, None, "no check-runs on HEAD (main.yml has not started?)"
    names = {
        r.get("name", ""): (r.get("conclusion") or r.get("status") or "unknown")
        for r in runs
    }
    pending = [n for n, c in names.items() if c in PENDING or c == "unknown"]
    failed = [
        f"{n}: {c}"
        for n, c in names.items()
        if c not in ("success", "neutral", "skipped") and n not in pending
    ]
    absent = [w for w in MAIN_WITNESSES if w not in names]
    if failed:
        return False, None, "; ".join(failed)
    if absent:
        return False, None, f"witness absent on HEAD: {absent}"
    if pending and final:
        held = [
            n
            for n in pending
            if names[n] not in PENDING or not n.startswith(EXCUSED_AT_DEADLINE)
        ]
        if held:
            return False, None, f"still running at the deadline: {', '.join(held)}"
        done = len(runs) - len(pending)
        return (
            True,
            None,
            f"{done} check-runs on HEAD concluded success, including "
            f"{list(MAIN_WITNESSES)}; not waited for: {', '.join(pending)}",
        )
    if pending:
        return False, ", ".join(pending), f"still running: {', '.join(pending)}"
    return (
        True,
        None,
        f"{len(runs)} check-runs on HEAD concluded success, including {list(MAIN_WITNESSES)}",
    )


def _head_runs() -> list[dict]:
    _, head = _run(["git", "rev-parse", "HEAD"])
    runs = _gh_json(
        f"repos/{SLUG}/commits/{head.strip()}/check-runs",
        "--paginate",
        "--jq",
        ".check_runs",
    )
    flat: list[dict] = []
    for chunk in runs if isinstance(runs, list) else [runs]:
        flat.extend(chunk if isinstance(chunk, list) else [chunk])
    return flat


def check_ci(wait_seconds: int = 0) -> tuple[bool, str]:
    """Wait up to `wait_seconds` for the runs on HEAD (the release runs right after a merge)."""
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            flat = _head_runs()
        except Exception as e:  # could not ask -> FAIL, never pass
            return False, f"could not read CI: {e}"
        final = time.monotonic() >= deadline
        ok, pending, msg = main_witness_verdict(flat, final=final)
        if ok or pending is None or final:
            return ok, msg
        print(f"ci         waiting for {pending} ...")
        time.sleep(30)


def check_tag(version: str) -> tuple[bool, str]:
    tag = f"v{version}"
    _, local = _run(["git", "tag", "--list", tag])
    if local.strip():
        return False, f"{tag} already exists locally"
    rc, remote = _run(["git", "ls-remote", "--tags", "origin", tag])
    if rc != 0:
        return False, "ls-remote failed: " + remote.strip()[-200:]
    if remote.strip():
        return False, f"{tag} already exists on origin"
    return True, f"{tag} unused"


def check_pypi(version: str) -> tuple[bool, str]:
    url = f"https://pypi.org/pypi/jcodemunch-mcp/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:  # noqa: S310
            if r.status == 200:
                return False, f"{version} is already on PyPI (cannot be re-uploaded)"
            return False, f"unexpected PyPI status {r.status}"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True, f"{version} not on PyPI"
        return False, f"PyPI answered {e.code}"
    except Exception as e:
        return False, f"could not reach PyPI: {e}"


def check_prs() -> tuple[bool, str]:
    rc, out = _run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--json",
            "number,author,mergeable,mergeStateStatus,headRefOid",
        ],
        gh=True,
    )
    if rc != 0:
        return False, "gh pr list failed: " + out.strip()[-200:]
    prs = json.loads(out)
    heads = {
        f"#{p['number']} {p['author']['login']}": (p["number"], p.get("headRefOid", ""))
        for p in prs
    }
    ready, held = [], []
    for label in mergeable_contributor_prs(prs):
        number, head = heads[label]
        # Every page, one review per line: GitHub lists reviews oldest first, so a
        # truncated read would miss the newest decision.
        rc, body = _run(
            [
                "gh",
                "api",
                f"repos/{SLUG}/pulls/{number}/reviews?per_page=100",
                "--paginate",
                "--jq",
                ".[]",
            ],
            gh=True,
        )
        try:
            reviews = (
                [json.loads(line) for line in body.splitlines() if line.strip()]
                if rc == 0
                else None
            )
        except ValueError:
            reviews = None
        # Could not read the reviews: the PR keeps holding, as it did before.
        if reviews is not None and changes_requested_on_head(reviews, head):
            held.append(label)
        else:
            ready.append(label)
    note = (
        f" ({', '.join(held)}: changes requested on the current head)" if held else ""
    )
    if ready:
        return (
            False,
            "contributor PR(s) MERGEABLE CLEAN merge first (policy 3b): "
            + ", ".join(ready)
            + note,
        )
    return True, "no contributor PR is waiting to merge first" + note


def check_lint() -> tuple[bool, str]:
    rc, out = _run([sys.executable, "-m", "ruff", "check", "src/"])
    return rc == 0, (out.strip().splitlines() or ["clean"])[-1]


def check_harness() -> tuple[bool, str]:
    rc, out = _run([sys.executable, "-m", "harness", "fast"])
    tail = [ln for ln in out.splitlines() if ln.startswith("HARNESS") or "FAIL" in ln]
    return rc == 0, "; ".join(tail[-3:]) or f"rc={rc}"


def pins_only(a) -> int:
    """Definition of Done 1-2 on a PR (DESIGN stage 5)."""
    lines: list[str] = []
    ok = True
    pins = read_pins()
    good, msg = pins_verdict(pins, None)
    lines.append(f"pins {'agree: ' + msg if good else 'FAIL: ' + msg}")
    ok = ok and good
    rc, base_py = _run(["git", "show", f"{a.base_ref}:pyproject.toml"])
    m = re.search(r'^version = "([^"]+)"', base_py, re.M) if rc == 0 else None
    base_version = m.group(1) if m else None
    lines.append(
        f"base version {base_version}, head version {pins.get('pyproject.toml')}"
    )
    labels = {x.strip() for x in a.labels.split(",") if x.strip()}
    moved = good and base_version is not None and base_version != msg
    if moved:
        version = msg
        text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
        if not changelog_has(version, text):
            ok = False
            lines.append(
                f"FAIL: pins moved to {version} but CHANGELOG.md has no `## [{version}]` heading"
            )
        else:
            lines.append(f"CHANGELOG.md has a heading for {version}")
        try:
            wn = json.loads((REPO / "whatsnew.json").read_text(encoding="utf-8"))
            if wn.get("current") != version or not any(
                e.get("version") == version for e in wn.get("entries", [])
            ):
                ok = False
                lines.append(
                    f"FAIL: whatsnew.json does not carry {version} as current with an entry"
                )
        except Exception as e:
            ok = False
            lines.append(f"FAIL: whatsnew.json unreadable: {e}")
        rc, tags = _run(["git", "ls-remote", "--tags", "origin", f"v{version}"])
        if tags.strip():
            ok = False
            lines.append(f"FAIL: tag v{version} already exists on origin")
    elif "release" in labels:
        ok = False
        lines.append("FAIL: PR is labeled `release` but the version pins did not move")
    else:
        lines.append("pins unchanged; not a release PR")
    for ln in lines:
        print(ln)
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as fh:
            fh.write(
                f"## done: version pins: {'PASS' if ok else 'FAIL'}\n\n"
                + "\n".join(f"- {ln}" for ln in lines)
                + "\n"
            )
    if not ok:
        print(
            "::error title=version pins::see the check summary (Definition of Done 1-2)"
        )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Release pre-flight (read-only).")
    ap.add_argument(
        "--version", help="the version about to be released; pins must equal it"
    )
    ap.add_argument(
        "--no-harness", action="store_true", help="skip the fast tier (~50 s)"
    )
    ap.add_argument("--offline", action="store_true", help="skip the PyPI lookup")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="release dry run from any ref: branch, ci, tag and pypi verdicts are reported but do not block",
    )
    ap.add_argument(
        "--ci",
        action="store_true",
        help="inside the Release workflow on a checkout of main: accept a detached HEAD at origin/main",
    )
    ap.add_argument(
        "--pins-only",
        action="store_true",
        help="PR gate mode: pins agree; if they moved vs --base-ref, CHANGELOG and whatsnew carry the version; the `release` label requires a move",
    )
    ap.add_argument("--base-ref", default="origin/main")
    ap.add_argument(
        "--labels", default="", help="comma-separated PR labels (with --pins-only)"
    )
    ap.add_argument("--summary", help="append the verdict lines to this Markdown file")
    a = ap.parse_args(argv)
    if a.pins_only:
        return pins_only(a)

    ok = True

    lines: list[str] = []

    soft = {"branch", "ci", "tag", "pypi"} if a.dry_run else set()

    def report(name: str, verdict: tuple[bool, str] | None) -> None:
        nonlocal ok
        if verdict is None:
            print(f"{name:<10} SKIP")
            lines.append(f"| {name} | — | SKIP |")
            return
        good, msg = verdict
        if not good and name in soft:
            print(f"{name:<10} {msg:<90} FAIL (dry run: not blocking)")
            lines.append(f"| {name} | {msg} | FAIL, not blocking under dry run |")
            return
        ok = ok and good
        print(f"{name:<10} {msg:<90} {'PASS' if good else 'FAIL'}")
        lines.append(f"| {name} | {msg} | {'PASS' if good else '**FAIL**'} |")
        if not good:
            print(f"::error title=pre-flight {name}::{msg}")

    report("branch", check_branch(a.ci))
    report("ci", check_ci(CI_WAIT_SECONDS if a.ci else 0))
    pins = read_pins()
    pv = pins_verdict(pins, a.version)
    report("pins", pv)
    version = pv[1] if pv[0] else (a.version or pins.get("pyproject.toml") or "")
    if version:
        text = (
            (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
            if (REPO / "CHANGELOG.md").exists()
            else ""
        )
        report(
            "changelog",
            (
                changelog_has(version, text),
                f"heading for {version} {'present' if changelog_has(version, text) else 'MISSING'}",
            ),
        )
        report("tag", check_tag(version))
        report("pypi", None if a.offline else check_pypi(version))
    else:
        report("changelog", (False, "no version to check"))
        report("tag", (False, "no version to check"))
        report("pypi", (False, "no version to check"))
    report("prs", check_prs())
    report("lint", check_lint())
    report("harness", None if a.no_harness else check_harness())
    print("PREFLIGHT", "PASS" if ok else "FAIL")
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as fh:
            head = f"## release: pre-flight: {'PASS' if ok else 'FAIL'}"
            table = "| check | observed | verdict |\n|---|---|---|\n" + "\n".join(lines)
            fh.write(head + "\n\n" + table + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
