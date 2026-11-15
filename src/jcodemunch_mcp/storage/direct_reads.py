"""How often Cursor's agent read indexed code itself, beside jCodeMunch lookups.

Written by ``hook-cursor`` (Cursor's ``beforeReadFile`` and
``afterMCPExecution`` hooks) and read by ``get_session_stats`` and the
``direct-reads`` command. One JSON file under the index store, counts per day:

* ``direct_reads``: a read of a code file under an indexed source root.
* ``lookups``: a finished call to the MCP server named ``jcodemunch``.
* ``reads_after_lookup``: a direct read of a file that an earlier jcodemunch
  answer in the same conversation named. Counted inside ``direct_reads`` as
  well, and reported beside it, never added to it.

⚠ The file holds counts and short path hashes, never a path, a query or file
text. Nothing here sends anything. ⚠ The figure is a FLOOR: a read the hook
never saw (a crash, a timeout, the Cursor CLI, a cloud agent, a lock it could
not take) is not counted, and the report says so. No token or savings figure
is computed here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

FILE_NAME = "_direct_reads.json"
SCHEMA = 1
COUNTERS = ("direct_reads", "lookups", "reads_after_lookup")
_CONVERSATION_KEEP_DAYS = 7
_KEYS_PER_CONVERSATION = 2000
_LOCK_WAIT_SECONDS = 1.0
_LOCK_STALE_SECONDS = 10.0


def _today() -> str:
    return date.today().isoformat()


def tally_path(storage_path: Optional[str] = None) -> Path:
    base = storage_path or os.environ.get("CODE_INDEX_PATH") or "~/.code-index"
    return Path(os.path.expanduser(base)) / FILE_NAME


def path_key(relative_path: str) -> str:
    """A short hash of a repo-relative path, the form both hooks agree on."""
    norm = relative_path.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    norm = os.path.normcase(norm.lstrip("/")).replace("\\", "/")
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _named_paths(value, out: set[str], depth: int = 0) -> None:
    """Collect the strings a jcodemunch answer gives under a file or path key."""
    if depth > 12:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if (
                isinstance(v, str)
                and isinstance(k, str)
                and (k in ("file", "path", "file_path") or k.endswith("_file"))
            ):
                out.add(v)
            elif isinstance(v, str) and k == "text":
                try:
                    _named_paths(json.loads(v), out, depth + 1)
                except (ValueError, TypeError):
                    pass
            else:
                _named_paths(v, out, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _named_paths(v, out, depth + 1)


def keys_named_in(result_json) -> set[str]:
    """Path keys for every file a jcodemunch result names (empty on no JSON)."""
    try:
        parsed = (
            json.loads(result_json) if isinstance(result_json, str) else result_json
        )
    except (ValueError, TypeError):
        return set()
    found: set[str] = set()
    _named_paths(parsed, found)
    return {path_key(p) for p in found if p and not os.path.isabs(p)}


class _Lock:
    def __init__(self, target: Path):
        self.path = target.with_name(target.name + ".lock")
        self.held = False

    def __enter__(self) -> "_Lock":
        deadline = time.monotonic() + _LOCK_WAIT_SECONDS
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > _LOCK_STALE_SECONDS:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    return self
                time.sleep(0.02)

    def __exit__(self, *exc) -> None:
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                logger.debug("direct-read lock release failed", exc_info=True)


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": SCHEMA, "days": {}, "conversations": {}}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {"schema": SCHEMA, "days": {}, "conversations": {}}
    data.setdefault("days", {})
    data.setdefault("conversations", {})
    return data


def _update(
    storage_path: Optional[str], today: str, conversation_id, cursor_version, change
) -> bool:
    path = tally_path(storage_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _Lock(path) as lock:
        if not lock.held:
            return False
        data = _load(path)
        day = data["days"].setdefault(today, {c: 0 for c in COUNTERS})
        conv = None
        if isinstance(conversation_id, str) and conversation_id:
            ckey = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:16]
            conv = data["conversations"].setdefault(ckey, {"day": today, "keys": []})
            conv["day"] = today
        change(day, conv)
        if isinstance(cursor_version, str) and cursor_version:
            data["cursor_version"] = cursor_version
        cutoff = (
            date.fromisoformat(today) - timedelta(days=_CONVERSATION_KEEP_DAYS)
        ).isoformat()
        data["conversations"] = {
            k: v
            for k, v in data["conversations"].items()
            if isinstance(v, dict) and str(v.get("day", "")) >= cutoff
        }
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
        return True


def record_read(
    *,
    conversation_id,
    key: Optional[str],
    cursor_version,
    storage_path: Optional[str] = None,
    today: Optional[str] = None,
) -> bool:
    def change(day, conv):
        day["direct_reads"] = day.get("direct_reads", 0) + 1
        if key and conv is not None and key in conv.get("keys", []):
            day["reads_after_lookup"] = day.get("reads_after_lookup", 0) + 1

    return _update(
        storage_path, today or _today(), conversation_id, cursor_version, change
    )


def record_lookup(
    *,
    conversation_id,
    keys: Iterable[str],
    cursor_version,
    storage_path: Optional[str] = None,
    today: Optional[str] = None,
) -> bool:
    keys = list(keys)

    def change(day, conv):
        day["lookups"] = day.get("lookups", 0) + 1
        if conv is not None and keys:
            seen = conv.setdefault("keys", [])
            for k in keys:
                if k not in seen:
                    seen.append(k)
            del seen[:-_KEYS_PER_CONVERSATION]

    return _update(
        storage_path, today or _today(), conversation_id, cursor_version, change
    )


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
    """Counts for today, the last seven days and all time; None with no tally."""
    path = tally_path(storage_path)
    if not path.exists():
        return None
    data = _load(path)
    day = today or _today()
    week = (date.fromisoformat(day) - timedelta(days=6)).isoformat()
    out = {
        "today": _window(data["days"], day, day),
        "last_7_days": _window(data["days"], week, day),
        "all_time": _window(data["days"], None, day),
        "coverage": "cursor_ide",
        "floor": True,
    }
    if data.get("cursor_version"):
        out["cursor_version"] = data["cursor_version"]
    return out
