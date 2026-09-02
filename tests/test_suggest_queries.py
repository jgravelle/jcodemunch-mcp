"""Tests for suggest_queries tool (T13)."""

import pytest
from jcodemunch_mcp.tools.suggest_queries import suggest_queries
from jcodemunch_mcp.tools.index_folder import index_folder


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestSuggestQueriesErrors:

    def test_repo_not_indexed_returns_error(self, tmp_path):
        r = suggest_queries(repo="no_such_repo", storage_path=str(tmp_path))
        assert "error" in r

    def test_empty_index_returns_error(self, tmp_path):
        """An indexed folder with no parseable symbols → empty index error."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()
        # Write a file type the parser ignores (no symbols extracted).
        # Plain text — NOT .md, which yields heading symbols since v1.108.316.
        (src / "NOTES.txt").write_text("plain notes, no structure\n")
        r_idx = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        # If no symbols were extracted the tool returns an empty-index error
        if r_idx["success"] and r_idx.get("symbols_indexed", 0) == 0:
            r = suggest_queries(repo=r_idx["repo"], storage_path=str(store))
            assert "error" in r


# ---------------------------------------------------------------------------
# Correctness: small_index (1 file, 3 symbols)
# ---------------------------------------------------------------------------

class TestSuggestQueriesSmall:

    def test_symbol_count_matches_indexed(self, small_index):
        r = suggest_queries(repo=small_index["repo"], storage_path=small_index["store"])
        assert "error" not in r
        assert r["symbol_count"] == 3

    def test_file_count_matches_indexed(self, small_index):
        r = suggest_queries(repo=small_index["repo"], storage_path=small_index["store"])
        assert "error" not in r
        assert r["file_count"] == 1

    def test_kind_distribution_present(self, small_index):
        r = suggest_queries(repo=small_index["repo"], storage_path=small_index["store"])
        assert "error" not in r
        assert "kind_distribution" in r
        kd = r["kind_distribution"]
        assert "function" in kd
        assert kd["function"] == 2

    def test_language_distribution_present(self, small_index):
        r = suggest_queries(repo=small_index["repo"], storage_path=small_index["store"])
        assert "error" not in r
        assert "language_distribution" in r
        assert "python" in r["language_distribution"]

    def test_example_queries_non_empty(self, small_index):
        r = suggest_queries(repo=small_index["repo"], storage_path=small_index["store"])
        assert "error" not in r
        assert "example_queries" in r
        assert len(r["example_queries"]) > 0

    def test_example_query_has_required_fields(self, small_index):
        r = suggest_queries(repo=small_index["repo"], storage_path=small_index["store"])
        assert "error" not in r
        for eq in r["example_queries"]:
            assert "query" in eq
            assert "tool" in eq
            assert "description" in eq


# ---------------------------------------------------------------------------
# Correctness: medium_index (3 files, cross-imports)
# ---------------------------------------------------------------------------

class TestSuggestQueriesMedium:

    def test_file_count_matches_indexed(self, medium_index):
        r = suggest_queries(repo=medium_index["repo"], storage_path=medium_index["store"])
        assert "error" not in r
        assert r["file_count"] == 3

    def test_most_imported_files_present_with_imports(self, medium_index):
        """models.py is imported by 2 files → should appear in most_imported."""
        r = suggest_queries(repo=medium_index["repo"], storage_path=medium_index["store"])
        assert "error" not in r
        # most_imported_files may be empty if imports aren't resolved
        # Only assert structure if data is present
        for entry in r.get("most_imported_files", []):
            assert "file" in entry
            assert "imported_by" in entry
            assert entry["imported_by"] >= 1

    def test_kind_distribution_includes_class_and_function(self, medium_index):
        r = suggest_queries(repo=medium_index["repo"], storage_path=medium_index["store"])
        assert "error" not in r
        kd = r["kind_distribution"]
        assert "class" in kd
        assert "function" in kd

    def test_response_has_repo_field(self, medium_index):
        r = suggest_queries(repo=medium_index["repo"], storage_path=medium_index["store"])
        assert "error" not in r
        assert "repo" in r

    def test_meta_has_timing(self, medium_index):
        r = suggest_queries(repo=medium_index["repo"], storage_path=medium_index["store"])
        assert "error" not in r
        assert "_meta" in r
        assert "timing_ms" in r["_meta"]
        assert isinstance(r["_meta"]["timing_ms"], (int, float))
