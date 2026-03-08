"""End-to-end server tests."""

import pytest
import json
from unittest.mock import AsyncMock, patch

from jcodemunch_mcp.server import server, list_tools, call_tool


@pytest.mark.asyncio
async def test_server_lists_all_tools():
    """Test that server lists all 12 tools."""
    tools = await list_tools()

    assert len(tools) == 12

    names = {t.name for t in tools}
    expected = {
        "index_repo", "index_folder", "list_repos", "get_file_tree",
        "get_file_outline", "get_file_content", "get_symbol", "get_symbols",
        "search_symbols", "invalidate_cache", "search_text", "get_repo_outline"
    }
    assert names == expected


@pytest.mark.asyncio
async def test_index_repo_tool_schema():
    """Test index_repo tool has correct schema."""
    tools = await list_tools()

    index_repo = next(t for t in tools if t.name == "index_repo")

    assert "url" in index_repo.inputSchema["properties"]
    assert "use_ai_summaries" in index_repo.inputSchema["properties"]
    assert "url" in index_repo.inputSchema["required"]


@pytest.mark.asyncio
async def test_search_symbols_tool_schema():
    """Test search_symbols tool has correct schema."""
    tools = await list_tools()

    search = next(t for t in tools if t.name == "search_symbols")

    props = search.inputSchema["properties"]
    assert "repo" in props
    assert "query" in props
    assert "kind" in props
    assert "file_pattern" in props
    assert "max_results" in props

    # kind should have enum
    assert "enum" in props["kind"]
    assert set(props["kind"]["enum"]) == {"function", "class", "method", "constant", "type"}
    assert "enum" in props["language"]
    assert "cpp" in props["language"]["enum"]


@pytest.mark.asyncio
async def test_search_text_tool_schema():
    """search_text should expose grouped-context parameters."""
    tools = await list_tools()

    search_text = next(t for t in tools if t.name == "search_text")
    props = search_text.inputSchema["properties"]

    assert "repo" in props
    assert "query" in props
    assert "file_pattern" in props
    assert "max_results" in props
    assert "context_lines" in props


@pytest.mark.asyncio
async def test_get_file_content_tool_schema():
    """get_file_content should accept optional line bounds."""
    tools = await list_tools()

    get_file_content = next(t for t in tools if t.name == "get_file_content")
    props = get_file_content.inputSchema["properties"]

    assert "repo" in props
    assert "file_path" in props
    assert "start_line" in props
    assert "end_line" in props


@pytest.mark.asyncio
async def test_call_tool_defaults_index_repo_incremental_true():
    """Omitted MCP args should preserve the tool's incremental default."""
    with patch("jcodemunch_mcp.server.index_repo", new=AsyncMock(return_value={"success": True})) as mock_index_repo:
        await call_tool("index_repo", {"url": "owner/repo"})

    mock_index_repo.assert_awaited_once_with(
        url="owner/repo",
        use_ai_summaries=True,
        storage_path=None,
        incremental=True,
    )


@pytest.mark.asyncio
async def test_call_tool_defaults_index_folder_incremental_true():
    """Local folder tool should also default incremental indexing to True."""
    with patch("jcodemunch_mcp.server.index_folder", return_value={"success": True}) as mock_index_folder:
        await call_tool("index_folder", {"path": "/tmp/project"})

    mock_index_folder.assert_called_once_with(
        path="/tmp/project",
        use_ai_summaries=True,
        storage_path=None,
        extra_ignore_patterns=None,
        follow_symlinks=False,
        incremental=True,
    )


@pytest.mark.asyncio
async def test_call_tool_forwards_search_text_context_lines():
    """Dispatcher should pass through grouped search options unchanged."""
    with patch("jcodemunch_mcp.server.search_text", return_value={"result_count": 1}) as mock_search_text:
        await call_tool("search_text", {"repo": "owner/repo", "query": "TODO", "context_lines": 3})

    mock_search_text.assert_called_once_with(
        repo="owner/repo",
        query="TODO",
        file_pattern=None,
        max_results=20,
        context_lines=3,
        storage_path=None,
    )


@pytest.mark.asyncio
async def test_call_tool_forwards_get_file_content_bounds():
    """Dispatcher should route file-content lookups with optional bounds."""
    with patch("jcodemunch_mcp.server.get_file_content", return_value={"file": "src/main.py"}) as mock_get_file_content:
        await call_tool(
            "get_file_content",
            {"repo": "owner/repo", "file_path": "src/main.py", "start_line": 5, "end_line": 8},
        )

    mock_get_file_content.assert_called_once_with(
        repo="owner/repo",
        file_path="src/main.py",
        start_line=5,
        end_line=8,
        storage_path=None,
    )
