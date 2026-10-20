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
