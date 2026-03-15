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
            byte_offset=12,  # Start of "def foo():"
            byte_length=19,  # Length of "def foo():\n    pass"
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


def test_get_file_content_preserves_exact_newlines(tmp_path):
    """Cached file reads should preserve the original newline style."""
    store = IndexStore(base_path=str(tmp_path))
    content = "a\r\nb\r\n"

    store.save_index(
        owner="testowner",
        name="newlines",
        source_files=["test.txt"],
        symbols=[],
        raw_files={"test.txt": content},
        languages={"text": 1},
    )

    assert store.get_file_content("testowner", "newlines", "test.txt") == content


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


def test_list_repos_includes_optional_metadata(tmp_path):
    """display_name and source_root should surface when present."""
    store = IndexStore(base_path=str(tmp_path))

    store.save_index(
        owner="local",
        name="demo-deadbeef",
        source_files=["main.py"],
        symbols=[],
        raw_files={"main.py": "print('x')\n"},
        languages={"python": 1},
        display_name="demo",
        source_root="/tmp/demo",
    )

    repos = store.list_repos()
    assert repos[0]["display_name"] == "demo"
    assert repos[0]["source_root"] == "/tmp/demo"


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


def test_save_index_rejects_path_traversal_in_raw_files(tmp_path):
    """Raw file cache writes must not escape content dir."""
    store = IndexStore(base_path=str(tmp_path))

    with pytest.raises(ValueError, match="Unsafe file path"):
        store.save_index(
            owner="evil",
            name="repo",
            source_files=["../../escape.py"],
            symbols=[],
            raw_files={"../../escape.py": "print('x')"},
            languages={"python": 1},
        )


def test_get_symbol_content_rejects_traversal_symbol_file(tmp_path):
    """Traversal in stored symbol file path should be blocked on read."""
    store = IndexStore(base_path=str(tmp_path))
    content_dir = tmp_path / "owner-repo"
    content_dir.mkdir(parents=True)
    (content_dir / "safe.py").write_text("def ok():\n    return 1\n", encoding="utf-8")

    # Write index manually to simulate malicious/corrupt symbol metadata.
    index_path = tmp_path / "owner-repo.json"
    index_path.write_text(
        json.dumps({
            "repo": "owner/repo",
            "owner": "owner",
            "name": "repo",
            "indexed_at": "2025-01-01T00:00:00",
            "source_files": ["safe.py"],
            "languages": {"python": 1},
            "symbols": [{
                "id": "bad::sym",
                "file": "../../etc/passwd",
                "name": "bad",
                "qualified_name": "bad",
                "kind": "function",
                "language": "python",
                "signature": "def bad():",
                "docstring": "",
                "summary": "",
                "decorators": [],
                "keywords": [],
                "parent": "",
                "line": 1,
                "end_line": 1,
                "byte_offset": 0,
                "byte_length": 10,
                "content_hash": "",
            }],
            "index_version": 2,
            "file_hashes": {"safe.py": "abc"},
            "git_head": "",
        }),
        encoding="utf-8",
    )

    assert store.get_symbol_content("owner", "repo", "bad::sym") is None


def test_save_index_rejects_invalid_owner_component(tmp_path):
    """Owner/name inputs should be safe for filesystem paths."""
    store = IndexStore(base_path=str(tmp_path))

    with pytest.raises(ValueError, match="Invalid owner"):
        store.save_index(
            owner="../escape",
            name="repo",
            source_files=["main.py"],
            symbols=[],
            raw_files={"main.py": ""},
            languages={"python": 1},
        )


def test_save_index_rejects_path_separator_in_name(tmp_path):
    """Reject names that contain path separators (/ or \\)."""
    store = IndexStore(base_path=str(tmp_path))

    with pytest.raises(ValueError, match="Invalid name"):
        store.save_index(
            owner="owner",
            name="repo/evil",
            source_files=["main.py"],
            symbols=[],
            raw_files={"main.py": ""},
            languages={"python": 1},
        )


