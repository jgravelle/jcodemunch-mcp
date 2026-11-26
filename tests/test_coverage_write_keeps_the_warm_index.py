"""A coverage or cap-status write keeps this process's warm index usable (LEDGER L-343).

`set_coverage` runs after every save that walked the tree, and
`set_file_cap_status` after a run that saved nothing else. Each commits one meta
row, which moves the .db stamp, and patched the cached index's attribute in
place without moving its key. So the next `incremental_save` in the same process
missed the cache and rebuilt the whole index from every row. The write now
re-keys the warm entry under `indexwrite` when the meta table differs from the
one the entry was cached with in this write's own row alone; so does
`stamp_parser_generation`, the third lock-free meta writer.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import process_locks, sqlite_store
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore
from jcodemunch_mcp.tools.index_folder import index_folder


def _indexed(tmp_path):
    repo, store_dir = tmp_path / "repo", tmp_path / "store"
    repo.mkdir()
    for i in range(6):
        (repo / f"m{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")

    def reindex(**kw):
        r = index_folder(path=str(repo), use_ai_summaries=False, storage_path=str(store_dir), **kw)
        assert r.get("success"), r
        return r

    reindex()
    store = SQLiteIndexStore(base_path=str(store_dir))
    owner, name = store.list_repos()[0]["repo"].split("/", 1)
    safe = store._safe_repo_component(name, "name")
    return repo, store_dir, store, owner, name, safe, reindex


def _entry(owner, safe):
    with sqlite_store._cache_lock:
        return sqlite_store._index_cache.get((owner, safe, ""))


def test_an_incremental_index_after_a_coverage_write_patches_in_memory(tmp_path, monkeypatch):
    repo, _sd, _store, _owner, _name, _safe, reindex = _indexed(tmp_path)
    builds = []
    real_build = SQLiteIndexStore._build_index_from_rows

    def counting(self, *a, **k):
        builds.append(1)
        return real_build(self, *a, **k)

    monkeypatch.setattr(SQLiteIndexStore, "_build_index_from_rows", counting)
    for i in range(3):
        (repo / "m1.py").write_text(f"def f1():\n    return {i}\n\n\ndef added_{i}():\n    return {i}\n", encoding="utf-8")
        r = reindex(incremental=True)
        assert r.get("performed_incremental"), r
    assert builds == [], f"three incremental runs rebuilt the index from every row {len(builds)} times"


def test_set_coverage_rekeys_the_warm_entry_at_the_new_stamp(tmp_path):
    _repo, _sd, store, owner, name, safe, _r = _indexed(tmp_path)
    warm = store.load_index(owner, name)
    db_path = store._db_path(owner, safe)
    store.set_coverage(owner, name, {"complete": True, "marker": "L-343"})
    entry = _entry(owner, safe)
    assert entry is not None and entry.code_index is warm
    assert entry.mtime_ns == sqlite_store._db_mtime_ns(db_path)
    assert warm.coverage == {"complete": True, "marker": "L-343"}
    assert store.load_index(owner, name) is warm


def test_set_file_cap_status_rekeys_the_warm_entry_at_the_new_stamp(tmp_path):
    _repo, _sd, store, owner, name, safe, _r = _indexed(tmp_path)
    warm = store.load_index(owner, name)
    db_path = store._db_path(owner, safe)
    store.set_file_cap_status(owner, name, {"capped": False, "marker": "L-343"})
    entry = _entry(owner, safe)
    assert entry is not None and entry.code_index is warm
    assert entry.mtime_ns == sqlite_store._db_mtime_ns(db_path)
    assert warm.file_cap_status == {"capped": False, "marker": "L-343"}


def test_stamp_parser_generation_rekeys_the_warm_entry_at_the_new_stamp(tmp_path):
    """The third lock-free meta writer, with the same rule (review round 3)."""
    _repo, _sd, store, owner, name, safe, _r = _indexed(tmp_path)
    warm = store.load_index(owner, name)
    db_path = store._db_path(owner, safe)
    target = warm.parser_generation + 1
    assert store.stamp_parser_generation(owner, name, target)
    entry = _entry(owner, safe)
    assert entry is not None and entry.code_index is warm
    assert entry.mtime_ns == sqlite_store._db_mtime_ns(db_path)
    assert warm.parser_generation == target
    assert store.load_index(owner, name) is warm


def _foreign_symbol_write(db_path, sid, *, keep_stamp=False):
    """Another process's symbol write, as a real writer makes it: the row and a
    new content generation in one commit. `keep_stamp` puts the .db and -wal
    mtimes back, so the stamp reads as unmoved (two writes inside one clock
    tick)."""
    _foreign_commit(
        db_path,
        [("UPDATE symbols SET summary = 'foreign' WHERE id = ?", (sid,)),
         ("INSERT OR REPLACE INTO meta (key, value) VALUES ('content_generation', ?)",
          (uuid.uuid4().hex,))],
        keep_stamp=keep_stamp,
    )


def _foreign_commit(db_path, statements, *, keep_stamp=False):
    before = sqlite_store._db_mtime_ns(db_path)
    conn = sqlite3.connect(str(db_path), timeout=5)
    try:
        for sql, args in statements:
            conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()
    if keep_stamp:
        for path in (db_path, Path(str(db_path) + "-wal")):
            if path.exists():
                os.utime(path, ns=(before, before))
        assert sqlite_store._db_mtime_ns(db_path) == before


def _summary(store, owner, name, sid):
    return {s["id"]: s.get("summary") for s in store.load_index(owner, name).symbols}[sid]


def _once(fired, action):
    def run(*_a, **_k):
        if not fired:
            fired.append(1)
            action()
    return run


def _before_the_rekey_lock(monkeypatch, fired, action):
    """Run `action` once, after the write and before `indexwrite` is taken."""
    real_held = process_locks.held

    def held(*a, **k):
        _once(fired, action)()
        return real_held(*a, **k)

    monkeypatch.setattr(process_locks, "held", held)


def test_both_saves_cache_the_meta_they_wrote(tmp_path):
    """The re-key compares against the meta an entry was cached with, so a
    save's pre-warm must record it (it recorded none before L-343)."""
    repo, _sd, store, owner, name, safe, reindex = _indexed(tmp_path)
    sqlite_store._cache_clear()
    reindex(incremental=False)
    assert _entry(owner, safe).meta, "a full save cached no meta"
    (repo / "m1.py").write_text("def f1():\n    return 11\n", encoding="utf-8")
    sqlite_store._cache_clear()
    store.load_index(owner, name)
    reindex(incremental=True)
    assert _entry(owner, safe).meta, "an incremental save cached no meta"


