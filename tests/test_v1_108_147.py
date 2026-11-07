"""v1.108.147 — #370: concurrent cold searches must not repeat whole-index work.

Two thundering-herd defects on Windows-scale indexes (665k symbols, ~0.5 GB
SQLite): (1) load_index's cold path hydrated the same repository once per
concurrent caller (the cache check was locked but the hydration was not), and
(2) the per-index _bm25_cache check-then-build was unsynchronized, so every
cold search independently built the full-corpus BM25/centrality state.

Fixes: a per-repo single-flight lock around load_index's cold hydration, and a
per-CodeIndex ``_bm25_lock`` used double-checked at every _bm25_cache build
site (search_symbols x3, get_ranked_context x4, plan_turn, find_implementations,
get_repo_map) plus register_edit's clear.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from jcodemunch_mcp.parser.symbols import Symbol
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage import sqlite_store as ss
from jcodemunch_mcp.storage.index_store import CodeIndex
from jcodemunch_mcp.tools.search_symbols import search_symbols

_THREADS = 8


def _sym(id_, name):
    return Symbol(
        id=id_, file="src/a.py", name=name, qualified_name=name, kind="function",
        language="python", signature=f"def {name}():", byte_offset=0,
        byte_length=50, summary=f"{name} helper",
    )


def _seed(tmp_path):
    store = IndexStore(base_path=str(tmp_path))
    symbols = [_sym(f"s{i}", f"stampede_fn_{i}") for i in range(20)]
    store.save_index(
        owner="test", name="stampede",
        source_files=["src/a.py"],
        symbols=symbols,
        raw_files={"src/a.py": "".join(f"def stampede_fn_{i}(): pass\n" for i in range(20))},
        languages={"python": 1},
        file_languages={"src/a.py": "python"},
    )
    return store


def _run_threads(fn):
    barrier = threading.Barrier(_THREADS)
    results, errors = [], []

    def worker():
        barrier.wait()
        try:
            results.append(fn())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    assert len(results) == _THREADS
    return results


def test_codeindex_has_bm25_lock():
    idx = CodeIndex(repo="o/n", owner="o", name="n", indexed_at="",
                    source_files=[], languages={}, symbols=[])
    assert isinstance(idx._bm25_lock, type(threading.Lock()))


def test_cold_load_index_is_single_flight(tmp_path):
    store = _seed(tmp_path)
    ss._cache_clear()

    calls = []
    real_build = ss.SQLiteIndexStore._build_index_from_rows

    def counting_build(self, *args, **kwargs):
        calls.append(1)
        time.sleep(0.05)  # widen the race window
        return real_build(self, *args, **kwargs)

    with patch.object(ss.SQLiteIndexStore, "_build_index_from_rows", counting_build):
        results = _run_threads(lambda: store.load_index("test", "stampede"))

    assert all(r is not None for r in results)
    # One hydration; every other caller took the freshly cached object.
    assert len(calls) == 1
    # And they all got the SAME object, not per-caller copies.
    assert len({id(r) for r in results}) == 1


def test_cold_bm25_build_is_single_flight(tmp_path):
    store = _seed(tmp_path)
    ss._cache_clear()
    index = store.load_index("test", "stampede")
    assert index is not None
    index._bm25_cache.clear()
    # A save stores the corpus statistics in the .db (#1343); drop them so the
    # threads below reach the build this test pins.
    import sqlite3

    conn = sqlite3.connect(str(index._db_path))
    conn.execute("DELETE FROM bm25_stats")
    conn.commit()
    conn.close()

    import jcodemunch_mcp.tools.search_symbols as sst

    calls = []
    real_compute = sst._compute_bm25

    def counting_compute(symbols):
        calls.append(1)
        time.sleep(0.05)  # widen the race window
        return real_compute(symbols)

    with patch.object(sst, "_compute_bm25", counting_compute):
        results = _run_threads(
            lambda: search_symbols(query="stampede_fn_3", repo="test/stampede",
                                   storage_path=str(store.base_path))
        )

    assert len(calls) == 1
    for r in results:
        assert "error" not in r
        assert any(e["name"] == "stampede_fn_3" for e in r["results"])


def test_second_load_after_single_flight_hits_cache(tmp_path):
    store = _seed(tmp_path)
    ss._cache_clear()
    first = store.load_index("test", "stampede")
    second = store.load_index("test", "stampede")
    assert first is second
