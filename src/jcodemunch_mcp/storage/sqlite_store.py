"""SQLite WAL storage backend for code indexes.

Replaces monolithic JSON files with per-repo SQLite databases.
WAL mode enables concurrent readers + single writer with delta writes.
"""

import json
import logging
import os
import platform
import shutil
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Callable, NamedTuple, Optional, cast

from ..parser.symbols import Symbol
from ..path_map import parse_path_map, remap

# Cache of base_path strings that have already had mkdir called — avoids
# a redundant CreateDirectoryW syscall on every tool call.
_VERIFIED_PATHS: set[str] = set()

if TYPE_CHECKING:
    from .index_store import CodeIndex

logger = logging.getLogger(__name__)

# Repos whose JSON index already failed migration schema validation this
# process. A stale/corrupt JSON index (e.g. a starter pack's `.pack/<id>`)
# never becomes loadable, so every load_index / eager-migrate path retries the
# migration and re-warns — once per indexed repo enumerated, which spams hard
# under `watch-all`. Warn once per (owner, name) instead.
_MIGRATION_SCHEMA_WARNED: set = set()

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
    data              TEXT,
    cyclomatic        INTEGER,
    max_nesting       INTEGER,
    param_count       INTEGER
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
    imports    TEXT,
    size_bytes INTEGER
);

CREATE TABLE IF NOT EXISTS branch_deltas (
    branch    TEXT NOT NULL,
    file      TEXT NOT NULL,
    action    TEXT NOT NULL,
    symbol_data TEXT,
    file_hash TEXT,
    file_mtime_ns INTEGER,
    file_language TEXT,
    file_summary TEXT,
    file_imports TEXT,
    file_size_bytes INTEGER,
    PRIMARY KEY (branch, file)
);

CREATE TABLE IF NOT EXISTS branch_meta (
    branch     TEXT PRIMARY KEY,
    git_head   TEXT,
    indexed_at TEXT,
    base_head  TEXT
);

CREATE TABLE IF NOT EXISTS runtime_calls (
    symbol_id   TEXT NOT NULL,
    source      TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    p50_ms      REAL,
    p95_ms      REAL,
    first_seen  TEXT,
    last_seen   TEXT,
    PRIMARY KEY (symbol_id, source)
);

CREATE INDEX IF NOT EXISTS idx_runtime_calls_last_seen ON runtime_calls(last_seen);

CREATE TABLE IF NOT EXISTS runtime_edges (
    caller_id   TEXT NOT NULL,
    callee_id   TEXT NOT NULL,
    source      TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    first_seen  TEXT,
    last_seen   TEXT,
    PRIMARY KEY (caller_id, callee_id, source)
);

CREATE INDEX IF NOT EXISTS idx_runtime_edges_callee ON runtime_edges(callee_id);

CREATE TABLE IF NOT EXISTS runtime_imports (
    import_id   TEXT NOT NULL,
    source      TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    first_seen  TEXT,
    last_seen   TEXT,
    PRIMARY KEY (import_id, source)
);

CREATE TABLE IF NOT EXISTS runtime_unmapped (
    file_path     TEXT,
    line_no       INTEGER,
    function_name TEXT,
    source        TEXT NOT NULL,
    count         INTEGER NOT NULL DEFAULT 0,
    last_seen     TEXT,
    PRIMARY KEY (file_path, line_no, function_name, source)
);

CREATE TABLE IF NOT EXISTS runtime_redaction_log (
    source           TEXT NOT NULL,
    pattern          TEXT NOT NULL,
    redaction_count  INTEGER NOT NULL DEFAULT 0,
    last_redacted    TEXT,
    PRIMARY KEY (source, pattern)
);

CREATE TABLE IF NOT EXISTS runtime_columns (
    model_name   TEXT NOT NULL,
    column_name  TEXT NOT NULL,
    source       TEXT NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0,
    first_seen   TEXT,
    last_seen    TEXT,
    PRIMARY KEY (model_name, column_name, source)
);

CREATE INDEX IF NOT EXISTS idx_runtime_columns_model ON runtime_columns(model_name);
CREATE INDEX IF NOT EXISTS idx_runtime_columns_last_seen ON runtime_columns(last_seen);

CREATE TABLE IF NOT EXISTS runtime_stack_events (
    symbol_id   TEXT NOT NULL,
    source      TEXT NOT NULL,
    severity    TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    first_seen  TEXT,
    last_seen   TEXT,
    PRIMARY KEY (symbol_id, source, severity)
);

CREATE INDEX IF NOT EXISTS idx_runtime_stack_events_severity ON runtime_stack_events(severity, last_seen);
CREATE INDEX IF NOT EXISTS idx_runtime_stack_events_symbol ON runtime_stack_events(symbol_id);

CREATE TABLE IF NOT EXISTS scip_edges (
    from_symbol_id TEXT NOT NULL,
    to_symbol_id   TEXT NOT NULL,
    kind           TEXT NOT NULL,
    count          INTEGER NOT NULL DEFAULT 0,
    first_seen     TEXT,
    last_seen      TEXT,
    PRIMARY KEY (from_symbol_id, to_symbol_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_scip_edges_to ON scip_edges(to_symbol_id);

CREATE TABLE IF NOT EXISTS scip_unmapped (
    file_path    TEXT,
    line_no      INTEGER,
    scip_symbol  TEXT,
    reason       TEXT NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0,
    last_seen    TEXT,
    PRIMARY KEY (file_path, line_no, scip_symbol, reason)
);

CREATE TABLE IF NOT EXISTS scip_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Pragmas set on every connection open
_PRAGMAS = [
    "PRAGMA synchronous = NORMAL",
    "PRAGMA wal_autocheckpoint = 1000",
    # A long-lived reader (watcher, long agent session) can starve autocheckpoint
    # and grow the WAL unbounded; this bounds it so a successful checkpoint
    # reclaims the file back to 64 MB.
    "PRAGMA journal_size_limit = 67108864",
    "PRAGMA cache_size = -8000",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA mmap_size = 268435456",   # 256 MB memory-mapped I/O
    "PRAGMA temp_store = MEMORY",
]

# Pragmas set only once per database file (persistent after first set)
_INIT_PRAGMAS = [
    "PRAGMA journal_mode = WAL",
]

# SQLite files in ~/.code-index/ that are NOT per-repo indexes — list_repos
# must skip these so they don't get phantom-resolved as repos (and worse,
# get auto-initialised with the code-index schema by _connect()).
# org_savings.db is the team-SKU org-rollup store (see org/store.py); without
# this it surfaced as a bogus `local/org_savings` card (sym 0, no source_root)
# that could never be deleted.
_NON_REPO_DB_FILES = frozenset({"telemetry.db", "org_savings.db"})

# Directory name the starter-pack builder clones into on its CI machine
# (`build_pack.py`: tempfile.gettempdir() / "jcm-pack-clones" / <owner>-<repo>).
# That absolute path is baked into every shipped pack index's meta and exists
# on no user's machine, so `cleanup_orphan_indexes` classified each pack as an
# orphaned local repo and deleted it on the next server start (#419,
# @MotoMato85). Recognising it here is what lets a pack index be told apart
# from a genuinely vanished local clone.
_PACK_CLONE_DIR = "jcm-pack-clones"


def is_pack_clone_path(source_root: str) -> bool:
    """True if `source_root` points into the pack builder's clone directory.

    Matched on a PATH COMPONENT, not a substring: a user directory merely
    named e.g. `my-jcm-pack-clones-backup` must not be mistaken for one, and
    a component match is also what makes this correct for the Windows case
    where the POSIX `/tmp/...` value resolves against the current drive.
    """
    if not source_root:
        return False
    try:
        parts = PurePosixPath(source_root.replace("\\", "/")).parts
    except Exception:
        return False
    return _PACK_CLONE_DIR in parts

# Keys stored in the meta table
_META_KEYS = [
    "repo", "owner", "name", "indexed_at", "index_version",
    "git_head", "source_root", "git_root", "source_roots", "display_name",
    "languages", "context_metadata",
]

# Lazily initialised to avoid circular import with index_store.
# None = not yet loaded; any accidental read before _ensure_index_store_deps()
# fires raises TypeError("'>' not supported between 'NoneType' and 'int'").
_INDEX_VERSION: Optional[int] = None
_PARSER_GENERATION: Optional[int] = None
_file_hash: Callable[[str], str] = lambda x: ""


def _ensure_index_store_deps() -> None:
    global _INDEX_VERSION, _PARSER_GENERATION, _file_hash
    if _INDEX_VERSION is None:
        from .index_store import INDEX_VERSION, PARSER_GENERATION, _file_hash as _fh
        _INDEX_VERSION = INDEX_VERSION
        _PARSER_GENERATION = PARSER_GENERATION
        _file_hash = _fh


def _safe_json_load_list(raw: str) -> list[str]:
    """Decode a JSON-encoded list[str] from meta, returning [] on any error.

    Old indexes (v12 and earlier) wrote no `source_roots` row, so the meta
    read returns the default "[]" we hand it. New indexes write a real
    JSON array. Anything malformed (truncated/corrupted) degrades to []
    rather than crashing the load path.
    """
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str)]


# ── In-memory CodeIndex cache ──────────────────────────────────────
# Mirrors the old @functools.lru_cache(maxsize=16) on JSON load.
# Module-level because every tool creates a new IndexStore() per call.
# Thread-safe: watcher runs incremental_save from a background thread.

class _CacheEntry(NamedTuple):
    mtime_ns: int
    code_index: "CodeIndex"
    last_used: float


_index_cache: OrderedDict[tuple[str, str, str], _CacheEntry] = OrderedDict()
_cache_lock = threading.Lock()
_CACHE_MAX_SIZE = 32

# Idle eviction, OPT-IN and DISABLED by default (jcm#375 follow-up).
#
# A hydrated CodeIndex is large and, until now, was released only under LRU
# pressure. Two independent reports are the same fact: a 665k-symbol index put a
# worker at ~16 GiB (#370), and 20 abandoned stdio servers on one box held ~17 GB
# between them, because the client that spawned them never reaped them at session
# end. We cannot reap another program's children, but we can stop an idle process
# from sitting on indexes nobody is asking for.
#
# ⚠ This MUST NOT default on. Cold hydration of that 665k-symbol index was
# measured at 7.5 to 11.4 minutes (#370). A TTL that evicts during a quiet spell
# hands the next query that bill. Defaulting it on would fix the leaking box by
# breaking the large-index one. Operators whose client leaks servers opt in;
# everyone else is byte-identical to before.
_CACHE_TTL_ENV = "JCODEMUNCH_INDEX_CACHE_TTL"


def _cache_ttl_seconds() -> float:
    """Idle TTL in seconds. 0 or unparseable means disabled (the default)."""
    raw = os.environ.get(_CACHE_TTL_ENV, "")
    if not raw:
        return 0.0
    try:
        ttl = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return ttl if ttl > 0 else 0.0


def _evict_idle_locked(ttl: float, now: float) -> int:
    """Drop entries untouched for longer than ttl. Caller holds _cache_lock.

    Swept on access rather than by a timer thread: a leaked process makes no
    calls, so it needs no timer to stay small, and adding a thread to every
    server to solve a problem caused by too many servers would be perverse.
    The memory is released on the next call from ANY caller in the process.
    """
    if ttl <= 0:
        return 0
    stale = [k for k, e in _index_cache.items() if (now - e.last_used) > ttl]
    for key in stale:
        _index_cache.pop(key, None)
    return len(stale)


def _cache_get(owner: str, name: str, mtime_ns: int, branch: str = "") -> Optional["CodeIndex"]:
    """Return cached CodeIndex if fresh, else None."""
    key = (owner, name, branch)
    now = time.monotonic()
    with _cache_lock:
        _evict_idle_locked(_cache_ttl_seconds(), now)
        entry = _index_cache.get(key)
        if entry is not None and entry.mtime_ns == mtime_ns:
            _index_cache.move_to_end(key)  # LRU touch
            # Refresh idle timestamp so an actively used index is never evicted.
            _index_cache[key] = entry._replace(last_used=now)
            return entry.code_index
    return None


