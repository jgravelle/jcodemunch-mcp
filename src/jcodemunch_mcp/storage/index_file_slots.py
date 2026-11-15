"""A per-machine bound on how many ``index-file`` runs work at once.

The PostToolUse hook starts one detached ``jcodemunch-mcp index-file <path>``
for every Edit or Write, and nothing waited for or counted them. Each run loads
the repository's index: about 250 MB and 2.5 s on a 50,000-symbol index, so
eight edits in a row held eight of them and about 800 MB at once. An agent session
that edits 47 times against an index that takes minutes to build starts runs
faster than they finish, memory pressure slows each one, and the pile grows.
A host that ends the session by killing only the agent leaves the pile running.

Two rules, both checked before an index is loaded:

1. **One run per checkout.** The key is the index store plus the file's git
   root, or the file's own folder outside git (``index_key``). A run whose key
   already has a live run does no work itself. It leaves its path in that
   key's pending list, and the live run indexes the pending paths in the same
   process before it finishes. Each pending path is still indexed by its own
   ``index_file`` call, which loads the index again; what the bound saves is
   the concurrent processes, not the loads.
2. **At most ``MAX_CONCURRENT`` runs on the machine.** A run that finds every
   slot taken does no work and exits.

After the live run gives its locks back it looks at the pending list once more
(``drain_after_release``, up to ``AFTER_RELEASE_TRIES`` times), so a path
queued between its last read of the list and its release is not stranded. A
path is still left over when every machine slot is taken at that moment, when
the run itself failed, when the list stays held open through every retry, or
when it arrives during the last try's own release. Such a path, like a
run turned away as busy, is re-indexed only by a running watcher, by
``refresh_on_read`` on the next read of a git repository (at most once per
30 s per server, and not while another write holds the index), or by the next
edit or ``index_folder``.

Every lock is an ordinary ``process_locks`` lock, so one held by a process that
died or whose PID was reused reads as free and a crash never leaks a slot. The
locks live in one private directory per user (``slot_dir``), never under the
index store: a host may give every session its own ``CODE_INDEX_PATH``, and a
bound kept inside each store would bound nothing across them. On POSIX the
directory is created ``0700`` and refused if it is a symlink, is owned by
another user, or is open to others (``_check_private``), because the system
temp directory is shared there. On Windows the check is only that it is a
directory: the temp directory is per user unless ``TMP``/``TEMP`` was pointed
at a shared folder. A refused directory makes the run go ahead
unbounded and say so, as any lock error does.

The bound lives in the command, so every caller of ``index-file`` inherits it.
The hook does not read the slots first: importing the lock layer would cost
every hook about 0.66 s, and a run that finds no slot exits before it loads
an index.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import process_locks

logger = logging.getLogger(__name__)

SCOPE = "indexfile"
INDEX_SCOPE = "indexfile-index"
#: Seeded, not calibrated: with this bound, eight back-to-back hooks held six
#: processes and 367 MB at the peak, two of them working, on a 50,000-symbol
#: index (evidence/measurements.md); no comparison of 1, 2 and 3 was made.
MAX_CONCURRENT = 2
#: A pending list longer than this is cut; the paths cut are left to the
#: watcher, refresh_on_read or the next edit.
MAX_PENDING = 500
#: How many times a finished run re-claims to drain what arrived after its release.
AFTER_RELEASE_TRIES = 3
#: How many times one claim re-reads the pending list before giving the locks back.
DRAIN_ROUNDS = 20
#: A pending list another process has open (a sharing violation on Windows) is
#: retried this many times, TAKE_WAIT_SECONDS apart, before it is left in place.
TAKE_TRIES = 5
TAKE_WAIT_SECONDS = 0.05


def slot_dir() -> Path:
    """The one directory, per user and machine, that holds every slot.

    Windows' temp directory is already per user; on POSIX the shared temp
    directory gets a per-user name, checked by ``_private_dir``.
    """
    base = Path(tempfile.gettempdir())
    if os.name == "nt":
        return base / "jcodemunch-index-file"
    return base / f"jcodemunch-index-file-{os.getuid()}"


def _check_private(st: os.stat_result, uid: Optional[int]) -> Optional[str]:
    """Why an ``lstat`` result is not a private directory of ``uid``, or None."""
    if stat.S_ISLNK(st.st_mode):
        return "is a symlink"
    if not stat.S_ISDIR(st.st_mode):
        return "is not a directory"
    if uid is not None and st.st_uid != uid:
        return f"is owned by uid {st.st_uid}"
    if uid is not None and st.st_mode & 0o077:
        return f"is open to other users (mode {stat.S_IMODE(st.st_mode):o})"
    return None


def _private_dir() -> Path:
    """``slot_dir()``, created if absent; raises if it is not private to this user."""
    d = slot_dir()
    try:
        os.mkdir(d, 0o700)
    except FileExistsError:
        pass
    why = _check_private(os.lstat(d), None if os.name == "nt" else os.getuid())
    if why:
        raise PermissionError(f"{d} {why}; index-file runs are not bounded")
    return d


def _targets() -> list[str]:
    return [f"slot{i}" for i in range(MAX_CONCURRENT)]


def _digest(key: str) -> str:
    return hashlib.sha256(key.lower().encode("utf-8")).hexdigest()[:16]


def _lock_target(name: str) -> str:
    """An absolute lock target. ``process_locks`` resolves a target as a path,
    so a bare name like ``slot0`` would name a different lock in every working
    directory, and runs started from two folders would never see each other."""
    return str(slot_dir() / name)


def index_key(path: str, storage_path: Optional[str]) -> str:
    """The run's key: its index store plus the file's git root, or its folder outside git."""
    store = os.path.abspath(storage_path) if storage_path else "<default>"
    try:
        from .git_root import _find_git_root

        root = _find_git_root(Path(path).parent) or Path(path).parent
    except Exception:
        logger.debug("could not find the checkout of %s", path, exc_info=True)
        root = Path(path).parent
    return f"{store}|{os.path.abspath(str(root))}"


def _pending_file(key: str) -> Path:
    return _private_dir() / f"_pending_{_digest(key)}.txt"


@dataclass
class Claim:
    index_target: Optional[str]
    slot: Optional[str]
    #: Set when a lock could not be read or written and the run went ahead
    #: without it. UNKNOWN runs the work: reporting it as busy would turn off
    #: every re-index with a reason that is not true.
    lock_error: Optional[str] = None


def claim(key: str) -> tuple[Optional[Claim], Optional[str]]:
    """Take this key's lock and a machine slot.

    Returns ``(claim, None)`` on success, or ``(None, reason)`` where reason is
    ``"same_index_busy"`` (another run holds this key) or ``"busy"``.
    """
    try:
        d = str(_private_dir())
        index_target = _lock_target(f"key-{_digest(key)}")
        got_index = process_locks.acquire(INDEX_SCOPE, index_target, d)
    except Exception as e:
        logger.warning(
            "index-file runs unbounded: the index lock failed (%s)", e, exc_info=True
        )
        return Claim(index_target=None, slot=None, lock_error=f"index lock: {e}"), None
    if not got_index:
        return None, "same_index_busy"
    for name in _targets():
        target = _lock_target(name)
        try:
            if process_locks.acquire(SCOPE, target, d):
                return Claim(index_target=index_target, slot=target), None
        except Exception as e:
            logger.warning(
                "index-file runs unbounded: slot %s in %s failed (%s)",
                target,
                d,
                e,
                exc_info=True,
            )
            return Claim(
                index_target=index_target, slot=None, lock_error=f"{name}: {e}"
            ), None
    process_locks.release(INDEX_SCOPE, index_target, d)
    return None, "busy"


def release(c: Claim) -> None:
    """Give back both locks taken by ``claim``."""
    d = str(slot_dir())
    if c.slot is not None:
        process_locks.release(SCOPE, c.slot, d)
    if c.index_target is not None:
        process_locks.release(INDEX_SCOPE, c.index_target, d)


def queue(key: str, path: str) -> bool:
    """Leave ``path`` for the live run of this key. False when not recorded."""
    try:
        p = _pending_file(key)
        if p.exists() and p.stat().st_size > MAX_PENDING * 1024:
            return False
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(p, flags, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(path + "\n")
        return True
    except OSError:
        logger.debug("could not queue %s", path, exc_info=True)
        return False


def has_pending(key: str) -> bool:
    """Whether this key's pending list holds anything (False when it cannot be read)."""
    try:
        p = _pending_file(key)
        return p.exists() and p.stat().st_size > 0
    except OSError:
        logger.debug("could not read the pending list for %s", key, exc_info=True)
        return False