def test_an_entry_with_no_meta_is_not_moved(tmp_path):
    _repo, _sd, store, owner, name, safe, _r = _indexed(tmp_path)
    store.load_index(owner, name)
    with sqlite_store._cache_lock:
        key = (owner, safe, "")
        sqlite_store._index_cache[key] = sqlite_store._index_cache[key]._replace(meta=None)
    before = _entry(owner, safe).mtime_ns
    store.set_coverage(owner, name, {"complete": True})
    assert _entry(owner, safe).mtime_ns == before


def test_a_foreign_commit_right_after_the_write_is_not_hidden(tmp_path, monkeypatch):
    """Another process commits between our COMMIT and the re-key."""
    _repo, _sd, store, owner, name, safe, _r = _indexed(tmp_path)
    sid = store.load_index(owner, name).symbols[0]["id"]
    db_path = store._db_path(owner, safe)
    fired = []
    monkeypatch.setattr(
        sqlite_store, "_after_write_back_commit",
        _once(fired, lambda: _foreign_symbol_write(db_path, sid)),
    )
    store.set_coverage(owner, name, {"complete": True})
    monkeypatch.undo()
    assert fired
    assert _summary(store, owner, name, sid) == "foreign"


def test_a_commit_before_the_lock_with_an_unmoved_stamp_is_not_hidden(tmp_path, monkeypatch):
    """A locked writer commits after our write and before we take the lock,
    inside one clock tick, so the stamp alone cannot see it."""
    _repo, _sd, store, owner, name, safe, _r = _indexed(tmp_path)
    sid = store.load_index(owner, name).symbols[0]["id"]
    db_path = store._db_path(owner, safe)
    fired = []
    _before_the_rekey_lock(monkeypatch, fired, lambda: _foreign_symbol_write(db_path, sid, keep_stamp=True))
    store.set_coverage(owner, name, {"complete": True})
    monkeypatch.undo()
    assert fired
    assert _summary(store, owner, name, sid) == "foreign"


