"""Merge a run's drafts into the ledger's draft directory (docs/competitive/
DESIGN.md s7.2; `competitive-run.yml` and `competitive-feed.yml` call it).

purpose:  put new drafts on the ledger and append a run's dated block to a
          draft already there, so a human's head (`approved: true`, a
          `posted:` line) is never overwritten and the newest values sit
          under a dated heading, as s7.2 says
invokes:  nothing but the two directories
produces: copied or appended files under the ledger's `competitive/drafts`;
          a summary of what was added and what was appended
refuses:  to touch a draft under `posted/`; to append a block whose
          heading is not a `## <date>` line (a draft without one is copied
          only when new); to write outside the ledger directory given
pinned:   the draft shape findings.py and feed.py write: head lines, a
          blank line, then `## <date>` blocks
fairness: none of the text is competitor text except what the drafts
          already quote as data
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^## \d{4}-\d{2}-\d{2}", re.M)


def split(text: str) -> tuple[str, str]:
    """(head, blocks): the head is everything before the first dated heading."""
    text = text.replace("\r\n", "\n")
    m = HEADING.search(text)
    if not m:
        return text, ""
    return text[: m.start()], text[m.start():]


def merge(src: Path, ledger_drafts: Path) -> dict:
    ledger_drafts.mkdir(parents=True, exist_ok=True)
    posted = ledger_drafts / "posted"
    added, appended, skipped = [], [], []
    for f in sorted(src.glob("*.md")):
        target = ledger_drafts / f.name
        if (posted / f.name).exists():
            skipped.append(f.name)
            continue
        head, blocks = split(f.read_text(encoding="utf-8"))
        if not target.exists():
            target.write_text(head + blocks if blocks else head, encoding="utf-8")
            added.append(f.name)
            continue
        if not blocks:
            skipped.append(f.name)
            continue
        existing = target.read_text(encoding="utf-8").replace("\r\n", "\n")
        if not existing.endswith("\n"):
            existing += "\n"
        target.write_text(existing + "\n" + blocks.lstrip("\n"), encoding="utf-8")
        appended.append(f.name)
    return {"added": added, "appended": appended, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", required=True, help="this run's drafts directory")
    ap.add_argument("--ledger-drafts", required=True, help="the ledger checkout's competitive/drafts directory")
    a = ap.parse_args(argv)
    out = merge(Path(a.src), Path(a.ledger_drafts))
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
