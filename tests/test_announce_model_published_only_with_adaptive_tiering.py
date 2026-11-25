"""announce_model is published only while adaptive_tiering is on.

With adaptive_tiering off (the default), announce_model returns the current
tier unchanged: it does nothing. A published tool is paid for on every request
of a session that loads the tool list, so a tool that does nothing was a
standing cost. It stays callable, so an instruction that still calls it gets
its answer; the counter surface is unchanged, its six tools byte-pinned.
"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp import server


@pytest.fixture
def cfg():
    orig = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()
    config_module._GLOBAL_CONFIG.update(deepcopy(config_module.DEFAULTS))
    config_module._GLOBAL_CONFIG["disabled_tools"] = []
    try:
        yield config_module._GLOBAL_CONFIG
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig)


def _names(**kw):
    return {t.name for t in server._build_tools_list(**kw)}


def test_the_default_is_off(cfg):
    assert config_module.DEFAULTS.get("adaptive_tiering", False) is False


@pytest.mark.parametrize("profile", ["core", "standard", "full"])
def test_not_published_while_adaptive_tiering_is_off(cfg, profile):
    cfg["tool_profile"] = profile
    names = _names(surface_override="full")
    assert "announce_model" not in names
    # The other always-present tools are unchanged.
    assert {"set_tool_tier", "jcodemunch_guide"} <= names


def test_not_published_under_a_custom_bundle(cfg):
    cfg["tool_profile"] = "core"
    cfg["tool_tier_bundles"] = {"core": ["search_symbols", "get_symbol_source"]}
    assert "announce_model" not in _names(surface_override="full")


@pytest.mark.parametrize("profile", ["core", "standard", "full"])
def test_published_while_adaptive_tiering_is_on(cfg, profile):
    cfg["tool_profile"] = profile
    cfg["adaptive_tiering"] = True
    assert "announce_model" in _names(surface_override="full")


def test_the_counter_surface_is_unchanged(cfg):
    off = [t.name for t in server._build_tools_list(surface_override="counter")]
    cfg["adaptive_tiering"] = True
    on = [t.name for t in server._build_tools_list(surface_override="counter")]
    assert off == on
    assert "announce_model" in off


def test_still_callable_while_unpublished(cfg):
    out = asyncio.run(server.call_tool("announce_model", {"model": "claude-opus-5-5"}))
    parts = out if isinstance(out, list) else out.content
    d = json.loads("".join(getattr(p, "text", "") for p in parts))
    assert d.get("adaptive_tiering") is False, d


def test_the_written_policy_keeps_its_explanation_when_the_tool_is_unlisted(cfg):
    # init writes this policy into a user's CLAUDE.md and drops any line that
    # names an unlisted tool, so the sentence saying the flag is off must not
    # share a line with announce_model.
    from jcodemunch_mcp.cli import policy

    cfg["tool_surface"] = "full"
    off = policy.active_policy()
    assert "It is off unless `adaptive_tiering` is enabled" in off
    assert "announce_model" not in off
    cfg["adaptive_tiering"] = True
    on = policy.active_policy()
    assert "It is off unless `adaptive_tiering` is enabled" in on
    assert "announce_model(" in on
