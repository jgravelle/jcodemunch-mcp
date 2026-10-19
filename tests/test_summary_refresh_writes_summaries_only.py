"""A summary refresh changes summaries and nothing else (#1161, @SlimLemon).

`summarize_repo` rebuilt every symbol through a converter that copied only
some fields, then rewrote every symbol row through `incremental_save`. The
fields it did not copy were stored as NULL: `cyclomatic`, `max_nesting` and
`param_count` (so `get_hotspots` returned `[]` and `get_symbol_complexity`
said "not available"), and the `data` column holding `call_references`, which
the call graph reads. The repository did not change between the two calls.

The guard runs the real indexer and the real store on a fixture derived from
the reporter's repository (four files; `use` gains a loop and a branch so it
carries a measured complexity), and compares every stored column before and
after.
Only the summarizer is replaced, so the test needs no AI provider.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore
from jcodemunch_mcp.tools import summarize_repo as summarize_mod
from jcodemunch_mcp.tools.index_folder import index_folder

FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/helper.py": "def helper():\n    return 1\n",
    "src/pkg/main.py": (
        "from . import helper\n"
        "from .helper import helper as h\n\n\n"
        "def use(flag, items):\n"
        "    total = 0\n"
        "    for item in items:\n"
        "        if flag and item:\n"
        "            total += helper.helper() + h()\n"
        "    return total\n"
    ),
    "tests/test_x.py": "from pkg.main import use\n\n\ndef test_use():\n    assert use(True, [1]) == 2\n",
}


@pytest.fixture
def indexed(tmp_path: Path):
    root = tmp_path / "repo"
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    storage = str(tmp_path / "store")
    res = index_folder(
        str(root), use_ai_summaries=False, storage_path=storage, force_reparse=True
    )
    assert res.get("success"), res
    repo = res["repo"]
    owner, name = repo.split("/", 1)
    db = SQLiteIndexStore(base_path=storage)._db_path(owner, name)
    return repo, storage, db


def _rows(db: Path) -> dict[str, dict]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM symbols")}
    finally:
        conn.close()


def _fake_summarizer(symbols, use_ai=True, repo=None):
    for sym in symbols:
        sym.summary = f"refreshed: {sym.name}"
    return symbols


@pytest.mark.parametrize("force", [True, False])
def test_every_column_but_summary_survives_a_refresh(indexed, monkeypatch, force):
    repo, storage, db = indexed
    before = _rows(db)
    use_id = next(i for i, r in before.items() if r["name"] == "use")
    assert before[use_id]["cyclomatic"], "the fixture must carry a measured complexity"
    assert before[use_id]["data"], "the fixture must carry call_references"

    monkeypatch.setattr(summarize_mod, "summarize_symbols", _fake_summarizer)
    out = summarize_mod.summarize_repo(repo, force=force, storage_path=storage)
    assert out.get("success"), out

    after = _rows(db)
    assert set(after) == set(before), "the refresh added or removed a symbol"
    for sid, row in before.items():
        changed = {k for k in row if row[k] != after[sid][k]}
        assert changed <= {"summary"}, (
            sid,
            {k: (row[k], after[sid][k]) for k in changed},
        )
    if force:
        assert all(
            after[s]["summary"] == f"refreshed: {after[s]['name']}" for s in after
        )


def test_the_complexity_tools_still_answer_after_a_refresh(indexed, monkeypatch):
    """The reporter's entry point: `get_symbol_complexity` must not fall back
    to "not available" after `summarize_repo(force=true)`."""
    from jcodemunch_mcp.tools.get_symbol_complexity import get_symbol_complexity

    repo, storage, db = indexed
    use_id = next(i for i, r in _rows(db).items() if r["name"] == "use")
    first = get_symbol_complexity(repo, use_id, storage_path=storage)
    monkeypatch.setattr(summarize_mod, "summarize_symbols", _fake_summarizer)
    assert summarize_mod.summarize_repo(repo, force=True, storage_path=storage).get(
        "success"
    )
    second = get_symbol_complexity(repo, use_id, storage_path=storage)
    assert second.get("cyclomatic") == first.get("cyclomatic") and first.get(
        "cyclomatic"
    ), (first, second)
    assert "note" not in second or second.get("note") == first.get("note"), second


def test_the_response_counts_are_unchanged(indexed, monkeypatch):
    """`updated` and `skipped` keep their meaning: a forced run counts every
    symbol whose summary differs from the cleared one."""
    repo, storage, db = indexed
    n = len(_rows(db))
    monkeypatch.setattr(summarize_mod, "summarize_symbols", _fake_summarizer)
    out = summarize_mod.summarize_repo(repo, force=True, storage_path=storage)
    assert out["symbol_count"] == n and out["updated"] == n and out["skipped"] == 0, out


def test_a_load_after_the_refresh_serves_the_new_summaries(indexed, monkeypatch):
    """The user's entry point reads through the store's in-process cache, which
    held the old summaries; a read of SQLite alone cannot see that (review).

    The cache is keyed by the file's mtime, and two writes inside one
    filesystem tick share it. The mtime is pinned here, as a coarse-tick
    filesystem would leave it, so the test depends on the write evicting the
    entry and not on the clock moving."""
    import jcodemunch_mcp.storage.sqlite_store as sqlite_store
    from jcodemunch_mcp.storage import IndexStore

    repo, storage, db = indexed
    owner, name = repo.split("/", 1)
    pinned = sqlite_store._db_mtime_ns(db)
    monkeypatch.setattr(sqlite_store, "_db_mtime_ns", lambda _path: pinned)
    store = IndexStore(base_path=storage)
    warm = store.load_index(owner, name)
    assert warm is not None and not any(
        s.get("summary", "").startswith("refreshed:") for s in warm.symbols
    )
    monkeypatch.setattr(summarize_mod, "summarize_symbols", _fake_summarizer)
    assert summarize_mod.summarize_repo(repo, force=True, storage_path=storage).get(
        "success"
    )
    fresh = IndexStore(base_path=storage).load_index(owner, name)
    assert fresh is not None
    assert all(s["summary"] == f"refreshed: {s['name']}" for s in fresh.symbols), [
        s["summary"] for s in fresh.symbols
    ]
    use = next(s for s in fresh.symbols if s["name"] == "use")
    assert use.get("cyclomatic"), use


def test_a_summary_is_not_written_onto_a_body_that_changed(indexed, monkeypatch):
    """The summaries are made from a snapshot; a re-index that rewrites a body
    in the meantime must not receive the old body's summary."""
    repo, storage, db = indexed
    use_id = next(i for i, r in _rows(db).items() if r["name"] == "use")

    def summarize_then_reindex(symbols, use_ai=True, repo=None):
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(
                "UPDATE symbols SET content_hash = 'changed' WHERE id = ?", (use_id,)
            )
            conn.commit()
        finally:
            conn.close()
        return _fake_summarizer(symbols)

    monkeypatch.setattr(summarize_mod, "summarize_symbols", summarize_then_reindex)
    out = summarize_mod.summarize_repo(repo, force=True, storage_path=storage)
    after = _rows(db)
    assert after[use_id]["summary"] != "refreshed: use", after[use_id]
    assert out["not_written"] == 1, out
    assert all(
        r["summary"] == f"refreshed: {r['name']}"
        for i, r in after.items()
        if i != use_id
    )


def test_a_row_with_no_stored_hash_still_gets_its_summary(indexed, monkeypatch):
    """The loader reads a NULL `content_hash` as "", so the guard compares the
    two the same way; `NULL IS ''` is false and would skip the row (review)."""
    repo, storage, db = indexed
    use_id = next(i for i, r in _rows(db).items() if r["name"] == "use")
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("UPDATE symbols SET content_hash = NULL WHERE id = ?", (use_id,))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(summarize_mod, "summarize_symbols", _fake_summarizer)
    out = summarize_mod.summarize_repo(repo, force=True, storage_path=storage)
    assert "not_written" not in out, out
    assert _rows(db)[use_id]["summary"] == "refreshed: use"
