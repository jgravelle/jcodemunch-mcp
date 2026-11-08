"""A connection that only READS the index must not make another process reload it.

The loaded index and the embedding matrix are cached per process, keyed by a
stamp over the .db file and its WAL sidecars, so a write made by another
process is noticed. Opening a WAL-mode database creates an EMPTY `-wal` file
when none exists, and every read touches `-shm`. The stamps counted both, so
an agent that merely held a read connection made every other process think the
index had been written: each reloaded every row, and with several agents on
one index they kept invalidating each other (the CHANGELOG entry "agents
sharing one index no longer make each other reload it" has the measurement).

A write still has to be noticed: a commit puts frames in `-wal`, so a
NON-empty `-wal` counts, by size and mtime. These tests pin both directions.
A second sqlite3 connection in the same process stands in for the other
process; SQLite's sidecar files do not care which process opened them.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import embedding_matrix, sqlite_store
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore


@pytest.fixture
def indexed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    store_dir = tmp_path / "store"
    from jcodemunch_mcp.tools.index_folder import index_folder

    res = index_folder(path=str(repo), use_ai_summaries=False, storage_path=str(store_dir))
    assert res.get("success"), res
    store = SQLiteIndexStore(base_path=str(store_dir))
    owner, name = store.list_repos()[0]["repo"].split("/", 1)
    db = next(store_dir.glob("*.db"))
    for suffix in ("-wal", "-shm"):
        assert not Path(str(db) + suffix).exists(), "the fixture must start with no sidecars"
    return store, owner, name, db


def _hold_a_reader(db: Path) -> sqlite3.Connection:
    """Another agent's open read connection: creates an empty -wal and writes -shm."""
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("SELECT count(*) FROM symbols").fetchone()
    wal = Path(str(db) + "-wal")
    assert wal.exists() and wal.stat().st_size == 0, "expected the empty -wal a reader creates"
    return con


def _commit_from_elsewhere(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('_probe', 'x')")
    con.commit()
    return con


def test_a_held_read_connection_does_not_force_a_reload(indexed):
    store, owner, name, db = indexed
    first = store.load_index(owner, name)
    reader = _hold_a_reader(db)
    try:
        assert store.load_index(owner, name) is first
    finally:
        reader.close()


def test_a_commit_while_a_reader_is_open_still_forces_a_reload(indexed):
    store, owner, name, db = indexed
    first = store.load_index(owner, name)
    reader = _hold_a_reader(db)
    writer = _commit_from_elsewhere(db)
    try:
        assert Path(str(db) + "-wal").stat().st_size > 0
        assert store.load_index(owner, name) is not first
    finally:
        writer.close()
        reader.close()


def test_the_index_stamp_ignores_an_empty_wal(indexed):
    _, _, _, db = indexed
    before = sqlite_store._db_mtime_ns(db)
    reader = _hold_a_reader(db)
    try:
        assert sqlite_store._db_mtime_ns(db) == before
    finally:
        reader.close()


def test_the_embedding_stamp_ignores_a_reader(indexed):
    _, _, _, db = indexed
    before = embedding_matrix._stamp(db)
    reader = _hold_a_reader(db)
    try:
        assert embedding_matrix._stamp(db) == before
    finally:
        reader.close()


def test_the_embedding_stamp_sees_a_commit(indexed):
    _, _, _, db = indexed
    reader = _hold_a_reader(db)
    try:
        before = embedding_matrix._stamp(db)
        writer = _commit_from_elsewhere(db)
        try:
            assert embedding_matrix._stamp(db) != before
        finally:
            writer.close()
    finally:
        reader.close()
