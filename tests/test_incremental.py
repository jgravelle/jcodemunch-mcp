"""Tests for incremental indexing via index_folder."""

import pytest
from pathlib import Path

from jcodemunch_mcp.tools.index_folder import index_folder


def _write_py(d: Path, name: str, content: str) -> Path:
    """Write a Python file into directory d."""
    p = d / name
    p.write_text(content, encoding="utf-8")
    return p


class TestIncrementalIndexFolder:
    """Test incremental indexing through index_folder."""

    def test_full_index_then_incremental_no_changes(self, tmp_path):
        """Incremental re-index with no changes returns early."""
        src = tmp_path / "src"
        src.mkdir()
        store = tmp_path / "store"

        _write_py(src, "hello.py", "def hello():\n    return 'hi'\n")
        _write_py(src, "world.py", "def world():\n    return 'earth'\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True
        assert result["symbol_count"] == 2

        # Incremental with no changes
        result2 = index_folder(
            str(src), use_ai_summaries=False, storage_path=str(store), incremental=True
        )
        assert result2["success"] is True
        assert result2["message"] == "No changes detected"
        assert result2["changed"] == 0
        assert result2["new"] == 0
        assert result2["deleted"] == 0

    def test_incremental_detects_modified_file(self, tmp_path):
        """Incremental re-index detects a modified file."""
        src = tmp_path / "src"
        src.mkdir()
        store = tmp_path / "store"

        _write_py(src, "calc.py", "def add(a, b):\n    return a + b\n")
        _write_py(src, "util.py", "def noop():\n    pass\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True
        original_count = result["symbol_count"]

        # Modify one file: change body and add a function
        _write_py(src, "calc.py", "def add(a, b):\n    return a + b + 1\n\ndef sub(a, b):\n    return a - b\n")

        result2 = index_folder(
            str(src), use_ai_summaries=False, storage_path=str(store), incremental=True
        )
        assert result2["success"] is True
        assert result2["incremental"] is True
        assert result2["changed"] == 1
        assert result2["new"] == 0
        assert result2["deleted"] == 0
        # Should have original symbols + 1 new (sub added)
        assert result2["symbol_count"] == original_count + 1

    def test_incremental_detects_new_file(self, tmp_path):
        """Incremental re-index detects a new file."""
        src = tmp_path / "src"
        src.mkdir()
        store = tmp_path / "store"

        _write_py(src, "a.py", "def func_a():\n    pass\n")

        index_folder(str(src), use_ai_summaries=False, storage_path=str(store))

        # Add a new file
        _write_py(src, "b.py", "def func_b():\n    return 42\n")

        result = index_folder(
            str(src), use_ai_summaries=False, storage_path=str(store), incremental=True
        )
        assert result["success"] is True
        assert result["new"] == 1
        assert result["symbol_count"] == 2

    def test_incremental_detects_deleted_file(self, tmp_path):
        """Incremental re-index detects a deleted file."""
        src = tmp_path / "src"
        src.mkdir()
        store = tmp_path / "store"

        _write_py(src, "keep.py", "def keep():\n    pass\n")
        _write_py(src, "remove.py", "def remove():\n    pass\n")

        index_folder(str(src), use_ai_summaries=False, storage_path=str(store))

        # Delete one file
        (src / "remove.py").unlink()

        result = index_folder(
            str(src), use_ai_summaries=False, storage_path=str(store), incremental=True
        )
        assert result["success"] is True
        assert result["deleted"] == 1
        assert result["symbol_count"] == 1

    def test_incremental_false_does_full_reindex(self, tmp_path):
        """With incremental=False (default), a full re-index is performed."""
        src = tmp_path / "src"
        src.mkdir()
        store = tmp_path / "store"

        _write_py(src, "mod.py", "def original():\n    pass\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True

        # Full re-index (default) should not have incremental key
        result2 = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result2["success"] is True
        assert "incremental" not in result2
