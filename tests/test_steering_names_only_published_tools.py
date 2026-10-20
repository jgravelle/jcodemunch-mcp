"""The search-steering hook names only tools the active surface publishes.

Found by Benchmarking on 1.108.386 (M4 design, 2026-10-20). `init`'s default
surface is `counter`: the client's tool list holds the front door (`order`,
`menu`, `route`) and the always-present controls, and every other action is
reachable only as `order { "action": ... }`. The PreToolUse nudge on a Grep or
Glob still told the agent to "exhaust" search_text, search_symbols,
check_references, find_references and find_importers by bare name, i.e. tools
the agent cannot call. The `CLAUDE.md` policy `init` writes already speaks in
`order` calls (cli/policy.py); the hook was the second copy that never asked.

The assertion is the PROPERTY, not the five names: every catalog tool the text
names as a direct call is one the surface publishes, for every hook and both
the nudge and the strict-mode deny.
"""

from __future__ import annotations

import json
import re

import pytest

from jcodemunch_mcp.cli import policy
from jcodemunch_mcp.cli.hooks import steering
from jcodemunch_mcp.server import _catalog_names

_ACTION_RE = re.compile(r'"action":\s*"([a-z_]+)"')
_WORD_RE = re.compile(r"\b[a-z][a-z0-9_]+\b")


def _emitted(capsys, tool_name: str, deny: bool) -> str:
    steering._emit_search_steering(tool_name, f"this {tool_name}", deny=deny)
    payload = json.loads(capsys.readouterr().out)
    hso = payload.get("hookSpecificOutput", {})
    return hso.get("additionalContext") or hso.get("permissionDecisionReason") or ""


def _direct_calls(text: str) -> set[str]:
    """Catalog tool names the text names outside an `"action": "..."` slot."""
    bare = _ACTION_RE.sub("", text)
    catalog = set(_catalog_names())
    return {w for w in _WORD_RE.findall(bare) if w in catalog}


@pytest.mark.parametrize("tool_name", sorted(steering._SEARCH_ROUTES))
@pytest.mark.parametrize("deny", [False, True])
def test_on_the_counter_surface_every_named_tool_is_callable(capsys, monkeypatch, tool_name, deny):
    monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "counter")
    text = _emitted(capsys, tool_name, deny)
    published = policy._front_door_tool_names()
    assert _direct_calls(text) <= published, (_direct_calls(text) - published, text)
    # And the routes are still there, as the actions `order` dispatches.
    wanted = set(_WORD_RE.findall(steering._SEARCH_ROUTES[tool_name])) & set(_catalog_names())
    assert wanted and wanted <= set(_ACTION_RE.findall(text)), text
    assert "order" in text


@pytest.mark.parametrize("tool_name", sorted(steering._SEARCH_ROUTES))
def test_on_the_full_surface_the_routes_are_named_directly(capsys, monkeypatch, tool_name):
    monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "full")
    text = _emitted(capsys, tool_name, deny=False)
    assert steering._SEARCH_ROUTES[tool_name] in text
    assert not _ACTION_RE.search(text)


def test_the_hook_reads_the_surface_without_building_the_tool_list(monkeypatch, capsys):
    """Hooks run once per tool call; building the server's tool list there
    would put the whole catalogue's import cost on every Grep."""
    import jcodemunch_mcp.server as server

    monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "counter")
    monkeypatch.setattr(server, "_build_tools_list", lambda *a, **k: pytest.fail("built the tool list"))
    _emitted(capsys, "Grep", deny=False)
