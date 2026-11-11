"""Per-file cl100k_base token counts, taken when an index is saved.

``tokens_saved`` credits a response against the files an agent would
otherwise have read. Counting those files on every call would put a
tokenizer pass over whole files on the read path, so each file is counted
once, here, while the index is written, and a call only looks the counts up
(``savings_tokenizer.credit``).

Keyed by the file's CONTENT HASH (``files.hash``), not its path: a count is a
property of the bytes, so an unchanged file keeps its row across saves, a
rename costs nothing, and a branch delta that restores old content finds the
old count. Rows for hashes no indexed file carries any more are removed at the
same save, so the table stays the size of the corpus.

⚠ ``record`` fills EVERY hash the index holds that has no row, not only the
files this save changed. An index saved before counts existed therefore gets
its counts on its next save of any kind, from the content-dir copy the indexer
already keeps, with no re-parse. Until then its files credit through the
bytes/token table and the response says so (``tokens_saved_basis``).
⚠ No ``INDEX_VERSION`` bump: the table is in ``_SCHEMA_SQL`` for new indexes
and created here for old ones, the ``diagnostics`` rule.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

SCHEMA = "CREATE TABLE IF NOT EXISTS token_counts (hash TEXT PRIMARY KEY, cl100k INTEGER NOT NULL)"

#: Counts taken while the indexer wrote each file's copy (``note``), waiting
#: for the save that records them. Reading the copy back at save time cost
#: 14 ms a file on Windows (the file was seconds old, so it was scanned again);
#: counting the text the writer already holds costs nothing extra to read.
#: Bounded; ``record`` takes what it uses.
_PENDING: "OrderedDict[str, int]" = OrderedDict()
_PENDING_MAX = 200_000
_PENDING_LOCK = threading.Lock()


def note(content: str) -> None:
    """Count ``content`` now, for the next ``record`` to store. Never raises."""
    try:
        from ..savings_tokenizer import count

        n = count(content)
        if n is None:
            return
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with _PENDING_LOCK:
            _PENDING[h] = n
            while len(_PENDING) > _PENDING_MAX:
                _PENDING.popitem(last=False)
    except Exception:
        logger.debug("token count not noted", exc_info=True)


def record(
    conn: sqlite3.Connection,
    content_dir: Optional[Path],
    raw_files: Optional[Mapping[str, str]] = None,
) -> int:
    """Count every indexed file whose content hash has no row; prune orphans.

    Runs inside the caller's write transaction, after the ``files`` rows are
    written. ``raw_files`` (path -> text) is used where the save holds the
    text; otherwise the indexed copy under ``content_dir`` is read. Returns
    the number of files counted. Never raises: a failure leaves the index
    saved and its calls on the bytes/token fallback.
    """
    try:
        from ..savings_tokenizer import count_many

        conn.execute(SCHEMA)
        rows = conn.execute(
            "SELECT path, hash FROM files WHERE hash IS NOT NULL AND hash != ''"
        ).fetchall()
        live = {h for _, h in rows}
        have = {h for (h,) in conn.execute("SELECT hash FROM token_counts").fetchall()}
        stale = have - live
        if stale:
            conn.executemany(
                "DELETE FROM token_counts WHERE hash = ?", [(h,) for h in stale]
            )
        todo: dict[str, str] = {}
        for path, h in rows:
            if h not in have and h not in todo:
                todo[h] = path
        if not todo:
            return 0
        found: list[tuple[str, int]] = []
        with _PENDING_LOCK:
            for h in list(todo):
                n = _PENDING.pop(h, None)
                if n is not None:
                    found.append((h, n))
                    del todo[h]
        hashes, texts = [], []
        base = _resolved(content_dir)
        for h, path in todo.items():
            text = (raw_files or {}).get(path)
            if text is None:
                text = _read_copy(content_dir, path, base)
            if text is not None:
                hashes.append(h)
                texts.append(text)
        counts = count_many(texts) if texts else []
        rows_out = found + list(zip(hashes, counts or []))
        if rows_out:
            conn.executemany(
                "INSERT OR REPLACE INTO token_counts (hash, cl100k) VALUES (?, ?)",
                rows_out,
            )
        return len(rows_out)
    except Exception:
        logger.debug("token counts not recorded", exc_info=True)
        return 0


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


#: (db path) -> ((db mtime_ns), {hash: count}). One read per index generation:
#: opening a read-only connection on every call measured about 1 ms of the
#: per-call cost on Windows. Bounded to a few indexes, like the matrix cache.
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
