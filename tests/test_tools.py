"""Tests for tools module."""

import pytest
from unittest.mock import patch

from jcodemunch_mcp.tools.index_repo import (
    parse_github_url,
    discover_source_files,
    should_skip_file,
)
from jcodemunch_mcp.security import MAX_INDEX_FILES_ENV_VAR


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
    ]
    
    files, truncated = discover_source_files(tree_entries, gitignore_content=None)
    
    assert "src/main.py" in files
    assert "src/utils.py" in files
    assert "node_modules/foo.js" not in files
    assert "README.md" not in files  # Not a source file
    assert truncated is False


def test_discover_source_files_respects_max():
    """Test that max_files limit is respected."""
    tree_entries = [
        {"path": f"file{i}.py", "type": "blob", "size": 100}
        for i in range(1000)
    ]
    
    files, truncated = discover_source_files(tree_entries, max_files=100)
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
    
    files, truncated = discover_source_files(tree_entries, max_files=100)
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
        files, truncated = discover_source_files(tree_entries)

    assert len(files) == 7
    assert truncated is True


def test_discover_source_files_explicit_max_overrides_env():
    """Explicit max_files should win over environment configuration."""
    tree_entries = [
        {"path": f"file{i}.py", "type": "blob", "size": 100}
        for i in range(20)
    ]

    with patch.dict("os.environ", {MAX_INDEX_FILES_ENV_VAR: "7"}, clear=False):
        files, truncated = discover_source_files(tree_entries, max_files=5)

    assert len(files) == 5
    assert truncated is True


def test_discover_source_files_exact_limit_is_not_truncated():
    """An exact match to the limit should not be reported as truncation."""
    tree_entries = [
        {"path": f"file{i}.py", "type": "blob", "size": 100}
        for i in range(5)
    ]

    files, truncated = discover_source_files(tree_entries, max_files=5)

    assert len(files) == 5
    assert truncated is False
