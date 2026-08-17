import os

from jcodemunch_mcp.storage.index_store import IndexStore
from jcodemunch_mcp.storage.sqlite_store import (
    SQLiteIndexStore,
    _VERIFIED_PATHS,
)


def test_relative_storage_paths_are_distinct_after_cwd_changes(tmp_path):
    work_a = tmp_path / "work-a"
    work_b = tmp_path / "work-b"
    work_a.mkdir()
    work_b.mkdir()
    original_cwd = os.getcwd()

    _VERIFIED_PATHS.clear()
    SQLiteIndexStore._initialized_dbs.clear()
    try:
        os.chdir(work_a)
        store_a = SQLiteIndexStore(base_path="store")
        store_a._connect(store_a.base_path / "repo.db").close()

        os.chdir(work_b)
        store_b = SQLiteIndexStore(base_path="store")
        store_b._connect(store_b.base_path / "repo.db").close()

        assert store_a.base_path == work_a / "store"
        assert store_b.base_path == work_b / "store"
        assert store_a.base_path != store_b.base_path
        assert len(SQLiteIndexStore._initialized_dbs) == 2
        assert (work_a / "store" / "repo.db").exists()
        assert (work_b / "store" / "repo.db").exists()
    finally:
        os.chdir(original_cwd)
        _VERIFIED_PATHS.clear()
        SQLiteIndexStore._initialized_dbs.clear()


def test_index_store_passes_resolved_path_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = IndexStore(base_path="relative-store")

    assert store.base_path == (tmp_path / "relative-store").resolve()
    assert store._sqlite.base_path == store.base_path
