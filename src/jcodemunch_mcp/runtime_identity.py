"""Runtime identity resource — munch.runtime.identity/v1 (issue #371).

One consistent read-only MCP resource (``munch://runtime/identity``) so
multi-agent harnesses can distinguish command-line-identical server
processes, detect restarts, and refuse cleanup on identity mismatch.

Contract notes:
- ``process_start`` is OS-derived process-creation identity when obtainable
  (Windows GetProcessTimes; Linux /proc starttime + btime; macOS libproc,
  read through ``storage.process_locks``). When it is not,
  the fallback is the module's own first-read wall clock, DISCLOSED via
  ``source: "self_recorded"`` — never fabricated as OS evidence.
- ``instance_id`` is a uuid4 minted once per process lifetime (lazily).
- ``launch_id`` is an opaque host-supplied echo from the environment;
  omitted when unset.
- ``build`` (#1435) tells apart two servers that report the same version but
  were installed from different files: ``sha256:`` plus 16 hex digits over the
  source-file rows of the installed distribution's ``RECORD`` (launcher,
  installer and ``.pyc`` rows vary per install and are left out). Computed
  once, at server startup, from one local file; no index, no network. ``null`` with ``build_reason`` when no RECORD describes the loaded
  code (a source or editable install, or a process that imported a different
  copy than the installed distribution). It describes the files as they were
  installed: a file edited in place afterwards does not change it.
- Deliberately OUT of the payload: command lines, env, cwd, hostnames,
  repo paths, task data.
"""

import hashlib
import json
import logging
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

IDENTITY_URI = "munch://runtime/identity"
IDENTITY_SCHEMA = "munch.runtime.identity/v1"
PRODUCT = "jcodemunch-mcp"

# Product-specific env var first, suite-generic fallback second.
LAUNCH_ID_ENV_VARS = ("JCODEMUNCH_LAUNCH_ID", "MUNCH_LAUNCH_ID")

_lock = threading.Lock()
_instance_id: Optional[str] = None
_process_start: Optional[Dict[str, str]] = None
_build: Optional[Dict[str, Optional[str]]] = None
_transport: str = "stdio"


def set_transport(transport: str) -> None:
    """Record the serve transport (called once at server startup)."""
    global _transport
    if transport:
        _transport = str(transport)


