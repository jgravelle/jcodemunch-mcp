"""Stop a stdio server whose client is gone, even if its stdin stays open.

A stdio server ends when stdin reaches EOF. That covers a client that closes
its pipe, and misses one that dies while something else keeps the pipe open (a
proxy in between, a handle a sibling inherited): the server then runs on with
nobody to answer (BACKLOG row 64).

At start the server records its parent's PID and that process's creation time.
A daemon thread polls every ``JCODEMUNCH_PARENT_WATCH_SECONDS`` and calls back
once the parent is gone. Liveness and creation time come from
``storage.process_locks``, which already answers both for the lock files.

⚠⚠ **Uncertainty never exits.** A false "gone" kills a live client's server,
which is worse than a server that lingers. So a parent that cannot be queried
at start disables the watch, and a check that raises counts as alive.
⚠ The creation time is what makes a dead parent stay dead: Windows reuses
PIDs, and a recycled PID answers "alive" for a process that is not our parent.
⚠ On Unix an orphan is reparented (to init or a subreaper), so a changed
``getppid()`` is itself the answer; Windows never reparents.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# The parent as it was when this module was imported. `server.py` imports this
# module before anything slow, so on Unix a client that dies while the server
# is still importing shows up as a changed parent: by the time the watch
# starts, the orphan has been reparented (to init or a subreaper), and the
# PID it reports then is not the client's (row 64 review round 1).
ORIGIN_PPID = os.getppid()


# Liveness and creation time come from `storage.process_locks`, imported on
# first use so this module costs nothing at import.
def _is_pid_alive(pid: int) -> bool:
    from .storage.process_locks import _is_pid_alive as impl

    return impl(pid)


def _process_create_time(pid: int) -> Optional[float]:
    from .storage.process_locks import _process_create_time as impl

    return impl(pid)


DEFAULT_SECONDS = 5.0
ENV = "JCODEMUNCH_PARENT_WATCH_SECONDS"


def interval_seconds() -> float:
    """The poll interval; 0 means off. A value that does not parse keeps the
    default, so a typo cannot switch the watch off."""
    raw = os.environ.get(ENV)
    if raw is None or not raw.strip():
        return DEFAULT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.debug("Bad %s=%r, using default", ENV, raw)
        return DEFAULT_SECONDS
    return value if value > 0 else 0.0


class ParentWatch:
    """One parent process, identified by PID and creation time."""

    __slots__ = ("pid", "anchor", "started_ppid", "gone_at_start")

    def __init__(self, pid: int, anchor: Optional[float], started_ppid: int):
        self.pid = pid
        self.anchor = anchor
        self.started_ppid = started_ppid
        self.gone_at_start = False

    @classmethod
    def of(cls, ppid: int) -> Optional["ParentWatch"]:
        """A watch on ``ppid``, or None when it cannot be watched: PID 0 or 1
        (no real client: already orphaned, or started by the init system), or
        a parent that cannot be queried.

        A parent that exited before the watch started (the client died while
        this server was still importing) is gone at start, and so is a PID
        that now names a process younger than this one, since a parent always
        starts before its child."""
        if ppid <= 1:
            return None
        try:
            if not _is_pid_alive(ppid):
                if not _no_such_process(ppid):
                    return None  # access denied, or cannot tell: never exit on it
                w = cls(ppid, None, ppid)
                w.gone_at_start = True
                return w
            anchor = _process_create_time(ppid)
            w = cls(ppid, anchor, ppid)
            me = _process_create_time(os.getpid())
            if anchor is not None and me is not None and anchor > me + 1.0:
                w.gone_at_start = True
            return w
        except Exception:
            logger.debug("parent %s cannot be queried", ppid, exc_info=True)
            return None

    def gone(self) -> bool:
        """True only when the parent is known to be gone."""
        if self.gone_at_start:
            return True
        try:
            if os.getppid() != self.started_ppid:
                return True
            if not _is_pid_alive(self.pid):
                # A failed query (access denied, a transient OpenProcess
                # failure) also reads "not alive" on Windows; only the OS
                # saying no such process means gone (row 64 review round 2).
                return _no_such_process(self.pid)
            if self.anchor is not None:
                now = _process_create_time(self.pid)
                if now is not None and abs(now - self.anchor) > 1.0:
                    return True
        except Exception:
            logger.debug("parent check failed; counting it alive", exc_info=True)
        return False


def _no_such_process(pid: int) -> bool:
    """True only when the OS says no process has this PID. A process we may
    not open (another user's, a protected one) is NOT reported gone: that
    answer would kill a live client's server."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            # Openable but not alive: it exited and a handle keeps it listed.
            return True
        return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: no such PID
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


# Programs that only start another program and wait for it.
_RUNNER_NAMES = frozenset({"uv.exe", "uvx.exe", "py.exe", "pyw.exe"})


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _launcher_images() -> set[str]:
    """Executables that, as our parent, are a launcher rather than a client.

    ⚠ On Windows a pip console script is a small `.exe` that starts Python and
    waits, and a venv's `python.exe` is a redirector that starts the base
    interpreter and waits. Either one outlives its own client, so watching it
    would never fire. Our `sys.argv[0]` names the console-script `.exe`, and
    `sys.executable` names the redirector only when it differs from the base
    interpreter: every real Python process in that venv runs the BASE image,
    so a Python client can never match it.
    """
    images: set[str] = set()
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0.lower().endswith(".exe") and os.path.isfile(argv0):
        images.add(_norm(argv0))
    base = getattr(sys, "_base_executable", sys.executable)
    if sys.executable and base and _norm(sys.executable) != _norm(base):
        images.add(_norm(sys.executable))
    return images


def _win_image_path(pid: int) -> Optional[str]:
    """Full executable path of a process, or None."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value or None
    finally:
        kernel32.CloseHandle(handle)


def _win_parent_pid(pid: int) -> Optional[int]:
    """The parent PID of a process, from a Toolhelp snapshot, or None."""
    import ctypes
    from ctypes import wintypes

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESSENTRY32W),
    ]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESSENTRY32W),
    ]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return None
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.th32ProcessID == pid:
                return int(entry.th32ParentProcessID)
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
        return None
    finally:
        kernel32.CloseHandle(snap)


def watched_parent() -> int:
    """The process whose death means our client is gone: the parent, past any
    launcher that only started us (Windows; elsewhere launchers exec in place).
    If a step cannot be made the launcher itself is watched, which can only
    make the server linger, never exit early."""
    pid = os.getppid()
    if sys.platform != "win32":
        return pid
    try:
        launchers = _launcher_images()
        for _ in range(4):
            image = _win_image_path(pid)
            if image is None:
                break
            if (
                _norm(image) not in launchers
                and os.path.basename(image).lower() not in _RUNNER_NAMES
            ):
                break
            up = _win_parent_pid(pid)
            if not up or up <= 1:
                break
            pid = up
    except Exception:
        logger.debug("launcher walk failed; watching the direct parent", exc_info=True)
    return pid


def start(
    on_gone: Callable[[], None], interval: Optional[float] = None
) -> Optional[threading.Thread]:
    """Watch this process's client and call ``on_gone`` once when it is gone.

    Returns the daemon thread, or None when the watch is off or the parent
    cannot be watched."""
    seconds = interval_seconds() if interval is None else interval
    if seconds <= 0:
        return None
    if ORIGIN_PPID > 1 and os.getppid() != ORIGIN_PPID:
        # Reparented between import and now: the client is already gone.
        watch: Optional[ParentWatch] = ParentWatch(ORIGIN_PPID, None, ORIGIN_PPID)
        watch.gone_at_start = True
        target = ORIGIN_PPID
    else:
        target = watched_parent()
        watch = ParentWatch.of(target)
        if watch is None:
            return None
        # The reparent check compares against our own parent, which is what
        # getppid() reports; the target may be further up.
        watch.started_ppid = os.getppid()
    logger.debug("parent watch on pid %s every %ss", target, seconds)
    stop = threading.Event()

    def _run() -> None:
        while not stop.wait(seconds):
            if watch.gone():
                try:
                    on_gone()
                except Exception:
                    logger.debug("parent-gone callback failed", exc_info=True)
                return

    t = threading.Thread(target=_run, name="jcm-parent-watch", daemon=True)
    t.start()
    return t
