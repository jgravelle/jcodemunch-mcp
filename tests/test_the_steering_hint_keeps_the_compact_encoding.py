"""The once-per-session steering hint rides inside the compact encoding.

The hint (`_meta.hint`, server `_steer_hint_due`) used to force JSON for the
response that carried it, so the encoder could not drop it. On the pinned
express corpus that one search cost 434 tokens served as JSON against 226 for
the same answer encoded without the hint (`.claude/state/evidence/steer_hint_tokens.txt`). The hint
now has its own key, `_meta.steer_hint`, which is in `UNIVERSAL_META_JSON`, so
every schema encoder carries and decodes it and the response keeps its
encoding. A tool's ordinary `_meta.hint` stays off the wire as before.
"""

from __future__ import annotations

import json

import pytest

import jcodemunch_mcp.config as config_module
from jcodemunch_mcp import server


def _content(res):
    from mcp.types import CallToolResult

    return res.content if isinstance(res, CallToolResult) else res


@pytest.fixture(autouse=True)
def _reset_steer_state():
    saved = dict(server._steer_state)
    server._steer_state.update({"hops": 0, "bundles": 0, "nudged": False, "repos": []})
    yield
    server._steer_state.update(saved)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.search_symbols import _result_cache

    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / ".idx"))
    monkeypatch.setenv("JCODEMUNCH_USE_AI_SUMMARIES", "false")
    real = config_module.get

    def _get(key, default=None, **kw):
        if key == "meta_fields":
            return []  # the shipped default
        return real(key, default, **kw)

    monkeypatch.setattr(config_module, "get", _get)
    src = tmp_path / "src"
    src.mkdir()
    for i in range(12):
        (src / f"handler{i}.py").write_text(
            f"def handle_request_{i}(request, response):\n"
            f"    '''Handle request {i} and write the response.'''\n"
            f"    return response\n",
            encoding="utf-8",
        )
    res = index_folder(str(src), use_ai_summaries=False)
    _result_cache.clear()
    return res["repo"]


async def _search(tool: str, repo: str, query: str) -> str:
    out = _content(
        await server.call_tool(tool, {"repo": repo, "query": query, "max_results": 10})
    )
    return out[0].text


def _decode(text: str) -> dict:
    if text.lstrip().startswith("{"):
        return json.loads(text)
    from jcodemunch_mcp.encoding.decoder import decode

    return decode(text)


@pytest.mark.parametrize("tool", ["search_symbols", "search_text"])
@pytest.mark.asyncio
async def test_the_hint_response_is_encoded_and_carries_the_hint(repo, tool):
    plain = await _search(tool, repo, "handle request")
    assert plain.startswith("#MUNCH"), (
        "precondition: this answer is served encoded without the hint"
    )

    server._steer_state.update(
        {"hops": server._STEER_NUDGE_AT, "bundles": 0, "nudged": False}
    )
    hinted = await _search(tool, repo, "handle request")

    assert server._steer_state["nudged"], "precondition: the hint fired on this call"
    assert hinted.startswith("#MUNCH"), (
        "the hint knocked the response out of the compact encoding"
    )
    body = _decode(hinted)
    assert "get_ranked_context" in body.get("_meta", {}).get("steer_hint", ""), (
        "the hint was dropped"
    )
    # Same answer as the call without the hint: nothing but the hint is added.
    rows = "results"
    assert body[rows] == _decode(plain)[rows]


def test_only_the_steering_key_rides_the_encoding():
    """A tool's own `_meta.hint` ("Use get_context_bundle ...") stays off the wire,
    as before; only the steering key is carried (review of this change)."""
    from jcodemunch_mcp.encoding.decoder import decode
    from jcodemunch_mcp.encoding.schemas import search_symbols as ss

    response = {
        "result_count": 1,
        "results": [
            {
                "id": "a.py::f#function",
                "name": "f",
                "kind": "function",
                "file": "a.py",
                "line": 1,
            }
        ],
        "_meta": {"hint": "tool hint", "steer_hint": "steering hint"},
    }
    payload, _ = ss.encode("search_symbols", response)
    meta = decode(payload).get("_meta", {})
    assert meta.get("steer_hint") == "steering hint"
    assert "hint" not in meta
