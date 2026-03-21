"""Local integration tests for the SQLite WAL storage backend.

Covers scenarios that are best verified end-to-end:
  1. WAL file presence after first write (.db-wal / .db-shm created)
  2. Delta write size — incremental save writes a fraction of what a full
     rewrite would cost
  3. Mtime fast-path stickiness — after a touch-only change (mtime differs,
     hash same) the new mtime is persisted so the *next* cycle is a cache hit
  4. Multi-process consistency — a second SQLite connection immediately sees
     committed writes from the first (WAL reader isolation)
  5. Checkpoint-on-close — calling checkpoint_and_close compacts the WAL
"""

import json
import os
import sqlite3
import threading
from pathlib import Path

import pytest

from jcodemunch_mcp.storage.index_store import IndexStore
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore
from jcodemunch_mcp.parser.symbols import Symbol


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _sym(name: str, file: str = "main.py") -> Symbol:
    return Symbol(
        id=f"{file}::{name}#function",
        file=file,
        name=name,
        qualified_name=name,
        kind="function",
        language="python",
        signature=f"def {name}()",
        line=1,
        end_line=3,
        byte_offset=0,
        byte_length=20,
    )


def _full_save(store: SQLiteIndexStore, n_files: int = 5) -> None:
    """Save a full index with *n_files* Python files."""
    files = [f"file_{i}.py" for i in range(n_files)]
    store.save_index(
        owner="local",
        name="test-abc123",
        source_files=files,
        symbols=[_sym(f"fn_{i}", f"file_{i}.py") for i in range(n_files)],
        raw_files={f: f"def fn_{i}(): pass" for i, f in enumerate(files)},
        file_hashes={f: f"h{i}" for i, f in enumerate(files)},
        file_mtimes={f: (i + 1) * 1_000_000_000 for i, f in enumerate(files)},
    )


# ---------------------------------------------------------------------------
# 1. WAL file presence
# ---------------------------------------------------------------------------

def test_wal_files_created_after_write(tmp_path):
    """WAL sidecar files (.db-wal, .db-shm) exist while a connection is open.

    SQLite may auto-checkpoint and remove them on clean close (especially on
    Windows), so we keep a reader connection open while asserting presence.
    """
    store = SQLiteIndexStore(base_path=str(tmp_path))
    _full_save(store)

    db_path = store._db_path("local", "test-abc123")
    wal = Path(str(db_path) + "-wal")
    shm = Path(str(db_path) + "-shm")

    assert db_path.exists(), ".db file must exist after save_index"

    # Hold a connection open — this prevents SQLite from removing WAL/SHM
    conn = store._connect(db_path)
    try:
        # Do a tiny write so WAL is non-empty
        conn.execute("BEGIN")
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('_test', '1')")
        conn.commit()
        assert wal.exists(), ".db-wal must exist while a connection holds the WAL open"
        assert shm.exists(), ".db-shm must exist while a connection holds the WAL open"
    finally:
        conn.close()


