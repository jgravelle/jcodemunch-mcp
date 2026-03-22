"""SQLite WAL storage backend for code indexes.

Replaces monolithic JSON files with per-repo SQLite databases.
WAL mode enables concurrent readers + single writer with delta writes.
"""

import json
import logging
import os
import shutil
import sqlite3
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, NamedTuple, Optional, cast

from ..parser.symbols import Symbol

if TYPE_CHECKING:
    from .index_store import CodeIndex

logger = logging.getLogger(__name__)

# SQL to create tables and indexes
_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS symbols (
    id                TEXT PRIMARY KEY,
    file              TEXT NOT NULL,
    name              TEXT NOT NULL,
    kind              TEXT,
    signature         TEXT,
    summary           TEXT,
    docstring         TEXT,
    line              INTEGER,
    end_line          INTEGER,
    byte_offset       INTEGER,
    byte_length       INTEGER,
    parent            TEXT,
    qualified_name    TEXT,
    language          TEXT,
    decorators        TEXT,
    keywords          TEXT,
    content_hash      TEXT,
    ecosystem_context TEXT,
    data              TEXT
);

CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);

CREATE TABLE IF NOT EXISTS files (
    path       TEXT PRIMARY KEY,
    hash       TEXT,
    mtime_ns   INTEGER,
    language   TEXT,
    summary    TEXT,
    blob_sha   TEXT,
    imports    TEXT
);
"""

# Pragmas set on every connection open
_PRAGMAS = [
    "PRAGMA synchronous = NORMAL",
    "PRAGMA wal_autocheckpoint = 1000",
    "PRAGMA cache_size = -8000",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA mmap_size = 268435456",   # 256 MB memory-mapped I/O
    "PRAGMA temp_store = MEMORY",
]

# Pragmas set only once per database file (persistent after first set)
_INIT_PRAGMAS = [
    "PRAGMA journal_mode = WAL",
]

# Keys stored in the meta table
_META_KEYS = [
    "repo", "owner", "name", "indexed_at", "index_version",
    "git_head", "source_root", "display_name",
    "languages", "context_metadata",
]

# Lazily initialised to avoid circular import with index_store.
# None = not yet loaded; any accidental read before _ensure_index_store_deps()
# fires raises TypeError("'>' not supported between 'NoneType' and 'int'").
_INDEX_VERSION: Optional[int] = None
_file_hash: Callable[[str], str] = lambda x: ""


def _ensure_index_store_deps() -> None:
    global _INDEX_VERSION, _file_hash
    if _INDEX_VERSION is None:
        from .index_store import INDEX_VERSION, _file_hash as _fh
        _INDEX_VERSION = INDEX_VERSION
        _file_hash = _fh


# ── In-memory CodeIndex cache ──────────────────────────────────────
# Mirrors the old @functools.lru_cache(maxsize=16) on JSON load.
# Module-level because every tool creates a new IndexStore() per call.
# Thread-safe: watcher runs incremental_save from a background thread.

class _CacheEntry(NamedTuple):
    mtime_ns: int
    code_index: "CodeIndex"


_index_cache: OrderedDict[tuple[str, str], _CacheEntry] = OrderedDict()
_cache_lock = threading.Lock()
_CACHE_MAX_SIZE = 16


def _cache_get(owner: str, name: str, mtime_ns: int) -> Optional["CodeIndex"]:
    """Return cached CodeIndex if fresh, else None."""
    key = (owner, name)
    with _cache_lock:
        entry = _index_cache.get(key)
        if entry is not None and entry.mtime_ns == mtime_ns:
            _index_cache.move_to_end(key)  # LRU touch
            return entry.code_index
    return None


def _cache_put(owner: str, name: str, mtime_ns: int, code_index: "CodeIndex") -> None:
    """Store a CodeIndex in the cache, evicting LRU if full."""
    key = (owner, name)
    with _cache_lock:
        _index_cache[key] = _CacheEntry(mtime_ns, code_index)
        _index_cache.move_to_end(key)
        while len(_index_cache) > _CACHE_MAX_SIZE:
            _index_cache.popitem(last=False)


def _cache_evict(owner: str, name: str) -> None:
    """Remove a specific repo from cache."""
    with _cache_lock:
        _index_cache.pop((owner, name), None)


def _cache_clear() -> None:
    """Clear entire index cache.

    Not called internally — provided for external callers (e.g. test teardown,
    future server-level invalidation). Per-repo eviction is handled by
    _cache_evict() which is wired into delete_index().
    """
    with _cache_lock:
        _index_cache.clear()


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    """Migrate a v4 database to v5: promote data JSON fields to real columns."""
    # Add new columns (IF NOT EXISTS not supported by ALTER TABLE, so check first)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()}
    new_cols = [
        ("qualified_name", "TEXT"),
        ("language", "TEXT"),
        ("decorators", "TEXT"),
        ("keywords", "TEXT"),
        ("content_hash", "TEXT"),
        ("ecosystem_context", "TEXT"),
    ]
    for col_name, col_type in new_cols:
        if col_name not in existing:
            conn.execute(f"ALTER TABLE symbols ADD COLUMN {col_name} {col_type}")

    conn.execute("BEGIN")
    # Populate new columns from data JSON
    conn.execute("""\
        UPDATE symbols SET
            qualified_name    = COALESCE(json_extract(data, '$.qualified_name'), name),
            language          = COALESCE(json_extract(data, '$.language'), ''),
            decorators        = COALESCE(json_extract(data, '$.decorators'), '[]'),
            keywords          = COALESCE(json_extract(data, '$.keywords'), '[]'),
            content_hash      = COALESCE(json_extract(data, '$.content_hash'), ''),
            ecosystem_context = COALESCE(json_extract(data, '$.ecosystem_context'), ''),
            data              = NULL
        WHERE data IS NOT NULL
    """)

    # Update version in meta
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("index_version", "5"),
    )
    conn.execute("COMMIT")
    logger.info("Migrated symbols table from v4 to v5 (promoted data fields to columns)")


class SQLiteIndexStore:
    """Storage backend using SQLite WAL for code indexes.

    One .db file per repo at {base_path}/{slug}.db.
    Content cache remains as individual files at {base_path}/{slug}/.
    """

    # Per-process set of DB paths that have had their schema initialised.
    # Skips the ~0.1 ms executescript() overhead on every subsequent connect.
    _initialized_dbs: set[str] = set()

    def __init__(self, base_path: Optional[str] = None) -> None:
        """Initialize store.

        Args:
            base_path: Base directory for storage. Defaults to ~/.code-index/
        """
        if base_path:
            self.base_path = Path(base_path)
        else:
            self.base_path = Path.home() / ".code-index"
        self.base_path.mkdir(parents=True, exist_ok=True)

    # ── Connection helpers ──────────────────────────────────────────

    def _db_path(self, owner: str, name: str) -> Path:
        """Path to the SQLite database file for a repo."""
        slug = self._repo_slug(owner, name)
        return self.base_path / f"{slug}.db"

    def _connect(self, db_path: Path) -> sqlite3.Connection:
        """Open a connection with WAL pragmas and schema ensured on first visit."""
        conn = sqlite3.connect(str(db_path), isolation_level=None)  # autocommit
        conn.row_factory = sqlite3.Row
        for pragma in _PRAGMAS:
            conn.execute(pragma)

        db_key = str(db_path)
        if db_key not in SQLiteIndexStore._initialized_dbs:
            # One-time pragmas (persistent on the db file)
            for pragma in _INIT_PRAGMAS:
                conn.execute(pragma)

            # Lightweight check: table_info returns column list; empty = not initialised.
            if not conn.execute("PRAGMA table_info(meta)").fetchall():
                conn.executescript(_SCHEMA_SQL)
            else:
                # Existing DB — check if v4→v5 migration is needed
                _ensure_index_store_deps()
                row = conn.execute(
                    "SELECT value FROM meta WHERE key = 'index_version'"
                ).fetchone()
                stored_version = int(row[0]) if row else 0
                if stored_version < 5:
                    _migrate_v4_to_v5(conn)

            SQLiteIndexStore._initialized_dbs.add(db_key)

        return conn

    def checkpoint_and_close(self, owner: str, name: str) -> None:
        """Compact WAL file on graceful shutdown. Call from server shutdown hook."""
        self.checkpoint_db(self._db_path(owner, name))

    def checkpoint_db(self, db_path: Path) -> None:
        """Checkpoint and close a WAL database by path.

        Unlike checkpoint_and_close(), this does not require owner/name
        parsing — useful when iterating *.db files directly.
        """
        if not db_path.exists():
            return
        conn = self._connect(db_path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()

    def get_file_languages(self, owner: str, name: str) -> dict[str, str]:
        """Query only the files table for path→language mapping.
        Avoids loading the full index when only file_languages is needed."""
        db_path = self._db_path(owner, name)
        if not db_path.exists():
            return {}
        conn = self._connect(db_path)
        try:
            rows = conn.execute(
                "SELECT path, language FROM files WHERE language != ''"
            ).fetchall()
            return {r["path"]: r["language"] for r in rows}
        finally:
            conn.close()

    def get_symbol_by_id(self, owner: str, name: str, symbol_id: str) -> Optional[dict]:
        """Query a single symbol by ID directly from SQLite.
        Avoids loading the full index for get_symbol_content."""
        db_path = self._db_path(owner, name)
        if not db_path.exists():
            return None
        conn = self._connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM symbols WHERE id = ?", (symbol_id,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_symbol_dict(row)
        finally:
            conn.close()

    def has_file(self, owner: str, name: str, file_path: str) -> bool:
        """Check if a file exists in the index without loading the full index."""
        db_path = self._db_path(owner, name)
        if not db_path.exists():
            return False
        conn = self._connect(db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM files WHERE path = ?", (file_path,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    # ── Public API (mirrors IndexStore) ─────────────────────────────

    def save_index(
        self,
        owner: str,
        name: str,
        source_files: list[str],
        symbols: list[Symbol],
        raw_files: dict[str, str],
        languages: Optional[dict[str, int]] = None,
        file_hashes: Optional[dict[str, str]] = None,
        git_head: str = "",
        file_summaries: Optional[dict[str, str]] = None,
        source_root: str = "",
        file_languages: Optional[dict[str, str]] = None,
        display_name: str = "",
        imports: Optional[dict[str, list[dict]]] = None,
        context_metadata: Optional[dict] = None,
        file_blob_shas: Optional[dict[str, str]] = None,
        file_mtimes: Optional[dict[str, int]] = None,
    ) -> "CodeIndex":
        """Save a full index to SQLite. Replaces all existing data."""
        _ensure_index_store_deps()
        from .index_store import CodeIndex

        normalized_source_files = sorted(dict.fromkeys(source_files or list(raw_files.keys())))

        if file_hashes is None:
            file_hashes = {fp: _file_hash(content) for fp, content in raw_files.items()}

        # Serialize symbols
        serialized_symbols = [
            {"id": s.id, "file": s.file, "name": s.name, "qualified_name": s.qualified_name,
             "kind": s.kind, "language": s.language, "signature": s.signature,
             "docstring": s.docstring, "summary": s.summary, "decorators": s.decorators,
             "keywords": s.keywords, "parent": s.parent, "line": s.line,
             "end_line": s.end_line, "byte_offset": s.byte_offset,
             "byte_length": s.byte_length, "content_hash": s.content_hash}
            for s in symbols
        ]

        # Compute languages from file_languages if not provided
        file_languages = file_languages or {}
        if not languages and file_languages:
            lang_counts: dict[str, int] = {}
            for lang in file_languages.values():
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
            languages = lang_counts

        index = CodeIndex(
            repo=f"{owner}/{name}", owner=owner, name=name,
            indexed_at=datetime.now().isoformat(),
            source_files=normalized_source_files,
            languages=languages or {},
            symbols=serialized_symbols,
            index_version=cast(int, _INDEX_VERSION),
            file_hashes=file_hashes,
            git_head=git_head,
            file_summaries=file_summaries or {},
            source_root=source_root,
            file_languages=file_languages,
            display_name=display_name or name,
            imports=imports if imports is not None else {},
            context_metadata=context_metadata or {},
            file_blob_shas=file_blob_shas or {},
            file_mtimes=file_mtimes or {},
        )

        db_path = self._db_path(owner, name)
        conn = self._connect(db_path)
        try:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM symbols")
            conn.execute("DELETE FROM files")
            conn.execute("DELETE FROM meta")

            self._write_meta(conn, index)

            # Insert symbols
            conn.executemany(
                "INSERT INTO symbols (id, file, name, kind, signature, summary, "
                "docstring, line, end_line, byte_offset, byte_length, parent, "
                "qualified_name, language, decorators, keywords, content_hash, "
                "ecosystem_context, data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [self._symbol_to_row(s) for s in symbols],
            )

            # Insert files (batch via executemany)
            conn.executemany(
                "INSERT OR REPLACE INTO files (path, hash, mtime_ns, language, "
                "summary, blob_sha, imports) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        fp,
                        file_hashes.get(fp, ""),
                        (file_mtimes or {}).get(fp),
                        (file_languages or {}).get(fp, ""),
                        (file_summaries or {}).get(fp, ""),
                        (file_blob_shas or {}).get(fp, ""),
                        json.dumps((imports or {}).get(fp, [])),
                    )
                    for fp in normalized_source_files
                ],
            )

            conn.commit()
        finally:
            conn.close()

        # Write raw content files
        content_dir = self._content_dir(owner, name)
        content_dir.mkdir(parents=True, exist_ok=True)
        for file_path, content in raw_files.items():
            file_dest = self._safe_content_path(content_dir, file_path)
            if not file_dest:
                raise ValueError(f"Unsafe file path in raw_files: {file_path}")
            file_dest.parent.mkdir(parents=True, exist_ok=True)
            self._write_cached_text(file_dest, content)

        # Pre-warm cache so the next load_index() is instant
        # Use safe_name to match the key used by load_index's _cache_get
        safe_name = self._safe_repo_component(name, "name")
        _cache_put(owner, safe_name, db_path.stat().st_mtime_ns, index)
        return index

    def load_index(self, owner: str, name: str) -> Optional["CodeIndex"]:
        """Load index from SQLite, constructing a CodeIndex dataclass."""
        _ensure_index_store_deps()
        # Sanitize name so "my project (v2)" maps to the same .db as save_index
        safe_name = self._safe_repo_component(name, "name")
        db_path = self._db_path(owner, safe_name)
        if not db_path.exists():
            return None

        # Check in-memory cache (mirrors old @lru_cache on JSON load)
        try:
            mtime_ns = db_path.stat().st_mtime_ns
        except OSError:
            return None  # file was deleted between exists() and stat()
        cached = _cache_get(owner, safe_name, mtime_ns)
        if cached is not None:
            return cached

        conn = self._connect(db_path)
        try:
            meta = self._read_meta(conn)
            if not meta:
                return None

            stored_version = int(meta.get("index_version", "0"))
            if stored_version > cast(int, _INDEX_VERSION):
                logger.warning("Index version %d > current %d for %s/%s", stored_version, _INDEX_VERSION, owner, name)
                return None

            symbol_rows = conn.execute("SELECT * FROM symbols").fetchall()
            file_rows = conn.execute("SELECT * FROM files").fetchall()

            index = self._build_index_from_rows(meta, symbol_rows, file_rows, owner, name)
        finally:
            conn.close()

        # Populate cache (re-stat to capture any WAL checkpoint mtime change)
        try:
            post_mtime_ns = db_path.stat().st_mtime_ns
        except OSError:
            post_mtime_ns = mtime_ns  # file gone; cache with pre-load mtime
        _cache_put(owner, safe_name, post_mtime_ns, index)
        return index

    def has_index(self, owner: str, name: str) -> bool:
        """Return True if a .db file exists for this repo."""
        safe_name = self._safe_repo_component(name, "name")
        return self._db_path(owner, safe_name).exists()

    def incremental_save(
        self,
        owner: str,
        name: str,
        changed_files: list[str],
        new_files: list[str],
        deleted_files: list[str],
        new_symbols: list[Symbol],
        raw_files: dict[str, str],
        languages: Optional[dict[str, int]] = None,
        git_head: str = "",
        file_summaries: Optional[dict[str, str]] = None,
        file_languages: Optional[dict[str, str]] = None,
        imports: Optional[dict[str, list[dict]]] = None,
        context_metadata: Optional[dict] = None,
        file_blob_shas: Optional[dict[str, str]] = None,
        file_hashes: Optional[dict[str, str]] = None,
        file_mtimes: Optional[dict[str, int]] = None,
    ) -> Optional["CodeIndex"]:
        """Incrementally update an existing index (delta write)."""
        db_path = self._db_path(owner, name)
        if not db_path.exists():
            return None

        # Grab old CodeIndex from cache BEFORE DB write changes mtime.
        # Used below to carry forward cached _tokens for unchanged symbols.
        safe_name = self._safe_repo_component(name, "name")
        old_index = None
        try:
            old_mtime = db_path.stat().st_mtime_ns
            old_index = _cache_get(owner, safe_name, old_mtime)
        except OSError:
            pass

        conn = self._connect(db_path)
        try:
            conn.execute("BEGIN")

            # Delete symbols for changed + deleted files
            files_to_remove = list(set(deleted_files) | set(changed_files))
            if files_to_remove:
                placeholders = ",".join("?" * len(files_to_remove))
                conn.execute(f"DELETE FROM symbols WHERE file IN ({placeholders})", files_to_remove)

            # Preserve existing hash/mtime for changed files before deleting them
            preserved: dict[str, dict] = {}
            if changed_files:
                placeholders = ",".join("?" * len(changed_files))
                rows = conn.execute(
                    f"SELECT path, hash, mtime_ns FROM files WHERE path IN ({placeholders})",
                    changed_files,
                ).fetchall()
                for r in rows:
                    preserved[r["path"]] = {"hash": r["hash"] or "", "mtime_ns": r["mtime_ns"]}

            # Delete file records for deleted files
            if deleted_files:
                placeholders = ",".join("?" * len(deleted_files))
                conn.execute(f"DELETE FROM files WHERE path IN ({placeholders})", deleted_files)

            # Insert new symbols
            if new_symbols:
                conn.executemany(
                    "INSERT OR REPLACE INTO symbols (id, file, name, kind, signature, summary, "
                    "docstring, line, end_line, byte_offset, byte_length, parent, "
                    "qualified_name, language, decorators, keywords, content_hash, "
                    "ecosystem_context, data) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [self._symbol_to_row(s) for s in new_symbols],
                )

            # Update file records for changed + new files
            changed_or_new = sorted(set(changed_files) | set(new_files))
            for fp in changed_or_new:
                # Prefer caller-supplied values; fall back to preserved (for changed files)
                # or empty (for truly new files)
                inp_hashes = file_hashes or {}
                inp_mtimes = file_mtimes or {}
                existing = preserved.get(fp, {})
                conn.execute(
                    "INSERT OR REPLACE INTO files (path, hash, mtime_ns, language, "
                    "summary, blob_sha, imports) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        fp,
                        inp_hashes.get(fp, existing.get("hash", "")),
                        inp_mtimes.get(fp, existing.get("mtime_ns")),
                        (file_languages or {}).get(fp, ""),
                        (file_summaries or {}).get(fp, ""),
                        (file_blob_shas or {}).get(fp, ""),
                        json.dumps((imports or {}).get(fp, [])),
                    ),
                )

            # Update meta
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("indexed_at", datetime.now().isoformat()),
            )
            if git_head:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    ("git_head", git_head),
                )
            if context_metadata is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    ("context_metadata", json.dumps(context_metadata)),
                )

            # Recompute languages from files table
            lang_rows = conn.execute(
                "SELECT language, COUNT(*) as cnt FROM files WHERE language != '' GROUP BY language"
            ).fetchall()
            computed_langs = {r["language"]: r["cnt"] for r in lang_rows}
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("languages", json.dumps(computed_langs)),
            )

            # Update mtime for files whose mtime changed but content didn't
            # (not in changed_or_new, not deleted — mtime-only drift e.g. `touch file.py`)
            # Without this, the mtime fast-path would never apply for these files on
            # subsequent cycles: old mtime stays in DB → perpetual re-hash.
            if file_mtimes:
                changed_or_new_set = set(changed_or_new)
                deleted_set = set(deleted_files)
                mtime_only = [
                    (mt, fp) for fp, mt in file_mtimes.items()
                    if fp not in changed_or_new_set and fp not in deleted_set
                ]
                if mtime_only:
                    conn.executemany("UPDATE files SET mtime_ns = ? WHERE path = ?", mtime_only)

            # Fetch final state BEFORE commit — same transaction, no second round-trip
            all_symbol_rows = conn.execute("SELECT * FROM symbols").fetchall()
            all_file_rows = conn.execute("SELECT * FROM files").fetchall()
            meta = self._read_meta(conn)

            conn.commit()
        finally:
            conn.close()

        # Update content cache
        content_dir = self._content_dir(owner, name)
        content_dir.mkdir(parents=True, exist_ok=True)
        for fp in deleted_files:
            dead = self._safe_content_path(content_dir, fp)
            if dead and dead.exists():
                dead.unlink()
        for fp, content in raw_files.items():
            dest = self._safe_content_path(content_dir, fp)
            if not dest:
                raise ValueError(f"Unsafe file path: {fp}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            self._write_cached_text(dest, content)

        # Build CodeIndex from already-fetched rows (no second round-trip)
        index = self._build_index_from_rows(meta, all_symbol_rows, all_file_rows, owner, name)

        # Carry forward cached BM25 token bags from unchanged symbols.
        # Matched by symbol id; content_hash must match on both sides to
        # guarantee the symbol text is identical. If either hash is missing,
        # we can't verify — skip to avoid stale tokens.
        if old_index is not None:
            old_sym_map = {}
            for sym in old_index.symbols:
                tokens = sym.get("_tokens")
                ch = sym.get("content_hash")
                if tokens is not None and ch:
                    old_sym_map[sym["id"]] = (ch, sym)
            if old_sym_map:
                for sym in index.symbols:
                    old = old_sym_map.get(sym["id"])
                    if old is None:
                        continue
                    old_hash, old_sym = old
                    new_hash = sym.get("content_hash")
                    if new_hash and new_hash == old_hash:
                        sym["_tokens"] = old_sym["_tokens"]
                        # Also carry forward pre-computed TF and dl if present
                        if "_tf" in old_sym:
                            sym["_tf"] = old_sym["_tf"]
                        if "_dl" in old_sym:
                            sym["_dl"] = old_sym["_dl"]

        # Pre-warm cache so the next load_index() is instant
        _cache_put(owner, safe_name, db_path.stat().st_mtime_ns, index)
        return index

    def detect_changes_with_mtimes(
        self,
        owner: str,
        name: str,
        current_mtimes: dict[str, int],
        hash_fn: Callable[[str], str],
    ) -> tuple[list[str], list[str], list[str], dict[str, str], dict[str, int]]:
        """Fast-path change detection using mtimes, falling back to hash.

        Note: Files stored with an empty/NULL hash in the DB are excluded from
        change detection. Since a missing hash means the file was not fully
        indexed (e.g., content was never cached), it is treated as if it does
        not exist in the DB and will be re-indexed as a "new file" on the next
        run. This is safe by design — an unindexed file is indistinguishable
        from a deleted file for the purposes of incremental updates."""
        db_path = self._db_path(owner, name)
        if not db_path.exists():
            # No existing index — all files are new, hash them all.
            hashes: dict[str, str] = {}
            for fp in current_mtimes:
                hashes[fp] = hash_fn(fp)
            return [], list(current_mtimes.keys()), [], hashes, dict(current_mtimes)

        conn = self._connect(db_path)
        try:
            rows = conn.execute("SELECT path, hash, mtime_ns FROM files").fetchall()
        finally:
            conn.close()

        old_hashes = {r["path"]: r["hash"] for r in rows if r["hash"]}
        old_mtimes = {r["path"]: r["mtime_ns"] for r in rows if r["mtime_ns"] is not None}

        old_set = set(old_hashes.keys())
        new_set = set(current_mtimes.keys())

        new_files = sorted(new_set - old_set)
        deleted_files = sorted(old_set - new_set)

        changed_files: list[str] = []
        computed_hashes: dict[str, str] = {}
        updated_mtimes: dict[str, int] = {}

        # Check files present in both old and new indexes.
        for fp in sorted(old_set & new_set):
            cur_mtime = current_mtimes[fp]
            old_mtime = old_mtimes.get(fp)

            if old_mtime is not None and cur_mtime == old_mtime:
                # mtime unchanged — skip hash, file is unchanged.
                updated_mtimes[fp] = cur_mtime
                continue

            # mtime differs (or no stored mtime) — compute hash to verify.
            h = hash_fn(fp)
            if h != old_hashes[fp]:
                changed_files.append(fp)
                computed_hashes[fp] = h
            # Update mtime regardless.
            updated_mtimes[fp] = cur_mtime

        # Hash all new files.
        for fp in new_files:
            computed_hashes[fp] = hash_fn(fp)
            updated_mtimes[fp] = current_mtimes[fp]

        return changed_files, new_files, deleted_files, computed_hashes, updated_mtimes

    def detect_changes(
        self,
        owner: str,
        name: str,
        current_files: dict[str, str],
    ) -> tuple[list[str], list[str], list[str]]:
        """Detect changed, new, and deleted files by comparing hashes."""
        _ensure_index_store_deps()
        current_hashes = {fp: _file_hash(content) for fp, content in current_files.items()}
        return self.detect_changes_from_hashes(owner, name, current_hashes)

    def detect_changes_from_hashes(
        self,
        owner: str,
        name: str,
        current_hashes: dict[str, str],
    ) -> tuple[list[str], list[str], list[str]]:
        """Detect changes from precomputed hashes."""
        db_path = self._db_path(owner, name)
        if not db_path.exists():
            return [], list(current_hashes.keys()), []

        conn = self._connect(db_path)
        try:
            rows = conn.execute("SELECT path, hash FROM files").fetchall()
        finally:
            conn.close()

        old_hashes = {r["path"]: r["hash"] for r in rows if r["hash"]}

        old_set = set(old_hashes.keys())
        new_set = set(current_hashes.keys())

        new_files = list(new_set - old_set)
        deleted_files = list(old_set - new_set)
        changed_files = [
            fp for fp in (old_set & new_set)
            if old_hashes[fp] != current_hashes[fp]
        ]

        return changed_files, new_files, deleted_files

    def list_repos(self) -> list[dict]:
        """List all indexed repositories (scans .db files only)."""
        repos = []
        for db_file in self.base_path.glob("*.db"):
            try:
                entry = self._list_repo_from_db(db_file)
                if entry:
                    repos.append(entry)
            except Exception:
                logger.debug("Skipping corrupted DB: %s", db_file, exc_info=True)
        repos.sort(key=lambda repo: repo["repo"])
        return repos

    def _list_repo_from_db(self, db_path: Path) -> Optional[dict]:
        """Read repo metadata from a .db file for list_repos."""
        conn = self._connect(db_path)
        try:
            meta = self._read_meta(conn)
            symbol_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        finally:
            conn.close()

        if not meta:
            return None
        languages = json.loads(meta.get("languages", "{}"))
        return {
            "repo": meta.get("repo", ""),
            "indexed_at": meta.get("indexed_at", ""),
            "symbol_count": symbol_count,
            "file_count": file_count,
            "languages": languages,
            "index_version": int(meta.get("index_version", "0")),
            "git_head": meta.get("git_head", ""),
            "display_name": meta.get("display_name", ""),
            "source_root": meta.get("source_root", ""),
        }

    def delete_index(self, owner: str, name: str) -> bool:
        """Delete a repo's .db, .db-wal, .db-shm, and content dir."""
        safe_name = self._safe_repo_component(name, "name")
        _cache_evict(owner, safe_name)
        db_path = self._db_path(owner, name)
        deleted = False

        if db_path.exists():
            db_path.unlink()
            deleted = True
            SQLiteIndexStore._initialized_dbs.discard(str(db_path))

        wal_path = Path(str(db_path) + "-wal")
        if wal_path.exists():
            wal_path.unlink()
            deleted = True

        shm_path = Path(str(db_path) + "-shm")
        if shm_path.exists():
            shm_path.unlink()
            deleted = True

        content_dir = self._content_dir(owner, name)
        if content_dir.exists():
            shutil.rmtree(content_dir)
            deleted = True

        return deleted

    def get_symbol_content(
        self, owner: str, name: str, symbol_id: str,
        _index: Optional["CodeIndex"] = None,
    ) -> Optional[str]:
        """Read symbol source using stored byte offsets from content cache."""
        if _index is not None:
            sym_dict = _index.get_symbol(symbol_id)
            if sym_dict is None:
                return None
        else:
            sym_dict = self.get_symbol_by_id(owner, name, symbol_id)
            if sym_dict is None:
                return None

        file_path = self._safe_content_path(self._content_dir(owner, name), sym_dict["file"])
        if not file_path or not file_path.exists():
            return None

        with open(file_path, "rb") as f:
            f.seek(sym_dict["byte_offset"])
            source_bytes = f.read(sym_dict["byte_length"])

        return source_bytes.decode("utf-8", errors="replace")

    def get_file_content(
        self, owner: str, name: str, file_path: str,
        _index: Optional["CodeIndex"] = None,
    ) -> Optional[str]:
        """Read a cached file's full content."""
        if _index is not None:
            if not _index.has_source_file(file_path):
                return None
        else:
            if not self.has_file(owner, name, file_path):
                return None

        content_path = self._safe_content_path(self._content_dir(owner, name), file_path)
        if not content_path or not content_path.exists():
            return None

        return self._read_cached_text(content_path)

    # ── Content cache helpers (reused from IndexStore) ──────────────

    def _content_dir(self, owner: str, name: str) -> Path:
        """Path to raw content directory."""
        return self.base_path / self._repo_slug(owner, name)

    def _safe_content_path(self, content_dir: Path, relative_path: str) -> Optional[Path]:
        """Resolve a content path and ensure it stays within content_dir."""
        try:
            base = content_dir.resolve()
            candidate = (content_dir / relative_path).resolve()
            if os.path.commonpath([str(base), str(candidate)]) != str(base):
                return None
            return candidate
        except (OSError, ValueError):
            return None

    def _write_cached_text(self, path: Path, content: str) -> None:
        """Write cached text without newline translation."""
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)

    def _read_cached_text(self, path: Path) -> Optional[str]:
        """Read cached text without newline normalization."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                return f.read()
        except OSError:
            return None

    # ── Internal helpers ────────────────────────────────────────────

    def _symbol_to_row(self, symbol: Symbol) -> tuple:
        """Convert a Symbol to a row tuple for INSERT (v5 schema)."""
        return (
            symbol.id, symbol.file, symbol.name, symbol.kind,
            symbol.signature, symbol.summary, symbol.docstring,
            symbol.line, symbol.end_line,
            symbol.byte_offset, symbol.byte_length,
            symbol.parent,
            symbol.qualified_name,
            symbol.language,
            json.dumps(symbol.decorators) if symbol.decorators else "[]",
            json.dumps(symbol.keywords) if symbol.keywords else "[]",
            symbol.content_hash,
            getattr(symbol, "ecosystem_context", ""),
            None,  # data column — no longer used in v5
        )

    def _symbol_dict_to_row(self, d: dict) -> tuple:
        """Convert a serialized symbol dict to a row tuple for INSERT (v5 schema)."""
        decorators = d.get("decorators", [])
        keywords = d.get("keywords", [])
        return (
            d["id"], d["file"], d["name"], d.get("kind", ""),
            d.get("signature", ""), d.get("summary", ""), d.get("docstring", ""),
            d.get("line", 0), d.get("end_line", 0),
            d.get("byte_offset", 0), d.get("byte_length", 0),
            d.get("parent"),
            d.get("qualified_name", d.get("name", "")),
            d.get("language", ""),
            json.dumps(decorators) if decorators else "[]",
            json.dumps(keywords) if keywords else "[]",
            d.get("content_hash", ""),
            d.get("ecosystem_context", ""),
            None,  # data column — no longer used in v5
        )

    def _row_to_symbol_dict(self, row: sqlite3.Row) -> dict:
        """Convert a database row to a symbol dict (matches CodeIndex.symbols format)."""
        # v5: read directly from columns. Fallback to data JSON for mid-migration rows.
        if row["data"]:
            # Legacy v4 row (data not yet migrated) — parse JSON
            data = json.loads(row["data"])
            qualified_name = data.get("qualified_name", row["name"])
            language = data.get("language", "")
            decorators = data.get("decorators", [])
            keywords = data.get("keywords", [])
            content_hash = data.get("content_hash", "")
            ecosystem_context = data.get("ecosystem_context", "")
        else:
            # v5 row — direct column reads, no JSON parsing
            qualified_name = row["qualified_name"] or row["name"]
            language = row["language"] or ""
            deco_raw = row["decorators"]
            decorators = json.loads(deco_raw) if deco_raw and deco_raw != "[]" else []
            kw_raw = row["keywords"]
            keywords = json.loads(kw_raw) if kw_raw and kw_raw != "[]" else []
            content_hash = row["content_hash"] or ""
            ecosystem_context = row["ecosystem_context"] or ""
        return {
            "id": row["id"],
            "file": row["file"],
            "name": row["name"],
            "kind": row["kind"] or "",
            "signature": row["signature"] or "",
            "summary": row["summary"] or "",
            "docstring": row["docstring"] or "",
            "qualified_name": qualified_name,
            "language": language,
            "decorators": decorators,
            "keywords": keywords,
            "parent": row["parent"],
            "line": row["line"] or 0,
            "end_line": row["end_line"] or 0,
            "byte_offset": row["byte_offset"] or 0,
            "byte_length": row["byte_length"] or 0,
            "content_hash": content_hash,
            "ecosystem_context": ecosystem_context,
        }

    def _build_index_from_rows(
        self, meta: dict, symbol_rows: list, file_rows: list, owner: str, name: str,
    ) -> "CodeIndex":
        """Build a CodeIndex from pre-fetched meta dict, symbol rows, and file rows.
        Used by both load_index and incremental_save to avoid redundant queries."""
        from .index_store import CodeIndex

        symbols = [self._row_to_symbol_dict(r) for r in symbol_rows]

        # Single pass over file_rows to build all file-level dicts
        source_files_unsorted: list[str] = []
        file_hashes: dict[str, str] = {}
        file_mtimes: dict[str, int] = {}
        file_languages: dict[str, str] = {}
        file_summaries: dict[str, str] = {}
        file_blob_shas: dict[str, str] = {}
        imports: Optional[dict[str, list[dict]]] = {}
        for r in file_rows:
            p = r["path"]
            source_files_unsorted.append(p)
            if r["hash"]:
                file_hashes[p] = r["hash"]
            if r["mtime_ns"] is not None:
                file_mtimes[p] = r["mtime_ns"]
            if r["language"]:
                file_languages[p] = r["language"]
            if r["summary"]:
                file_summaries[p] = r["summary"]
            if r["blob_sha"]:
                file_blob_shas[p] = r["blob_sha"]
            if r["imports"]:
                parsed = json.loads(r["imports"])
                if parsed:
                    imports[p] = parsed
        source_files = sorted(source_files_unsorted)
        if not imports:
            # v3 format had no imports field — preserve None for backward compatibility
            index_version = int(meta.get("index_version", "0"))
            imports = None if index_version < 4 else {}

        languages = json.loads(meta.get("languages", "{}"))
        context_metadata = json.loads(meta.get("context_metadata", "{}"))

        return CodeIndex(
            repo=meta.get("repo", f"{owner}/{name}"),
            owner=meta.get("owner", owner),
            name=meta.get("name", name),
            indexed_at=meta.get("indexed_at", ""),
            source_files=source_files,
            languages=languages,
            symbols=symbols,
            index_version=int(meta.get("index_version", "0")),
            file_hashes=file_hashes,
            git_head=meta.get("git_head", ""),
            file_summaries=file_summaries,
            source_root=meta.get("source_root", ""),
            file_languages=file_languages,
            display_name=meta.get("display_name", name),
            imports=imports,
            context_metadata=context_metadata,
            file_blob_shas=file_blob_shas,
            file_mtimes=file_mtimes,
        )

    def _write_meta(self, conn: sqlite3.Connection, index: "CodeIndex") -> None:
        """Write all meta keys for an index."""
        _ensure_index_store_deps()
        meta = {
            "repo": index.repo,
            "owner": index.owner,
            "name": index.name,
            "indexed_at": index.indexed_at,
            "index_version": str(index.index_version),
            "git_head": index.git_head,
            "source_root": index.source_root,
            "display_name": index.display_name,
            "languages": json.dumps(index.languages),
            "context_metadata": json.dumps(index.context_metadata or {}),
        }
        conn.executemany(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            list(meta.items()),
        )

    def _read_meta(self, conn: sqlite3.Connection) -> dict:
        """Read all meta keys into a dict."""
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def _file_languages_for_paths(
        self,
        paths: list[str],
        symbols: list[dict],
        existing: Optional[dict[str, str]] = None,
    ) -> dict[str, str]:
        """Fill file -> language for the given paths using symbols then extension fallback."""
        result = dict(existing) if existing else {}
        sym_by_file: dict[str, list[dict]] = {}
        for sym in symbols:
            sym_by_file.setdefault(sym.get("file", ""), []).append(sym)
        for path in paths:
            if path in result:
                continue
            file_syms = sym_by_file.get(path, [])
            if file_syms:
                lang = file_syms[0].get("language", "")
                if lang:
                    result[path] = lang
        if len(result) < len(paths):
            ext_map = {
                ".py": "python", ".js": "javascript", ".ts": "typescript",
                ".jsx": "javascript", ".tsx": "typescript", ".go": "go",
                ".rs": "rust", ".java": "java", ".c": "c", ".cpp": "cpp",
                ".h": "cpp", ".cs": "csharp", ".swift": "swift",
                ".rb": "ruby", ".php": "php", ".dart": "dart",
                ".kt": "kotlin", ".scala": "scala", ".lua": "lua",
                ".r": "r", ".m": "objective-c", ".mm": "objective-cpp",
                ".sh": "bash", ".bash": "bash", ".zsh": "zsh",
                ".sql": "sql", ".xml": "xml", ".html": "html",
                ".css": "css", ".scss": "scss", ".less": "less",
                ".json": "json", ".yaml": "yaml", ".yml": "yaml",
                ".toml": "toml", ".md": "markdown", ".rst": "rst",
                ".sh": "bash", ".ps1": "powershell",
            }
            for path in paths:
                if path in result:
                    continue
                ext = os.path.splitext(path)[1].lower()
                lang = ext_map.get(ext, "")
                if lang:
                    result[path] = lang
        return result

    def _languages_from_file_languages(self, file_languages: dict[str, str]) -> dict[str, int]:
        """Compute language -> file count from stored file language metadata."""
        counts: dict[str, int] = {}
        for lang in file_languages.values():
            counts[lang] = counts.get(lang, 0) + 1
        return counts

    def _repo_slug(self, owner: str, name: str) -> str:
        """Stable slug for file paths (same as IndexStore._repo_slug)."""
        safe_owner = self._safe_repo_component(owner, "owner")
        safe_name = self._safe_repo_component(name, "name")
        return f"{safe_owner}-{safe_name}"

    def _safe_repo_component(self, value: str, field_name: str) -> str:
        """Validate/sanitize owner/name for filesystem paths (matches IndexStore._safe_repo_component)."""
        import re

        if not value or value in {".", ".."}:
            raise ValueError(f"Invalid {field_name}: {value!r}")
        if "/" in value or "\\" in value:
            raise ValueError(f"Path separator in {field_name}: {value!r}")
        sanitized = re.sub(r"[^A-Za-z0-9._-]", "-", value)
        sanitized = re.sub(r"-+", "-", sanitized).strip("-")
        if not sanitized:
            raise ValueError(f"Invalid {field_name}: sanitized to empty string")
        return sanitized

    # ── Migration ───────────────────────────────────────────────────

    def migrate_from_json(self, json_path: Path, owner: str, name: str) -> Optional["CodeIndex"]:
        """Read a JSON index file and populate the SQLite database."""
        _ensure_index_store_deps()
        if not json_path.exists():
            return None

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            logger.warning("Failed to read JSON index for migration: %s", json_path)
            return None

        # Schema validation: require essential fields (matches original load_index)
        if not isinstance(data, dict) or "indexed_at" not in data:
            logger.warning(
                "Migration schema validation failed for %s/%s — missing required fields",
                owner, name,
            )
            return None

        source_files = data.get("source_files", [])
        symbols = data.get("symbols", [])
        raw_file_languages = data.get("file_languages", {})

        # Backfill file_languages from symbols (same as original load_index)
        if not raw_file_languages:
            merged_fl = self._file_languages_for_paths(
                source_files, symbols, existing=None,
            )
        else:
            merged_fl = dict(raw_file_languages)

        # Compute languages from file_languages (same as original load_index)
        computed_languages = self._languages_from_file_languages(merged_fl)
        if not computed_languages:
            computed_languages = data.get("languages", {})

        # Preserve imports=None for pre-v1.3.0 indexes (v3 format had no imports field)
        has_imports_key = "imports" in data
        stored_imports = data.get("imports") if has_imports_key else None

        # Populate SQLite from JSON data
        db_path = self._db_path(owner, name)
        conn = self._connect(db_path)
        try:
            conn.execute("BEGIN")
            # Write meta
            meta_keys = {
                "repo": data.get("repo", f"{owner}/{name}"),
                "owner": data.get("owner", owner),
                "name": data.get("name", name),
                "indexed_at": data["indexed_at"],
                "index_version": str(data.get("index_version", _INDEX_VERSION)),
                "git_head": data.get("git_head", ""),
                "source_root": data.get("source_root", ""),
                "display_name": data.get("display_name", name),
                "languages": json.dumps(computed_languages),
                "context_metadata": json.dumps(data.get("context_metadata", {})),
            }
            conn.executemany(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                list(meta_keys.items()),
            )

            # Write symbols
            if symbols:
                conn.executemany(
                    "INSERT OR REPLACE INTO symbols (id, file, name, kind, signature, summary, "
                    "docstring, line, end_line, byte_offset, byte_length, parent, "
                    "qualified_name, language, decorators, keywords, content_hash, "
                    "ecosystem_context, data) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [self._symbol_dict_to_row(s) for s in symbols],
                )

            # Write files
            file_hashes = data.get("file_hashes", {})
            file_mtimes = data.get("file_mtimes", {})
            file_summaries = data.get("file_summaries", {})
            file_blob_shas = data.get("file_blob_shas", {})
            imports_map = data.get("imports", {})

            for fp in source_files:
                conn.execute(
                    "INSERT OR REPLACE INTO files (path, hash, mtime_ns, language, "
                    "summary, blob_sha, imports) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        fp,
                        file_hashes.get(fp, ""),
                        file_mtimes.get(fp),
                        merged_fl.get(fp, ""),
                        file_summaries.get(fp, ""),
                        file_blob_shas.get(fp, ""),
                        json.dumps(imports_map.get(fp, [])),
                    ),
                )

            conn.commit()
        finally:
            conn.close()

        # Rename original JSON to .migrated
        migrated_path = json_path.with_suffix(".json.migrated")
        json_path.rename(migrated_path)

        # Clean up sidecars (naming: {slug}.meta.json, {slug}.json.sha256, {slug}.json.lock)
        slug = json_path.stem  # e.g. "local-test-abc123"
        for sidecar_name in (f"{slug}.meta.json", f"{slug}.json.sha256", f"{slug}.json.lock"):
            sidecar = json_path.parent / sidecar_name
            sidecar.unlink(missing_ok=True)

        return self.load_index(owner, name)
