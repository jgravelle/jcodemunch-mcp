"""Definition-of-Done: a change under src/ carries a CHANGELOG line (DESIGN stage 5, DoD 3).

`python scripts/dod_changelog.py --base-ref origin/main --labels "a,b" [--summary FILE]`

Rule: if any file under `src/` changed relative to the base, `CHANGELOG.md`
must have gained at least one non-heading line inside `## [Unreleased]` or
inside a `## [X.Y.Z]` block that the base did not have. The `no-changelog`
label exempts a PR, and the exemption is printed so it is visible in the
check summary. Docs-only, CI-only and test-only PRs trigger nothing.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HEADING = re.compile(r"^## \[([^\]]+)\]")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def blocks(text: str) -> dict[str, list[str]]:
    """Version heading -> non-blank body lines."""
    out: dict[str, list[str]] = {}
    cur = None
    for ln in text.splitlines():
        m = HEADING.match(ln)
        if m:
            cur = m.group(1)
            out.setdefault(cur, [])
        elif cur is not None and ln.strip():
            out[cur].append(ln.rstrip())
    return out


def verdict(
    changed: list[str], labels: set[str], base_text: str, head_text: str
) -> tuple[bool, list[str]]:
    src_changed = [f for f in changed if f.startswith("src/")]
    if not src_changed:
        return True, ["no file under src/ changed; a changelog line is not required"]
    if "no-changelog" in labels:
        return True, [
            f"{len(src_changed)} file(s) under src/ changed; exempted by the `no-changelog` label (say why in the PR)"
        ]
    base, head = blocks(base_text), blocks(head_text)
    new_versions = [v for v in head if v not in base and v.lower() != "unreleased"]
    unreleased_grew = len(head.get("Unreleased", [])) > len(base.get("Unreleased", []))
    if unreleased_grew:
        return True, [
            f"{len(src_changed)} file(s) under src/ changed; `[Unreleased]` gained {len(head['Unreleased']) - len(base.get('Unreleased', []))} line(s)"
        ]
    if new_versions:
        return True, [
            f"{len(src_changed)} file(s) under src/ changed; new CHANGELOG block(s) {new_versions}"
        ]
    return False, [
        f"FAIL: {len(src_changed)} file(s) under src/ changed and CHANGELOG.md gained nothing under `[Unreleased]` and no new version block",
        "      add an entry, or label the PR `no-changelog` and say why (Definition of Done 3)",
        "      changed: "
        + ", ".join(src_changed[:8])
        + (" ..." if len(src_changed) > 8 else ""),
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-ref", default="origin/main")
    ap.add_argument("--labels", default="")
    ap.add_argument("--summary")
    a = ap.parse_args(argv)
    changed = [
        x.strip()
        for x in _git("diff", "--name-only", f"{a.base_ref}...HEAD").splitlines()
        if x.strip()
    ]
    base_text = _git("show", f"{a.base_ref}:CHANGELOG.md")
    head_text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    labels = {x.strip() for x in a.labels.split(",") if x.strip()}
    ok, lines = verdict(changed, labels, base_text, head_text)
    for ln in lines:
        print(ln)
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as fh:
            fh.write(
                f"## done: changelog: {'PASS' if ok else 'FAIL'}\n\n"
                + "\n".join(f"- {ln.strip()}" for ln in lines)
                + "\n"
            )
    if not ok:
        print(
            "::error title=changelog::src/ changed without a CHANGELOG entry (Definition of Done 3)"
        )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
