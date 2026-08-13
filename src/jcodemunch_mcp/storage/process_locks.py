"""Process-level coordination locks for multi-agent shared-index workflows.

When multiple MCP-server processes (Claude Code + Cursor + Codex + ...) share
the same on-disk index, they must coordinate so that:

* Only one process indexes a given repo at a time (otherwise two save_index
  calls race the SQLite write).
* Only one process actively watches a given repo (otherwise duplicate reindex
  storms on every file change).
* Other processes can *see* who currently holds each lock — `get_watch_status`
  surfaces holder identity (pid, client_id, started_at, age) so agents know a
  parallel session is live.

This module promotes the lock helpers originally written for watcher.py into a
generic primitive reusable from any code path that needs per-repo
single-writer coordination.

Semantics (same as the original watcher lock, deliberately preserved):

* Atomic ``os.O_CREAT | os.O_EXCL`` creation eliminates the TOCTOU race
  window of "check then write."
* On Unix, layered ``fcntl.flock(LOCK_EX | LOCK_NB)`` adds OS-level advisory
  locking — if the holder dies, the OS releases the lock automatically.
* Cross-platform stale-lock recovery via PID liveness check: if the lock
  metadata names a PID that no longer exists, the lock is reclaimed.
* Lock metadata is human-readable JSON; readers can ``inspect`` a lock
  without acquiring it (used by `get_watch_status`).

Read-only query paths (load_index, search, find_references, ...) never touch
these locks — SQLite WAL handles concurrent reads natively.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# fcntl is Unix-only. On Windows we rely on the atomic O_EXCL guarantee.
try:
    import fcntl
except ImportError:
    fcntl = None

# Module-level registry of open file descriptors held for Unix flock. Keyed by
# (scope, target) so the same process can hold an index_write lock on a repo
# while another thread holds the watcher lock.
_held_fds: dict[tuple[str, str], int] = {}


def _client_id() -> str:
    """Best-effort identification of which agent runtime spawned us.

    Read JCODEMUNCH_CLIENT_ID if set (the explicit, documented path). Fall
    back to the basename of sys.argv[0] (catches `claude`, `cursor`, `codex`
    when they exec our entry point). Return "unknown" if neither produces
    anything meaningful — better than guessing wrong.
    """
    explicit = os.environ.get("JCODEMUNCH_CLIENT_ID", "").strip()
    if explicit:
        return explicit
    try:
        arg0 = (sys.argv[0] or "").strip()
        if arg0:
            return Path(arg0).name or "unknown"
    except Exception:
        pass
    return "unknown"


def _path_hash(target: str) -> str:
    """Return a stable 12-char hash of a normalized target identifier.

    Used to derive lock filenames from arbitrary repo identifiers (filesystem
    paths, owner/name slugs). Normalizing on Windows means C:\\Foo and c:\\foo
    map to the same lock.
    """
    resolved = target
    try:
        as_path = Path(target).resolve()
        resolved = str(as_path)
    except (OSError, ValueError):
        pass
    if sys.platform == "win32":
        resolved = resolved.lower()
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]


def _lock_dir(storage_path: Optional[str]) -> Path:
    """Return the directory for lock files, creating it if needed."""
    base = Path(storage_path) if storage_path else Path.home() / ".code-index"
    base.mkdir(parents=True, exist_ok=True)
    return base


def lock_path(scope: str, target: str, storage_path: Optional[str]) -> Path:
    """Compute the lock file path for a (scope, target) pair.

    scope: short verb identifying the lock category (e.g. "watcher",
        "indexwrite"). Two locks with different scopes on the same target do
        not block each other.
    target: the repo identifier — a filesystem path for watcher locks, an
        "owner/name" slug for index-write locks.
    """
    safe_scope = scope.replace("/", "_").replace("\\", "_")
    return _lock_dir(storage_path) / f"_{safe_scope}_{_path_hash(target)}.lock"


@dataclass(frozen=True)
class LockHolder:
    """Metadata about the process currently holding a lock."""

    scope: str
    target: str
    pid: int
    client_id: str
    started_at: str
    lock_path: str

    def age_seconds(self) -> Optional[float]:
        """Best-effort age (seconds since started_at). None if unparseable."""
        try:
            started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            return max(0.0, (now - started).total_seconds())
        except (ValueError, TypeError):
            return None

    def as_dict(self) -> dict:
        d = {
            "scope": self.scope,
            "target": self.target,
            "pid": self.pid,
            "client_id": self.client_id,
            "started_at": self.started_at,
            "lock_path": self.lock_path,
        }
        age = self.age_seconds()
        if age is not None:
            d["age_seconds"] = round(age, 1)
        return d


_STILL_ACTIVE = 259


def _is_pid_alive(pid: int) -> bool:
    """Return True if a process with the given PID is running.

    ⚠ Windows: a successful ``OpenProcess`` does NOT mean the process is alive.
    While any handle to it remains open, the PID stays queryable after exit, and
    the parent that spawned it normally holds exactly such a handle. So a
    crashed server read as alive, which made its lock file look permanently held
    (``inspect`` treats a dead holder as stale and ignorable) and inflated the
    process-registry sprawl count with processes whose memory was already freed.
    ``GetExitCodeProcess`` is the authoritative check: anything other than
    ``STILL_ACTIVE`` means it has exited.

    ⚠ ``argtypes``/``restype`` are REQUIRED, not decoration. ``OpenProcess``
    returns a pointer-sized HANDLE and ctypes defaults the return type to
    ``c_int``, which truncates it on 64-bit — the same trap already documented
    for ``GetProcessTimes`` in ``runtime_identity``. A truncated handle is then
    closed by ``CloseHandle``, which is at best a no-op on the wrong value.
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                # Cannot determine: fall back to "the handle opened", the old
                # behavior, rather than declaring a possibly-live process dead.
                return True
            return code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        # A zombie answers signal 0 but holds no memory. Reap-state is only
        # visible for our own children, so this is best effort by design.
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                fields = fh.read().rsplit(b")", 1)[-1].split()
            if fields and fields[0] == b"Z":
                return False
        except (OSError, IndexError):
            pass
        return True


