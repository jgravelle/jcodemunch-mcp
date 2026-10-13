"""Two local per-tool counters: calls, and tokens returned.

Kept in `<storage>/_tool_counters.json`. Read by `jcodemunch-mcp diagnose`;
sent nowhere. The savings file records what each tool SAVED; nothing recorded
what each tool RETURNED or how often it was called, so "what did this do for
me" had half an answer.

⚠⚠ **A separate file on purpose.** The savings file feeds the telemetry
payload, which is exactly three fields and is test-bound to stay so. This
module never reads or writes that file and holds no network import;
`tests/test_diagnose_writes_only_what_is_on_its_list.py` asserts both.

⚠⚠ **A count is written to the storage directory in force when it was
COUNTED**, never the one in force at flush time. The last flush runs at
interpreter exit, after a test session has restored its environment, so a
flush that re-read the environment would write a developer's real storage
directory from a test run (Maintenance Practice 8).

⚠ `tokens_returned` is bytes/4, the dispatcher's own scale, and the file names
that estimator: a count with no basis beside it reads as a tokenizer count.

⚠ The caller decides WHICH names are counted (the dispatcher passes only
catalog names). A tool name arrives from the client, so writing it unchecked
would let any string into a file a user may hand to someone else.

A leaf: stdlib only.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

FILE = "_tool_counters.json"
ESTIMATOR = "bytes/4"
_VERSION = 1
# Calls between writes. A crash loses at most this many counts; the file is
# small, so the cost of a write is the read-merge-replace and nothing else.
_FLUSH_EVERY = 20


def _root(base_path: Optional[str] = None) -> str:
    return base_path or os.environ.get("CODE_INDEX_PATH") or str(Path.home() / ".code-index")


def _load(path: Path) -> dict:
    """The file's content, or an empty record when it is absent or damaged."""
    empty = {"version": _VERSION, "since": datetime.now().date().isoformat(), "estimator": ESTIMATOR, "tools": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict) or not isinstance(data.get("tools"), dict):
        return empty
    tools = {}
    for name, row in data["tools"].items():
        if isinstance(name, str) and isinstance(row, dict):
            tools[name] = {
                "calls": _as_count(row.get("calls")),
                "tokens_returned": _as_count(row.get("tokens_returned")),
            }
    since = data.get("since") if isinstance(data.get("since"), str) else empty["since"]
    return {"version": _VERSION, "since": since, "estimator": ESTIMATOR, "tools": tools}


def _as_count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


class _State:
    """Counts not yet on disk, keyed by the storage directory they belong to."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, dict[str, list[int]]] = {}
        self._since_flush = 0

    def record(self, name: str, response_bytes: int, base_path: Optional[str]) -> None:
        with self._lock:
            row = self._pending.setdefault(_root(base_path), {}).setdefault(name, [0, 0])
            row[0] += 1
            row[1] += max(0, int(response_bytes)) // 4
            self._since_flush += 1
            due = self._since_flush >= _FLUSH_EVERY
        if due:
            self.flush()

    def flush(self) -> None:
        with self._lock:
            pending, self._pending, self._since_flush = self._pending, {}, 0
        for root, rows in pending.items():
            try:
                path = Path(root) / FILE
                if not path.parent.is_dir():
                    continue  # the storage directory is gone; nothing to add a count to
                # Read-merge-replace: another server process may have written
                # since this one last did, and its counts are not ours to drop.
                data = _load(path)
                for name, (calls, tokens) in rows.items():
                    cur = data["tools"].setdefault(name, {"calls": 0, "tokens_returned": 0})
                    cur["calls"] += calls
                    cur["tokens_returned"] += tokens
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True), encoding="utf-8")
                tmp.replace(path)
            except OSError:
                logger.debug("tool counters not written", exc_info=True)


_state = _State()


def record(name: str, response_bytes: int, base_path: Optional[str] = None) -> None:
    """Count one call of *name* whose answer was *response_bytes* long."""
    _state.record(name, response_bytes, base_path)


def flush() -> None:
    """Write every count not yet on disk. Never raises."""
    try:
        _state.flush()
    except Exception:
        logger.debug("tool counters flush failed", exc_info=True)


def read(base_path: Optional[str] = None) -> dict:
    """What is on disk for *base_path*: `{version, since, estimator, tools}`.

    `since` is `None` when there is no file: nothing has been counted, and
    today's date there would claim a start that never happened.
    """
    path = Path(_root(base_path)) / FILE
    data = _load(path)
    if not path.is_file():
        data["since"] = None
    return data


atexit.register(flush)
