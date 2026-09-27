"""The full tier with the D5 stamp (DESIGN section 4; FINDINGS W-3).

purpose:  run `python -m harness full --summary` and record that it passed on
          exactly this tree, so pre_pr.py can refuse a PR from any other tree
invokes:  `uv run python -m harness full --summary .claude/state/evidence/full.md`
produces: .claude/state/evidence/full.md, .claude/state/full-tier.json
          {tree, ok, date, commit, workers, seconds}, and on a red pytest run
          .claude/state/evidence/full-failures.txt (tracebacks, harness F-26)
refuses:  nothing; exit code is the harness's

Usage: python .claude/hooks/run_full.py [--workers N] [extra harness args]
The stamp is written BEFORE the run with ok=false and rewritten after, so an
interrupted run never leaves a stale pass behind.

`--workers N` caps xdist's `-n auto` through PYTEST_XDIST_AUTO_NUM_WORKERS,
for a box where other work leaves too little memory for one worker per core
(two full runs were killed that way, F-26). The stamp records the cap, so a
capped run's wall clock is never read as an uncapped one.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

from _common import EVIDENCE, REPO, STATE, UNREADABLE_PREFIX, git, tree_id

STAMP = STATE / "full-tier.json"
WORKERS_ENV = "PYTEST_XDIST_AUTO_NUM_WORKERS"


def _split_workers(argv: list[str]) -> tuple[str | None, list[str]]:
    """`--workers N` (or `--workers=N`) out of argv; the rest goes to the harness."""
    rest: list[str] = []
    workers = None
    given = False
    it = iter(argv)
    for arg in it:
        if arg == "--workers":
            given, workers = True, next(it, None)
        elif arg.startswith("--workers="):
            given, workers = True, arg.split("=", 1)[1]
        else:
            rest.append(arg)
    # ⚠ A bare `--workers` must refuse, not run uncapped: a flag that is
    # present and does nothing reads as the cap it failed to set (review).
    # ASCII digits only: `"²".isdigit()` is True and `int("²")` raises.
    if given and not (workers and re.fullmatch(r"[1-9][0-9]*", workers)):
        raise SystemExit(f"run_full: --workers takes a positive integer, got {workers!r}")
    return workers, rest


def main(argv: list[str]) -> int:
    workers, argv = _split_workers(argv)
    env = dict(os.environ)
    if workers is not None:
        env[WORKERS_ENV] = workers
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    # `--summary` APPENDS; a stale FAIL row from an earlier run would read as
    # this run failing (FINDINGS W-20). One run, one summary.
    (EVIDENCE / "full.md").unlink(missing_ok=True)
    (EVIDENCE / "full-failures.txt").unlink(missing_ok=True)
    tree = tree_id()
    commit = git("rev-parse", "--short", "HEAD").strip()
    stamp = {
        "tree": tree,
        "ok": False,
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "commit": commit,
        # A cap set in the caller's environment counts too; "auto" is none.
        "workers": env.get(WORKERS_ENV) or "auto",
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
            # F-26: the id alone did not explain a Windows-only failure twice.
            # The harness writes the tracebacks here on red, removes it on green.
            "--failures",
            str(EVIDENCE / "full-failures.txt"),
            *argv,
        ],
        cwd=REPO,
        env=env,
    )
    # The cap reaches `full.md` too, the file a reviewer reads: a capped run
    # near the `suite.full_seconds` Floor is a different measurement (review).
    with open(EVIDENCE / "full.md", "a", encoding="utf-8") as fh:
        fh.write(f"\nxdist workers: {stamp['workers']}\n")
    after = tree_id()
    stamp.update(
        ok=(rc == 0 and after == tree), seconds=round(time.monotonic() - t0, 1)
    )
    if tree.startswith(UNREADABLE_PREFIX) or after.startswith(UNREADABLE_PREFIX):
        stamp["note"] = "tree identity could not be read (see stderr); stamp invalid"
    elif after != tree:
        stamp["note"] = "tree changed during the run; stamp invalid"
    STAMP.write_text(json.dumps(stamp, indent=1), encoding="utf-8")
    print(f"full-tier stamp: ok={stamp['ok']} tree={tree[:12]} workers={stamp['workers']} -> {STAMP}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
