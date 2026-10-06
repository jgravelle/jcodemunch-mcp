"""A full re-index keeps the embedding metadata beside the vectors it describes (#522).

`save_index` cleared the whole `meta` table, and `EmbeddingStore` keeps
`embed_dimension` / `embed_model` / `embed_task_type` there. A full save left
`symbol_embeddings` in place, so the store held vectors with no record of what
produced them. A semantic `search_symbols` then stamped the store with the
current model as it embedded the symbols with no vector, and after a model
change `embed_repo`'s check (#500) compared the new model with itself.

The keys are read off `embedding_store` rather than listed here, so a key the
embedding store adds later is covered on arrival.
"""

import sqlite3

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage import embedding_store as es_mod
from jcodemunch_mcp.storage.embedding_store import EmbeddingStore
from jcodemunch_mcp.tools.index_folder import index_folder

EMBED_KEYS = sorted(
    value for name, value in vars(es_mod).items()
    if name.startswith("_EMBED_") and name.endswith("_KEY")
)


def _index(src, store, **kw):
    result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store), **kw)
    assert result["success"] is True, result
    return result


def _db_path(store, repo):
    owner, name = repo.split("/", 1)
    return IndexStore(base_path=str(store))._sqlite._db_path(owner, name)


def _meta(db):
    conn = sqlite3.connect(str(db))
    try:
        return dict(conn.execute("SELECT key, value FROM meta").fetchall())
    finally:
        conn.close()


@pytest.fixture
def embedded(tmp_path):
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    store.mkdir()
    (src / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    result = _index(src, store)
    db = _db_path(store, result["repo"])
    es = EmbeddingStore(db)
    # what a completed embed_repo leaves behind
    es.set_dimension(8, "BAAI/bge-m3")
    es.set_task_type("")
    es.set_many({"main.py::main#function": [0.1] * 8})
    return src, store, db, es


def test_the_embedding_store_declares_its_meta_keys():
    assert len(EMBED_KEYS) >= 3
    assert all(key.startswith(es_mod.META_KEY_PREFIX) for key in EMBED_KEYS)


def test_a_full_reindex_keeps_the_embedding_metadata(embedded):
    src, store, db, es = embedded
    before = {k: v for k, v in _meta(db).items() if k in EMBED_KEYS}
    assert sorted(before) == EMBED_KEYS

    (src / "extra.py").write_text("def extra():\n    return 2\n", encoding="utf-8")
    _index(src, store, incremental=False)

    assert es.count() == 1
    assert {k: v for k, v in _meta(db).items() if k in EMBED_KEYS} == before
    assert (es.get_dimension(), es.get_model(), es.get_task_type()) == (8, "BAAI/bge-m3", "")


def test_a_full_save_still_replaces_the_index_own_keys(embedded):
    """The save drops what it owns: an index key the new index does not write
    must not survive from the old one."""
    src, store, db, _es = embedded
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('stale_index_key', 'old')")
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('embedx', 'not the store')")
    conn.commit()
    conn.close()

    _index(src, store, incremental=False)

    meta = _meta(db)
    assert "stale_index_key" not in meta
    assert "embedx" not in meta
    assert meta["index_version"]
