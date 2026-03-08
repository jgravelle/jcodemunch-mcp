"""Tests for repository-wide retrieval tools."""

from jcodemunch_mcp.parser import Symbol
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.get_file_content import get_file_content
from jcodemunch_mcp.tools.get_file_outline import get_file_outline
from jcodemunch_mcp.tools.get_repo_outline import get_repo_outline
from jcodemunch_mcp.tools.search_text import search_text


def _seed_repo(tmp_path):
    store = IndexStore(base_path=str(tmp_path))
    symbol = Symbol(
        id="src-main-py::run#function",
        file="src/main.py",
        name="run",
        qualified_name="run",
        kind="function",
        language="python",
        signature="def run():",
        byte_offset=0,
        byte_length=45,
    )

    store.save_index(
        owner="retrieval",
        name="demo",
        source_files=["src/main.py", "include/no_symbols.h"],
        symbols=[symbol],
        raw_files={
            "src/main.py": "def run():\n    # TODO: wire main\n    return FLAG\n",
            "include/no_symbols.h": "// TODO: wire header\n#define FLAG 1\n",
        },
        languages={"python": 1, "cpp": 1},
        file_languages={
            "src/main.py": "python",
            "include/no_symbols.h": "cpp",
        },
        file_summaries={
            "src/main.py": "Runs the demo entry point.",
            "include/no_symbols.h": "",
        },
    )


def test_get_file_outline_returns_language_for_no_symbol_file(tmp_path):
    """No-symbol files should still resolve language and summaries."""
    _seed_repo(tmp_path)

    result = get_file_outline("retrieval/demo", "include/no_symbols.h", storage_path=str(tmp_path))

    assert result["language"] == "cpp"
    assert result["file_summary"] == ""
    assert result["symbols"] == []
    assert result["_meta"]["symbol_count"] == 0


def test_get_repo_outline_counts_no_symbol_files(tmp_path):
    """Repo outline should count every indexed file, not just symbol-bearing ones."""
    _seed_repo(tmp_path)

    result = get_repo_outline("retrieval/demo", storage_path=str(tmp_path))

    assert result["file_count"] == 2
    assert result["languages"] == {"python": 1, "cpp": 1}
    assert result["directories"] == {"include/": 1, "src/": 1}


def test_search_text_groups_matches_and_includes_context(tmp_path):
    """search_text should return grouped matches and surrounding lines."""
    _seed_repo(tmp_path)

    result = search_text("retrieval/demo", "TODO", context_lines=1, storage_path=str(tmp_path))

    assert result["result_count"] == 2
    grouped = {entry["file"]: entry["matches"] for entry in result["results"]}
    assert grouped["include/no_symbols.h"][0]["text"] == "// TODO: wire header"
    assert grouped["include/no_symbols.h"][0]["before"] == []
    assert grouped["include/no_symbols.h"][0]["after"] == ["#define FLAG 1"]
    assert grouped["src/main.py"][0]["before"] == ["def run():"]
    assert grouped["src/main.py"][0]["after"] == ["    return FLAG"]


def test_search_text_truncates_across_grouped_matches(tmp_path):
    """max_results should cap total matches, not files."""
    _seed_repo(tmp_path)

    result = search_text("retrieval/demo", "TODO", max_results=1, context_lines=1, storage_path=str(tmp_path))

    assert result["result_count"] == 1
    assert result["_meta"]["truncated"] is True
    assert len(result["results"]) == 1
    assert result["results"][0]["file"] == "include/no_symbols.h"


def test_get_file_content_clamps_line_ranges(tmp_path):
    """get_file_content should clamp requested lines to file bounds."""
    _seed_repo(tmp_path)

    result = get_file_content(
        "retrieval/demo",
        "src/main.py",
        start_line=2,
        end_line=99,
        storage_path=str(tmp_path),
    )

    assert result["start_line"] == 2
    assert result["end_line"] == 3
    assert result["line_count"] == 3
    assert result["language"] == "python"
    assert result["content"] == "    # TODO: wire main\n    return FLAG"
