"""Render a version's GitHub release notes from CHANGELOG.md (LEDGER L-92).

`release.yml`'s github-release job runs this. It used to render inline: the
block verbatim plus a footer. GitHub refuses a release body over 125,000
characters, and the 1.108.320 block was 335,730. That job runs AFTER the PyPI
publish and the registry job needs it, so an oversized block half-publishes a
version that cannot be re-uploaded.

A block that fits is published verbatim, as before. One that does not is
published as its lead paragraph, every entry heading and a link to the full
block at the tag. If even the heading list is too long, it is cut and says how
many it left out.

Usage: python scripts/release_notes.py <version> <tool_count>  (stdout)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# GitHub's maximum for a release body, as its API refuses it: "body is too long
# (maximum is 125000 characters)". Characters are code points on both sides.
GITHUB_RELEASE_BODY_LIMIT = 125_000
# The lead paragraph kept in the fallback; a longer one is cut, so a block with
# no headings, or one long lead, cannot push the fallback over the limit.
LEAD_MAX = 20_000
REPO_URL = "https://github.com/jgravelle/jcodemunch-mcp"


def _footer(count: str, pyproject: str) -> str:
    py = re.search(r'requires-python\s*=\s*"([^"]+)"', pyproject)
    return (
        f"---\n{count} tools in the `full` surface; Python {py.group(1) if py else 'unknown'}. "
        "Published by the Release workflow with PyPI trusted publishing and sigstore-signed artifacts."
    )


def render(version: str, count: str, changelog: str, pyproject: str) -> str:
    m = re.search(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
        changelog,
        re.M | re.S,
    )
    if not m:
        sys.exit(f"no CHANGELOG block for {version}")
    block = m.group(1).strip()
    footer = _footer(count, pyproject)
    verbatim = f"{block}\n\n{footer}"
    # `main` writes one trailing newline, and `gh --notes-file` sends the file as is.
    if len(verbatim) + 1 <= GITHUB_RELEASE_BODY_LIMIT:
        return verbatim

    lead = block.split("\n### ", 1)[0].strip() if not block.startswith("### ") else ""
    link = f"{REPO_URL}/blob/v{version}/CHANGELOG.md"
    if len(lead) > LEAD_MAX:
        lead = (
            lead[:LEAD_MAX].rstrip()
            + f" ... (continued in [CHANGELOG.md at v{version}]({link}))"
        )
    heads = [line[4:].strip() for line in block.splitlines() if line.startswith("### ")]
    intro = (
        f"The full notes for this release are {len(block):,} characters, over GitHub's release body "
        f"limit, so every entry is listed by its heading here and the text is in "
        f"[CHANGELOG.md at v{version}]({link})."
    )
    parts = [p for p in (lead, intro, f"### Entries ({len(heads)})") if p]
    head_text = "\n\n".join(parts) + "\n\n"
    tail = f"\n\n{footer}"
    budget = GITHUB_RELEASE_BODY_LIMIT - len(head_text) - len(tail) - 200
    items, used = [], 0
    for h in heads:
        item = f"- {h}\n"
        if used + len(item) > budget:
            break
        items.append(item)
        used += len(item)
    while True:
        left = len(heads) - len(items)
        listing = "".join(items).rstrip("\n")
        if left:
            listing += f"\n- ... and {left} more entries, in [CHANGELOG.md at v{version}]({link})"
        notes = head_text + listing + tail
        # The guarantee, by construction: drop list items until it holds.
        if len(notes) + 1 <= GITHUB_RELEASE_BODY_LIMIT or not items:
            break
        items.pop()
    assert len(notes) + 1 <= GITHUB_RELEASE_BODY_LIMIT, len(notes)
    return notes


def main(argv: list[str]) -> int:
    version, count = argv[1], argv[2]
    root = Path(__file__).resolve().parent.parent
    notes = render(
        version,
        count,
        (root / "CHANGELOG.md").read_text(encoding="utf-8"),
        (root / "pyproject.toml").read_text(encoding="utf-8"),
    )
    sys.stdout.write(notes + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
