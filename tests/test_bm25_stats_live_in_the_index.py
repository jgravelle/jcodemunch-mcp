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


@pytest.fixture(autouse=True)
def _no_write_back_carries_over():
    """The write-back queue and the dispatcher's counters are process-wide."""
    def reset():
        try:
            stats = _stats()
        except ImportError:
            return
        with stats._pending_lock:
            stats._pending.clear()
        from jcodemunch_mcp import server

        if hasattr(server, "_bm25_outer_calls"):
            server._bm25_outer_calls[0] = 0
            server._bm25_flush_task[0] = None

    reset()
    yield
    reset()


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

    async def call_then_drain():
        out = await server.call_tool("search_symbols", {"repo": repo, "query": "alpha"})
        # The write runs after the response, in the background.
        await server._await_bm25_flush()
        return out

    out = asyncio.run(call_then_drain())
    assert not getattr(out, "isError", False), out
    assert _row(db), "the dispatcher did not store the statistics the call computed"
    assert _stats().pending_count() == 0


def test_the_fingerprint_covers_every_module_the_resolver_reaches():
    """Centrality goes through ``resolve_specifier``; a release that changes
    only a helper it reaches, at any depth, must not reuse stored centrality."""
    stats = _stats()
    covered = set(stats.fingerprint_files())
    for rel in ("parser/imports.py", "retrieval/scoring.py", "tools/search_symbols.py",
                "parser/languages.py", "config.py"):
        assert rel in covered, rel
    # Closed under package-relative imports, starting from the two followed files.
    root = SRC
    for rel in [r for r in covered if r in stats._FINGERPRINT_FOLLOW or r not in stats._FINGERPRINT_ROOTS]:
        missing = sorted(stats._relative_imports(root, rel) - covered)
        assert missing == [], f"{rel} imports {missing}, which the fingerprint omits"
    # Two hops from the resolver: the review's examples.
    assert {"parser/parse_budget.py", "parser/sql_preprocessor.py", "parser/symbols.py"} <= covered
    for rel in covered:
        assert (SRC / rel).is_file(), rel


def test_the_extension_table_is_part_of_the_digest(indexed, monkeypatch):
    """`extra_extensions` changes which file an import resolves to, so a row
    written under one table must miss under another."""
    repo, store, _src = indexed
    index = _fresh(repo, store)
    stats = _stats()
    before = stats.corpus_digest(index)
    real = stats._extension_table
    monkeypatch.setattr(stats, "_extension_table", lambda: {**real(), ".zzq": "python"})
    assert stats.corpus_digest(index) != before
    assert stats.load(_db(store), index) is None


class _RecordingDict(dict):
    def __init__(self):
        super().__init__()
        self.order = []

    def __setitem__(self, key, value):
        self.order.append(key)
        super().__setitem__(key, value)


def test_the_stored_hit_publishes_idf_last(indexed):
    """#490's readiness rule holds on the path that reads the stored row."""
    repo, store, _src = indexed
    index = _fresh(repo, store)
    assert _stats().load(_db(store), index) is not None
    index._bm25_cache = _RecordingDict()
    ss.ensure_bm25_cache(index)
    keys = [k for k in index._bm25_cache.order if k in ss._BM25_CORPUS_KEYS]
    assert set(keys) == set(ss._BM25_CORPUS_KEYS) and keys[-1] == "idf", keys


def test_the_save_time_publish_writes_idf_last():
    """#490's readiness rule holds on the path a full save fills."""
    from types import SimpleNamespace

    index = SimpleNamespace(_bm25_cache=_RecordingDict(), _bm25_lock=None)
    ss.publish_corpus_stats(index, {"a": 1.0}, 1.0, {"a": [0]}, {})
    assert index._bm25_cache.order == ["avgdl", "inverted", "centrality", "idf"]
    ss.publish_corpus_stats(index, {"b": 2.0}, 2.0, {"b": [0]}, {})
    assert index._bm25_cache["idf"] == {"a": 1.0}, "a filled cache was overwritten"


@pytest.mark.parametrize("key, attr", [
    ("coverage", "coverage"),
    ("file_cap_status", "file_cap_status"),
])
def test_a_meta_write_after_the_commit_evicts_instead_of_restamping(indexed, monkeypatch, key, attr):
    """Some meta writers skip `indexwrite`. One committing between the
    write-back's COMMIT and its re-stamp must not be hidden behind the warm
    index: `coverage` gates absence claims (#566) and `file_cap_status` marks
    a partial index (#1341)."""
    repo, store, _src = indexed
    db = _db(store)
    _raw_write(db, [("DELETE FROM bm25_stats", ())])
    index = _fresh(repo, store)
    _ranked(repo, store, "alpha")
    from jcodemunch_mcp.storage import sqlite_store

    real_connect = sqlite_store.sqlite3.connect

    class Conn:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args):
            out = self._inner.execute(sql, *args)
            if sql == "COMMIT":
                foreign = real_connect(str(db), isolation_level=None)
                foreign.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    (key, json.dumps({"complete": False, "truncated": True, "reason": "foreign"})),
                )
                foreign.close()
            return out

        def close(self):
            self._inner.close()

    monkeypatch.setattr(sqlite_store.sqlite3, "connect", lambda *a, **k: Conn(real_connect(*a, **k)))
    assert _stats().flush_pending() == 1
    monkeypatch.setattr(sqlite_store.sqlite3, "connect", real_connect)

    from jcodemunch_mcp.tools._utils import load_repo_index_or_error

    again, error, _ = load_repo_index_or_error(repo, store)
    assert error is None
    assert again is not index, "the warm index was re-keyed over a write it never saw"
    assert (getattr(again, attr) or {}).get("reason") == "foreign"


