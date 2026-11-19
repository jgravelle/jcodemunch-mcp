"""The mcp 2.x handler registration, and the request context it has to republish.

1.x registered handlers with decorators (`@server.list_tools()`), which 2.x
dropped, and it published the active request as `Server.request_context`, which
2.x also dropped. Both are load-bearing here: the six handler bodies stay as
they were, and helpers such as `progress.make_progress_notify` reach for the
request context from deep inside a tool call, not through their arguments.

These tests pin the bridge, not the SDK: that the six methods are registered at
all, and that the context a handler receives is visible to those helpers for the
duration of the call and gone afterwards.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jcodemunch_mcp import request_context, server as server_module


def test_the_six_handlers_are_registered():
    registered = set(server_module.server._request_handlers)
    assert {
        "tools/list",
        "tools/call",
        "resources/list",
        "resources/read",
        "prompts/list",
        "prompts/get",
    } <= registered


@pytest.mark.asyncio
async def test_a_tool_call_sees_the_request_context(monkeypatch):
    """The helper inside the tool reads the same context the handler was handed."""
    seen = {}

    async def fake_call_tool(name, arguments):
        seen["name"] = name
        seen["ctx"] = request_context.current()
        seen["session"] = server_module._get_mcp_session()
        return []

    monkeypatch.setattr(server_module, "call_tool", fake_call_tool)
    ctx = SimpleNamespace(session="the-session")
    params = SimpleNamespace(name="menu", arguments={})

    await server_module._mcp_call_tool(ctx, params)

    assert seen["name"] == "menu"
    assert seen["ctx"] is ctx
    assert seen["session"] == "the-session"


@pytest.mark.asyncio
async def test_the_context_is_gone_after_the_call(monkeypatch):
    """No leak: a later call outside a request must not inherit the last one."""

    async def fake_call_tool(name, arguments):
        return []

    monkeypatch.setattr(server_module, "call_tool", fake_call_tool)
    ctx = SimpleNamespace(session="the-session")

    await server_module._mcp_call_tool(ctx, SimpleNamespace(name="menu", arguments={}))

    assert request_context.current() is None


@pytest.mark.asyncio
async def test_the_context_is_gone_when_the_handler_raises(monkeypatch):
    """The reset is in a finally, so a failing tool cannot poison the next call."""

    async def boom(name, arguments):
        raise RuntimeError("tool failed")

    monkeypatch.setattr(server_module, "call_tool", boom)

    with pytest.raises(RuntimeError):
        await server_module._mcp_call_tool(
            SimpleNamespace(session="s"), SimpleNamespace(name="menu", arguments={})
        )

    assert request_context.current() is None


def test_resolve_falls_back_to_the_1x_property():
    """Embedders and tests still set `server.request_context`; honour it."""
    fake_server = SimpleNamespace(request_context="from-the-property")
    assert request_context.resolve(fake_server) == "from-the-property"


def test_resolve_returns_none_outside_a_request():
    assert request_context.resolve(SimpleNamespace()) is None


@pytest.mark.asyncio
async def test_resources_and_prompts_are_wrapped_in_their_result_models(monkeypatch):
    """2.x handlers must return the model, not the bare list the bodies build."""
    async def fake_list_resources():
        return []

    async def fake_list_prompts():
        return []

    monkeypatch.setattr(server_module, "list_resources", fake_list_resources)
    monkeypatch.setattr(server_module, "list_prompts", fake_list_prompts)
    ctx = SimpleNamespace(session=None)

    resources = await server_module._mcp_list_resources(ctx, None)
    prompts = await server_module._mcp_list_prompts(ctx, None)

    assert resources.resources == []
    assert prompts.prompts == []