def _cache_put(owner: str, name: str, mtime_ns: int, code_index: "CodeIndex", branch: str = "") -> None:
    """Store a CodeIndex in the cache, evicting LRU if full."""
    key = (owner, name, branch)
    now = time.monotonic()
    with _cache_lock:
        _evict_idle_locked(_cache_ttl_seconds(), now)
        _index_cache[key] = _CacheEntry(mtime_ns, code_index, now)
        _index_cache.move_to_end(key)
        while len(_index_cache) > _CACHE_MAX_SIZE:
            _index_cache.popitem(last=False)


# Single-flight guards for cold index hydration (#370): concurrent cold
# callers against the same repo must not each independently read the full
# symbol table and build a CodeIndex — one hydrates, the rest wait and take
# the cached object. Keys mirror _index_cache; entries are tiny (one Lock
# per repo ever loaded in this process), so no eviction is needed.
_load_locks: dict[tuple[str, str, str], threading.Lock] = {}
_load_locks_guard = threading.Lock()


def _load_lock_for(key: tuple[str, str, str]) -> threading.Lock:
    with _load_locks_guard:
        lock = _load_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _load_locks[key] = lock
        return lock


def _cache_evict(owner: str, name: str) -> None:
    """Remove a specific repo from cache (all branches)."""
    with _cache_lock:
        keys_to_remove = [k for k in _index_cache if k[0] == owner and k[1] == name]
        for k in keys_to_remove:
            _index_cache.pop(k, None)


def _stamp_load_provenance(index, db_path: Path, mtime_ns: int):
    """Record which .db an index came from and its mtime at load time.

    Lets a retrieval verdict detect that the index was rewritten UNDERNEATH a
    scan (`retrieval.verdict.index_changed_since_load`). This is deliberately
    a filesystem signal rather than `reindex_state`: that module is in-memory
    and per-process, so a server answering a search cannot see a reindex run by
    a separate `watch-all` service — the common deployment. The .db/.db-wal
    mtime is visible across processes.
    """
    try:
        index._db_path = str(db_path)
        index._loaded_mtime_ns = int(mtime_ns)
    except Exception:  # pragma: no cover - defensive; never break a load
        logger.debug("Could not stamp load provenance", exc_info=True)
    return index


def _db_mtime_ns(db_path: Path) -> int:
    """Return the most recent mtime_ns between .db and .db-wal files.

    SQLite WAL mode may not update the .db file's mtime on every commit,
    so we check both files and return the maximum to ensure cache
    invalidation works correctly across processes.
    """
    db_mtime = db_path.stat().st_mtime_ns
    wal_path = Path(str(db_path) + "-wal")
    try:
        wal_mtime = wal_path.stat().st_mtime_ns
        return max(db_mtime, wal_mtime)
    except FileNotFoundError:
        return db_mtime


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


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """Migrate a v5 database to v6: add size_bytes column to files table."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(files)").fetchall()}
    if "size_bytes" not in existing:
        conn.execute("ALTER TABLE files ADD COLUMN size_bytes INTEGER")
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("index_version", "6"),
    )
    logger.info("Migrated files table from v5 to v6 (added size_bytes column)")


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    """Migrate a v6 database to v7: add complexity columns to symbols table."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()}
    for col_name in ("cyclomatic", "max_nesting", "param_count"):
        if col_name not in existing:
            conn.execute(f"ALTER TABLE symbols ADD COLUMN {col_name} INTEGER")
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("index_version", "7"),
    )
    logger.info("Migrated symbols table from v6 to v7 (added complexity columns)")


def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    """Migrate a v7 database to v8: call_references stored in data column as JSON array."""
    # v7 stored no call_references data; v8 needs it for AST-based call graphs.
    # We cannot reconstruct call references from the v7 schema without re-parsing sources.
    # Mark the index as requiring re-index for call graph features.
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("index_version", "8"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("call_refs_missing", "1"),
    )
    logger.warning(
        "Migrated v7→v8: call_references were not stored in v7. "
        "Call graph features (get_call_hierarchy, get_impact_preview, etc.) "
        "will use text heuristics. Run 'jcodemunch-mcp index-folder' to fully "
        "re-index and enable AST-based call graphs."
    )


def _migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
    """Migrate a v8 database to v9: add branch_deltas and branch_meta tables."""
    # Create branch tables if they don't exist (idempotent)
    conn.executescript("""\
        CREATE TABLE IF NOT EXISTS branch_deltas (
            branch    TEXT NOT NULL,
            file      TEXT NOT NULL,
            action    TEXT NOT NULL,
            symbol_data TEXT,
            file_hash TEXT,
            file_mtime_ns INTEGER,
            file_language TEXT,
            file_summary TEXT,
            file_imports TEXT,
            file_size_bytes INTEGER,
            PRIMARY KEY (branch, file)
        );
        CREATE TABLE IF NOT EXISTS branch_meta (
            branch     TEXT PRIMARY KEY,
            git_head   TEXT,
            indexed_at TEXT,
            base_head  TEXT
        );
    """)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("index_version", "9"),
    )
    logger.info("Migrated v8→v9: added branch_deltas and branch_meta tables")


def _migrate_v13_to_v14(conn: sqlite3.Connection) -> None:
    """Migrate a v13 database to v14: add runtime_* tables for trace ingestion (Phase 0).

    Tables are created empty; no existing rows are touched. Until a runtime
    signal is ingested (Phase 1+), the tables stay empty and are zero-cost.
    """
    conn.executescript("""\
        CREATE TABLE IF NOT EXISTS runtime_calls (
            symbol_id   TEXT NOT NULL,
            source      TEXT NOT NULL,
            count       INTEGER NOT NULL DEFAULT 0,
            p50_ms      REAL,
            p95_ms      REAL,
            first_seen  TEXT,
            last_seen   TEXT,
            PRIMARY KEY (symbol_id, source)
        );
        CREATE INDEX IF NOT EXISTS idx_runtime_calls_last_seen ON runtime_calls(last_seen);

        CREATE TABLE IF NOT EXISTS runtime_edges (
            caller_id   TEXT NOT NULL,
            callee_id   TEXT NOT NULL,
            source      TEXT NOT NULL,
            count       INTEGER NOT NULL DEFAULT 0,
            first_seen  TEXT,
            last_seen   TEXT,
            PRIMARY KEY (caller_id, callee_id, source)
        );
        CREATE INDEX IF NOT EXISTS idx_runtime_edges_callee ON runtime_edges(callee_id);

        CREATE TABLE IF NOT EXISTS runtime_imports (
            import_id   TEXT NOT NULL,
            source      TEXT NOT NULL,
            count       INTEGER NOT NULL DEFAULT 0,
            first_seen  TEXT,
            last_seen   TEXT,
            PRIMARY KEY (import_id, source)
        );

        CREATE TABLE IF NOT EXISTS runtime_unmapped (
            file_path     TEXT,
            line_no       INTEGER,
            function_name TEXT,
            source        TEXT NOT NULL,
            count         INTEGER NOT NULL DEFAULT 0,
            last_seen     TEXT,
            PRIMARY KEY (file_path, line_no, function_name, source)
        );

        CREATE TABLE IF NOT EXISTS runtime_redaction_log (
            source           TEXT NOT NULL,
            pattern          TEXT NOT NULL,
            redaction_count  INTEGER NOT NULL DEFAULT 0,
            last_redacted    TEXT,
            PRIMARY KEY (source, pattern)
        );
    """)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("index_version", "14"),
    )
    logger.info("Migrated v13→v14: added runtime_* tables for trace ingestion")


def _migrate_v14_to_v15(conn: sqlite3.Connection) -> None:
    """Migrate a v14 database to v15: add runtime_columns for SQL-log ingest (Phase 4).

    Existing runtime_calls / runtime_edges / runtime_imports / runtime_unmapped /
    runtime_redaction_log rows are preserved verbatim — the new table is purely
    additive. Stays empty until ``import_runtime_signal({source: 'sql_log'})``
    runs against a dbt-style repo whose index already has dbt_columns metadata.
    """
    conn.executescript("""\
        CREATE TABLE IF NOT EXISTS runtime_columns (
            model_name   TEXT NOT NULL,
            column_name  TEXT NOT NULL,
            source       TEXT NOT NULL,
            count        INTEGER NOT NULL DEFAULT 0,
            first_seen   TEXT,
            last_seen    TEXT,
            PRIMARY KEY (model_name, column_name, source)
        );
        CREATE INDEX IF NOT EXISTS idx_runtime_columns_model ON runtime_columns(model_name);
        CREATE INDEX IF NOT EXISTS idx_runtime_columns_last_seen ON runtime_columns(last_seen);
    """)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("index_version", "15"),
    )
    logger.info("Migrated v14→v15: added runtime_columns table for SQL-log ingest")


def _migrate_v15_to_v16(conn: sqlite3.Connection) -> None:
    """Migrate a v15 database to v16: add runtime_stack_events for Phase 5.

    Existing runtime_calls / runtime_columns / etc. rows are preserved
    verbatim. ``runtime_stack_events`` stays empty until
    ``import_runtime_signal({source: 'stack_log'})`` runs against an
    application log containing parseable Python / JVM / Node.js stacks.

    The (symbol_id, source, severity) PK lets a single symbol carry a
    distinct row per severity level; the symbol's runtime_calls row
    gets a separate (severity-agnostic) rollup so confidence-stamping
    on existing tools still works.
    """
    conn.executescript("""\
        CREATE TABLE IF NOT EXISTS runtime_stack_events (
            symbol_id   TEXT NOT NULL,
            source      TEXT NOT NULL,
            severity    TEXT NOT NULL,
            count       INTEGER NOT NULL DEFAULT 0,
            first_seen  TEXT,
            last_seen   TEXT,
            PRIMARY KEY (symbol_id, source, severity)
        );
        CREATE INDEX IF NOT EXISTS idx_runtime_stack_events_severity ON runtime_stack_events(severity, last_seen);
        CREATE INDEX IF NOT EXISTS idx_runtime_stack_events_symbol ON runtime_stack_events(symbol_id);
    """)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("index_version", "16"),
    )
    logger.info("Migrated v15→v16: added runtime_stack_events table for stack-log ingest")