def test_a_summary_write_carries_a_foreign_file_cap_status(indexed, monkeypatch):
    """The summary write re-keys the warm index too; a `file_cap_status` an
    unlocked writer commits inside its window must reach the cached copy."""
    repo, store, _src = indexed
    db = _db(store)
    _fresh(repo, store)
    from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

    conn = sqlite3.connect(str(db))
    sid, content_hash = conn.execute(
        "SELECT id, content_hash FROM symbols WHERE name = 'beta_cleanup'"
    ).fetchone()
    conn.close()
    store_obj = SQLiteIndexStore(base_path=store)
    real_connect = store_obj._connect

    class Conn:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, item):
            return getattr(self._inner, item)

        def commit(self):
            self._inner.commit()
            foreign = sqlite3.connect(str(db), isolation_level=None)
            foreign.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('file_cap_status', ?)",
                (json.dumps({"truncated": True, "reason": "foreign"}),),
            )
            foreign.close()

    monkeypatch.setattr(store_obj, "_connect", lambda path: Conn(real_connect(path)))
    owner, name = repo.split("/", 1)
    store_obj.update_symbol_summaries(owner, name, {sid: ("a new summary", content_hash)})
    monkeypatch.setattr(store_obj, "_connect", real_connect)

    from jcodemunch_mcp.tools._utils import load_repo_index_or_error

    again, error, _ = load_repo_index_or_error(repo, store)
    assert error is None
    assert (again.file_cap_status or {}).get("reason") == "foreign"


def test_every_meta_key_written_outside_the_lock_is_read_back():
    """A warm index is re-keyed after a locked write only once the meta keys
    that unlocked writers set have been read back. A writer added outside
    `indexwrite` with a new key would be hidden by every re-key until it is
    listed in `_UNLOCKED_META_KEYS`; this finds such writers in the source."""
    import ast
    import re

    from jcodemunch_mcp.storage import sqlite_store

    path = SRC / "storage" / "sqlite_store.py"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    unlocked: dict[str, set[str]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        if fn.name.startswith("_migrate") or fn.name.endswith("_locked") or fn.name == "_write_meta":
            continue  # run on connect, or called under `indexwrite`
        body = ast.get_source_segment(text, fn) or ""
        if "indexwrite" in body:
            continue
        keys: set[str] = set()
        for call in ast.walk(fn):
            if not (isinstance(call, ast.Call) and call.args):
                continue
            sql = call.args[0]
            if not (isinstance(sql, ast.Constant) and isinstance(sql.value, str)):
                continue
            s = sql.value
            if " meta" not in s or not re.search(r"INSERT|UPDATE|DELETE", s):
                continue
            keys |= set(re.findall(r"'(\w+)'", s))
            if len(call.args) > 1 and isinstance(call.args[1], ast.Tuple) and call.args[1].elts:
                first = call.args[1].elts[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    keys.add(first.value)
        if keys:
            unlocked[fn.name] = keys
    assert {"set_coverage", "set_file_cap_status", "stamp_parser_generation"} <= set(unlocked), unlocked
    listed = set(sqlite_store._UNLOCKED_META_KEYS)
    missing = {fn: sorted(keys - listed) for fn, keys in unlocked.items() if keys - listed}
    assert missing == {}, f"meta keys written outside indexwrite but never read back: {missing}"


def test_the_write_back_waits_for_every_call_in_flight(indexed, monkeypatch):
    """A write while another call scans moves the .db under that call's
    before/after check, which then refuses its absence as a rebuild."""
    repo, store, _src = indexed
    monkeypatch.setenv("CODE_INDEX_PATH", store)
    db = _db(store)
    _raw_write(db, [("DELETE FROM bm25_stats", ())])
    _fresh(repo, store)
    from jcodemunch_mcp import server

    async def scenario():
        gate = asyncio.Event()
        real_impl = server._call_tool_impl

        async def impl(name, arguments):
            if name == "list_repos":
                await gate.wait()  # a slow call still in flight
            return await real_impl(name, arguments)

        monkeypatch.setattr(server, "_call_tool_impl", impl)
        slow = asyncio.create_task(server.call_tool("list_repos", {}))
        await asyncio.sleep(0)
        await server.call_tool("search_symbols", {"repo": repo, "query": "alpha"})
        await asyncio.sleep(0.2)
        written_while_busy = _row(db) is not None
        gate.set()
        await slow
        await server._await_bm25_flush()
        return written_while_busy

    written_while_busy = asyncio.run(scenario())
    assert not written_while_busy, "the write-back ran while another call was in flight"
    assert _row(db), "the write-back never ran once the last call finished"