def take_pending(key: str) -> Optional[list[str]]:
    """Read and clear this key's pending list; each path once, in order.

    ``[]`` means there is no list. ``None`` means the list is there and could
    not be taken: another process held it open through every retry.
    """
    try:
        p = _pending_file(key)
    except OSError:
        logger.debug("no private slot directory for %s", key, exc_info=True)
        return []
    taken = p.with_suffix(f".{os.getpid()}.taking")
    for attempt in range(TAKE_TRIES):
        try:
            os.replace(p, taken)
            break
        except FileNotFoundError:
            return []
        except OSError:
            if attempt == TAKE_TRIES - 1:
                logger.warning(
                    "index-file: could not take the pending list %s; its paths are left in it",
                    p,
                    exc_info=True,
                )
                return None
            time.sleep(TAKE_WAIT_SECONDS)
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


def drain(key: str, index_one: Callable[[str], object]) -> tuple[int, int]:
    """Index every pending path of ``key`` while holding its claim.

    Returns ``(indexed, failed)``. A path fails when ``index_one`` raises or
    returns a result whose ``success`` is false, which is how ``index_file``
    reports a missing, unreadable or rejected file. One path's failure is
    logged and counted and never stops the rest.
    """
    indexed = failed = 0
    for _round in range(DRAIN_ROUNDS):
        pending = take_pending(key)
        if not pending:
            break
        for path in pending:
            try:
                r = index_one(path)
                if isinstance(r, dict) and not r.get("success"):
                    failed += 1
                    logger.warning(
                        "index-file: the queued path %s was not indexed: %s",
                        path,
                        r.get("error"),
                    )
                else:
                    indexed += 1
            except Exception:
                failed += 1
                logger.warning(
                    "index-file: the queued path %s failed", path, exc_info=True
                )
    return indexed, failed


def drain_after_release(
    key: str, index_one: Callable[[str], object]
) -> tuple[int, int]:
    """Drain what arrived after the last read of the list, under a new claim.

    A path queued after the live run's last ``take_pending`` and before its
    ``release`` was turned away by a lock that is now free, so nothing else
    would index it. If the claim goes to another run, that run checks again
    after its own release; if every machine slot is taken, the path is left
    to the watcher, refresh_on_read or the next edit.
    """
    indexed = failed = 0
    for _try in range(AFTER_RELEASE_TRIES):
        if not has_pending(key):
            break
        c, _why = claim(key)
        if c is None:
            break
        try:
            i, f = drain(key, index_one)
            indexed += i
            failed += f
        finally:
            release(c)
    return indexed, failed
