"""v1.108.74 — error responses carry isError (PRD WI-2.2 / F-P01).

Tool failures were returned in-band: a `{"error": ...}` JSON body inside a plain
content list, which the SDK wraps as CallToolResult(isError=False). A non-Claude
MCP client that branches on `isError` therefore saw a failure as a success.

call_tool now returns a CallToolResult(isError=True) for failures — carrying the
SAME JSON body in content (so the in-band v1.108.30 contract still holds) — and
keeps success results as a plain content list (SDK wraps isError=False). The wire
change is purely additive: only failures gain the isError signal.
"""

from __future__ import annotations

import json

import pytest

from mcp.types import CallToolResult, TextContent
from jcodemunch_mcp.server import call_tool


@pytest.mark.asyncio
async def test_unknown_tool_sets_iserror_and_preserves_body():
    res = await call_tool("no_such_tool_xyz", {})
    assert isinstance(res, CallToolResult)
    assert res.is_error is True
    body = json.loads(res.content[0].text)  # body preserved (v1.108.30 in-band contract)
    assert "error" in body
    assert "Unknown tool" in body["error"]


@pytest.mark.asyncio
async def test_input_validation_error_sets_iserror():
    res = await call_tool(
        "search_symbols", {"repo": "owner/repo", "query": "x", "max_results": "NaN"}
    )
    assert isinstance(res, CallToolResult)
    assert res.is_error is True
    assert "Input validation error" in json.loads(res.content[0].text)["error"]


@pytest.mark.asyncio
async def test_disabled_tool_path_is_error(monkeypatch):
    """A project-disabled tool is an error and now carries isError."""
    from jcodemunch_mcp import config as cfg
    monkeypatch.setitem(cfg._GLOBAL_CONFIG, "disabled_tools", ["get_repo_outline"])
    res = await call_tool("get_repo_outline", {"repo": "owner/repo"})
    assert isinstance(res, CallToolResult)
    assert res.is_error is True
    assert "disabled" in json.loads(res.content[0].text)["error"].lower()


@pytest.mark.asyncio
async def test_success_stays_plain_list_not_iserror():
    """Success results stay a plain content list (SDK wraps them isError=False),
    so existing consumers that read content[0].text are unaffected."""
    # Force JSON so the body is deterministic; the success path is otherwise
    # free to MUNCH-encode (compact), which is not json.loads-able. The contract
    # under test is the RETURN SHAPE: success stays a plain content list, never a
    # CallToolResult, so the SDK wraps it isError=False.
    res = await call_tool("list_repos", {"format": "json"})
    assert not isinstance(res, CallToolResult)
    assert isinstance(res, list) and res
    assert isinstance(res[0], TextContent)
    # A successful list_repos body is a JSON object, not an error envelope.
    body = json.loads(res[0].text)
    assert "error" not in body