def test_a_commit_before_this_write_is_not_hidden(tmp_path, monkeypatch):
    """Another process commits after this write opened its connection and
    before it wrote, inside one clock tick."""
    _repo, _sd, store, owner, name, safe, _r = _indexed(tmp_path)
    sid = store.load_index(owner, name).symbols[0]["id"]
    db_path = store._db_path(owner, safe)
    fired = []
    real_connect = SQLiteIndexStore._connect

    def connect_then_foreign_commit(self, path, *a, **k):
        conn = real_connect(self, path, *a, **k)
        _once(fired, lambda: _foreign_symbol_write(db_path, sid, keep_stamp=True))()
        return conn

    monkeypatch.setattr(SQLiteIndexStore, "_connect", connect_then_foreign_commit)
    store.set_coverage(owner, name, {"complete": True})
    monkeypatch.undo()
    assert fired
    assert _summary(store, owner, name, sid) == "foreign"


def test_an_entry_already_behind_the_db_is_not_moved_forward(tmp_path):
    """Another process wrote before this coverage write, inside one clock
    tick, so the entry's stamp still matches."""
    _repo, _sd, store, owner, name, safe, _r = _indexed(tmp_path)
    sid = store.load_index(owner, name).symbols[0]["id"]
    db_path = store._db_path(owner, safe)
    _foreign_symbol_write(db_path, sid, keep_stamp=True)
    store.set_coverage(owner, name, {"complete": True})
    assert _summary(store, owner, name, sid) == "foreign"


def test_another_lock_free_write_before_the_lock_is_not_hidden(tmp_path, monkeypatch):
    """A lock-free meta writer in another process (`stamp_parser_generation`'s
    key) commits after our write and before the re-key, inside one clock tick.
    It bumps no content generation; its own row differs from the entry's."""
    _repo, _sd, store, owner, name, safe, _r = _indexed(tmp_path)
    store.load_index(owner, name)
    db_path = store._db_path(owner, safe)
    fired = []
    _before_the_rekey_lock(monkeypatch, fired, lambda: _foreign_commit(
        db_path,
        [("INSERT OR REPLACE INTO meta (key, value) VALUES ('parser_generation', '1')", ())],
        keep_stamp=True,
    ))
    store.set_coverage(owner, name, {"complete": True})
    monkeypatch.undo()
    assert fired
    assert store.load_index(owner, name).parser_generation == 1


def test_another_write_of_the_same_key_before_the_lock_is_not_hidden(tmp_path, monkeypatch):
    """Another process's `set_coverage` lands after ours and before the re-key,
    inside one clock tick. The cached index holds our value; the .db holds
    theirs."""
    _repo, _sd, store, owner, name, safe, _r = _indexed(tmp_path)
    store.load_index(owner, name)
    db_path = store._db_path(owner, safe)
    fired = []
    _before_the_rekey_lock(monkeypatch, fired, lambda: _foreign_commit(
        db_path,
        [("INSERT OR REPLACE INTO meta (key, value) VALUES ('coverage', ?)",
          ('{"written_by": "another process"}',))],
        keep_stamp=True,
    ))
    store.set_coverage(owner, name, {"written_by": "this process"})
    monkeypatch.undo()
    assert fired
    assert store.load_index(owner, name).coverage == {"written_by": "another process"}


