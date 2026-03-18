"""Tests for mtime-based incremental indexing optimization."""

import os
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage.index_store import _file_hash


def _write_py(d: Path, name: str, content: str) -> Path:
    p = d / name
    p.write_text(content, encoding="utf-8")
    return p


class TestMtimeOptimization:
    """Tests for mtime fast-path in incremental indexing."""

    def test_full_index_stores_mtimes(self, tmp_path):
        """After a full index, file_mtimes should be persisted."""
        src = tmp_path / "src"
        src.mkdir()
        store_path = tmp_path / "store"

        _write_py(src, "hello.py", "def hello():\n    return 'hi'\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_path))
        assert result["success"] is True

        store = IndexStore(base_path=str(store_path))
        index = store.load_index("local", list(store.list_repos())[0]["repo"].split("/")[1])
        assert index is not None
        assert len(index.file_mtimes) > 0
        assert "hello.py" in index.file_mtimes
        # mtime should be a positive integer (nanoseconds)
        assert index.file_mtimes["hello.py"] > 0

    def test_incremental_no_changes_skips_hashing(self, tmp_path):
        """When mtimes are unchanged, incremental should skip hashing entirely."""
        src = tmp_path / "src"
        src.mkdir()
        store_path = tmp_path / "store"

        _write_py(src, "hello.py", "def hello():\n    return 'hi'\n")
        _write_py(src, "world.py", "def world():\n    return 'earth'\n")

        # Full index
        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_path))
        assert result["success"] is True

        # Incremental with no changes — should detect via mtime fast-path
        result2 = index_folder(
            str(src), use_ai_summaries=False, storage_path=str(store_path), incremental=True
        )
        assert result2["success"] is True
        assert result2["message"] == "No changes detected"

    def test_mtime_changed_but_content_same(self, tmp_path):
        """If mtime changes but content is the same, file should not be re-indexed."""
        src = tmp_path / "src"
        src.mkdir()
        store_path = tmp_path / "store"

        py_file = _write_py(src, "hello.py", "def hello():\n    return 'hi'\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_path))
        assert result["success"] is True

        # Touch the file to update mtime without changing content
        time.sleep(0.05)
        py_file.write_text("def hello():\n    return 'hi'\n", encoding="utf-8")

        result2 = index_folder(
            str(src), use_ai_summaries=False, storage_path=str(store_path), incremental=True
        )
        assert result2["success"] is True
        assert result2["message"] == "No changes detected"

    def test_mtime_changed_with_content_change_detected(self, tmp_path):
        """When mtime differs and content changed, file should be re-indexed."""
        src = tmp_path / "src"
        src.mkdir()
        store_path = tmp_path / "store"

        _write_py(src, "hello.py", "def hello():\n    return 'hi'\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_path))
        assert result["success"] is True

        # Actually change the file content
        time.sleep(0.05)
        _write_py(src, "hello.py", "def hello():\n    return 'hello world'\n\ndef greet():\n    pass\n")

        result2 = index_folder(
            str(src), use_ai_summaries=False, storage_path=str(store_path), incremental=True
        )
        assert result2["success"] is True
        assert result2["incremental"] is True
        assert result2["changed"] == 1

    def test_backward_compat_v4_index_without_mtimes(self, tmp_path):
        """A v4 index without file_mtimes should still work (falls back to hash-all)."""
        src = tmp_path / "src"
        src.mkdir()
        store_path = tmp_path / "store"

        _write_py(src, "hello.py", "def hello():\n    return 'hi'\n")

        # Full index to create the index
        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_path))
        assert result["success"] is True

        # Manually remove file_mtimes from the stored index to simulate v4
        store = IndexStore(base_path=str(store_path))
        repos = store.list_repos()
        repo_name = repos[0]["repo"].split("/")[1]
        index = store.load_index("local", repo_name)
        assert index is not None
        index.file_mtimes.clear()

        # Save back without mtimes (simulating old format)
        import json
        index_path = store._index_path("local", repo_name)
        data = json.loads(index_path.read_text(encoding="utf-8"))
        data.pop("file_mtimes", None)
        index_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        # Incremental should still work (all files get hashed since no mtimes)
        _write_py(src, "hello.py", "def hello():\n    return 'changed'\n")
        result2 = index_folder(
            str(src), use_ai_summaries=False, storage_path=str(store_path), incremental=True
        )
        assert result2["success"] is True
        assert result2["incremental"] is True
        assert result2["changed"] == 1

    def test_incremental_merges_mtimes(self, tmp_path):
        """After incremental update, old mtimes should be preserved and new ones added."""
        src = tmp_path / "src"
        src.mkdir()
        store_path = tmp_path / "store"

        _write_py(src, "a.py", "def a():\n    pass\n")
        _write_py(src, "b.py", "def b():\n    pass\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_path))
        assert result["success"] is True

        store = IndexStore(base_path=str(store_path))
        repos = store.list_repos()
        repo_name = repos[0]["repo"].split("/")[1]

        # Add a new file
        _write_py(src, "c.py", "def c():\n    pass\n")

        result2 = index_folder(
            str(src), use_ai_summaries=False, storage_path=str(store_path), incremental=True
        )
        assert result2["success"] is True
        assert result2["new"] == 1

        index = store.load_index("local", repo_name)
        assert index is not None
        # All three files should have mtimes
        assert "a.py" in index.file_mtimes
        assert "b.py" in index.file_mtimes
        assert "c.py" in index.file_mtimes


class TestDetectChangesWithMtimes:
    """Unit tests for IndexStore.detect_changes_with_mtimes."""

    def test_no_existing_index_returns_all_new(self, tmp_path):
        """When no index exists, all files are new."""
        store = IndexStore(base_path=str(tmp_path / "store"))
        hash_fn = MagicMock(side_effect=lambda fp: f"hash_{fp}")

        changed, new, deleted, hashes, mtimes = store.detect_changes_with_mtimes(
            "local", "nonexistent",
            {"a.py": 1000, "b.py": 2000},
            hash_fn,
        )

        assert changed == []
        assert sorted(new) == ["a.py", "b.py"]
        assert deleted == []
        assert hash_fn.call_count == 2
        assert "a.py" in hashes
        assert "b.py" in hashes

    def test_mtime_match_skips_hash(self, tmp_path):
        """When mtimes match, hash_fn should not be called for those files."""
        store_path = tmp_path / "store"
        store = IndexStore(base_path=str(store_path))

        # Create an index with known hashes and mtimes
        from jcodemunch_mcp.parser.symbols import Symbol
        store.save_index(
            owner="local", name="test",
            source_files=["a.py"],
            symbols=[],
            raw_files={"a.py": "content_a"},
            file_hashes={"a.py": _file_hash("content_a")},
            file_mtimes={"a.py": 1000},
        )

        hash_fn = MagicMock()

        changed, new, deleted, hashes, mtimes = store.detect_changes_with_mtimes(
            "local", "test",
            {"a.py": 1000},  # Same mtime
            hash_fn,
        )

        assert changed == []
        assert new == []
        assert deleted == []
        hash_fn.assert_not_called()

    def test_mtime_differs_hash_same_not_changed(self, tmp_path):
        """When mtime differs but hash is the same, file is not changed."""
        store_path = tmp_path / "store"
        store = IndexStore(base_path=str(store_path))

        content_hash = _file_hash("content_a")
        store.save_index(
            owner="local", name="test",
            source_files=["a.py"],
            symbols=[],
            raw_files={"a.py": "content_a"},
            file_hashes={"a.py": content_hash},
            file_mtimes={"a.py": 1000},
        )

        hash_fn = MagicMock(return_value=content_hash)

        changed, new, deleted, hashes, mtimes = store.detect_changes_with_mtimes(
            "local", "test",
            {"a.py": 2000},  # Different mtime
            hash_fn,
        )

        assert changed == []
        assert new == []
        assert deleted == []
        hash_fn.assert_called_once_with("a.py")
        # Mtime should be updated to current value
        assert mtimes["a.py"] == 2000

    def test_mtime_differs_hash_differs_is_changed(self, tmp_path):
        """When mtime differs and hash differs, file is changed."""
        store_path = tmp_path / "store"
        store = IndexStore(base_path=str(store_path))

        store.save_index(
            owner="local", name="test",
            source_files=["a.py"],
            symbols=[],
            raw_files={"a.py": "content_a"},
            file_hashes={"a.py": _file_hash("content_a")},
            file_mtimes={"a.py": 1000},
        )

        new_hash = _file_hash("content_a_modified")
        hash_fn = MagicMock(return_value=new_hash)

        changed, new, deleted, hashes, mtimes = store.detect_changes_with_mtimes(
            "local", "test",
            {"a.py": 2000},
            hash_fn,
        )

        assert changed == ["a.py"]
        assert new == []
        assert deleted == []
        assert hashes["a.py"] == new_hash
