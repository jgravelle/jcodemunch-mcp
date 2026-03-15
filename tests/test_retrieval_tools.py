"""Tests for repository-wide retrieval tools."""

import json
from datetime import datetime, timedelta, timezone

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


def _backdate_index(tmp_path, owner, name, days):
    """Overwrite indexed_at in the stored JSON to simulate an old index."""
    index_file = tmp_path / f"{owner}-{name}.json"
    data = json.loads(index_file.read_text())
    data["indexed_at"] = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    index_file.write_text(json.dumps(data))


def test_get_repo_outline_staleness_warning_when_old(tmp_path):
    """get_repo_outline should include staleness_warning when index is >= 7 days old."""
    _seed_repo(tmp_path)
    _backdate_index(tmp_path, "retrieval", "demo", days=8)

    result = get_repo_outline("retrieval/demo", storage_path=str(tmp_path))

    assert "staleness_warning" in result
    assert "8 days old" in result["staleness_warning"]


def test_get_repo_outline_no_staleness_warning_when_fresh(tmp_path):
    """get_repo_outline should not include staleness_warning for a recent index."""
    _seed_repo(tmp_path)

    result = get_repo_outline("retrieval/demo", storage_path=str(tmp_path))

    assert "staleness_warning" not in result


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


def test_search_text_respects_file_pattern(tmp_path):
    """file_pattern should constrain grouped search to matching files only."""
    _seed_repo(tmp_path)

    result = search_text(
        "retrieval/demo",
        "TODO",
        file_pattern="src/*.py",
        context_lines=1,
        storage_path=str(tmp_path),
    )

    assert result["result_count"] == 1
    assert [entry["file"] for entry in result["results"]] == ["src/main.py"]


def test_search_text_clamps_context_lines(tmp_path):
    """Excessively large context requests should be clamped, not blow up responses."""
    _seed_repo(tmp_path)

    result = search_text("retrieval/demo", "TODO", context_lines=999, storage_path=str(tmp_path))

    assert result["context_lines"] == 10
    grouped = {entry["file"]: entry["matches"] for entry in result["results"]}
    assert grouped["src/main.py"][0]["before"] == ["def run():"]
    assert grouped["src/main.py"][0]["after"] == ["    return FLAG", ""]


def test_search_text_skips_missing_cached_files(tmp_path):
    """Missing raw cache entries should not crash grouped search."""
    _seed_repo(tmp_path)
    cached = tmp_path / "retrieval-demo" / "include" / "no_symbols.h"
    cached.unlink()

    result = search_text("retrieval/demo", "TODO", storage_path=str(tmp_path))

    assert result["result_count"] == 1
    assert [entry["file"] for entry in result["results"]] == ["src/main.py"]


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


def test_get_file_content_handles_reversed_ranges(tmp_path):
    """end_line before start_line should collapse to a valid single-line slice."""
    _seed_repo(tmp_path)

    result = get_file_content(
        "retrieval/demo",
        "src/main.py",
        start_line=3,
        end_line=1,
        storage_path=str(tmp_path),
    )

    assert result["start_line"] == 3
    assert result["end_line"] == 3
    assert result["content"] == "    return FLAG"


def test_get_file_content_handles_empty_file(tmp_path):
    """Empty cached files should return a stable empty slice contract."""
    store = IndexStore(base_path=str(tmp_path))
    store.save_index(
        owner="retrieval",
        name="empty",
        source_files=["empty.py"],
        symbols=[],
        raw_files={"empty.py": ""},
        languages={"python": 1},
        file_languages={"empty.py": "python"},
    )

    result = get_file_content("retrieval/empty", "empty.py", storage_path=str(tmp_path))

    assert result["line_count"] == 0
    assert result["start_line"] == 0
    assert result["end_line"] == 0
    assert result["content"] == ""


def test_get_file_content_returns_unsliced_content_verbatim(tmp_path):
    """Unsliced file retrieval should return the cached text unchanged."""
    store = IndexStore(base_path=str(tmp_path))
    content = "first\r\nsecond\r\n"
    store.save_index(
        owner="retrieval",
        name="verbatim",
        source_files=["demo.txt"],
        symbols=[],
        raw_files={"demo.txt": content},
        languages={"text": 1},
        file_languages={"demo.txt": "text"},
    )

    result = get_file_content("retrieval/verbatim", "demo.txt", storage_path=str(tmp_path))

    assert result["line_count"] == 2
    assert result["start_line"] == 1
    assert result["end_line"] == 2
    assert result["content"] == content


def test_get_file_content_reports_missing_cached_file(tmp_path):
    """If metadata exists but raw content is gone, the tool should fail cleanly."""
    _seed_repo(tmp_path)
    cached = tmp_path / "retrieval-demo" / "src" / "main.py"
    cached.unlink()

    result = get_file_content("retrieval/demo", "src/main.py", storage_path=str(tmp_path))

    assert result["error"] == "File content not found: src/main.py"
