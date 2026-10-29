"""The first 512 characters of the `instructions` string serve every client.

Codex advises that a server's instructions be self-contained in their first
512 characters, because a client may show or keep only that much. The
ToolSearch line is for Claude Code alone, and it used to sit in that window
(about characters 311-512 of the `full` text), pushing the tool bullets out.
It now follows the bullets, so the window carries what the server is for and
the tools to start with, on both surfaces.
"""

import pytest

from jcodemunch_mcp import server as srv

WINDOW = 512


@pytest.mark.parametrize("surface", ["full", "counter"])
@pytest.mark.parametrize("indexed_files", [None, 24347])
def test_the_first_512_characters_carry_no_toolsearch_line(surface, indexed_files):
    text = srv._mcp_instructions(surface=surface, indexed_files=indexed_files)
    assert "ToolSearch" not in text[:WINDOW]


@pytest.mark.parametrize("surface", ["full", "counter"])
def test_the_first_512_characters_name_a_core_tool(surface):
    text = srv._mcp_instructions(surface=surface, indexed_files=24347)
    rows = srv._INSTRUCTION_TOOLS_COUNTER if surface == "counter" else srv._INSTRUCTION_TOOLS_FULL
    window = text[:WINDOW]
    if surface == "counter":
        # Three verbs: the opening names all of them.
        assert all(name in window for name, _ in rows), window
    else:
        assert any(f"- {name}:" in window for name, _ in rows), window


@pytest.mark.parametrize("surface", ["full", "counter"])
def test_the_toolsearch_line_is_still_sent(surface):
    text = srv._mcp_instructions(surface=surface)
    assert "ToolSearch" in text
    last_bullet = max(text.rfind(f"- {name}:") for name, _ in (
        srv._INSTRUCTION_TOOLS_COUNTER if surface == "counter" else srv._INSTRUCTION_TOOLS_FULL))
    assert text.index("ToolSearch") > last_bullet
