"""A per-machine bound on how many ``index-file`` runs work at once.

The PostToolUse hook starts one detached ``jcodemunch-mcp index-file <path>``
for every Edit or Write, and nothing waited for or counted them. Each run loads
the repository's index: about 250 MB and 2.5 s on a 50,000-symbol index, so
eight edits in a row held eight of them and 800 MB at once. An agent session
that edits 47 times against an index that takes minutes to build starts runs
faster than they finish, memory pressure slows each one, and the pile grows.
A host that ends the session by killing only the agent leaves the pile running.

Two rules, both checked before an index is loaded:

1. **One run per index.** A run for an index that already has a live run does
   no work itself. It leaves its path in that index's pending list, and the
   live run indexes the pending paths in the same process before it finishes,
   with the index already loaded.
2. **At most ``MAX_CONCURRENT`` runs on the machine.** A run that finds every
   slot taken does no work and exits.

A skipped edit is not lost. The pending list covers the first case, and
``refresh_on_read`` (on by default) re-indexes a changed file before the next
read answers, as does a running watcher.

Every lock is an ordinary ``process_locks`` lock, so one held by a process that
died or whose PID was reused reads as free and a crash never leaks a slot. The
locks live in one directory per user (``slot_dir``), never under the index
store: a host may give every session its own ``CODE_INDEX_PATH``, and a bound
kept inside each store would bound nothing across them.

The bound lives in the command, so every caller of ``index-file`` inherits it.
The hook does not read the slots first: importing the lock layer would cost
every hook about 0.66 s, and a run that finds no slot exits before it loads
an index.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import process_locks

logger = logging.getLogger(__name__)

SCOPE = "indexfile"
INDEX_SCOPE = "indexfile-index"
#: Seeded, not calibrated: two working runs held about 400 MB on a 50,000-symbol
#: index (evidence/measurements.md); no comparison of 1, 2 and 3 was made.
MAX_CONCURRENT = 2
#: A pending list longer than this is cut; refresh_on_read covers the rest.
MAX_PENDING = 500


def slot_dir() -> Path:
    """The one directory, per user and machine, that holds every slot."""
    return Path(tempfile.gettempdir()) / "jcodemunch-index-file"


def _targets() -> list[str]:
    return [f"slot{i}" for i in range(MAX_CONCURRENT)]


def index_key(path: str, storage_path: Optional[str]) -> str:
    """Which index a file's run would load: its index store plus its checkout."""
    store = os.path.abspath(storage_path) if storage_path else "<default>"
    try:
        from .git_root import _find_git_root

        root = _find_git_root(Path(path).parent) or Path(path).parent
    except Exception:
        logger.debug("could not find the checkout of %s", path, exc_info=True)
        root = Path(path).parent
    return f"{store}|{os.path.abspath(str(root))}"


def _pending_file(key: str) -> Path:
    digest = hashlib.sha256(key.lower().encode("utf-8")).hexdigest()[:12]
    return slot_dir() / f"_pending_{digest}.txt"


@dataclass
class Claim:
    index_target: Optional[str]
    slot: Optional[str]
    #: Set when a lock could not be read or written and the run went ahead
    #: without it. UNKNOWN runs the work: reporting it as busy would turn off
    #: every re-index with a reason that is not true.
    lock_error: Optional[str] = None


def claim(key: str) -> tuple[Optional[Claim], Optional[str]]:
    """Take this index's lock and a machine slot.

    Returns ``(claim, None)`` on success, or ``(None, reason)`` where reason is
    ``"same_index_busy"`` (the path was queued for the live run) or ``"busy"``.
    """
    d = str(slot_dir())
    try:
        got_index = process_locks.acquire(INDEX_SCOPE, key, d)
    except Exception as e:
        logger.warning("index-file runs unbounded: the index lock in %s failed (%s)", d, e, exc_info=True)
        return Claim(index_target=None, slot=None, lock_error=f"index lock: {e}"), None
    if not got_index:
        return None, "same_index_busy"
    for target in _targets():
        try:
            if process_locks.acquire(SCOPE, target, d):
                return Claim(index_target=key, slot=target), None
        except Exception as e:
            logger.warning("index-file runs unbounded: slot %s in %s failed (%s)", target, d, e, exc_info=True)
            return Claim(index_target=key, slot=None, lock_error=f"{target}: {e}"), None
    process_locks.release(INDEX_SCOPE, key, d)
    return None, "busy"


def release(c: Claim) -> None:
    """Give back both locks taken by ``claim``."""
    d = str(slot_dir())
    if c.slot is not None:
        process_locks.release(SCOPE, c.slot, d)
    if c.index_target is not None:
        process_locks.release(INDEX_SCOPE, c.index_target, d)


def queue(key: str, path: str) -> bool:
    """Leave ``path`` for the live run of this index. False when not recorded."""
    p = _pending_file(key)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > MAX_PENDING * 1024:
            return False
        with open(p, "a", encoding="utf-8") as f:
            f.write(path + "\n")
        return True
    except OSError:
        logger.debug("could not queue %s", path, exc_info=True)
        return False


def take_pending(key: str) -> list[str]:
    """Read and clear this index's pending list; each path once, in order."""
    p = _pending_file(key)
    taken = p.with_suffix(f".{os.getpid()}.taking")
    try:
        os.replace(p, taken)
    except FileNotFoundError:
        return []
    except OSError:
        logger.debug("could not take the pending list %s", p, exc_info=True)
        return []
    try:
        lines = taken.read_text(encoding="utf-8").splitlines()
    except OSError:
        logger.debug("could not read %s", taken, exc_info=True)
        lines = []
    finally:
        try:
            taken.unlink()
        except OSError:
            pass
    seen: dict[str, None] = {}
    for line in lines:
        if line.strip():
            seen.setdefault(line.strip(), None)
    return list(seen)[:MAX_PENDING]
