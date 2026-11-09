"""Run `watch-all` from pythonw.exe without any console window, its children's included.

The Windows sign-in task starts `pythonw.exe` so no console window opens at
logon. A pythonw process has no console, and Windows gives every console
program it starts (`git.exe`, `python.exe`) a NEW console window, so a watcher
running there directly would flash a window on every re-index that runs git.
⚠⚠ Measured on Windows 11 (2026-10-30): a plain `python.exe` child of pythonw
reported a visible console window; a child started with CREATE_NO_WINDOW
reported none, and so did a plain child of THAT child, because a child shares
its parent's console. Patching each spawn site instead would mean every one of
the `subprocess` calls under `src/` keeping a flag, and the one written next
forgetting it.

So pythonw runs only this trampoline: it starts `python.exe` with the same
arguments and CREATE_NO_WINDOW, sends that process's stdout and stderr to the
log file (pythonw has no stderr of its own, so the watcher's warning lines
would otherwise go nowhere), ties it to a job object so ending the task ends
the watcher, and waits for it. The Task Scheduler state follows the pythonw
process, which lives exactly as long as the watcher.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_KILL_ON_JOB_CLOSE = 0x2000
_EXTENDED_LIMIT_INFORMATION = 9
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def console_interpreter(executable: str) -> Optional[Path]:
    """`python.exe` beside a `pythonw.exe`, or None when this is not pythonw."""
    path = Path(executable)
    if path.name.lower() != "pythonw.exe":
        return None
    console = path.with_name("python.exe")
    return console if console.is_file() else None


def child_argv(argv: list[str]) -> tuple[list[str], Optional[str]]:
    """Split `--log-file` off the arguments: the child writes its log to stderr."""
    out: list[str] = []
    log_file: Optional[str] = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--log-file" and i + 1 < len(argv):
            log_file = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--log-file="):
            log_file = arg.split("=", 1)[1]
            i += 1
            continue
        out.append(arg)
        i += 1
    return out, log_file


def _kill_with_parent(proc: subprocess.Popen) -> None:
    """Put the child in a job that is closed, and so ended, when this process ends."""
    try:
        import ctypes
        from ctypes import wintypes

        class _BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                (n, ctypes.c_uint64)
                for n in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class _ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimits),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        # Looked up by name: the type checker runs for Linux, where neither
        # `WinDLL`, `get_last_error` nor `Popen._handle` exists.
        kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        last_error = getattr(ctypes, "get_last_error")
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(last_error(), "CreateJobObjectW failed")
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise OSError(last_error(), "SetInformationJobObject failed")
        if not kernel32.AssignProcessToJobObject(
            job, wintypes.HANDLE(int(getattr(proc, "_handle")))
        ):
            raise OSError(last_error(), "AssignProcessToJobObject failed")
        # The handle is deliberately never closed: closing it ends the watcher.
        # It closes when this process exits, which is the point.
    except Exception:
        logger.warning(
            "could not tie the watcher to the task's lifetime; ending the task "
            "may leave the watcher running",
            exc_info=True,
        )


def run_with_hidden_console(argv: list[str], default_log_file: Path) -> Optional[int]:
    """Re-run `argv` under the console interpreter with no window; None if not pythonw.

    Returns the child's exit code, or None when this process is not pythonw
    (or there is no `python.exe` beside it), so the caller runs normally.
    """
    if sys.platform != "win32":
        return None
    console = console_interpreter(sys.executable)
    if console is None:
        return None
    args, log_file = child_argv(list(argv))
    log_path = Path(log_file).expanduser() if log_file else default_log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        proc = spawn_hidden([str(console), "-m", "jcodemunch_mcp", *args], log)
        return proc.wait()


def spawn_hidden(cmd: list[str], log) -> subprocess.Popen:
    """Start `cmd` with a console that has no window, output to `log`, ended with us."""
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        creationflags=_CREATE_NO_WINDOW,
    )
    _kill_with_parent(proc)
    return proc
