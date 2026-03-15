"""Tests for Luau (Roblox) language support in jcodemunch-mcp."""
import pytest
from jcodemunch_mcp.parser import parse_file


LUAU_SERVICE = """\
-- RaidService handles raid mechanics between players
local RaidService = {}
RaidService.__index = RaidService

export type RaidConfig = {
    duration: number,
    maxPlayers: number,
    rewards: {string},
}

type State = "idle" | "active" | "complete"

-- Create a new RaidService instance
function RaidService.new(config: RaidConfig): RaidService
    local self = setmetatable({}, RaidService)
    self.config = config
    return self
end

-- Start the raid
function RaidService:Start()
    self.active = true
end

function RaidService:GetRewards(player: Player, multiplier: number): {string}
    return {}
end

local function helperFunction(x: number, y: number): number
    return x + y
end
"""


def test_luau_parser_available():
    """Smoke test: luau grammar must load without raising."""
    from tree_sitter_language_pack import get_parser
    parser = get_parser("luau")
    assert parser is not None


def test_luau_symbols_extracted():
    symbols = parse_file(LUAU_SERVICE, "RaidService.luau", "luau")
    assert len(symbols) >= 5


def test_luau_module_method():
    symbols = parse_file(LUAU_SERVICE, "RaidService.luau", "luau")
    new_fn = next((s for s in symbols if s.name == "new"), None)
    assert new_fn is not None
    assert new_fn.kind == "method"
    assert new_fn.qualified_name == "RaidService.new"
    assert new_fn.parent == "RaidService"


def test_luau_oop_method():
    symbols = parse_file(LUAU_SERVICE, "RaidService.luau", "luau")
    start = next((s for s in symbols if s.name == "Start"), None)
    assert start is not None
    assert start.kind == "method"
    assert start.qualified_name == "RaidService:Start"
    assert start.parent == "RaidService"


def test_luau_local_function():
    symbols = parse_file(LUAU_SERVICE, "RaidService.luau", "luau")
    helper = next((s for s in symbols if s.name == "helperFunction"), None)
    assert helper is not None
    assert helper.kind == "function"
    assert helper.parent is None
    assert "local function" in helper.signature


def test_luau_typed_params_in_signature():
    symbols = parse_file(LUAU_SERVICE, "RaidService.luau", "luau")
    rewards = next((s for s in symbols if s.name == "GetRewards"), None)
    assert rewards is not None
    assert "player: Player" in rewards.signature
    assert "multiplier: number" in rewards.signature


def test_luau_return_type_in_signature():
    symbols = parse_file(LUAU_SERVICE, "RaidService.luau", "luau")
    helper = next((s for s in symbols if s.name == "helperFunction"), None)
    assert helper is not None
    assert ": number" in helper.signature


def test_luau_export_type():
    symbols = parse_file(LUAU_SERVICE, "RaidService.luau", "luau")
    raid_config = next((s for s in symbols if s.name == "RaidConfig"), None)
    assert raid_config is not None
    assert raid_config.kind == "type"
    assert "export type" in raid_config.signature


def test_luau_local_type():
    symbols = parse_file(LUAU_SERVICE, "RaidService.luau", "luau")
    state = next((s for s in symbols if s.name == "State"), None)
    assert state is not None
    assert state.kind == "type"


def test_luau_docstring():
    symbols = parse_file(LUAU_SERVICE, "RaidService.luau", "luau")
    start = next((s for s in symbols if s.name == "Start"), None)
    assert start is not None
    assert "Start the raid" in start.docstring


def test_luau_language_field():
    symbols = parse_file(LUAU_SERVICE, "RaidService.luau", "luau")
    assert all(s.language == "luau" for s in symbols)


def test_luau_no_return_type_without_annotation():
    """Functions without return type annotations should not have spurious types."""
    symbols = parse_file(LUAU_SERVICE, "RaidService.luau", "luau")
    start = next((s for s in symbols if s.name == "Start"), None)
    assert start is not None
    # Signature should end with () — no return type
    assert start.signature == "function RaidService:Start()"


def test_luau_extension_registered():
    from jcodemunch_mcp.parser.languages import LANGUAGE_EXTENSIONS
    assert ".luau" in LANGUAGE_EXTENSIONS
    assert LANGUAGE_EXTENSIONS[".luau"] == "luau"


def test_luau_language_in_registry():
    from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY
    assert "luau" in LANGUAGE_REGISTRY
