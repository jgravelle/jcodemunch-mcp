"""Tests for storage module."""

import pytest
import json
from pathlib import Path

from jcodemunch_mcp.storage import IndexStore, CodeIndex
from jcodemunch_mcp.parser import Symbol


def test_save_and_load_index(tmp_path):
    """Test saving and loading an index."""
    store = IndexStore(base_path=str(tmp_path))
    
    symbols = [
        Symbol(
            id="test-py::foo",
            file="test.py",
            name="foo",
            qualified_name="foo",
            kind="function",
            language="python",
            signature="def foo():",
            summary="Does foo",
            byte_offset=0,
            byte_length=100,
        )
    ]
    
    index = store.save_index(
        owner="testowner",
        name="testrepo",
        source_files=["test.py"],
        symbols=symbols,
        raw_files={"test.py": "def foo(): pass"},
        languages={"python": 1}
    )
    
    assert index.repo == "testowner/testrepo"
    assert len(index.symbols) == 1
    
    # Load and verify
    loaded = store.load_index("testowner", "testrepo")
    assert loaded is not None
    assert loaded.repo == "testowner/testrepo"
    assert len(loaded.symbols) == 1


def test_byte_offset_retrieval(tmp_path):
    """Test byte-offset content retrieval."""
    store = IndexStore(base_path=str(tmp_path))
    
    content = "line1\nline2\ndef foo():\n    pass\n"
    
    symbols = [
        Symbol(
            id="test-py::foo",
            file="test.py",
            name="foo",
            qualified_name="foo",
            kind="function",
            language="python",
            signature="def foo():",
            byte_offset=14,  # Start of "def foo()"
            byte_length=14,  # Length of "def foo():\n    pass"
        )
    ]
    
    store.save_index(
        owner="testowner",
        name="testrepo",
        source_files=["test.py"],
        symbols=symbols,
        raw_files={"test.py": content},
        languages={"python": 1}
    )
    
    # Retrieve symbol content
    source = store.get_symbol_content("testowner", "testrepo", "test-py::foo")
    assert source is not None
    assert "def foo():" in source


def test_list_repos(tmp_path):
    """Test listing indexed repositories."""
    store = IndexStore(base_path=str(tmp_path))
    
    # Create two indexes
    for owner, name in [("owner1", "repo1"), ("owner2", "repo2")]:
        store.save_index(
            owner=owner,
            name=name,
            source_files=["main.py"],
            symbols=[],
            raw_files={"main.py": ""},
            languages={"python": 1}
        )
    
    repos = store.list_repos()
    assert len(repos) == 2


def test_delete_index(tmp_path):
    """Test deleting an index."""
    store = IndexStore(base_path=str(tmp_path))
    
    store.save_index(
        owner="test",
        name="repo",
        source_files=["main.py"],
        symbols=[],
        raw_files={"main.py": ""},
        languages={"python": 1}
    )
    
    assert store.load_index("test", "repo") is not None
    
    store.delete_index("test", "repo")
    
    assert store.load_index("test", "repo") is None


def test_codeindex_get_symbol():
    """Test getting a symbol by ID from CodeIndex."""
    index = CodeIndex(
        repo="test/repo",
        owner="test",
        name="repo",
        indexed_at="2025-01-15T10:00:00",
        source_files=["main.py"],
        languages={"python": 1},
        symbols=[
            {"id": "main-py::foo", "name": "foo", "kind": "function"},
            {"id": "main-py::bar", "name": "bar", "kind": "function"},
        ]
    )
    
    sym = index.get_symbol("main-py::foo")
    assert sym is not None
    assert sym["name"] == "foo"
    
    assert index.get_symbol("nonexistent") is None


def test_codeindex_search():
    """Test searching symbols."""
    index = CodeIndex(
        repo="test/repo",
        owner="test",
        name="repo",
        indexed_at="2025-01-15T10:00:00",
        source_files=["main.py"],
        languages={"python": 1},
        symbols=[
            {"id": "main-py::authenticate", "name": "authenticate", "kind": "function", "signature": "def authenticate(user)", "summary": "Auth user", "keywords": ["auth"]},
            {"id": "main-py::login", "name": "login", "kind": "function", "signature": "def login()", "summary": "Login user", "keywords": []},
            {"id": "main-py::MyClass", "name": "MyClass", "kind": "class", "signature": "class MyClass", "summary": "A class", "keywords": []},
        ]
    )
    
    # Search by name
    results = index.search("authenticate")
    assert len(results) > 0
    assert results[0]["name"] == "authenticate"
    
    # Search by kind filter
    results = index.search("login", kind="class")
    assert len(results) == 0  # login is a function
    
    results = index.search("login", kind="function")
    assert len(results) > 0

