"""A write that lands during another process's load is not hidden from it (LEDGER L-342).

`load_index` sampled the .db stamp, read the rows, then RE-STATTED the .db and
cached the snapshot under the second stamp. A commit that landed while the read
transaction was open moved that stamp, so the older snapshot was cached under
the newer stamp and served as current until some later write. The entry is now
keyed at the stamp sampled before the read: a move during the read makes the
next call miss, and a move that changed nothing (a checkpoint) is re-keyed by
`_revalidate_cached` from one meta read, with no reload, for a base index
that records a content generation; a branch view, or an index without one,
reloads once.

"Another process" is simulated as the repro does: this process's cache entry is
put back after each write made here, because a write in this process re-keys its
own cache.
"""

from __future__ import annotations

import os

from jcodemunch_mcp.storage import sqlite_store
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore
from jcodemunch_mcp.tools.index_folder import index_folder


def _setup(tmp_path):
    repo, store_dir = tmp_path / "repo", tmp_path / "store"
    repo.mkdir()
    for i in range(6):
        (repo / f"m{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")

    def reindex(**kw):
        r = index_folder(path=str(repo), use_ai_summaries=False, storage_path=str(store_dir), **kw)
        assert r.get("success"), r

    reindex()
    store = SQLiteIndexStore(base_path=str(store_dir))
    owner, name = store.list_repos()[0]["repo"].split("/", 1)
    key = (owner, store._safe_repo_component(name, "name"), "")
    return repo, store, owner, name, key, reindex


def _as_another_process(key, write):
    entry = sqlite_store._index_cache.get(key)
    sqlite_store._index_cache.clear()
    write()
    sqlite_store._index_cache.clear()
    if entry is not None:
        sqlite_store._index_cache[key] = entry


def _during_read(monkeypatch, action):
    """Run `action` once, inside the load's open read transaction."""
    fired = []
    real = SQLiteIndexStore._read_meta

    def read_meta_then_act(self, conn):
        meta = real(self, conn)
        if not fired:
            fired.append(1)
            action()
        return meta

    monkeypatch.setattr(SQLiteIndexStore, "_read_meta", read_meta_then_act)
    return fired


def _names(index):
    return {s["name"] for s in index.symbols}


def test_a_write_during_another_load_is_seen_by_the_next_call(tmp_path, monkeypatch):
    repo, store, owner, name, key, reindex = _setup(tmp_path)
    sqlite_store._index_cache.clear()
    store.load_index(owner, name)

    def write(path, body):
        def w():
            (repo / path).write_text(body, encoding="utf-8")
            reindex(incremental=True)
        return w

    _as_another_process(key, write("m1.py", "def f1():\n    return 1\n\n\ndef first_write():\n    return 1\n"))
    fired = _during_read(
        monkeypatch,
        lambda: _as_another_process(
            key, write("m2.py", "def f2():\n    return 2\n\n\ndef second_write():\n    return 2\n")
        ),
    )
    store.load_index(owner, name)  # the load that races write 2
    monkeypatch.undo()
    after = store.load_index(owner, name)
    assert fired
    assert "first_write" in _names(after)
    assert "second_write" in _names(after)


def test_the_raced_load_is_not_stamped_as_loaded_after_the_write(tmp_path, monkeypatch):
    repo, store, owner, name, key, reindex = _setup(tmp_path)
    sqlite_store._index_cache.clear()
    db_path = store._db_path(owner, key[1])
    pre = sqlite_store._db_mtime_ns(db_path)

    def write():
        (repo / "m3.py").write_text("def f3():\n    return 3\n\n\ndef raced():\n    return 3\n", encoding="utf-8")
        reindex(incremental=True)

    fired = _during_read(monkeypatch, lambda: _as_another_process(key, write))
    raced = store.load_index(owner, name)
    assert fired, "the in-read write never ran, so nothing was tested"
    assert sqlite_store._db_mtime_ns(db_path) != pre
    assert "raced" not in _names(raced)  # the snapshot predates the write
    assert raced._loaded_mtime_ns == pre
    with sqlite_store._cache_lock:
        assert sqlite_store._index_cache[key].mtime_ns == pre


def test_a_move_that_changed_nothing_is_rekeyed_without_a_reload(tmp_path, monkeypatch):
    """The re-stat existed to absorb a stamp move the load did not cause by
    writing (a checkpoint). That case must still cost one meta read, not a
    reload."""
    _repo, store, owner, name, key, _reindex = _setup(tmp_path)
    sqlite_store._index_cache.clear()
    db_path = store._db_path(owner, key[1])

    def touch():
        st = os.stat(db_path)
        os.utime(db_path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))

    fired = _during_read(monkeypatch, touch)
    first = store.load_index(owner, name)
    monkeypatch.undo()
    assert fired, "the in-read move never ran, so nothing was tested"
    with sqlite_store._cache_lock:
        assert sqlite_store._index_cache[key].mtime_ns != sqlite_store._db_mtime_ns(db_path)

    builds = []
    real_build = SQLiteIndexStore._build_index_from_rows

    def counting(self, *a, **k):
        builds.append(1)
        return real_build(self, *a, **k)

    monkeypatch.setattr(SQLiteIndexStore, "_build_index_from_rows", counting)
    again = store.load_index(owner, name)
    assert again is first
    assert builds == []
    with sqlite_store._cache_lock:
        assert sqlite_store._index_cache[key].mtime_ns == sqlite_store._db_mtime_ns(db_path)
