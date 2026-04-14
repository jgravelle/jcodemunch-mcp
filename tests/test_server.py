"""End-to-end server tests."""

import pytest
import json
import threading
from unittest.mock import AsyncMock, patch

from jcodemunch_mcp.server import server, list_tools, call_tool, _coerce_arguments, _ensure_tool_schemas


@pytest.mark.asyncio
async def test_server_lists_all_tools():
    """Test that server lists all 36 enabled tools (test_summarizer disabled by default)."""
    from jcodemunch_mcp import config as config_module
    from copy import deepcopy

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()
    config_module._GLOBAL_CONFIG.update(deepcopy(config_module.DEFAULTS))

    try:
        tools = await list_tools()

        assert len(tools) == 51

        names = {t.name for t in tools}
        expected = {
            "index_repo", "index_folder", "index_file", "summarize_repo", "list_repos", "resolve_repo",
            "get_file_tree", "get_file_outline", "get_file_content", "get_symbol_source",
            "search_symbols", "invalidate_cache", "search_text", "get_repo_outline",
            "find_importers", "find_references", "check_references", "search_columns", "get_context_bundle",
            "get_session_stats", "get_session_context", "get_session_snapshot", "plan_turn", "register_edit",
            "get_dependency_graph", "get_blast_radius",
            "get_symbol_diff", "get_class_hierarchy", "get_related_symbols", "suggest_queries",
            "get_symbol_importance", "find_dead_code",
            "get_changed_symbols", "get_ranked_context", "embed_repo",
            "get_cross_repo_map",
            "get_call_hierarchy", "get_impact_preview",
            "get_dependency_cycles", "get_coupling_metrics", "get_layer_violations",
            "check_rename_safe", "get_dead_code_v2", "get_extraction_candidates",
            "plan_refactoring",
            "get_symbol_complexity", "get_churn_rate", "get_hotspots", "get_repo_health",
            "audit_agent_config", "get_untested_symbols",
        }
        assert names == expected
        assert "test_summarizer" not in names  # disabled by default in DEFAULTS
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


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
    assert set(props["kind"]["enum"]) == {"function", "class", "method", "constant", "type", "template", "import"}
    assert "enum" in props["language"]
    assert "cpp" in props["language"]["enum"]
    assert "razor" in props["language"]["enum"]


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
    assert "is_regex" in props


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
    with patch("jcodemunch_mcp.tools.index_repo.index_repo", new=AsyncMock(return_value={"success": True})) as mock_index_repo:
        await call_tool("index_repo", {"url": "owner/repo"})

    mock_index_repo.assert_awaited_once_with(
        url="owner/repo",
        use_ai_summaries=True,
        storage_path=None,
        incremental=True,
        extra_ignore_patterns=None,
        progress_cb=None,
    )


@pytest.mark.asyncio
async def test_call_tool_defaults_index_folder_incremental_true():
    """Local folder tool should also default incremental indexing to True."""
    with patch("jcodemunch_mcp.tools.index_folder.index_folder", return_value={"success": True}) as mock_index_folder:
        await call_tool("index_folder", {"path": "/tmp/project"})

    mock_index_folder.assert_called_once_with(
        path="/tmp/project",
        use_ai_summaries=True,
        storage_path=None,
        extra_ignore_patterns=None,
        follow_symlinks=False,
        incremental=True,
        progress_cb=None,
    )


@pytest.mark.asyncio
async def test_call_tool_forwards_search_text_context_lines():
    """Dispatcher should pass through grouped search options unchanged."""
    with patch("jcodemunch_mcp.tools.search_text.search_text", return_value={"result_count": 1}) as mock_search_text:
        await call_tool("search_text", {"repo": "owner/repo", "query": "TODO", "context_lines": 3})

    mock_search_text.assert_called_once_with(
        repo="owner/repo",
        query="TODO",
        file_pattern=None,
        max_results=20,
        context_lines=3,
        is_regex=False,
        storage_path=None,
    )


