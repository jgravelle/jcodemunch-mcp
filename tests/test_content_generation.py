"""A write that changes no symbol does not make other processes reload the index.

BACKLOG row 3, fix 1. The process cache keys a loaded index by the mtime of
the .db and its -wal, and many writers move that stamp without touching a
symbol: the embedding store, every runtime ingest, the SCIP import, the BM25
statistics write-back. Each one cost every other process holding the index a
full reload. Every transaction that writes `symbols` or `files` now also writes a
fresh `content_generation` token into `meta`; a reader whose stamp moved
keeps its copy only when the meta table is exactly what its load read.

"Another process" is simulated by putting the reader's old cache entry back
after a write made here, because a write in this process re-keys its own
cache and would prove nothing.
"""

from __future__ import annotations

import ast
import re
import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import sqlite_store
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

SRC = Path(sqlite_store.__file__)


def _index(src: Path, storage: Path) -> dict:
    from jcodemunch_mcp.tools.index_folder import index_folder

    return index_folder(
        path=str(src),
        use_ai_summaries=False,
        storage_path=str(storage),
        incremental=True,
        context_providers=False,
    )


@pytest.fixture
def loaded(tmp_path: Path):
    src = tmp_path / "repo"
    src.mkdir()
    (src / "a.py").write_text("def a():\n    return 1\n")
    (src / "b.py").write_text("class B:\n    def m(self):\n        return 2\n")
    storage = tmp_path / ".code-index"
    r = _index(src, storage)
    assert r["success"], r
    store = SQLiteIndexStore(base_path=str(storage))
    owner, name = store.list_repos()[0]["repo"].split("/", 1)
    sqlite_store._index_cache.clear()
    index = store.load_index(owner, name)
    assert index is not None
    db_path = store._db_path(owner, name)
    return src, storage, store, owner, name, index, db_path


def _entry(owner, name):
    return sqlite_store._index_cache[(owner, name, "")]


def _runtime_write(db_path: Path) -> None:
    """The shape of a runtime ingest: rows in a table the index never loads."""
    c = sqlite3.connect(str(db_path))
    try:
        c.execute(
            "INSERT OR REPLACE INTO runtime_calls (symbol_id, source, count) "
            "VALUES ('probe', 'test', 1)"
        )
        c.commit()
    finally:
        c.close()


def _count_builds(monkeypatch) -> list:
    calls: list = []
    real = SQLiteIndexStore._build_index_from_rows

    def counting(self, *a, **kw):
        calls.append(1)
        return real(self, *a, **kw)

    monkeypatch.setattr(SQLiteIndexStore, "_build_index_from_rows", counting)
    return calls


def test_a_load_records_a_generation(loaded):
    *_, owner, name, index, db_path = loaded
    meta = _entry(owner, name).meta
    assert meta and meta.get("content_generation")


def test_a_write_to_another_table_keeps_the_loaded_index(loaded, monkeypatch):
    _, _, store, owner, name, index, db_path = loaded
    before = sqlite_store._db_mtime_ns(db_path)
    _runtime_write(db_path)
    after = sqlite_store._db_mtime_ns(db_path)
    assert after != before, "the write must move the stamp, or this proves nothing"
    builds = _count_builds(monkeypatch)
    again = store.load_index(owner, name)
    assert again is index
    assert builds == []
    assert _entry(owner, name).mtime_ns == after
    assert again._loaded_mtime_ns == after


def test_a_symbol_write_elsewhere_reloads(loaded, monkeypatch):
    src, storage, store, owner, name, index, db_path = loaded
    old_entry = _entry(owner, name)
    (src / "a.py").write_text("def a():\n    return 1\n\ndef a2():\n    return 3\n")
    assert _index(src, storage)["success"]
    # The reader in another process still holds its old copy.
    sqlite_store._index_cache[(owner, name, "")] = old_entry
    builds = _count_builds(monkeypatch)
    again = store.load_index(owner, name)
    assert again is not index
    assert builds == [1]
    assert any(s["name"] == "a2" for s in again.symbols)


def test_a_meta_write_reloads(loaded, monkeypatch):
    _, _, store, owner, name, index, db_path = loaded
    c = sqlite3.connect(str(db_path))
    try:
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('coverage', '{\"complete\": false}')")
        c.commit()
    finally:
        c.close()
    builds = _count_builds(monkeypatch)
    assert store.load_index(owner, name) is not index
    assert builds == [1]


def test_an_index_with_no_generation_reloads(loaded, monkeypatch):
    """An index written before this key, or by an older release: unknown reloads."""
    _, _, store, owner, name, index, db_path = loaded
    c = sqlite3.connect(str(db_path))
    try:
        c.execute("DELETE FROM meta WHERE key = 'content_generation'")
        c.commit()
    finally:
        c.close()
    sqlite_store._index_cache.clear()
    first = store.load_index(owner, name)
    assert "content_generation" not in _entry(owner, name).meta
    _runtime_write(db_path)
    builds = _count_builds(monkeypatch)
    assert store.load_index(owner, name) is not first
    assert builds == [1]


def test_a_write_during_the_meta_read_reloads(loaded, monkeypatch):
    """The stamp is sampled around the read; a write inside it is never hidden."""
    from jcodemunch_mcp.storage import generation

    _, _, store, owner, name, index, db_path = loaded
    _runtime_write(db_path)  # moves the stamp, so the next load revalidates
    real = generation.connect_readonly

    def write_then_connect(path):
        _runtime_write(Path(path))
        return real(path)

    monkeypatch.setattr(generation, "connect_readonly", write_then_connect)
    builds = _count_builds(monkeypatch)
    store.load_index(owner, name)
    assert builds == [1]


