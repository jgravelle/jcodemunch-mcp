"""A search that finds something on a capped index says, in the answer, that the index is partial.

`max_folder_files` (default 2,000) drops files past the cap at index time. The
search tools reported that as `_meta.index_truncated`, and `meta_fields: []`,
the shipped default, strips `_meta`. A search that finds nothing already got an
absence refusal that survives the strip; a search that finds something returned
hits from the indexed part and said nothing about the files it never read. On a
large repo (2,000 of 28,141 files) that is most of the repo.

The line rides in the BODY as `index_warning`, a key every compact encoder
carries, and these tests read it through `server.call_tool` on the default
`meta_fields: []`, in both output formats.
"""

from __future__ import annotations

import json

import pytest

import jcodemunch_mcp.config as config_module


def _content(res):
    from mcp.types import CallToolResult

    return res.content if isinstance(res, CallToolResult) else res


def _meta_fields(monkeypatch, value):
    real = config_module.get

    def _get(key, default=None, **kw):
        if key == "meta_fields":
            return value
        return real(key, default, **kw)

    monkeypatch.setattr(config_module, "get", _get)


def _index(tmp_path, monkeypatch, cap):
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.search_symbols import _result_cache

    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / ".idx"))
    monkeypatch.setenv("JCODEMUNCH_USE_AI_SUMMARIES", "false")
    src = tmp_path / "src"
    src.mkdir()
    for i in range(5):
        (src / f"mod{i}.py").write_text(f"def fn{i}():\n    return {i}\n", encoding="utf-8")
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_folder_files", cap)
    res = index_folder(str(src), use_ai_summaries=False)
    _result_cache.clear()
    return res["repo"]


@pytest.fixture
def capped(tmp_path, monkeypatch):
    """5 files under a cap of 3, so 2 are dropped."""
    return _index(tmp_path, monkeypatch, 3)


@pytest.fixture
def complete(tmp_path, monkeypatch):
    return _index(tmp_path, monkeypatch, 100)


def _body(text: str, fmt: str) -> dict:
    if fmt == "json":
        return json.loads(text)
    from jcodemunch_mcp.encoding.decoder import decode

    return decode(text)


async def _call(tool: str, args: dict, fmt: str) -> dict:
    from jcodemunch_mcp import server

    out = _content(await server.call_tool(tool, {**args, "format": fmt}))
    return _body(out[0].text, fmt)


CALLS = [
    ("search_symbols", {"query": "fn"}, "results"),
    ("search_text", {"query": "return"}, "results"),
    ("get_ranked_context", {"query": "fn0"}, "context_items"),
    ("get_ranked_context", {"query": "fn0", "fusion": True}, "context_items"),
]
IDS = ["search_symbols", "search_text", "get_ranked_context", "get_ranked_context-fusion"]


@pytest.mark.parametrize("fmt", ["json", "compact"])
@pytest.mark.parametrize("tool,args,rows", CALLS, ids=IDS)
@pytest.mark.asyncio
async def test_a_hit_from_a_capped_index_says_the_index_is_partial(capped, monkeypatch, tool, args, rows, fmt):
    _meta_fields(monkeypatch, [])
    body = await _call(tool, {"repo": capped, **args}, fmt)
    assert body.get(rows), f"precondition: {tool} found something"
    assert "_meta" not in body or "index_truncated" not in body["_meta"], "precondition: _meta was stripped"
    warning = body.get("index_warning")
    assert warning, f"{tool} on a capped index returned hits and no word that the index is partial"
    assert "3 of 5 files" in warning, warning
    assert "max_folder_files" in warning, "the line names the setting that fixes it"


@pytest.mark.asyncio
async def test_the_line_survives_a_cached_replay(capped, monkeypatch):
    _meta_fields(monkeypatch, [])
    args = {"repo": capped, "query": "fn"}
    await _call("search_symbols", args, "json")
    again = await _call("search_symbols", args, "json")
    assert "3 of 5 files" in (again.get("index_warning") or "")


@pytest.mark.parametrize("tool,args,rows", CALLS, ids=IDS)
@pytest.mark.asyncio
async def test_a_complete_index_carries_no_line(complete, monkeypatch, tool, args, rows):
    _meta_fields(monkeypatch, [])
    body = await _call(tool, {"repo": complete, **args}, "json")
    assert body.get(rows), "precondition"
    assert "index_warning" not in body


@pytest.mark.asyncio
async def test_the_line_comes_once_when_meta_is_kept(capped, monkeypatch):
    """With `_meta` shown the block stays where it was; the body line is added, not moved."""
    _meta_fields(monkeypatch, None)
    body = await _call("search_symbols", {"repo": capped, "query": "fn"}, "json")
    assert body["_meta"]["index_truncated"]["truncated"] is True
    assert body["index_warning"].count("3 of 5 files") == 1