@pytest.mark.asyncio
async def test_index_folder_dispatched_via_to_thread():
    """index_folder must run in a thread-pool thread, not on the event loop thread.

    This guards against regressions where the sync call_tool branch accidentally
    awaits index_folder directly, which would block the asyncio event loop.
    """
    thread_used = []

    def recording_index_folder(**kwargs):
        thread_used.append(threading.current_thread())
        return {"success": True}

    with patch("jcodemunch_mcp.tools.index_folder.index_folder", recording_index_folder):
        await call_tool("index_folder", {"path": "/tmp/project"})

    assert thread_used, "index_folder was never called"
    assert thread_used[0] is not threading.main_thread(), (
        "index_folder ran on the main thread — asyncio.to_thread dispatch is broken"
    )


@pytest.mark.asyncio
async def test_call_tool_forwards_get_file_content_bounds():
    """Dispatcher should route file-content lookups with optional bounds."""
    with patch("jcodemunch_mcp.tools.get_file_content.get_file_content", return_value={"file": "src/main.py"}) as mock_get_file_content:
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


# ---------------------------------------------------------------------------
# Tests for _coerce_arguments
# ---------------------------------------------------------------------------

def test_coerce_boolean_strings():
    """String booleans are coerced to real booleans."""
    schema = {
        "properties": {
            "enabled": {"type": "boolean"},
            "verbose": {"type": "boolean"},
        }
    }
    args = {"enabled": "true", "verbose": "false"}
    result = _coerce_arguments(args, schema)
    assert result["enabled"] is True
    assert result["verbose"] is False


def test_coerce_boolean_strings_variant_forms():
    """Boolean coercion handles '1', '0', 'yes', 'no', 'on', 'off' variants."""
    schema = {"properties": {"a": {"type": "boolean"}, "b": {"type": "boolean"}, "c": {"type": "boolean"}, "d": {"type": "boolean"}}}
    args = {"a": "1", "b": "0", "c": "yes", "d": "no"}
    result = _coerce_arguments(args, schema)
    assert result == {"a": True, "b": False, "c": True, "d": False}


def test_coerce_boolean_case_insensitive():
    """Boolean string coercion is case-insensitive."""
    schema = {"properties": {"a": {"type": "boolean"}, "b": {"type": "boolean"}}}
    args = {"a": "TRUE", "b": "FALSE"}
    result = _coerce_arguments(args, schema)
    assert result["a"] is True
    assert result["b"] is False


def test_coerce_integer_strings():
    """String integers are coerced to int."""
    schema = {
        "properties": {
            "max_results": {"type": "integer"},
            "depth": {"type": "integer"},
        }
    }
    args = {"max_results": "10", "depth": "3"}
    result = _coerce_arguments(args, schema)
    assert result["max_results"] == 10
    assert isinstance(result["max_results"], int)
    assert result["depth"] == 3
    assert isinstance(result["depth"], int)


def test_coerce_number_strings():
    """String numbers are coerced to float."""
    schema = {"properties": {"threshold": {"type": "number"}}}
    args = {"threshold": "0.75"}
    result = _coerce_arguments(args, schema)
    assert result["threshold"] == 0.75
    assert isinstance(result["threshold"], float)


def test_coerce_leaves_non_string_values_unchanged():
    """Already-typed values pass through without modification."""
    schema = {"properties": {"enabled": {"type": "boolean"}, "count": {"type": "integer"}}}
    args = {"enabled": True, "count": 42}
    result = _coerce_arguments(args, schema)
    assert result == {"enabled": True, "count": 42}


def test_coerce_preserves_unknown_keys():
    """Keys not in the schema pass through untouched."""
    schema = {"properties": {"known": {"type": "boolean"}}}
    args = {"known": "true", "extra": "keep-me"}
    result = _coerce_arguments(args, schema)
    assert result == {"known": True, "extra": "keep-me"}


