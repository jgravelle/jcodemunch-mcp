"""Tool-surface diff between a base ref and the working tree (DESIGN stage 5, Practice 1).

`python scripts/surface_diff.py --base-ref origin/main [--changed-files FILE] [--summary FILE]`

Lists the `full` surface (every visible tool name) on both sides by calling
`_build_tools_list` from each tree -- never a hand-typed count. A non-empty
diff (added, removed or renamed tools) requires `README.md`, `CLAUDE.md` and
`CHANGELOG.md` among the changed files, and the CHANGELOG diff must name each
added or removed tool. Exit 1 otherwise. Nothing here restates a number.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REQUIRED_DOCS = ("README.md", "CLAUDE.md", "CHANGELOG.md")
SNIPPET = (
    "import json,sys;"
    "from jcodemunch_mcp import server;"
    "print(json.dumps(sorted(t.name for t in server._build_tools_list(profile_override='full', surface_override='full'))))"
)


def _names(src_root: Path) -> list[str]:
    env = dict(
        os.environ, PYTHONPATH=str(src_root / "src"), JCODEMUNCH_TOOL_SURFACE="full"
    )
    p = subprocess.run(
        [sys.executable, "-c", SNIPPET],
        cwd=src_root,
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if p.returncode != 0:
        raise SystemExit(
            f"could not list the tool surface in {src_root}:\n{p.stderr[-1500:]}"
        )
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("[")][-1]
    return json.loads(line)


def base_names(base_ref: str) -> list[str]:
    wt = Path(tempfile.mkdtemp(prefix="surface-base-"))
    subprocess.run(
        ["git", "worktree", "add", "--detach", "-q", str(wt), base_ref],
        cwd=REPO,
        check=True,
    )
    try:
        return _names(wt)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=REPO)


def changed_files(base_ref: str) -> list[str]:
    out = subprocess.check_output(
        ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
        cwd=REPO,
        text=True,
        encoding="utf-8",
    )
    return [x.strip() for x in out.splitlines() if x.strip()]


def changelog_diff(base_ref: str) -> str:
    return subprocess.run(
        ["git", "diff", f"{base_ref}...HEAD", "--", "CHANGELOG.md"],
        cwd=REPO,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def verdict(
    base: list[str], head: list[str], changed: list[str], cl_diff: str
) -> tuple[bool, list[str]]:
    added = sorted(set(head) - set(base))
    removed = sorted(set(base) - set(head))
    lines = [
        f"surface: base {len(base)} tools, head {len(head)} tools; added {added or 'none'}, removed {removed or 'none'}"
    ]
    if not added and not removed:
        return True, lines + ["no surface change; documentation rule not triggered"]
    ok = True
    for doc in REQUIRED_DOCS:
        if doc not in changed:
            ok = False
            lines.append(
                f"FAIL: the tool surface changed and {doc} is not in the diff (Practice 1)"
            )
    added_text = "\n".join(
        ln[1:]
        for ln in cl_diff.splitlines()
        if ln.startswith("+") and not ln.startswith("+++")
    )
    for name in added + removed:
        if name not in added_text:
            ok = False
            lines.append(f"FAIL: CHANGELOG.md's added lines do not name `{name}`")
    if ok:
        lines.append(
            "PASS: surface change is documented in README.md, CLAUDE.md and CHANGELOG.md"
        )
    return ok, lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-ref", default="origin/main")
    ap.add_argument("--summary")
    a = ap.parse_args(argv)
    base = base_names(a.base_ref)
    head = _names(REPO)
    ok, lines = verdict(
        base, head, changed_files(a.base_ref), changelog_diff(a.base_ref)
    )
    for ln in lines:
        print(ln)
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as fh:
            fh.write(
                f"## done: tool surface documented: {'PASS' if ok else 'FAIL'}\n\n"
                + "\n".join(f"- {ln}" for ln in lines)
                + "\n"
            )
    if not ok:
        print(
            "::error title=tool surface::the tool surface changed without the documentation Practice 1 requires"
        )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
