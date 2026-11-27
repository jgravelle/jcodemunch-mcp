"""Does this PR's diff need the PR-gate bench? (maintainer ruling, 2026-11-27)

The PR-gate bench jobs skip-pass a diff that touches no stamp code root: a
LEDGER-only change (PR 1580) cannot move a latency or token Floor, and failing
it on a runner reading is a rerun with nothing behind it. The bench on main
(main.yml) runs on every merge and is not gated by this.

The roots are the stamp's own list, `paths_for("stamp")` in
`.claude/hooks/_common.py`, so the question "can this change move a verdict"
has one answer for the stamp and the bench. A diff that cannot be read runs
the bench: unknown is not "docs only".

    python scripts/bench_gate_scope.py --base origin/main     diff base...HEAD
    python scripts/bench_gate_scope.py --paths a.md b.py      decide for a list

Writes `run=true|false` to `$GITHUB_OUTPUT` when set, and prints one line
naming the rule. Exit 0 either way; the decision is the output, not the code.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RULE = (
    "Maintainer ruling 2026-11-27: the PR bench skip-passes a diff that touches no stamp code root; "
    "the bench on main still runs after the merge"
)


def code_roots() -> tuple[str, ...]:
    """The stamp's code roots, read from the one table."""
    spec = importlib.util.spec_from_file_location(
        "hooks_common", REPO / ".claude" / "hooks" / "_common.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load .claude/hooks/_common.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.paths_for("stamp")


def _under(path: str, root: str) -> bool:
    # A root ending in "/" is a directory; any other root is one file.
    return path.startswith(root) if root.endswith("/") else path == root


def decide(paths: list[str] | None) -> tuple[bool, str]:
    """(run, reason) for a list of changed paths; None means the diff was unreadable."""
    if paths is None:
        return True, "bench gate: run (the diff could not be read, so the bench runs)"
    roots = code_roots()
    hits = [p for p in paths if any(_under(p, r) for r in roots)]
    if hits:
        shown = ", ".join(hits[:5]) + (
            f" and {len(hits) - 5} more" if len(hits) > 5 else ""
        )
        return (
            True,
            f"bench gate: run ({len(hits)} changed path(s) under a stamp code root: {shown})",
        )
    return False, (
        f"bench gate: skipped ({len(paths)} changed path(s), none under a stamp code root "
        f"{', '.join(roots)}). Rule: {RULE}."
    )


def changed_paths(base: str) -> list[str] | None:
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--base")
    g.add_argument("--paths", nargs="*")
    a = ap.parse_args(argv)
    run, reason = decide(a.paths if a.paths is not None else changed_paths(a.base))
    print(reason)
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"run={'true' if run else 'false'}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and not run:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"## {reason}\n")
    return 0


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252 (L-145, L-281).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
