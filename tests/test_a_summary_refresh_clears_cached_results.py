"""A search after a summary refresh serves the new summaries (LEDGER L-226).

`search_symbols` keeps a private result cache keyed on the index's identity,
including `indexed_at`. `summarize_repo` writes summaries with a one-column
UPDATE (#1161) that leaves `indexed_at` alone, so the cached key stayed valid
and a search repeated after the refresh served the summaries from before it.

The first draft of this fix cleared the dispatcher's SHARED result cache,
which `search_symbols` never writes to; its test seeded that cache with an
entry no producer makes, and passed against the defect. So these tests use a
real index, real `search_symbols` calls through the server, and the real
`summarize_repo` with only the AI summarizer replaced.
"""

from __future__ import annotations

import json

import pytest

from jcodemunch_mcp import server
from jcodemunch_mcp.tools import summarize_repo as summarize_mod
from jcodemunch_mcp.tools.index_folder import index_folder

SOURCE = (
    'def alpha_widget(x):\n    """Original docstring summary."""\n    return x + 1\n'
)


def _text(res) -> str:
    item = res[0] if isinstance(res, list) else res.content[0]
    return item.text


def _refreshed(symbols, use_ai=True, repo=None, source_for=None):
    for sym in symbols:
        sym.summary = f"Refreshed summary for {sym.name}."
    return symbols


@pytest.fixture
def repo(tmp_path, monkeypatch):
    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    root = tmp_path / "src_repo"
    root.mkdir()
    (root / "widgets.py").write_text(SOURCE, encoding="utf-8")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert res.get("success"), res
    return res["repo"]


@pytest.mark.asyncio
async def test_a_search_repeated_after_summarize_repo_serves_the_new_summary(
    repo, monkeypatch
):
    query = {
        "repo": repo,
        "query": "alpha_widget",
        "detail_level": "full",
        "format": "json",
    }
    before = _text(await server.call_tool("search_symbols", query))
    assert "Original docstring summary" in before, before

    monkeypatch.setattr(summarize_mod, "summarize_symbols", _refreshed)
    done = json.loads(
        _text(await server.call_tool("summarize_repo", {"repo": repo, "force": True}))
    )
    assert done.get("success"), done

    after = _text(await server.call_tool("search_symbols", query))
    assert "Refreshed summary for alpha_widget." in after, after


def test_a_direct_store_write_reaches_the_search_cache(repo):
    """One layer down from the dispatcher: a caller of the store's summary
    write in the SAME process leaves no stale search behind. The registry is
    per process; another process's write is LEDGER L-228."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    first = search_symbols(repo, "alpha_widget", detail_level="full")
    assert "Original docstring summary" in json.dumps(first)
    owner, name = repo.split("/", 1)
    store = IndexStore()
    sym = next(
        s for s in store.load_index(owner, name).symbols if s["name"] == "alpha_widget"
    )
    written = store.update_symbol_summaries(
        owner, name, {sym["id"]: ("Written directly.", sym.get("content_hash"))}
    )
    assert written == 1
    second = search_symbols(repo, "alpha_widget", detail_level="full")
    assert "Written directly." in json.dumps(second), second


def test_a_failing_listener_does_not_stop_the_others():
    from jcodemunch_mcp.storage import write_listeners

    seen = []

    def boom(owner, name):
        raise RuntimeError("listener broke")

    def ok(owner, name):
        seen.append((owner, name))

    write_listeners.register(boom)
    write_listeners.register(ok)
    try:
        write_listeners.notify("o", "n")
    finally:
        write_listeners._listeners[:] = [
            fn for fn in write_listeners._listeners if fn not in (boom, ok)
        ]
    assert seen == [("o", "n")]
