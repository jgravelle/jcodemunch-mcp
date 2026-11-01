"""The alwaysLoad measurement's two benchmark switches, off and on.

Arm A strips the per-tool alwaysLoad flag; arm F shortens the full surface's
instructions. Every arm runs one build, so the arms must differ only by these
env vars, and with neither set the build must publish exactly what it would
without them.
"""
from __future__ import annotations

import pytest

from jcodemunch_mcp import server

_KEY = server._ALWAYS_LOAD_META_KEY


def _flagged(tools):
    return sorted(t.name for t in tools if (t.meta or {}).get(_KEY))


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.delenv("JCODEMUNCH_BENCH_NO_ALWAYS_LOAD", raising=False)
    monkeypatch.delenv("JCODEMUNCH_BENCH_SHORT_INSTRUCTIONS", raising=False)


@pytest.mark.parametrize("surface", ["full", "counter"])
def test_unset_switch_flags_the_instruction_tools(surface):
    flagged = _flagged(server._build_tools_list(surface_override=surface))
    assert flagged
    assert set(flagged) <= set(server._instruction_tool_names(surface))


@pytest.mark.parametrize("surface", ["full", "counter"])
def test_arm_a_switch_strips_every_flag(surface, monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_BENCH_NO_ALWAYS_LOAD", "1")
    tools = server._build_tools_list(surface_override=surface)
    assert tools
    assert _flagged(tools) == []


@pytest.mark.parametrize("value", ["0", "true", "yes", ""])
def test_only_exactly_one_turns_a_switch_on(value, monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_BENCH_NO_ALWAYS_LOAD", value)
    monkeypatch.setenv("JCODEMUNCH_BENCH_SHORT_INSTRUCTIONS", value)
    assert _flagged(server._build_tools_list(surface_override="full"))
    assert "ToolSearch" in server._mcp_instructions("full")


def test_arm_f_switch_keeps_the_opening_and_bullets_only(monkeypatch):
    long_text = server._mcp_instructions("full")
    monkeypatch.setenv("JCODEMUNCH_BENCH_SHORT_INSTRUCTIONS", "1")
    short = server._mcp_instructions("full")
    assert len(short) < len(long_text)
    assert "ToolSearch" not in short
    assert "On a small repository" not in short
    for name in server._instruction_tool_names("full"):
        assert f"- {name}: " in short
    assert short.splitlines()[0] == long_text.splitlines()[0]


def test_arm_f_switch_leaves_the_counter_text_alone(monkeypatch):
    before = server._mcp_instructions("counter")
    monkeypatch.setenv("JCODEMUNCH_BENCH_SHORT_INSTRUCTIONS", "1")
    assert server._mcp_instructions("counter") == before