def test_coerce_non_coercible_string_stays_string():
    """Strings that can't be coerced to the expected type are left unchanged."""
    schema = {"properties": {"count": {"type": "integer"}, "flag": {"type": "boolean"}}}
    args = {"count": "not_a_number", "flag": "maybe"}
    result = _coerce_arguments(args, schema)
    # Not coercible → stays as string (tool will receive it and handle the error)
    assert result["count"] == "not_a_number"
    assert result["flag"] == "maybe"


def test_coerce_empty_properties_returns_arguments_unchanged():
    """Schema with no properties returns arguments as-is."""
    schema = {"properties": {}}
    args = {"foo": "bar", "count": "5"}
    result = _coerce_arguments(args, schema)
    assert result == args


def test_coerce_empty_arguments():
    """Empty arguments dict is returned unchanged."""
    schema = {"properties": {"foo": {"type": "boolean"}}}
    result = _coerce_arguments({}, schema)
    assert result == {}


def test_coerce_mixed_types_in_single_call():
    """Boolean, integer, number, and string fields all coexist correctly."""
    schema = {
        "properties": {
            "enabled": {"type": "boolean"},
            "limit": {"type": "integer"},
            "ratio": {"type": "number"},
            "name": {"type": "string"},
        }
    }
    args = {
        "enabled": "true",
        "limit": "42",
        "ratio": "1.5",
        "name": "my-repo",
    }
    result = _coerce_arguments(args, schema)
    assert result["enabled"] is True
    assert result["limit"] == 42
    assert result["ratio"] == 1.5
    assert result["name"] == "my-repo"


# ---------------------------------------------------------------------------
# Integration tests for call_tool coercion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_tool_coerces_string_boolean_to_true():
    """call_tool coerces string 'true' to boolean True before dispatching."""
    with patch("jcodemunch_mcp.tools.index_folder.index_folder", return_value={"success": True}) as mock_index_folder:
        # "true" as a string — how Claude Code serialises booleans
        await call_tool("index_folder", {"path": "/tmp", "follow_symlinks": "true"})

    mock_index_folder.assert_called_once()
    call_kwargs = mock_index_folder.call_args[1]
    assert call_kwargs["follow_symlinks"] is True


@pytest.mark.asyncio
async def test_call_tool_coerces_string_boolean_to_false():
    """call_tool coerces string 'false' to boolean False before dispatching."""
    with patch("jcodemunch_mcp.tools.index_folder.index_folder", return_value={"success": True}) as mock_index_folder:
        await call_tool("index_folder", {"path": "/tmp", "incremental": "false"})

    mock_index_folder.assert_called_once()
    call_kwargs = mock_index_folder.call_args[1]
    assert call_kwargs["incremental"] is False


@pytest.mark.asyncio
async def test_call_tool_coerces_string_integer():
    """call_tool coerces string integers to int before dispatching."""
    with patch("jcodemunch_mcp.tools.search_symbols.search_symbols", return_value={}) as mock_search:
        await call_tool(
            "search_symbols",
            {"repo": "owner/repo", "query": "foo", "max_results": "20"},
        )

    mock_search.assert_called_once()
    call_kwargs = mock_search.call_args[1]
    assert call_kwargs["max_results"] == 20
    assert isinstance(call_kwargs["max_results"], int)


@pytest.mark.asyncio
async def test_call_tool_validation_error_returns_json_error():
    """call_tool returns a JSON error when coerced arguments still fail validation."""
    result = await call_tool("search_symbols", {"repo": "owner/repo", "query": "foo", "max_results": "not_an_int"})

    assert len(result) == 1
    payload = json.loads(result[0].text)
    assert "error" in payload
    assert "Input validation error" in payload["error"]


