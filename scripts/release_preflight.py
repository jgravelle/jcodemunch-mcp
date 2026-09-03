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
  ci            every required status check on `main` has a check-run on HEAD
                whose conclusion is `success` (`license/cla` is a PR status and
                is not expected on a main commit)
  pins          every version pin site agrees (pyproject, server.json x2,
                .claude-plugin/plugin.json, whatsnew.json current + entries[0],
                uv.lock name-scoped line) and equals --version when given
  changelog     CHANGELOG.md has a heading for that version
  tag           `v<version>` exists neither locally nor on origin
  pypi          the version is not already on PyPI (network; --offline skips)
  prs           no open contributor PR is MERGEABLE + CLEAN (policy 3b: those
                merge BEFORE our release commit)
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
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SLUG = "jgravelle/jcodemunch-mcp"
OWNER = "jgravelle"
PR_STATUS_ONLY = {
    "license/cla"
}  # posted to PR heads by a webhook, never to a main commit

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


def ci_verdict(required: list[str], check_runs: list[dict]) -> tuple[bool, str]:
    """Every required context (minus PR-only statuses) must have a successful run on HEAD.

    A context with no run at all is a FAIL: a renamed job silently stops being
    required on GitHub's side, and this is the only place that notices.
    """
    by_name: dict[str, list[str]] = {}
    for r in check_runs:
        by_name.setdefault(r.get("name", ""), []).append(
            r.get("conclusion") or r.get("status") or "unknown"
        )
    bad = []
    for ctx in required:
        if ctx in PR_STATUS_ONLY:
            continue
        runs = by_name.get(ctx)
        if not runs:
            bad.append(f"{ctx}: no check-run on HEAD")
        elif any(c != "success" for c in runs):
            bad.append(f"{ctx}: {','.join(runs)}")
    if not [c for c in required if c not in PR_STATUS_ONLY]:
        return False, "no required checks configured on main (policy 3d)"
    if bad:
        return False, "; ".join(bad)
    n = len([c for c in required if c not in PR_STATUS_ONLY])
    return True, f"{n} required checks success on HEAD"


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


# ------------------------------------------------------------------ live runs


def check_branch() -> tuple[bool, str]:
    rc, br = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    br = br.strip()
    if br != "main":
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


def check_ci() -> tuple[bool, str]:
    try:
        prot = _gh_json(f"repos/{SLUG}/branches/main/protection")
        required = list(prot.get("required_status_checks", {}).get("contexts") or [])
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
    except Exception as e:  # could not ask -> FAIL, never pass
        return False, f"could not read CI: {e}"
    return ci_verdict(required, flat)


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
            "number,author,mergeable,mergeStateStatus",
        ],
        gh=True,
    )
    if rc != 0:
        return False, "gh pr list failed: " + out.strip()[-200:]
    ready = mergeable_contributor_prs(json.loads(out))
    if ready:
        return (
            False,
            "contributor PR(s) MERGEABLE CLEAN merge first (policy 3b): "
            + ", ".join(ready),
        )
    return True, "no contributor PR is waiting to merge first"


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

    def report(name: str, verdict: tuple[bool, str] | None) -> None:
        nonlocal ok
        if verdict is None:
            print(f"{name:<10} SKIP")
            return
        good, msg = verdict
        ok = ok and good
        print(f"{name:<10} {msg:<90} {'PASS' if good else 'FAIL'}")

    report("branch", check_branch())
    report("ci", check_ci())
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
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
