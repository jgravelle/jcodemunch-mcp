#!/usr/bin/env python3
"""Regenerate the expected object counts in the vendored SQL corpus manifest.

Reads only `tests/fixtures/sql_corpora/`, which is vendored into this repo, and
rewrites the `expected` block of `tests/fixtures/public_sql_corpora.json`.

⚠ **Touches no network.** The corpora used to be fetched on demand; they are
vendored now, because a test that reaches out fails for reasons unrelated to
the change under review and cannot run in an air-gapped checkout at all.
Provenance -- upstream repo, ref, path and the sha256 of the artifact each file
came from -- stays in the manifest, and the licenses are in
`tests/fixtures/sql_corpora/SOURCES.md`.

    python benchmarks/remeasure_sql_corpora.py           # show what moved
    python benchmarks/remeasure_sql_corpora.py --write   # and rewrite the pins

⚠ Rewriting is right ONLY when a parser change is understood and intended.
Running this to turn a red test green erases the signal the pins exist to
carry: every one of them was measured, and several were measured DOWN because a
false positive was removed. Read the diff and check the direction of each move
before committing it.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "tests" / "fixtures" / "public_sql_corpora.json"
CORPORA = REPO_ROOT / "tests" / "fixtures" / "sql_corpora"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--write", action="store_true", help="rewrite the manifest (read the diff!)"
    )
    args = ap.parse_args()

    sys.path.insert(0, str(REPO_ROOT / "src"))
    os.environ.setdefault("CODE_INDEX_PATH", tempfile.mkdtemp(prefix="jcm-corpus"))
    from jcodemunch_mcp.parser.extractor import _parse_sql_symbols

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    moved = 0
    for source in manifest["sources"]:
        for entry in source["files"]:
            path = CORPORA / entry["vendored_as"]
            if not path.is_file():
                print(f"  MISSING {entry['vendored_as']}")
                continue
            counts = collections.Counter(
                s.kind for s in _parse_sql_symbols(path.read_bytes(), entry["path"])
            )
            new = dict(sorted(counts.items()))
            if new != entry.get("expected"):
                moved += 1
                print(f"  {entry['vendored_as']}: {entry.get('expected')} -> {new}")
            entry["expected"] = new

    if args.write:
        MANIFEST.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"\n{moved} expectation(s) moved; manifest rewritten. Read the diff.")
    else:
        print(f"\n{moved} expectation(s) would move. Re-run with --write to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