@pytest.mark.asyncio
async def test_call_tool_unexpected_coerce_error_returns_json():
    """Unexpected errors return a generic message — raw exception text must not leak to clients."""
    with patch("jcodemunch_mcp.server._ensure_tool_schemas", side_effect=RuntimeError("boom")):
        result = await call_tool("index_folder", {"path": "/tmp"})

    assert len(result) == 1
    payload = json.loads(result[0].text)
    assert "error" in payload
    # Raw exception message must NOT appear in the client response (S3 hardening)
    assert "boom" not in payload["error"]
    assert "index_folder" in payload["error"]


@pytest.mark.asyncio
async def test_call_tool_uses_our_schema_cache_not_sdk():
    """call_tool uses _ensure_tool_schemas, not the private SDK method."""
    with patch("jcodemunch_mcp.server._ensure_tool_schemas") as mock_ensure:
        mock_ensure.return_value = {"index_folder": {"properties": {"path": {"type": "string"}}}}
        with patch("jcodemunch_mcp.tools.index_folder.index_folder", return_value={"success": True}):
            await call_tool("index_folder", {"path": "/tmp"})

    mock_ensure.assert_called_once()



@pytest.mark.asyncio
async def test_descriptions_shared_applied_to_all_tools(monkeypatch):
    """_shared description should apply to all tools with that param."""
    from jcodemunch_mcp import config as config_module

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["descriptions"] = {
            "_shared": {
                "repo": "Custom shared repo description"
            }
        }
        config_module._GLOBAL_CONFIG["disabled_tools"] = []

        tools = await list_tools()

        for tool_name in ["search_symbols", "get_file_tree", "get_symbol_source"]:
            tool = next((t for t in tools if t.name == tool_name), None)
            if tool:
                repo_param = tool.inputSchema.get("properties", {}).get("repo", {})
                if repo_param:
                    assert "Custom shared repo description" in repo_param.get("description", "")
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_descriptions_tool_specific_overrides_shared(monkeypatch):
    """Tool-specific description should override _shared for that param."""
    from jcodemunch_mcp import config as config_module

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["descriptions"] = {
            "_shared": {"repo": "Shared description"},
            "search_symbols": {"repo": "search_symbols specific desc"}
        }
        config_module._GLOBAL_CONFIG["disabled_tools"] = []

        tools = await list_tools()

        search_tool = next((t for t in tools if t.name == "search_symbols"), None)
        assert search_tool is not None
        repo_param = search_tool.inputSchema.get("properties", {}).get("repo", {})
        assert "search_symbols specific desc" in repo_param.get("description", "")

        tree_tool = next((t for t in tools if t.name == "get_file_tree"), None)
        assert tree_tool is not None
        repo_param = tree_tool.inputSchema.get("properties", {}).get("repo", {})
        assert "Shared description" in repo_param.get("description", "")
        assert "search_symbols specific" not in repo_param.get("description", "")
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_descriptions_config_overrides_tool_descriptions(monkeypatch):
    """Config descriptions should override tool descriptions."""
    from jcodemunch_mcp import config as config_module

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["descriptions"] = {
            "search_symbols": {
                "_tool": "Custom search_symbols description",
                "repo": "Custom repo description"
            },
            "_shared": {
                "repo": "Shared custom repo desc"
            }
        }
        config_module._GLOBAL_CONFIG["disabled_tools"] = []

        tools = await list_tools()
        search_symbols = next((t for t in tools if t.name == "search_symbols"), None)
        assert search_symbols is not None
        assert search_symbols.description == "Custom search_symbols description"

        # Param description should also be overridden
        repo_param = search_symbols.inputSchema.get("properties", {}).get("repo", {})
        assert repo_param.get("description") == "Custom repo description"
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_no_descriptions_config_keeps_original(monkeypatch):
    """When descriptions config is absent, original tool descriptions are used."""
    from jcodemunch_mcp import config as config_module

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["descriptions"] = {}
        config_module._GLOBAL_CONFIG["disabled_tools"] = []

        tools = await list_tools()
        search_symbols = next((t for t in tools if t.name == "search_symbols"), None)
        assert search_symbols is not None

        # Should keep original description (starts with "Search for")
        assert search_symbols.description.startswith("Search for")
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_meta_fields_empty_list_removes_meta_envelope():
    """meta_fields=[] strips the _meta key from the response."""
    from jcodemunch_mcp import config as config_module

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["meta_fields"] = []
        with patch("jcodemunch_mcp.tools.list_repos.list_repos", return_value={"repos": []}):
            result = await call_tool("list_repos", {})
        payload = json.loads(result[0].text)
        assert "_meta" not in payload
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_sql_removed_auto_disables_search_columns(monkeypatch):
    """search_columns should be auto-disabled when SQL not in languages."""
    from jcodemunch_mcp import config as config_module

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["languages"] = ["python", "javascript"]
        config_module._GLOBAL_CONFIG["disabled_tools"] = []  # Explicitly empty

        tools = await list_tools()
        tool_names = [t.name for t in tools]

        # search_columns should be auto-disabled
        assert "search_columns" not in tool_names
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_sql_enabled_keeps_search_columns(monkeypatch):
    """search_columns should stay enabled when SQL is in languages."""
    from jcodemunch_mcp import config as config_module

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["languages"] = ["python", "sql"]
        config_module._GLOBAL_CONFIG["disabled_tools"] = []

        tools = await list_tools()
        tool_names = [t.name for t in tools]

        assert "search_columns" in tool_names
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_language_enum_reflects_config_limited(monkeypatch):
    """Language enum should only include configured languages."""
    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["languages"] = ["python", "javascript"]
        config_module._GLOBAL_CONFIG["disabled_tools"] = []

        tools = await list_tools()
        search_symbols_tool = next((t for t in tools if t.name == "search_symbols"), None)
        assert search_symbols_tool is not None

        lang_param = search_symbols_tool.inputSchema.get("properties", {}).get("language", {})
        enum_values = lang_param.get("enum", [])

        assert "python" in enum_values
        assert "javascript" in enum_values
        assert "sql" not in enum_values
        assert "rust" not in enum_values
        # Should match exactly the configured languages
        assert set(enum_values) == {"python", "javascript"}
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_language_enum_all_languages_when_config_none(monkeypatch):
    """When languages config is None, enum includes all registry languages."""
    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["languages"] = None
        config_module._GLOBAL_CONFIG["disabled_tools"] = []

        tools = await list_tools()
        search_symbols_tool = next((t for t in tools if t.name == "search_symbols"), None)
        lang_param = search_symbols_tool.inputSchema.get("properties", {}).get("language", {})
        enum_values = lang_param.get("enum", [])

        for lang in LANGUAGE_REGISTRY.keys():
            assert lang in enum_values, f"{lang} missing from enum"
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_disabled_tools_filtered_from_schema(monkeypatch):
    """Should remove disabled tools from list_tools output."""
    from jcodemunch_mcp import config as config_module

    # Save and clear existing config
    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["disabled_tools"] = ["index_repo", "search_columns"]

        tools = await list_tools()
        tool_names = [t.name for t in tools]

        assert "index_repo" not in tool_names
        assert "search_columns" not in tool_names
        assert "get_file_tree" in tool_names  # Not disabled
        # 52 total tools - 2 explicitly disabled = 50
        assert len(tools) == 50
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_disabled_tools_empty_all_tools_present(monkeypatch):
    """When disabled_tools is empty, all 52 tools are present."""
    from jcodemunch_mcp import config as config_module

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["disabled_tools"] = []

        tools = await list_tools()
        assert len(tools) == 52
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_meta_fields_null_keeps_meta_envelope():
    """meta_fields=null passes through tool-native _meta unchanged."""
    from jcodemunch_mcp import config as config_module

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["meta_fields"] = None
        with patch("jcodemunch_mcp.tools.list_repos.list_repos", return_value={"repos": [], "_meta": {"timing_ms": 1.0}}):
            result = await call_tool("list_repos", {})
        payload = json.loads(result[0].text)
        assert "_meta" in payload
        assert payload["_meta"]["timing_ms"] == 1.0
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_meta_fields_empty_list_removes_meta():
    """meta_fields=[] removes _meta entirely (maximum token savings)."""
    from jcodemunch_mcp import config as config_module

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["meta_fields"] = []
        with patch("jcodemunch_mcp.tools.list_repos.list_repos", return_value={"repos": [], "_meta": {"timing_ms": 5.0}}):
            result = await call_tool("list_repos", {})
        payload = json.loads(result[0].text)
        assert "_meta" not in payload
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_list_tools_no_suppress_meta_param():
    """No tool schema exposes suppress_meta (replaced by meta_fields config)."""
    tools = await list_tools()
    for tool in tools:
        props = (tool.inputSchema or {}).get("properties", {})
        assert "suppress_meta" not in props, f"{tool.name} should not have suppress_meta"


