"""End-to-end server tests."""

import pytest
import json

from jcodemunch_mcp.server import server, list_tools, call_tool


@pytest.mark.asyncio
async def test_server_lists_seven_tools():
    """Test that server lists all 7 tools."""
    tools = await list_tools()
    
    assert len(tools) == 7
    
    names = {t.name for t in tools}
    expected = {
        "index_repo", "list_repos", "get_file_tree",
        "get_file_outline", "get_symbol", "get_symbols", "search_symbols"
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

