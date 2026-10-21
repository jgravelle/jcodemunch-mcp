"""On the counter surface, the hook's advice says how to load `order`.

The hook points the agent at `order { "action": ... }`. A client that defers
MCP tools may not have `order`'s schema loaded: the server marks it to load up
front, but a user can override that, and other clients ignore the mark. So
advice the agent can't act on without a ToolSearch call names that call: the
same `select:` lookup the server's instructions give, for `order` alone.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp import server
from jcodemunch_mcp.cli.hooks import steering

from tests.test_steering_names_only_published_tools import _hook_text, _paths, indexed  # noqa: F401

LOAD = 'ToolSearch "select:mcp__jcodemunch__order"'


@pytest.mark.parametrize("enforce", ["advisory", "strict"])
def test_counter_advice_names_the_load_step_on_every_path(indexed, monkeypatch, enforce):  # noqa: F811
    root, big = indexed
    monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "counter")
    monkeypatch.setenv("JCODEMUNCH_ENFORCE", enforce)
    for tool_name, tool_input in _paths(root, big):
        text = _hook_text(tool_name, tool_input, str(root))
        assert LOAD in text, (tool_name, text)


def test_full_surface_advice_has_no_load_step(indexed, monkeypatch):  # noqa: F811
    root, big = indexed
    monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "full")
    for tool_name, tool_input in _paths(root, big):
        text = _hook_text(tool_name, tool_input, str(root))
        assert "ToolSearch" not in text, (tool_name, text)


def test_the_hook_uses_the_servers_tool_prefix():
    """The hook can't import the server (it runs on every tool call), so it
    keeps its own copy of the prefix; this pins the two together."""
    assert steering._ORDER_TOOL == server._MCP_TOOL_PREFIX + "order"
