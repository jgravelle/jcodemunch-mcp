"""Presence registry: which jcodemunch processes share this index store.

Origin (jcm#375 follow-up). A user found **25+ live jcodemunch instances** on one
box, all against the same ~140MB index store, ages up to 1d15h, mostly stdio
servers their client had spawned and never reaped at session end. Reaping the
day-plus-old ones freed **17 GB of RAM**. Nobody knew until they went looking.

We cannot reap another program's children. What we can do is stop the sprawl
being invisible, which is the part that let it reach 25.

Each server writes one small file on startup and removes it on clean exit.
Readers filter by PID liveness AND creation-time identity (jcm#450: a recycled
PID is pruned like a dead one, not mistaken for the old server) and prune what
they find dead, so a killed process leaves no lasting trace and there is no
daemon to keep the registry honest. This deliberately reuses
``process_locks._is_live_holder`` rather than inventing a second liveness
notion.

Contains no queries and no file contents. The one path it records is the
directory the server's own package was imported from (#1435), so a reader can
tell whether a running server serves THIS install; on a source install that
directory sits inside the jcodemunch checkout. Only `install-status` reads it
back; `get_session_stats` does not return it. Written under the index store,
disclosed in SECURITY.md alongside the lock files and the savings meter.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .process_locks import _client_id, _is_live_holder, _process_create_time

logger = logging.getLogger(__name__)

_DIR_NAME = "_processes"
# Quiet for a normal one-or-two-client setup; loud once sprawl is real.
_SPRAWL_HINT_THRESHOLD = 5
_registered_path: Optional[Path] = None


def _registry_dir(storage_path: Optional[str]) -> Path:
    base = storage_path or os.environ.get("CODE_INDEX_PATH") or "~/.code-index"
    return Path(os.path.expanduser(base)) / _DIR_NAME


@dataclass
class ProcessEntry:
    pid: int
    client_id: str
    transport: str
    version: str
    started_at: str
    # OS creation time of the registered process (identity anchor against PID
    # reuse, jcm#450). None for rows written by pre-fix versions or platforms
    # without a creation-time source.
    create_time: Optional[float] = None
    # Directory the server imported `jcodemunch_mcp` from (#1435). None for rows
    # written before it was recorded; a reader treats that as UNKNOWN.
    package_dir: Optional[str] = None

    def age_seconds(self) -> Optional[float]:
        try:
            started = datetime.fromisoformat(self.started_at)
        except (TypeError, ValueError):
            return None
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - started).total_seconds())

    def code_stale(self, source_changed_at: Optional[float]) -> Optional[bool]:
        """Did the package source change AFTER this process started?

        ⚠⚠ This is the question `version` cannot answer, and the reason it must
        exist. `version` is the RECORDED metadata number, frozen in
        `.dist-info` at install time -- on an editable install every process
        reports the same string no matter when it started, so the one thing an
        operator wants from a process registry ("is this old server running old
        code?") was unanswerable from the row. A start timestamp can answer it;
        a version string never could.

        ⚠ Tri-state. `None` means could not establish -- a copied install (the
        tree's mtimes say nothing about what a copy loaded), an unparseable
        `started_at`, or an unreadable tree. Never `False` for an unasked
        question.
        """
        if source_changed_at is None:
            return None
        try:
            started = datetime.fromisoformat(self.started_at)
        except (TypeError, ValueError):
            return None
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return source_changed_at > started.timestamp()

    def as_dict(
        self, source_changed_at: Optional[float] = None, *, include_package_dir: bool = False
    ) -> dict:
        out = {
            "pid": self.pid,
            "client_id": self.client_id,
            # ⚠ The RECORDED metadata version, not evidence of which code is
            # loaded. Read `code_stale` for that.
            "version": self.version,
            "transport": self.transport,
            "started_at": self.started_at,
        }
        stale = self.code_stale(source_changed_at)
        if stale is not None:
            out["code_stale"] = stale
        age = self.age_seconds()
        if age is not None:
            out["age_seconds"] = round(age, 1)
        if self.create_time is not None:
            out["create_time"] = self.create_time
        # ⚠ Off by default: `sprawl_report` reaches the model through
        # `get_session_stats`, and an install path is not that response's to
        # carry. `install-status`, a local CLI, asks for it.
        if include_package_dir and self.package_dir is not None:
            out["package_dir"] = self.package_dir
        return out


def register(transport: str, version: str, storage_path: Optional[str] = None) -> Optional[Path]:
    """Record this process. Best effort: a failure here must never block startup."""
    global _registered_path
    try:
        directory = _registry_dir(storage_path)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{os.getpid()}.json"
        payload = {
            "pid": os.getpid(),
            "client_id": _client_id(),
            "transport": transport,
            "version": version,
            "started_at": datetime.now(timezone.utc).isoformat(),
            # Identity anchor against PID reuse (jcm#450).
            "create_time": _process_create_time(os.getpid()),
            # Which install this process serves (#1435).
            "package_dir": _package_dir(),
        }
        tmp = path.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)
        _registered_path = path
        return path
    except Exception:
        logger.debug("process registry: register failed", exc_info=True)
        return None


def unregister() -> None:
    """Remove this process's entry. Best effort; a hard kill skips this entirely,
    which is exactly why readers verify PID liveness instead of trusting the file."""
    global _registered_path
    if _registered_path is None:
        return
    try:
        _registered_path.unlink(missing_ok=True)
    except Exception:
        logger.debug("process registry: unregister failed", exc_info=True)
    finally:
        _registered_path = None


def live_processes(storage_path: Optional[str] = None, prune: bool = True) -> list[ProcessEntry]:
    """Return entries whose recorded process is still alive, pruning the rest.

    "Alive" means PID alive AND, when the row recorded a ``create_time``,
    creation-time identity matches — a recycled PID (jcm#450) is pruned like a
    dead one instead of impersonating the long-gone server forever.
    """
    directory = _registry_dir(storage_path)
    if not directory.is_dir():
        return []

    entries: list[ProcessEntry] = []
    try:
        candidates = list(directory.glob("*.json"))
    except OSError:
        return []

    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Unreadable or half-written: treat as dead and clean it up.
            if prune:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            continue

        pid = data.get("pid")
        if not isinstance(pid, int) or not _is_live_holder(
            pid, data.get("create_time"), data.get("started_at"),
        ):
            if prune:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            continue

        raw_ct = data.get("create_time")
        raw_dir = data.get("package_dir")
        entries.append(
            ProcessEntry(
                pid=pid,
                client_id=str(data.get("client_id") or "unknown"),
                transport=str(data.get("transport") or "unknown"),
                version=str(data.get("version") or "unknown"),
                started_at=str(data.get("started_at") or ""),
                create_time=float(raw_ct) if isinstance(raw_ct, (int, float)) else None,
                package_dir=raw_dir if isinstance(raw_dir, str) and raw_dir else None,
            )
        )

    entries.sort(key=lambda e: e.started_at)
    return entries


def _package_dir() -> Optional[str]:
    """Directory this process imported `jcodemunch_mcp` from, or None."""
    try:
        import jcodemunch_mcp

        return str(Path(jcodemunch_mcp.__file__).parent)
    except Exception:
        logger.debug("process registry: package dir unreadable", exc_info=True)
        return None


def _installed_version() -> str:
    """The version installed for THIS process's package (read at import)."""
    try:
        from .. import __version__

        return str(__version__)
    except Exception:
        return "unknown"


def _same_dir(a: str, b: str) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, ValueError):
        return False


def restart_verdict(
    entry: ProcessEntry,
    *,
    installed_version: str,
    package_dir: Optional[str],
    source_layout: Optional[bool],
    source_changed_at: Optional[float],
) -> dict:
    """Does this running server serve older code than is installed now? (#1435)

    Tri-state `restart_required`; `None` means could not establish and is never
    read as current. A REPORT only: restarting belongs to the client or the
    service manager, so nothing here signals or stops a process.

    ⚠ Two bases, picked by install layout. On a SOURCE install every process
    reports the same metadata version, so the version cannot answer; a source
    mtime later than the server's start can (`code_stale`). On a COPIED install
    (pip, uv tool, pipx) the recorded version IS the loaded code, so a version
    that differs from the installed one is a code gap.
    ⚠⚠ The layout is passed in, never inferred from `source_changed_at`: that
    is None on a source install whose mtime walk failed too, and falling through
    to the version check there compares the one metadata string every process
    shares and reports current for a comparison that was never made.
    ⚠ The comparison holds only for a server serving THIS install. A server
    imported from another directory (another venv, a uvx cache) answers about
    an install this process did not read, so it is UNKNOWN, never current.
    """
    if not entry.package_dir or not package_dir:
        return {
            "restart_required": None,
            "basis": None,
            "reason": "no install record (started by an older release, or the "
            "path was unreadable)",
        }
    if not _same_dir(entry.package_dir, package_dir):
        return {
            "restart_required": None,
            "basis": None,
            "reason": f"serves a different install ({entry.package_dir})",
        }
    if source_layout is None:
        return {
            "restart_required": None,
            "basis": None,
            "reason": "install layout could not be established",
        }
    if source_layout:
        if source_changed_at is None:
            return {
                "restart_required": None,
                "basis": "source_mtime",
                "reason": "source install, but the source mtime was unreadable",
            }
        stale = entry.code_stale(source_changed_at)
        if stale is None:
            return {
                "restart_required": None,
                "basis": "source_mtime",
                "reason": "start time unreadable",
            }
        return {
            "restart_required": stale,
            "basis": "source_mtime",
            "reason": "source changed after this server started"
            if stale
            else "started after the last source change",
        }
    if not installed_version or installed_version == "unknown" or entry.version == "unknown":
        return {
            "restart_required": None,
            "basis": "version",
            "reason": "a version is unknown",
        }
    if entry.version != installed_version:
        return {
            "restart_required": True,
            "basis": "version",
            "reason": f"serving {entry.version}; installed is {installed_version}",
        }
    return {
        "restart_required": False,
        "basis": "version",
        "reason": f"serving the installed version {installed_version}",
    }


def serving_report(storage_path: Optional[str] = None) -> dict:
    """Running servers on this index store, each with a restart verdict (#1435).

    For `install-status`. Sees only servers registered under this store's
    `_processes` directory, i.e. on this machine and this CODE_INDEX_PATH; a
    server elsewhere is not listed, and the `scope` field says so.
    """
    from ..install_layout import is_source_layout, running_source_changed_at

    installed = _installed_version()
    here = _package_dir()
    layout: Optional[bool] = None
    if here:
        try:
            layout = is_source_layout(Path(here) / "__init__.py")
        except Exception:
            logger.debug("process registry: layout unreadable", exc_info=True)
            layout = None
    changed_at = running_source_changed_at()
    me = os.getpid()
    servers = []
    for e in live_processes(storage_path):
        if e.pid == me:
            continue
        row = e.as_dict(changed_at, include_package_dir=True)
        row.update(
            restart_verdict(
                e,
                installed_version=installed,
                package_dir=here,
                source_layout=layout,
                source_changed_at=changed_at,
            )
        )
        servers.append(row)
    verdicts = [s["restart_required"] for s in servers]
    return {
        "installed_version": installed,
        "package_dir": here,
        "scope": "servers registered under this index store on this machine",
        "servers": servers,
        "restart_required": sum(1 for v in verdicts if v is True),
        "current": sum(1 for v in verdicts if v is False),
        "unknown": sum(1 for v in verdicts if v is None),
    }


def sprawl_report(storage_path: Optional[str] = None, max_listed: int = 10) -> dict:
    """Summary for `get_session_stats`.

    ``others`` is the number the operator actually cares about: how many OTHER
    jcodemunch processes are sharing this store right now. A hint is attached
    only past a threshold, so a normal one-or-two-client setup stays quiet.
    """
    entries = live_processes(storage_path)
    me = os.getpid()
    others = [e for e in entries if e.pid != me]

    ages = [a for a in (e.age_seconds() for e in entries) if a is not None]
    report: dict = {
        "live": len(entries),
        "others": len(others),
        "this_pid": me,
    }
    if ages:
        report["oldest_age_seconds"] = round(max(ages), 1)
    if others:
        # ⚠ Gated on having something to judge: the walk is ~13 ms over 274
        # files, cheap but not free, and a lone process has no peer to compare.
        from ..install_layout import running_source_changed_at

        changed_at = running_source_changed_at()
        report["processes"] = [e.as_dict(changed_at) for e in others[:max_listed]]
        if len(others) > max_listed:
            report["processes_truncated"] = len(others) - max_listed
        if changed_at is not None:
            report["source_changed_at"] = datetime.fromtimestamp(
                changed_at, timezone.utc
            ).isoformat()
            behind = [e for e in others if e.code_stale(changed_at)]
            if behind:
                report["processes_running_stale_code"] = len(behind)
                report["hint_stale_code"] = (
                    f"{len(behind)} of {len(others)} other jcodemunch processes "
                    f"started before the package source last changed, so they are "
                    f"serving older code. Their reported `version` cannot show "
                    f"this -- it is the metadata number, identical across all of "
                    f"them on a source install. Restart those MCP clients."
                )

    # Each live process can hold its own hydrated index cache, so sprawl is a
    # memory story, not just a process-count story. Name the lever rather than
    # only the number.
    if len(entries) >= _SPRAWL_HINT_THRESHOLD:
        report["hint"] = (
            f"{len(entries)} jcodemunch processes share this index store. Each can "
            f"hold its own in-memory index cache. If your client does not reap "
            f"stdio servers at session end, they accumulate. Set "
            f"JCODEMUNCH_INDEX_CACHE_TTL to release idle cached indexes, and "
            f"consider reaping old server processes."
        )
    return report

