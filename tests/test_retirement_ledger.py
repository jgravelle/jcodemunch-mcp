"""A test may be deleted only through the ledger.

`docs/harness/ARCHAEOLOGY.md` section 1 lists every test file that existed
on 2026-09-03 with the reason it exists. From now on a listed file may
disappear only if `harness/retired.json` names it, the lesson it encoded,
and the replacement assertion (`file::test_name`) that now carries the
lesson, and that assertion must exist and collect. Otherwise this fails.

The ledger starts empty: the archaeology found nothing to retire.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from harness import thresholds as T

REPO = T.REPO_ROOT
LEDGER = REPO / "harness" / "retired.json"
ARCH = REPO / "docs" / "harness" / "ARCHAEOLOGY.md"


def _archaeology_paths() -> list[str]:
    text = ARCH.read_text(encoding="utf-8", errors="replace")
    # Section 1 only: section D lists tests deleted BEFORE the ledger existed.
    start = text.index("## 1. Test files")
    end = text.index("## 2. ", start)
    return sorted(set(re.findall(r"^\| (tests/[^ |]+\.py) \|", text[start:end], re.M)))


def _ledger() -> list[dict]:
    return json.loads(LEDGER.read_text(encoding="utf-8"))["retired"]


def test_ledger_is_well_formed():
    for r in _ledger():
        for k in ("path", "lesson", "replacement", "commit", "date"):
            assert r.get(k), f"retired entry {r} lacks {k!r}"
        assert "::" in r["replacement"], f"{r['path']}: replacement must be file::test_name"


def test_every_archaeology_test_still_exists_or_is_in_the_ledger():
    paths = _archaeology_paths()
    assert len(paths) >= 480, f"ARCHAEOLOGY.md section 1 parsed to only {len(paths)} rows"
    ledgered = {r["path"] for r in _ledger()}
    gone = [p for p in paths if not (REPO / p).exists() and p not in ledgered]
    assert not gone, (
        "test file(s) deleted without a harness/retired.json entry naming the lesson "
        f"and the replacement assertion: {gone}"
    )


def test_every_ledgered_replacement_exists_and_collects():
    bad = []
    for r in _ledger():
        f, _, name = r["replacement"].partition("::")
        p = REPO / f
        if not p.exists():
            bad.append(f"{r['path']}: replacement file {f} missing")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if not re.search(rf"^\s*(async\s+)?def {re.escape(name)}\b", text, re.M):
            bad.append(f"{r['path']}: replacement {r['replacement']} not found")
        # A ledgered `file::test_name` retires one function from a file that
        # stays; its absence is the DEF, not the file (#670's entry).
        rfile, sep, rname = r["path"].partition("::")
        if sep:
            rp = REPO / rfile
            if rp.exists() and re.search(
                rf"^\s*(async\s+)?def {re.escape(rname)}\b",
                rp.read_text(encoding="utf-8", errors="replace"),
                re.M,
            ):
                bad.append(f"{r['path']}: listed as retired but still defined")
        elif (REPO / r["path"]).exists():
            bad.append(f"{r['path']}: listed as retired but still present")
    assert not bad, "\n".join(bad)
