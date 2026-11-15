"""A per-machine bound on how many ``index-file`` runs work at once.

The PostToolUse hook starts one detached ``jcodemunch-mcp index-file <path>``
for every Edit or Write, and nothing waited for or counted them. Each run loads
the repository's index: about 250 MB and 2.5 s on a 50,000-symbol index, so
eight edits in a row held eight of them and 800 MB at once. When edits arrive
faster than runs finish, the runs pile up, memory pressure slows each one, and
the pile grows. A machine running many agent sessions has no upper limit.

``claim`` takes one of ``MAX_CONCURRENT`` slots, each an ordinary
``process_locks`` lock, so a slot held by a dead or recycled process reads as
free and a crash never leaks a slot. A run that finds every slot taken does no
work and exits. Its edit is not lost: ``refresh_on_read`` (on by default)
re-indexes a changed file before the next read answers, and the watcher does
the same where one runs.

The bound lives in the command, so every caller of ``index-file`` inherits it.
The hook does not read the slots first: importing the lock layer would cost
every hook about 0.66 s, and a run that finds no slot exits before it loads
an index.
"""

from __future__ import annotations

import logging
from typing import Optional

from . import process_locks

logger = logging.getLogger(__name__)

SCOPE = "indexfile"
MAX_CONCURRENT = 2


def _targets() -> list[str]:
    return [f"slot{i}" for i in range(MAX_CONCURRENT)]


def claim(storage_path: Optional[str] = None) -> Optional[str]:
    """Take a free slot and return its name, or None when every slot is held."""
    for target in _targets():
        try:
            if process_locks.acquire(SCOPE, target, storage_path):
                return target
        except Exception:
            logger.debug("could not claim index-file %s", target, exc_info=True)
    return None


def release(target: str, storage_path: Optional[str] = None) -> None:
    """Give back a slot taken by ``claim``."""
    process_locks.release(SCOPE, target, storage_path)