def get_instance_id() -> str:
    """uuid4 minted once per process lifetime."""
    global _instance_id
    if _instance_id is None:
        with _lock:
            if _instance_id is None:
                _instance_id = str(uuid.uuid4())
    return _instance_id


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _windows_process_start() -> Optional[str]:
    """Process creation time via GetProcessTimes (FILETIME, 100ns since 1601)."""
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
    ]
    handle = kernel32.GetCurrentProcess()
    creation, exit_t, kernel_t, user_t = FILETIME(), FILETIME(), FILETIME(), FILETIME()
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
    if ticks <= 0:
        return None
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    return _iso_utc(epoch + timedelta(microseconds=ticks // 10))


def _linux_process_start() -> Optional[str]:
    """starttime (field 22 of /proc/self/stat, after the comm field) + btime."""
    with open("/proc/self/stat", "r", encoding="ascii", errors="replace") as f:
        stat = f.read()
    # comm can contain spaces/parens; fields resume after the LAST ')'.
    rest = stat.rsplit(")", 1)[1].split()
    # rest[0] is field 3 (state); starttime is field 22 → rest[19].
    start_ticks = int(rest[19])
    btime = None
    with open("/proc/stat", "r", encoding="ascii", errors="replace") as f:
        for line in f:
            if line.startswith("btime "):
                btime = int(line.split()[1])
                break
    if btime is None:
        return None
    hertz = os.sysconf("SC_CLK_TCK")
    if hertz <= 0:
        return None
    epoch_s = btime + (start_ticks / hertz)
    return _iso_utc(datetime.fromtimestamp(epoch_s, tz=timezone.utc))


def _darwin_process_start() -> Optional[str]:
    """macOS start via libproc, read through the lock layer's one reader."""
    # Imported here: process_locks pulls in the SQLite store, which this
    # module has no other reason to load.
    from .storage.process_locks import _darwin_process_start as _libproc_start

    epoch_s = _libproc_start(os.getpid())
    if epoch_s is None:
        return None
    return _iso_utc(datetime.fromtimestamp(epoch_s, tz=timezone.utc))


def _probe_process_start() -> Dict[str, str]:
    """OS-derived process start when obtainable; else disclosed self-recorded."""
    try:
        if os.name == "nt":
            value = _windows_process_start()
        elif os.path.exists("/proc/self/stat"):
            value = _linux_process_start()
        elif sys.platform == "darwin":
            value = _darwin_process_start()
        else:
            value = None
    except Exception:
        logger.debug("OS process-start probe failed", exc_info=True)
        value = None
    if value is not None:
        return {"value": value, "source": "os"}
    # Disclosed fallback: our own wall clock at first read — an honest
    # self-report, never presented as OS evidence.
    return {
        "value": _iso_utc(datetime.fromtimestamp(time.time(), tz=timezone.utc)),
        "source": "self_recorded",
    }


def get_process_start() -> Dict[str, str]:
    """Stable per-process {value, source} block (probed once, cached)."""
    global _process_start
    if _process_start is None:
        with _lock:
            if _process_start is None:
                _process_start = _probe_process_start()
    return dict(_process_start)


def _launch_id() -> Optional[str]:
    for var in LAUNCH_ID_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return value
    return None


def _version() -> str:
    try:
        from . import __version__

        return __version__
    except Exception:
        return "unknown"


def _code_rows(record: str) -> list[str]:
    """The RECORD rows that describe the package's own code, sorted.

    A RECORD also lists rows that depend on the install, not the build: the
    launcher scripts (``../../Scripts/x.exe``, which embed the interpreter path),
    ``INSTALLER`` (pip or uv), ``REQUESTED``, ``direct_url.json`` and compiled
    ``.pyc`` files (one per Python version). Hashing those would give one wheel
    a different build on every machine, so only hashed source rows count.
    """
    rows = []
    for line in record.splitlines():
        parts = line.rsplit(",", 2)
        if len(parts) != 3 or not parts[1]:
            continue
        path = parts[0].strip('"').replace("\\", "/")
        if (
            path.startswith("../")
            or ".dist-info/" in path
            or ".data/" in path
            or "__pycache__/" in path
            or path.endswith(".pyc")
        ):
            continue
        rows.append(f"{path},{parts[1]}")
    return sorted(rows)


def _probe_build(module_file: Optional[Path] = None, dist: Any = None) -> Dict[str, Optional[str]]:
    """``{"build": "sha256:<16 hex>"}``, or ``{"build": None, "build_reason": ...}``."""
    module_file = Path(module_file or Path(__file__).parent / "__init__.py")
    try:
        from .install_layout import is_source_layout

        if is_source_layout(module_file):
            return {"build": None, "build_reason": "source install: no RECORD describes the loaded code"}
        if dist is None:
            from importlib.metadata import distribution

            dist = distribution(PRODUCT)
        installed = Path(str(dist.locate_file("jcodemunch_mcp/__init__.py")))
        if os.path.normcase(os.path.realpath(installed)) != os.path.normcase(os.path.realpath(module_file)):
            return {"build": None, "build_reason": "this process loaded a different copy than the installed distribution"}
        record = dist.read_text("RECORD")
        if not record:
            return {"build": None, "build_reason": "the installed distribution has no RECORD"}
        rows = _code_rows(record)
        if not any(r.startswith("jcodemunch_mcp/__init__.py,") for r in rows):
            return {"build": None, "build_reason": "the RECORD does not list the package files (an editable install)"}
        return {"build": "sha256:" + hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()[:16]}
    except Exception as e:
        logger.debug("build digest unavailable", exc_info=True)
        return {"build": None, "build_reason": f"could not read the installed RECORD ({type(e).__name__})"}


def get_build() -> Dict[str, Optional[str]]:
    """This process's build block, computed once and cached.

    The server calls this at startup (server.py, before the presence
    registry), so the RECORD read is the one that described the loaded code.
    """
    global _build
    if _build is None:
        with _lock:
            if _build is None:
                _build = _probe_build()
    return dict(_build)


def identity_payload() -> Dict[str, Any]:
    """The munch.runtime.identity/v1 document as a dict."""
    payload: Dict[str, Any] = {
        "schema": IDENTITY_SCHEMA,
        "product": PRODUCT,
        "version": _version(),
        "transport": _transport,
        "pid": os.getpid(),
        "process_start": get_process_start(),
        "instance_id": get_instance_id(),
        **get_build(),
    }
    launch_id = _launch_id()
    if launch_id is not None:
        payload["launch_id"] = launch_id
    return payload


def identity_json() -> str:
    return json.dumps(identity_payload(), separators=(",", ":"))
