"""The deferred-summary thread writes summaries and nothing else (LEDGER L-227).

After a watched edit, `index_folder` saves the re-parsed symbols at once and a
daemon thread fills in their AI summaries later. That thread saved with a
whole-row `incremental_save(new_symbols=...)`, the shape #1161 removed from
`summarize_repo`: every field of the Symbol objects it was handed was written
back. It passes the parser's own objects today, which carry complexity and
call references, so nothing was lost yet; a summarizer that returned rebuilt
symbols would have stored zeros and empty call lists over real values, which is
what #1161 did to `summarize_repo`. It now writes through
`update_symbol_summaries`, the one-column UPDATE guarded by `content_hash`.
"""

from __future__ import annotations

import dataclasses
import threading
from pathlib import Path

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools import _indexing_pipeline
from jcodemunch_mcp.tools.index_folder import index_folder

SOURCE = (
    "def alpha(x):\n"
    "    if x:\n"
    "        if x > 1:\n"
    "            return beta(x)\n"
    "    return 0\n\n"
    "def beta(y):\n"
    "    return y\n"
)


def _rebuilt(symbols, file_contents, use_ai_summaries=True, repo=None):
    """A summarizer that returns NEW symbols carrying only what it computed."""
    return [
        dataclasses.replace(
            s,
            summary=f"AI {s.name}",
            cyclomatic=0,
            max_nesting=0,
            param_count=0,
            call_references=[],
        )
        for s in symbols
    ]


def test_a_summary_write_keeps_the_index_warm(tmp_path: Path, monkeypatch):
    """Review of L-227: the deferred thread calls `update_symbol_summaries` after
    every watched edit, and that write evicted the warm index, so the next query
    paid a full reload the whole-row save had avoided. The cache is patched now.
    """
    from jcodemunch_mcp.storage import sqlite_store

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(SOURCE, encoding="utf-8")
    store_dir = tmp_path / "store"
    res = index_folder(
        str(repo),
        use_ai_summaries=False,
        storage_path=str(store_dir),
        identity_mode="local",
    )
    owner, name = res["repo"].split("/", 1)
    store = IndexStore(base_path=str(store_dir))
    warm = store.load_index(owner, name)
    alpha = next(s for s in warm.symbols if s["name"] == "alpha")
    gen_before = warm.summaries_generation

    assert (
        store.update_symbol_summaries(
            owner, name, {alpha["id"]: ("Patched in place.", alpha["content_hash"])}
        )
        == 1
    )

    def cold(*a, **k):
        raise AssertionError("the summary write sent the next load to the database")

    monkeypatch.setattr(sqlite_store.SQLiteIndexStore, "_build_index_from_rows", cold)
    after = store.load_index(owner, name)
    got = next(s for s in after.symbols if s["name"] == "alpha")
    assert got["summary"] == "Patched in place."
    assert got["cyclomatic"] == alpha["cyclomatic"]
    assert after.summaries_generation != gen_before
    # The read view a fresh process gets agrees with the patched entry.
    monkeypatch.undo()
    sqlite_store._cache_clear()
    disk = next(
        s
        for s in IndexStore(base_path=str(store_dir)).load_index(owner, name).symbols
        if s["name"] == "alpha"
    )
    assert disk["summary"] == got["summary"]
    assert (
        IndexStore(base_path=str(store_dir))
        .load_index(owner, name)
        .summaries_generation
        == after.summaries_generation
    )


def test_a_summary_write_never_re_keys_a_stale_cache(tmp_path: Path):
    """Review of L-227, round 2: patching the warm entry must not hide a write
    another process made after this process loaded it. Warm load, a child
    process writes beta's summary, this process writes alpha's, and the next
    load must equal a cold read of the database."""
    import os
    import subprocess
    import sys

    from jcodemunch_mcp.storage import sqlite_store

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(SOURCE, encoding="utf-8")
    store_dir = tmp_path / "store"
    res = index_folder(
        str(repo),
        use_ai_summaries=False,
        storage_path=str(store_dir),
        identity_mode="local",
    )
    owner, name = res["repo"].split("/", 1)
    store = IndexStore(base_path=str(store_dir))
    warm = store.load_index(owner, name)
    alpha = next(s for s in warm.symbols if s["name"] == "alpha")
    beta = next(s for s in warm.symbols if s["name"] == "beta")

    child = (
        "import sys\n"
        "from jcodemunch_mcp.storage import IndexStore\n"
        "print(IndexStore(base_path=sys.argv[1]).update_symbol_summaries("
        "sys.argv[2], sys.argv[3], {sys.argv[4]: ('Written by another process.', sys.argv[5])}))\n"
    )
    src = str(Path(__file__).resolve().parents[1] / "src")
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            child,
            str(store_dir),
            owner,
            name,
            beta["id"],
            beta["content_hash"],
        ],
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=src),
        check=True,
    )
    assert out.stdout.strip() == "1", out.stderr

    assert (
        store.update_symbol_summaries(
            owner, name, {alpha["id"]: ("Written here.", alpha["content_hash"])}
        )
        == 1
    )
    served = {s["name"]: s["summary"] for s in store.load_index(owner, name).symbols}
    sqlite_store._cache_clear()
    disk = {
        s["name"]: s["summary"]
        for s in IndexStore(base_path=str(store_dir)).load_index(owner, name).symbols
    }
    assert served == disk
    assert served["beta"] == "Written by another process."
    assert served["alpha"] == "Written here."


def test_the_deferred_save_keeps_complexity_and_calls(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    mod = repo / "mod.py"
    mod.write_text(SOURCE, encoding="utf-8")
    store_dir = tmp_path / "store"
    first = index_folder(
        str(repo),
        use_ai_summaries=False,
        storage_path=str(store_dir),
        identity_mode="local",
    )
    assert first.get("success") is True, first
    owner, name = first["repo"].split("/", 1)

    mod.write_text(SOURCE.replace("return 0", "return -1"), encoding="utf-8")
    monkeypatch.setattr(_indexing_pipeline, "deferred_summarize", _rebuilt)
    res = index_folder(
        str(repo),
        use_ai_summaries=True,
        storage_path=str(store_dir),
        identity_mode="local",
        changed_paths=[("modified", str(mod))],
    )
    assert res.get("summarization_deferred") is True, res
    for t in threading.enumerate():
        if t.name == "deferred-summarizer":
            t.join(timeout=60)

    index = IndexStore(base_path=str(store_dir)).load_index(owner, name)
    alpha = next(s for s in index.symbols if s["name"] == "alpha")
    assert alpha["summary"] == "AI alpha", "the deferred summary must still be written"
    assert alpha["cyclomatic"] >= 3, alpha
    assert alpha["param_count"] == 1, alpha
    assert "beta" in (alpha.get("call_references") or []), alpha
