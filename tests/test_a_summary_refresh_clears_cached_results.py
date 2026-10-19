"""A summary refresh clears the cached tool results that carry summaries (LEDGER L-226).

The dispatcher clears the shared result cache after every tool that rewrites an
index (`index_folder`, `index_repo`, `index_file`, `index_dependency`), so a
cached `search_symbols` or `get_symbol_source` result cannot outlive the index
it was read from. `summarize_repo` rewrites every symbol's summary and was the
one writer missing from that list, so a result cached before a refresh kept
serving the old summaries. Found in the review of #1161.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp import server
from jcodemunch_mcp.storage import token_tracker
from jcodemunch_mcp.tools import summarize_repo as summarize_mod


@pytest.mark.asyncio
async def test_summarize_repo_through_the_dispatcher_empties_the_result_cache(
    monkeypatch, tmp_path
):
    token_tracker.result_cache_invalidate()
    token_tracker.result_cache_put(
        "search_symbols", "local/x", ("q",), {"results": [{"summary": "old"}]}
    )
    assert (
        token_tracker.result_cache_get("search_symbols", "local/x", ("q",)) is not None
    )

    monkeypatch.setattr(
        summarize_mod,
        "summarize_repo",
        lambda **kw: {"success": True, "repo": "local/x", "updated": 1, "skipped": 0},
    )
    await server.call_tool("summarize_repo", {"repo": "local/x"})

    assert token_tracker.result_cache_get("search_symbols", "local/x", ("q",)) is None


def test_every_index_writing_tool_clears_the_cache_in_its_dispatch_branch():
    """Over the dispatcher's source: each of the five index-writing tools
    clears the result cache in its branch. One that stops doing so fails here
    by name. A writer added later is only covered once it joins this list."""
    import inspect
    import re

    src = inspect.getsource(server)
    writers = (
        "index_repo",
        "index_folder",
        "index_file",
        "index_dependency",
        "summarize_repo",
    )
    for tool in writers:
        m = re.search(
            rf'(?:if|elif) name == "{tool}":(.*?)(?=\n        elif name == ")',
            src,
            re.S,
        )
        assert m, f"dispatch branch for {tool} not found"
        assert "_result_cache_invalidate()" in m.group(1), (
            f"{tool} does not clear the result cache"
        )
