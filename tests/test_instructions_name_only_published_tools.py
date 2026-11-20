"""The instructions name a tool only while the session is served it.

The `instructions` string is the one piece of steering that survives tool
deferral. Its last row named `check_references` on every `full`-surface
install, and `tool_profile: "core"` does not publish it, so a core session was
told to look up a name its tool lookup cannot find. Where `get_call_hierarchy`
WAS published, nothing in the string named it: a team that enabled it for
structural questions saw it loaded in every session and never called, with
Grep doing the work. Both directions come from one rule now: the rows follow
the published tool list.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from jcodemunch_mcp import server
from jcodemunch_mcp.server import _MCP_INSTRUCTIONS_MAX_CHARS, _mcp_instructions

_NAME_RE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")
_NOT_TOOL_NAMES = {"file_line", "one_at_a_time"}


def _published(profile: str) -> set:
    return {t.name for t in server._build_tools_list(profile_override=profile)}


def _named(text: str) -> set:
    return {
        m
        for m in _NAME_RE.findall(text.replace("mcp__jcodemunch__", ""))
        if m not in _NOT_TOOL_NAMES
    }


def test_the_profiles_differ_in_the_tools_at_issue():
    """Non-vacuity: core lacks both tools and full serves both."""
    core, full = _published("core"), _published("full")
    assert "check_references" not in core and "get_call_hierarchy" not in core
    assert {"check_references", "get_call_hierarchy"} <= full


@pytest.mark.parametrize("profile", ["core", "standard", "full"])
def test_every_name_in_the_text_is_published(profile):
    published = _published(profile)
    text = _mcp_instructions("full", indexed_files=1234567, published=published)
    unserved = _named(text) - published
    assert not unserved, f"{profile}: names {sorted(unserved)}, not published"
    assert len(text) <= _MCP_INSTRUCTIONS_MAX_CHARS, len(text)


def test_core_names_neither_tool():
    text = _mcp_instructions("full", published=_published("core"))
    assert "check_references" not in text
    assert "get_call_hierarchy" not in text
    assert "find_references: who imports a name." in text


def test_a_published_call_graph_is_named_for_its_question():
    published = (_published("core") | {"get_call_hierarchy", "check_references"}) - {
        "index_repo",
        "index_folder",
        "index_file",
    }
    text = _mcp_instructions("full", published=published)
    assert "get_call_hierarchy: who calls it" in text
    assert "check_references: where it's used" in text


def test_the_lookup_never_loads_an_unpublished_tool():
    published = _published("core") - {"get_ranked_context"}
    query = server._tool_search_query("full", published=published)
    assert "get_ranked_context" not in query
    assert "get_ranked_context" not in _mcp_instructions("full", published=published)


def test_the_served_string_reads_the_published_list(monkeypatch):
    """Without `published`, the text follows what `_build_tools_list` serves."""
    core = [SimpleNamespace(name=n) for n in _published("core")]
    monkeypatch.setattr(server, "_build_tools_list", lambda *a, **k: core)
    text = _mcp_instructions("full")
    assert "check_references" not in text
    assert "get_call_hierarchy" not in text


def test_no_row_tool_published_sends_no_empty_lookup():
    published = _published("full") - {name for name, _ in server._INSTRUCTION_TOOLS_FULL}
    text = _mcp_instructions("full", published=published)
    assert "select:" not in text
    assert "ToolSearch" not in text


def test_a_core_profile_in_config_reaches_the_default_path(monkeypatch):
    """The real producer, no mock: `tool_profile: core` set in config."""
    from jcodemunch_mcp import config as config_module

    real_get = config_module.get

    def fake_get(key, default=None, repo=None):
        if key == "tool_profile":
            return "core"
        return real_get(key, default, repo)

    monkeypatch.setattr(config_module, "get", fake_get)
    assert "check_references" not in {t.name for t in server._build_tools_list()}
    text = _mcp_instructions("full")
    assert "check_references" not in text
    assert "get_call_hierarchy" not in text


def test_the_call_graph_description_names_no_other_tool():
    tool = next(
        t
        for t in server._build_tools_list(profile_override="full")
        if t.name == "get_call_hierarchy"
    )
    assert tool.description.startswith("Who calls this function")
    others = _named(tool.description) - {"get_call_hierarchy"}
    tool_names = _published("full")
    assert not (others & tool_names), sorted(others & tool_names)
