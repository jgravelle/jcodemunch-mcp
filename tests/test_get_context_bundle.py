"""Tests for get_context_bundle tool."""

from unittest.mock import patch, MagicMock
from jcodemunch_mcp.tools.get_context_bundle import get_context_bundle, _extract_imports


def _make_symbol():
    return {
        "id": "test.py::my_func",
        "name": "my_func",
        "kind": "function",
        "file": "test.py",
        "line": 5,
        "end_line": 10,
        "signature": "def my_func(x: int) -> str:",
        "docstring": "Does something.",
        "language": "python",
        "byte_length": 120,
    }


def _make_index(symbol):
    index = MagicMock()
    index.get_symbol = MagicMock(return_value=symbol)
    return index


def _make_store(index, symbol_content="    return str(x)", file_content=None):
    store = MagicMock()
    store.load_index = MagicMock(return_value=index)
    store.get_symbol_content = MagicMock(return_value=symbol_content)
    store.get_file_content = MagicMock(return_value=file_content)
    store._content_dir = MagicMock(return_value=MagicMock(__truediv__=MagicMock()))
    return store


def test_context_bundle_includes_symbol_source():
    """Returned bundle contains symbol code."""
    sym = _make_symbol()
    file_content = "import os\n\ndef my_func(x: int) -> str:\n    return str(x)\n"
    index = _make_index(sym)
    store = _make_store(index, symbol_content="    return str(x)", file_content=file_content)

    with patch("jcodemunch_mcp.tools.get_context_bundle.resolve_repo", return_value=("local", "test")), \
         patch("jcodemunch_mcp.tools.get_context_bundle.IndexStore", return_value=store):
        result = get_context_bundle(repo="test", symbol_id="test.py::my_func")

    assert "error" not in result
    assert result["source"] == "    return str(x)"
    assert result["name"] == "my_func"
    assert result["kind"] == "function"


def test_context_bundle_includes_imports():
    """Returned bundle contains file's import lines."""
    sym = _make_symbol()
    file_content = "import os\nfrom pathlib import Path\n\ndef my_func(x: int) -> str:\n    return str(x)\n"
    index = _make_index(sym)
    store = _make_store(index, file_content=file_content)

    with patch("jcodemunch_mcp.tools.get_context_bundle.resolve_repo", return_value=("local", "test")), \
         patch("jcodemunch_mcp.tools.get_context_bundle.IndexStore", return_value=store):
        result = get_context_bundle(repo="test", symbol_id="test.py::my_func")

    assert len(result["imports"]) == 2
    assert "import os" in result["imports"][0]
    assert "from pathlib import Path" in result["imports"][1]


def test_context_bundle_invalid_symbol_id():
    """Invalid symbol ID returns graceful error, not a crash."""
    index = MagicMock()
    index.get_symbol = MagicMock(return_value=None)

    store = MagicMock()
    store.load_index = MagicMock(return_value=index)

    with patch("jcodemunch_mcp.tools.get_context_bundle.resolve_repo", return_value=("local", "test")), \
         patch("jcodemunch_mcp.tools.get_context_bundle.IndexStore", return_value=store):
        result = get_context_bundle(repo="test", symbol_id="nonexistent::symbol")

    assert "error" in result
    assert "not found" in result["error"].lower()


def test_context_bundle_meta_envelope():
    """_meta is present with expected fields."""
    sym = _make_symbol()
    index = _make_index(sym)
    store = _make_store(index, file_content="import os\n\ndef my_func(x):\n    pass\n")

    with patch("jcodemunch_mcp.tools.get_context_bundle.resolve_repo", return_value=("local", "test")), \
         patch("jcodemunch_mcp.tools.get_context_bundle.IndexStore", return_value=store):
        result = get_context_bundle(repo="test", symbol_id="test.py::my_func")

    assert "_meta" in result
    meta = result["_meta"]
    assert "timing_ms" in meta
    assert "tokens_saved" in meta
    assert "total_tokens_saved" in meta


def test_extract_imports_python():
    """_extract_imports finds Python import lines."""
    content = "import os\nfrom sys import argv\n\nx = 1\nfrom pathlib import Path\n"
    imports = _extract_imports(content, "python")
    assert len(imports) == 3


def test_extract_imports_go_block():
    """_extract_imports handles Go block imports."""
    content = 'package main\n\nimport (\n\t"fmt"\n\t"os"\n)\n\nfunc main() {}\n'
    imports = _extract_imports(content, "go")
    assert len(imports) == 4  # import (, "fmt", "os", )