# FILETIME epoch (1601-01-01) to Unix epoch (1970-01-01), in seconds.
_FILETIME_EPOCH_DELTA = 11644473600


def _process_create_time(pid: int) -> Optional[float]:
    """OS creation time of an arbitrary PID as Unix-epoch seconds, or None.

    jcm#450: ``_is_pid_alive`` answers "is this PID taken?", not "is my process
    still there?" — after PID reuse a registry row or lock file for a long-dead
    holder reads as live forever (observed: two-week-old rows resolving to a
    Chrome renderer and an AMD service). Creation time is the identity anchor:
    recorded at write time, compared at read time; a mismatch means a recycled
    PID, never our holder.

    Windows: OpenProcess + GetProcessTimes (same primitive runtime_identity
    uses for self; here against a foreign PID) — seconds since the Unix epoch.
    ``argtypes``/``restype`` are REQUIRED — see the warning on ``_is_pid_alive``.
    Linux: /proc/<pid>/stat starttime (field 22) — SECONDS SINCE BOOT, not
    epoch. The two platforms deliberately do not share an epoch; values are
    only ever compared against ones produced by this same function on the same
    machine, so the domain only has to be internally stable (see the Linux
    branch comment for why boot-relative is the safe choice there).
    Other platforms: None (identity check degrades to liveness-only).
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class _FILETIME(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD),
            ]

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            creation, exit_t, kernel_t, user_t = (
                _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME(),
            )
            ok = kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_t),
                ctypes.byref(kernel_t),
                ctypes.byref(user_t),
            )
            if not ok:
                return None
            ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            if ticks == 0:
                return None
            return ticks / 1e7 - _FILETIME_EPOCH_DELTA
        finally:
            kernel32.CloseHandle(handle)
    elif sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                fields = fh.read().rsplit(b")", 1)[-1].split()
            # After the comm field: fields[0] is stat field 3; starttime is
            # stat field 22 -> index 19.
            start_ticks = int(fields[19])
            # ⚠ Deliberately NOT converted to wall clock. /proc/stat's btime is
            # derived from the current wall clock, so a settimeofday-class step
            # (suspend/resume, VM restore, first NTP sync) moves every recorded
            # value at once and reads live holders as recycled. This value is
            # only ever compared against one produced by this same function on
            # this same machine, so the epoch is irrelevant to the comparison
            # and seconds-since-boot is strictly safer than seconds-since-epoch.
            # (A lock file surviving a reboot degrades to bare-PID matching —
            # exactly pre-fix behavior, and far rarer than a clock step.)
            return start_ticks / os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError, IndexError):
            return None
    return None


# Both sides derive the value with the same code from the same OS constant
# (Windows: absolute FILETIME; Linux: boot-relative starttime ticks — neither
# moves under wall-clock adjustment), so a real match is near-exact and the
# tolerance only absorbs float rounding. A recycled PID's mismatch is
# minutes-to-weeks, never within this window.
_CREATE_TIME_TOLERANCE_S = 2.0


def _is_live_holder(pid: int, expected_create_time: object) -> bool:
    """Liveness + identity: the PID is alive AND is still the recorded process.

    ``expected_create_time`` is the value recorded at write time (may be absent
    or None for files written by pre-jcm#450 versions — those keep the old
    liveness-only behavior; there is nothing to compare against). If the
    current creation time cannot be read while the PID is alive, fall back to
    liveness-only rather than declaring a possibly-live holder dead.
    """
    if not _is_pid_alive(pid):
        return False
    if not isinstance(expected_create_time, (int, float)):
        return True
    actual = _process_create_time(pid)
    if actual is None:
        return True
    return abs(actual - float(expected_create_time)) <= _CREATE_TIME_TOLERANCE_S


def inspect(scope: str, target: str, storage_path: Optional[str] = None) -> Optional[LockHolder]:
    """Read the holder of a lock without acquiring it.

    Returns ``None`` if the lock file does not exist, is corrupted, or names
    a PID that is no longer alive — or that has been recycled to a different
    process (creation-time mismatch, jcm#450). Stale either way.
    """
    lock_fp = lock_path(scope, target, storage_path)
    if not lock_fp.exists():
        return None
    try:
        data = json.loads(lock_fp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    pid = data.get("pid")
    if pid is None or not isinstance(pid, int):
        return None
    if not _is_live_holder(pid, data.get("create_time")):
        return None
    return LockHolder(
        scope=scope,
        target=str(data.get("target", target)),
        pid=pid,
        client_id=str(data.get("client_id") or "unknown"),
        started_at=str(data.get("started_at") or ""),
        lock_path=str(lock_fp),
    )


def acquire(scope: str, target: str, storage_path: Optional[str] = None) -> bool:
    """Attempt to acquire an exclusive lock for (scope, target).

    Returns True on success, False if another live process holds the lock.
    Caller is responsible for matching :func:`release` on the way out — or
    use :func:`held` as a context manager.

    Implementation: atomic O_EXCL create with PID+client_id+started_at
    metadata, plus an fcntl.flock layer on Unix so OS-level lock release
    fires automatically if the holder dies without unlinking.
    """
    lock_fp = lock_path(scope, target, storage_path)

    metadata = {
        "scope": scope,
        "target": target,
        "pid": os.getpid(),
        "client_id": _client_id(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        # Identity anchor against PID reuse (jcm#450); None on platforms
        # without a creation-time source, which readers treat as "no identity
        # recorded" (liveness-only).
        "create_time": _process_create_time(os.getpid()),
    }
    payload = json.dumps(metadata).encode("utf-8")

    def _try_create() -> bool:
        try:
            fd = os.open(str(lock_fp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            return False
        except OSError:
            return False

    def _apply_flock() -> bool:
        """Layer OS-level flock on Unix. Returns False on race loss."""
        if fcntl is None:
            return True  # Windows — atomic create is enough
        try:
            fd = os.open(str(lock_fp), os.O_RDWR)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            try:
                lock_fp.unlink()
            except OSError:
                pass
            return False
        _held_fds[(scope, target)] = fd
        return True

    # Fast path: atomic create succeeds outright.
    if _try_create():
        return _apply_flock()

    # Stale-lock recovery: inspect existing holder.
    try:
        existing = json.loads(lock_fp.read_text(encoding="utf-8"))
        existing_pid = existing.get("pid")
        if existing_pid is None:
            logger.info("Removing stale %s lock for %s (no pid)", scope, target)
        elif _is_live_holder(existing_pid, existing.get("create_time")):
            client = existing.get("client_id", "unknown")
            logger.info(
                "%s lock held for %s by pid %s (%s)",
                scope, target, existing_pid, client,
            )
            return False
        else:
            logger.info(
                "Removing stale %s lock for %s (pid %s is dead or recycled)",
                scope, target, existing_pid,
            )
    except (json.JSONDecodeError, OSError):
        logger.info("Removing corrupted %s lock for %s", scope, target)

    # Clean up stale lock and retry.
    try:
        lock_fp.unlink()
    except OSError:
        # Windows may hold the file open; O_EXCL on retry will reject anyway.
        pass

    time.sleep(0.05)  # Brief pause narrows the collision window.

    if _try_create():
        return _apply_flock()

    logger.warning("Could not acquire %s lock for %s", scope, target)
    return False


def release(scope: str, target: str, storage_path: Optional[str] = None) -> None:
    """Release the (scope, target) lock and remove the lock file."""
    key = (scope, target)
    if key in _held_fds:
        try:
            os.close(_held_fds[key])
        except OSError:
            pass
        del _held_fds[key]
    try:
        lock_path(scope, target, storage_path).unlink()
    except OSError:
        pass


class held:  # noqa: N801 — context-manager helper, lowercase reads natural at call sites
    """Context manager wrapping :func:`acquire` / :func:`release`.

    By default behaves like ``acquire`` — returns immediately on failure with
    ``False``. Pass ``wait_seconds > 0`` to poll for up to that long, useful
    for serialise-concurrent-writes scenarios like ``save_index`` where two
    MCP processes legitimately want the same lock and should wait their turn
    rather than error.

    Usage::

        with held("indexwrite", f"{owner}/{name}", storage_path, wait_seconds=30) as got:
            if not got:
                raise RuntimeError("another process held the index-write lock too long")
            sqlite_store.save_index(...)
    """

    def __init__(
        self,
        scope: str,
        target: str,
        storage_path: Optional[str] = None,
        *,
        wait_seconds: float = 0.0,
        poll_seconds: float = 0.5,
    ) -> None:
        self.scope = scope
        self.target = target
        self.storage_path = storage_path
        self.wait_seconds = max(0.0, wait_seconds)
        self.poll_seconds = max(0.05, poll_seconds)
        self._acquired = False

    def __enter__(self) -> bool:
        deadline = time.monotonic() + self.wait_seconds
        while True:
            self._acquired = acquire(self.scope, self.target, self.storage_path)
            if self._acquired or time.monotonic() >= deadline:
                return self._acquired
            time.sleep(self.poll_seconds)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._acquired:
            release(self.scope, self.target, self.storage_path)
            self._acquired = False


def current_holder_diagnostic(scope: str, target: str, storage_path: Optional[str] = None) -> str:
    """Return a one-line diagnostic about the current holder, or '' if free.

    Convenience helper for error messages: "another process is indexing
    {target}{diagnostic}; gave up after 30s".
    """
    h = inspect(scope, target, storage_path)
    if h is None:
        return ""
    age = h.age_seconds()
    age_str = f", started {age:.0f}s ago" if age is not None else ""
    return f" (held by pid {h.pid}, client={h.client_id}{age_str})"