def test_wal_mode_pragma(tmp_path):
    """Every connection opens in WAL journal mode."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    _full_save(store)

    db_path = store._db_path("local", "test-abc123")
    conn = store._connect(db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()

    assert mode == "wal", f"Expected 'wal', got '{mode}'"


# ---------------------------------------------------------------------------
# 2. Delta write size
# ---------------------------------------------------------------------------

def test_incremental_write_is_smaller_than_full_rewrite(tmp_path):
    """An incremental save (1 file changed) grows the DB far less than a full
    save would.  We verify this by comparing the WAL growth vs .db file size.
    """
    store = SQLiteIndexStore(base_path=str(tmp_path))
    # Save a moderately large full index
    _full_save(store, n_files=50)

    db_path = store._db_path("local", "test-abc123")
    wal_path = Path(str(db_path) + "-wal")

    # Force a WAL checkpoint so we start from a clean baseline
    conn = store._connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    db_size_before = db_path.stat().st_size
    wal_size_before = wal_path.stat().st_size if wal_path.exists() else 0

    # Incremental save: change exactly 1 file out of 50
    store.incremental_save(
        owner="local",
        name="test-abc123",
        changed_files=["file_0.py"],
        new_files=[],
        deleted_files=[],
        new_symbols=[_sym("fn_0_updated", "file_0.py")],
        raw_files={"file_0.py": "def fn_0_updated(): pass"},
        file_hashes={"file_0.py": "h0_new"},
        file_mtimes={"file_0.py": 99_000_000_000},
    )

    wal_size_after = wal_path.stat().st_size if wal_path.exists() else 0
    wal_growth = wal_size_after - wal_size_before

    # The WAL growth for 1 changed file must be well under 50 % of the full
    # DB size (in practice it's <1 %, but 50 % is a very conservative bound).
    assert wal_growth < db_size_before * 0.5, (
        f"WAL grew by {wal_growth} bytes, which is ≥50% of the full DB size "
        f"({db_size_before} bytes). Incremental save should write only delta rows."
    )


# ---------------------------------------------------------------------------
# 3. Mtime fast-path stickiness (BUG 1 fix)
# ---------------------------------------------------------------------------

def test_mtime_fast_path_stickiness_after_touch(tmp_path):
    """After a touch (mtime changes, hash unchanged), incremental_save must
    persist the new mtime so the *next* watcher cycle is a fast-path hit
    (no re-hash required).

    Before the fix, the old mtime stayed in the DB → every subsequent cycle
    re-hashed the file perpetually.
    """
    store = SQLiteIndexStore(base_path=str(tmp_path))
    store.save_index(
        owner="local",
        name="test-abc123",
        source_files=["a.py"],
        symbols=[],
        raw_files={"a.py": "content"},
        file_hashes={"a.py": "h_stable"},
        file_mtimes={"a.py": 1_000_000_000},
    )

    hash_calls: list[str] = []

    def hash_fn(fp: str) -> str:
        hash_calls.append(fp)
        return "h_stable"  # content never changes

    # --- Cycle 1: mtime changed (touch), same content ---
    changed, new, deleted, _, updated_mtimes = store.detect_changes_with_mtimes(
        "local", "test-abc123",
        current_mtimes={"a.py": 2_000_000_000},  # mtime bumped
        hash_fn=hash_fn,
    )
    assert changed == [], "Hash is identical — file should not be in changed_files"
    assert len(hash_calls) == 1, "Cycle 1 must hash to confirm content is unchanged"

    # Persist updated mtimes (including the new mtime for a.py)
    store.incremental_save(
        owner="local",
        name="test-abc123",
        changed_files=[],
        new_files=[],
        deleted_files=[],
        new_symbols=[],
        raw_files={},
        file_mtimes=updated_mtimes,
    )

    # --- Cycle 2: same mtime — must be a fast-path hit, no re-hash ---
    hash_calls.clear()
    changed2, new2, deleted2, _, _ = store.detect_changes_with_mtimes(
        "local", "test-abc123",
        current_mtimes={"a.py": 2_000_000_000},  # same as persisted
        hash_fn=hash_fn,
    )
    assert changed2 == [], "File is still unchanged"
    assert len(hash_calls) == 0, (
        "Cycle 2 must NOT call hash_fn — mtime matches the stored value so the "
        "fast path should skip hashing entirely. "
        "(This would fail before the mtime-stickiness fix.)"
    )


# ---------------------------------------------------------------------------
# 4. Multi-process consistency (two independent connections)
# ---------------------------------------------------------------------------

def test_multiprocess_consistency(tmp_path):
    """A second SQLite connection opened after the first commits immediately
    sees the committed data — no stale reads via WAL.

    We simulate two processes with two separate SQLiteIndexStore instances
    pointing at the same base_path.
    """
    writer = SQLiteIndexStore(base_path=str(tmp_path))
    reader = SQLiteIndexStore(base_path=str(tmp_path))

    writer.save_index(
        owner="local",
        name="shared-abc123",
        source_files=["v1.py"],
        symbols=[_sym("v1_fn", "v1.py")],
        raw_files={"v1.py": "def v1_fn(): pass"},
        file_hashes={"v1.py": "hv1"},
    )

    # Reader must see the write immediately (no in-process cache to invalidate)
    index = reader.load_index("local", "shared-abc123")
    assert index is not None, "Reader must see the committed write"
    assert len(index.symbols) == 1
    assert index.symbols[0]["name"] == "v1_fn"

    # Writer updates
    writer.incremental_save(
        owner="local",
        name="shared-abc123",
        changed_files=["v1.py"],
        new_files=[],
        deleted_files=[],
        new_symbols=[_sym("v2_fn", "v1.py")],
        raw_files={"v1.py": "def v2_fn(): pass"},
        file_hashes={"v1.py": "hv2"},
    )

    # Reader must see the incremental update on next read
    updated = reader.load_index("local", "shared-abc123")
    assert updated is not None
    assert len(updated.symbols) == 1
    assert updated.symbols[0]["name"] == "v2_fn", (
        "Reader should see the incremental update — WAL ensures cross-connection "
        "consistency without any explicit cache invalidation."
    )


def test_concurrent_reader_writer_no_errors(tmp_path):
    """Concurrent reads and writes via separate connections must not raise errors.

    SQLite WAL allows one writer + many concurrent readers, so a read running
    in parallel with a write should always succeed (busy_timeout handles any
    brief lock contention).
    """
    store = SQLiteIndexStore(base_path=str(tmp_path))
    _full_save(store, n_files=10)

    errors: list[Exception] = []

    def writer():
        for i in range(5):
            try:
                store.incremental_save(
                    owner="local",
                    name="test-abc123",
                    changed_files=["file_0.py"],
                    new_files=[],
                    deleted_files=[],
                    new_symbols=[_sym(f"fn_cycle_{i}", "file_0.py")],
                    raw_files={"file_0.py": f"def fn_cycle_{i}(): pass"},
                    file_hashes={"file_0.py": f"h{i}"},
                )
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    def reader():
        for _ in range(10):
            try:
                store.load_index("local", "test-abc123")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    t_write = threading.Thread(target=writer)
    t_read = threading.Thread(target=reader)
    t_write.start()
    t_read.start()
    t_write.join()
    t_read.join()

    assert not errors, f"Concurrent read/write raised errors: {errors}"


# ---------------------------------------------------------------------------
# 5. Checkpoint-on-close
# ---------------------------------------------------------------------------

def test_checkpoint_compacts_wal(tmp_path):
    """checkpoint_and_close truncates the WAL file to near-zero."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    _full_save(store, n_files=20)

    db_path = store._db_path("local", "test-abc123")
    wal_path = Path(str(db_path) + "-wal")

    # Ensure there is a non-empty WAL to checkpoint
    if not wal_path.exists() or wal_path.stat().st_size == 0:
        # Force some WAL content via an extra write
        store.incremental_save(
            owner="local",
            name="test-abc123",
            changed_files=["file_0.py"],
            new_files=[],
            deleted_files=[],
            new_symbols=[_sym("extra", "file_0.py")],
            raw_files={"file_0.py": "def extra(): pass"},
            file_hashes={"file_0.py": "hextra"},
        )

    store.checkpoint_and_close("local", "test-abc123")

    wal_size_after = wal_path.stat().st_size if wal_path.exists() else 0
    # After TRUNCATE checkpoint the WAL should be 0 bytes (or absent)
    assert wal_size_after == 0, (
        f"WAL should be 0 bytes after checkpoint(TRUNCATE), got {wal_size_after}"
    )


