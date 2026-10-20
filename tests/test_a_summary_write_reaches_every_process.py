"""A summary written by another process reaches this process's search cache (LEDGER L-228, L-229).

L-226 made `update_symbol_summaries` tell `search_symbols` to drop the
repository's cached searches, through a listener held in memory. A write from
ANOTHER process, a second server or a script, never reached it, and the
cache key holds `indexed_at`, which a summary write leaves alone, so this
process kept serving the old summaries. The write now also advances a
`summaries_generation` meta row, and the cache key carries it. It is read
from the .db at load, so it moves for every process. A cache entry restored
from the session journal keeps its full key, so after a refresh made while
the server was down, the restored entry no longer matches (L-229).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols

SRC = Path(__file__).resolve().parents[1] / "src"
SOURCE = (
    'def alpha_widget(x):\n    """Original docstring summary."""\n    return x + 1\n'
)

CHILD = """
import sys
from jcodemunch_mcp.storage import IndexStore
owner, name, sid, chash = sys.argv[1:5]
print(IndexStore().update_symbol_summaries(owner, name, {sid: ("Written by another process.", chash)}))
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    root = tmp_path / "src_repo"
    root.mkdir()
    (root / "widgets.py").write_text(SOURCE, encoding="utf-8")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert res.get("success"), res
    return res["repo"], store


def _symbol(repo):
    owner, name = repo.split("/", 1)
    return (
        owner,
        name,
        next(
            s
            for s in IndexStore().load_index(owner, name).symbols
            if s["name"] == "alpha_widget"
        ),
    )


def test_a_summary_written_by_another_process_is_served_here(repo):
    repo_id, store = repo
    first = search_symbols(repo_id, "alpha_widget", detail_level="full")
    assert "Original docstring summary" in json.dumps(first)

    owner, name, sym = _symbol(repo_id)
    env = dict(os.environ, CODE_INDEX_PATH=str(store), PYTHONPATH=str(SRC))
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            CHILD,
            owner,
            name,
            sym["id"],
            sym.get("content_hash") or "",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "1", out.stdout

    second = search_symbols(repo_id, "alpha_widget", detail_level="full")
    assert "Written by another process." in json.dumps(second), second


def test_the_generation_advances_only_when_a_row_changes(repo):
    repo_id, _store = repo
    owner, name, sym = _symbol(repo_id)
    store = IndexStore()
    before = store.load_index(owner, name).summaries_generation
    assert (
        store.update_symbol_summaries(
            owner, name, {sym["id"]: ("one", sym.get("content_hash"))}
        )
        == 1
    )
    after_one = store.load_index(owner, name).summaries_generation
    assert after_one != before
    assert (
        store.update_symbol_summaries(owner, name, {"no::such#function": ("x", "")})
        == 0
    )
    assert store.load_index(owner, name).summaries_generation == after_one
    assert (
        store.update_symbol_summaries(
            owner, name, {sym["id"]: ("two", sym.get("content_hash"))}
        )
        == 1
    )
    assert store.load_index(owner, name).summaries_generation != after_one


def test_a_selective_view_reads_the_generation_without_hydrating(repo):
    """A meta field belongs in EXACT_FIELDS (CLAUDE.md, storage/selective.py):
    reading it must not promote the view to a full load."""
    repo_id, _store = repo
    owner, name, sym = _symbol(repo_id)
    from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

    sq = SQLiteIndexStore()
    sq.update_symbol_summaries(
        owner, name, {sym["id"]: ("gen", sym.get("content_hash"))}
    )
    # The write keeps a warm index warm (L-227), and `open_selective` hands a
    # warm index back as it is; the selective path is the COLD one, so start cold.
    from jcodemunch_mcp.storage import sqlite_store

    sqlite_store._cache_clear()
    view = sq.open_selective(owner, name, symbol_ids=[sym["id"]])
    assert view is not None
    assert view.summaries_generation == sq.load_index(owner, name).summaries_generation
    assert view.promoted is False


def test_a_journal_entry_restored_after_an_offline_refresh_is_not_served(
    repo, tmp_path
):
    """L-229: the session journal saves the search cache and a restart
    restores it, checking only `indexed_at`. A refresh made while the server
    was down left `indexed_at` alone, so the restored entry used to match."""
    from collections import OrderedDict

    from jcodemunch_mcp.tools import search_symbols as ss
    from jcodemunch_mcp.tools.session_journal import SessionJournal
    from jcodemunch_mcp.tools.session_state import SessionState

    repo_id, _store = repo
    ss.search_symbols(repo_id, "alpha_widget", detail_level="full")
    with ss._result_cache_lock:
        saved_cache = OrderedDict(ss._result_cache)
    assert saved_cache, "the first search must leave an entry to save"
    state = SessionState(base_path=str(tmp_path / "journal"))
    state.save(SessionJournal(), saved_cache, max_queries=50)
    loaded = state.load(max_age_minutes=60)
    assert loaded and loaded.get("search_cache"), loaded

    # The "offline" refresh: a summary write with this process's cache empty,
    # as after a restart.
    with ss._result_cache_lock:
        ss._result_cache.clear()
    owner, name, sym = _symbol(repo_id)
    assert (
        IndexStore().update_symbol_summaries(
            owner,
            name,
            {
                sym["id"]: (
                    "Refreshed while the server was down.",
                    sym.get("content_hash"),
                )
            },
        )
        == 1
    )

    current = {repo_id: IndexStore().load_index(owner, name).indexed_at}
    with ss._result_cache_lock:
        restored = state.restore_search_cache(ss._result_cache, loaded, current)
    assert restored >= 1, "the entry's indexed_at still matches, so it is restored"
    after = ss.search_symbols(repo_id, "alpha_widget", detail_level="full")
    assert "Refreshed while the server was down." in json.dumps(after), after
