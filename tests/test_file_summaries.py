"""Tests for file-level summaries feature."""

import json
import tempfile
from pathlib import Path

import pytest

from jcodemunch_mcp.parser.symbols import Symbol
from jcodemunch_mcp.summarizer.file_summarize import (
    _heuristic_summary,
    generate_file_summaries,
)
from jcodemunch_mcp.storage.index_store import IndexStore, CodeIndex, INDEX_VERSION


# --- _heuristic_summary tests ---

def _make_symbol(name, kind, file="test.py", parent=None):
    return Symbol(
        id=f"{file}::{name}#{kind}",
        file=file,
        name=name,
        qualified_name=name,
        kind=kind,
        language="python",
        signature=f"def {name}()" if kind == "function" else f"class {name}",
        parent=parent,
    )


def test_heuristic_summary_single_class():
    cls = _make_symbol("MyClass", "class")
    method1 = _make_symbol("method1", "method", parent="test.py::MyClass#class")
    method2 = _make_symbol("method2", "method", parent="test.py::MyClass#class")
    result = _heuristic_summary("test.py", [cls, method1, method2])
    assert "MyClass" in result
    assert "2 methods" in result


def test_heuristic_summary_multi_function():
    funcs = [_make_symbol(f"func_{i}", "function") for i in range(5)]
    result = _heuristic_summary("test.py", funcs)
    assert "5 functions" in result
    assert "func_0" in result
    assert "..." in result


def test_heuristic_summary_few_functions():
    funcs = [_make_symbol(f"func_{i}", "function") for i in range(2)]
    result = _heuristic_summary("test.py", funcs)
    assert "2 functions" in result
    assert "..." not in result


def test_heuristic_summary_empty():
    assert _heuristic_summary("test.py", []) == ""


# --- generate_file_summaries tests ---

def test_generate_file_summaries_heuristic():
    """Heuristic summary from symbols."""
    symbols = {"b.py": [_make_symbol("bar", "function", file="b.py")]}
    result = generate_file_summaries(symbols)
    assert "bar" in result["b.py"]


def test_generate_file_summaries_empty_fallback():
    """No symbols -> empty string."""
    symbols = {"c.py": []}
    result = generate_file_summaries(symbols)
    assert result["c.py"] == ""


# --- Context provider integration tests ---

def test_generate_with_context_providers():
    """Context providers produce enriched summaries."""
    from jcodemunch_mcp.parser.context.base import ContextProvider, FileContext
    from pathlib import Path

    class _MockProvider(ContextProvider):
        @property
        def name(self):
            return "mock"

        def detect(self, fp):
            return True

        def load(self, fp):
            pass

        def get_file_context(self, file_path):
            if Path(file_path).stem == "enriched":
                return FileContext(description="Business context here", tags=["daily"])
            return None

        def stats(self):
            return {}

    provider = _MockProvider()
    symbols = {"enriched.py": [_make_symbol("func", "function", file="enriched.py")]}
    result = generate_file_summaries(symbols, context_providers=[provider])
    assert "Business context here" in result["enriched.py"]
    assert "func" in result["enriched.py"]


def test_generate_context_only_no_symbols():
    """Provider context for files with no symbols."""
    from jcodemunch_mcp.parser.context.base import ContextProvider, FileContext
    from pathlib import Path

    class _MockProvider(ContextProvider):
        @property
        def name(self):
            return "mock"

        def detect(self, fp):
            return True

        def load(self, fp):
            pass

        def get_file_context(self, file_path):
            if Path(file_path).stem == "ctx_only":
                return FileContext(description="Only from provider", tags=["nightly"])
            return None

        def stats(self):
            return {}

    provider = _MockProvider()
    symbols = {"ctx_only.sql": []}
    result = generate_file_summaries(symbols, context_providers=[provider])
    assert "Only from provider" in result["ctx_only.sql"]


def test_backward_compat_dbt_project_kwarg():
    """Old dbt_project= kwarg doesn't crash."""
    symbols = {"a.py": [_make_symbol("foo", "function", file="a.py")]}
    result = generate_file_summaries(symbols, dbt_project=None)
    assert "foo" in result["a.py"]


# --- Storage round-trip tests ---

def test_storage_roundtrip_file_summaries():
    """Save and load an index with file_summaries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = IndexStore(base_path=tmpdir)
        sym = _make_symbol("hello", "function")
        summaries = {"test.py": "Utility functions for testing."}

        index = store.save_index(
            owner="test",
            name="repo",
            source_files=["test.py"],
            symbols=[sym],
            raw_files={"test.py": "def hello(): pass"},
            languages={"python": 1},
            file_summaries=summaries,
        )

        assert index.file_summaries == summaries

        loaded = store.load_index("test", "repo")
        assert loaded is not None
        assert loaded.file_summaries == summaries


def test_backward_compat_v2_index():
    """Loading a v2 index (without file_summaries) should succeed with empty dict."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write a v2-style index file directly
        index_data = {
            "repo": "test/repo",
            "owner": "test",
            "name": "repo",
            "indexed_at": "2024-01-01T00:00:00",
            "source_files": ["main.py"],
            "languages": {"python": 1},
            "symbols": [],
            "index_version": 2,
            "file_hashes": {},
            "git_head": "",
        }
        index_path = Path(tmpdir) / "test-repo.json"
        with open(index_path, "w") as f:
            json.dump(index_data, f)

        store = IndexStore(base_path=tmpdir)
        loaded = store.load_index("test", "repo")
        assert loaded is not None
        assert loaded.file_summaries == {}


def test_index_version_bumped():
    """INDEX_VERSION should reflect the current index schema."""
    assert INDEX_VERSION == 4
