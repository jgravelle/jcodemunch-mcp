"""Tests for the index_file tool."""

import pytest
from pathlib import Path

from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.index_file import index_file
from jcodemunch_mcp.storage import IndexStore


def _write_py(d: Path, name: str, content: str) -> Path:
    p = d / name
    p.write_text(content, encoding="utf-8")
    return p


class TestIndexFile:
    """Tests for single-file indexing."""

    def test_file_not_found(self, tmp_path):
        """Returns error when file doesn't exist."""
        result = index_file(
            path=str(tmp_path / "nonexistent.py"),
            use_ai_summaries=False,
            storage_path=str(tmp_path / "store"),
        )
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_no_matching_index(self, tmp_path):
        """Returns error when file isn't in any indexed folder."""
        src = tmp_path / "src"
        src.mkdir()
        py_file = _write_py(src, "hello.py", "def hello():\n    return 'hi'\n")

        result = index_file(
            path=str(py_file),
            use_ai_summaries=False,
            storage_path=str(tmp_path / "store"),
        )
        assert result["success"] is False
        assert "No indexed folder" in result["error"]

    def test_index_changed_file(self, tmp_path):
        """Modified file gets re-indexed."""
        src = tmp_path / "src"
        src.mkdir()
        store_path = tmp_path / "store"

        py_file = _write_py(src, "calc.py", "def add(a, b):\n    return a + b\n")
        _write_py(src, "util.py", "def noop():\n    pass\n")

        # First, index the folder
        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_path))
        assert result["success"] is True
        original_count = result["symbol_count"]

        # Modify the file
        _write_py(src, "calc.py", "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n")

        # Index just the changed file
        result2 = index_file(
            path=str(py_file),
            use_ai_summaries=False,
            storage_path=str(store_path),
        )
        assert result2["success"] is True
        assert result2["is_new"] is False
        assert result2["symbol_count"] == 2  # add + sub

        # Verify the index was updated
        store = IndexStore(base_path=str(store_path))
        repos = store.list_repos()
        repo_name = repos[0]["repo"].split("/")[1]
        index = store.load_index("local", repo_name)
        assert index is not None
        assert len(index.symbols) == original_count + 1  # added sub()

    def test_index_new_file(self, tmp_path):
        """New file gets added to existing index."""
        src = tmp_path / "src"
        src.mkdir()
        store_path = tmp_path / "store"

        _write_py(src, "hello.py", "def hello():\n    return 'hi'\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_path))
        assert result["success"] is True
        original_count = result["symbol_count"]

        # Create a new file
        new_file = _write_py(src, "world.py", "def world():\n    return 'earth'\n")

        result2 = index_file(
            path=str(new_file),
            use_ai_summaries=False,
            storage_path=str(store_path),
        )
        assert result2["success"] is True
        assert result2["is_new"] is True
        assert result2["symbol_count"] == 1

        # Verify index was updated
        store = IndexStore(base_path=str(store_path))
        repos = store.list_repos()
        repo_name = repos[0]["repo"].split("/")[1]
        index = store.load_index("local", repo_name)
        assert index is not None
        assert len(index.symbols) == original_count + 1

    def test_unchanged_file_early_exit(self, tmp_path):
        """Unchanged file returns early without re-parsing."""
        src = tmp_path / "src"
        src.mkdir()
        store_path = tmp_path / "store"

        py_file = _write_py(src, "hello.py", "def hello():\n    return 'hi'\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_path))
        assert result["success"] is True

        # Index same file again with no changes
        result2 = index_file(
            path=str(py_file),
            use_ai_summaries=False,
            storage_path=str(store_path),
        )
        assert result2["success"] is True
        assert result2["message"] == "File unchanged"

    def test_unsupported_file_type(self, tmp_path):
        """Unsupported file type returns error."""
        src = tmp_path / "src"
        src.mkdir()
        store_path = tmp_path / "store"

        _write_py(src, "hello.py", "def hello():\n    return 'hi'\n")
        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_path))
        assert result["success"] is True

        # Try to index an unsupported file type
        txt_file = src / "readme.txt"
        txt_file.write_text("Just a text file", encoding="utf-8")

        result2 = index_file(
            path=str(txt_file),
            use_ai_summaries=False,
            storage_path=str(store_path),
        )
        assert result2["success"] is False
        assert "Unsupported file type" in result2["error"]

    def test_index_file_stores_mtime(self, tmp_path):
        """index_file should store the file's mtime."""
        src = tmp_path / "src"
        src.mkdir()
        store_path = tmp_path / "store"

        py_file = _write_py(src, "hello.py", "def hello():\n    return 'hi'\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_path))
        assert result["success"] is True

        # Modify and re-index
        _write_py(src, "hello.py", "def hello():\n    return 'world'\n")
        result2 = index_file(
            path=str(py_file),
            use_ai_summaries=False,
            storage_path=str(store_path),
        )
        assert result2["success"] is True

        store = IndexStore(base_path=str(store_path))
        repos = store.list_repos()
        repo_name = repos[0]["repo"].split("/")[1]
        index = store.load_index("local", repo_name)
        assert index is not None
        assert "hello.py" in index.file_mtimes
        assert index.file_mtimes["hello.py"] > 0

    def test_path_not_a_file(self, tmp_path):
        """Returns error when path is a directory."""
        result = index_file(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=str(tmp_path / "store"),
        )
        assert result["success"] is False
        assert "not a file" in result["error"]