@pytest.mark.asyncio
async def test_sql_language_gating_removes_search_columns(monkeypatch):
    """Removing 'sql' from languages auto-disables search_columns tool."""
    from jcodemunch_mcp import config as config_module

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        # Enable only python and javascript — sql is NOT in the list
        config_module._GLOBAL_CONFIG["languages"] = ["python", "javascript"]
        config_module._GLOBAL_CONFIG["disabled_tools"] = []

        tools = await list_tools()
        tool_names = [t.name for t in tools]

        # search_columns must be absent when sql is not in languages
        assert "search_columns" not in tool_names
        # Other tools should remain
        assert "search_symbols" in tool_names
        assert "get_file_tree" in tool_names
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_sql_in_languages_keeps_search_columns(monkeypatch):
    """When sql IS in languages, search_columns remains in the schema."""
    from jcodemunch_mcp import config as config_module

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["languages"] = ["python", "sql"]
        config_module._GLOBAL_CONFIG["disabled_tools"] = []

        tools = await list_tools()
        tool_names = [t.name for t in tools]

        # search_columns must be present when sql is in languages
        assert "search_columns" in tool_names
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


# ── Description Override Empty String Tests (B1, B2) ──────────────────────────────────


@pytest.mark.asyncio
async def test_descriptions_empty_string_tool_clears_description():
    """Empty string _tool clears the tool description (B1)."""
    from jcodemunch_mcp import config as config_module

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        # Set _tool to empty string — should clear the description
        config_module._GLOBAL_CONFIG["descriptions"] = {
            "search_symbols": {"_tool": ""},
        }
        config_module._GLOBAL_CONFIG["disabled_tools"] = []

        tools = await list_tools()
        search_symbols = next((t for t in tools if t.name == "search_symbols"), None)
        assert search_symbols is not None
        # Empty string means "use hardcoded minimal base only"
        assert search_symbols.description == ""
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_descriptions_empty_string_param_clears_description():
    """Empty string param description clears the param description (B2)."""
    from jcodemunch_mcp import config as config_module

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        # Set param to empty string via _shared — should clear repo param description
        config_module._GLOBAL_CONFIG["descriptions"] = {
            "_shared": {"repo": ""},
        }
        config_module._GLOBAL_CONFIG["disabled_tools"] = []

        tools = await list_tools()
        search_symbols = next((t for t in tools if t.name == "search_symbols"), None)
        assert search_symbols is not None

        # repo param description should be cleared to empty string
        repo_param = search_symbols.inputSchema.get("properties", {}).get("repo", {})
        assert repo_param.get("description") == ""
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_descriptions_flat_string_overrides_tool_description():
    """Flat string format 'tool_name': 'description' overrides tool description."""
    from jcodemunch_mcp import config as config_module

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["descriptions"] = {
            "search_symbols": "Find symbols in this Python project",
        }
        config_module._GLOBAL_CONFIG["disabled_tools"] = []

        tools = await list_tools()
        search_symbols = next(t for t in tools if t.name == "search_symbols")
        assert search_symbols.description == "Find symbols in this Python project"

        # Param descriptions should be unchanged (flat format doesn't touch params)
        repo_param = search_symbols.inputSchema.get("properties", {}).get("repo", {})
        assert repo_param.get("description")  # should still have original description
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


