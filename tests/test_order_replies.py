"""A wrong call through `order` ends in the right next call.

Two replies an agent acts on:

- An unknown action names the nearest real action, from the same names
  `menu` searches, so a typo costs one call instead of a `menu` round trip.
- A tool the user turned off says the user turned it off and to ask them,
  and names the file that did it. It used to name the project's
  `.jcodemunch.jsonc` even when the global `config.jsonc` held the setting,
  and told the agent how to edit it.
"""

from __future__ import annotations

import json

import pytest


def test_an_unknown_action_names_the_nearest_action():
    from jcodemunch_mcp import counter

    names = {"search_symbols", "search_text", "get_file_outline"}
    err = counter.order_gate("serch_symbols", names, False)
    assert err.startswith("Unknown action 'serch_symbols'")
    assert "'search_symbols'" in err
    assert "menu" in err


def test_a_spelling_with_other_separators_finds_its_action():
    from jcodemunch_mcp import counter

    err = counter.order_gate("Get-File-Outline", {"get_file_outline", "search_text"}, False)
    assert "'get_file_outline'" in err


def test_an_unknown_action_with_no_near_name_suggests_nothing():
    from jcodemunch_mcp import counter

    err = counter.order_gate("brew_coffee", {"search_symbols", "search_text"}, False)
    assert err.startswith("Unknown action 'brew_coffee'")
    assert "Did you mean" not in err


def _disabled_reply(global_disabled, project_disabled, tool="get_file_tree"):
    import asyncio

    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp.server import call_tool

    orig_global = config_module._GLOBAL_CONFIG.copy()
    orig_project = config_module._PROJECT_CONFIGS.copy()
    config_module._GLOBAL_CONFIG.clear()
    config_module._PROJECT_CONFIGS.clear()
    try:
        config_module._GLOBAL_CONFIG["disabled_tools"] = list(global_disabled)
        config_module._GLOBAL_CONFIG["meta_fields"] = None
        project_root = "/fake/project"
        if project_disabled is not None:
            config_module._PROJECT_CONFIGS[project_root] = {"disabled_tools": list(project_disabled)}
        result = asyncio.run(
            call_tool(tool, {"repo": project_root})
        )
        assert result.isError is True
        return json.loads(result.content[0].text)["error"]
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_global)
        config_module._PROJECT_CONFIGS.clear()
        config_module._PROJECT_CONFIGS.update(orig_project)


def test_a_tool_the_project_turned_off_says_ask_the_user():
    err = _disabled_reply([], ["get_file_tree"])
    assert "get_file_tree" in err
    assert ".jcodemunch.jsonc" in err
    assert "ask" in err.lower()
    assert "Remove" not in err


def test_a_tool_the_global_config_turned_off_names_the_global_file():
    err = _disabled_reply(["get_file_tree"], None)
    assert "config.jsonc" in err
    assert ".jcodemunch.jsonc" not in err
    assert "ask" in err.lower()


@pytest.mark.parametrize("where", ["project", "global"])
def test_which_file_turned_a_tool_off(where):
    from jcodemunch_mcp import config as config_module

    orig_global = config_module._GLOBAL_CONFIG.copy()
    orig_project = config_module._PROJECT_CONFIGS.copy()
    try:
        config_module._GLOBAL_CONFIG.clear()
        config_module._PROJECT_CONFIGS.clear()
        config_module._GLOBAL_CONFIG["disabled_tools"] = ["a"] if where == "global" else []
        if where == "project":
            config_module._PROJECT_CONFIGS["/p"] = {"disabled_tools": ["a"]}
        assert config_module.disabled_tool_source("a", repo="/p") == where
        assert config_module.disabled_tool_source("b", repo="/p") is None
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_global)
        config_module._PROJECT_CONFIGS.clear()
        config_module._PROJECT_CONFIGS.update(orig_project)


def test_close_actions_come_closest_first():
    from jcodemunch_mcp import counter

    near = counter.nearest_actions("get_symbol_sorce", {"get_symbol_diff", "get_symbol_source", "search_text"})
    assert near[0] == "get_symbol_source"
    err = counter.order_gate("get_symbol_dif", {"get_symbol_diff", "get_symbol_source"}, False)
    assert "'get_symbol_diff'" in err


# The real catalog, not a fixture: a two-name fixture cannot show a prefix
# shared by five actions, which is where a single guess goes wrong.
def _real_names():
    from jcodemunch_mcp import server

    return server._catalog_names()


def test_on_the_real_catalog_a_prefix_of_many_actions_gets_no_guess():
    from jcodemunch_mcp import counter

    names = _real_names()
    for action in ("get_symbol", "search"):
        assert counter.nearest_actions(action, names) == [], action
    err = counter.order_gate("get_symbol_", names, False)
    assert "Did you mean" not in err


def test_on_the_real_catalog_a_typo_and_a_short_prefix_find_their_actions():
    from jcodemunch_mcp import counter

    names = _real_names()
    assert counter.nearest_actions("serch_symbols", names) == ["search_symbols"]
    assert set(counter.nearest_actions("get_repo", names)) == {
        "get_repo_health",
        "get_repo_map",
        "get_repo_outline",
    }


def test_a_prefix_of_many_actions_points_at_menu_with_that_query():
    from jcodemunch_mcp import counter

    names = {f"get_widget_{x}" for x in ("a", "b", "c", "d")}
    err = counter.order_gate("get_widget", names, False)
    assert "menu" in err and "'get_widget'" in err
    assert "Did you mean" not in err


def test_a_suggestion_never_names_a_tool_that_would_refuse():
    from jcodemunch_mcp import counter

    err = counter.order_gate(
        "test_summariser", {"test_summarizer", "search_text"}, False, suggest_from={"search_text"}
    )
    assert "test_summarizer" not in err


def test_a_tool_the_package_turned_off_does_not_blame_the_user():
    from jcodemunch_mcp import config as config_module

    shipped = list(config_module.DEFAULTS["disabled_tools"])
    assert shipped, "the shipped default turns at least one tool off"
    err = _disabled_reply(shipped, None, tool=shipped[0])
    assert "by default" in err
    assert "the user listed" not in err
    assert "config.jsonc" not in err
