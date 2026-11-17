"""Pending files: counts a per-call store could not add to its file, kept on disk.

A per-call store (`tool_counters`, `direct_reads`) adds counts to one JSON file
under a lock. When the lock is not taken in time, or the read or the replace
fails, a count held only in memory is lost the moment the process exits. A
pending file keeps it instead: `<file>.<uuid>.pending` beside the main file,
folded in and removed by a later write (`fold_due`).

⚠⚠ **One protocol, shared.** Both stores had the exit defect and the first fix
(1.108.454, Cursor reads) wrote this protocol inside its own module; the
second (tool counters) copied it. The rules live here so a store written next
inherits them:

- A pending file is written to a `.ptmp` temp and renamed, so a reader never
  sees half a file. Its name carries a uuid, never pid + clock: two writes in
  one process inside the clock's resolution would share a name and one rename
  would drop the other.
- A writer CLAIMS a pending file (removes it) BEFORE adding its counts. One it
  cannot remove stays and is counted by a later write, once.
- A reader reads the main file FIRST and the pending files after. A fold
  removes a pending file before it replaces the main file, so in this order a
  fold between the two reads can only under-count.
- A `.ptmp` older than a minute was left by a process killed mid-write and is
  removed by the next fold.
- A writer folds on its process's first write to a file, on its exit write,
  and at most once a minute in between (`fold_due`). ⚠ A fold lists the
  directory twice, and a per-call store writes every few calls inside a tool
  call: folding on every write put about half a millisecond on the slowest
  calls of a warm read. A stopped server's counts reach the main file up to a
  minute later; a reader counts pending files at load, so a total it shows
  is not delayed.

What a payload means, and whether it is valid, stays with each store.

A leaf: stdlib only.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SUFFIX = ".pending"
_TEMP_SUFFIX = ".ptmp"
_STALE_TEMP_SECONDS = 60.0
FOLD_INTERVAL_SECONDS = 60.0
_last_fold: dict = {}
_fold_lock = threading.Lock()


def fold_due(main: Path, *, force: bool = False) -> bool:
    """True when a write to *main* should fold its pending files now.

    True on this process's first write to *main*, when *force* is set (the exit
    write), and once FOLD_INTERVAL_SECONDS have passed since the last fold.
    A True answer is recorded as a fold.
    """
    key = str(main)
    now = time.monotonic()
    with _fold_lock:
        last = _last_fold.get(key)
        if force or last is None or now - last >= FOLD_INTERVAL_SECONDS:
            _last_fold[key] = now
            return True
        return False


def paths(main: Path) -> list:
    """Every pending file beside *main*, oldest name first."""
    return sorted(main.parent.glob(f"{main.name}.*{SUFFIX}"))


def write(main: Path, payload: dict) -> bool:
    """Keep *payload* beside *main* for the next write. False when it could not be written."""
    name = f"{main.name}.{uuid.uuid4().hex}"
    tmp = main.with_name(name + _TEMP_SUFFIX)
    try:
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, main.with_name(name + SUFFIX))
        return True
    except OSError:
        logger.debug("pending file for %s not written", main.name, exc_info=True)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def read(p: Path) -> Optional[dict]:
    """The pending file's JSON object, or None when it is unreadable or not an object."""
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def claim(p: Path) -> bool:
    """Remove *p* before its counts are added. False: leave it for a later write."""
    try:
        p.unlink()
        return True
    except OSError:
        logger.debug(
            "pending file %s not removed; left for a later write", p.name, exc_info=True
        )
        return False


def sweep_stale_temps(main: Path) -> None:
    """Remove `.ptmp` files beside *main* that a killed process left over a minute ago."""
    cutoff = time.time() - _STALE_TEMP_SECONDS
    for t in main.parent.glob(f"{main.name}.*{_TEMP_SUFFIX}"):
        try:
            if t.stat().st_mtime < cutoff:
                t.unlink()
        except OSError:
            logger.debug("stale pending temp %s not removed", t.name, exc_info=True)