# ── Meta Fields Partial List Test (E1) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_meta_fields_partial_list_preserves_tool_fields():
    """Partial meta_fields list preserves tool-generated fields like timing_ms (E1)."""
    import jcodemunch_mcp.tools.list_repos as list_repos_module
    import jcodemunch_mcp.server as server_module
    from jcodemunch_mcp import config as config_module

    orig_config = config_module._GLOBAL_CONFIG.copy()
    orig_list_repos = list_repos_module.list_repos

    def fake_list_repos(storage_path=None):
        return {"repos": [], "_meta": {
            "timing_ms": 12.5,
            "tokens_saved": 1000,
            "candidates_scored": 50,
        }}

    try:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG["meta_fields"] = ["timing_ms"]
        config_module._GLOBAL_CONFIG["disabled_tools"] = []

        # Patch at the tool module level (tools are now lazily imported in dispatch)
        list_repos_module.list_repos = fake_list_repos
        # Clear the tool schemas cache so call_tool picks up the patched function
        server_module._TOOL_SCHEMAS = None

        result = await call_tool("list_repos", {})

        payload = json.loads(result[0].text)
        assert "_meta" in payload
        # timing_ms should be preserved
        assert payload["_meta"]["timing_ms"] == 12.5
        # tokens_saved should NOT be in _meta (not in partial list)
        assert "tokens_saved" not in payload["_meta"]
        # candidates_scored should NOT be in _meta (not in partial list)
        assert "candidates_scored" not in payload["_meta"]
    finally:
        list_repos_module.list_repos = orig_list_repos
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


