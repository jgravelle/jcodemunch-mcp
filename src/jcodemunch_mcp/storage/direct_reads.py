"""Daily counts of Cursor agent reads of indexed code, beside jCodeMunch lookups.

Written by ``hook-cursor`` and read by ``get_session_stats`` and the
``direct-reads`` command: one JSON file under the index store holding, per day,
``direct_reads`` (a read of a code file under an indexed source root) and
``lookups`` (a finished call to the MCP server named ``jcodemunch``), plus the
last ``cursor_version`` a payload carried. No path, query or file text.

⚠ A FLOOR: a read the hook never saw (the Cursor CLI, a cloud agent, a hook
that timed out) is not counted.
⚠ A count whose lock wait runs out is NOT dropped: it is written to a
pending file of its own (``<file>.<uuid>.pending``, created whole by a rename),
shown by ``summary`` at once, and folded into the counts by the next writer
that takes the lock. A pending file is folded in only after it is removed, so
a count is never added twice; if the write after a fold fails, every count it
held goes back to a pending file. ``summary`` reads the counts file BEFORE it
looks for pending files, so a fold between the two can only under-count.
⚠ An unreadable file is UNKNOWN, never zero: ``summary`` says it could not
read the counts, and a write never replaces a file it could not read, because
that would turn one transient read error into lost history.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from .tool_counters import acquire_lock

logger = logging.getLogger(__name__)

FILE_NAME = "_direct_reads.json"
SCHEMA = 1
COUNTERS = ("direct_reads", "lookups")
_LOCK_WAIT_SECONDS = 1.0
PENDING_SUFFIX = ".pending"


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


def _pending_paths(path: Path) -> list:
    return sorted(path.parent.glob(f"{path.name}.*{PENDING_SUFFIX}"))


def _read_pending(p: Path) -> Optional[tuple]:
    """(day, counter, cursor_version or None), or None for a file that is not a count."""
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    day, counter, version = rec.get("day"), rec.get("counter"), rec.get("cursor_version")
    if not isinstance(day, str) or counter not in COUNTERS:
        return None
    return day, counter, version if isinstance(version, str) and version else None


def _write_pending(path: Path, counter: str, cursor_version, day: str) -> bool:
    """Keep one count the lock could not be taken for. False when it could not be written."""
    # ⚠ A uuid, never pid + clock: two counts from one process inside the
    # clock's resolution would share a name and the rename would drop one.
    name = f"{path.name}.{uuid.uuid4().hex}"
    tmp = path.with_name(name + ".ptmp")
    rec = {"day": day, "counter": counter}
    if isinstance(cursor_version, str) and cursor_version:
        rec["cursor_version"] = cursor_version
    try:
        tmp.write_text(json.dumps(rec, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path.with_name(name + PENDING_SUFFIX))
        return True
    except OSError:
        logger.debug("direct-read pending count not written", exc_info=True)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _add(data: dict, day: str, counter: str) -> None:
    counts = data["days"].setdefault(day, {})
    counts[counter] = int(counts.get(counter, 0) or 0) + 1


def _record(
    counter: str, cursor_version, storage_path: Optional[str], today: Optional[str]
) -> bool:
    path = tally_path(storage_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    # ⚠ The per-call stores' one lock rule. On Windows, creating a lock its
    # holder is deleting this instant raises PermissionError, not
    # FileExistsError; a hand-rolled copy that caught only the latter lost a
    # count to parallel writers (9 of 10, Windows CI).
    day = today or _today()
    if not acquire_lock(lock, _LOCK_WAIT_SECONDS):
        # A slow box with many reads at once runs the wait out; the count
        # is kept in a pending file rather than dropped.
        return _write_pending(path, counter, cursor_version, day)
    try:
        data = _load(path)
        if data is None:
            return False
        folded: list = []
        for pending in _pending_paths(path):
            rec = _read_pending(pending)
            if rec is None:
                continue
            # ⚠ Removed BEFORE it is added: one that cannot be removed stays
            # pending and is counted there, so it is never counted twice.
            try:
                pending.unlink()
            except OSError:
                logger.debug("direct-read pending count left", exc_info=True)
                continue
            folded.append(rec)
            _add(data, rec[0], rec[1])
            if rec[2] and not data.get("cursor_version"):
                data["cursor_version"] = rec[2]
        _add(data, day, counter)
        if isinstance(cursor_version, str) and cursor_version:
            data["cursor_version"] = cursor_version
        # A uuid, so two holders after a stale-lock break never share a temp
        # file and publish each other's counts.
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            # ⚠ The folded files are already removed. On Windows a reader
            # holding the counts file open makes the replace fail, so every
            # count this write carried goes back to a pending file.
            logger.debug("direct-read counts not written; kept as pending", exc_info=True)
            try:
                tmp.unlink()
            except OSError:
                pass
            kept = [_write_pending(path, c, v, d) for d, c, v in folded]
            return _write_pending(path, counter, cursor_version, day) and all(kept)
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
    # ⚠ The counts file FIRST, the pending files after. A fold removes a
    # pending file before it replaces the counts, so in this order a fold
    # between the two reads can only under-count; the other order counted
    # one record twice.
    data = _load(path)
    pending = [r for r in map(_read_pending, _pending_paths(path)) if r]
    if not pending and not path.exists():
        return None
    out: dict = {"coverage": "cursor_ide", "floor": True}
    if data is None:
        out["unreadable"] = f"{path.name} could not be read, so no count is shown"
        return out
    for p_day, p_counter, p_version in pending:
        _add(data, p_day, p_counter)
        if p_version and not data.get("cursor_version"):
            data["cursor_version"] = p_version
    day = today or _today()
    week = (date.fromisoformat(day) - timedelta(days=6)).isoformat()
    out["today"] = _window(data["days"], day, day)
    out["last_7_days"] = _window(data["days"], week, day)
    out["all_time"] = _window(data["days"], None, day)
    if data.get("cursor_version"):
        out["cursor_version"] = data["cursor_version"]
    return out
