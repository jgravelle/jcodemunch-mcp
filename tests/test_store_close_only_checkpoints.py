"""LEDGER L-143: `IndexStore.close()` checkpoints, and does nothing else.

`close()` is the shutdown hook: it globs every `*.db` under the storage path
and compacts its WAL. It opened each one through `SQLiteIndexStore._connect`,
the connection the indexer uses, which on a first visit creates the index
schema or runs the version migrations. So a shutdown:

* raised `no such table: files` on a database another thread or process was
  still creating (the `meta` table exists, the version row and `files` do not,
  so every migration since v4 ran against it). Seen once on CI, in the watcher
  shutdown path, where the cancelled initial index is still running in its
  thread when the store is closed;
* created the index tables inside any other SQLite file in the directory
  (`telemetry.db` lives there).

A checkpoint needs neither. `close()` opens a plain connection, checkpoints,
and logs what it could not do; a shutdown hook has nobody to raise to.
"""

import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.index_folder import index_folder

NL = chr(10)


def _tables(db_path: Path) -> set:
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def _half_created(db_path: Path) -> None:
    """The state between the first statement of the schema script and the rest."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
    finally:
        conn.close()


def test_close_does_not_raise_on_a_database_still_being_created(tmp_path):
    db = tmp_path / "local-half-00000000.db"
    _half_created(db)

    IndexStore(base_path=str(tmp_path)).close()

    assert _tables(db) == {"meta"}


def test_close_does_not_create_the_index_schema_in_another_database(tmp_path):
    other = tmp_path / "telemetry.db"
    conn = sqlite3.connect(str(other))
    conn.execute("CREATE TABLE tool_calls (tool TEXT, ms REAL)")
    conn.execute("INSERT INTO tool_calls VALUES ('search_symbols', 1.5)")
    conn.commit()
    conn.close()

    IndexStore(base_path=str(tmp_path)).close()

    assert _tables(other) == {"tool_calls"}


def test_close_does_not_raise_on_a_file_that_is_not_a_database(tmp_path):
    (tmp_path / "garbage.db").write_bytes(b"this is not sqlite" * 64)

    IndexStore(base_path=str(tmp_path)).close()


def test_close_goes_on_to_the_next_database_after_one_it_cannot_open(tmp_path):
    """One unreadable file must not leave every later WAL uncompacted."""
    src = tmp_path / "src"
    store_dir = tmp_path / "store"
    src.mkdir()
    store_dir.mkdir()
    (src / "m.py").write_text("def f():" + NL + "    return 1" + NL, encoding="utf-8")
    (store_dir / "aaa-garbage.db").write_bytes(b"this is not sqlite" * 64)
    indexed = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_dir))
    assert indexed["success"] is True, indexed
    owner, name = indexed["repo"].split("/", 1)
    store = IndexStore(base_path=str(store_dir))
    db = store._sqlite._db_path(owner, name)
    assert sorted(p.name for p in store_dir.glob("*.db"))[0] == "aaa-garbage.db"

    # A reader holds the WAL open, so it survives until something checkpoints.
    writer = sqlite3.connect(str(db))
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('l143_probe', 'x')")
    writer.commit()
    wal = Path(str(db) + "-wal")
    assert wal.exists() and wal.stat().st_size > 0
    try:
        store.close()
        assert wal.stat().st_size == 0
    finally:
        writer.close()


def test_close_still_compacts_the_wal_of_an_index(tmp_path):
    """What the hook is for, unchanged."""
    src = tmp_path / "src"
    store_dir = tmp_path / "store"
    src.mkdir()
    store_dir.mkdir()
    (src / "m.py").write_text("def f():" + NL + "    return 1" + NL, encoding="utf-8")
    indexed = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_dir))
    owner, name = indexed["repo"].split("/", 1)
    store = IndexStore(base_path=str(store_dir))
    db = store._sqlite._db_path(owner, name)
    before = _tables(db)

    store.close()

    assert _tables(db) == before
    assert store.load_index(owner, name) is not None


def test_the_watcher_shutdown_does_not_raise_when_the_store_cannot_be_closed(tmp_path, monkeypatch):
    """The path the CI failure took: the last line of `_run_server_with_watcher`."""
    pytest.importorskip("watchfiles")
    import asyncio

    from jcodemunch_mcp import server

    half = tmp_path / "store"
    half.mkdir()
    _half_created(half / "local-half-00000000.db")
    folder = tmp_path / "proj"
    folder.mkdir()

    class Manager:
        _watched: set = set()

        def __init__(self, *args, **kwargs):
            self._stop_event = None

        async def run(self):
            await self._stop_event.wait()

        def stop(self):
            self._stop_event.set()

    monkeypatch.setattr(server, "WatcherManager", Manager)

    async def fake_server():
        await asyncio.sleep(0)

    asyncio.run(server._run_server_with_watcher(
        fake_server, (),
        dict(paths=[], debounce_ms=2000, use_ai_summaries=False, storage_path=str(half),
             extra_ignore_patterns=None, follow_symlinks=False, idle_timeout_minutes=None),
    ))
    assert _tables(half / "local-half-00000000.db") == {"meta"}