# ── Project-Level Tool Disabling Test (M2) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_project_tool_disabled_rejected_in_call_tool():
    """Project-level disabled_tools rejects the tool at call_tool with an error (M2)."""
    from jcodemunch_mcp import config as config_module

    orig_global = config_module._GLOBAL_CONFIG.copy()
    orig_project = config_module._PROJECT_CONFIGS.copy()
    config_module._GLOBAL_CONFIG.clear()
    config_module._PROJECT_CONFIGS.clear()

    try:
        # Global config: tool is NOT disabled (schema includes it)
        config_module._GLOBAL_CONFIG["disabled_tools"] = []
        config_module._GLOBAL_CONFIG["meta_fields"] = None

        # Project config: index_folder IS disabled
        project_root = "/fake/project"
        config_module._PROJECT_CONFIGS[project_root] = {
            **config_module._GLOBAL_CONFIG,
            "disabled_tools": ["index_folder"],
        }

        # Attempting to call index_folder for the project should be rejected
        result = await call_tool("index_folder", {
            "path": "/fake/project/src",
            "repo": project_root,
        })

        payload = json.loads(result[0].text)
        assert "error" in payload
        assert "index_folder" in payload["error"]
        assert "disabled" in payload["error"].lower()
        assert "project" in payload["error"].lower()
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_global)
        config_module._PROJECT_CONFIGS.clear()
        config_module._PROJECT_CONFIGS.update(orig_project)


