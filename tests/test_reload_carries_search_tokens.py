"""A reload after another process writes keeps the search tokens of symbols that did not change.

A running server caches the loaded index keyed by the .db mtime. When another
process (the `watch-all` service, an editor's server, a hook) writes the index,
the next search reloads every row, which is unavoidable today, and then
re-tokenizes every symbol for BM25, which is not. Measured on a 34,225-symbol
index, the re-tokenizing was most of the first search after a write (see the
CHANGELOG entry "a search after another process writes the index no longer
re-tokenizes every symbol").
`incremental_save` already carried them for its own writes; a write from
another process skipped the carry.

A token bag is built from name, keywords, signature, summary and docstring. A
summary can change while the source (content_hash) does not, so the carry
requires every one of those to match. These tests pin both directions.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp.retrieval.scoring import _sym_tokens
from jcodemunch_mcp.storage import sqlite_store
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore


@pytest.fixture
def indexed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text(
        'def alpha():\n    """Alpha docs."""\n    return 1\n\n\ndef beta():\n    return 2\n',
        encoding="utf-8",
    )
    (repo / "b.py").write_text("def gamma():\n    return 3\n", encoding="utf-8")
    store_dir = tmp_path / "store"
    from jcodemunch_mcp.tools.index_folder import index_folder

    res = index_folder(path=str(repo), use_ai_summaries=False, storage_path=str(store_dir))
    assert res.get("success"), res
    store = SQLiteIndexStore(base_path=str(store_dir))
    owner, name = store.list_repos()[0]["repo"].split("/", 1)
    db = next(store_dir.glob("*.db"))
    return store, owner, name, db


def _by_name(index):
    return {s["name"]: s for s in index.symbols}


def _write_from_elsewhere(db: Path, sql: str, params=()) -> None:
    """A write the cache did not see: a separate connection, as another process makes."""
    before = sqlite_store._db_mtime_ns(db)
    con = sqlite3.connect(str(db))
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()
    # Same-second writes can leave the mtime unchanged on coarse filesystems.
    if sqlite_store._db_mtime_ns(db) == before:
        import os

        st = db.stat()
        os.utime(db, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def test_a_reload_after_an_outside_write_keeps_unchanged_tokens(indexed):
    store, owner, name, db = indexed
    first = store.load_index(owner, name)
    for s in first.symbols:
        _sym_tokens(s)
    old_gamma_tokens = _by_name(first)["gamma"]["_tokens"]

    _write_from_elsewhere(db, "INSERT OR REPLACE INTO meta(key, value) VALUES ('_probe', '1')")
    second = store.load_index(owner, name)
    assert second is not first, "the write did not force a reload"
    assert _by_name(second)["gamma"].get("_tokens") is old_gamma_tokens


def test_a_changed_summary_is_tokenized_again(indexed):
    store, owner, name, db = indexed
    first = store.load_index(owner, name)
    for s in first.symbols:
        _sym_tokens(s)
    beta_id = _by_name(first)["beta"]["id"]

    _write_from_elsewhere(
        db, "UPDATE symbols SET summary = ? WHERE id = ?", ("quokka marsupial helper", beta_id)
    )
    second = store.load_index(owner, name)
    beta = _by_name(second)["beta"]
    assert "_tokens" not in beta, "a stale token bag was carried over a new summary"
    assert "quokka" in _sym_tokens(beta)
    # The search a user runs finds the new words.
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    out = search_symbols(repo=f"{owner}/{name}", query="quokka", storage_path=str(db.parent))
    names = [r.get("name") for r in out.get("results", [])]
    assert "beta" in names, out


@pytest.mark.parametrize("field", ["name", "keywords", "signature", "summary", "docstring"])
def test_any_token_input_that_differs_blocks_the_carry(field):
    old_sym = {
        "id": "a.py::f#function",
        "name": "f",
        "keywords": ["k"],
        "signature": "def f()",
        "summary": "s",
        "docstring": "d",
        "content_hash": "h",
    }
    _sym_tokens(old_sym)
    new_sym = {k: v for k, v in old_sym.items() if not k.startswith("_")}
    new_sym[field] = ["other"] if field == "keywords" else "other"

    class _Idx:
        def __init__(self, symbols):
            self.symbols = symbols

    from jcodemunch_mcp.storage.sqlite_store import _carry_bm25_tokens

    carried = _carry_bm25_tokens(_Idx([old_sym]), _Idx([new_sym]))
    assert carried == 0
    assert "_tokens" not in new_sym


def test_a_matching_symbol_is_carried():
    old_sym = {"id": "x", "name": "f", "signature": "def f()", "content_hash": "h"}
    _sym_tokens(old_sym)
    new_sym = {"id": "x", "name": "f", "signature": "def f()", "content_hash": "h"}

    class _Idx:
        def __init__(self, symbols):
            self.symbols = symbols

    from jcodemunch_mcp.storage.sqlite_store import _carry_bm25_tokens

    assert _carry_bm25_tokens(_Idx([old_sym]), _Idx([new_sym])) == 1
    assert new_sym["_tokens"] is old_sym["_tokens"]


def test_an_own_save_after_an_outside_write_keeps_unchanged_tokens(indexed):
    """The second route: another process writes, then this one saves with no load between.

    `index_folder` loads before it saves, so that load's carry already serves it;
    a writer that calls `incremental_save` on a cache the outside write made
    stale (as `index_file` can) rebuilds from rows, and the stale copy's bags
    must carry there too.
    """
    store, owner, name, db = indexed
    first = store.load_index(owner, name)
    for s in first.symbols:
        _sym_tokens(s)
    old_gamma_tokens = _by_name(first)["gamma"]["_tokens"]
    _write_from_elsewhere(db, "INSERT OR REPLACE INTO meta(key, value) VALUES ('_probe', '2')")

    saved = store.incremental_save(owner, name, [], [], [], [], {})
    assert saved is not None and saved is not first, "the save took the patch path"
    assert _by_name(saved)["gamma"].get("_tokens") is old_gamma_tokens


def _carry_one(old_sym, new_sym):
    from jcodemunch_mcp.storage.sqlite_store import _carry_bm25_tokens

    class _Idx:
        def __init__(self, symbols):
            self.symbols = symbols

    return _carry_bm25_tokens(_Idx([old_sym]), _Idx([new_sym]))


def test_a_different_content_hash_blocks_the_carry():
    old_sym = {"id": "x", "name": "f", "content_hash": "h1"}
    _sym_tokens(old_sym)
    new_sym = {"id": "x", "name": "f", "content_hash": "h2"}
    assert _carry_one(old_sym, new_sym) == 0
    assert "_tokens" not in new_sym


def test_a_missing_content_hash_blocks_the_carry():
    old_sym = {"id": "x", "name": "f", "content_hash": "h"}
    _sym_tokens(old_sym)
    new_sym = {"id": "x", "name": "f"}
    assert _carry_one(old_sym, new_sym) == 0
    assert "_tokens" not in new_sym
