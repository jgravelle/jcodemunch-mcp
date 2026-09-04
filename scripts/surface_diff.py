"""Tool-surface diff between a base ref and the working tree (DESIGN stage 5, Practice 1).

`python scripts/surface_diff.py --base-ref origin/main [--descriptions] [--summary FILE]`

Lists the `full` surface (every visible tool name) on both sides by calling
`_build_tools_list` from each tree -- never a hand-typed count. A non-empty
diff (added, removed or renamed tools) requires `README.md`, `CLAUDE.md` and
`CHANGELOG.md` among the changed files, and the CHANGELOG diff must name each
added or removed tool. Exit 1 otherwise. Nothing here restates a number.

`--descriptions` also diffs each tool's DESCRIPTION between the two trees
(docs/workflows/FINDINGS.md W-1): one `description changed: <name>` line per
tool whose description differs, and under `--summary` a
`## done: tool descriptions` block. A description change is a surface change
(STANDARD criterion 4) and is always LISTED; it never moves the exit code,
which belongs to the name rule alone.
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
# One subprocess per tree returns BOTH halves: the sorted names and the
# name -> description map. Two snippets would mean two worktrees of the base
# ref for one question.
SNIPPET = (
    "import json,sys;"
    "from jcodemunch_mcp import server;"
    "ts=server._build_tools_list(profile_override='full', surface_override='full');"
    "print(json.dumps({'names': sorted(t.name for t in ts), "
    "'descriptions': {t.name: t.description for t in ts}}))"
)


def _surface(src_root: Path) -> dict:
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
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("{")][-1]
    return json.loads(line)


def base_surface(base_ref: str) -> dict:
    wt = Path(tempfile.mkdtemp(prefix="surface-base-"))
    subprocess.run(
        ["git", "worktree", "add", "--detach", "-q", str(wt), base_ref],
        cwd=REPO,
        check=True,
    )
    try:
        return _surface(wt)
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


def description_diff(base_desc: dict[str, str], head_desc: dict[str, str]) -> list[str]:
    """One `description changed: <name>` line per tool on BOTH sides whose text differs.

    A tool on one side only is a name change and belongs to `verdict()`; a
    single space moved counts, because the published surface is byte-pinned.
    """
    return [
        f"description changed: {name}"
        for name in sorted(set(base_desc) & set(head_desc))
        if base_desc[name] != head_desc[name]
    ]


def report(
    *,
    base: list[str],
    head: list[str],
    changed: list[str],
    cl_diff: str,
    base_desc: dict[str, str],
    head_desc: dict[str, str],
    descriptions: bool,
) -> tuple[int, list[str], str]:
    """(exit code, stdout lines, summary text). The exit code is the name verdict's alone."""
    ok, lines = verdict(base, head, changed, cl_diff)
    summary = (
        f"## done: tool surface documented: {'PASS' if ok else 'FAIL'}\n\n"
        + "\n".join(f"- {ln}" for ln in lines)
        + "\n"
    )
    if descriptions:
        desc_lines = description_diff(base_desc, head_desc)
        lines = lines + desc_lines
        count = len(desc_lines) if desc_lines else "none"
        body = (
            "\n".join(f"- {ln}" for ln in desc_lines)
            if desc_lines
            else "- no description changes"
        )
        summary += f"\n## done: tool descriptions: {count} changed\n\n{body}\n"
    return (0 if ok else 1), lines, summary


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-ref", default="origin/main")
    ap.add_argument("--summary")
    ap.add_argument(
        "--descriptions",
        action="store_true",
        help="also diff each tool's description (listed, never a failure; FINDINGS W-1)",
    )
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    base = base_surface(a.base_ref)
    head = _surface(REPO)
    rc, lines, summary = report(
        base=base["names"],
        head=head["names"],
        changed=changed_files(a.base_ref),
        cl_diff=changelog_diff(a.base_ref),
        base_desc=base["descriptions"],
        head_desc=head["descriptions"],
        descriptions=a.descriptions,
    )
    for ln in lines:
        print(ln)
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as fh:
            fh.write(summary)
    if rc:
        print(
            "::error title=tool surface::the tool surface changed without the documentation Practice 1 requires"
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
