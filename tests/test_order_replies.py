"""A wrong call through `order` ends in a call that works.

Two replies an agent acts on:

- An unknown action names the closest real actions, from the names `menu`
  searches, so a typo costs one call instead of a `menu` round trip. A name
  that starts many actions gets no guess.
- A turned-off tool says where it is turned off, whether the package ships it
  that way, and to ask the user. It used to name the project's
  `.jcodemunch.jsonc` even when the global `config.jsonc` held the setting,
  and told the agent how to edit it.

Codex, VS Code and Claude Code refuse a tool name the server did not list, so
on the counter surface every action a reply names is written as
`order(action="...")`.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest


def _real_names():
    from jcodemunch_mcp import server

    return server._catalog_names()


def _with_config(global_disabled, project=None):
    """Context manager: a global `disabled_tools` list and an optional project overlay."""
    import contextlib

    from jcodemunch_mcp import config as config_module

    @contextlib.contextmanager
    def cm():
        orig_global = config_module._GLOBAL_CONFIG.copy()
        orig_project = config_module._PROJECT_CONFIGS.copy()
        config_module._GLOBAL_CONFIG.clear()
        config_module._PROJECT_CONFIGS.clear()
        try:
            config_module._GLOBAL_CONFIG["disabled_tools"] = list(global_disabled)
            config_module._GLOBAL_CONFIG["meta_fields"] = None
            if project is not None:
                config_module._PROJECT_CONFIGS["/fake/project"] = dict(project)
            yield
        finally:
            config_module._GLOBAL_CONFIG.clear()
            config_module._GLOBAL_CONFIG.update(orig_global)
            config_module._PROJECT_CONFIGS.clear()
            config_module._PROJECT_CONFIGS.update(orig_project)

    return cm()


def _error_of(result) -> str:
    assert result.isError is True
    return json.loads(result.content[0].text)["error"]


def _order(action, args=None):
    from jcodemunch_mcp.server import _handle_order

    return _error_of(asyncio.run(_handle_order({"action": action, "args": args or {}})))


def _call(tool, args):
    from jcodemunch_mcp.server import call_tool

    return _error_of(asyncio.run(call_tool(tool, args)))


# --- unknown actions -----------------------------------------------------------


def test_a_typo_names_the_action_as_an_order_call():
    from jcodemunch_mcp import counter

    err = counter.order_gate("serch_symbols", {"search_symbols", "search_text"}, False)
    assert err.startswith("Unknown action 'serch_symbols'")
    assert 'order(action="search_symbols")' in err


def test_a_spelling_with_other_separators_finds_its_action():
    from jcodemunch_mcp import counter

    err = counter.order_gate("Get-File-Outline", {"get_file_outline", "search_text"}, False)
    assert 'order(action="get_file_outline")' in err


def test_a_far_off_name_gets_no_guess():
    from jcodemunch_mcp import counter

    err = counter.order_gate("brew_coffee", {"search_symbols", "search_text"}, False)
    assert "Did you mean" not in err and "menu" in err


def test_on_the_real_catalog_a_prefix_of_many_actions_points_at_menu():
    """A two-name fixture cannot show a prefix shared by five actions."""
    from jcodemunch_mcp import counter

    names = _real_names()
    for action, folded in (("get_symbol_", "get_symbol"), ("Search", "search")):
        assert counter.nearest_actions(action, names) == [], action
        err = counter.order_gate(action, names, False)
        assert "Did you mean" not in err
        assert f"query '{folded}'" in err


def test_on_the_real_catalog_a_short_prefix_names_all_its_actions():
    from jcodemunch_mcp import counter

    names = _real_names()
    assert counter.nearest_actions("serch_symbols", names) == ["search_symbols"]
    assert set(counter.nearest_actions("get_repo", names)) == {
        "get_repo_health",
        "get_repo_map",
        "get_repo_outline",
    }


def test_close_actions_come_closest_first():
    from jcodemunch_mcp import counter

    near = counter.nearest_actions(
        "get_symbol_sorce", {"get_symbol_diff", "get_symbol_source", "search_text"}
    )
    assert near[0] == "get_symbol_source"


def test_a_state_changing_action_without_opt_in_is_named_as_an_order_call():
    from jcodemunch_mcp import counter

    err = counter.order_gate("index_folder", _real_names(), False)
    assert err.startswith('order(action="index_folder") changes index/session state')


def test_a_retired_name_points_through_order():
    from jcodemunch_mcp import counter

    err = counter.order_gate("get_symbol", _real_names(), False)
    assert 'order(action="get_symbol_source")' in err


# --- what gets suggested, through the server's own order handler --------------


def test_order_never_suggests_a_tool_the_project_turned_off():
    with _with_config([], {"disabled_tools": ["get_file_tree"]}):
        err = _order("get_file_tre", {"repo": "/fake/project"})
    assert "get_file_tree" not in err


def test_order_suggests_a_tool_that_still_runs_although_it_is_listed():
    """`set_tool_tier` survives `disabled_tools` (issue #299), so it is still offered."""
    with _with_config(["set_tool_tier"]):
        err = _order("set_tool_tir")
    assert 'order(action="set_tool_tier")' in err


# --- turned-off tools -----------------------------------------------------------


def test_a_tool_the_project_turned_off_names_the_project_file():
    with _with_config([], {"disabled_tools": ["get_file_tree"]}):
        err = _call("get_file_tree", {"repo": "/fake/project"})
    assert ".jcodemunch.jsonc" in err
    assert "ask the user" in err.lower()
    assert "Remove" not in err


def test_a_tool_the_global_config_turned_off_names_the_global_file():
    with _with_config(["get_file_tree"]):
        err = _call("get_file_tree", {"repo": "/fake/project"})
    assert "global config.jsonc" in err
    assert ".jcodemunch.jsonc" not in err
    assert "ships turned off" not in err


@pytest.mark.parametrize("extra", [[], ["get_file_tree"]])
def test_a_tool_the_package_ships_turned_off_says_so(extra):
    """The template copies the shipped list into the user's file; adding a tool must not change the answer."""
    from jcodemunch_mcp import config as config_module

    shipped = list(config_module.DEFAULTS["disabled_tools"])
    assert shipped, "the shipped default turns at least one tool off"
    with _with_config(shipped + extra):
        err = _call(shipped[0], {"repo": "/fake/project"})
        assert "ships turned off" in err
        assert "the user listed" not in err
        if extra:
            other = _call(extra[0], {"repo": "/fake/project"})
            assert "ships turned off" not in other


@pytest.mark.parametrize("where", ["project", "global"])
def test_which_file_lists_a_turned_off_tool(where):
    from jcodemunch_mcp import config as config_module

    project = {"disabled_tools": ["a"]} if where == "project" else None
    with _with_config(["a"] if where == "global" else [], project):
        assert config_module.disabled_tool_source("a", repo="/fake/project") == where
        assert config_module.disabled_tool_source("b", repo="/fake/project") is None


# --- the counter surface ---------------------------------------------------------


def _hidden_names_outside_order(text, hidden):
    """Hidden tool names that appear in ``text`` other than inside order(action="...")."""
    from jcodemunch_mcp.word_rule import word_pattern

    stripped = re.sub(r'order\(action="[^"]*"\)', "", text)
    return sorted(n for n in hidden if re.search(word_pattern(n), stripped))


def test_on_the_counter_surface_no_order_reply_names_a_hidden_tool_bare(monkeypatch):
    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp import server

    monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "counter")
    listed = {t.name for t in server._build_tools_list(surface_override="counter")}
    hidden = set(_real_names()) - listed
    assert {"search_symbols", "index_folder"} <= hidden

    shipped = list(config_module.DEFAULTS["disabled_tools"])
    replies = []
    with _with_config(shipped + ["get_file_tree"], {"disabled_tools": ["get_repo_map"]}):
        for action in ("serch_symbols", "Get-File-Outline", "get_repo", "get_symbol",
                       "get_symbol_", "search", "brew_coffee", "index-foldr"):
            replies.append(_order(action))
        # A real state-changing action sent without the opt-in.
        replies.append(_order("index_folder", {"path": "/elsewhere"}))
        replies.append(_call("get_file_tree", {"repo": "/elsewhere"}))
        replies.append(_call(shipped[0], {"repo": "/elsewhere"}))
        replies.append(_call("get_repo_map", {"repo": "/fake/project"}))
    for text in replies:
        assert _hidden_names_outside_order(text, hidden) == [], text