def _migrate_v16_to_v17(conn: sqlite3.Connection) -> None:
    """Migrate a v16 database to v17: add scip_* tables for compile-time evidence.

    Existing rows in every other table are preserved verbatim — the new
    tables are purely additive and stay empty (zero-cost) until
    ``jcodemunch-mcp import-scip <path.scip>`` ingests a SCIP index file.

    ``scip_edges`` is deliberately a separate family from ``runtime_edges``:
    compile-time evidence (what the compiler proved) must never pollute
    runtime coverage statistics (what production actually executed).
    """
    conn.executescript("""\
        CREATE TABLE IF NOT EXISTS scip_edges (
            from_symbol_id TEXT NOT NULL,
            to_symbol_id   TEXT NOT NULL,
            kind           TEXT NOT NULL,
            count          INTEGER NOT NULL DEFAULT 0,
            first_seen     TEXT,
            last_seen      TEXT,
            PRIMARY KEY (from_symbol_id, to_symbol_id, kind)
        );
        CREATE INDEX IF NOT EXISTS idx_scip_edges_to ON scip_edges(to_symbol_id);

        CREATE TABLE IF NOT EXISTS scip_unmapped (
            file_path    TEXT,
            line_no      INTEGER,
            scip_symbol  TEXT,
            reason       TEXT NOT NULL,
            count        INTEGER NOT NULL DEFAULT 0,
            last_seen    TEXT,
            PRIMARY KEY (file_path, line_no, scip_symbol, reason)
        );

        CREATE TABLE IF NOT EXISTS scip_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("index_version", "17"),
    )
    logger.info("Migrated v16→v17: added scip_* tables for compile-time evidence ingest")


def _unlink_retry(path: Path, retries: int = 3, delay: float = 0.1) -> bool:
    """Delete a file with retry logic for Windows file-locking (PermissionError).

    On Windows, WAL-mode SQLite files can remain briefly locked by another
    thread even after all connections are closed.  Retrying with a short
    sleep resolves this in practice without masking real permission errors
    (which would persist across all retries and re-raise on the final attempt).
    """
    for attempt in range(retries):
        try:
            path.unlink()
            return True
        except PermissionError:
            if platform.system() != "Windows" or attempt == retries - 1:
                raise
            time.sleep(delay)
    return False  # unreachable, but satisfies type checkers



def _default_base_path() -> Path:
    """Storage root when a caller names none: `CODE_INDEX_PATH`, else `~/.code-index`.

    ⚠⚠ This env var was DOCUMENTED and only half implemented. `config.py`,
    `process_registry.py`, `install_pack.py`, `receipt.py` and two `server.py`
    sites honoured `CODE_INDEX_PATH`; the two storage classes hardcoded
    `Path.home() / ".code-index"` and ignored it. A user who set it therefore got
    their CONFIG from one directory and their INDEXES in another -- split state
    produced by a knob the env table calls "Index storage location".

    ⚠ That is the #428 shape: a declared capability with nothing behind it,
    which errors nowhere and so reads as working. The two `server.py` sites that
    pass `os.environ.get("CODE_INDEX_PATH")` by hand are the fingerprint --
    someone hit this and patched their own call site instead of the default.

    ⚠ Resolved exactly as an explicit `base_path` is, so the two entry points
    cannot disagree about what one spelling of a directory means (v1.108.280).
    """
    env = os.environ.get("CODE_INDEX_PATH")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".code-index"


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
            self.base_path = Path(base_path).expanduser().resolve()
        else:
            self.base_path = _default_base_path()
        _key = str(self.base_path)
        if _key not in _VERIFIED_PATHS:
            self.base_path.mkdir(parents=True, exist_ok=True)
            _VERIFIED_PATHS.add(_key)
        # Instance-scoped cache of resolved content_dir paths (was a class
        # attribute, which leaked entries across every instance for the life
        # of the process and shared state across tests).
        self._resolved_content_dirs: dict[str, str] = {}

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
                if stored_version < 6:
                    _migrate_v5_to_v6(conn)
                if stored_version < 7:
                    _migrate_v6_to_v7(conn)
                if stored_version < 8:
                    _migrate_v7_to_v8(conn)
                if stored_version < 9:
                    _migrate_v8_to_v9(conn)
                if stored_version < 14:
                    _migrate_v13_to_v14(conn)
                if stored_version < 15:
                    _migrate_v14_to_v15(conn)
                if stored_version < 16:
                    _migrate_v15_to_v16(conn)
                if stored_version < 17:
                    _migrate_v16_to_v17(conn)

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

    # ── Branch delta API ─────────────────────────────────────────────

    def save_branch_delta(
        self,
        owner: str,
        name: str,
        branch: str,
        changed_files: list[str],
        new_files: list[str],
        deleted_files: list[str],
        new_symbols: list["Symbol"],
        raw_files: dict[str, str],
        git_head: str = "",
        base_head: str = "",
        file_hashes: Optional[dict[str, str]] = None,
        file_mtimes: Optional[dict[str, int]] = None,
        file_languages: Optional[dict[str, str]] = None,
        file_summaries: Optional[dict[str, str]] = None,
        file_imports: Optional[dict[str, list[dict]]] = None,
    ) -> None:
        """Save a branch delta layer — records only what changed relative to the base index.

        For each changed/new file: stores its symbols, hash, and metadata.
        For each deleted file: stores a 'delete' marker.

        v1.108.105: serialised via the ``indexwrite`` lock (audit V9), so a
        branch-delta write can't interleave with a concurrent full/incremental
        reindex of the same .db from another process.
        """
        db_path = self._db_path(owner, name)
        if not db_path.exists():
            return

        from . import process_locks
        lock_target = f"{owner}/{name}"
        storage_root = str(self.base_path)
        with process_locks.held(
            "indexwrite", lock_target, storage_root, wait_seconds=60.0
        ) as got_lock:
            if not got_lock:
                detail = process_locks.current_holder_diagnostic(
                    "indexwrite", lock_target, storage_root,
                )
                raise RuntimeError(
                    f"Could not acquire index-write lock for {lock_target} "
                    f"after 60s{detail}"
                )
            self._save_branch_delta_locked(
                owner, name, branch, changed_files, new_files, deleted_files,
                new_symbols, raw_files, git_head, base_head, file_hashes,
                file_mtimes, file_languages, file_summaries, file_imports,
            )

    def _save_branch_delta_locked(
        self,
        owner: str,
        name: str,
        branch: str,
        changed_files: list[str],
        new_files: list[str],
        deleted_files: list[str],
        new_symbols: list["Symbol"],
        raw_files: dict[str, str],
        git_head: str = "",
        base_head: str = "",
        file_hashes: Optional[dict[str, str]] = None,
        file_mtimes: Optional[dict[str, int]] = None,
        file_languages: Optional[dict[str, str]] = None,
        file_summaries: Optional[dict[str, str]] = None,
        file_imports: Optional[dict[str, list[dict]]] = None,
    ) -> None:
        """Inner body of save_branch_delta; runs under the indexwrite lock."""
        db_path = self._db_path(owner, name)
        if not db_path.exists():
            return

        file_sizes = {fp: len(content.encode("utf-8")) for fp, content in raw_files.items()}

        conn = self._connect(db_path)
        try:
            conn.execute("BEGIN")

            # Clear existing delta entries for files we're updating
            all_affected = set(changed_files) | set(new_files) | set(deleted_files)
            if all_affected:
                placeholders = ",".join("?" * len(all_affected))
                conn.execute(
                    f"DELETE FROM branch_deltas WHERE branch = ? AND file IN ({placeholders})",
                    (branch, *all_affected),
                )

            # Insert delta entries
            rows = []
            for fp in set(changed_files) | set(new_files):
                # Gather symbols for this file
                file_syms = [s for s in new_symbols if s.file == fp]
                sym_data = json.dumps([self._symbol_to_dict_for_delta(s) for s in file_syms]) if file_syms else "[]"
                rows.append((
                    branch, fp, "modify" if fp in changed_files else "add",
                    sym_data,
                    (file_hashes or {}).get(fp, ""),
                    (file_mtimes or {}).get(fp),
                    (file_languages or {}).get(fp, ""),
                    (file_summaries or {}).get(fp, ""),
                    json.dumps((file_imports or {}).get(fp, [])),
                    file_sizes.get(fp),
                ))
            for fp in deleted_files:
                rows.append((branch, fp, "delete", None, None, None, None, None, None, None))

            if rows:
                conn.executemany(
                    "INSERT OR REPLACE INTO branch_deltas "
                    "(branch, file, action, symbol_data, file_hash, file_mtime_ns, "
                    "file_language, file_summary, file_imports, file_size_bytes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )

            # Update branch_meta
            conn.execute(
                "INSERT OR REPLACE INTO branch_meta (branch, git_head, indexed_at, base_head) "
                "VALUES (?, ?, ?, ?)",
                (branch, git_head, datetime.now().isoformat(), base_head),
            )

            conn.commit()
        finally:
            conn.close()

        # Write raw content files for branch delta
        content_dir = self._content_dir(owner, name)
        content_dir.mkdir(parents=True, exist_ok=True)
        for file_path, content in raw_files.items():
            file_dest = self._safe_content_path(content_dir, file_path)
            if not file_dest:
                raise ValueError(f"Unsafe file path in raw_files: {file_path}")
            file_dest.parent.mkdir(parents=True, exist_ok=True)
            self._write_cached_text(file_dest, content)

        # Evict branch-specific cache entry
        safe_name = self._safe_repo_component(name, "name")
        _cache_evict(owner, safe_name)

    def load_branch_delta(
        self, owner: str, name: str, branch: str,
    ) -> Optional[dict]:
        """Load a branch delta from the database.

        Returns a dict with keys: branch, git_head, base_head, indexed_at,
        files (list of {file, action, symbols, hash, ...}).
        Returns None if no delta exists for this branch.
        """
        db_path = self._db_path(owner, name)
        if not db_path.exists():
            return None

        conn = self._connect(db_path)
        try:
            meta_row = conn.execute(
                "SELECT * FROM branch_meta WHERE branch = ?", (branch,)
            ).fetchone()
            if meta_row is None:
                return None

            delta_rows = conn.execute(
                "SELECT * FROM branch_deltas WHERE branch = ?", (branch,)
            ).fetchall()

            files = []
            for r in delta_rows:
                entry: dict = {
                    "file": r["file"],
                    "action": r["action"],
                }
                if r["symbol_data"]:
                    try:
                        entry["symbols"] = json.loads(r["symbol_data"])
                    except (json.JSONDecodeError, ValueError):
                        entry["symbols"] = []
                if r["file_hash"]:
                    entry["hash"] = r["file_hash"]
                if r["file_mtime_ns"] is not None:
                    entry["mtime_ns"] = r["file_mtime_ns"]
                if r["file_language"]:
                    entry["language"] = r["file_language"]
                if r["file_summary"]:
                    entry["summary"] = r["file_summary"]
                if r["file_imports"]:
                    try:
                        entry["imports"] = json.loads(r["file_imports"])
                    except (json.JSONDecodeError, ValueError):
                        entry["imports"] = []
                if r["file_size_bytes"] is not None:
                    entry["size_bytes"] = r["file_size_bytes"]
                files.append(entry)

            return {
                "branch": branch,
                "git_head": meta_row["git_head"] or "",
                "base_head": meta_row["base_head"] or "",
                "indexed_at": meta_row["indexed_at"] or "",
                "files": files,
            }
        finally:
            conn.close()

    def list_branches(self, owner: str, name: str) -> list[dict]:
        """List all indexed branches for a repo.

        Returns list of dicts with: branch, git_head, indexed_at, base_head, delta_file_count.
        """
        db_path = self._db_path(owner, name)
        if not db_path.exists():
            return []

        conn = self._connect(db_path)
        try:
            meta_rows = conn.execute("SELECT * FROM branch_meta").fetchall()
            result = []
            for r in meta_rows:
                count = conn.execute(
                    "SELECT COUNT(*) FROM branch_deltas WHERE branch = ?",
                    (r["branch"],),
                ).fetchone()[0]
                result.append({
                    "branch": r["branch"],
                    "git_head": r["git_head"] or "",
                    "indexed_at": r["indexed_at"] or "",
                    "base_head": r["base_head"] or "",
                    "delta_file_count": count,
                })
            return result
        finally:
            conn.close()

    def delete_branch_delta(self, owner: str, name: str, branch: str) -> bool:
        """Delete a branch delta. Returns True if anything was deleted."""
        db_path = self._db_path(owner, name)
        if not db_path.exists():
            return False

        conn = self._connect(db_path)
        try:
            conn.execute("BEGIN")
            r1 = conn.execute("DELETE FROM branch_deltas WHERE branch = ?", (branch,))
            r2 = conn.execute("DELETE FROM branch_meta WHERE branch = ?", (branch,))
            conn.commit()
            deleted = (r1.rowcount or 0) + (r2.rowcount or 0) > 0
        finally:
            conn.close()

        if deleted:
            safe_name = self._safe_repo_component(name, "name")
            _cache_evict(owner, safe_name)
        return deleted

    def compose_branch_index(
        self, base_index: "CodeIndex", branch: str, delta: dict,
    ) -> "CodeIndex":
        """Compose a branch-aware CodeIndex by overlaying a delta on the base index.

        Args:
            base_index: The full base index (typically main/master).
            branch: Branch name.
            delta: Result from load_branch_delta().

        Returns:
            A new CodeIndex representing the repo state on the given branch.
        """
        from .index_store import CodeIndex

        deleted_files: set[str] = set()
        modified_files: set[str] = set()
        added_files: set[str] = set()
        delta_symbols_by_file: dict[str, list[dict]] = {}
        delta_hashes: dict[str, str] = {}
        delta_mtimes: dict[str, int] = {}
        delta_languages: dict[str, str] = {}
        delta_summaries: dict[str, str] = {}
        delta_imports: dict[str, list[dict]] = {}
        delta_sizes: dict[str, int] = {}

        for entry in delta.get("files", []):
            fp = entry["file"]
            action = entry["action"]
            if action == "delete":
                deleted_files.add(fp)
            elif action == "modify":
                modified_files.add(fp)
            elif action == "add":
                added_files.add(fp)

            if action in ("add", "modify"):
                delta_symbols_by_file[fp] = entry.get("symbols", [])
                if "hash" in entry:
                    delta_hashes[fp] = entry["hash"]
                if "mtime_ns" in entry:
                    delta_mtimes[fp] = entry["mtime_ns"]
                if "language" in entry:
                    delta_languages[fp] = entry["language"]
                if "summary" in entry:
                    delta_summaries[fp] = entry["summary"]
                if "imports" in entry:
                    delta_imports[fp] = entry["imports"]
                if "size_bytes" in entry:
                    delta_sizes[fp] = entry["size_bytes"]

        files_to_remove = deleted_files | modified_files

        # Patch symbols: drop symbols from removed/modified files, add delta symbols
        retained_syms = [s for s in base_index.symbols if s.get("file") not in files_to_remove]
        new_syms = []
        for fp, syms in delta_symbols_by_file.items():
            new_syms.extend(syms)
        composed_symbols = retained_syms + new_syms

        # Patch source_files
        kept_files = [f for f in base_index.source_files if f not in files_to_remove]
        composed_source_files = sorted(set(kept_files) | modified_files | added_files)

        # Patch file-level dicts
        def _patch(base_d: dict, delta_d: dict, remove: set) -> dict:
            result = {k: v for k, v in base_d.items() if k not in remove}
            result.update(delta_d)
            return result

        composed_hashes = _patch(base_index.file_hashes, delta_hashes, files_to_remove)
        composed_mtimes = _patch(base_index.file_mtimes, delta_mtimes, files_to_remove)
        composed_languages = _patch(base_index.file_languages, delta_languages, files_to_remove)
        composed_summaries = _patch(base_index.file_summaries, delta_summaries, files_to_remove)
        composed_sizes = _patch(base_index.file_sizes, delta_sizes, files_to_remove)

        base_imports = base_index.imports if base_index.imports is not None else {}
        composed_imports = _patch(base_imports, delta_imports, files_to_remove)

        # Recompute language counts
        lang_counts: dict[str, int] = {}
        for lang in composed_languages.values():
            lang_counts[lang] = lang_counts.get(lang, 0) + 1

        return CodeIndex(
            repo=base_index.repo,
            owner=base_index.owner,
            name=base_index.name,
            indexed_at=delta.get("indexed_at", base_index.indexed_at),
            source_files=composed_source_files,
            languages=lang_counts,
            symbols=composed_symbols,
            index_version=base_index.index_version,
            parser_generation=getattr(base_index, "parser_generation", 0),
            file_hashes=composed_hashes,
            git_head=delta.get("git_head", base_index.git_head),
            file_summaries=composed_summaries,
            source_root=base_index.source_root,
            git_root=getattr(base_index, "git_root", "") or "",
            source_roots=list(getattr(base_index, "source_roots", []) or []),
            file_languages=composed_languages,
            display_name=base_index.display_name,
            imports=composed_imports,
            context_metadata=base_index.context_metadata,
            file_blob_shas=base_index.file_blob_shas,
            file_mtimes=composed_mtimes,
            file_sizes=composed_sizes,
            package_names=getattr(base_index, "package_names", []),
            branch=branch,
            file_cap_status=getattr(base_index, "file_cap_status", {}) or {},
            coverage=getattr(base_index, "coverage", {}) or {},
        )

    def _symbol_to_dict_for_delta(self, symbol: "Symbol") -> dict:
        """Convert a Symbol to a serializable dict for branch delta storage."""
        return {
            "id": symbol.id, "file": symbol.file, "name": symbol.name,
            "kind": symbol.kind or "", "signature": symbol.signature or "",
            "summary": symbol.summary or "", "docstring": symbol.docstring or "",
            "qualified_name": symbol.qualified_name or symbol.name,
            "language": symbol.language or "",
            "decorators": symbol.decorators or [], "keywords": symbol.keywords or [],
            "parent": symbol.parent, "line": symbol.line or 0,
            "end_line": symbol.end_line or 0,
            "byte_offset": symbol.byte_offset or 0,
            "byte_length": symbol.byte_length or 0,
            "content_hash": symbol.content_hash or "",
            "ecosystem_context": getattr(symbol, "ecosystem_context", "") or "",
            "cyclomatic": getattr(symbol, "cyclomatic", 0) or 0,
            "max_nesting": getattr(symbol, "max_nesting", 0) or 0,
            "param_count": getattr(symbol, "param_count", 0) or 0,
            "call_references": getattr(symbol, "call_references", []) or [],
        }

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
        package_names: Optional[list[str]] = None,
        git_root: str = "",
        source_roots: Optional[list[str]] = None,
        file_cap_status: Optional[dict] = None,
    ) -> "CodeIndex":
        """Save a full index to SQLite. Replaces all existing data.

        v1.106.0: serialises against concurrent save_index calls from other
        MCP processes via the ``indexwrite`` lock. SQLite WAL alone makes
        single-process writes safe, but two processes both rebuilding the
        same .db can interleave DELETE/INSERT batches and corrupt the index.
        Waits up to 60s for a parallel writer to finish; raises if longer.
        """
        _ensure_index_store_deps()
        from .index_store import CodeIndex
        from . import process_locks

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
             "byte_length": s.byte_length, "content_hash": s.content_hash,
             "cyclomatic": getattr(s, "cyclomatic", 0) or 0,
             "max_nesting": getattr(s, "max_nesting", 0) or 0,
             "param_count": getattr(s, "param_count", 0) or 0,
             "call_references": getattr(s, "call_references", []) or []}
            for s in symbols
        ]

        # Compute languages from file_languages if not provided
        file_languages = file_languages or {}
        if not languages and file_languages:
            lang_counts: dict[str, int] = {}
            for lang in file_languages.values():
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
            languages = lang_counts

        file_sizes = {fp: len(content.encode("utf-8")) for fp, content in raw_files.items()}

        index = CodeIndex(
            repo=f"{owner}/{name}", owner=owner, name=name,
            indexed_at=datetime.now().isoformat(),
            source_files=normalized_source_files,
            languages=languages or {},
            symbols=serialized_symbols,
            index_version=cast(int, _INDEX_VERSION),
            parser_generation=cast(int, _PARSER_GENERATION),
            file_hashes=file_hashes,
            git_head=git_head,
            file_summaries=file_summaries or {},
            source_root=source_root,
            git_root=git_root,
            source_roots=source_roots or [],
            file_languages=file_languages,
            display_name=display_name or name,
            imports=imports if imports is not None else {},
            context_metadata=context_metadata or {},
            file_blob_shas=file_blob_shas or {},
            file_mtimes=file_mtimes or {},
            file_sizes=file_sizes,
            package_names=package_names or [],
            file_cap_status=file_cap_status or {},
        )

        db_path = self._db_path(owner, name)
        lock_target = f"{owner}/{name}"
        storage_root = str(self.base_path)
        with process_locks.held(
            "indexwrite", lock_target, storage_root, wait_seconds=60.0
        ) as got_lock:
            if not got_lock:
                detail = process_locks.current_holder_diagnostic(
                    "indexwrite", lock_target, storage_root,
                )
                raise RuntimeError(
                    f"Could not acquire index-write lock for {lock_target} "
                    f"after 60s{detail}"
                )
            return self._save_index_locked(
                owner, name, db_path, index, symbols,
                normalized_source_files, raw_files,
                file_hashes, file_languages, file_mtimes,
                file_blob_shas, file_summaries, file_sizes, imports,
            )

    def _save_index_locked(
        self,
        owner: str,
        name: str,
        db_path,
        index,
        symbols,
        normalized_source_files,
        raw_files,
        file_hashes,
        file_languages,
        file_mtimes,
        file_blob_shas,
        file_summaries,
        file_sizes,
        imports,
    ):
        """Inner body of save_index; runs under the indexwrite lock."""
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
                "ecosystem_context, data, cyclomatic, max_nesting, param_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [self._symbol_to_row(s) for s in symbols],
            )

            # Insert files (batch via executemany)
            conn.executemany(
                "INSERT OR REPLACE INTO files (path, hash, mtime_ns, language, "
                "summary, blob_sha, imports, size_bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        fp,
                        file_hashes.get(fp, ""),
                        (file_mtimes or {}).get(fp),
                        (file_languages or {}).get(fp, ""),
                        (file_summaries or {}).get(fp, ""),
                        (file_blob_shas or {}).get(fp, ""),
                        json.dumps((imports or {}).get(fp, [])),
                        file_sizes.get(fp),
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
        # Touch .db mtime BEFORE caching so the cached mtime matches what
        # cross-process readers will see via _db_mtime_ns().
        try:
            os.utime(db_path)
        except OSError:
            pass  # best-effort cross-process hint
        _cache_put(owner, safe_name, _db_mtime_ns(db_path), index)
        return index

    def load_index(self, owner: str, name: str, branch: str = "") -> Optional["CodeIndex"]:
        """Load index from SQLite, constructing a CodeIndex dataclass.

        When branch is non-empty and a branch delta exists, the base index
        is composed with the delta to produce a branch-specific view.
        When branch is empty, loads the base index as before.
        """
        _ensure_index_store_deps()
        # Sanitize name so "my project (v2)" maps to the same .db as save_index
        safe_name = self._safe_repo_component(name, "name")
        db_path = self._db_path(owner, safe_name)
        if not db_path.exists():
            return None

        # Check in-memory cache (mirrors old @lru_cache on JSON load).
        # Stat only when the key is present — avoids a syscall on cold starts.
        key = (owner, safe_name, branch)
        with _cache_lock:
            _has_key = key in _index_cache
        if _has_key:
            try:
                mtime_ns = _db_mtime_ns(db_path)
            except OSError:
                return None  # file was deleted between exists() and stat()
            cached = _cache_get(owner, safe_name, mtime_ns, branch)
            if cached is not None:
                return _stamp_load_provenance(cached, db_path, mtime_ns)

        # Single-flight cold hydration (#370): concurrent cold callers against
        # the same repo serialize here — one reads the rows and builds the
        # CodeIndex, the rest wake up, re-check the cache, and take the
        # freshly cached object instead of duplicating the whole load.
        with _load_lock_for(key):
            try:
                mtime_ns = _db_mtime_ns(db_path)
            except OSError:
                return None  # file was deleted while waiting on the lock
            cached = _cache_get(owner, safe_name, mtime_ns, branch)
            if cached is not None:
                return _stamp_load_provenance(cached, db_path, mtime_ns)

            try:
                conn = self._connect(db_path)
                try:
                    meta = self._read_meta(conn)
                    if not meta:
                        return None

                    try:
                        stored_version = int(meta.get("index_version", "0"))
                    except (TypeError, ValueError):
                        logger.warning("Corrupt index version for %s/%s", owner, name)
                        return None
                    if stored_version > cast(int, _INDEX_VERSION):
                        logger.warning("Index version %d > current %d for %s/%s", stored_version, _INDEX_VERSION, owner, name)
                        return None

                    symbol_rows = conn.execute("SELECT * FROM symbols").fetchall()
                    file_rows = conn.execute("SELECT * FROM files").fetchall()

                    index = self._build_index_from_rows(meta, symbol_rows, file_rows, owner, name)

                    # Warn if call references were not migrated (v7→v8 case)
                    if meta.get("call_refs_missing") == "1":
                        logger.warning(
                            "Index %s/%s was migrated from v7 which did not store call references. "
                            "get_call_hierarchy and get_impact_preview will use text heuristics. "
                            "Run 'jcodemunch-mcp index-folder' to re-index for AST-based call graphs.",
                            owner, name,
                        )
                finally:
                    conn.close()
            except sqlite3.DatabaseError:
                # A corrupt DB must not raise a raw traceback out of a retrieval
                # tool (audit V9b). Return None; callers routed through
                # load_repo_index_or_error then fall through to inspect_index, which
                # reports sqlite_corrupt + a re-index hint. Mirrors inspect_index's
                # own DatabaseError guard.
                logger.debug("Corrupt SQLite index at %s; treating as unloadable", db_path, exc_info=True)
                return None

            # If a branch is requested, compose the delta on top of the base
            if branch:
                delta = self.load_branch_delta(owner, name, branch)
                if delta is not None:
                    # Check if delta is stale (base has been re-indexed since delta was created)
                    if delta.get("base_head") and delta["base_head"] != index.git_head:
                        logger.warning(
                            "Branch delta for '%s' on %s/%s is stale "
                            "(base_head %s != current %s). Delta will be applied but may be inaccurate. "
                            "Re-run index_folder on the branch to refresh.",
                            branch, owner, name,
                            delta["base_head"][:8], (index.git_head or "")[:8],
                        )
                    index = self.compose_branch_index(index, branch, delta)

            # Populate cache (re-stat to capture any WAL checkpoint mtime change)
            try:
                post_mtime_ns = _db_mtime_ns(db_path)
            except OSError:
                post_mtime_ns = 0
            _cache_put(owner, safe_name, post_mtime_ns, index, branch)
            return _stamp_load_provenance(index, db_path, post_mtime_ns)

    # ── Selective read path (#398 Arc 2) ─────────────────────────────

    #: Chunk size for `id IN (...)`. SQLITE_MAX_VARIABLE_NUMBER is 32766 on
    #: modern builds and 999 on older ones; 900 clears the floor with headroom.
    _SELECT_CHUNK = 900

    def open_selective(
        self,
        owner: str,
        name: str,
        *,
        symbol_ids: Optional[list] = None,
        files: Optional[list] = None,
        branch: str = "",
    ):
        """One read transaction: repo + file metadata, plus only named symbols.

        Returns a ``SelectiveIndexView`` or ``None``. ``None`` means *use
        ``load_index``* — it is never an absence claim about the repository, and
        every caller must treat it as "take the ordinary path".

        ⚠ **A non-empty branch always returns None.** A branch view is
        ``load_index`` composed with a delta, including the staleness warning
        when the base moved underneath it. Reproducing that against a partial row
        set would change branch behaviour, which the close condition forbids.
        Promotion is the honest answer, and it is one line rather than a second
        implementation of delta composition.

        ⚠ No read here uses ``immutable=1``; see ``storage/selective.py``.
        """
        _ensure_index_store_deps()
        if branch:
            return None
        safe_name = self._safe_repo_component(name, "name")
        db_path = self._db_path(owner, safe_name)
        if not db_path.exists():
            return None

        # A cached full index already answers everything, exactly, for free.
        # Paying for a second (narrower) read would be strictly worse.
        try:
            mtime_ns = _db_mtime_ns(db_path)
        except OSError:
            return None
        cached = _cache_get(owner, safe_name, mtime_ns, branch)
        if cached is not None:
            return _stamp_load_provenance(cached, db_path, mtime_ns)

        from .generation import connect_readonly
        from .selective import SelectiveIndexView

        try:
            conn = connect_readonly(db_path)
        except Exception:
            logger.debug("Selective open failed for %s", db_path, exc_info=True)
            return None
        conn.row_factory = sqlite3.Row
        try:
            # One read transaction, so the meta, files and symbols rows all
            # describe the same generation (Arc 1's consistency guarantee).
            conn.execute("BEGIN")
            try:
                meta = self._read_meta(conn)
                if not meta:
                    return None
                try:
                    stored_version = int(meta.get("index_version", "0"))
                except (TypeError, ValueError):
                    return None
                if stored_version > cast(int, _INDEX_VERSION):
                    return None

                file_rows = conn.execute("SELECT * FROM files").fetchall()

                symbol_rows: list = []
                if symbol_ids:
                    ids = [s for s in dict.fromkeys(symbol_ids) if s]
                    for start in range(0, len(ids), self._SELECT_CHUNK):
                        chunk = ids[start:start + self._SELECT_CHUNK]
                        marks = ",".join("?" * len(chunk))
                        symbol_rows.extend(
                            conn.execute(
                                f"SELECT * FROM symbols WHERE id IN ({marks})", chunk
                            ).fetchall()
                        )
                if files:
                    paths = [f for f in dict.fromkeys(files) if f]
                    for start in range(0, len(paths), self._SELECT_CHUNK):
                        chunk = paths[start:start + self._SELECT_CHUNK]
                        marks = ",".join("?" * len(chunk))
                        symbol_rows.extend(
                            conn.execute(
                                f"SELECT * FROM symbols WHERE file IN ({marks})", chunk
                            ).fetchall()
                        )
                partial = self._build_index_from_rows(
                    meta, symbol_rows, file_rows, owner, name
                )
            finally:
                conn.rollback()
        except sqlite3.DatabaseError:
            logger.debug("Corrupt SQLite index at %s during selective open",
                         db_path, exc_info=True)
            return None
        finally:
            conn.close()

        view = SelectiveIndexView(
            partial,
            promote=lambda: self.load_index(owner, name, branch),
            fetch_symbol=lambda sid: self._fetch_symbol_row(db_path, sid),
            generation=meta.get("indexed_at") or None,
        )
        return _stamp_load_provenance(view, db_path, mtime_ns)

    def _fetch_symbol_row(self, db_path: Path, symbol_id: str) -> Optional[dict]:
        """One symbol row, read-only. Raises so the caller can promote.

        Deliberately NOT ``get_symbol_by_id``: that opens through ``_connect``,
        which runs PRAGMA and CREATE-TABLE on every connection and therefore
        moves the database mtime on a pure read — the v1.108.185 defect.
        """
        from .generation import connect_readonly

        conn = connect_readonly(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM symbols WHERE id = ?", (symbol_id,)
            ).fetchone()
            return self._row_to_symbol_dict(row) if row is not None else None
        finally:
            conn.close()

    def inspect_index(self, owner: str, name: str, branch: str = ""):
        """Check SQLite index presence and compatibility without loading rows."""
        _ensure_index_store_deps()
        from .index_store import IndexLoadStatus

        safe_name = self._safe_repo_component(name, "name")
        db_path = self._db_path(owner, safe_name)
        repo_id = f"{owner}/{safe_name}"
        if not db_path.exists():
            return IndexLoadStatus(
                repo=repo_id,
                owner=owner,
                name=safe_name,
                backend="none",
                index_present=False,
                loadable=False,
                status="missing",
                load_error="missing",
                hint="Call index_folder to index this repository.",
            )

        try:
            conn = self._connect(db_path)
            try:
                meta = self._read_meta(conn)
                if not meta:
                    return IndexLoadStatus(
                        repo=repo_id,
                        owner=owner,
                        name=safe_name,
                        backend="sqlite",
                        index_present=True,
                        loadable=False,
                        status="sqlite_missing_meta",
                        load_error="sqlite_missing_meta",
                        hint="Re-index this repository to rebuild missing SQLite metadata.",
                    )

                try:
                    stored_version = int(meta.get("index_version", "0"))
                except (TypeError, ValueError):
                    return IndexLoadStatus(
                        repo=meta.get("repo", repo_id),
                        owner=owner,
                        name=safe_name,
                        backend="sqlite",
                        index_present=True,
                        loadable=False,
                        status="sqlite_corrupt",
                        load_error="sqlite_corrupt",
                        hint="Re-index this repository to rebuild corrupt SQLite metadata.",
                        indexed_at=meta.get("indexed_at", ""),
                        display_name=meta.get("display_name", ""),
                        source_root=meta.get("source_root", ""),
                    )

                symbol_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
                file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
                try:
                    languages = json.loads(meta.get("languages", "{}"))
                except (TypeError, json.JSONDecodeError):
                    logger.warning("Corrupted languages JSON in metadata, defaulting to empty")
                    languages = {}
                base_kwargs = {
                    "repo": meta.get("repo", repo_id),
                    "owner": owner,
                    "name": safe_name,
                    "backend": "sqlite",
                    "index_present": True,
                    "indexed_at": meta.get("indexed_at", ""),
                    "symbol_count": symbol_count,
                    "file_count": file_count,
                    "languages": languages,
                    "index_version": stored_version,
                    "git_head": meta.get("git_head", ""),
                    "display_name": meta.get("display_name", ""),
                    "source_root": meta.get("source_root", ""),
                }
                if stored_version > cast(int, _INDEX_VERSION):
                    return IndexLoadStatus(
                        **base_kwargs,
                        loadable=False,
                        status="sqlite_future_version",
                        load_error="sqlite_future_version",
                        hint="Re-index this repository with the current server version.",
                    )
                return IndexLoadStatus(
                    **base_kwargs,
                    loadable=True,
                    status="loadable",
                )
            finally:
                conn.close()
        except sqlite3.DatabaseError:
            logger.debug("Failed to inspect SQLite index: %s", db_path, exc_info=True)
            return IndexLoadStatus(
                repo=repo_id,
                owner=owner,
                name=safe_name,
                backend="sqlite",
                index_present=True,
                loadable=False,
                status="sqlite_corrupt",
                load_error="sqlite_corrupt",
                hint="Re-index this repository to rebuild the corrupt SQLite index.",
            )

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
        package_names: Optional[list[str]] = None,
        file_cap_status: Optional[dict] = None,
    ) -> Optional["CodeIndex"]:
        """Incrementally update an existing index (delta write).

        v1.108.105: serialised against concurrent writers via the ``indexwrite``
        lock, mirroring save_index (audit V9). A watcher delta and a manual full
        reindex from another process both touch the same .db; without the lock
        their DELETE/INSERT batches can interleave and corrupt the index.
        """
        db_path = self._db_path(owner, name)
        if not db_path.exists():
            return None

        from . import process_locks
        lock_target = f"{owner}/{name}"
        storage_root = str(self.base_path)
        with process_locks.held(
            "indexwrite", lock_target, storage_root, wait_seconds=60.0
        ) as got_lock:
            if not got_lock:
                detail = process_locks.current_holder_diagnostic(
                    "indexwrite", lock_target, storage_root,
                )
                raise RuntimeError(
                    f"Could not acquire index-write lock for {lock_target} "
                    f"after 60s{detail}"
                )
            return self._incremental_save_locked(
                owner, name, changed_files, new_files, deleted_files,
                new_symbols, raw_files, languages, git_head, file_summaries,
                file_languages, imports, context_metadata, file_blob_shas,
                file_hashes, file_mtimes, package_names, file_cap_status,
            )

    def _incremental_save_locked(
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
        package_names: Optional[list[str]] = None,
        file_cap_status: Optional[dict] = None,
    ) -> Optional["CodeIndex"]:
        """Inner body of incremental_save; runs under the indexwrite lock."""
        db_path = self._db_path(owner, name)
        if not db_path.exists():
            return None

        # Grab old CodeIndex from cache BEFORE DB write changes mtime.
        # Used below to carry forward cached _tokens for unchanged symbols.
        safe_name = self._safe_repo_component(name, "name")
        old_index = None
        try:
            old_mtime = _db_mtime_ns(db_path)
            old_index = _cache_get(owner, safe_name, old_mtime)
        except OSError:
            pass

        conn = self._connect(db_path)
        try:
            conn.execute("BEGIN")

            # Delete symbols for changed + deleted files
            files_to_remove: set[str] = set(deleted_files) | set(changed_files)
            if files_to_remove:
                placeholders = ",".join("?" * len(files_to_remove))
                conn.execute(f"DELETE FROM symbols WHERE file IN ({placeholders})", tuple(files_to_remove))

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
                    "ecosystem_context, data, cyclomatic, max_nesting, param_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [self._symbol_to_row(s) for s in new_symbols],
                )

            # Update file records for changed + new files
            changed_or_new = sorted(set(changed_files) | set(new_files))
            incr_file_sizes = {fp: len(c.encode("utf-8")) for fp, c in raw_files.items()}
            for fp in changed_or_new:
                # Prefer caller-supplied values; fall back to preserved (for changed files)
                # or empty (for truly new files)
                inp_hashes = file_hashes or {}
                inp_mtimes = file_mtimes or {}
                existing = preserved.get(fp, {})
                conn.execute(
                    "INSERT OR REPLACE INTO files (path, hash, mtime_ns, language, "
                    "summary, blob_sha, imports, size_bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        fp,
                        inp_hashes.get(fp, existing.get("hash", "")),
                        inp_mtimes.get(fp, existing.get("mtime_ns")),
                        (file_languages or {}).get(fp, ""),
                        (file_summaries or {}).get(fp, ""),
                        (file_blob_shas or {}).get(fp, ""),
                        json.dumps((imports or {}).get(fp, [])),
                        incr_file_sizes.get(fp),
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
            # Refresh the published-package registry entry when the caller
            # recomputed it (e.g. a manifest was added/renamed). Without this a
            # delta write left the old package_names in meta, so cross-repo
            # features saw a stale registry until the next full reindex (audit
            # W7). None = caller didn't recompute → preserve the existing value.
            if package_names is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    ("package_names", json.dumps(package_names)),
                )
            # Refresh the file-cap truncation status so a re-index that newly
            # hits (or newly clears) the max_folder_files cap is reflected in
            # queries. None = caller didn't recompute → preserve existing (#366).
            if file_cap_status is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    ("file_cap_status", json.dumps(file_cap_status)),
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
            mtime_only: list = []
            if file_mtimes:
                changed_or_new_set = set(changed_or_new)
                deleted_set = set(deleted_files)
                mtime_only = [
                    (mt, fp) for fp, mt in file_mtimes.items()
                    if fp not in changed_or_new_set and fp not in deleted_set
                ]
                if mtime_only:
                    conn.executemany("UPDATE files SET mtime_ns = ? WHERE path = ?", mtime_only)

            # Always read meta (small). Only read all rows when no cached index to patch.
            meta = self._read_meta(conn)
            if old_index is None:
                all_symbol_rows = conn.execute("SELECT * FROM symbols").fetchall()
                all_file_rows = conn.execute("SELECT * FROM files").fetchall()
            else:
                all_symbol_rows = all_file_rows = None  # unused in patch path

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

        if old_index is not None:
            # Fast path: patch in-memory — O(delta), no full table read.
            # Retained symbols already carry their BM25 token bags (_tokens/_tf/_dl).
            index = self._patch_index_from_delta(
                old=old_index,
                meta=meta,
                files_to_remove=files_to_remove,
                new_files=new_files,
                changed_files=changed_files,
                new_symbols=new_symbols,
                file_hashes=file_hashes,
                file_mtimes=file_mtimes,
                mtime_only=mtime_only,
                file_languages=file_languages,
                file_summaries=file_summaries,
                file_blob_shas=file_blob_shas,
                imports=imports,
                context_metadata=context_metadata,
                computed_langs=computed_langs,
                file_sizes=incr_file_sizes,
            )
        else:
            # Cold path: build from DB rows (no cached index available)
            index = self._build_index_from_rows(meta, all_symbol_rows, all_file_rows, owner, name)

            # Carry forward cached BM25 token bags from unchanged symbols.
            # (Only needed in cold path — patch path retains them automatically.)
            # Matched by symbol id; content_hash must match on both sides to
            # guarantee the symbol text is identical.
            old_sym_map = {}
            for sym in (old_index.symbols if old_index else []):
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
                        if "_tf" in old_sym:
                            sym["_tf"] = old_sym["_tf"]
                        if "_dl" in old_sym:
                            sym["_dl"] = old_sym["_dl"]

        # Keep the returned/cached index consistent with the meta write above.
        # The fast (patch) path carries package_names from the old cached index,
        # so an in-place refresh here is needed; the cold path already rebuilt it
        # from the freshly-written meta, so this is a harmless no-op there.
        if package_names is not None:
            index.package_names = list(package_names)

        # Pre-warm cache so the next load_index() is instant
        # Touch .db mtime BEFORE caching so the cached mtime matches what
        # cross-process readers will see via _db_mtime_ns().
        try:
            os.utime(db_path)
        except OSError:
            pass  # best-effort cross-process hint
        _cache_put(owner, safe_name, _db_mtime_ns(db_path), index)
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

    def get_source_root(self, owner: str, name: str) -> Optional[str]:
        """Fast metadata-only query for source_root.

        Returns None if the repo is not indexed.
        """
        safe_owner = self._safe_repo_component(owner, "owner")
        safe_name = self._safe_repo_component(name, "name")
        db_path = self._db_path(safe_owner, safe_name)
        if not db_path.exists():
            return None
        _pairs = parse_path_map()
        try:
            conn = self._connect(db_path)
            try:
                meta = self._read_meta(conn)
            finally:
                conn.close()
            if not meta:
                return None
            return remap(meta.get("source_root", ""), _pairs)
        except Exception:
            logger.debug("Failed to get source_root for %s/%s", owner, name, exc_info=True)
            return None

    def get_file_cap_status(self, owner: str, name: str) -> dict:
        """Fast metadata-only read of the max_folder_files truncation status (#366).

        Returns ``{}`` when unknown (repo not indexed, or indexed before
        v1.108.126 which didn't record it). A recorded status is
        ``{"truncated": False}`` or the full ``{truncated, files_discovered,
        files_indexed, files_skipped_cap, max_folder_files}`` block.
        """
        safe_owner = self._safe_repo_component(owner, "owner")
        safe_name = self._safe_repo_component(name, "name")
        db_path = self._db_path(safe_owner, safe_name)
        if not db_path.exists():
            return {}
        try:
            conn = self._connect(db_path)
            try:
                meta = self._read_meta(conn)
            finally:
                conn.close()
            return json.loads(meta.get("file_cap_status", "{}")) or {}
        except Exception:
            logger.debug("Failed to get file_cap_status for %s/%s", owner, name, exc_info=True)
            return {}

    def get_coverage(self, owner: str, name: str) -> dict:
        """Fast metadata-only read of the persisted coverage contract.

        Returns ``{}`` when unknown (repo not indexed, or indexed before the
        coverage contract was recorded).
        """
        safe_owner = self._safe_repo_component(owner, "owner")
        safe_name = self._safe_repo_component(name, "name")
        db_path = self._db_path(safe_owner, safe_name)
        if not db_path.exists():
            return {}
        try:
            conn = self._connect(db_path)
            try:
                meta = self._read_meta(conn)
            finally:
                conn.close()
            return json.loads(meta.get("coverage", "{}")) or {}
        except Exception:
            logger.debug("Failed to get coverage for %s/%s", owner, name, exc_info=True)
            return {}

    def set_coverage(self, owner: str, name: str, coverage: dict) -> None:
        """Persist the coverage contract into the meta table (post-save).

        Written after every save that ran a full discovery walk, so the
        recorded skip counts always describe the corpus the index was built
        from. Best-effort: a failure never breaks the save that preceded it.
        """
        safe_owner = self._safe_repo_component(owner, "owner")
        safe_name = self._safe_repo_component(name, "name")
        db_path = self._db_path(safe_owner, safe_name)
        if not db_path.exists():
            return
        try:
            conn = self._connect(db_path)
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    ("coverage", json.dumps(coverage or {})),
                )
                conn.commit()
            finally:
                conn.close()
            # Keep any cached in-process CodeIndex consistent without waiting
            # for the mtime-keyed cache to miss.
            with _cache_lock:
                for key, entry in _index_cache.items():
                    if key[0] == owner and key[1] == name:
                        entry.code_index.coverage = coverage or {}
        except Exception:
            logger.debug("Failed to set coverage for %s/%s", owner, name, exc_info=True)

    def stamp_parser_generation(self, owner: str, name: str, generation: int) -> bool:
        """Record that this index's symbols were produced by `generation`.

        v1.108.259 (#395). Written ONLY by a bounded refresh campaign that has
        re-parsed every file in the corpus, which is the same thing a full save
        earns the stamp for, arrived at in slices instead of in one event.

        ⚠⚠ The stamp is a CLAIM about every symbol in the index, so the caller
        must have verified full coverage before calling. `index_store` states the
        rule this upholds: an incremental save deliberately leaves the stamp
        alone, because a partially re-parsed index must keep claiming the older
        generation. A campaign that stamps early is that bug with a scheduler in
        front of it.

        Refuses to move the stamp BACKWARDS. A lower generation is either a
        caller bug or a downgrade, and neither is a reason to un-claim work that
        was really done.

        Returns True when the stamp was written.
        """
        safe_owner = self._safe_repo_component(owner, "owner")
        safe_name = self._safe_repo_component(name, "name")
        db_path = self._db_path(safe_owner, safe_name)
        if not db_path.exists():
            return False
        try:
            conn = self._connect(db_path)
            try:
                current = int(self._read_meta(conn).get("parser_generation", "0") or 0)
                if generation <= current:
                    return False
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    ("parser_generation", str(int(generation))),
                )
                conn.commit()
            finally:
                conn.close()
            with _cache_lock:
                for key, entry in _index_cache.items():
                    if key[0] == owner and key[1] == name:
                        entry.code_index.parser_generation = int(generation)
            return True
        except Exception:
            logger.debug(
                "Failed to stamp parser_generation for %s/%s", owner, name, exc_info=True
            )
            return False

    def list_repos(self) -> list[dict]:
        """List all indexed repositories (scans .db files only)."""
        _pairs = parse_path_map()
        repos = []
        for db_file in self.base_path.glob("*.db"):
            if db_file.name in _NON_REPO_DB_FILES:
                continue
            try:
                entry = self._list_repo_from_db(db_file, _pairs)
                if entry:
                    repos.append(entry)
            except Exception:
                logger.debug("Skipping corrupted DB: %s", db_file, exc_info=True)
                slug = db_file.stem
                parts = slug.split("-", 1)
                owner, name = parts if len(parts) == 2 else ("local", slug)
                status = self.inspect_index(owner, name)
                repos.append({
                    "repo": status.repo,
                    **status.as_fields(include_empty=True),
                })
        repos.sort(key=lambda repo: repo["repo"])
        return repos

    def _list_repo_from_db(self, db_path: Path, _pairs: Optional[list] = None) -> Optional[dict]:
        """Read repo metadata from a .db file for list_repos."""
        if _pairs is None:
            _pairs = parse_path_map()
        slug = db_path.stem
        parts = slug.split("-", 1)
        owner, name = parts if len(parts) == 2 else ("local", slug)
        conn = self._connect(db_path)
        try:
            meta = self._read_meta(conn)
            if not meta or not meta.get("repo"):
                status = self.inspect_index(owner, name)
                return {
                    "repo": status.repo,
                    **status.as_fields(include_empty=True),
                }
            symbol_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            # Read branch info if branch tables exist
            branches = []
            try:
                branch_rows = conn.execute(
                    "SELECT branch, git_head, indexed_at, base_head FROM branch_meta"
                ).fetchall()
                for r in branch_rows:
                    delta_count = conn.execute(
                        "SELECT COUNT(*) FROM branch_deltas WHERE branch = ?",
                        (r["branch"],),
                    ).fetchone()[0]
                    branches.append({
                        "branch": r["branch"],
                        "git_head": r["git_head"] or "",
                        "indexed_at": r["indexed_at"] or "",
                        "delta_file_count": delta_count,
                    })
            except Exception:
                pass  # branch tables may not exist on pre-v9 DBs
        finally:
            conn.close()

        entry = {
            "repo": meta.get("repo", ""),
            "indexed_at": meta.get("indexed_at", ""),
            "symbol_count": symbol_count,
            "file_count": file_count,
            "git_head": meta.get("git_head", ""),
            "git_root": meta.get("git_root", ""),
            "display_name": meta.get("display_name", ""),
            "source_root": remap(meta.get("source_root", ""), _pairs),
            "index_present": True,
            "backend": "sqlite",
        }

        try:
            index_version = int(meta.get("index_version", "0"))
        except (TypeError, ValueError):
            entry.update({
                "loadable": False,
                "status": "sqlite_corrupt",
                "load_error": "sqlite_corrupt",
                "hint": "Re-index this repository to rebuild corrupt SQLite metadata.",
            })
            if branches:
                entry["branches"] = branches
            return entry

        try:
            languages = json.loads(meta.get("languages", "{}"))
        except (TypeError, json.JSONDecodeError):
            logger.warning("Corrupted languages JSON in metadata, defaulting to empty")
            languages = {}

        loadable = index_version <= cast(int, _INDEX_VERSION)
        status = "loadable" if loadable else "sqlite_future_version"
        entry.update({
            "languages": languages,
            "index_version": index_version,
            "loadable": loadable,
            "status": status,
        })
        if not loadable:
            entry["load_error"] = status
            entry["hint"] = "Re-index this repository with the current server version."
        if branches:
            entry["branches"] = branches
        return entry

    def delete_index(self, owner: str, name: str) -> bool:
        """Delete a repo's .db, .db-wal, .db-shm, and content dir."""
        safe_name = self._safe_repo_component(name, "name")
        _cache_evict(owner, safe_name)
        db_path = self._db_path(owner, name)
        deleted = False

        if db_path.exists():
            _unlink_retry(db_path)
            deleted = True
            SQLiteIndexStore._initialized_dbs.discard(str(db_path))

        wal_path = Path(str(db_path) + "-wal")
        if wal_path.exists():
            _unlink_retry(wal_path)
            deleted = True

        shm_path = Path(str(db_path) + "-shm")
        if shm_path.exists():
            _unlink_retry(shm_path)
            deleted = True

        content_dir = self._content_dir(owner, name)
        if content_dir.exists():
            shutil.rmtree(content_dir)
            deleted = True

        return deleted

    def cleanup_orphan_indexes(self) -> int:
        """Delete indexes whose source_root no longer exists on disk.

        Remote repos (GitHub, empty source_root) are skipped.
        Returns the number of orphan indexes deleted.
        """
        deleted = 0
        for entry in self.list_repos():
            source_root = entry.get("source_root", "")
            if not source_root:
                continue  # Remote repo — no filesystem path to validate
            if is_pack_clone_path(source_root):
                # ⚠ A starter-pack index, NOT a vanished local clone. Its
                # source_root is the pack builder's CI path and is absent on
                # every user machine by construction, so the is_dir() test
                # below would delete it every single server start (#419).
                # `heal_pack_index_paths()` normally blanks these first; this
                # guard is the backstop for a store that has not been healed
                # yet — the order of those two must never become load-bearing.
                continue
            try:
                if not Path(source_root).is_dir():
                    repo_id = entry["repo"]
                    # repo_id is "local/name-hash" or "github/owner/repo"
                    parts = repo_id.split("/", 1)
                    if len(parts) == 2:
                        owner, name = parts
                    else:
                        continue  # Malformed — skip
                    if self.delete_index(owner, name):
                        deleted += 1
                        logger.info(
                            "Deleted orphan index: %s (source_root: %s)",
                            repo_id,
                            source_root,
                        )
            except Exception:
                logger.debug("Orphan check failed for %s", source_root, exc_info=True)
        return deleted

    def heal_pack_index_paths(self) -> int:
        """Blank the builder's clone paths on already-installed pack indexes.

        Fixing `install-pack` only helps the NEXT install. Every pack already
        on disk still carries `/tmp/jcm-pack-clones/...` in its meta, and
        re-installing is not a remedy a user knows to apply — the marker
        survives deletion, so the pack still reports itself installed while
        nothing is queryable (#419, @MotoMato85). This repairs them in place.

        Blanking is the correct repair rather than a special case elsewhere:
        an empty `source_root` is exactly what the store already means by "no
        local clone to validate", which is the truth for a downloaded pack. It
        also takes packs out of `watch-all`, which was picking every one of
        them up and logging a failed index per pack.

        Returns the number of indexes repaired.
        """
        healed = 0
        for entry in self.list_repos():
            source_root = entry.get("source_root", "")
            if not is_pack_clone_path(source_root):
                continue
            repo_id = entry.get("repo", "")
            parts = repo_id.split("/", 1)
            if len(parts) != 2:
                continue
            owner, name = parts
            db_path = self._db_path(owner, name)
            if not db_path.exists():
                continue
            try:
                conn = self._connect(db_path)
                try:
                    conn.execute(
                        "UPDATE meta SET value = '' WHERE key IN ('source_root', 'git_root')"
                    )
                finally:
                    conn.close()
                healed += 1
                logger.info(
                    "Repaired starter-pack index %s (cleared builder path: %s)",
                    repo_id,
                    source_root,
                )
            except Exception:
                logger.debug("Pack path repair failed for %s", repo_id, exc_info=True)
        return healed

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
            dir_key = str(content_dir)
            base_str = self._resolved_content_dirs.get(dir_key)
            if base_str is None:
                base_str = str(content_dir.resolve())
                self._resolved_content_dirs[dir_key] = base_str
            candidate = (content_dir / relative_path).resolve()
            if os.path.commonpath([base_str, str(candidate)]) != base_str:
                return None
            return candidate
        except (OSError, ValueError):
            return None

    def _write_cached_text(self, path: Path, content: str) -> None:
        """Write cached text atomically, without newline translation.

        Honors ``cache_mode`` config: when set to ``"metadata_only"``, source
        bodies are not persisted to disk. The symbol table still gets
        populated normally; only the ``bodies/`` directory stays empty. P1.4.

        The write goes to a sibling temp file that is swapped in with
        ``os.replace`` (audit WS-5). A crash or disk-full mid-write then leaves
        the temp file, not a truncated body at ``path`` — readers never serve a
        half-written file as real source. The swap is same-directory so the
        rename is atomic on every platform; the indexwrite lock (V9) already
        serialises concurrent writers to a repo, so the pid-suffixed temp name
        cannot collide.
        """
        try:
            from .. import config as _cfg
            if _cfg.get("cache_mode", "full") == "metadata_only":
                return  # skip body persistence; symbol table still written
        except Exception:
            pass  # config unavailable, fall through to default full-cache behavior
        tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        try:
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            os.replace(tmp, path)
        except OSError:
            try:
                if tmp.exists():
                    os.unlink(tmp)
            except OSError:
                pass
            raise

    def _read_cached_text(self, path: Path) -> Optional[str]:
        """Read cached text without newline normalization."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                return f.read()
        except OSError:
            return None

    # ── Internal helpers ────────────────────────────────────────────

    def _symbol_to_row(self, symbol: Symbol) -> tuple:
        """Convert a Symbol to a row tuple for INSERT (v8 schema)."""
        call_refs = getattr(symbol, "call_references", []) or []
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
            json.dumps(call_refs) if call_refs else None,  # data column — v8: call_references as JSON array
            getattr(symbol, "cyclomatic", 0) or None,
            getattr(symbol, "max_nesting", 0) or None,
            getattr(symbol, "param_count", 0) or None,
        )

    def _symbol_dict_to_row(self, d: dict) -> tuple:
        """Convert a serialized symbol dict to a row tuple for INSERT (v8 schema)."""
        decorators = d.get("decorators", [])
        keywords = d.get("keywords", [])
        call_refs = d.get("call_references", [])
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
            json.dumps(call_refs) if call_refs else None,  # data column — v8: call_references as JSON array
            d.get("cyclomatic") or None,
            d.get("max_nesting") or None,
            d.get("param_count") or None,
        )

    def _row_to_symbol_dict(self, row: sqlite3.Row) -> dict:
        """Convert a database row to a symbol dict (matches CodeIndex.symbols format)."""
        call_references: list[str] = []
        if row["data"]:
            try:
                data = json.loads(row["data"])
            except (json.JSONDecodeError, ValueError):
                logger.warning("Corrupted JSON in symbol data column for row %s, skipping legacy fields", row["name"])
                # Keep corrupt v8 rows on the array path so row-backed metadata still loads.
                data = []
            if isinstance(data, list):
                # v8: data column contains call_references as JSON array
                # Read metadata from row columns (not from data, which is an array)
                call_references = data
                qualified_name = row["qualified_name"] or row["name"]
                language = row["language"] or ""
                deco_raw = row["decorators"]
                try:
                    decorators = json.loads(deco_raw) if deco_raw and deco_raw != "[]" else []
                except (json.JSONDecodeError, ValueError):
                    logger.warning("Corrupted decorators JSON for symbol %s", row["name"])
                    decorators = []
                kw_raw = row["keywords"]
                try:
                    keywords = json.loads(kw_raw) if kw_raw and kw_raw != "[]" else []
                except (json.JSONDecodeError, ValueError):
                    logger.warning("Corrupted keywords JSON for symbol %s", row["name"])
                    keywords = []
                content_hash = row["content_hash"] or ""
                ecosystem_context = row["ecosystem_context"] or ""
            else:
                # legacy v4 row (data is a JSON object)
                qualified_name = data.get("qualified_name", row["name"])
                language = data.get("language", "")
                decorators = data.get("decorators", [])
                keywords = data.get("keywords", [])
                content_hash = data.get("content_hash", "")
                ecosystem_context = data.get("ecosystem_context", "")
        else:
            # v5/v6/v7 row — direct column reads, no JSON parsing
            qualified_name = row["qualified_name"] or row["name"]
            language = row["language"] or ""
            deco_raw = row["decorators"]
            try:
                decorators = json.loads(deco_raw) if deco_raw and deco_raw != "[]" else []
            except (json.JSONDecodeError, ValueError):
                logger.warning("Corrupted decorators JSON for symbol %s", row["name"])
                decorators = []
            kw_raw = row["keywords"]
            try:
                keywords = json.loads(kw_raw) if kw_raw and kw_raw != "[]" else []
            except (json.JSONDecodeError, ValueError):
                logger.warning("Corrupted keywords JSON for symbol %s", row["name"])
                keywords = []
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
            "cyclomatic": row["cyclomatic"] or 0,
            "max_nesting": row["max_nesting"] or 0,
            "param_count": row["param_count"] or 0,
            "call_references": call_references,
        }

    def _symbol_to_dict(self, symbol: "Symbol") -> dict:
        """Convert a Symbol object directly to a CodeIndex.symbols-format dict.

        Used by the in-memory patch path in incremental_save to avoid a DB
        round-trip when the cached old_index is available.
        """
        return {
            "id": symbol.id,
            "file": symbol.file,
            "name": symbol.name,
            "kind": symbol.kind or "",
            "signature": symbol.signature or "",
            "summary": symbol.summary or "",
            "docstring": symbol.docstring or "",
            "qualified_name": symbol.qualified_name or symbol.name,
            "language": symbol.language or "",
            "decorators": symbol.decorators or [],
            "keywords": symbol.keywords or [],
            "parent": symbol.parent,
            "line": symbol.line or 0,
            "end_line": symbol.end_line or 0,
            "byte_offset": symbol.byte_offset or 0,
            "byte_length": symbol.byte_length or 0,
            "content_hash": symbol.content_hash or "",
            "ecosystem_context": getattr(symbol, "ecosystem_context", "") or "",
            "cyclomatic": getattr(symbol, "cyclomatic", 0) or 0,
            "max_nesting": getattr(symbol, "max_nesting", 0) or 0,
            "param_count": getattr(symbol, "param_count", 0) or 0,
            "call_references": getattr(symbol, "call_references", []) or [],
        }

    def _patch_index_from_delta(
        self,
        old: "CodeIndex",
        meta: dict,
        files_to_remove: set,
        new_files: list,
        changed_files: list,
        new_symbols: list,
        file_hashes: Optional[dict],
        file_mtimes: Optional[dict],
        mtime_only: list,
        file_languages: Optional[dict],
        file_summaries: Optional[dict],
        file_blob_shas: Optional[dict],
        imports: Optional[dict],
        context_metadata: Optional[dict],
        computed_langs: dict,
        file_sizes: Optional[dict] = None,
    ) -> "CodeIndex":
        """Patch an existing CodeIndex in memory — O(delta) instead of O(total rows).

        Retained symbols already carry their BM25 token bags (_tokens/_tf/_dl)
        from old_index, so no separate carry-forward step is needed.
        """
        from .index_store import CodeIndex

        # New symbol dicts — no DB round-trip required
        new_sym_dicts = [self._symbol_to_dict(s) for s in new_symbols]

        # Patch symbol list: drop changed/deleted, append new.
        # Also drop any retained symbol whose id is being replaced by a new_sym_dict
        # (e.g. deferred summarization updates existing symbols in-place without
        # touching files_to_remove — without this check they would be duplicated).
        # Strip BM25 internal keys from any retained symbol that lacks a content_hash —
        # matches the carry-forward contract of the cold path (no hash = can't verify).
        _bm25_keys = {"_tokens", "_tf", "_dl"}
        new_sym_ids = {s.get("id") for s in new_sym_dicts}
        retained_syms = []
        for s in old.symbols:
            if s.get("file") in files_to_remove:
                continue
            if s.get("id") in new_sym_ids:
                continue
            if s.keys() & _bm25_keys and not s.get("content_hash"):
                s = {k: v for k, v in s.items() if k not in _bm25_keys}
            retained_syms.append(s)
        patched_symbols = retained_syms + new_sym_dicts

        # Patch source_files
        kept_files = [f for f in old.source_files if f not in files_to_remove]
        added_files = set(changed_files) | set(new_files)
        patched_source_files = sorted(set(kept_files) | added_files)

        def _patch_dict(old_d: dict, delta: Optional[dict], remove_keys: set) -> dict:
            result = {k: v for k, v in old_d.items() if k not in remove_keys}
            if delta:
                result.update(delta)
            return result

        mtime_only_dict = {fp: mt for mt, fp in mtime_only}

        new_file_mtimes = _patch_dict(old.file_mtimes, file_mtimes, files_to_remove)
        new_file_mtimes.update(mtime_only_dict)  # mtime-only drift updates
        new_file_hashes = _patch_dict(old.file_hashes, file_hashes, files_to_remove)
        new_file_languages = _patch_dict(old.file_languages, file_languages, files_to_remove)
        new_file_summaries = _patch_dict(old.file_summaries, file_summaries, files_to_remove)
        new_file_blob_shas = _patch_dict(old.file_blob_shas, file_blob_shas, files_to_remove)
        new_file_sizes = _patch_dict(old.file_sizes, file_sizes, files_to_remove)

        if old.imports is not None:
            new_imports: Optional[dict] = _patch_dict(old.imports, imports, files_to_remove)
        else:
            new_imports = imports or {}

        new_ctx = context_metadata if context_metadata is not None else old.context_metadata

        if "file_cap_status" in meta:
            try:
                new_file_cap_status = json.loads(meta.get("file_cap_status", "{}")) or {}
            except (json.JSONDecodeError, ValueError):
                new_file_cap_status = getattr(old, "file_cap_status", {})
        else:
            new_file_cap_status = getattr(old, "file_cap_status", {})

        if "coverage" in meta:
            try:
                new_coverage = json.loads(meta.get("coverage", "{}")) or {}
            except (json.JSONDecodeError, ValueError):
                new_coverage = getattr(old, "coverage", {})
        else:
            new_coverage = getattr(old, "coverage", {})

        return CodeIndex(
            repo=old.repo,
            owner=old.owner,
            name=old.name,
            indexed_at=meta.get("indexed_at", old.indexed_at),
            source_files=patched_source_files,
            languages=computed_langs,
            symbols=patched_symbols,
            index_version=old.index_version,
            parser_generation=getattr(old, "parser_generation", 0),
            file_hashes=new_file_hashes,
            git_head=meta.get("git_head", old.git_head),
            file_summaries=new_file_summaries,
            source_root=old.source_root,
            git_root=getattr(old, "git_root", "") or "",
            source_roots=list(getattr(old, "source_roots", []) or []),
            file_languages=new_file_languages,
            display_name=old.display_name,
            imports=new_imports,
            context_metadata=new_ctx,
            file_blob_shas=new_file_blob_shas,
            file_mtimes=new_file_mtimes,
            file_sizes=new_file_sizes,
            package_names=getattr(old, "package_names", []),
            file_cap_status=new_file_cap_status,
            coverage=new_coverage,
        )

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
        file_sizes: dict[str, int] = {}
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
            size = r["size_bytes"] if "size_bytes" in r.keys() else None
            if size is not None:
                file_sizes[p] = size
            if r["imports"]:
                try:
                    parsed = json.loads(r["imports"])
                    if parsed:
                        imports[p] = parsed
                except (json.JSONDecodeError, ValueError):
                    logger.warning("Corrupted imports JSON for file %s, skipping", p)
        source_files = sorted(source_files_unsorted)
        if not imports:
            # v3 format had no imports field — preserve None for backward compatibility
            index_version = int(meta.get("index_version", "0"))
            imports = None if index_version < 4 else {}

        try:
            languages = json.loads(meta.get("languages", "{}"))
        except (json.JSONDecodeError, ValueError):
            logger.warning("Corrupted languages JSON in metadata, defaulting to empty")
            languages = {}
        try:
            context_metadata = json.loads(meta.get("context_metadata", "{}"))
        except (json.JSONDecodeError, ValueError):
            logger.warning("Corrupted context_metadata JSON in metadata, defaulting to empty")
            context_metadata = {}
        package_names_raw = meta.get("package_names", "[]")
        try:
            package_names = json.loads(package_names_raw) if package_names_raw else []
        except Exception:
            package_names = []
        try:
            file_cap_status = json.loads(meta.get("file_cap_status", "{}")) or {}
        except (json.JSONDecodeError, ValueError):
            file_cap_status = {}
        try:
            coverage = json.loads(meta.get("coverage", "{}")) or {}
        except (json.JSONDecodeError, ValueError):
            coverage = {}

        return CodeIndex(
            repo=meta.get("repo", f"{owner}/{name}"),
            owner=meta.get("owner", owner),
            name=meta.get("name", name),
            indexed_at=meta.get("indexed_at", ""),
            source_files=source_files,
            languages=languages,
            symbols=symbols,
            index_version=int(meta.get("index_version", "0")),
            parser_generation=int(meta.get("parser_generation", "0") or 0),
            file_hashes=file_hashes,
            git_head=meta.get("git_head", ""),
            file_summaries=file_summaries,
            source_root=meta.get("source_root", ""),
            git_root=meta.get("git_root", ""),
            source_roots=_safe_json_load_list(meta.get("source_roots", "[]")),
            file_languages=file_languages,
            display_name=meta.get("display_name", name),
            imports=imports,
            context_metadata=context_metadata,
            file_blob_shas=file_blob_shas,
            file_mtimes=file_mtimes,
            file_sizes=file_sizes,
            package_names=package_names,
            file_cap_status=file_cap_status,
            coverage=coverage,
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
            "parser_generation": str(getattr(index, "parser_generation", 0)),
            "git_head": index.git_head,
            "source_root": index.source_root,
            "git_root": getattr(index, "git_root", "") or "",
            "source_roots": json.dumps(getattr(index, "source_roots", []) or []),
            "display_name": index.display_name,
            "languages": json.dumps(index.languages),
            "context_metadata": json.dumps(index.context_metadata or {}),
            "package_names": json.dumps(getattr(index, "package_names", []) or []),
            "base_branch": getattr(index, "branch", "") or "",
            "file_cap_status": json.dumps(getattr(index, "file_cap_status", {}) or {}),
            "coverage": json.dumps(getattr(index, "coverage", {}) or {}),
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
                ".mjs": "javascript", ".cjs": "javascript",
                ".jsx": "javascript", ".tsx": "typescript", ".go": "go",
                ".rs": "rust", ".java": "java", ".c": "c", ".cpp": "cpp",
                ".h": "cpp", ".ino": "arduino", ".pde": "arduino",
                ".vhd": "vhdl", ".vhdl": "vhdl",
                ".v": "verilog", ".sv": "verilog",
                ".cs": "csharp", ".swift": "swift",
                ".rb": "ruby", ".php": "php", ".dart": "dart",
                ".kt": "kotlin", ".scala": "scala", ".lua": "lua",
                ".r": "r", ".m": "objective-c", ".mm": "objective-cpp",
                ".sh": "bash", ".bash": "bash", ".zsh": "zsh",
                ".sql": "sql", ".xml": "xml", ".html": "html",
                ".css": "css", ".scss": "scss", ".less": "less",
                ".json": "json", ".yaml": "yaml", ".yml": "yaml",
                ".toml": "toml", ".md": "markdown", ".rst": "rst",
                ".ps1": "powershell",
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
        """Read a JSON index file and populate the SQLite database.

        v1.106.0: serialises against concurrent save_index / migrate_from_json
        for the same repo via the ``indexwrite`` lock.
        """
        _ensure_index_store_deps()
        from . import process_locks

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
            warn_key = (owner, name)
            if warn_key not in _MIGRATION_SCHEMA_WARNED:
                _MIGRATION_SCHEMA_WARNED.add(warn_key)
                logger.warning(
                    "Migration schema validation failed for %s/%s — missing required "
                    "fields (stale or corrupt JSON index; remove it with "
                    "`jcodemunch-mcp delete-index %s/%s`)",
                    owner, name, owner, name,
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

        # Populate SQLite from JSON data — serialised via indexwrite lock so
        # we don't race a concurrent save_index from another process.
        lock_target = f"{owner}/{name}"
        storage_root = str(self.base_path)
        with process_locks.held(
            "indexwrite", lock_target, storage_root, wait_seconds=60.0,
        ) as got_lock:
            if not got_lock:
                detail = process_locks.current_holder_diagnostic(
                    "indexwrite", lock_target, storage_root,
                )
                raise RuntimeError(
                    f"Could not acquire index-write lock to migrate {lock_target} "
                    f"after 60s{detail}"
                )
            return self._migrate_from_json_locked(
                json_path, owner, name, data, source_files, symbols,
                merged_fl, computed_languages, has_imports_key, stored_imports,
            )

    def _migrate_from_json_locked(
        self,
        json_path: "Path",
        owner: str,
        name: str,
        data: dict,
        source_files: list,
        symbols: list,
        merged_fl: dict,
        computed_languages: dict,
        has_imports_key: bool,
        stored_imports,
    ) -> Optional["CodeIndex"]:
        """Inner body of migrate_from_json; runs under the indexwrite lock."""
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
                # A JSON index predates the generation stamp; 0 makes the
                # migrated copy re-parse once rather than inherit a claim.
                "parser_generation": str(data.get("parser_generation", 0) or 0),
                "git_head": data.get("git_head", ""),
                "source_root": data.get("source_root", ""),
                "git_root": data.get("git_root", ""),
                "source_roots": json.dumps(data.get("source_roots", []) or []),
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
                    "ecosystem_context, data, cyclomatic, max_nesting, param_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
