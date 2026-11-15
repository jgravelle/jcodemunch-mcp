"""Daily counts of Cursor agent reads of indexed code, beside jCodeMunch lookups.

Written by ``hook-cursor`` and read by ``get_session_stats`` and the
``direct-reads`` command: one JSON file under the index store holding, per day,
``direct_reads`` (a read of a code file under an indexed source root) and
``lookups`` (a finished call to the MCP server named ``jcodemunch``), plus the
last ``cursor_version`` a payload carried. No path, query or file text.

⚠ A FLOOR: a read the hook never saw (the Cursor CLI, a cloud agent, a hook
that timed out, a lock it could not take in time) is not counted.
⚠ An unreadable file is UNKNOWN, never zero: ``summary`` says it could not
read the counts, and a write never replaces a file it could not read, because
that would turn one transient read error into lost history.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

FILE_NAME = "_direct_reads.json"
SCHEMA = 1
COUNTERS = ("direct_reads", "lookups")
_LOCK_WAIT_SECONDS = 1.0
_LOCK_STALE_SECONDS = 10.0


def _today() -> str:
    return date.today().isoformat()


def tally_path(storage_path: Optional[str] = None) -> Path:
    base = storage_path or os.environ.get("CODE_INDEX_PATH") or "~/.code-index"
    return Path(os.path.expanduser(base)) / FILE_NAME


def _load(path: Path) -> Optional[dict]:
    """The tally, a fresh one when the file is absent, None when unreadable."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"schema": SCHEMA, "days": {}}
    except OSError:
        logger.debug("direct-read counts unreadable", exc_info=True)
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if (
        not isinstance(data, dict)
        or data.get("schema") != SCHEMA
        or not isinstance(data.get("days"), dict)
    ):
        return None
    for counts in data["days"].values():
        if not isinstance(counts, dict) or not all(
            isinstance(counts.get(c, 0), int) and not isinstance(counts.get(c, 0), bool)
            for c in COUNTERS
        ):
            return None
    return data


def _record(
    counter: str, cursor_version, storage_path: Optional[str], today: Optional[str]
) -> bool:
    path = tally_path(storage_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + _LOCK_WAIT_SECONDS
    while True:
        try:
            os.close(os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > _LOCK_STALE_SECONDS:
                    lock.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)
    try:
        data = _load(path)
        if data is None:
            return False
        day = data["days"].setdefault(today or _today(), {})
        day[counter] = int(day.get(counter, 0) or 0) + 1
        if isinstance(cursor_version, str) and cursor_version:
            data["cursor_version"] = cursor_version
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
        return True
    finally:
        try:
            lock.unlink()
        except OSError:
            logger.debug("direct-read lock release failed", exc_info=True)


def record_read(
    *,
    cursor_version=None,
    storage_path: Optional[str] = None,
    today: Optional[str] = None,
) -> bool:
    return _record("direct_reads", cursor_version, storage_path, today)


def record_lookup(
    *,
    cursor_version=None,
    storage_path: Optional[str] = None,
    today: Optional[str] = None,
) -> bool:
    return _record("lookups", cursor_version, storage_path, today)


def _window(days: dict, first: Optional[str], last: str) -> dict:
    total = {c: 0 for c in COUNTERS}
    for d, counts in days.items():
        if (first is None or d >= first) and d <= last and isinstance(counts, dict):
            for c in COUNTERS:
                total[c] += int(counts.get(c, 0) or 0)
    return total


def summary(
    storage_path: Optional[str] = None, today: Optional[str] = None
) -> Optional[dict]:
    """Today, the last seven days and all time; None when nothing was counted."""
    path = tally_path(storage_path)
    if not path.exists():
        return None
    out: dict = {"coverage": "cursor_ide", "floor": True}
    data = _load(path)
    if data is None:
        out["unreadable"] = f"{path.name} could not be read, so no count is shown"
        return out
    day = today or _today()
    week = (date.fromisoformat(day) - timedelta(days=6)).isoformat()
    out["today"] = _window(data["days"], day, day)
    out["last_7_days"] = _window(data["days"], week, day)
    out["all_time"] = _window(data["days"], None, day)
    if data.get("cursor_version"):
        out["cursor_version"] = data["cursor_version"]
    return out