def test_checkpoint_db_by_path(tmp_path):
    """checkpoint_db works with a db_path directly — no owner/name parsing."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    _full_save(store, n_files=5)

    db_path = store._db_path("local", "test-abc123")
    wal_path = Path(str(db_path) + "-wal")

    # Force WAL content
    if not wal_path.exists() or wal_path.stat().st_size == 0:
        store.incremental_save(
            owner="local",
            name="test-abc123",
            changed_files=["file_0.py"],
            new_files=[],
            deleted_files=[],
            new_symbols=[_sym("extra", "file_0.py")],
            raw_files={"file_0.py": "def extra(): pass"},
            file_hashes={"file_0.py": "hextra"},
        )

    store.checkpoint_db(db_path)

    wal_size_after = wal_path.stat().st_size if wal_path.exists() else 0
    assert wal_size_after == 0, (
        f"WAL should be 0 bytes after checkpoint_db, got {wal_size_after}"
    )


def test_index_store_close_checkpoints_all(tmp_path):
    """IndexStore.close() checkpoints all *.db files without slug parsing."""
    from jcodemunch_mcp.storage import IndexStore

    idx = IndexStore(base_path=str(tmp_path))
    store = SQLiteIndexStore(base_path=str(tmp_path))
    _full_save(store, n_files=5)

    db_path = store._db_path("local", "test-abc123")
    wal_path = Path(str(db_path) + "-wal")

    # Force WAL content
    if not wal_path.exists() or wal_path.stat().st_size == 0:
        store.incremental_save(
            owner="local",
            name="test-abc123",
            changed_files=["file_0.py"],
            new_files=[],
            deleted_files=[],
            new_symbols=[_sym("extra", "file_0.py")],
            raw_files={"file_0.py": "def extra(): pass"},
            file_hashes={"file_0.py": "hextra"},
        )

    idx.close()

    wal_size_after = wal_path.stat().st_size if wal_path.exists() else 0
    assert wal_size_after == 0, (
        f"WAL should be 0 bytes after IndexStore.close(), got {wal_size_after}"
    )


# ---------------------------------------------------------------------------
# 6. JSON migration safety
# ---------------------------------------------------------------------------

def _write_legacy_json(tmp_path, owner="local", name="test-abc123"):
    """Write a legacy JSON index file and return its path."""
    json_data = {
        "repo": f"{owner}/{name}",
        "owner": owner,
        "name": name,
        "indexed_at": "2025-01-01T00:00:00",
        "index_version": 4,
        "source_files": ["main.py"],
        "languages": {"python": 1},
        "symbols": [{
            "id": "main.py::greet#function",
            "file": "main.py",
            "name": "greet",
            "qualified_name": "greet",
            "kind": "function",
            "language": "python",
            "signature": "def greet()",
            "summary": "",
            "docstring": "",
            "decorators": [],
            "keywords": [],
            "parent": None,
            "line": 1,
            "end_line": 3,
            "byte_offset": 0,
            "byte_length": 20,
            "content_hash": "",
            "ecosystem_context": "",
        }],
        "file_hashes": {"main.py": "hash1"},
        "git_head": "abc",
        "file_summaries": {"main.py": "Greeting module"},
        "source_root": "/tmp/proj",
        "display_name": "test",
        "file_languages": {"main.py": "python"},
        "imports": {"main.py": [{"specifier": "os", "names": ["path"]}]},
        "context_metadata": {},
        "file_blob_shas": {},
        "file_mtimes": {"main.py": 1234567890000000000},
    }
    json_path = tmp_path / f"{owner}-{name}.json"
    json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")
    return json_path


def test_delete_index_preserves_unmigrated_json(tmp_path):
    """delete_index must NOT delete a .json file if no .db exists yet.

    This prevents data loss when invalidate_cache is called on a repo that
    hasn't been migrated from JSON → SQLite.
    """
    json_path = _write_legacy_json(tmp_path)
    assert json_path.exists()

    store = IndexStore(base_path=str(tmp_path))
    result = store.delete_index("local", "test-abc123")

    # Should report True (something was found) but the JSON is preserved.
    assert result is True
    assert json_path.exists(), (
        "delete_index must NOT delete an unmigrated .json — it is the only "
        "copy of the user's data."
    )


def test_delete_index_removes_json_after_migration(tmp_path):
    """Once a .db exists (data migrated), delete_index CAN remove the .json."""
    json_path = _write_legacy_json(tmp_path)
    store = IndexStore(base_path=str(tmp_path))

    # Trigger migration via load_index
    index = store.load_index("local", "test-abc123")
    assert index is not None, "Migration should succeed"

    db_path = tmp_path / "local-test-abc123.db"
    assert db_path.exists(), ".db must exist after migration"

    # Now delete — should remove the .db (and the .json if still present)
    result = store.delete_index("local", "test-abc123")
    assert result is True
    assert not db_path.exists(), ".db should be gone after delete_index"


def test_list_repos_eagerly_migrates_json(tmp_path):
    """list_repos must eagerly migrate any JSON-only repos to SQLite.

    This ensures that by the time an AI client sees repos in the listing,
    they are all backed by SQLite — preventing the race where invalidate_cache
    is called before load_index triggers lazy migration.
    """
    json_path = _write_legacy_json(tmp_path)
    store = IndexStore(base_path=str(tmp_path))

    repos = store.list_repos()
    assert len(repos) == 1
    assert repos[0]["repo"] == "local/test-abc123"

    # After list_repos, the .db must exist (eager migration happened).
    db_path = tmp_path / "local-test-abc123.db"
    assert db_path.exists(), (
        "list_repos must eagerly migrate JSON → SQLite so that "
        "invalidate_cache cannot destroy unmigrated data."
    )

    # The original .json should be renamed to .json.migrated
    assert not json_path.exists(), ".json should be gone after migration"
    assert (tmp_path / "local-test-abc123.json.migrated").exists()


def test_save_index_cleans_up_zombie_json(tmp_path):
    """save_index must clean up any leftover .json for the same slug.

    This prevents zombie .json files from accumulating after re-indexing.
    """
    json_path = _write_legacy_json(tmp_path)
    store = IndexStore(base_path=str(tmp_path))

    # save_index creates a fresh .db — the old .json should be cleaned up.
    store.save_index(
        owner="local",
        name="test-abc123",
        source_files=["main.py"],
        symbols=[_sym("greet")],
        raw_files={"main.py": "def greet(): pass"},
        file_hashes={"main.py": "h1"},
    )

    db_path = tmp_path / "local-test-abc123.db"
    assert db_path.exists(), ".db must exist after save_index"
    assert not json_path.exists(), (
        "save_index must rename the old .json to .json.migrated"
    )
    assert (tmp_path / "local-test-abc123.json.migrated").exists()
