"""Per-file cl100k_base token counts, taken the first time a file is credited.

``tokens_saved`` credits a response against the files an agent would
otherwise have read. Counting those files on every call would put a
tokenizer pass over whole files on the read path, so each file is counted
once and the count is stored in the index; a later call only looks it up
(``savings_tokenizer.credit_files``).

⚠ Counted on FIRST USE, never at save (the cold-index fix). Counting every
file while the index was written made a full index slower (since
v1.108.439, #1414) to pay for files no response ever credits. Now a
response that credits a file with no stored count divides that file's
bytes by the table, says so per file in ``tokens_saved_basis``, and queues
the file; ``flush_pending`` counts the queued files from the indexed copy
after the call and stores the rows, so the file's next credit is counted.

Keyed by the file's CONTENT HASH (``files.hash``), not its path: a count is a
property of the bytes, so an unchanged file keeps its row across saves, a
rename costs nothing, and a branch delta that restores old content finds the
old count. Rows for hashes no indexed file carries any more are removed at
each save (``prune``), so the table stays the size of the corpus.
⚠ No ``INDEX_VERSION`` bump: the table is in ``_SCHEMA_SQL`` for new indexes
and created here for old ones, the ``diagnostics`` rule.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

SCHEMA = "CREATE TABLE IF NOT EXISTS token_counts (hash TEXT PRIMARY KEY, cl100k INTEGER NOT NULL)"


def prune(conn: sqlite3.Connection) -> int:
    """Drop rows for hashes no indexed file carries; count nothing.

    Runs inside the caller's write transaction, after the ``files`` rows are
    written. Returns the number of rows dropped. Never raises.
    """
    try:
        conn.execute(SCHEMA)
        live = {
            h for (h,) in conn.execute(
                "SELECT hash FROM files WHERE hash IS NOT NULL AND hash != ''"
            ).fetchall()
        }
        have = {h for (h,) in conn.execute("SELECT hash FROM token_counts").fetchall()}
        stale = have - live
        if stale:
            conn.executemany(
                "DELETE FROM token_counts WHERE hash = ?", [(h,) for h in stale]
            )
        return len(stale)
    except Exception:
        logger.debug("token counts not pruned", exc_info=True)
        return 0


# --- counted after a call credited files that had no count ------------------

#: (index, {hash: path}) a response credited without a stored count. Bounded:
#: a file left out is queued again by its next credit.
_pending: "list[tuple[object, dict[str, str]]]" = []
_MAX_PENDING_INDEXES = 4
_MAX_PENDING_FILES = 5_000
#: Characters of text counted per flush, checked before each file, so one
#: flush can pass it by one file. The next call waits for a write-back in
#: flight (server ``_await_bm25_flush``), so one flush stays short; what is
#: left stays queued for the next one.
_FLUSH_MAX_BYTES = 1_000_000
_pending_lock = threading.Lock()
#: (db path) -> hashes that could not be counted from the indexed copy: no
#: copy (``cache_mode: metadata_only``, a deleted copy) or a copy whose text no
#: longer hashes to the key. Remembered for the process so a later credit
#: does not queue and re-open them on every call. Bounded.
_uncountable: "dict[str, set[str]]" = {}
_UNCOUNTABLE_MAX = 50_000


def _mark_uncountable(db_path, hashes: Iterable[str]) -> None:
    key = str(db_path)
    seen = _uncountable.setdefault(key, set())
    seen.update(hashes)
    if len(seen) > _UNCOUNTABLE_MAX:
        seen.clear()


def _content_hash(text: str) -> str:
    # The rule `files.hash` is written with (index_store._file_hash).
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def queue(index, missing: Mapping[str, str]) -> None:
    """Remember files to count after this call. Never raises."""
    try:
        db_path = getattr(index, "_db_path", None)
        if not missing or not db_path:
            return
        with _pending_lock:
            skip = _uncountable.get(str(db_path))
            if skip:
                missing = {h: p for h, p in missing.items() if h not in skip}
                if not missing:
                    return
            for ix, files in _pending:
                if ix is index:
                    for h, path in missing.items():
                        if len(files) >= _MAX_PENDING_FILES:
                            break
                        files.setdefault(h, path)
                    return
            _pending.append((index, dict(list(missing.items())[:_MAX_PENDING_FILES])))
            del _pending[:-_MAX_PENDING_INDEXES]
    except Exception:
        logger.debug("token counts not queued", exc_info=True)


def pending_count() -> int:
    with _pending_lock:
        return len(_pending)


def flush_pending() -> int:
    """Count every queued file from its indexed copy and store the rows.

    Off the request path (the dispatcher runs it after the response, with no
    call in flight). Returns how many counts were stored. Never raises.
    """
    from ..savings_tokenizer import count_many, encoding

    with _pending_lock:
        work = list(_pending)
        _pending.clear()
    stored = 0
    for index, files in work:
        try:
            if encoding() is None:
                continue  # nothing can count them; the next credit queues them again
            db_path = getattr(index, "_db_path", None)
            if not db_path:
                continue
            from .sqlite_store import loaded_index_can_take_a_write_back

            # The store refuses a write-back to a .db that moved since load;
            # ask first, before reading and counting the copies (L-346).
            if not loaded_index_can_take_a_write_back(index):
                continue
            content_dir = Path(db_path).with_suffix("")
            base = _resolved(content_dir)
            hashes, texts, size, failed = [], [], 0, []
            items = list(files.items())
            for i, (h, path) in enumerate(items):
                if size >= _FLUSH_MAX_BYTES:
                    queue(index, dict(items[i:]))
                    break
                text = _read_copy(content_dir, path, base)
                if text is None or _content_hash(text) != h:
                    failed.append(h)
                    continue
                hashes.append(h)
                texts.append(text)
                size += len(text)
            if failed:
                with _pending_lock:
                    _mark_uncountable(db_path, failed)
            counts = count_many(texts) if texts else None
            if not counts:
                continue
            rows = list(zip(hashes, counts))
            from .sqlite_store import store_token_counts_for_loaded_index

            if store_token_counts_for_loaded_index(index, rows):
                stored += len(rows)
        except Exception:
            logger.debug("token count write-back failed", exc_info=True)
    return stored


def write_in_txn(conn: sqlite3.Connection, rows: Iterable[tuple[str, int]]) -> None:
    """Store ``(hash, count)`` rows inside the caller's open transaction."""
    conn.execute(SCHEMA)
    conn.executemany(
        "INSERT OR REPLACE INTO token_counts (hash, cl100k) VALUES (?, ?)", list(rows)
    )


