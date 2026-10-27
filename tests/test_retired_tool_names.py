"""A retired tool name says which tool replaced it.

`get_symbol` and `get_symbols` were merged into `get_symbol_source` in
March 2026. Agent instructions written before then still name them, and a
call to either returned a bare `Unknown tool: get_symbol`, which gives the
agent nothing to try next. The error now names the replacement and the
argument to pass, and stays an error: the server never dispatches a name
it does not list. Both routes say so: a direct call and ``order``.
"""

from __future__ import annotations

import json

import pytest

from mcp.types import CallToolResult

from jcodemunch_mcp.counter import RETIRED_TOOL_NAMES, order_gate
from jcodemunch_mcp.server import _build_tools_list, call_tool


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "old, new, arg",
    [
        ("get_symbol", "get_symbol_source", "symbol_id"),
        ("get_symbols", "get_symbol_source", "symbol_ids"),
    ],
)
async def test_retired_name_error_names_the_replacement(old, new, arg):
    res = await call_tool(old, {"repo": "owner/repo", arg: "x"})
    assert isinstance(res, CallToolResult)
    assert res.isError is True
    body = json.loads(res.content[0].text)
    assert body["error"].startswith(f"Unknown tool: {old}")
    assert new in body["error"]
    assert arg in body["error"]
    assert body["replaced_by"] == new


@pytest.mark.asyncio
async def test_an_unrelated_unknown_name_gets_no_replacement():
    res = await call_tool("no_such_tool_xyz", {})
    body = json.loads(res.content[0].text)
    assert body["error"] == "Unknown tool: no_such_tool_xyz"
    assert "replaced_by" not in body


def test_every_replacement_is_a_published_tool_and_no_retired_name_is():
    published = {t.name for t in _build_tools_list(surface_override="full")}
    for old, (new, _arg) in RETIRED_TOOL_NAMES.items():
        assert new in published, f"{old} points at {new}, which is not published"
        assert old not in published, f"{old} is published again; drop it from the table"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "old, new, arg",
    [
        ("get_symbol", "get_symbol_source", "symbol_id"),
        ("get_symbols", "get_symbol_source", "symbol_ids"),
    ],
)
async def test_order_names_the_replacement_too(old, new, arg):
    res = await call_tool("order", {"action": old, "args": {"repo": "owner/repo"}})
    assert isinstance(res, CallToolResult)
    assert res.isError is True
    err = json.loads(res.content[0].text)["error"]
    assert err.startswith(f"Unknown action '{old}'")
    assert new in err and arg in err


def test_order_gate_keeps_the_menu_hint_for_an_unrelated_name():
    err = order_gate("no_such_action_xyz", ["search_symbols"], False)
    assert "Call 'menu'" in err
    assert "replaced by" not in err
