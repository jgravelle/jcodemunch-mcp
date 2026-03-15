"""Tests for tools module."""

import json
import pytest
from unittest.mock import patch

from jcodemunch_mcp.tools.index_repo import (
    parse_github_url,
    discover_source_files,
    should_skip_file,
)
from jcodemunch_mcp.security import MAX_INDEX_FILES_ENV_VAR, MAX_FOLDER_FILES_ENV_VAR


def test_parse_github_url_full():
    """Test parsing full GitHub URL."""
    assert parse_github_url("https://github.com/owner/repo") == ("owner", "repo")


def test_parse_github_url_with_git():
    """Test parsing URL with .git suffix."""
    assert parse_github_url("https://github.com/owner/repo.git") == ("owner", "repo")


def test_parse_github_url_short():
    """Test parsing owner/repo shorthand."""
    assert parse_github_url("owner/repo") == ("owner", "repo")


def test_should_skip_file():
    """Test skip patterns."""
    assert should_skip_file("node_modules/foo.js") is True
    assert should_skip_file("vendor/github.com/foo.go") is True
    assert should_skip_file("src/main.py") is False


def test_discover_source_files():
    """Test file discovery from tree entries."""
    tree_entries = [
        {"path": "src/main.py", "type": "blob", "size": 1000},
        {"path": "node_modules/foo.js", "type": "blob", "size": 500},
        {"path": "README.md", "type": "blob", "size": 200},
        {"path": "src/utils.py", "type": "blob", "size": 500},
        {"path": "src/engine.cpp", "type": "blob", "size": 700},
        {"path": "include/engine.hpp", "type": "blob", "size": 350},
    ]
    
    files, _, truncated = discover_source_files(tree_entries, gitignore_content=None)
    
    assert "src/main.py" in files
    assert "src/utils.py" in files
    assert "src/engine.cpp" in files
    assert "include/engine.hpp" in files
    assert "node_modules/foo.js" not in files
    assert "README.md" not in files  # Not a source file
    assert truncated is False


def test_discover_source_files_respects_max():
    """Test that max_files limit is respected."""
    tree_entries = [
        {"path": f"file{i}.py", "type": "blob", "size": 100}
        for i in range(1000)
    ]
    
    files, _, truncated = discover_source_files(tree_entries, max_files=100)
    assert len(files) == 100
    assert truncated is True


def test_discover_source_files_prioritizes_src():
    """Test that src/ files are prioritized."""
    tree_entries = [
        {"path": f"other/file{i}.py", "type": "blob", "size": 100}
        for i in range(300)
    ] + [
        {"path": f"src/file{i}.py", "type": "blob", "size": 100}
        for i in range(300)
    ]
    
    files, _, truncated = discover_source_files(tree_entries, max_files=100)
    # Most files should be from src/
    src_count = sum(1 for f in files if f.startswith("src/"))
    assert src_count > 50  # Majority should be src/
    assert truncated is True


def test_discover_source_files_uses_env_override():
    """Test that environment override is used when max_files is omitted."""
    tree_entries = [
        {"path": f"file{i}.py", "type": "blob", "size": 100}
        for i in range(20)
    ]

    with patch.dict("os.environ", {MAX_INDEX_FILES_ENV_VAR: "7"}, clear=False):
        files, _, truncated = discover_source_files(tree_entries)

    assert len(files) == 7
    assert truncated is True


def test_discover_source_files_explicit_max_overrides_env():
    """Explicit max_files should win over environment configuration."""
    tree_entries = [
        {"path": f"file{i}.py", "type": "blob", "size": 100}
        for i in range(20)
    ]

    with patch.dict("os.environ", {MAX_INDEX_FILES_ENV_VAR: "7"}, clear=False):
        files, _, truncated = discover_source_files(tree_entries, max_files=5)

    assert len(files) == 5
    assert truncated is True


def test_discover_source_files_exact_limit_is_not_truncated():
    """An exact match to the limit should not be reported as truncation."""
    tree_entries = [
        {"path": f"file{i}.py", "type": "blob", "size": 100}
        for i in range(5)
    ]

    files, _, truncated = discover_source_files(tree_entries, max_files=5)

    assert len(files) == 5
    assert truncated is False


# --- has_index / version mismatch ---

