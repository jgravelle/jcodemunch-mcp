"""Tests for the parser module (Phase 1)."""

import pytest
from jcodemunch_mcp.parser import parse_file, Symbol


PYTHON_SOURCE = '''
class MyClass:
    """A sample class."""
    def method(self, x: int) -> str:
        """Do something."""
        return str(x)

def standalone(a, b):
    """Standalone function."""
    return a + b

MAX_SIZE = 100
'''


def test_parse_python():
    """Test Python parsing extracts expected symbols."""
    symbols = parse_file(PYTHON_SOURCE, "test.py", "python")
    
    # Should have class, method, function, constant
    assert len(symbols) >= 3
    
    # Check class
    class_syms = [s for s in symbols if s.kind == "class"]
    assert len(class_syms) == 1
    assert class_syms[0].name == "MyClass"
    assert "A sample class" in class_syms[0].docstring
    
    # Check method
    method_syms = [s for s in symbols if s.kind == "method"]
    assert len(method_syms) == 1
    assert method_syms[0].name == "method"
    assert method_syms[0].parent is not None
    
    # Check standalone function
    func_syms = [s for s in symbols if s.kind == "function" and s.name == "standalone"]
    assert len(func_syms) == 1
    assert "Standalone function" in func_syms[0].docstring
    
    # Check constant
    const_syms = [s for s in symbols if s.kind == "constant"]
    assert len(const_syms) == 1
    assert const_syms[0].name == "MAX_SIZE"


def test_symbol_id_format():
    """Test symbol ID generation."""
    from jcodemunch_mcp.parser import make_symbol_id

    assert make_symbol_id("src/main.py", "MyClass.method", "method") == "src/main.py::MyClass.method#method"
    assert make_symbol_id("test.py", "standalone", "function") == "test.py::standalone#function"
    # Without kind falls back to no suffix
    assert make_symbol_id("test.py", "foo") == "test.py::foo"


def test_unknown_language_returns_empty():
    """Test that unknown languages return empty list."""
    result = parse_file("some code", "test.unknown", "unknown")
    assert result == []


def test_symbol_byte_offsets():
    """Test that byte offsets are correct."""
    symbols = parse_file(PYTHON_SOURCE, "test.py", "python")

    for sym in symbols:
        # Byte offset should be non-negative
        assert sym.byte_offset >= 0
        assert sym.byte_length > 0

        # Line numbers should be positive
        assert sym.line > 0
        assert sym.end_line >= sym.line


LUA_SOURCE = """\
--- Initialise the addon
-- @param name string
local function init(name)
    return {name = name}
end

function MyAddon.OnLoad(self)
    print("loaded")
end

--- Handle combat log event
function MyAddon:OnCombatLogEvent(event, ...)
    self:process(event)
end
"""


def test_lua_local_function():
    symbols = parse_file(LUA_SOURCE, "addon.lua", "lua")
    names = {s.qualified_name for s in symbols}
    assert "init" in names
    sym = next(s for s in symbols if s.qualified_name == "init")
    assert sym.kind == "function"
    assert sym.parent is None
    assert "Initialise the addon" in sym.docstring


def test_lua_dot_method():
    symbols = parse_file(LUA_SOURCE, "addon.lua", "lua")
    sym = next(s for s in symbols if s.qualified_name == "MyAddon.OnLoad")
    assert sym.kind == "method"
    assert sym.parent == "MyAddon"
    assert sym.name == "OnLoad"


def test_lua_colon_method():
    symbols = parse_file(LUA_SOURCE, "addon.lua", "lua")
    sym = next(s for s in symbols if s.qualified_name == "MyAddon:OnCombatLogEvent")
    assert sym.kind == "method"
    assert sym.parent == "MyAddon"
    assert "Handle combat log event" in sym.docstring


def test_lua_extension_registered():
    from jcodemunch_mcp.parser.languages import LANGUAGE_EXTENSIONS
    assert LANGUAGE_EXTENSIONS.get(".lua") == "lua"

