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
   key's pending list (one small file per path, written under a temporary
   name and renamed into place, so a reader never sees half a write), and the
   live run indexes the pending paths in the same
   process before it finishes. Each pending path is still indexed by its own
   ``index_file`` call, which loads the index again; what the bound saves is
   the concurrent processes, not the loads.
2. **At most ``MAX_CONCURRENT`` runs on the machine.** A run that finds every
   slot taken does no work and exits.

After the live run gives its locks back it looks at the pending list once more
(``drain_after_release``, up to ``AFTER_RELEASE_TRIES`` times), so a path
queued between its last read of the list and its release is not stranded. A
run whose path went to the list checks the list once more after writing it,
because the live run may have taken its last look and released in between.
A path is still left over when every machine slot is taken at that moment,
when the run itself failed, when it arrives during the last try's own
release, or when the run holding the checkout's lock was one that found no
machine slot and gave the lock straight back. Such a path, like a
run turned away as busy, is re-indexed only by a running watcher, by
``refresh_on_read`` on the next read of a git repository (at most once per
30 s per server, and not while another write holds the index), or by the next
edit or ``index_folder``.

Every lock is an ordinary ``process_locks`` lock, so one held by a process that
died or whose PID was reused reads as free and a crash never leaks a slot. The
locks live in one private directory per user (``slot_dir``), never under the
index store: a host may give every session its own ``CODE_INDEX_PATH``, and a
bound kept inside each store would bound nothing across them. On POSIX the
system temp directory is shared, so the folder carries the user's id and is
kept at ``0700``: it is opened without following a symlink, checked and, when
it is this user's own with another mode, tightened through that descriptor.
A folder that is a symlink, another user's or not a folder is refused, and
the run does no work and exits, leaving the file to the watcher,
``refresh_on_read`` or the next edit. The name is predictable, so another
local user who creates it first turns this user's hook re-index off; the run
logs that as a WARNING, which nobody reads. On Windows the check is only that
it is a folder: the temp directory is per user unless ``TMP`` or ``TEMP``
points at a shared folder.

``process_locks`` reads a lock file it cannot create (a full or read-only
folder) as held, so such a folder reports ``busy``, not ``slot_error``. An
exception from the lock layer runs the work unbounded and says so.

The bound lives in the command, so every caller of ``index-file`` inherits it.
The hook does not read the slots first: importing the lock layer would cost
every hook about 0.66 s, and a run that finds no slot exits before it loads
an index.
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import os
import stat
import tempfile
import time
import uuid
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
#: A key with this many queued paths queues no more; those are left to the
#: watcher, refresh_on_read or the next edit.
MAX_PENDING = 500
#: How many times a finished run re-claims to drain what arrived after its release.
AFTER_RELEASE_TRIES = 3
#: How many times one claim re-reads the pending list before giving the locks back.
DRAIN_ROUNDS = 20
#: A ``.tmp`` entry older than this was left by a writer that died mid-write,
#: and the next take removes it.
STALE_TMP_SECONDS = 60
#: Orders one process's entries when the clock does not move between them
#: (``time.time_ns()`` repeats within a tick on Windows before Python 3.13).
_sequence = itertools.count()


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
    if uid is not None and stat.S_IMODE(st.st_mode) != 0o700:
        return f"has mode {stat.S_IMODE(st.st_mode):o}, not 700"
    return None


class SlotDirRefused(PermissionError):
    """The slot folder is a symlink, another user's, or not a folder."""


def _owner_uid() -> Optional[int]:
    return None if os.name == "nt" else os.getuid()


