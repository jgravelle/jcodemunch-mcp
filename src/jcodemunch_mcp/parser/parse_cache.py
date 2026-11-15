"""Content-addressed parse cache for shared-host deployments.

When N home directories on one machine index overlapping repos, each runs an
independent tree-sitter parse of identical files. This cache stores parse output
keyed by (index_version, content_hash, language, filename) in a **shared**
SQLite store, so an identical file parses once regardless of which indexer asked
— turning Nx parsing into 1x + (N-1) lookups.

Opt-in: disabled unless ``JCODEMUNCH_PARSE_CACHE`` points at a shared directory
(all seats on the box set it to the same path). When unset, ``cached_parse_file``
is a thin pass-through to ``parse_file`` — zero behavior change.

Correctness:
* The key includes ``content_hash`` + ``language`` + ``filename`` (symbol ids
  embed the path), so a hit is byte-identical to a fresh parse.
* The key includes ``INDEX_VERSION``, ``PARSER_GENERATION`` and the installed
  grammar versions, so a schema, extractor or grammar change invalidates the
  cache (the index-version cache-key rule).
* Any read/deserialize failure falls back to a live parse — the cache can never
  produce wrong symbols, only a slower miss.

Bounded: content-addressed keys accumulate without limit otherwise — every edit
mints a new ``content_hash`` whose old entry can never hit again, and an
``INDEX_VERSION`` bump strands the whole previous generation. After each write
the store is FIFO-trimmed to ``JCODEMUNCH_PARSE_CACHE_MAX_ROWS`` rows (default
50,000; ``<= 0`` disables the cap). Eviction is oldest-first by ``rowid``
(insertion order), so stale-content and stale-version rows — written before the
live ones — are evicted first. The cache is recomputable, so over-eviction under
concurrent seats only costs a re-parse, never correctness.
"""

from __future__ import annotations

import dataclasses
import datetime
import functools
import hashlib
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

from .symbols import Symbol

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROWS = 50_000


def cache_dir() -> Optional[str]:
    """Shared parse-cache directory, or None when the cache is disabled."""
    d = (os.environ.get("JCODEMUNCH_PARSE_CACHE") or "").strip()
    return d or None


def _max_rows() -> int:
    """Row cap for the shared store; <= 0 disables eviction (unbounded)."""
    raw = (os.environ.get("JCODEMUNCH_PARSE_CACHE_MAX_ROWS") or "").strip()
    if not raw:
        return DEFAULT_MAX_ROWS
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_MAX_ROWS


def _evict_oldest(conn: sqlite3.Connection, max_rows: int) -> int:
    """FIFO-trim the store to at most ``max_rows`` rows; return rows deleted.

    Oldest-first by ``rowid`` (insertion order), so stale-content and
    stale-INDEX_VERSION rows — written before the current generation — go first.
    A no-op when under the cap or when ``max_rows <= 0``."""
    if max_rows <= 0:
        return 0
    (count,) = conn.execute("SELECT COUNT(*) FROM parse_cache").fetchone()
    excess = count - max_rows
    if excess <= 0:
        return 0
    conn.execute(
        "DELETE FROM parse_cache WHERE rowid IN "
        "(SELECT rowid FROM parse_cache ORDER BY rowid ASC LIMIT ?)",
        (excess,),
    )
    return excess


def _index_version() -> int:
    # Lazy import: parser must not depend on storage at import time.
    try:
        from ..storage.index_store import INDEX_VERSION
        return int(INDEX_VERSION)
    except Exception:
        return 0