# --------------------------------------------------------------------------- #
# Tool profiles                                                                #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_tool_profile_core():
    """Core profile should only expose ~16 essential tools."""
    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp.server import _TOOL_TIER_CORE
    from copy import deepcopy

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()
    config_module._GLOBAL_CONFIG.update(deepcopy(config_module.DEFAULTS))
    config_module._GLOBAL_CONFIG["tool_profile"] = "core"
    config_module._GLOBAL_CONFIG["disabled_tools"] = []

    try:
        tools = await list_tools()
        names = {t.name for t in tools}
        assert names == _TOOL_TIER_CORE
        # Core must include the essentials
        for essential in ("search_symbols", "get_symbol_source", "list_repos",
                          "get_file_tree", "index_folder"):
            assert essential in names, f"{essential} missing from core profile"
        # Core must NOT include advanced tools
        for excluded in ("plan_refactoring", "get_hotspots", "audit_agent_config",
                         "get_session_stats", "plan_turn"):
            assert excluded not in names, f"{excluded} should not be in core profile"
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_tool_profile_standard():
    """Standard profile should include core + analytics but not refactoring/session."""
    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp.server import _TOOL_TIER_STANDARD
    from copy import deepcopy

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()
    config_module._GLOBAL_CONFIG.update(deepcopy(config_module.DEFAULTS))
    config_module._GLOBAL_CONFIG["tool_profile"] = "standard"
    config_module._GLOBAL_CONFIG["disabled_tools"] = []

    try:
        tools = await list_tools()
        names = {t.name for t in tools}
        assert names == _TOOL_TIER_STANDARD
        # Standard includes analytics
        assert "get_hotspots" in names
        assert "get_blast_radius" in names
        # Standard excludes power-user tools
        assert "plan_refactoring" not in names
        assert "get_session_stats" not in names
        assert "audit_agent_config" not in names
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_tool_profile_full_is_default():
    """Full profile (default) should expose all tools minus disabled."""
    from jcodemunch_mcp import config as config_module
    from copy import deepcopy

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()
    config_module._GLOBAL_CONFIG.update(deepcopy(config_module.DEFAULTS))

    try:
        tools = await list_tools()
        names = {t.name for t in tools}
        # Full profile includes everything except default-disabled test_summarizer
        assert "plan_refactoring" in names
        assert "get_session_stats" in names
        assert "audit_agent_config" in names
        assert "test_summarizer" not in names  # disabled by default, not by profile
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_tool_profile_combined_with_disabled_tools():
    """Profile + disabled_tools should stack: profile filters first, then disabled."""
    from jcodemunch_mcp import config as config_module
    from copy import deepcopy

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()
    config_module._GLOBAL_CONFIG.update(deepcopy(config_module.DEFAULTS))
    config_module._GLOBAL_CONFIG["tool_profile"] = "core"
    config_module._GLOBAL_CONFIG["disabled_tools"] = ["search_text"]

    try:
        tools = await list_tools()
        names = {t.name for t in tools}
        assert "search_text" not in names
        assert "search_symbols" in names
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


# --------------------------------------------------------------------------- #
# Compact schemas                                                              #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_compact_schemas_strips_advanced_params():
    """compact_schemas should remove advanced params from search_symbols schema."""
    from jcodemunch_mcp import config as config_module
    from copy import deepcopy

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()
    config_module._GLOBAL_CONFIG.update(deepcopy(config_module.DEFAULTS))
    config_module._GLOBAL_CONFIG["compact_schemas"] = True
    config_module._GLOBAL_CONFIG["disabled_tools"] = []

    try:
        tools = await list_tools()
        search = next(t for t in tools if t.name == "search_symbols")
        props = search.inputSchema["properties"]
        # Core params still present
        assert "repo" in props
        assert "query" in props
        assert "kind" in props
        assert "max_results" in props
        # Advanced params stripped
        assert "debug" not in props
        assert "fusion" not in props
        assert "semantic" not in props
        assert "semantic_only" not in props
        assert "fuzzy" not in props
        assert "fuzzy_threshold" not in props
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_compact_schemas_off_preserves_all_params():
    """When compact_schemas is off (default), all params remain."""
    from jcodemunch_mcp import config as config_module
    from copy import deepcopy

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()
    config_module._GLOBAL_CONFIG.update(deepcopy(config_module.DEFAULTS))
    config_module._GLOBAL_CONFIG["disabled_tools"] = []

    try:
        tools = await list_tools()
        search = next(t for t in tools if t.name == "search_symbols")
        props = search.inputSchema["properties"]
        assert "debug" in props
        assert "fusion" in props
        assert "semantic" in props
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)