def _open_dir_nofollow(d: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(d, flags)


def _fstat(fd: int) -> os.stat_result:
    return os.fstat(fd)


def _fchmod(fd: int, mode: int) -> None:
    getattr(os, "fchmod")(fd, mode)  # POSIX only; never reached on Windows


def _close(fd: int) -> None:
    os.close(fd)


def _check_posix_dir(d: Path, uid: int) -> Optional[str]:
    """Check ``d`` through a descriptor opened without following a symlink, and
    tighten it there when it is ``uid``'s own: the path is looked up once, so
    nothing can be swapped in between the check and the change.

    Later lock and queue operations use the path, so they assume the temp
    directory holding ``d`` is sticky or private (as ``/tmp`` is): in a
    world-writable folder without the sticky bit another user could rename
    ``d`` after this check."""
    try:
        fd = _open_dir_nofollow(d)
    except OSError as e:
        return f"cannot be opened as a folder without following a link ({e.strerror})"
    try:
        st = _fstat(fd)
        why = _check_private(st, uid)
        if why and stat.S_ISDIR(st.st_mode) and st.st_uid == uid:
            _fchmod(fd, 0o700)
            why = _check_private(_fstat(fd), uid)
        return why
    finally:
        _close(fd)


def _private_dir() -> Path:
    """``slot_dir()``, created if absent and kept ``0700`` when it is ours.

    A folder this user owns with another mode (made by an earlier version, or
    by the lock layer with the default umask) is tightened. One that is a
    symlink, belongs to another user or is not a folder raises
    ``SlotDirRefused``.
    """
    d = slot_dir()
    try:
        os.mkdir(d, 0o700)
    except FileExistsError:
        pass
    uid = _owner_uid()
    why = _check_private(os.lstat(d), None) if uid is None else _check_posix_dir(d, uid)
    if why:
        raise SlotDirRefused(f"{d} {why}")
    return d


def _targets() -> list[str]:
    return [f"slot{i}" for i in range(MAX_CONCURRENT)]


def _digest(key: str) -> str:
    # surrogatepass: a POSIX path that is not valid UTF-8 arrives with lone
    # surrogates (surrogateescape), and a Windows name can carry one too.
    return hashlib.sha256(key.lower().encode("utf-8", "surrogatepass")).hexdigest()[:16]


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


def _pending_prefix(key: str) -> str:
    return f"_pending_{_digest(key)}_"


def _pending_files(key: str) -> list[Path]:
    """This key's queued paths, oldest first; never an unfinished write."""
    return sorted(_private_dir().glob(f"{_pending_prefix(key)}*.path"))


@dataclass
class Claim:
    index_target: Optional[str]
    slot: Optional[str]
    #: Set when the slot folder or the lock layer raised and the run went ahead
    #: without the lock it was taking (without both, or with this checkout's
    #: lock and no machine slot). A lock file the lock layer cannot create is
    #: not an error here: ``process_locks.acquire`` reads it as held, so it
    #: reports busy.
    lock_error: Optional[str] = None


def claim(key: str) -> tuple[Optional[Claim], Optional[str]]:
    """Take this key's lock and a machine slot.

    Returns ``(claim, None)`` on success, or ``(None, reason)`` where reason is
    ``"same_index_busy"`` (another run holds this key), ``"busy"``, or
    ``"slot_dir_refused"`` (the slot folder is a symlink or another user's:
    nothing runs, because a folder someone else controls could hold every
    slot or none). Any other lock error runs the work and says so.
    """
    try:
        d = str(_private_dir())
    except SlotDirRefused as e:
        logger.warning("index-file: no run, the slot folder is refused: %s", e)
        return None, "slot_dir_refused"
    except Exception as e:
        logger.warning(
            "index-file runs unbounded: the slot folder failed (%s)", e, exc_info=True
        )
        return Claim(index_target=None, slot=None, lock_error=f"slot folder: {e}"), None
    try:
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
    """Leave ``path`` for the live run of this key. False when not recorded.

    The path goes into a file of its own under a temporary name, renamed into
    place once written, so a run reading the list sees only whole entries and
    never removes one it has not read.
    """
    try:
        d = _private_dir()
        if len(_pending_files(key)) >= MAX_PENDING:
            return False
        name = (
            f"{_pending_prefix(key)}{time.time_ns():020d}_{next(_sequence):08d}"
            f"_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        )
        tmp = d / f"{name}.tmp"
    except OSError:
        logger.debug("could not queue %s", path, exc_info=True)
        return False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogatepass") as f:
            f.write(path)
        os.replace(tmp, d / f"{name}.path")
        return True
    except (OSError, ValueError):
        # ValueError: a path that cannot be encoded even with surrogatepass.
        logger.debug("could not queue %r", path, exc_info=True)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def has_pending(key: str) -> bool:
    """Whether this key has a queued path (False when the folder cannot be read)."""
    try:
        return bool(_pending_files(key))
    except OSError:
        logger.debug("could not read the pending list for %s", key, exc_info=True)
        return False


def _sweep_stale_tmp(key: str) -> None:
    """Remove ``.tmp`` entries a writer that died mid-write left behind."""
    now = time.time()
    for f in _private_dir().glob(f"{_pending_prefix(key)}*.tmp"):
        try:
            if now - f.stat().st_mtime > STALE_TMP_SECONDS:
                f.unlink()
        except OSError:
            logger.debug("could not sweep %s", f, exc_info=True)


def _read_entry(f: Path) -> str:
    fd = os.open(f, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "r", encoding="utf-8", errors="surrogatepass") as fh:
        return fh.read().strip()


def take_pending(key: str) -> list[str]:
    """Read and remove this key's queued paths; each path once, oldest first.

    An entry is opened without following a symlink. One that cannot be read, or
    read but not removed, is left in place for a later take and not returned:
    returning an entry that stays would index it again on every round.
    """
    try:
        _sweep_stale_tmp(key)
        files = _pending_files(key)
    except OSError:
        logger.debug("no private slot directory for %s", key, exc_info=True)
        return []
    seen: dict[str, None] = {}
    for f in files[:MAX_PENDING]:
        try:
            text = _read_entry(f)
        except (OSError, ValueError):
            # ValueError: bytes that are not text (an entry this module did not
            # write). Raising here would stop every later run's drain for this
            # checkout; one bad entry is left and the rest are taken.
            logger.debug("could not read %s; left for the next take", f, exc_info=True)
            continue
        try:
            f.unlink()
        except OSError:
            logger.debug("could not remove %s; left for a later take", f, exc_info=True)
            continue
        if text:
            seen.setdefault(text, None)
    return list(seen)


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
