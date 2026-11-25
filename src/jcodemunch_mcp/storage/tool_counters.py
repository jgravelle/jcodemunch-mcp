"""Three local per-tool counters: calls, tokens returned, and calls that found nothing.

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

⚠⚠ **A write that cannot be made keeps its counts.** The first draft swapped
the pending counts out, then read, merged and replaced the file with no lock
and one shared temp name: eight servers exiting together kept 5 of 40 counts
in 12 of 12 trials (review, 2026-10-13). A flush now takes a lock file, writes
through a temp file named for this process, and puts back every count it
could not write. Clients that start several servers and close them together
are an ordinary shape here (#375).

⚠⚠ **The exit flush has no later chance, so what it cannot write goes to a
`.pending` sibling** (`_tool_counters.json.<uuid>.pending`). Put back in
memory, those counts died with the process: CI on Windows lost three of eight
processes' counts at a shared exit (run 43971776048, 2026-11-16), through a
lock wait running out or a `PermissionError` on the read or the replace. The
protocol (claim before adding, read the main file first, sweep stale temps)
is `pending_files`, shared with the Cursor read counter. A write that fails
after folding writes the folded counts back as a pending file, and keeps them
in memory if even that fails. `read` adds pending files to what it reports. A
serving flush still keeps its own counts in memory: the next write retries them.

⚠ **The dispatcher never waits for the disk.** `record` only adds to a dict;
when a write is due it is handed to a short-lived thread. A synchronous write
there cost about 15 ms on every twentieth call, which is where a p95 sits.

⚠ `tokens_returned` is bytes/4, the dispatcher's own scale, and the file names
that estimator: a count with no basis beside it reads as a tokenizer count.

⚠ `no_match` counts calls whose answer said nothing in the index matched:
its `negative_evidence` verdict (#1308) was an absence verdict, never merely
present. The dispatcher decides (`server._is_absence_answer`); this module
only adds. A file written before the count existed reads it as 0.

⚠ The caller decides WHICH names are counted (the dispatcher passes only
catalog names). A tool name arrives from the client, so writing it unchecked
would let any string into a file a user may hand to someone else. A key
already in the file is carried forward as it stands.

A leaf: stdlib only.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import pending_files

logger = logging.getLogger(__name__)

FILE = "_tool_counters.json"
LOCK_FILE = "_tool_counters.lock"
PENDING_SUFFIX = pending_files.SUFFIX
ESTIMATOR = "bytes/4"
_VERSION = 1
# A write is due after this many calls, or on the first call after this many
# seconds. A killed server loses what it had not written.
_FLUSH_EVERY = 20
_FLUSH_SECONDS = 30.0
# How long a flush waits for another process's lock: briefly while serving,
# longer at exit, when these counts have no later chance.
_LOCK_WAIT = 0.25
_LOCK_WAIT_AT_EXIT = 3.0
# A lock file older than this was left by a process that died holding it.
_LOCK_STALE_SECONDS = 30.0


def storage_root(base_path: Optional[str] = None) -> str:
    """The storage directory: *base_path*, else `CODE_INDEX_PATH`, else `~/.code-index`.

    ⚠⚠ **Not a second rule: it asks the index store's** (`sqlite_store.
    _default_base_path`, and the same `expanduser().resolve()` the store applies
    to an explicit path). The savings path used to ignore `CODE_INDEX_PATH`, and
    a copy here that skipped `expanduser` would put `CODE_INDEX_PATH=~/x`
    counters in a literal `~` folder beside indexes in the home one.
    ⚠ Imported at call time: this module stays a stdlib leaf at import.
    """
    from .sqlite_store import _default_base_path

    if base_path:
        return str(Path(base_path).expanduser().resolve())
    return str(_default_base_path())


_root = storage_root


def _as_count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _empty() -> dict:
    return {"version": _VERSION, "since": datetime.now().date().isoformat(), "estimator": ESTIMATOR, "tools": {}}


def _load(path: Path, *, strict: bool = False) -> dict:
    """The file's content, or an empty record when it is absent or damaged.

    ⚠ *strict* is for the writer: a file that EXISTS and could not be read this
    instant (another process is replacing it) raises, because merging into an
    empty record and writing that back would erase every count in it.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty()
    except OSError:
        if strict:
            raise
        logger.debug("tool counters file not read", exc_info=True)
        return _empty()
    except ValueError:
        logger.debug("tool counters file is damaged; starting an empty record", exc_info=True)
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("tools"), dict):
        return _empty()
    tools = {}
    for name, row in data["tools"].items():
        if isinstance(name, str) and isinstance(row, dict):
            tools[name] = {
                "calls": _as_count(row.get("calls")),
                "tokens_returned": _as_count(row.get("tokens_returned")),
                "no_match": _as_count(row.get("no_match")),
            }
    since = data.get("since") if isinstance(data.get("since"), str) else _empty()["since"]
    return {"version": _VERSION, "since": since, "estimator": ESTIMATOR, "tools": tools}


def acquire_lock(lock: Path, wait: float) -> bool:
    """Create *lock* exclusively, waiting up to *wait* seconds. False when another holds it.

    ⚠ Also the savings meter's lock (see `token_tracker`): one lock rule for both
    per-call stores, so a fix to it (the Windows `PermissionError` below) reaches both.
    """
    deadline = time.monotonic() + wait
    while True:
        try:
            os.close(os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > _LOCK_STALE_SECONDS:
                    lock.unlink()
                    continue
            except OSError:
                pass  # released or removed between the two calls; try again
        except OSError:
            # ⚠ On Windows, creating a lock its holder is deleting this instant
            # raises PermissionError, not FileExistsError. That is a held lock:
            # giving up there lost an exit-time flush its 3 seconds (review,
            # 2026-10-13: 3 wrong totals in 17 trials at 24 or more processes).
            if not lock.parent.is_dir():
                return False  # the storage directory is gone
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


_acquire = acquire_lock


def _pending_paths(base: Path) -> list:
    return pending_files.paths(base / FILE)


def _read_pending(p: Path) -> Optional[dict]:
    """`{name: [calls, tokens, misses]}`, or None for a file that is not a count."""
    rec = pending_files.read(p)
    tools = rec.get("tools") if rec is not None else None
    if not isinstance(tools, dict):
        return None
    rows = {}
    for name, row in tools.items():
        if not (isinstance(name, str) and isinstance(row, list) and len(row) == 3):
            return None
        if any(_as_count(v) != v for v in row):
            return None
        rows[name] = list(row)
    return rows


def _write_pending(base: Path, rows: dict) -> bool:
    """Keep *rows* on disk for the next write. False when they could not be written."""
    if not rows:
        return True
    return pending_files.write(base / FILE, {"tools": {k: list(v) for k, v in rows.items()}})


def _add_rows(into: dict, rows: dict) -> None:
    for name, (calls, tokens, misses) in rows.items():
        row = into.setdefault(name, [0, 0, 0])
        row[0] += calls
        row[1] += tokens
        row[2] += misses


def _merge_into_file(root: str, rows: dict, wait: float, unkept: Optional[dict] = None) -> bool:
    """Add *rows*, and every pending file, to the file under *root*. False when *rows* were not written.

    Folded counts from other processes' pending files that could be neither
    written nor written back as a pending file are added to *unkept*, for the
    caller to keep with its own.
    """
    base = Path(root)
    if not base.is_dir():
        return True  # the storage directory is gone; there is no file to add a count to
    lock = base / LOCK_FILE
    if not _acquire(lock, wait):
        return False
    try:
        path = base / FILE
        try:
            data = _load(path, strict=True)
        except OSError:
            logger.debug("tool counters file busy; counts kept for the next write", exc_info=True)
            return False
        pending_files.sweep_stale_temps(path)
        folded: dict = {}
        for p in _pending_paths(base):
            got = _read_pending(p)
            if got is None or not pending_files.claim(p):
                continue
            _add_rows(folded, got)
        total: dict = {}
        _add_rows(total, folded)
        _add_rows(total, rows)
        for name, (calls, tokens, misses) in total.items():
            cur = data["tools"].setdefault(name, {"calls": 0, "tokens_returned": 0, "no_match": 0})
            cur["calls"] += calls
            cur["tokens_returned"] += tokens
            cur["no_match"] = cur.get("no_match", 0) + misses
        tmp = base / f"{FILE}.{os.getpid()}.tmp"
        try:
            tmp.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            logger.debug("tool counters not written", exc_info=True)
            try:
                tmp.unlink()
            except OSError:
                pass
            if folded and not _write_pending(base, folded):
                if unkept is not None:
                    _add_rows(unkept, folded)
                else:
                    logger.warning("tool counters: %d folded tool row(s) could not be kept", len(folded))
            return False
        return True
    finally:
        try:
            lock.unlink()
        except OSError:
            logger.debug("tool counters lock not removed", exc_info=True)


class _State:
    """Counts not yet on disk, keyed by the storage directory they belong to."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, dict[str, list[int]]] = {}
        self._since_flush = 0
        self._last_flush = time.monotonic()
        self._flushing = False

    def record(self, name: str, response_bytes: int, base_path: Optional[str], no_match: bool = False) -> None:
        with self._lock:
            row = self._pending.setdefault(_root(base_path), {}).setdefault(name, [0, 0, 0])
            row[0] += 1
            row[1] += max(0, int(response_bytes)) // 4
            row[2] += 1 if no_match else 0
            self._since_flush += 1
            due = not self._flushing and (
                self._since_flush >= _FLUSH_EVERY or time.monotonic() - self._last_flush >= _FLUSH_SECONDS
            )
            if due:
                self._flushing = True
        if due:
            # Not a daemon: the interpreter waits for a write in progress, so a
            # lock file is never left behind by a normal exit.
            threading.Thread(target=self._flush_in_thread, name="jcm-tool-counters").start()

    def _flush_in_thread(self) -> None:
        try:
            self.flush()
        except Exception:
            logger.debug("tool counters flush failed", exc_info=True)
        finally:
            with self._lock:
                self._flushing = False

    def flush(self, wait: float = _LOCK_WAIT, *, last_chance: bool = False) -> None:
        with self._lock:
            pending, self._pending = self._pending, {}
            self._since_flush = 0
            self._last_flush = time.monotonic()
        for root, rows in pending.items():
            written = False
            unkept: dict = {}
            try:
                written = _merge_into_file(root, rows, wait, unkept)
            except Exception:
                logger.debug("tool counters flush failed for one storage directory", exc_info=True)
            if unkept:
                # Other processes' counts this write folded and could not keep on disk.
                _add_rows(rows, unkept)
            if not written:
                # At exit there is no later write to retry these, so they go to disk.
                if last_chance and Path(root).is_dir() and _write_pending(Path(root), rows):
                    continue
                self._put_back(root, rows)

    def _put_back(self, root: str, rows: dict) -> None:
        with self._lock:
            mine = self._pending.setdefault(root, {})
            for name, (calls, tokens, misses) in rows.items():
                row = mine.setdefault(name, [0, 0, 0])
                row[0] += calls
                row[1] += tokens
                row[2] += misses
            # ⚠ The call counter is NOT put back. Put back, it stays at or above
            # the threshold and every later call starts a writer thread for as
            # long as the directory is read-only or the disk full (measured: 281
            # threads in 300 calls). The kept counts go out with the next write
            # the clock or the next twenty calls bring.


_state = _State()


def record(name: str, response_bytes: int, base_path: Optional[str] = None, *, no_match: bool = False) -> None:
    """Count one call of *name* whose answer was *response_bytes* long, and whether it
    found nothing (*no_match*). Never touches the disk."""
    _state.record(name, response_bytes, base_path, no_match)


def flush(wait: float = _LOCK_WAIT, *, last_chance: bool = False) -> None:
    """Write every count not yet on disk; keep what could not be written. Never raises.

    *last_chance* (the exit flush) keeps what it could not write in a pending
    file, since memory goes with the process.
    """
    try:
        _state.flush(wait, last_chance=last_chance)
    except Exception:
        logger.debug("tool counters flush failed", exc_info=True)


def read(base_path: Optional[str] = None) -> dict:
    """What is on disk for *base_path*: `{version, since, estimator, tools}`.

    `since` is `None` when there is no file: nothing has been counted, and
    today's date there would claim a start that never happened.
    """
    path = Path(_root(base_path)) / FILE
    data = _load(path)
    pending: dict = {}
    for p in _pending_paths(path.parent):
        got = _read_pending(p)
        if got is not None:
            _add_rows(pending, got)
    if not path.is_file() and not pending:
        data["since"] = None
    for name, (calls, tokens, misses) in pending.items():
        cur = data["tools"].setdefault(name, {"calls": 0, "tokens_returned": 0, "no_match": 0})
        cur["calls"] += calls
        cur["tokens_returned"] += tokens
        cur["no_match"] += misses
    return data


def read_or_none(base_path: Optional[str] = None) -> Optional[dict]:
    """`read`, or `None` when the file EXISTS and could not be read or parsed.

    For a reader that publishes the counts: an empty record there would say
    "nothing counted, starting today" about a file that may hold months.
    """
    path = Path(_root(base_path)) / FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return read(base_path)
    except (OSError, ValueError):
        logger.debug("tool counters file exists and was not read", exc_info=True)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("tools"), dict):
        return None
    return read(base_path)


def _flush_at_exit() -> None:
    flush(_LOCK_WAIT_AT_EXIT, last_chance=True)


atexit.register(_flush_at_exit)
