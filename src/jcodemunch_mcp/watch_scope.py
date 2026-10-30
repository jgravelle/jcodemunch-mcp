"""Which indexed repos `watch-all` leaves unwatched, and why.

`watch-all` watched every local index forever, including throwaway ones: an
index of a folder under the system temp directory, or of a checkout nobody has
touched in months, kept its watcher, its startup re-index at every sign-in and
its share of idle CPU. Two settings narrow that, both on by default:

* ``watch_all_skip_temp`` (True): skip a repo under the system temp directory
  or a top-level ``tmp``/``temp`` folder (``C:\\tmp``, ``/tmp``, ``/var/tmp``).
* ``watch_all_max_idle_days`` (14; 0 watches everything): skip a repo whose
  newest indexed file AND last indexed commit are both older than that.

⚠⚠ The index's ``indexed_at`` cannot be the idle signal: the watcher's own
startup pass re-indexes every watched repo at sign-in and rewrites it, so every
watched index reads as indexed today. The two signals used are the newest file
mtime the index stored and the date of the commit it stored, which change only
when the code does.

⚠ UNKNOWN never skips. A signal that cannot be read (no files row, an
unreadable database, a commit git cannot date) keeps the repo watched; a wrong
skip leaves an index silently stale, which is the worse error.

Nothing here deletes or edits an index. A skipped repo is still searchable and
is re-indexed by any explicit ``index_folder``; once its files or commits are
recent again it is watched on the next rediscovery pass.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from . import config as _config
from .storage.generation import connect_readonly

logger = logging.getLogger(__name__)

SKIP_TEMP = "temp"
SKIP_IDLE = "idle"

_TEMP_ROOT_NAMES = frozenset({"tmp", "temp"})
_DAY_NS = 86_400 * 1_000_000_000

# (folder, git_head) -> commit time in ns, or None when git could not date it.
_commit_times: dict[tuple[str, str], Optional[int]] = {}


def settings() -> tuple[bool, int]:
    """(skip_temp, max_idle_days) from config. A bad value keeps the default."""
    skip = _config.get("watch_all_skip_temp", True)
    days = _config.get("watch_all_max_idle_days", 14)
    if not isinstance(days, int) or isinstance(days, bool) or days < 0:
        days = 14
    return (False if skip is False else True), days


def is_temp_path(path: str) -> bool:
    """True when ``path`` sits under the system temp dir or a top-level tmp/temp folder."""
    try:
        p = Path(path).resolve()
    except OSError:
        return False
    try:
        tmp = Path(tempfile.gettempdir()).resolve()
        if p == tmp or tmp in p.parents:
            return True
    except OSError:
        logger.debug("could not resolve the temp dir", exc_info=True)
    parts = [x.lower() for x in p.parts[1:]]
    if parts and parts[0] in _TEMP_ROOT_NAMES:
        return True
    return len(parts) >= 2 and parts[0] == "var" and parts[1] in _TEMP_ROOT_NAMES


def newest_indexed_mtime_ns(db_path: Path) -> Optional[int]:
    """The newest file mtime the index stored, or None when it cannot be read."""
    try:
        conn = connect_readonly(db_path)
        try:
            row = conn.execute("SELECT MAX(mtime_ns) FROM files").fetchone()
        finally:
            conn.close()
    except Exception:
        logger.debug("could not read file mtimes from %s", db_path, exc_info=True)
        return None
    value = row[0] if row else None
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


def commit_time_ns(folder: str, git_head: str) -> Optional[int]:
    """When ``git_head`` was committed, or None. Cached per (folder, head)."""
    key = (folder, git_head)
    if key in _commit_times:
        return _commit_times[key]
    value: Optional[int] = None
    try:
        out = subprocess.run(
            ["git", "-C", folder, "show", "-s", "--format=%ct", git_head],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        text = (out.stdout or "").strip()
        if out.returncode == 0 and text.isdigit():
            value = int(text) * 1_000_000_000
    except (OSError, subprocess.SubprocessError):
        logger.debug("could not date %s in %s", git_head, folder, exc_info=True)
    _commit_times[key] = value
    return value


def skip_reason(
    folder: str,
    entry: dict,
    db_path: Optional[Path],
    *,
    skip_temp: bool,
    max_idle_days: int,
    now_ns: Optional[int] = None,
) -> Optional[str]:
    """``"temp"``, ``"idle"`` or None (watch it)."""
    if skip_temp and is_temp_path(folder):
        return SKIP_TEMP
    if max_idle_days <= 0 or db_path is None:
        return None
    now_ns = time.time_ns() if now_ns is None else now_ns
    cutoff = now_ns - max_idle_days * _DAY_NS
    newest = newest_indexed_mtime_ns(db_path)
    if newest is None or newest >= cutoff:
        return None
    head = (entry.get("git_head") or "").strip()
    if head:
        committed = commit_time_ns(folder, head)
        if committed is None or committed >= cutoff:
            return None
    return SKIP_IDLE
