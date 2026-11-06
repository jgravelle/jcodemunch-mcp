"""#1343 (@tmhtech4): BM25 corpus statistics are stored inside the index .db.

A fresh process used to re-tokenize every symbol before its first search,
because the corpus statistics (idf, avgdl, the inverted lists and file
centrality) lived only in memory. On a 1,040,596-symbol index that was most of
a cold search. The statistics are now written into the index's own .db: by a
full save, in the same transaction as the symbols; and after a search on an
index that has none, once the call returns.

They are reused only when they describe exactly the symbols the process
loaded, in the order it loaded them, under the same tokenizer and resolver
code. The posting lists are positions in ``index.symbols``, so the same
symbols in another order, or a rewrite that keeps the count and the mtime,
must be a miss. A write of symbols (an incremental save, a summary write)
clears them in the same transaction.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import stat
import zlib
from pathlib import Path

import pytest

from jcodemunch_mcp.tools import search_symbols as ss
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols

SRC = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"


def _stats():
    from jcodemunch_mcp.storage import bm25_stats

    return bm25_stats


@pytest.fixture
def indexed(tmp_path):
    """A small real repo indexed into its own store: (repo_id, store, src)."""
    src = tmp_path / "src"
    src.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    (src / "alpha.py").write_text(
        "class AlphaWidget:\n    \"\"\"Renders the alpha widget.\"\"\"\n\n"
        "    def render_alpha(self):\n        return 1\n\n\n"
        "def parse_alpha_config(path):\n    \"\"\"Read the alpha config file.\"\"\"\n    return path\n",
        encoding="utf-8",
    )
    (src / "beta.py").write_text(
        "import alpha\n\n\ndef beta_handler(request):\n    \"\"\"Handle a beta request.\"\"\"\n"
        "    return alpha.parse_alpha_config(request)\n\n\n"
        "def beta_cleanup():\n    return None\n",
        encoding="utf-8",
    )
    (src / "gamma.py").write_text(
        "from alpha import AlphaWidget\n\n\nclass GammaRenderer(AlphaWidget):\n"
        "    def render_gamma(self):\n        return self.render_alpha()\n",
        encoding="utf-8",
    )
    result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert result["success"] is True, result
    return result["repo"], str(store), src


def _db(store: str) -> Path:
    dbs = sorted(Path(store).glob("*.db"))
    assert len(dbs) == 1, dbs
    return dbs[0]


def _fresh(repo: str, store: str):
    """The index as a NEW process would load it: no in-memory cache."""
    from jcodemunch_mcp.storage import sqlite_store
    from jcodemunch_mcp.tools._utils import load_repo_index_or_error

    sqlite_store._cache_clear()
    index, error, _status = load_repo_index_or_error(repo, store)
    assert error is None, error
    return index


def _row(db: Path):
    conn = sqlite3.connect(str(db))
    try:
        try:
            got = conn.execute("SELECT value FROM bm25_stats WHERE key = 'corpus'").fetchone()
        except sqlite3.OperationalError:
            return None
        return got[0] if got else None
    finally:
        conn.close()


def _raw_write(db: Path, sql_statements: list[tuple[str, tuple]]):
    """Rewrite the .db the way a writer that knows nothing of the stats would,
    then put its mtime back, so only the content tells the two apart."""
    before = os.stat(db)
    conn = sqlite3.connect(str(db), isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for sql, args in sql_statements:
            conn.execute(sql, args)
        conn.execute("COMMIT")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    for suffix in ("-wal", "-shm"):
        side = Path(str(db) + suffix)
        if side.exists():
            try:
                side.unlink()
            except OSError:
                pass
    os.utime(db, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert os.stat(db).st_mtime_ns == before.st_mtime_ns


def _spy(monkeypatch):
    calls = {"n": 0}
    real = ss._compute_bm25

    def counting(symbols):
        calls["n"] += 1
        return real(symbols)

    monkeypatch.setattr(ss, "_compute_bm25", counting)
    return calls


def _warm(repo, store):
    """Search once in a fresh load and store whatever it persists, so the next
    load has stored statistics to reuse (or wrongly reuse)."""
    _fresh(repo, store)
    _ranked(repo, store, "alpha")
    try:
        _stats().flush_pending()
    except ImportError:
        pass


def _ranked(repo, store, query):
    out = search_symbols(repo=repo, query=query, storage_path=store, debug=True, max_results=20)
    return [(r["id"], r.get("score")) for r in out["results"]]


def test_a_full_index_writes_the_stats_with_the_symbols(indexed):
    repo, store, _src = indexed
    blob = _row(_db(store))
    assert blob, "a full save wrote no corpus statistics"
    index = _fresh(repo, store)
    assert _stats().load(_db(store), index) is not None, "the stats a save wrote do not match a fresh load"


def test_a_hit_returns_the_same_answer_without_recomputing(indexed, monkeypatch):
    repo, store, _src = indexed
    db = _db(store)
    queries = ("alpha", "render", "beta handler", "config", "GammaRenderer")

    _fresh(repo, store)
    calls = _spy(monkeypatch)
    from_stats = [_ranked(repo, store, q) for q in queries]
    assert calls["n"] == 0, "a fresh load with stored statistics recomputed them"

    _raw_write(db, [("DELETE FROM bm25_stats", ())])
    _fresh(repo, store)
    computed = [_ranked(repo, store, q) for q in queries]
    assert calls["n"] == 1
    assert from_stats == computed
    assert any(from_stats), "the queries returned nothing; the comparison is vacuous"


def test_a_rewrite_that_keeps_count_and_mtime_is_a_miss(indexed, monkeypatch):
    repo, store, _src = indexed
    db = _db(store)
    _warm(repo, store)
    conn = sqlite3.connect(str(db))
    sid = conn.execute("SELECT id FROM symbols WHERE name = 'beta_cleanup'").fetchone()[0]
    count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    conn.close()
    _raw_write(db, [("UPDATE symbols SET summary = ? WHERE id = ?", ("zebracrossing teardown", sid))])

    index = _fresh(repo, store)
    assert len(index.symbols) == count
    calls = _spy(monkeypatch)
    hits = _ranked(repo, store, "zebracrossing")
    assert calls["n"] == 1, "stale statistics were reused after a same-count, same-mtime rewrite"
    assert hits and hits[0][0] == sid


def test_the_same_symbols_in_another_order_are_a_miss(indexed, monkeypatch):
    repo, store, _src = indexed
    db = _db(store)
    _warm(repo, store)
    first = _fresh(repo, store)
    order_before = [s["id"] for s in first.symbols]
    _raw_write(db, [
        ("CREATE TABLE symbols_copy AS SELECT * FROM symbols", ()),
        ("DELETE FROM symbols", ()),
        ("INSERT INTO symbols SELECT * FROM symbols_copy ORDER BY rowid DESC", ()),
        ("DROP TABLE symbols_copy", ()),
    ])
    index = _fresh(repo, store)
    order_after = [s["id"] for s in index.symbols]
    assert sorted(order_after) == sorted(order_before)
    assert order_after != order_before, "the reorder did not change the loaded order; the test is vacuous"
    calls = _spy(monkeypatch)
    hits = _ranked(repo, store, "render")
    assert calls["n"] == 1, "statistics for another symbol order were reused"
    assert hits


@pytest.mark.parametrize("kind", ["garbage", "truncated", "nan", "oversized", "wrong_shape"])
def test_a_damaged_row_is_a_miss_and_never_raises(indexed, monkeypatch, kind):
    repo, store, _src = indexed
    db = _db(store)
    good = _row(db)
    assert good
    if kind == "garbage":
        bad = b"not zlib at all"
    elif kind == "truncated":
        bad = good[: len(good) // 2]
    elif kind == "nan":
        payload = json.loads(zlib.decompress(good))
        text = json.dumps(payload).replace(str(payload["avgdl"]), "NaN", 1)
        bad = zlib.compress(text.encode("utf-8"))
    elif kind == "oversized":
        monkeypatch.setattr(_stats(), "_MAX_STATS_BYTES", 64)
        bad = good
    else:
        payload = json.loads(zlib.decompress(good))
        payload["inverted"] = {t: [10**6] for t in payload["inverted"]}
        bad = zlib.compress(json.dumps(payload).encode("utf-8"))
    _raw_write(db, [("UPDATE bm25_stats SET value = ? WHERE key = 'corpus'", (bad,))])
    _fresh(repo, store)
    calls = _spy(monkeypatch)
    assert _ranked(repo, store, "alpha")
    assert calls["n"] == 1


def test_an_incremental_save_clears_the_stats_and_a_search_writes_them_back(indexed, monkeypatch):
    repo, store, src = indexed
    db = _db(store)
    (src / "beta.py").write_text(
        (src / "beta.py").read_text(encoding="utf-8") + "\n\ndef beta_extra():\n    return 2\n",
        encoding="utf-8",
    )
    result = index_folder(str(src), use_ai_summaries=False, storage_path=store)
    assert result["success"] is True
    assert _row(db) is None, "an incremental save left statistics for the old symbols"

    _fresh(repo, store)
    assert _ranked(repo, store, "beta extra")
    assert _stats().pending_count() >= 1
    _stats().flush_pending()
    assert _row(db), "the search did not store the statistics it computed"

    index = _fresh(repo, store)
    assert _stats().load(db, index) is not None
    calls = _spy(monkeypatch)
    _ranked(repo, store, "beta")
    assert calls["n"] == 0


def test_writing_back_does_not_make_the_process_reload_its_own_index(indexed):
    repo, store, _src = indexed
    db = _db(store)
    _raw_write(db, [("DELETE FROM bm25_stats", ())])
    index = _fresh(repo, store)
    _ranked(repo, store, "alpha")
    _stats().flush_pending()
    assert _row(db)
    from jcodemunch_mcp.storage import sqlite_store
    from jcodemunch_mcp.tools._utils import load_repo_index_or_error

    assert index._loaded_mtime_ns == sqlite_store._db_mtime_ns(db), (
        "the write moved the .db and the index still carries the old mtime, so "
        "its own next search reads the write as a rebuild"
    )
    again, error, _ = load_repo_index_or_error(repo, store)
    assert error is None
    assert again is index, "the process reloaded the index it had just written statistics for"


def test_a_summary_write_clears_the_stats(indexed):
    repo, store, _src = indexed
    db = _db(store)
    from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

    conn = sqlite3.connect(str(db))
    sid, content_hash = conn.execute(
        "SELECT id, content_hash FROM symbols WHERE name = 'beta_cleanup'"
    ).fetchone()
    conn.close()
    owner, name = repo.split("/", 1)
    SQLiteIndexStore(base_path=store).update_symbol_summaries(
        owner, name, {sid: ("a new summary", content_hash)}
    )
    assert _row(db) is None


def test_deleting_the_index_leaves_no_statistics_behind(indexed):
    repo, store, _src = indexed
    from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

    _warm(repo, store)
    owner, name = repo.split("/", 1)
    assert SQLiteIndexStore(base_path=store).delete_index(owner, name)
    leftovers = [p.name for p in Path(store).rglob("*") if "bm25" in p.name.lower() or p.suffix == ".db"]
    assert leftovers == []


def test_read_only_storage_does_not_raise(indexed):
    repo, store, _src = indexed
    db = _db(store)
    _raw_write(db, [("DELETE FROM bm25_stats", ())])
    _fresh(repo, store)
    os.chmod(db, stat.S_IREAD)
    try:
        assert _ranked(repo, store, "alpha")
        _stats().flush_pending()
    finally:
        os.chmod(db, stat.S_IREAD | stat.S_IWRITE)
    assert _row(db) is None


def test_the_call_dispatcher_stores_the_stats_after_the_call(indexed, monkeypatch):
    repo, store, _src = indexed
    # The server reads its store from the environment, not from arguments.
    monkeypatch.setenv("CODE_INDEX_PATH", store)
    db = _db(store)
    _raw_write(db, [("DELETE FROM bm25_stats", ())])
    _fresh(repo, store)
    from jcodemunch_mcp import server

    out = asyncio.run(server.call_tool("search_symbols", {"repo": repo, "query": "alpha"}))
    assert not getattr(out, "isError", False), out
    assert _row(db), "the dispatcher did not store the statistics the call computed"
    assert _stats().pending_count() == 0


def test_the_fingerprint_covers_every_module_the_resolver_imports():
    """Centrality goes through ``resolve_specifier``; a release that changes
    only one of its helpers must not reuse stored centrality."""
    import ast

    covered = set(_stats()._FINGERPRINT_FILES)
    tree = ast.parse((SRC / "parser" / "imports.py").read_text(encoding="utf-8"))
    needed = {"parser/imports.py", "retrieval/scoring.py", "tools/search_symbols.py"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level >= 1 and node.module:
            base = "parser/" if node.level == 1 else ""
            needed.add(base + node.module.replace(".", "/") + ".py")
    missing = sorted(n for n in needed if n not in covered)
    assert missing == [], f"modules the stored statistics depend on but the fingerprint omits: {missing}"
    for rel in covered:
        assert (SRC / rel).is_file(), rel