class TestHasIndex:
    def test_returns_false_when_no_index(self, tmp_path):
        from jcodemunch_mcp.storage.index_store import IndexStore
        store = IndexStore(base_path=str(tmp_path))
        assert store.has_index("local", "myrepo") is False

    def test_returns_true_after_save(self, tmp_path):
        from jcodemunch_mcp.storage.index_store import IndexStore
        store = IndexStore(base_path=str(tmp_path))
        store.save_index(
            owner="local", name="myrepo",
            source_files=[], symbols=[], raw_files={},
        )
        assert store.has_index("local", "myrepo") is True

    def test_returns_true_for_future_version_index(self, tmp_path):
        """has_index should return True even when load_index rejects a future version."""
        from jcodemunch_mcp.storage.index_store import IndexStore, INDEX_VERSION
        store = IndexStore(base_path=str(tmp_path))
        # Write a fake index with a version newer than current
        index_path = store._index_path("local", "myrepo")
        index_path.write_text(
            json.dumps({"index_version": INDEX_VERSION + 1, "indexed_at": "2099-01-01T00:00:00"}),
            encoding="utf-8",
        )
        assert store.load_index("local", "myrepo") is None  # rejected
        assert store.has_index("local", "myrepo") is True   # file still there


class TestVersionMismatchWarning:
    def test_index_folder_warns_on_version_mismatch(self, tmp_path, monkeypatch):
        """index_folder should include a warning when the on-disk index is a newer version."""
        import json
        from jcodemunch_mcp.storage.index_store import IndexStore, INDEX_VERSION
        from jcodemunch_mcp.tools.index_folder import index_folder

        # Plant a newer-version index in the store
        store = IndexStore(base_path=str(tmp_path / "store"))
        # We need to know what repo_name index_folder will compute for src_dir
        src_dir = tmp_path / "project"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("def hello(): pass\n")

        import hashlib
        digest = hashlib.sha1(str(src_dir.resolve()).encode("utf-8")).hexdigest()[:8]
        repo_name = f"{src_dir.name}-{digest}"
        index_path = store._index_path("local", repo_name)
        index_path.write_text(
            json.dumps({"index_version": INDEX_VERSION + 1, "indexed_at": "2099-01-01T00:00:00"}),
            encoding="utf-8",
        )

        result = index_folder(
            str(src_dir),
            use_ai_summaries=False,
            storage_path=str(tmp_path / "store"),
        )

        assert result["success"] is True
        warnings = result.get("warnings", [])
        assert any("newer version" in w for w in warnings)


class TestNestedGitignore:
    def test_nested_gitignore_excludes_subdirectory_files(self, tmp_path):
        """Nested .gitignore files should exclude files relative to their own directory."""
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        # Root structure: cap/ and core/ each with their own .gitignore + deps/
        for subdir in ("cap", "core"):
            sub = tmp_path / subdir
            (sub / "deps").mkdir(parents=True)
            (sub / "deps" / "some_dep.ex").write_text("defmodule Dep do end\n")
            (sub / "app.ex").write_text("defmodule App do end\n")
            (sub / ".gitignore").write_text("/deps/\n/_build/\n")

        files, _, skip_counts = discover_local_files(tmp_path)
        paths = [f.as_posix() for f in files]

        # app.ex files should be indexed
        assert any("cap/app.ex" in p for p in paths)
        assert any("core/app.ex" in p for p in paths)

        # deps/ files should be excluded by nested .gitignore
        assert not any("deps" in p for p in paths)
        assert skip_counts["gitignore"] >= 2

    def test_root_gitignore_still_works(self, tmp_path):
        """Root .gitignore should still be respected."""
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        (tmp_path / ".gitignore").write_text("*.pyc\n__pycache__/\n")
        (tmp_path / "main.py").write_text("def main(): pass\n")
        (tmp_path / "main.pyc").write_bytes(b"\x00compiled")

        files, _, skip_counts = discover_local_files(tmp_path)
        paths = [f.as_posix() for f in files]

        assert any("main.py" in p for p in paths)
        assert not any(".pyc" in p for p in paths)


class TestFolderFileLimitEnvVar:
    def test_folder_specific_env_var_respected(self, tmp_path):
        """JCODEMUNCH_MAX_FOLDER_FILES should cap index_folder file discovery."""
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        for i in range(10):
            (tmp_path / f"file{i}.py").write_text(f"def f{i}(): pass\n")

        with patch.dict("os.environ", {MAX_FOLDER_FILES_ENV_VAR: "3"}, clear=False):
            files, _, _ = discover_local_files(tmp_path)

        assert len(files) == 3

    def test_legacy_env_var_still_works_for_folders(self, tmp_path):
        """JCODEMUNCH_MAX_INDEX_FILES should still cap index_folder when folder var unset."""
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        for i in range(10):
            (tmp_path / f"file{i}.py").write_text(f"def f{i}(): pass\n")

        env = {MAX_INDEX_FILES_ENV_VAR: "4"}
        # Ensure the folder-specific var is absent so legacy fallback is tested
        with patch.dict("os.environ", env, clear=False):
            import os
            os.environ.pop(MAX_FOLDER_FILES_ENV_VAR, None)
            files, _, _ = discover_local_files(tmp_path)

        assert len(files) == 4