def test_a_summary_generation_change_elsewhere_reloads(loaded, monkeypatch):
    """A summary writer from 1.108.385 on changes `summaries_generation`; the
    meta compare is what makes such a write reach this process."""
    _, _, store, owner, name, index, db_path = loaded
    c = sqlite3.connect(str(db_path))
    try:
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('summaries_generation', '99')")
        c.commit()
    finally:
        c.close()
    builds = _count_builds(monkeypatch)
    assert store.load_index(owner, name) is not index
    assert builds == [1]


def test_a_symbol_write_that_breaks_the_contract_is_not_seen(loaded, monkeypatch):
    """The limit, pinned: a writer that changes `symbols` and leaves every meta
    row as it was is invisible until the next content write. Only
    `update_symbol_summaries` in 1.108.383 and .384 does that; every other
    writer, of any release, changes some meta row."""
    _, _, store, owner, name, index, db_path = loaded
    c = sqlite3.connect(str(db_path))
    try:
        c.execute("UPDATE symbols SET summary = 'foreign' WHERE name = 'a'")
        c.commit()
    finally:
        c.close()
    builds = _count_builds(monkeypatch)
    assert store.load_index(owner, name) is index
    assert builds == []


def test_a_summary_refresh_that_changes_nothing_moves_no_generation(loaded):
    _, _, store, owner, name, index, db_path = loaded
    token = _entry(owner, name).meta["content_generation"]
    store.update_symbol_summaries(owner, name, {"no::such#symbol": ("x", "")})
    c = sqlite3.connect(str(db_path))
    try:
        stored = c.execute("SELECT value FROM meta WHERE key = 'content_generation'").fetchone()[0]
    finally:
        c.close()
    assert stored == token


def test_a_branch_view_is_never_kept_by_generation(loaded):
    *_, owner, name, index, db_path = loaded
    assert sqlite_store._revalidate_cached(owner, name, "feature", db_path) is None


# ── The writer contract ────────────────────────────────────────────────

_TABLE_WRITE = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+(symbols|files)\b",
    re.IGNORECASE,
)


def _writers(tree: ast.AST) -> dict[str, bool]:
    """Every function that writes `symbols` or `files`, and whether it bumps."""
    out: dict[str, bool] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        writes = any(
            isinstance(n, ast.Constant) and isinstance(n.value, str) and _TABLE_WRITE.search(n.value)
            for n in ast.walk(node)
        )
        if not writes:
            continue
        bumps = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_bump_content_generation"
            for n in ast.walk(node)
        )
        # A schema migration (`_migrate_vN_to_vM`) rewrites `index_version`,
        # which changes the meta table, so a reader reloads without a
        # generation. They run outside `indexwrite`, where a new meta key
        # would have to join `_UNLOCKED_META_KEYS`. Exempt by NAME and by the
        # write, never by the word appearing somewhere in the body.
        migrates = node.name.startswith("_migrate_v") and any(
            _writes_index_version(n) for n in ast.walk(node)
        )
        out[node.name] = bumps or migrates
    return out


def _writes_index_version(node: ast.AST) -> bool:
    """A call whose SQL writes `meta` and whose arguments name `index_version`."""
    if not isinstance(node, ast.Call):
        return False
    consts = [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    writes_meta = any(re.search(r"\b(?:INSERT|REPLACE|UPDATE)\b[^;]*\bmeta\b", c, re.IGNORECASE) for c in consts)
    return writes_meta and any(c == "index_version" or "'index_version'" in c for c in consts)


def test_every_symbol_or_file_writer_bumps_the_generation():
    writers: dict[str, bool] = {}
    for path in sorted(SRC.parent.parent.rglob("*.py")):
        for fn, ok in _writers(ast.parse(path.read_text(encoding="utf-8"))).items():
            writers[f"{path.name}:{fn}"] = ok
    writers = {k.split(":", 1)[1] if k.startswith("sqlite_store.py:") else k: v for k, v in writers.items()}
    # Non-vacuity: the scan sees the writers it exists to watch.
    assert {"_save_index_locked", "_incremental_save_locked", "update_symbol_summaries"} <= set(writers)
    missing = sorted(n for n, bumps in writers.items() if not bumps)
    assert not missing, f"writes symbols/files without _bump_content_generation: {missing}"


def test_the_writer_scan_fails_on_a_writer_that_does_not_bump():
    tree = ast.parse(
        "def w(conn):\n"
        "    conn.execute('UPDATE files SET mtime_ns = ? WHERE path = ?', (1, 'a'))\n"
    )
    assert _writers(tree) == {"w": False}


def test_the_migration_exemption_needs_the_name():
    """A function that mentions index_version is not thereby a migration."""
    tree = ast.parse(
        "def _migrate_from_json_locked(conn):\n"
        "    conn.execute('INSERT INTO symbols (id) VALUES (?)', ('a',))\n"
        "    meta = {'index_version': '1'}\n"
    )
    assert _writers(tree) == {"_migrate_from_json_locked": False}
    tree = ast.parse(
        "def _migrate_v4_to_v5(conn):\n"
        "    conn.execute('UPDATE symbols SET name = name')\n"
        "    conn.execute('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)', ('index_version', '5'))\n"
    )
    assert _writers(tree) == {"_migrate_v4_to_v5": True}
    # A migration that only READS index_version is not exempt.
    tree = ast.parse(
        "def _migrate_v9_to_v10(conn):\n"
        "    v = conn.execute('SELECT value FROM meta WHERE key = ?', ('index_version',))\n"
        "    conn.execute('UPDATE symbols SET name = name')\n"
    )
    assert _writers(tree) == {"_migrate_v9_to_v10": False}
