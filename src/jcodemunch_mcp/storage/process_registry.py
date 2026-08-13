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

Contains no repo paths, no queries, and no file contents. Written under the
index store, disclosed in the README's background-behavior section alongside the
lock files and the savings meter.
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

    def age_seconds(self) -> Optional[float]:
        try:
            started = datetime.fromisoformat(self.started_at)
        except (TypeError, ValueError):
            return None
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - started).total_seconds())

    def as_dict(self) -> dict:
        out = {
            "pid": self.pid,
            "client_id": self.client_id,
            "transport": self.transport,
            "version": self.version,
            "started_at": self.started_at,
        }
        age = self.age_seconds()
        if age is not None:
            out["age_seconds"] = round(age, 1)
        if self.create_time is not None:
            out["create_time"] = self.create_time
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
        if not isinstance(pid, int) or not _is_live_holder(pid, data.get("create_time")):
            if prune:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            continue

        raw_ct = data.get("create_time")
        entries.append(
            ProcessEntry(
                pid=pid,
                client_id=str(data.get("client_id") or "unknown"),
                transport=str(data.get("transport") or "unknown"),
                version=str(data.get("version") or "unknown"),
                started_at=str(data.get("started_at") or ""),
                create_time=float(raw_ct) if isinstance(raw_ct, (int, float)) else None,
            )
        )

    entries.sort(key=lambda e: e.started_at)
    return entries


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
        report["processes"] = [e.as_dict() for e in others[:max_listed]]
        if len(others) > max_listed:
            report["processes_truncated"] = len(others) - max_listed

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

