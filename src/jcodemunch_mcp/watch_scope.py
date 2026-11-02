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
is re-indexed by any explicit ``index_folder``. Because the index's signals do
not move while a repo is unwatched, an idle verdict also checks the tree
(``tree_changed_since``, ``head_unchanged``), at most every ``IDLE_RECHECK_S``,
so a repo edited or committed in after a skip is watched again.
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

# A file mtime counts as newer than the index only past this slack: some file
# systems store coarser times than the index read (FAT keeps 2 s).
_MTIME_SLACK_NS = 2 * 1_000_000_000

#: Seconds an idle verdict is reused before the tree is stat'ed again.
IDLE_RECHECK_S = 600.0

# (folder, git_head) -> commit time in ns, or None when git could not date it.
_commit_times: dict[tuple[str, str], Optional[int]] = {}

# (folder, db path, max_idle_days) -> (idle?, time.monotonic() of the check)
_idle_verdicts: dict[tuple[str, str, int], tuple[bool, float]] = {}


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
    # Both spellings: macOS resolves /tmp to /private/tmp and /var/tmp to
    # /private/var/tmp, so the resolved path alone never starts with tmp.
    for spelling in (Path(path).absolute(), p):
        parts = [x.lower() for x in spelling.parts[1:]]
        if parts[:1] == ["private"]:
            parts = parts[1:]
        if parts and parts[0] in _TEMP_ROOT_NAMES:
            return True
        if len(parts) >= 2 and parts[0] == "var" and parts[1] in _TEMP_ROOT_NAMES:
            return True
    return False


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
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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


def head_unchanged(folder: str, entry: dict) -> Optional[bool]:
    """Is the checkout still at the commit the index stored? None when unknown.

    Asks `FreshnessProbe.repo_freshness`, the authority on this question:
    ``fresh`` is True, ``stale`` False, ``not_tracked`` (a plain folder, no
    revision to move) True, anything else None.
    """
    try:
        from .retrieval.freshness import FreshnessProbe

        verdict = FreshnessProbe(
            folder, entry.get("indexed_at") or "", entry.get("git_head")
        ).repo_freshness
    except Exception:
        logger.debug("freshness probe failed for %s", folder, exc_info=True)
        return None
    if verdict in ("fresh", "not_tracked"):
        return True
    if verdict == "stale":
        return False
    return None


def tree_changed_since(folder: str, db_path: Path, newest_ns: int) -> Optional[bool]:
    """Has the tree moved past what the index holds? None when it cannot tell.

    The index's own signals do not move while a repo is unwatched, so a skip
    decided from them alone would be permanent. This stats every file the index
    holds and each folder that holds one: an edited file has a newer mtime, a
    deleted one is missing, and a file added or renamed moves its folder's
    mtime. A change in a folder holding no indexed file is not seen.
    """
    try:
        conn = connect_readonly(db_path)
        try:
            paths = [r[0] for r in conn.execute("SELECT path FROM files")]
        finally:
            conn.close()
    except Exception:
        logger.debug("could not read file paths from %s", db_path, exc_info=True)
        return None
    root = Path(folder)
    limit = newest_ns + _MTIME_SLACK_NS
    folders: set[Path] = {root}
    for rel in paths:
        target = root / rel
        folders.add(target.parent)
        try:
            if target.stat().st_mtime_ns > limit:
                return True
        except FileNotFoundError:
            return True
        except OSError:
            return None
    for d in folders:
        try:
            if d.stat().st_mtime_ns > limit:
                return True
        except FileNotFoundError:
            return True
        except OSError:
            return None
    return False


def _idle(
    folder: str, entry: dict, db_path: Path, max_idle_days: int, now_ns: int
) -> bool:
    cutoff = now_ns - max_idle_days * _DAY_NS
    newest = newest_indexed_mtime_ns(db_path)
    if newest is None or newest >= cutoff:
        return False
    head = (entry.get("git_head") or "").strip()
    if head:
        committed = commit_time_ns(folder, head)
        if committed is None or committed >= cutoff:
            return False
    # The index says idle; the tree must agree, or the skip never ends.
    if head_unchanged(folder, entry) is not True:
        return False
    return tree_changed_since(folder, db_path, newest) is False


def skip_reason(
    folder: str,
    entry: dict,
    db_path: Optional[Path],
    *,
    skip_temp: bool,
    max_idle_days: int,
    now_ns: Optional[int] = None,
) -> Optional[str]:
    """``"temp"``, ``"idle"`` or None (watch it).

    The idle verdict is kept for `IDLE_RECHECK_S` per repo, so the rediscovery
    pass (every 30 s) re-stats an idle repo's tree at most that often. A repo
    edited or committed after a skip is therefore watched again within that
    interval.
    """
    if skip_temp and is_temp_path(folder):
        return SKIP_TEMP
    if max_idle_days <= 0 or db_path is None:
        return None
    now_ns = time.time_ns() if now_ns is None else now_ns
    key = (folder, str(db_path), max_idle_days)
    cached = _idle_verdicts.get(key)
    clock = time.monotonic()
    if cached is not None and clock - cached[1] < IDLE_RECHECK_S:
        idle = cached[0]
    else:
        idle = _idle(folder, entry, db_path, max_idle_days, now_ns)
        _idle_verdicts[key] = (idle, clock)
    return SKIP_IDLE if idle else None
