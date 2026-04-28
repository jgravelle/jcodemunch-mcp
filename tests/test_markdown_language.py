"""Tests for Markdown text-only indexing."""

from pathlib import Path

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.languages import LANGUAGE_EXTENSIONS, get_language_for_path
from jcodemunch_mcp.tools.index_folder import discover_local_files, index_folder
from jcodemunch_mcp.tools.index_repo import discover_source_files
from jcodemunch_mcp.tools.search_text import search_text


def test_markdown_extensions_detected():
    assert get_language_for_path("README.md") == "markdown"
    assert get_language_for_path("docs/guide.markdown") == "markdown"
    assert get_language_for_path("docs/page.mdx") == "markdown"
    assert LANGUAGE_EXTENSIONS[".md"] == "markdown"
    assert LANGUAGE_EXTENSIONS[".markdown"] == "markdown"
    assert LANGUAGE_EXTENSIONS[".mdx"] == "markdown"


def test_markdown_parse_file_is_text_only():
    content = "# Architecture\n\nUse `search_text` for documentation lookup.\n"

    assert parse_file(content, "README.md", "markdown") == []


def test_discover_source_files_includes_markdown_docs():
    tree_entries = [
        {"path": "src/main.py", "type": "blob", "size": 100},
        {"path": "README.md", "type": "blob", "size": 100},
        {"path": "docs/guide.mdx", "type": "blob", "size": 100},
        {"path": "notes.txt", "type": "blob", "size": 100},
    ]

    files, _, truncated, total = discover_source_files(tree_entries)

    assert "src/main.py" in files
    assert "README.md" in files
    assert "docs/guide.mdx" in files
    assert "notes.txt" not in files
    assert truncated is False
    assert total == 3


def test_discover_local_files_includes_markdown_docs(tmp_path):
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.markdown").write_text("# Guide\n", encoding="utf-8")
    (docs / "page.mdx").write_text("# Page\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not indexed by default\n", encoding="utf-8")

    files, warnings, skip_counts = discover_local_files(tmp_path)
    paths = {Path(f).relative_to(tmp_path).as_posix() for f in files}

    assert warnings == []
    assert "README.md" in paths
    assert "docs/guide.markdown" in paths
    assert "docs/page.mdx" in paths
    assert "notes.txt" not in paths
    assert skip_counts["wrong_extension"] == 1


def test_indexed_markdown_is_searchable_text(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Project\n\nDocumentation lookup should find this sentence.\n",
        encoding="utf-8",
    )

    result = index_folder(
        str(tmp_path),
        use_ai_summaries=False,
        storage_path=str(tmp_path / ".index"),
        context_providers=False,
    )
    assert result["success"] is True
    assert "README.md" in result["files"]
    assert result["languages"]["markdown"] == 1

    matches = search_text(
        result["repo"],
        "Documentation lookup",
        file_pattern="*.md",
        storage_path=str(tmp_path / ".index"),
    )
    assert matches["result_count"] == 1
    assert matches["results"][0]["file"] == "README.md"
