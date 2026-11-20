"""A call-hierarchy question asked with no depth gets the direct rows.

``get_call_hierarchy`` defaulted to three hops in both directions. Measured on
this repository's own index (1.108.473), one default call for a small helper
(``_name_through``) returned 20,494 tokens, 176 of its 194 caller rows at depth
3; for ``resolve_repo`` it returned 499,342 tokens. One hop in both
directions, the direct rows that answer "who calls this function", returned
899 tokens for the first. An agent that
omits ``depth`` pays for every hop it did not ask for, so the default is one
hop, and a deeper walk is one argument away.
"""

from __future__ import annotations

import inspect
import json

import pytest

from jcodemunch_mcp import server
from jcodemunch_mcp.tools.get_call_hierarchy import get_call_hierarchy

NL = chr(10)


@pytest.fixture
def chain(tmp_path, monkeypatch):
    """d calls c calls b calls a, one file each, so each hop is one depth."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text(
        NL.join(["def leaf_a():", "    return 1", ""]), encoding="utf-8"
    )
    (src / "b.py").write_text(
        NL.join(
            ["from a import leaf_a", "", "def mid_b():", "    return leaf_a()", ""]
        ),
        encoding="utf-8",
    )
    (src / "c.py").write_text(
        NL.join(["from b import mid_b", "", "def mid_c():", "    return mid_b()", ""]),
        encoding="utf-8",
    )
    (src / "d.py").write_text(
        NL.join(["from c import mid_c", "", "def top_d():", "    return mid_c()", ""]),
        encoding="utf-8",
    )
    store = tmp_path / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r.get("success"), r
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    return r["repo"], str(store)


async def _call(arguments):
    out = await server.call_tool("get_call_hierarchy", {**arguments, "format": "json"})
    content = out.content if hasattr(out, "content") else out
    return json.loads(content[0].text)


def _names(rows):
    return {r["name"] for r in rows}


def test_an_explicit_depth_still_walks_every_hop(chain):
    """Non-vacuity: the chain really is three callers deep."""
    repo, store = chain
    r = get_call_hierarchy(
        repo=repo, symbol_id="leaf_a", direction="callers", depth=3, storage_path=store
    )
    assert _names(r["callers"]) == {"mid_b", "mid_c", "top_d"}, r["callers"]


async def test_the_served_default_returns_only_direct_callers(chain):
    repo, _ = chain
    r = await _call({"repo": repo, "symbol_id": "leaf_a", "direction": "callers"})
    assert r["depth"] == 1, r
    assert _names(r["callers"]) == {"mid_b"}, r["callers"]


async def test_the_served_explicit_depth_is_honoured(chain):
    repo, _ = chain
    r = await _call(
        {"repo": repo, "symbol_id": "leaf_a", "direction": "callers", "depth": 3}
    )
    assert _names(r["callers"]) == {"mid_b", "mid_c", "top_d"}, r["callers"]


def test_the_function_default_matches_the_served_default(chain):
    repo, store = chain
    assert inspect.signature(get_call_hierarchy).parameters["depth"].default == 1
    r = get_call_hierarchy(
        repo=repo, symbol_id="leaf_a", direction="callers", storage_path=store
    )
    assert _names(r["callers"]) == {"mid_b"}, r["callers"]


def test_the_published_schema_states_the_default_it_applies():
    tool = next(
        t
        for t in server._build_tools_list(profile_override="full")
        if t.name == "get_call_hierarchy"
    )
    depth = tool.inputSchema["properties"]["depth"]
    assert depth["default"] == 1
    assert "Default 3" not in depth["description"]
    assert "Default 1" in depth["description"]