def _connect(d: str) -> sqlite3.Connection:
    p = Path(d) / "parse_cache.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA journal_size_limit = 67108864")  # bound WAL growth under starved checkpoints
    conn.execute(
        "CREATE TABLE IF NOT EXISTS parse_cache "
        "(key TEXT PRIMARY KEY, symbols TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    return conn


# What emits the symbols for identical bytes. A release bumps
# PARSER_GENERATION when the extractor's output changes, and a grammar package
# can change it inside its allowed range, so an entry written by any other
# parser is a different entry. Without this, the re-index a generation bump
# forces read the old symbols back from the cache and stamped the new
# generation over them.
_GRAMMAR_DISTRIBUTIONS = ("tree-sitter", "tree-sitter-language-pack", "tree-sitter-fsharp")


def _installed_version(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        logger.debug("no installed version for %s", name, exc_info=True)
        return ""


@functools.lru_cache(maxsize=1)
def _grammar_identity() -> str:
    """Installed grammar versions; read once, since a process cannot swap them."""
    return ",".join(_installed_version(d) for d in _GRAMMAR_DISTRIBUTIONS)


def _parser_identity() -> str:
    try:
        from ..storage.index_store import PARSER_GENERATION
        gen = int(PARSER_GENERATION)
    except Exception:
        logger.debug("parser generation unavailable for parse cache", exc_info=True)
        gen = 0
    return f"g{gen}:{_grammar_identity()}"


def _key(content: str, filename: str, language: str, repo: Optional[str] = None) -> str:
    h = hashlib.sha256(content.encode("utf-8", "surrogatepass")).hexdigest()
    key = f"v{_index_version()}:{_parser_identity()}:{language}:{h}:{filename}"
    if language == "racket":
        # Racket's own generation: the reader replaced tree-sitter for `.rkt`
        # without a PARSER_GENERATION bump.
        from .racket_reader import READER_GENERATION
        key = f"{key}:rr{READER_GENERATION}"
        # `racket_definition_forms` / `racket_langs` change what the parser
        # emits for identical bytes, so identical bytes are not one entry
        # across two configs. Empty for an unconfigured project, so the key
        # is unchanged there.
        try:
            from ..config import racket_config_digest
            digest = racket_config_digest(repo)
        except Exception:
            logger.debug("racket config digest unavailable for parse cache", exc_info=True)
            digest = ""
        if digest:
            key = f"{key}:rkt{digest}"
    return key


def cached_parse_file(
    content: str,
    filename: str,
    language: str,
    source_bytes: Optional[bytes] = None,
    repo: Optional[str] = None,
) -> list[Symbol]:
    """Drop-in for ``parse_file`` that consults the shared parse cache when
    enabled. Transparent pass-through when ``JCODEMUNCH_PARSE_CACHE`` is unset."""
    from .extractor import parse_file  # lazy: avoid import cycle

    d = cache_dir()
    if not d:
        return parse_file(content, filename, language, source_bytes=source_bytes, repo=repo)

    key = _key(content, filename, language, repo)

    # Read
    try:
        conn = _connect(d)
        try:
            row = conn.execute("SELECT symbols FROM parse_cache WHERE key = ?", (key,)).fetchone()
        finally:
            conn.close()
        if row:
            try:
                return [Symbol(**sd) for sd in json.loads(row[0])]
            except (ValueError, TypeError) as exc:
                logger.debug("parse cache deserialize failed for %s (%s); reparsing", filename, exc)
    except sqlite3.Error as exc:
        logger.debug("parse cache read failed (%s); reparsing", exc)
        return parse_file(content, filename, language, source_bytes=source_bytes, repo=repo)

    # Miss → parse, then store (best-effort)
    symbols = parse_file(content, filename, language, source_bytes=source_bytes, repo=repo)
    try:
        payload = json.dumps([dataclasses.asdict(s) for s in symbols], separators=(",", ":"))
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = _connect(d)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO parse_cache (key, symbols, created_at) VALUES (?, ?, ?)",
                (key, payload, now),
            )
            evicted = _evict_oldest(conn, _max_rows())
            conn.commit()
            if evicted:
                logger.debug("parse cache evicted %d oldest row(s) to honor cap", evicted)
        finally:
            conn.close()
    except (sqlite3.Error, TypeError, ValueError) as exc:
        logger.debug("parse cache write failed for %s (%s)", filename, exc)
    return symbols