def test_save_index_sanitizes_special_chars_in_name(tmp_path):
    """Names with spaces or other special characters are sanitized to hyphens."""
    store = IndexStore(base_path=str(tmp_path))

    store.save_index(
        owner="local",
        name="my project (v2)",
        source_files=["main.py"],
        symbols=[],
        raw_files={"main.py": ""},
        languages={"python": 1},
    )

    # Index should be retrievable under the sanitized slug
    index = store.load_index("local", "my project (v2)")
    assert index is not None
    # The on-disk file should use the sanitized name (spaces/parens → hyphens, collapsed)
    assert (tmp_path / "local-my-project-v2.json").exists()


def test_load_index_backfills_new_metadata_for_legacy_indexes(tmp_path):
    """Older indexes without file_languages/source_root should still load cleanly."""
    store = IndexStore(base_path=str(tmp_path))
    content_dir = tmp_path / "legacy-demo"
    (content_dir / "src").mkdir(parents=True)
    (content_dir / "include").mkdir(parents=True)
    (content_dir / "src" / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (content_dir / "include" / "no_symbols.h").write_text("/* no symbols */\n", encoding="utf-8")

    legacy_index = {
        "repo": "legacy/demo",
        "owner": "legacy",
        "name": "demo",
        "indexed_at": "2025-01-01T00:00:00",
        "source_files": ["src/main.py", "include/no_symbols.h"],
        "languages": {"python": 1},
        "symbols": [{
            "id": "src-main-py::run#function",
            "file": "src/main.py",
            "name": "run",
            "qualified_name": "run",
            "kind": "function",
            "language": "python",
            "signature": "def run():",
            "docstring": "",
            "summary": "",
            "decorators": [],
            "keywords": [],
            "parent": "",
            "line": 1,
            "end_line": 2,
            "byte_offset": 0,
            "byte_length": 19,
            "content_hash": "",
        }],
        "index_version": 3,
        "file_hashes": {
            "src/main.py": "a",
            "include/no_symbols.h": "b",
        },
        "git_head": "",
    }
    (tmp_path / "legacy-demo.json").write_text(json.dumps(legacy_index), encoding="utf-8")

    loaded = store.load_index("legacy", "demo")
    assert loaded is not None
    assert loaded.source_root == ""
    assert loaded.display_name == "demo"
    assert loaded.file_languages == {
        "src/main.py": "python",
        "include/no_symbols.h": "cpp",
    }
    assert loaded.languages == {"python": 1, "cpp": 1}


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


def test_incremental_save_recomputes_languages_from_merged_symbols(tmp_path):
    """incremental_save should derive languages from merged symbols, not caller counts."""
    store = IndexStore(base_path=str(tmp_path))

    py_sym = Symbol(
        id="app-py::run#function",
        file="app.py",
        name="run",
        qualified_name="run",
        kind="function",
        language="python",
        signature="def run():",
        byte_offset=0,
        byte_length=10,
    )
    c_sym = Symbol(
        id="api-h::only_c#function",
        file="api.h",
        name="only_c",
        qualified_name="only_c",
        kind="function",
        language="c",
        signature="int only_c(void)",
        byte_offset=0,
        byte_length=20,
    )

    store.save_index(
        owner="lang",
        name="demo",
        source_files=["app.py", "api.h"],
        symbols=[py_sym, c_sym],
        raw_files={"app.py": "def run():\n    pass\n", "api.h": "int only_c(void) { return 0; }\n"},
        languages={"python": 1, "c": 1},
    )

    cpp_sym = Symbol(
        id="api-h::Widget#class",
        file="api.h",
        name="Widget",
        qualified_name="Widget",
        kind="class",
        language="cpp",
        signature="class Widget",
        byte_offset=0,
        byte_length=12,
    )

    updated = store.incremental_save(
        owner="lang",
        name="demo",
        changed_files=["api.h"],
        new_files=[],
        deleted_files=[],
        new_symbols=[cpp_sym],
        raw_files={"api.h": "class Widget { public: int Get() const; };"},
        languages={"c": 99},  # stale caller-provided data; should be ignored
    )

    assert updated is not None
    assert updated.languages == {"python": 1, "cpp": 1}
