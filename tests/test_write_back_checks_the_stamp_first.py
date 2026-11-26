"""A write-back the store will refuse is not computed first (LEDGER L-346).

`bm25_stats.flush_pending` computed the corpus digest and encoded the stats row,
and `token_counts.flush_pending` read and counted the file copies, before the
store checked that the .db was still as the index was loaded. Under a watcher
the .db moves between a search and its flush, so every reader paid for work the
store then refused. One predicate, `loaded_index_can_take_a_write_back`, now
says whether a write-back would pass; both flushes ask it first, and the store
applies the same rule (`_write_back_target`) under its lock.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from jcodemunch_mcp import savings_tokenizer
from jcodemunch_mcp.storage import bm25_stats, sqlite_store, token_counts
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols


def _indexed(tmp_path):
    repo, store_dir = tmp_path / "repo", tmp_path / "store"
    repo.mkdir()
    for i in range(6):
        (repo / f"m{i}.py").write_text(f"def alpha_{i}():\n    return {i}\n", encoding="utf-8")
    r = index_folder(path=str(repo), use_ai_summaries=False, storage_path=str(store_dir))
    assert r.get("success"), r
    store = SQLiteIndexStore(base_path=str(store_dir))
    owner, name = r["repo"].split("/", 1)
    db_path = store._db_path(owner, store._safe_repo_component(name, "name"))
    return r["repo"], str(store_dir), store, owner, name, db_path


def _move_stamp(db_path: Path) -> None:
    """Another process's write, as the stamp sees it."""
    st = os.stat(db_path)
    os.utime(db_path, ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000_000))


def _queue_bm25(repo, store_dir, db_path):
    with bm25_stats._pending_lock:
        bm25_stats._pending.clear()
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(f"DELETE FROM {bm25_stats.TABLE}")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # no stats table yet: nothing stored
    finally:
        conn.close()
    sqlite_store._cache_clear()
    search_symbols(repo=repo, query="alpha", storage_path=store_dir)
    with bm25_stats._pending_lock:
        assert len(bm25_stats._pending) == 1, "the search queued no statistics"


def _spy(monkeypatch, module, attr):
    calls = []
    real = getattr(module, attr)

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(module, attr, spy)
    return calls


def test_a_bm25_flush_to_a_moved_db_computes_nothing(tmp_path, monkeypatch):
    repo, store_dir, _store, _o, _n, db_path = _indexed(tmp_path)
    _queue_bm25(repo, store_dir, db_path)
    _move_stamp(db_path)
    digests = _spy(monkeypatch, bm25_stats, "corpus_digest")
    encodes = _spy(monkeypatch, bm25_stats, "encode")
    assert bm25_stats.flush_pending() == 0
    assert digests == [] and encodes == []


def test_a_bm25_flush_to_an_unmoved_db_still_stores(tmp_path, monkeypatch):
    """Control: the pre-check refuses only what the store would refuse."""
    repo, store_dir, _store, _o, _n, db_path = _indexed(tmp_path)
    _queue_bm25(repo, store_dir, db_path)
    digests = _spy(monkeypatch, bm25_stats, "corpus_digest")
    assert bm25_stats.flush_pending() == 1
    assert digests == [1]


def _queue_token_counts(store, owner, name, db_path):
    with token_counts._pending_lock:
        token_counts._pending.clear()
        token_counts._uncountable.clear()
    sqlite_store._cache_clear()
    index = store.load_index(owner, name)
    copy = Path(db_path).with_suffix("") / "m0.py"
    with open(copy, encoding="utf-8", newline="") as f:  # the bytes the flush hashes
        text = f.read()
    token_counts.queue(index, {token_counts._content_hash(text): "m0.py"})
    with token_counts._pending_lock:
        assert token_counts._pending, "nothing was queued"


def test_a_token_count_flush_to_a_moved_db_reads_nothing(tmp_path, monkeypatch):
    _repo, _sd, store, owner, name, db_path = _indexed(tmp_path)
    _queue_token_counts(store, owner, name, db_path)
    _move_stamp(db_path)
    monkeypatch.setattr(savings_tokenizer, "encoding", lambda: object())
    counted = _spy(monkeypatch, savings_tokenizer, "count_many")
    reads = _spy(monkeypatch, token_counts, "_read_copy")
    assert token_counts.flush_pending() == 0
    assert reads == [] and counted == []


def test_a_token_count_flush_to_an_unmoved_db_still_reads(tmp_path, monkeypatch):
    """Control: with the .db as loaded the flush reads and counts the copy."""
    _repo, _sd, store, owner, name, db_path = _indexed(tmp_path)
    _queue_token_counts(store, owner, name, db_path)
    monkeypatch.setattr(savings_tokenizer, "encoding", lambda: object())
    monkeypatch.setattr(savings_tokenizer, "count_many", lambda texts: [1] * len(texts))
    reads = _spy(monkeypatch, token_counts, "_read_copy")
    assert token_counts.flush_pending() == 1
    assert reads == [1]


def test_the_predicate_is_the_rule_the_store_applies(tmp_path):
    """`loaded_index_can_take_a_write_back` and the store's refusal agree:
    a moved .db, a branch view and an index with no provenance all refuse."""
    _repo, _sd, store, owner, name, db_path = _indexed(tmp_path)
    sqlite_store._cache_clear()
    index = store.load_index(owner, name)
    can = sqlite_store.loaded_index_can_take_a_write_back
    assert can(index)

    class NoProvenance:
        owner, name, branch = index.owner, index.name, ""

    assert not can(NoProvenance())
    index.branch = "feature"
    try:
        assert not can(index)
        assert not sqlite_store._write_back_to_loaded_index(index, lambda conn: None, "test")
    finally:
        index.branch = ""
    _move_stamp(db_path)
    assert not can(index)
    assert not sqlite_store._write_back_to_loaded_index(index, lambda conn: None, "test")

    # Where the predicate says yes, the store stores.
    sqlite_store._cache_clear()
    fresh = store.load_index(owner, name)
    assert can(fresh)
    assert sqlite_store._write_back_to_loaded_index(fresh, lambda conn: None, "test")


def test_a_stamp_that_cannot_be_read_refuses(tmp_path, monkeypatch):
    """A stamp that could not be read is never taken as unchanged."""
    from jcodemunch_mcp.storage import generation

    _repo, _sd, store, owner, name, _db = _indexed(tmp_path)
    sqlite_store._cache_clear()
    index = store.load_index(owner, name)
    assert sqlite_store.loaded_index_can_take_a_write_back(index)
    monkeypatch.setattr(generation, "db_mtime_ns", lambda _p: None)
    assert not sqlite_store.loaded_index_can_take_a_write_back(index)
    assert not sqlite_store._write_back_to_loaded_index(index, lambda conn: None, "test")
