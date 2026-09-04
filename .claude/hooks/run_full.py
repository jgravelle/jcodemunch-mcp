"""The full tier with the D5 stamp (DESIGN section 4; FINDINGS W-3).

purpose:  run `python -m harness full --summary` and record that it passed on
          exactly this tree, so pre_pr.py can refuse a PR from any other tree
invokes:  `uv run python -m harness full --summary .claude/state/evidence/full.md`
produces: .claude/state/evidence/full.md, .claude/state/full-tier.json
          {tree, ok, date, commit, seconds}
refuses:  nothing; exit code is the harness's

Usage: python .claude/hooks/run_full.py [extra harness args]
The stamp is written BEFORE the run with ok=false and rewritten after, so an
interrupted run never leaves a stale pass behind.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

from _common import EVIDENCE, REPO, STATE, git, tree_id

STAMP = STATE / "full-tier.json"


def main(argv: list[str]) -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    # `--summary` APPENDS; a stale FAIL row from an earlier run would read as
    # this run failing (FINDINGS W-20). One run, one summary.
    (EVIDENCE / "full.md").unlink(missing_ok=True)
    tree = tree_id()
    commit = git("rev-parse", "--short", "HEAD").strip()
    stamp = {
        "tree": tree,
        "ok": False,
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "commit": commit,
    }
    STAMP.write_text(json.dumps(stamp, indent=1), encoding="utf-8")
    t0 = time.monotonic()
    rc = subprocess.call(
        [
            "uv",
            "run",
            "python",
            "-m",
            "harness",
            "full",
            "--summary",
            str(EVIDENCE / "full.md"),
            *argv,
        ],
        cwd=REPO,
    )
    after = tree_id()
    stamp.update(
        ok=(rc == 0 and after == tree), seconds=round(time.monotonic() - t0, 1)
    )
    if after != tree:
        stamp["note"] = "tree changed during the run; stamp invalid"
    STAMP.write_text(json.dumps(stamp, indent=1), encoding="utf-8")
    print(f"full-tier stamp: ok={stamp['ok']} tree={tree[:12]} -> {STAMP}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
