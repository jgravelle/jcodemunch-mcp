"""Tests for cleanup PR: resolve_repo helper and input validation."""

import json

import pytest

from jcodemunch_mcp.tools._utils import resolve_repo


class TestResolveRepo:
    """Tests for the resolve_repo helper."""

    def test_owner_slash_repo(self):
        owner, name = resolve_repo("octocat/hello-world")
        assert owner == "octocat"
        assert name == "hello-world"

    def test_name_only_lookup(self, tmp_path):
        # IndexStore.list_repos() looks for *.json in base_path
        index_data = {
            "repo": "octocat/hello-world",
            "indexed_at": "2024-01-01T00:00:00",
            "symbols": [],
            "source_files": [],
            "languages": {},
        }
        (tmp_path / "octocat__hello-world.json").write_text(json.dumps(index_data))

        owner, name = resolve_repo("hello-world", storage_path=str(tmp_path))
        assert owner == "octocat"
        assert name == "hello-world"

    def test_unknown_repo_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Repository not found: nonexistent"):
            resolve_repo("nonexistent", storage_path=str(tmp_path))


class TestInputValidation:
    """Tests for input clamping in tool functions."""

    def test_search_symbols_clamps_max_results(self):
        from jcodemunch_mcp.tools.search_symbols import search_symbols

        # max_results gets clamped, but the function will fail on repo lookup.
        # We just verify it doesn't crash with extreme values.
        result = search_symbols("owner/repo", "query", max_results=0)
        assert "error" in result  # repo not indexed, but didn't crash

        result = search_symbols("owner/repo", "query", max_results=999)
        assert "error" in result

    def test_search_text_clamps_max_results(self):
        from jcodemunch_mcp.tools.search_text import search_text

        result = search_text("owner/repo", "query", max_results=0)
        assert "error" in result

        result = search_text("owner/repo", "query", max_results=999)
        assert "error" in result

    def test_get_symbol_clamps_context_lines(self):
        from jcodemunch_mcp.tools.get_symbol import get_symbol

        result = get_symbol("owner/repo", "sym_id", context_lines=-5)
        assert "error" in result

        result = get_symbol("owner/repo", "sym_id", context_lines=999)
        assert "error" in result
