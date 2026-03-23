"""Tests for WatcherChange NamedTuple, watcher memory cache, and fast-path integration."""

import os
import tempfile

import pytest
from pathlib import Path

from jcodemunch_mcp.reindex_state import WatcherChange


class TestWatcherChangeFormat:
    def test_watcher_change_properties(self):
        wc = WatcherChange("modified", "/path/to/file.py", "abc123")
        assert wc.change_type == "modified"
        assert wc.path == "/path/to/file.py"
        assert wc.old_hash == "abc123"

    def test_watcher_change_tuple_access(self):
        wc = WatcherChange("added", "/path/to/file.py", "")
        assert wc[0] == "added"
        assert wc[1] == "/path/to/file.py"
        assert wc[2] == ""

    def test_watcher_change_default_old_hash(self):
        wc = WatcherChange("added", "/path/to/file.py")
        assert wc.old_hash == ""


class TestWatcherMemoryCache:
    def test_watcher_change_with_old_hash(self):
        wc = WatcherChange("modified", "/path/to/file.py", "old_hash_value")
        assert wc.old_hash == "old_hash_value"
        assert wc.change_type == "modified"
        assert wc.path == "/path/to/file.py"


class TestBuildHashCacheIntegration:
    """Verify that _build_hash_cache can actually load an index via IndexStore.

    This catches the bug where _local_repo_id returns 'local/name-hash'
    but store.load_index(owner, name) rejects '/' in the name parameter.
    """

    def test_load_index_with_split_repo_id(self, tmp_path):
        """Simulate what _build_hash_cache does: split repo_id and call load_index."""
        from jcodemunch_mcp.storage.index_store import IndexStore
        from jcodemunch_mcp.watcher import _local_repo_id

        folder_path = str(tmp_path)
        repo_id = _local_repo_id(folder_path)
        assert "/" in repo_id, "repo_id must contain 'local/' prefix"

        repo_owner, repo_store_name = repo_id.split("/", 1)
        store = IndexStore(base_path=str(tmp_path / ".code-index"))

        # Must not raise ValueError — this is the exact call _build_hash_cache makes
        result = store.load_index(repo_owner, repo_store_name)
        assert result is None  # no index yet, but no crash

    def test_load_index_rejects_unsplit_repo_id(self, tmp_path):
        """Passing the full repo_id as name must raise (validates the bug existed)."""
        from jcodemunch_mcp.storage.index_store import IndexStore
        from jcodemunch_mcp.watcher import _local_repo_id

        folder_path = str(tmp_path)
        repo_id = _local_repo_id(folder_path)  # "local/name-hash"
        store = IndexStore(base_path=str(tmp_path / ".code-index"))

        with pytest.raises(ValueError, match="Path separator"):
            store.load_index("local", repo_id)  # <-- the old bug


class TestFastPathDeletedFiles:
    """Verify that deleted files are processed on the memory-cache fast path."""

    def test_deleted_file_with_memory_cache(self, tmp_path):
        """When use_memory_hash_cache=True, deleted files must still be removed from the index."""
        from jcodemunch_mcp.tools.index_folder import index_folder

        # Create a test file and index it
        test_file = tmp_path / "hello.py"
        test_file.write_text("def hello():\n    return 'world'\n")

        result = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=str(tmp_path / ".code-index"),
            incremental=False,
        )
        assert result["success"]
        assert result["symbol_count"] >= 1

        # Now delete the file and call index_folder with changed_paths simulating
        # a watcher delete event with old_hash (memory cache path)
        abs_path = str(test_file.resolve())
        test_file.unlink()

        watcher_changes = [WatcherChange("deleted", abs_path, "some_old_hash")]
        result2 = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=str(tmp_path / ".code-index"),
            incremental=True,
            changed_paths=watcher_changes,
        )
        assert result2["success"]
        assert result2.get("deleted", 0) >= 1, (
            f"Expected at least 1 deleted file, got {result2}"
        )
