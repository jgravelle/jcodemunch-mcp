"""`compact_schemas` is on by default, and an explicit `false` still publishes full schemas.

The owner ruled the default flip on 2026-11-20, from a measurement of agents
working under compact schemas in which the hidden parameter they reached for was
`max_source_lines`, on bounded `get_symbol_source` fetches. So it stays in the
compact schema as name and type. Its description is what does not fit under the core_compact ceiling,
so only the description is stripped, and only under compact.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp import server


@pytest.fixture
def cfg():
    saved = deepcopy(config_module._GLOBAL_CONFIG)
    config_module._GLOBAL_CONFIG.clear()
    config_module._GLOBAL_CONFIG.update(deepcopy(config_module.DEFAULTS))
    config_module._GLOBAL_CONFIG.update({"tool_surface": "full", "tool_profile": "core"})
    try:
        yield config_module._GLOBAL_CONFIG
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(saved)


def _props(tool_name: str) -> dict:
    tool = next(t for t in server._build_tools_list() if t.name == tool_name)
    return tool.inputSchema["properties"]


def test_the_default_is_compact():
    assert config_module.DEFAULTS["compact_schemas"] is True


def test_the_default_strips_the_advanced_params(cfg):
    props = _props("search_symbols")
    assert "debug" not in props and "fusion" not in props


def test_an_explicit_false_keeps_full_schemas(cfg):
    cfg["compact_schemas"] = False
    props = _props("search_symbols")
    assert "debug" in props and "fusion" in props
    assert "description" in _props("get_symbol_source")["max_source_lines"]


def test_max_source_lines_stays_visible_under_compact_as_name_and_type(cfg):
    prop = _props("get_symbol_source").get("max_source_lines")
    assert prop is not None, "compact schemas dropped max_source_lines"
    assert prop == {"type": "integer"}, prop


def test_the_counter_surface_does_not_read_compact_schemas(cfg):
    cfg["tool_surface"] = "counter"
    names = []
    for compact in (False, True):
        cfg["compact_schemas"] = compact
        names.append([(t.name, t.description, t.inputSchema) for t in server._build_tools_list()])
    assert names[0] == names[1]


@pytest.mark.asyncio
async def test_a_hidden_boolean_sent_as_a_string_is_still_coerced(cfg, monkeypatch):
    """`incremental` is hidden under compact; "false" must still arrive as False."""
    from unittest.mock import patch

    cfg["tool_profile"] = "full"
    monkeypatch.setattr(server, "_TOOL_SCHEMAS", None)
    assert "incremental" not in _props("index_folder")
    with patch("jcodemunch_mcp.tools.index_folder.index_folder", return_value={"success": True}) as fake:
        await server.call_tool("index_folder", {"path": "/tmp", "incremental": "false"})
    assert fake.call_args[1]["incremental"] is False