def _resolved(content_dir: Optional[Path]) -> Optional[str]:
    # Resolved once per save; resolve_within re-resolves the base otherwise
    # (file_sizes.py does the same).
    if content_dir is None:
        return None
    try:
        return str(content_dir.resolve())
    except (OSError, ValueError):
        return None


def _read_copy(
    content_dir: Optional[Path], rel: str, base: Optional[str] = None
) -> Optional[str]:
    if content_dir is None:
        return None
    from ..security import resolve_within

    try:
        path = resolve_within(content_dir, rel, base_resolved=base)
        if path is None:
            return None
        # newline="": the count is of the bytes the index holds, CRLF included.
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            return f.read()
    except (OSError, ValueError):
        return None


#: (db path) -> ((db mtime_ns), {hash: count}). One read per index generation,
#: so a call does not open a read-only connection each time. Bounded to a few
#: indexes, like the matrix cache.
_CACHE: "OrderedDict[str, tuple[Optional[int], dict[str, int]]]" = OrderedDict()
_CACHE_MAX = 8
_CACHE_LOCK = threading.Lock()


def _all_counts(db_path) -> dict[str, int]:
    from .generation import connect_readonly, db_mtime_ns

    key = str(db_path)
    stamp = db_mtime_ns(db_path)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None and stamp is not None and hit[0] == stamp:
            _CACHE.move_to_end(key)
            return hit[1]
    conn = connect_readonly(db_path)
    try:
        counts = dict(conn.execute("SELECT hash, cl100k FROM token_counts"))
    finally:
        conn.close()
    with _CACHE_LOCK:
        _CACHE[key] = (stamp, counts)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return counts


def lookup(db_path, hashes: Iterable[str]) -> dict[str, int]:
    """Stored counts for ``hashes``; a missing hash is absent from the result.

    Read-only (``generation.connect_readonly``), so a read never moves the
    index's mtime, and cached until the index's ``.db``/``-wal`` mtime moves.
    An index with no ``token_counts`` table answers ``{}``.
    """
    wanted = {h for h in hashes if h}
    if not db_path or not wanted:
        return {}
    try:
        counts = _all_counts(db_path)
        return {h: counts[h] for h in wanted if h in counts}
    except Exception:
        logger.debug("token counts unreadable at %s", db_path, exc_info=True)
        return {}
