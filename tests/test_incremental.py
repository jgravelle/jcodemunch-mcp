"""Tests for incremental indexing via index_folder."""

import pytest
from pathlib import Path

from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.storage import IndexStore


def _write_py(d: Path, name: str, content: str) -> Path:
    """Write a Python file into directory d."""
    p = d / name
    p.write_text(content, encoding="utf-8")
    return p


def _write_file(d: Path, name: str, content: str) -> Path:
    """Write an arbitrary source file into directory d."""
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

    def test_incremental_reclassifies_h_language_from_c_to_cpp(self, tmp_path):
        """Changing .h from C-like to C++-like should update persisted language counts."""
        src = tmp_path / "src"
        src.mkdir()
        store = tmp_path / "store"

        _write_file(src, "api.h", "int only_c(void) { int v[] = (int[]){1,2,3}; return v[0]; }\n")

        full = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert full["success"] is True
        assert full["languages"] == {"c": 1}

        _write_file(
            src,
            "api.h",
            "namespace demo { class Widget { public: int Get() const; }; }\n",
        )

        inc = index_folder(str(src), use_ai_summaries=False, storage_path=str(store), incremental=True)
        assert inc["success"] is True
        assert inc["changed"] == 1

        owner, repo_name = full["repo"].split("/", 1)
        idx = IndexStore(base_path=str(store)).load_index(owner, repo_name)
        assert idx is not None
        assert idx.languages.get("cpp") == 1
        assert "c" not in idx.languages

    def test_incremental_delete_and_readd_h_updates_language_counts(self, tmp_path):
        """Deleting and re-adding .h with different style should keep language counts correct."""
        src = tmp_path / "src"
        src.mkdir()
        store = tmp_path / "store"

        _write_file(src, "api.h", "int only_c(void) { int v[] = (int[]){1,2,3}; return v[0]; }\n")
        _write_file(src, "main.cpp", "namespace demo { int add(int a, int b) { return a + b; } }\n")

        full = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert full["success"] is True
        assert full["languages"] == {"c": 1, "cpp": 1}

        (src / "api.h").unlink()
        inc_delete = index_folder(str(src), use_ai_summaries=False, storage_path=str(store), incremental=True)
        assert inc_delete["success"] is True
        assert inc_delete["deleted"] == 1

        owner, repo_name = full["repo"].split("/", 1)
        idx_after_delete = IndexStore(base_path=str(store)).load_index(owner, repo_name)
        assert idx_after_delete is not None
        assert idx_after_delete.languages == {"cpp": 1}

        _write_file(src, "api.h", "namespace demo { class Readded { public: int Go() const; }; }\n")
        inc_add = index_folder(str(src), use_ai_summaries=False, storage_path=str(store), incremental=True)
        assert inc_add["success"] is True
        assert inc_add["new"] == 1

        idx_after_add = IndexStore(base_path=str(store)).load_index(owner, repo_name)
        assert idx_after_add is not None
        assert idx_after_add.languages == {"cpp": 2}

    def test_incremental_no_symbol_file_not_repeatedly_new(self, tmp_path):
        """No-symbol files should not be repeatedly reported as new across incremental runs."""
        src = tmp_path / "src"
        src.mkdir()
        store = tmp_path / "store"

        _write_file(src, "main.cpp", "int main() { return 0; }\n")
        _write_file(src, "no_symbols.h", "/* no symbols here */\n")

        full = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert full["success"] is True
        assert full["no_symbols_count"] >= 1

        # No changes should be clean on first incremental run.
        inc1 = index_folder(str(src), use_ai_summaries=False, storage_path=str(store), incremental=True)
        assert inc1["success"] is True
        assert inc1["message"] == "No changes detected"
        assert inc1["changed"] == 0
        assert inc1["new"] == 0
        assert inc1["deleted"] == 0

        # Change the no-symbol file once.
        _write_file(src, "no_symbols.h", "/* still no symbols, but changed */\n")
        inc2 = index_folder(str(src), use_ai_summaries=False, storage_path=str(store), incremental=True)
        assert inc2["success"] is True
        assert inc2["changed"] == 1
        assert inc2["new"] == 0
        assert inc2["deleted"] == 0

        # Next incremental should be clean again (no repeated churn).
        inc3 = index_folder(str(src), use_ai_summaries=False, storage_path=str(store), incremental=True)
        assert inc3["success"] is True
        assert inc3["message"] == "No changes detected"
        assert inc3["changed"] == 0
        assert inc3["new"] == 0
        assert inc3["deleted"] == 0

    def test_index_folder_preserves_crlf_in_cached_files(self, tmp_path):
        """Local folder indexing should not normalize CRLF line endings."""
        src = tmp_path / "src"
        src.mkdir()
        store = tmp_path / "store"
        file_path = src / "main.py"

        with open(file_path, "w", encoding="utf-8", newline="") as f:
            f.write("def hello():\r\n    return 1\r\n")

        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))

        assert result["success"] is True
        owner, repo_name = result["repo"].split("/", 1)
        cached = IndexStore(base_path=str(store)).get_file_content(owner, repo_name, "main.py")
        assert cached == "def hello():\r\n    return 1\r\n"

    def test_local_repo_ids_include_path_hash_and_do_not_collide(self, tmp_path):
        """Two folders with the same basename should get distinct local repo ids."""
        left = tmp_path / "left" / "shared"
        right = tmp_path / "right" / "shared"
        left.mkdir(parents=True)
        right.mkdir(parents=True)
        store = tmp_path / "store"

        _write_py(left, "main.py", "def left():\n    return 'left'\n")
        _write_py(right, "main.py", "def right():\n    return 'right'\n")

        result_left = index_folder(str(left), use_ai_summaries=False, storage_path=str(store), incremental=False)
        result_right = index_folder(str(right), use_ai_summaries=False, storage_path=str(store), incremental=False)

        assert result_left["success"] is True
        assert result_right["success"] is True
        assert result_left["repo"].startswith("local/shared-")
        assert result_right["repo"].startswith("local/shared-")
        assert result_left["repo"] != result_right["repo"]

        repos = IndexStore(base_path=str(store)).list_repos()
        assert {repo["display_name"] for repo in repos} == {"shared"}
        assert len(repos) == 2
