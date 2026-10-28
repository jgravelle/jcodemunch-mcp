"""Per-file trigram bitmaps, so a substring search opens only the files that can match.

`search_text` reads every cached file in scope, so a cold search costs one open
and one read per file: seconds on a few thousand files. Each cached file gets a
bitmap of the hashed trigrams of its lowered text. A substring query can occur
in a file only if every trigram of the lowered query is set in that file's
bitmap, so a file whose bitmap lacks one is skipped without being opened.

⚠ The bitmap can say "maybe" for a file that has no match (a hash collision),
never "no" for one that has. It answers for the text it was built from, and
each row records that text's file size and mtime: a file whose stat differs
from its row is scanned, never trusted. A file with no row is scanned too.

The bitmaps live in a sidecar SQLite file beside the repo's index
(``<slug>.textfilter``), not in the index ``.db``: the index's mtime keys the
in-memory index cache, and a search that repaired a row there would invalidate
it (the v1.108.185 class).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import zlib
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

SUFFIX = ".textfilter"
MIN_QUERY_CHARS = 3
# Bits per distinct trigram, rounded up to a power of two (k = 1 hash).
BITS_PER_GRAM = 4
MIN_BITS = 1024

_DDL = (
    "CREATE TABLE IF NOT EXISTS text_filters ("
    "path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, "
    "nbits INTEGER NOT NULL, bits BLOB NOT NULL)"
)


def sidecar_path(content_dir: Path) -> Path:
    return Path(str(content_dir) + SUFFIX)


def _grams(text_lower: str) -> set:
    return {text_lower[i : i + 3] for i in range(len(text_lower) - 2)}


def _bit(gram: str, mask: int) -> int:
    return zlib.crc32(gram.encode("utf-8", "surrogatepass")) & mask


def build(text: str) -> tuple[int, bytes]:
    """(nbits, bitmap) for a file's text."""
    grams = _grams(text.lower())
    nbits = MIN_BITS
    while nbits < len(grams) * BITS_PER_GRAM:
        nbits <<= 1
    mask = nbits - 1
    bits = bytearray(nbits // 8)
    for g in grams:
        h = _bit(g, mask)
        bits[h >> 3] |= 1 << (h & 7)
    return nbits, bytes(bits)


def query_grams(query_lower: Optional[str]) -> Optional[list]:
    """The trigrams a lowered substring query needs, or None when it has none."""
    if not query_lower or len(query_lower) < MIN_QUERY_CHARS or "\n" in query_lower:
        return None
    return sorted(_grams(query_lower))


def may_contain(nbits: int, bits: bytes, grams: list) -> bool:
    mask = nbits - 1
    for g in grams:
        h = _bit(g, mask)
        if not bits[h >> 3] & (1 << (h & 7)):
            return False
    return True


def content_stat(content_dir: Path, rel_path: str) -> Optional[tuple[int, int]]:
    """(size, mtime_ns) of a cached file, or None. No realpath: this only
    decides whether a row describes the file, and a path that could leave the
    content directory gets None, so it is scanned through the guarded route."""
    parts = rel_path.replace("\\", "/").split("/")
    if (
        not rel_path
        or rel_path.startswith("/")
        or any(p in ("", ".", "..") for p in parts)
    ):
        return None
    if ":" in parts[0]:
        return None
    try:
        st = os.stat(os.path.join(str(content_dir), *parts))
    except (OSError, ValueError):
        return None
    return st.st_size, st.st_mtime_ns


def _connect(content_dir: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(sidecar_path(content_dir)), timeout=5)
    conn.execute(_DDL)
    return conn


def load(content_dir: Path) -> dict:
    """path -> (size, mtime_ns, nbits, bits); {} when there is no sidecar."""
    side = sidecar_path(content_dir)
    if not side.exists():
        return {}
    try:
        from .generation import connect_readonly

        conn = connect_readonly(side, timeout=5)
        try:
            rows = conn.execute(
                "SELECT path, size, mtime_ns, nbits, bits FROM text_filters"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        logger.debug("text filter load failed", exc_info=True)
        return {}
    return {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}


def record(content_dir: Path, items: Iterable[tuple[str, str]]) -> None:
    """Build and store rows for (rel_path, text) just written to the cache.

    Each row's stamp is the cached file's stat now, so call it after the write.
    Best-effort: a failure here leaves files unfiltered (scanned), never wrong.
    """
    rows = [row_for(content_dir, p, t) for p, t in items]
    store_rows(content_dir, [r for r in rows if r is not None])


def _bodies_written() -> bool:
    """False under ``cache_mode: metadata_only``, where the indexer writes no
    body. An older cached file can still be on disk then, and a row built from
    the NEW text but stamped with that OLD file's stat would let a search skip
    a match the old file holds. Its existing row, if any, still describes it."""
    try:
        from .. import config as _cfg

        return _cfg.get("cache_mode", "full") != "metadata_only"
    except Exception:
        logger.debug("cache_mode read failed", exc_info=True)
        return True


def row_for(content_dir: Path, rel_path: str, text: str) -> Optional[tuple]:
    """A row for a file just written to the cache, or None when no body was
    written (``cache_mode: metadata_only``) or the file isn't there."""
    if not _bodies_written():
        return None
    stamp = content_stat(content_dir, rel_path)
    if stamp is None:
        return None
    nbits, bits = build(text)
    return (rel_path, stamp[0], stamp[1], nbits, bits)


def store_rows(content_dir: Path, rows: list) -> None:
    if not rows:
        return
    try:
        conn = _connect(content_dir)
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO text_filters (path, size, mtime_ns, nbits, bits) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        logger.debug("text filter write failed", exc_info=True)


def forget(content_dir: Path, rel_paths: Iterable[str]) -> None:
    paths = [(p,) for p in rel_paths]
    if not paths or not sidecar_path(content_dir).exists():
        return
    try:
        conn = _connect(content_dir)
        try:
            conn.executemany("DELETE FROM text_filters WHERE path = ?", paths)
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        logger.debug("text filter delete failed", exc_info=True)


def remove(content_dir: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            Path(str(sidecar_path(content_dir)) + suffix).unlink()
        except OSError:
            pass


class Filter:
    """One search's view: rows loaded once, rows repaired while scanning."""

    def __init__(self, content_dir: Path, query_lower: Optional[str]):
        self.content_dir = content_dir
        self.grams = query_grams(query_lower)
        self.rows = load(content_dir) if self.grams else {}
        self.skipped = 0
        self._repairs: list = []

    def rules_out(self, rel_path: str) -> tuple[bool, Optional[tuple[int, int]]]:
        """(skip?, stat taken before any read). Skip only on a matching stamp."""
        if not self.grams:
            return False, None
        stamp = content_stat(self.content_dir, rel_path)
        row = self.rows.get(rel_path)
        if stamp is not None and row is not None and (row[0], row[1]) == stamp:
            if not may_contain(row[2], row[3], self.grams):
                self.skipped += 1
                return True, stamp
            return False, None  # current row says maybe: nothing to repair
        return False, stamp

    def note_read(
        self, rel_path: str, stamp: Optional[tuple[int, int]], text: str
    ) -> None:
        """Queue a row for a file read without a current row (stamp from before the read)."""
        if stamp is None or not self.grams:
            return
        nbits, bits = build(text)
        self._repairs.append((rel_path, stamp[0], stamp[1], nbits, bits))

    def flush(self) -> None:
        rows, self._repairs = self._repairs, []
        store_rows(self.content_dir, rows)