def test_the_stamp_is_taken_before_the_meta_is_read(tmp_path, monkeypatch):
    """A lock-free write that lands after the re-key's meta read must leave the
    entry under a stamp that predates it (the L-342 rule), so the next load
    misses and sees it. Here it lands as the read-only connection closes."""
    from jcodemunch_mcp.storage import generation

    _repo, _sd, store, owner, name, safe, _r = _indexed(tmp_path)
    store.load_index(owner, name)
    db_path = store._db_path(owner, safe)
    fired = []
    real_ro = generation.connect_readonly

    class ClosingWrites:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, *a, **k):
            return self._conn.execute(*a, **k)

        def close(self):
            self._conn.close()
            _once(fired, lambda: _foreign_commit(
                db_path,
                [("INSERT OR REPLACE INTO meta (key, value) VALUES ('parser_generation', '1')", ())],
            ))()

    def ro(path, *a, **k):
        conn = real_ro(path, *a, **k)
        return ClosingWrites(conn) if not fired else conn

    monkeypatch.setattr(generation, "connect_readonly", ro)
    store.set_coverage(owner, name, {"complete": True})
    monkeypatch.undo()
    assert fired
    assert store.load_index(owner, name).parser_generation == 1


@pytest.mark.parametrize(
    "key, value, attr, expected",
    [
        ("parser_generation", "977", "parser_generation", 977),
        ("source_root", "/elsewhere/repo", "source_root", "/elsewhere/repo"),
    ],
)
def test_a_lock_free_write_before_an_incremental_save_is_not_hidden(
    tmp_path, monkeypatch, key, value, attr, expected
):
    """Another process's lock-free meta write commits as the incremental save
    opens its connection. The save patches the old index, which keeps the old
    value, so its pre-warm must not record the meta that holds the new one
    (found in review round 3 by a probe). `source_root` is the case a
    present-or-absent comparison cannot see."""
    import inspect

    repo, _sd, store, owner, name, safe, reindex = _indexed(tmp_path)
    store.load_index(owner, name)
    db_path = store._db_path(owner, safe)
    fired = []
    real_connect = SQLiteIndexStore._connect

    def connect(self, path, *a, **k):
        conn = real_connect(self, path, *a, **k)
        if not fired and any(f.function == "_incremental_save_locked" for f in inspect.stack()):
            fired.append(1)
            _foreign_commit(db_path, [
                ("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))])
        return conn

    monkeypatch.setattr(SQLiteIndexStore, "_connect", connect)
    (repo / "m1.py").write_text("def f1():\n    return 11\n", encoding="utf-8")
    reindex(incremental=True)
    monkeypatch.undo()
    assert fired
    store.set_coverage(owner, name, {"complete": True})
    assert getattr(store.load_index(owner, name), attr) == expected


def test_a_held_index_lock_leaves_the_entry_as_before(tmp_path):
    """With `indexwrite` held elsewhere the write still lands, and the entry is
    not re-keyed: the next load reloads, as it did before this change."""
    _repo, _sd, store, owner, name, safe, _r = _indexed(tmp_path)
    store.load_index(owner, name)
    before = _entry(owner, safe).mtime_ns
    with process_locks.held("indexwrite", f"{owner}/{name}", str(store.base_path)) as got:
        assert got
        store.set_coverage(owner, name, {"complete": True, "marker": "locked"})
    assert _entry(owner, safe).mtime_ns == before
    sqlite_store._cache_clear()
    assert store.load_index(owner, name).coverage == {"complete": True, "marker": "locked"}


def test_after_the_rekey_a_move_that_changed_nothing_needs_no_reload(tmp_path, monkeypatch):
    """The re-keyed entry carries the meta table as of its new stamp, so a
    later move that changed nothing this index holds (another process's
    runtime ingest, a checkpoint) is matched by `_revalidate_cached`. Without
    the meta the kept entry would reload on every such move."""
    import os

    repo, _sd, store, owner, name, safe, reindex = _indexed(tmp_path)
    (repo / "m2.py").write_text("def f2():\n    return 22\n", encoding="utf-8")
    reindex(incremental=True)
    warm = _entry(owner, safe).code_index
    db_path = store._db_path(owner, safe)
    st = os.stat(db_path)
    os.utime(db_path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    builds = []
    real_build = SQLiteIndexStore._build_index_from_rows

    def counting(self, *a, **k):
        builds.append(1)
        return real_build(self, *a, **k)

    monkeypatch.setattr(SQLiteIndexStore, "_build_index_from_rows", counting)
    assert store.load_index(owner, name) is warm
    assert builds == []
