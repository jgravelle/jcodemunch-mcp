"""Install a full-tier stamp made on ANOTHER machine, for THIS tree only.

purpose:  a full tier can run on a second machine (the Mac) while this one is
          busy; pre_pr reads only this checkout's `.claude/state/full-tier.json`
invokes:  _common.tree_id, _common.timing_only_failures
produces: .claude/state/full-tier.json (the remote stamp plus `imported_from`)
          and .claude/state/evidence/full.md (the remote summary, verbatim);
          removes a stale evidence/full-failures.txt, as run_full.py does
refuses:  a stamp that is not green; a stamp whose tree is not this checkout's
          tree, or could not be read on either side; a full.md that is absent,
          is not a full-tier summary, says FAIL with no failing row, or fails
          on any row that is not a timing row (CI judges those, jjg
          2026-10-16); a full.md that does not belong to the stamp (its
          deferred timing rows or its xdist worker count differ). A refusal
          writes nothing, so the local stamp stays as it was.
budget:   5 s

Usage: python .claude/hooks/import_full_stamp.py <full-tier.json> <full.md>

`tree_id` hashes git blob content, not the bytes on disk, so one commit has one
id whatever a checkout's line-ending setting (tests/test_import_full_stamp.py
pins this over core.autocrlf true and false). The check is the one pre_pr
makes; doing it here refuses a stamp for another tree when it arrives, with its
source named, instead of later as an unexplained "DIFFERENT tree".

A stamp certifies the platform it ran on. A darwin stamp says nothing about
win32-only tests; the PR gate's matrix stays the authority for those.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from _common import EVIDENCE, STATE, UNREADABLE_PREFIX, timing_only_failures, tree_id

_WORKERS_RE = re.compile(r"^xdist workers: (\S+)\s*$", re.MULTILINE)


def _refuse(reason: str) -> int:
    print(f"import_full_stamp: refused, {reason}. Nothing was written.", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return _refuse("usage is import_full_stamp.py <full-tier.json> <full.md>")
    stamp_path, md_path = Path(argv[0]), Path(argv[1])
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _refuse(f"the stamp could not be read ({exc})")
    if not isinstance(stamp, dict) or not isinstance(stamp.get("tree"), str):
        return _refuse("the stamp has no tree")
    if stamp["tree"].startswith(UNREADABLE_PREFIX):
        return _refuse("the stamp's tree could not be read on the machine that ran it")
    if stamp.get("ok") is not True:
        return _refuse(f"the stamp is not green (ok={stamp.get('ok')!r}, note={stamp.get('note')!r})")
    here = tree_id()
    if here.startswith(UNREADABLE_PREFIX):
        return _refuse("this checkout's tree could not be read")
    if stamp["tree"] != here:
        return _refuse(
            f"the stamp is for a different tree (stamp {stamp['tree'][:12]}, "
            f"commit {stamp.get('commit')}, this checkout {here[:12]})"
        )
    if not md_path.is_file():
        return _refuse(f"no full.md at {md_path}")
    summary = md_path.read_text(encoding="utf-8")
    if "## harness full:" not in summary:
        return _refuse("full.md is not a full-tier summary (no `## harness full:` heading)")
    failing = "## harness full: FAIL" in summary or "**FAIL**" in summary
    deferred = timing_only_failures(summary) if failing else []
    if failing and not deferred:
        # timing_only_failures returns [] for a real failure AND for a FAIL
        # with no readable row; both are UNKNOWN-or-worse and refuse.
        return _refuse("full.md carries a non-timing failure, or a FAIL with no failing row")
    # The summary must be the one this stamp's run wrote: run_full records the
    # timing rows it deferred and appends the worker cap to full.md.
    if sorted(stamp.get("timing_deferred_to_ci") or []) != sorted(deferred):
        return _refuse(
            "full.md does not belong to the stamp (deferred timing rows "
            f"{stamp.get('timing_deferred_to_ci') or []} vs {deferred})"
        )
    m = _WORKERS_RE.search(summary)
    if m is None or m.group(1) != str(stamp.get("workers")):
        return _refuse(
            "full.md does not belong to the stamp (xdist workers "
            f"{m.group(1) if m else 'absent'} vs {stamp.get('workers')!r})"
        )
    stamp["imported_from"] = {
        "platform": stamp.get("platform") or "unknown",
        "source": str(stamp_path.resolve()),
        "imported": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    # Both files are written beside their targets first and then moved in, the
    # summary before the stamp, so a failed write leaves no stamp naming it.
    md_tmp = EVIDENCE / "full.md.importing"
    st_tmp = STATE / "full-tier.json.importing"
    md_tmp.write_text(summary, encoding="utf-8")
    st_tmp.write_text(json.dumps(stamp, indent=1), encoding="utf-8")
    (EVIDENCE / "full-failures.txt").unlink(missing_ok=True)
    os.replace(md_tmp, EVIDENCE / "full.md")
    os.replace(st_tmp, STATE / "full-tier.json")
    print(
        f"import_full_stamp: installed a {stamp['imported_from']['platform']} full-tier stamp "
        f"for tree {here[:12]} (commit {stamp.get('commit')})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
