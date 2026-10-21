"""The core tools load up front in clients that defer MCP tools.

Claude Code defers every MCP tool by default: the model gets tool names and the
server's `instructions`, and a schema arrives only after a ToolSearch call. An
agent that already has Grep loaded rarely makes that call, so a deferred
jcodemunch is effectively absent. A server can mark a tool
`_meta["anthropic/alwaysLoad"]: true` in its tool list, and Claude Code then
loads it up front (the user can still override per server).

The tools marked are exactly the ones the `instructions` string names for the
active surface (`server._instruction_tool_names`), so the two cannot drift: the
text tells the agent to start with a tool, and that tool is loaded.
"""

from __future__ import annotations

import json

import pytest

from jcodemunch_mcp import server

KEY = "anthropic/alwaysLoad"


def _wire(tools) -> list[dict]:
    """The tool rows as the client receives them."""
    from mcp.types import ListToolsResult

    payload = ListToolsResult(tools=tools).model_dump(by_alias=True, exclude_none=True, mode="json")
    return json.loads(json.dumps(payload))["tools"]


def _marked(rows: list[dict]) -> set[str]:
    return {r["name"] for r in rows if (r.get("_meta") or {}).get(KEY) is True}


@pytest.mark.parametrize("surface", ["counter", "full"])
def test_the_tools_the_instructions_name_are_marked_always_load(surface):
    rows = _wire(server._build_tools_list(surface_override=surface))
    named = set(server._instruction_tool_names(surface))
    published = {r["name"] for r in rows}
    assert named <= published, named - published
    assert _marked(rows) == named


def test_counter_marks_the_three_front_door_verbs():
    rows = _wire(server._build_tools_list(surface_override="counter"))
    assert _marked(rows) == {"route", "menu", "order"}


def test_no_other_tool_is_marked():
    rows = _wire(server._build_tools_list(surface_override="full"))
    extra = _marked(rows) - set(server._instruction_tool_names("full"))
    assert extra == set()
    assert len(rows) > len(_marked(rows))  # the rest stay deferred


def test_a_disabled_core_tool_is_not_marked_or_resurrected(monkeypatch):
    """Marking never adds a tool the user's config removed."""
    real_get = server.config_module.get

    def fake_get(key, default=None, *a, **k):
        if key == "disabled_tools":
            return ["get_symbol_source"]
        return real_get(key, default, *a, **k)

    monkeypatch.setattr(server.config_module, "get", fake_get)
    rows = _wire(server._build_tools_list(surface_override="full"))
    names = {r["name"] for r in rows}
    assert "get_symbol_source" not in names
    assert _marked(rows) == set(server._instruction_tool_names("full")) - {"get_symbol_source"}
