"""Tests for JSONC config parsing."""

import json
import tempfile
from pathlib import Path

import pytest

from tests import _platform_path, _platform_path_str
from jcodemunch_mcp.config import _strip_jsonc


class TestJSONCParser:
    """Test JSONC comment stripping."""

    @pytest.mark.parametrize("text,id", [
        ('{"key": "value" // this is a comment\n}', "jsonc_line_comment"),
        ('{"key": "value"} // comment', "jsonc_line_comment_no_trailing"),
        ('{"key" /* comment */: "value"}', "jsonc_block_comment"),
        ('{"a": 1, "b": 2,}', "jsonc_trailing_comma_object"),
        ('{"a": {"b": 1,}, "c": 2,}', "jsonc_trailing_comma_nested"),
        ('{"arr": [1, 2, 3,]}', "jsonc_trailing_comma_array"),
        ('{\n  "key": "value", // comment\n}', "jsonc_trailing_comma_with_comment"),
        ('{"key": "value", // comment\n}', "jsonc_comment_before_closing_brace"),
        ('{"a": {"b": {"c": 1,},}, "d": [{"e": 2,},],}', "jsonc_multiple_trailing_commas"),
    ], ids=lambda x: x[1] if isinstance(x, tuple) else "custom")
    def test_strips_comments_and_trailing_commas(self, text, id):
        """Should strip comments and trailing commas, producing valid JSON."""
        result = _strip_jsonc(text)
        json.loads(result)  # Must be valid JSON

    @pytest.mark.parametrize("text,expected", [
        ('{"url": "http://example.com", "note": "use /* here*/"}',
         {"url": "http://example.com", "note": "use /* here*/"}),
        ('{"url": "http://example.com", "regex": "/*.*?*/"}',
         {"url": "http://example.com", "regex": "/*.*?*/"}),
    ], ids=["preserves_block_comment_in_string", "preserves_comment_chars_in_string"])
    def test_preserves_strings_with_comment_chars(self, text, expected):
        """Should not strip // or /* inside quoted strings."""
        result = _strip_jsonc(text)
        json.loads(result)  # Must be valid JSON
        assert json.loads(result) == expected

    def test_strips_multiline_block_comments(self):
        """Should strip multiline /* */ comments."""
        text = '''{
    "key": "value" /* this is
    a multiline
    comment */
}'''
        result = _strip_jsonc(text)
        assert '"key"' in result
        assert 'this is' not in result
        json.loads(result)  # Must be valid JSON

    @pytest.mark.parametrize("text,expected", [
        (r'{"key": "value with \"quote\""}', 'value with "quote"'),
    ], ids=["escaped_quotes"])
    def test_escaped_quotes_in_strings(self, text, expected):
        """Should preserve escaped quotes inside strings."""
        result = _strip_jsonc(text)
        json.loads(result)  # Must be valid JSON
        assert json.loads(result)["key"] == expected

    def test_real_world_config(self):
        """Should parse a real-world JSONC config file."""
        text = '''
{
  // === Indexing ===
  "use_ai_summaries": true,
  "max_folder_files": 2000,
  "max_index_files": 10000,
  "staleness_days": 7,
  "max_results": 500,
  "extra_ignore_patterns": [],
  "extra_extensions": {},
  "context_providers": true,

  // === Meta Response Control ===
  "meta_fields": [
    "timing_ms",
    "powered_by",
  ],

  // === Languages ===
  "languages": ["python", "javascript", "typescript"],

  // === Disabled Tools ===
  "disabled_tools": [],

  // === Descriptions ===
  "descriptions": {
    "search_symbols": {
      "_tool": "",
      "debug": "",
    },
  },
}
'''
        result = _strip_jsonc(text)
        parsed = json.loads(result)  # Must be valid JSON
        assert parsed["use_ai_summaries"] is True
        assert parsed["max_folder_files"] == 2000
        assert "python" in parsed["languages"]
        assert parsed["disabled_tools"] == []
        assert "search_symbols" in parsed["descriptions"]


class TestConfigDefaults:
    """Test default config values."""

    @pytest.mark.parametrize("key,expected", [
        ("max_folder_files", 2000),
        ("max_index_files", 10000),
        ("languages", None),
        ("disabled_tools", ["test_summarizer"]),
        ("server_output", "adaptive"),
        ("server_output_threshold", 0.15),
    ], ids=[
        "max_folder_files",
        "max_index_files",
        "languages_none",
        "disabled_tools",
        "server_output",
        "server_output_threshold",
    ])
    def test_default_values(self, key, expected):
        """Should have correct default values."""
        from jcodemunch_mcp.config import DEFAULTS
        assert DEFAULTS[key] == expected

    @pytest.mark.parametrize("key,expected_type", [
        ("strict_timeout_ms", int),
        ("summarizer_provider", str),
        ("embed_model", str),
        ("summarizer_model", str),
        ("server_output", str),
        ("server_output_threshold", float),
    ], ids=[
        "strict_timeout_ms",
        "summarizer_provider",
        "embed_model",
        "summarizer_model",
        "server_output",
        "server_output_threshold",
    ])
    def test_default_types(self, key, expected_type):
        """Config types should match expected types."""
        from jcodemunch_mcp.config import CONFIG_TYPES
        assert CONFIG_TYPES[key] is expected_type

    def test_default_use_ai_summaries(self):
        """use_ai_summaries should default to 'auto'."""
        from jcodemunch_mcp.config import DEFAULTS, CONFIG_TYPES
        assert DEFAULTS["use_ai_summaries"] == "auto"
        assert CONFIG_TYPES["use_ai_summaries"] == (bool, str)


class TestConfigLoading:
    """Test config file loading."""

    def test_auto_creates_default_config_if_missing(self, tmp_path):
        """load_config() should create default config.jsonc if it doesn't exist."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()
        storage_path = str(tmp_path)

        # No config.jsonc exists yet
        config_path = tmp_path / "config.jsonc"
        assert not config_path.exists()

        load_config(storage_path)

        # Should have created the config file
        assert config_path.exists()

        # Config should have default values
        content = config_path.read_text()
        assert "languages" in content  # Template includes languages

        # And defaults should be available
        assert get("max_folder_files") == 2000
        assert get("use_ai_summaries") == "auto"

    def test_missing_file_uses_defaults(self, monkeypatch):
        """Should use defaults when config file doesn't exist."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        # Clear any existing config
        _GLOBAL_CONFIG.clear()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            non_existent = Path(tmpdir) / "nonexistent" / "config.jsonc"
            monkeypatch.setenv("CODE_INDEX_PATH", str(Path(tmpdir) / "nonexistent"))

            load_config(str(Path(tmpdir) / "nonexistent"))

            assert get("max_folder_files") == 2000
            assert get("use_ai_summaries") == "auto"

    def test_loads_valid_config(self, monkeypatch):
        """Should load valid JSONC config."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('''{
                "max_folder_files": 5000,
                "use_ai_summaries": false
            }''')

            load_config(tmpdir)

            assert get("max_folder_files") == 5000
            assert get("use_ai_summaries") is False

    @pytest.mark.parametrize(
        "configured,expected",
        [
            ("adaptive", "adaptive"),
            ("raw", "raw"),
            ("encoded", "encoded"),
            ("auto", "adaptive"),
            ("json", "raw"),
            ("compact", "encoded"),
        ],
        ids=["adaptive", "raw", "encoded", "alias_auto", "alias_json", "alias_compact"],
    )
    def test_server_output_normalizes_aliases(self, configured, expected):
        """server_output should normalize legacy aliases to user-facing values."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text(f'{{"server_output": "{configured}"}}')
            load_config(tmpdir)
            assert get("server_output") == expected

    @pytest.mark.parametrize("value,expected", [
        ('null', None),
        ('[]', []),
        ('["timing_ms", "powered_by"]', ["timing_ms", "powered_by"]),
    ], ids=["null", "empty_list", "partial_list"])
    def test_meta_fields_config_values(self, value, expected):
        """meta_fields should handle null, empty list, and partial list values."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text(f'{{"meta_fields": {value}}}')
            load_config(tmpdir)
            assert get("meta_fields") == expected

    def test_meta_fields_absent_uses_default(self):
        """meta_fields absent from config uses default ([] = no metadata)."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": 5000}')

            load_config(tmpdir)

            # When not specified, should use DEFAULTS value ([] = no metadata)
            assert get("meta_fields") == []

    def test_type_mismatch_logs_warning_and_uses_default(self, monkeypatch, caplog):
        """Should log warning and use default on type mismatch."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG
        import logging

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{ "max_folder_files": "2000" }')  # String instead of int

            with caplog.at_level(logging.WARNING):
                load_config(tmpdir)

            # Should have logged a warning
            assert "invalid type" in caplog.text.lower()

            # Should use default
            assert get("max_folder_files") == 2000

    def test_unknown_language_logs_warning(self, monkeypatch, caplog):
        """Unknown language in config should log warning and be filtered."""
        import logging
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        caplog.set_level(logging.WARNING)
        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"languages": ["python", "pythno", "javascript"]}')

            load_config(str(tmpdir))

            assert any("pythno" in record.message for record in caplog.records)

            langs = get("languages")
            assert "python" in langs
            assert "javascript" in langs
            assert "pythno" not in langs


class TestProjectConfig:
    """Test project-level config loading."""

    def test_load_all_project_configs_at_startup(self, tmp_path, monkeypatch):
        """load_all_project_configs() should load .jcodemunch.jsonc for all local repos."""
        from jcodemunch_mcp.config import (
            load_config, load_all_project_configs, get, _GLOBAL_CONFIG, _PROJECT_CONFIGS
        )
        import unittest.mock

        # Create two project roots with different configs
        project_a = tmp_path / "project_a"
        project_b = tmp_path / "project_b"
        project_a.mkdir()
        project_b.mkdir()

        (project_a / ".jcodemunch.jsonc").write_text('{"max_folder_files": 1000}')
        (project_b / ".jcodemunch.jsonc").write_text('{"max_folder_files": 3000}')

        # Mock list_repos to return our test repos
        mock_repos = [
            {"repo": "local/project-a-abc123", "source_root": str(project_a)},
            {"repo": "local/project-b-def456", "source_root": str(project_b)},
            {"repo": "github/owner/repo", "source_root": ""},  # Remote repo, no source_root
        ]

        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()

        load_config(str(tmp_path))

        with unittest.mock.patch(
            "jcodemunch_mcp.config._list_repos_for_config", return_value=mock_repos
        ):
            load_all_project_configs()

        # Project A should have max_folder_files=1000
        assert get("max_folder_files", repo=str(project_a.resolve())) == 1000
        # Project B should have max_folder_files=3000
        assert get("max_folder_files", repo=str(project_b.resolve())) == 3000
        # Remote repo should use global default
        assert get("max_folder_files") == 2000

    def test_project_config_merges_over_global(self):
        """Should merge project config over global config."""
        from jcodemunch_mcp.config import (
            load_config, load_project_config, get,
            _GLOBAL_CONFIG, _PROJECT_CONFIGS
        )

        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Set up global config
            global_config = Path(tmpdir) / "global" / "config.jsonc"
            global_config.parent.mkdir()
            global_config.write_text('{"max_folder_files": 2000, "use_ai_summaries": true}')

            load_config(str(global_config.parent))

            # Set up project config
            project_root = Path(tmpdir) / "project"
            project_root.mkdir()
            project_config = project_root / ".jcodemunch.jsonc"
            project_config.write_text('{"max_folder_files": 5000}')

            load_project_config(str(project_root))

            # Project value should override
            repo_key = str(project_root.resolve())
            assert get("max_folder_files", repo=repo_key) == 5000
            # Non-overridden values should come from global
            assert get("use_ai_summaries", repo=repo_key) is True  # set explicitly in global config


class TestConfigGetters:
    """Test config getter functions."""

    def test_is_tool_disabled(self):
        """Should return True if tool is in disabled_tools."""
        from jcodemunch_mcp.config import (
            load_config, is_tool_disabled, _GLOBAL_CONFIG
        )

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"disabled_tools": ["index_repo", "search_columns"]}')

            load_config(tmpdir)

            assert is_tool_disabled("index_repo") is True
            assert is_tool_disabled("search_columns") is True
            assert is_tool_disabled("get_file_tree") is False

    def test_is_language_enabled_all_enabled(self):
        """Should return True for all languages when languages is None."""
        from jcodemunch_mcp.config import (
            load_config, is_language_enabled, _GLOBAL_CONFIG, DEFAULTS
        )

        _GLOBAL_CONFIG.clear()
        _GLOBAL_CONFIG.update(DEFAULTS)  # languages = None

        assert is_language_enabled("python") is True
        assert is_language_enabled("sql") is True

    def test_is_language_enabled_filtered(self):
        """Should return False for disabled languages."""
        from jcodemunch_mcp.config import load_config, is_language_enabled, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"languages": ["python", "javascript"]}')

            load_config(tmpdir)

            assert is_language_enabled("python") is True
            assert is_language_enabled("javascript") is True
            assert is_language_enabled("sql") is False


class TestTemplateGeneration:
    """Test config template generation."""

    def test_generate_template_returns_valid_jsonc(self):
        """Should generate valid JSONC template."""
        from jcodemunch_mcp.config import generate_template

        template = generate_template()

        # Should be parseable after stripping comments
        from jcodemunch_mcp.config import _strip_jsonc
        stripped = _strip_jsonc(template)
        parsed = json.loads(stripped)

        assert "languages" in parsed
        assert "disabled_tools" in parsed
        assert "meta_fields" in parsed

    def test_template_languages_synced_from_registry(self):
        """Should include all languages from LANGUAGE_REGISTRY as active entries."""
        from jcodemunch_mcp.config import generate_template
        from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY
        from jcodemunch_mcp.config import _strip_jsonc
        import json

        template = generate_template()
        parsed = json.loads(_strip_jsonc(template))

        # All registry languages should be present and active (not commented out)
        for lang in LANGUAGE_REGISTRY.keys():
            assert lang in parsed["languages"], f"Language '{lang}' not found in parsed template"

    def test_template_all_tools_matches_canonical(self):
        """all_tools in generate_template must include every canonical tool."""
        from jcodemunch_mcp.config import generate_template
        from jcodemunch_mcp.server import _CANONICAL_TOOL_NAMES

        template = generate_template()

        for tool in _CANONICAL_TOOL_NAMES:
            assert tool in template, f"Tool '{tool}' missing from config template"

    def test_template_documents_server_output_controls(self):
        """Template should document server_output and threshold keys."""
        from jcodemunch_mcp.config import generate_template

        template = generate_template()
        assert '"server_output": "adaptive"' in template
        assert '"server_output_threshold": 0.15' in template


class TestGetDescriptions:
    """Test get_descriptions() function."""

    def test_returns_descriptions_dict(self):
        """Should return descriptions dict from config."""
        from jcodemunch_mcp.config import load_config, get_descriptions, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('''{
                "descriptions": {
                    "search_symbols": {"_tool": "custom"},
                    "_shared": {"repo": "shared desc"}
                }
            }''')

            load_config(tmpdir)

            result = get_descriptions()
            assert isinstance(result, dict)
            assert "search_symbols" in result
            assert "_shared" in result

    def test_returns_empty_dict_when_absent(self):
        """Should return empty dict when descriptions key absent."""
        from jcodemunch_mcp.config import load_config, get_descriptions, _GLOBAL_CONFIG, DEFAULTS

        _GLOBAL_CONFIG.clear()
        _GLOBAL_CONFIG.update(DEFAULTS)  # descriptions = {}

        result = get_descriptions()
        assert result == {}


class TestEnvVarFallback:
    """Test deprecated env var fallback with warnings."""

    def test_env_var_used_when_config_key_absent(self, monkeypatch, caplog):
        """Should use env var value when config key not set."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG, _DEPRECATED_ENV_VARS_LOGGED
        import logging

        _GLOBAL_CONFIG.clear()
        _DEPRECATED_ENV_VARS_LOGGED.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Config without max_folder_files
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"use_ai_summaries": false}')

            # Env var set for max_folder_files
            monkeypatch.setenv("JCODEMUNCH_MAX_FOLDER_FILES", "5000")

            with caplog.at_level(logging.WARNING):
                load_config(tmpdir)

            # Should use env var value
            assert get("max_folder_files") == 5000

    def test_warning_logged_once_per_deprecated_var(self, monkeypatch, caplog):
        """Should log one warning per deprecated env var found."""
        from jcodemunch_mcp.config import load_config, _GLOBAL_CONFIG, _DEPRECATED_ENV_VARS_LOGGED
        import logging

        _GLOBAL_CONFIG.clear()
        _DEPRECATED_ENV_VARS_LOGGED.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{}')  # Empty config

            monkeypatch.setenv("JCODEMUNCH_MAX_FOLDER_FILES", "5000")
            monkeypatch.setenv("JCODEMUNCH_MAX_INDEX_FILES", "15000")

            with caplog.at_level(logging.WARNING):
                load_config(tmpdir)

            # Should have warnings (one per env var)
            warning_count = sum(1 for rec in caplog.records if rec.levelname == "WARNING")
            assert warning_count >= 2

            # Each warning should mention v2.0 removal
            for rec in caplog.records:
                if "deprecated" in rec.message.lower():
                    assert "v2.0" in rec.message

    def test_no_warning_when_config_key_present(self, monkeypatch, caplog):
        """Should NOT log warning when config key is present."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG, _DEPRECATED_ENV_VARS_LOGGED
        import logging

        _GLOBAL_CONFIG.clear()
        _DEPRECATED_ENV_VARS_LOGGED.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": 3000}')

            # Env var set but should be ignored (config takes precedence)
            monkeypatch.setenv("JCODEMUNCH_MAX_FOLDER_FILES", "5000")

            with caplog.at_level(logging.WARNING):
                load_config(tmpdir)

            # Should NOT log deprecation warning (config key present)
            assert "deprecated" not in caplog.text.lower()

            # Config value should be used, not env var
            assert get("max_folder_files") == 3000

    def test_trusted_folders_env_var_used_when_config_key_absent(
        self, monkeypatch, caplog
    ):
        """Should use trusted_folders env var fallback when config key not set."""
        from jcodemunch_mcp.config import (
            load_config,
            get,
            _GLOBAL_CONFIG,
            _DEPRECATED_ENV_VARS_LOGGED,
        )
        import logging

        _GLOBAL_CONFIG.clear()
        _DEPRECATED_ENV_VARS_LOGGED.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"use_ai_summaries": false}')

            monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", "/work,/mounted/src")

            with caplog.at_level(logging.WARNING):
                load_config(tmpdir)

            assert get("trusted_folders") == ["/work", "/mounted/src"]

    def test_trusted_folders_config_wins_over_env_var(self, monkeypatch, caplog):
        """Explicit trusted_folders config should take precedence over env fallback."""
        from jcodemunch_mcp.config import (
            load_config,
            get,
            _GLOBAL_CONFIG,
            _DEPRECATED_ENV_VARS_LOGGED,
        )
        import logging

        _GLOBAL_CONFIG.clear()
        _DEPRECATED_ENV_VARS_LOGGED.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_work = _platform_path_str("/config/work")
            env_work = _platform_path_str("/env/work")
            config_path.write_text(f'{{"trusted_folders": ["{config_work}"]}}')

            monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", env_work)

            with caplog.at_level(logging.WARNING):
                load_config(tmpdir)

            assert get("trusted_folders") == [_platform_path("/config/work").resolve()]
            assert not any(
                "JCODEMUNCH_TRUSTED_FOLDERS" in rec.message for rec in caplog.records
            )

    @pytest.mark.parametrize(
        "env_key,env_value,expected_key,expected_value",
        [
            ("JCODEMUNCH_DEFAULT_FORMAT", "compact", "server_output", "encoded"),
            ("JCODEMUNCH_ENCODING_THRESHOLD", "0.25", "server_output_threshold", 0.25),
        ],
        ids=["default_format_alias", "encoding_threshold"],
    )
    def test_legacy_encoding_env_vars_fallback(
        self, monkeypatch, caplog, env_key, env_value, expected_key, expected_value
    ):
        """Legacy encoding env vars should map through config fallback."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG, _DEPRECATED_ENV_VARS_LOGGED
        import logging

        _GLOBAL_CONFIG.clear()
        _DEPRECATED_ENV_VARS_LOGGED.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text("{}")

            monkeypatch.setenv(env_key, env_value)

            with caplog.at_level(logging.WARNING):
                load_config(tmpdir)

            assert get(expected_key) == expected_value
            assert any(env_key in rec.message for rec in caplog.records)


class TestTrustedFoldersConfig:
    @pytest.mark.parametrize("text,check", [
        ('{"trusted_folders": ["work"]}', "trusted_folders entry 'work' must be an absolute path"),
        ('{"trusted_folders": [123]}', "Config key 'trusted_folders' has invalid type"),
    ], ids=["relative_entry", "non_string_entry"])
    def test_validate_trusted_folders_rejects(self, tmp_path, text, check):
        """validate_config should reject invalid trusted_folders entries."""
        from jcodemunch_mcp.config import validate_config

        config_path = tmp_path / "config.jsonc"
        config_path.write_text(text)

        issues = validate_config(str(config_path))
        assert any(check in issue for issue in issues)

    @pytest.mark.parametrize("input_folders,expected_count", [
        (["/work"], 1),
    ], ids=["valid_single"])
    def test_load_config_trusted_folders(self, tmp_path, input_folders, expected_count):
        """load_config should keep valid trusted_folders."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        config_path = tmp_path / "config.jsonc"
        work_path = _platform_path_str("/work")
        config_path.write_text(f'{{"trusted_folders": ["{work_path}"]}}')

        load_config(str(tmp_path))

        result = get("trusted_folders")
        assert len(result) == expected_count
        assert _platform_path("/work").expanduser() in result

    @pytest.mark.parametrize("folders_json,expected", [
        ('["./work"]', lambda root: [(root / "work").resolve()]),
        ('["./../../outside"]', lambda root: []),  # Escapes project
        ('["./subdir/../work"]', lambda root: [(root / "work").resolve()]),  # Normalized but stays in project
    ], ids=["expand_dot_slash", "reject_escape", "allow_normalized"])
    def test_project_config_dot_slash_resolution(self, tmp_path, folders_json, expected):
        """Project config './' entries should expand from project root with escape detection."""
        from jcodemunch_mcp.config import (
            load_config,
            load_project_config,
            get,
            _GLOBAL_CONFIG,
            _PROJECT_CONFIGS,
            _PROJECT_CONFIG_HASHES,
        )

        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()
        _PROJECT_CONFIG_HASHES.clear()

        global_config = tmp_path / "config.jsonc"
        global_config.write_text("{}")
        load_config(str(tmp_path))

        project_root = tmp_path / "project"
        project_root.mkdir()
        project_config = project_root / ".jcodemunch.jsonc"
        project_config.write_text(f'{{"trusted_folders": {folders_json}}}')

        load_project_config(str(project_root))

        repo_key = str(project_root.resolve())
        assert get("trusted_folders", repo=repo_key) == expected(project_root)

    def test_load_config_raises_for_relative_trusted_folders(self, tmp_path):
        """Non-rooted trusted_folders entries should raise during config load."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG, DEFAULTS

        _GLOBAL_CONFIG.clear()

        config_path = tmp_path / "config.jsonc"
        config_path.write_text('{"trusted_folders": ["relative/path"]}')

        load_config(str(tmp_path))

        assert get("trusted_folders") == DEFAULTS["trusted_folders"]

    @pytest.mark.parametrize("folders_json,expected", [
        ('["."]', lambda root: [root.resolve()]),
        ('["work"]', lambda root: [(root / "work").resolve()]),
        ('["../outside"]', lambda root: []),  # Escapes without ./
    ], ids=["dot_resolves", "implicit_relative", "implicit_escape"])
    def test_project_config_path_resolution(self, tmp_path, folders_json, expected):
        """Project config paths should resolve relative to project root."""
        from jcodemunch_mcp.config import (
            load_config,
            load_project_config,
            get,
            _GLOBAL_CONFIG,
            _PROJECT_CONFIGS,
            _PROJECT_CONFIG_HASHES,
        )

        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()
        _PROJECT_CONFIG_HASHES.clear()

        global_config = tmp_path / "config.jsonc"
        global_config.write_text("{}")
        load_config(str(tmp_path))

        project_root = tmp_path / "project"
        project_root.mkdir()
        project_config = project_root / ".jcodemunch.jsonc"
        project_config.write_text(f'{{"trusted_folders": {folders_json}}}')

        load_project_config(str(project_root))

        repo_key = str(project_root.resolve())
        assert get("trusted_folders", repo=repo_key) == expected(project_root)

    def test_project_config_implicit_relative_escape_rejected(self, tmp_path):
        """Project config '../outside' (without ./ prefix) should be rejected."""
        from jcodemunch_mcp.config import (
            load_config,
            load_project_config,
            get,
            _GLOBAL_CONFIG,
            _PROJECT_CONFIGS,
            _PROJECT_CONFIG_HASHES,
        )

        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()
        _PROJECT_CONFIG_HASHES.clear()

        global_config = tmp_path / "config.jsonc"
        global_config.write_text("{}")
        load_config(str(tmp_path))

        project_root = tmp_path / "project"
        project_root.mkdir()
        project_config = project_root / ".jcodemunch.jsonc"
        project_config.write_text('{"trusted_folders": ["../outside"]}')

        load_project_config(str(project_root))

        repo_key = str(project_root.resolve())
        assert get("trusted_folders", repo=repo_key) == []

    def test_project_config_multiple_mixed_entries(self, tmp_path):
        """Project config can have multiple entries of different types."""
        from jcodemunch_mcp.config import (
            load_config,
            load_project_config,
            get,
            _GLOBAL_CONFIG,
            _PROJECT_CONFIGS,
            _PROJECT_CONFIG_HASHES,
        )

        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()
        _PROJECT_CONFIG_HASHES.clear()

        global_config = tmp_path / "config.jsonc"
        global_config.write_text("{}")
        load_config(str(tmp_path))

        project_root = tmp_path / "project"
        project_root.mkdir()
        project_config = project_root / ".jcodemunch.jsonc"
        work_path = _platform_path_str("/work")
        project_config.write_text(
            f'{{"trusted_folders": [".", "src", "{work_path}", "./lib"]}}'
        )

        load_project_config(str(project_root))

        repo_key = str(project_root.resolve())
        result = get("trusted_folders", repo=repo_key)
        assert project_root.resolve() in result
        assert (project_root / "src").resolve() in result
        assert _platform_path("/work").resolve() in result
        assert (project_root / "lib").resolve() in result
        assert len(result) == 4

    def test_project_config_overrides_global_trusted_folders(self, tmp_path):
        """Project trusted_folders should override global config, not merge."""
        from jcodemunch_mcp.config import (
            load_config,
            load_project_config,
            get,
            _GLOBAL_CONFIG,
            _PROJECT_CONFIGS,
            _PROJECT_CONFIG_HASHES,
        )

        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()
        _PROJECT_CONFIG_HASHES.clear()

        global_config = tmp_path / "config.jsonc"
        global_trusted = _platform_path_str("/global/trusted")
        global_config.write_text(f'{{"trusted_folders": ["{global_trusted}"]}}')
        load_config(str(tmp_path))

        project_root = tmp_path / "project"
        project_root.mkdir()
        project_config = project_root / ".jcodemunch.jsonc"
        project_config.write_text('{"trusted_folders": ["."]}')

        load_project_config(str(project_root))

        repo_key = str(project_root.resolve())
        # Global should still have its value
        assert get("trusted_folders") == [_platform_path("/global/trusted").resolve()]
        # Project should have only its own value
        assert get("trusted_folders", repo=repo_key) == [project_root.resolve()]

    def test_project_config_empty_list_overrides_global(self, tmp_path):
        """Empty project trusted_folders should clear the setting for that project."""
        from jcodemunch_mcp.config import (
            load_config,
            load_project_config,
            get,
            _GLOBAL_CONFIG,
            _PROJECT_CONFIGS,
            _PROJECT_CONFIG_HASHES,
        )

        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()
        _PROJECT_CONFIG_HASHES.clear()

        global_config = tmp_path / "config.jsonc"
        global_config.write_text('{"trusted_folders": ["/global/trusted"]}')
        load_config(str(tmp_path))

        project_root = tmp_path / "project"
        project_root.mkdir()
        project_config = project_root / ".jcodemunch.jsonc"
        project_config.write_text('{"trusted_folders": []}')

        load_project_config(str(project_root))

        repo_key = str(project_root.resolve())
        assert get("trusted_folders", repo=repo_key) == []

    @pytest.mark.parametrize("folders_json,expected_count", [
        (f'["{_platform_path_str("/work")}", "{_platform_path_str("/work")}", "{_platform_path_str("/work")}"]', 1),
    ], ids=["global_dedup"])
    def test_global_config_deduplicates(self, tmp_path, folders_json, expected_count):
        """Global config should deduplicate identical trusted_folders entries."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        config_path = tmp_path / "config.jsonc"
        config_path.write_text(f'{{"trusted_folders": {folders_json}}}')

        load_config(str(tmp_path))

        result = get("trusted_folders")
        assert len(result) == expected_count

    @pytest.mark.parametrize("folders_json,expected_count", [
        ('[".", "./", "work", "./work"]', 2),
        (f'["{_platform_path_str("/work")}", "{_platform_path_str("/work")}", "{_platform_path_str("/work")}"]', 1),
    ], ids=["project_equiv_dedup", "project_absolute_dedup"])
    def test_project_config_deduplicates(self, tmp_path, folders_json, expected_count):
        """Project config should deduplicate equivalent and absolute duplicate entries."""
        from jcodemunch_mcp.config import (
            load_config,
            load_project_config,
            get,
            _GLOBAL_CONFIG,
            _PROJECT_CONFIGS,
            _PROJECT_CONFIG_HASHES,
        )

        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()
        _PROJECT_CONFIG_HASHES.clear()

        global_config = tmp_path / "config.jsonc"
        global_config.write_text("{}")
        load_config(str(tmp_path))

        project_root = tmp_path / "project"
        project_root.mkdir()
        project_config = project_root / ".jcodemunch.jsonc"
        project_config.write_text(f'{{"trusted_folders": {folders_json}}}')

        load_project_config(str(project_root))

        repo_key = str(project_root.resolve())
        result = get("trusted_folders", repo=repo_key)
        assert len(result) == expected_count


# ── Config file validation ────────────────────────────────────────────────────


class TestConfigValidation:
    """Test validate_config() function in config module."""

    def test_validate_valid_config_returns_empty(self):
        """Should return no issues for a valid config."""
        from jcodemunch_mcp.config import validate_config, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": 5000}')
            issues = validate_config(str(config_path))
            assert issues == []

    @pytest.mark.parametrize("text,check", [
        ('{"max_folder_files": }', "parse"),
        ('{"max_folder_files": "not_an_int"}', lambda i: "type" in i.lower() or "invalid" in i.lower()),
        ('{"max_folder_files": 5000, "unknown_key": true}', "unknown"),
    ], ids=["invalid_json", "type_mismatch", "unknown_key"])
    def test_validate_errors_and_warnings(self, text, check):
        """Should report parse errors, type mismatches, and unknown keys."""
        from jcodemunch_mcp.config import validate_config, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text(text)
            issues = validate_config(str(config_path))
            if callable(check):
                assert any(check(i) for i in issues)
            else:
                assert any(check in i.lower() for i in issues)

    def test_validate_missing_file_returns_error(self):
        """Should report when config file is missing."""
        from jcodemunch_mcp.config import validate_config, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "nonexistent.jsonc"
            issues = validate_config(str(missing))
            assert any("not found" in i.lower() for i in issues)

    @pytest.mark.parametrize("value", [
        "true", "false", '"true"', '"false"', '"auto"', '"AUTO"',
    ], ids=["bool_true", "bool_false", "str_true", "str_false", "str_auto", "str_auto_upper"])
    def test_validate_use_ai_summaries_valid_values(self, value):
        """Valid use_ai_summaries values should pass validation."""
        from jcodemunch_mcp.config import validate_config, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text(f'{{"use_ai_summaries": {value}}}')
            issues = validate_config(str(config_path))
            assert issues == []

    @pytest.mark.parametrize("value", [
        '"maybe"', '"yes"',
    ], ids=["maybe", "yes"])
    def test_validate_use_ai_summaries_rejected_values(self, value):
        """Invalid use_ai_summaries values should be rejected."""
        from jcodemunch_mcp.config import validate_config, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text(f'{{"use_ai_summaries": {value}}}')
            issues = validate_config(str(config_path))
            assert len(issues) == 1
            assert "use_ai_summaries" in issues[0]

    def test_validate_use_ai_summaries_string_yes_rejected(self):
        """"yes" should be rejected as invalid use_ai_summaries value."""
        from jcodemunch_mcp.config import validate_config, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"use_ai_summaries": "yes"}')
            issues = validate_config(str(config_path))
            assert len(issues) == 1
            assert "use_ai_summaries" in issues[0]
            assert "'yes'" in issues[0]
            assert '"auto"' in issues[0]


class TestServerConfigCheck:
    """Test that `config --check` validates the config file."""

    def test_run_config_check_reports_config_parse_error(self, capsys, monkeypatch):
        """Should report config file parse errors in --check output."""
        from jcodemunch_mcp.config import _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": }')  # Invalid JSON

            monkeypatch.setenv("CODE_INDEX_PATH", tmpdir)

            from jcodemunch_mcp.server import _run_config
            with pytest.raises(SystemExit) as exc_info:
                _run_config(check=True)
            assert exc_info.value.code == 1

            captured = capsys.readouterr().out
            # Should mention config.jsonc and parse error
            assert "config" in captured.lower()
            assert "parse" in captured.lower()

    def test_run_config_check_reports_type_error(self, capsys, monkeypatch):
        """Should report config type errors in --check output."""
        from jcodemunch_mcp.config import _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": "wrong_type"}')

            monkeypatch.setenv("CODE_INDEX_PATH", tmpdir)

            from jcodemunch_mcp.server import _run_config
            with pytest.raises(SystemExit) as exc_info:
                _run_config(check=True)
            assert exc_info.value.code == 1

            captured = capsys.readouterr().out
            assert "max_folder_files" in captured.lower()
            assert "type" in captured.lower()

    def test_run_config_check_passes_for_valid_config(self, capsys, monkeypatch):
        """Should pass checks when config is valid."""
        from jcodemunch_mcp.config import _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": 5000}')

            monkeypatch.setenv("CODE_INDEX_PATH", tmpdir)

            # Provide a CLAUDE.md that mentions all canonical tools so the
            # drift check passes without flagging the test as an issue.
            from jcodemunch_mcp.server import _CANONICAL_TOOL_NAMES
            claude_dir = Path(tmpdir) / ".claude"
            claude_dir.mkdir()
            (claude_dir / "CLAUDE.md").write_text(
                "\n".join(_CANONICAL_TOOL_NAMES), encoding="utf-8"
            )
            monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(tmpdir)))

            from jcodemunch_mcp.server import _run_config
            _run_config(check=True)

            captured = capsys.readouterr().out
            # Should NOT mention config errors
            assert "config error" not in captured.lower()
            assert "parse error" not in captured.lower()


class TestServerConfigCheckStorageProbe:
    """Regression: issue #335 (reported by @mmashwani). `config --check`
    treated every storage-probe exception as a confirmed `storage` failure,
    so a sandboxed agent shell that simply cannot prove host writability
    (EPERM/EACCES) reported a healthy install as broken and exited 1. A
    confirmed non-writable root must still fail; a sandbox-limited probe must
    be flagged for host confirmation and NOT exit 1.
    """

    def _setup_valid_env(self, tmpdir, monkeypatch):
        """Valid config + isolated home so only the storage probe varies."""
        from jcodemunch_mcp.config import _GLOBAL_CONFIG
        _GLOBAL_CONFIG.clear()
        config_path = Path(tmpdir) / "config.jsonc"
        config_path.write_text('{"max_folder_files": 5000}')
        monkeypatch.setenv("CODE_INDEX_PATH", tmpdir)
        # Isolate home so the real ~/.claude/CLAUDE.md + settings.json don't
        # inject unrelated issues (missing CLAUDE.md only warns).
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(tmpdir)))

    def test_writable_storage_passes_and_exits_zero(self, capsys, monkeypatch):
        """A normal writable storage path prints a passing storage row, exit 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._setup_valid_env(tmpdir, monkeypatch)

            from jcodemunch_mcp.server import _run_config
            _run_config(check=True)  # no SystemExit

            captured = capsys.readouterr().out
            assert "index storage writable" in captured.lower()
            assert "host confirmation" not in captured.lower()

    def test_confirmed_non_writable_storage_still_fails(self, capsys, monkeypatch):
        """A confirmed non-writable path (non-EPERM/EACCES OSError) exits 1."""
        import errno as _errno
        with tempfile.TemporaryDirectory() as tmpdir:
            self._setup_valid_env(tmpdir, monkeypatch)

            orig_write_text = Path.write_text

            def fake_write_text(self, *a, **k):
                if self.name == ".jcm_probe":
                    raise OSError(_errno.EROFS, "Read-only file system")
                return orig_write_text(self, *a, **k)

            monkeypatch.setattr(Path, "write_text", fake_write_text)

            from jcodemunch_mcp.server import _run_config
            with pytest.raises(SystemExit) as exc_info:
                _run_config(check=True)
            assert exc_info.value.code == 1

            captured = capsys.readouterr().out
            assert "index storage not writable" in captured.lower()
            assert "host confirmation" not in captured.lower()

    def test_sandbox_eperm_is_host_confirmation_not_failure(self, capsys, monkeypatch):
        """A mocked PermissionError(EPERM) reports a host-confirmation warning
        rather than a confirmed storage failure, and does NOT exit 1."""
        import errno as _errno
        with tempfile.TemporaryDirectory() as tmpdir:
            self._setup_valid_env(tmpdir, monkeypatch)

            orig_write_text = Path.write_text

            def fake_write_text(self, *a, **k):
                if self.name == ".jcm_probe":
                    raise PermissionError(_errno.EPERM, "Operation not permitted")
                return orig_write_text(self, *a, **k)

            monkeypatch.setattr(Path, "write_text", fake_write_text)

            from jcodemunch_mcp.server import _run_config
            _run_config(check=True)  # no SystemExit

            captured = capsys.readouterr().out
            assert "host confirmation" in captured.lower()
            # Not presented as a confirmed failure.
            assert "index storage not writable" not in captured.lower()
            assert "issue(s) found" not in captured.lower()

    def test_sandbox_probe_tells_operator_to_rerun_outside_sandbox(self, capsys, monkeypatch):
        """The host-confirmation output instructs rerunning outside a sandbox
        before repairing or reporting storage drift."""
        import errno as _errno
        with tempfile.TemporaryDirectory() as tmpdir:
            self._setup_valid_env(tmpdir, monkeypatch)

            orig_write_text = Path.write_text

            def fake_write_text(self, *a, **k):
                if self.name == ".jcm_probe":
                    raise PermissionError(_errno.EACCES, "Permission denied")
                return orig_write_text(self, *a, **k)

            monkeypatch.setattr(Path, "write_text", fake_write_text)

            from jcodemunch_mcp.server import _run_config
            _run_config(check=True)

            captured = capsys.readouterr().out.lower()
            assert "sandbox" in captured or "restricted shell" in captured
            assert "rerun" in captured


class TestConfigDisplayHonorsProjectOverride:
    """Regression: jcm #300 follow-up (reported by @slazarov on issue #300
    after v1.108.14). `config --check` validated the project file but the
    displayed config values still came from _GLOBAL_CONFIG alone, so any
    project-level override was silently invisible in diagnostic output.
    """

    def test_project_override_visible_in_config_output(self, capsys, monkeypatch):
        from jcodemunch_mcp.config import _GLOBAL_CONFIG, _PROJECT_CONFIGS
        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)

            # Global config sets one value; project config overrides it.
            storage = tmp / "storage"
            storage.mkdir()
            (storage / "config.jsonc").write_text(
                '{"max_folder_files": 2000}', encoding="utf-8"
            )

            project = tmp / "project"
            project.mkdir()
            (project / ".jcodemunch.jsonc").write_text(
                '{"max_folder_files": 9999}', encoding="utf-8"
            )

            monkeypatch.setenv("CODE_INDEX_PATH", str(storage))
            monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp))
            monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: project))

            from jcodemunch_mcp.server import _run_config
            _run_config(check=False)

            captured = capsys.readouterr().out
            # Find the line for the overridden key and verify it carries the
            # project value plus the [project] source tag. Other lines mention
            # similar numbers (watch_debounce_ms defaults to 2000) so we have
            # to match the specific row.
            mff_lines = [
                line for line in captured.splitlines()
                if "max_folder_files" in line
            ]
            assert mff_lines, f"max_folder_files row missing; got: {captured}"
            row = mff_lines[0]
            assert "9999" in row, (
                f"expected project-override value 9999 in max_folder_files row, got: {row}"
            )
            assert "2000" not in row, (
                f"global value 2000 should not appear in overridden row, got: {row}"
            )
            assert "[project]" in row, (
                f"expected '[project]' source tag on overridden row, got: {row}"
            )

    def test_check_section_reports_project_config_loaded(self, capsys, monkeypatch):
        from jcodemunch_mcp.config import _GLOBAL_CONFIG, _PROJECT_CONFIGS
        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            storage = tmp / "storage"
            storage.mkdir()
            (storage / "config.jsonc").write_text("{}", encoding="utf-8")

            project = tmp / "project"
            project.mkdir()
            (project / ".jcodemunch.jsonc").write_text(
                '{"max_folder_files": 1234}', encoding="utf-8"
            )

            monkeypatch.setenv("CODE_INDEX_PATH", str(storage))
            monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp))
            monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: project))

            # Skip CLAUDE.md drift check noise by writing the canonical tool list.
            from jcodemunch_mcp.server import _CANONICAL_TOOL_NAMES
            (tmp / ".claude").mkdir()
            (tmp / ".claude" / "CLAUDE.md").write_text(
                "\n".join(_CANONICAL_TOOL_NAMES), encoding="utf-8"
            )

            from jcodemunch_mcp.server import _run_config
            _run_config(check=True)

            captured = capsys.readouterr().out
            # The Config File section now reports the project file status.
            assert ".jcodemunch.jsonc loaded from cwd" in captured, (
                f"expected project-config-loaded line in output; got: {captured}"
            )

    def test_no_project_file_keeps_global_only_behavior(self, capsys, monkeypatch):
        """When cwd has no .jcodemunch.jsonc, output is unchanged from
        pre-fix behavior — global values, no [project] tags."""
        from jcodemunch_mcp.config import _GLOBAL_CONFIG, _PROJECT_CONFIGS
        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            storage = tmp / "storage"
            storage.mkdir()
            (storage / "config.jsonc").write_text(
                '{"max_folder_files": 2000}', encoding="utf-8"
            )

            project = tmp / "project"
            project.mkdir()
            # No .jcodemunch.jsonc here.

            monkeypatch.setenv("CODE_INDEX_PATH", str(storage))
            monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp))
            monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: project))

            from jcodemunch_mcp.server import _run_config
            _run_config(check=False)

            captured = capsys.readouterr().out
            assert "2000" in captured
            assert "[project]" not in captured
            assert ".jcodemunch.jsonc loaded" not in captured


class TestConfigReportGrouping:
    """config_report emits `group` + `description` per key (for the Console's
    grouped, self-documenting config screen)."""

    def test_every_entry_has_group_and_description(self):
        from jcodemunch_mcp.config import config_report
        report = config_report()
        assert report, "config_report should not be empty"
        for entry in report:
            assert "group" in entry and isinstance(entry["group"], str) and entry["group"]
            assert "description" in entry and isinstance(entry["description"], str)

    def test_no_key_falls_into_other_or_missing_description(self):
        """Every config key must be documented in the template — a real section
        (not the 'Other' catch-all) AND a non-empty description. Regression for
        the 13 keys (watch_paths/watch_extra_ignore/watch_follow_symlinks/
        watch_idle_timeout/watch_log, runtime_ingest_enabled/org_ingest_enabled/
        runtime_ingest_max_body_bytes, license_key, cross_repo_default,
        discovery_hint, agent_selector, enrichment) that shipped template-less,
        so jcm emitted them as Other/no-description and the Console showed blank
        rows. Fails loudly if a future key is added to DEFAULTS without a
        matching template entry."""
        from jcodemunch_mcp.config import config_report
        offenders = [
            e["key"] for e in config_report()
            if e["group"] == "Other" or not e["description"]
        ]
        assert not offenders, (
            f"config keys missing a template section/description: {offenders}. "
            "Add each to the JSONC template in generate_template() under a "
            "=== Section === header with an inline comment."
        )

    def test_known_keys_map_to_expected_sections(self):
        from jcodemunch_mcp.config import config_report, _config_meta, generate_template
        groups = {e["key"]: e["group"] for e in config_report()}
        # report groups agree with a direct parse of the template
        meta = _config_meta(generate_template())
        for k, g in groups.items():
            assert g == (meta.get(k, (None, ""))[0] or "Other")
        # representative spot-check + catch-all fallback is non-empty
        assert groups["use_ai_summaries"] == "AI Summarizer"
        assert all(g for g in groups.values())

    def test_descriptions_present_where_template_documents_them(self):
        from jcodemunch_mcp.config import config_report
        descs = {e["key"]: e["description"] for e in config_report()}
        # use_ai_summaries carries an inline comment in the template
        assert "summaries" in descs["use_ai_summaries"].lower()

    def test_config_meta_parses_sections_and_comments(self):
        from jcodemunch_mcp.config import _config_meta
        template = (
            "{\n"
            "  // === Indexing ===\n"
            "  // how many files to index\n"
            '  "max_index_files": 10000,\n'
            "  // === Transport ===\n"
            '  "host": "127.0.0.1",\n'
            "}\n"
        )
        meta = _config_meta(template)
        assert meta["max_index_files"] == ("Indexing", "how many files to index")
        assert meta["host"] == ("Transport", "")  # no preceding comment

    def test_below_style_comments_attach_to_their_own_key(self):
        """Off-by-one regression: some template keys are documented by an
        indented block BELOW the key (contiguous, no blank gap). Those must
        attach to the key they follow, not bleed onto the next one. A blank
        line ends a block, so a subsequent comment is a lead block for the
        next key."""
        from jcodemunch_mcp.config import _config_meta
        template = (
            "{\n"
            "  // === Section ===\n"
            "  // lead comment for alpha\n"
            '  "alpha": "",\n'
            '  "beta": "",\n'
            "  //   below comment for beta\n"
            '  "gamma": "",\n'
            "  //   below comment for gamma\n"
            "\n"
            "  // lead comment for delta\n"
            '  "delta": "",\n'
            "}\n"
        )
        meta = _config_meta(template)
        assert meta["alpha"] == ("Section", "lead comment for alpha")
        assert meta["beta"] == ("Section", "below comment for beta")
        assert meta["gamma"] == ("Section", "below comment for gamma")
        assert meta["delta"] == ("Section", "lead comment for delta")

    def test_below_style_keys_get_their_own_description(self):
        """The reported bug: embed_model / allow_remote_summarizer / path_map
        are documented with below-key blocks in the real template and were
        each mis-described with the *next* key's comment."""
        from jcodemunch_mcp.config import _config_meta, generate_template
        meta = _config_meta(generate_template())
        assert "sentence-transformers" in meta["embed_model"][1].lower()
        assert "remote llm" in meta["allow_remote_summarizer"][1].lower()
        assert "path remapping" in meta["path_map"][1].lower()


class TestSummarizerModelDisplay:
    """Regression: surfaced by @slazarov on #300 (comment 4472458576).
    `config --check` showed the OPENAI_MODEL env default even when
    summarizer_model was configured, because the display logic only read
    OPENAI_MODEL env var, not the config key. Mirror the runtime
    resolution order (config summarizer_model > provider env var > default).
    """

    def _setup(self, monkeypatch, tmp, project_config: dict, env_vars: dict):
        from jcodemunch_mcp.config import _GLOBAL_CONFIG, _PROJECT_CONFIGS
        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()

        storage = tmp / "storage"
        storage.mkdir()
        (storage / "config.jsonc").write_text("{}", encoding="utf-8")

        project = tmp / "project"
        project.mkdir()
        if project_config:
            import json
            (project / ".jcodemunch.jsonc").write_text(
                json.dumps(project_config), encoding="utf-8"
            )

        monkeypatch.setenv("CODE_INDEX_PATH", str(storage))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp))
        monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: project))
        for k in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_BASE",
                  "MINIMAX_API_KEY", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY",
                  "OPENAI_MODEL", "ANTHROPIC_MODEL", "GOOGLE_MODEL",
                  "JCODEMUNCH_SUMMARIZER_PROVIDER",
                  "JCODEMUNCH_SUMMARIZER_MODEL"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env_vars.items():
            monkeypatch.setenv(k, v)
        return project

    def test_summarizer_model_row_present_with_global_value(self, capsys, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            self._setup(monkeypatch, tmp, project_config={}, env_vars={})
            # Set global summarizer_model via the storage config.jsonc.
            import json
            (tmp / "storage" / "config.jsonc").write_text(
                json.dumps({"summarizer_model": "Qwen3.6-Plus"}), encoding="utf-8"
            )

            from jcodemunch_mcp.server import _run_config
            _run_config(check=False)

            captured = capsys.readouterr().out
            sm_lines = [l for l in captured.splitlines() if "summarizer_model" in l]
            assert sm_lines, f"summarizer_model row missing; got: {captured}"
            assert "Qwen3.6-Plus" in sm_lines[0], (
                f"expected configured value on summarizer_model row, got: {sm_lines[0]}"
            )
            # The warning hint for #304 only fires when project-only.
            assert "#304" not in sm_lines[0]

    def test_summarizer_model_project_only_now_displays_cleanly(
        self, capsys, monkeypatch
    ):
        """After #304 runtime fix, project-level summarizer_model flows through
        cleanly — the project-aware shim returns it, runtime honors it, and the
        display tags it [project] without the v1.108.17 'not honored by runtime'
        warning that #304 has now resolved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            self._setup(
                monkeypatch, tmp,
                project_config={"summarizer_model": "Qwen3.6-Plus"},
                env_vars={},
            )

            from jcodemunch_mcp.server import _run_config
            _run_config(check=False)

            captured = capsys.readouterr().out
            sm_lines = [l for l in captured.splitlines() if "summarizer_model" in l]
            assert sm_lines, f"summarizer_model row missing; got: {captured}"
            row = sm_lines[0]
            assert "Qwen3.6-Plus" in row
            assert "[project]" in row, (
                f"expected [project] source tag now that runtime honors it; got: {row}"
            )
            assert "#304" not in row, (
                "the runtime-gap warning was specific to v1.108.17 and is "
                f"obsolete after the #304 runtime fix; got: {row}"
            )

    def test_openai_model_row_uses_configured_summarizer_model(
        self, capsys, monkeypatch
    ):
        """When summarizer_model is set globally AND OpenAI provider is
        active, the OPENAI_MODEL row reflects the config value, not the
        env-or-default fallback. Mirrors batch_summarize.py's resolution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            self._setup(
                monkeypatch, tmp,
                project_config={},
                env_vars={"OPENAI_API_BASE": "http://localhost:11434/v1"},
            )
            import json
            (tmp / "storage" / "config.jsonc").write_text(
                json.dumps({"summarizer_model": "Qwen3.6-Plus"}), encoding="utf-8"
            )

            from jcodemunch_mcp.server import _run_config
            _run_config(check=False)

            captured = capsys.readouterr().out
            openai_model_lines = [
                l for l in captured.splitlines() if "OPENAI_MODEL" in l
            ]
            assert openai_model_lines, f"OPENAI_MODEL row missing; got: {captured}"
            row = openai_model_lines[0]
            assert "Qwen3.6-Plus" in row, (
                f"OPENAI_MODEL should reflect summarizer_model config, got: {row}"
            )
            assert "qwen3-coder" not in row, (
                f"hardcoded default should be replaced by config, got: {row}"
            )


class TestClaudeMdDriftCheck:
    """config --check: CLAUDE.md drift detection."""

    def _run_check(self, monkeypatch, tmpdir, claude_md_content=None):
        """Helper: set up temp dir and run config --check."""
        from jcodemunch_mcp.config import _GLOBAL_CONFIG
        _GLOBAL_CONFIG.clear()

        config_path = Path(tmpdir) / "config.jsonc"
        config_path.write_text("{}")
        monkeypatch.setenv("CODE_INDEX_PATH", tmpdir)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(tmpdir)))

        claude_dir = Path(tmpdir) / ".claude"
        claude_dir.mkdir(exist_ok=True)
        if claude_md_content is not None:
            (claude_dir / "CLAUDE.md").write_text(claude_md_content, encoding="utf-8")

        from jcodemunch_mcp.server import _run_config
        return _run_config

    def test_check_passes_when_all_tools_present(self, capsys, monkeypatch):
        """check should pass when CLAUDE.md mentions all canonical tools."""
        from jcodemunch_mcp.server import _CANONICAL_TOOL_NAMES
        with tempfile.TemporaryDirectory() as tmpdir:
            fn = self._run_check(monkeypatch, tmpdir,
                                 claude_md_content="\n".join(_CANONICAL_TOOL_NAMES))
            fn(check=True)
            out = capsys.readouterr().out
            assert "All checks passed" in out
            assert "not mentioned in CLAUDE.md" not in out

    def test_check_warns_when_tools_missing(self, capsys, monkeypatch):
        """check should warn and exit 1 when CLAUDE.md is missing tools."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fn = self._run_check(monkeypatch, tmpdir,
                                 claude_md_content="list_repos search_symbols")
            with pytest.raises(SystemExit) as exc:
                fn(check=True)
            assert exc.value.code == 1
            out = capsys.readouterr().out
            assert "not mentioned in CLAUDE.md" in out
            assert "claude-md --generate" in out

    def test_check_passes_with_one_line_jcodemunch_guide_form(self, capsys, monkeypatch):
        """check should pass when CLAUDE.md uses the documented one-line form
        ('Call the jcodemunch_guide tool ...') without listing every tool — the
        guide returns the version-pinned policy at runtime. Issue #271."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fn = self._run_check(
                monkeypatch, tmpdir,
                claude_md_content=(
                    "Call the jcodemunch_guide tool and "
                    "strictly follow its instructions.\n"
                ),
            )
            fn(check=True)
            out = capsys.readouterr().out
            assert "All checks passed" in out
            assert "not mentioned in CLAUDE.md" not in out
            assert "one-line form" in out

    def test_check_warns_when_claude_md_absent(self, capsys, monkeypatch):
        """check should warn (not error) when CLAUDE.md is not found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # No CLAUDE.md created — only config
            fn = self._run_check(monkeypatch, tmpdir, claude_md_content=None)
            # No sys.exit raised for missing file (just a warning)
            out_lines: list[str] = []
            try:
                fn(check=True)
                out_lines = capsys.readouterr().out.splitlines()
            except SystemExit:
                out_lines = capsys.readouterr().out.splitlines()
            assert any("CLAUDE.md not found" in l or "not mentioned" in l for l in out_lines)


class TestClaudeMdGenerate:
    """claude-md --generate subcommand."""

    def test_generate_full_snippet(self, capsys, monkeypatch):
        """--generate outputs all canonical tool names."""
        from jcodemunch_mcp.server import _run_claude_md, _CANONICAL_TOOL_NAMES
        _run_claude_md(generate=True, fmt="full")
        out = capsys.readouterr().out
        for tool in _CANONICAL_TOOL_NAMES:
            assert tool in out, f"Expected tool {tool!r} in snippet"

    def test_generate_append_reports_missing(self, monkeypatch, tmp_path, capsys):
        """--format=append outputs only tools absent from the existing CLAUDE.md."""
        from jcodemunch_mcp.server import _run_claude_md, _CANONICAL_TOOL_NAMES
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "CLAUDE.md").write_text("list_repos search_symbols", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        _run_claude_md(generate=True, fmt="append")
        out = capsys.readouterr().out
        # Should include tools not in the existing file
        assert "index_repo" in out
        # Should NOT include tools already present
        assert "list_repos" not in out
        assert "search_symbols" not in out

    def test_generate_append_silent_when_current(self, monkeypatch, tmp_path, capsys):
        """--format=append prints nothing to stdout when CLAUDE.md is up to date."""
        from jcodemunch_mcp.server import _run_claude_md, _CANONICAL_TOOL_NAMES
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "CLAUDE.md").write_text(
            "\n".join(_CANONICAL_TOOL_NAMES), encoding="utf-8"
        )
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        _run_claude_md(generate=True, fmt="append")
        out = capsys.readouterr().out
        assert out == ""

    def test_canonical_list_matches_build_tools_list(self, monkeypatch):
        """_CANONICAL_TOOL_NAMES must include every tool _build_tools_list() can emit.

        We clear disabled_tools so the full unfiltered list is returned, then
        verify the canonical tuple is a superset of what the builder produces.
        Tools can appear in _CANONICAL_TOOL_NAMES but not in the live list
        (e.g. test_summarizer, which is disabled by default) — that's fine.
        The reverse (a built tool absent from the canonical list) is the error.
        """
        import jcodemunch_mcp.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "get", lambda key, default=None: (
            [] if key == "disabled_tools" else
            "full" if key == "tool_profile" else
            False if key == "compact_schemas" else
            None if key in ("languages", "meta_fields", "descriptions") else
            default
        ))
        from jcodemunch_mcp.server import _CANONICAL_TOOL_NAMES, _build_tools_list
        built_names = {t.name for t in _build_tools_list()}
        canonical = set(_CANONICAL_TOOL_NAMES)
        missing_from_canonical = built_names - canonical
        assert not missing_from_canonical, (
            f"Tools in _build_tools_list() but missing from _CANONICAL_TOOL_NAMES: "
            f"{missing_from_canonical}"
        )


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
@pytest.mark.filterwarnings("ignore::RuntimeWarning")
class TestLoadConfigWiredIntoMain:
    """Test that load_config() is called during server startup."""

    @pytest.mark.asyncio
    async def test_main_calls_load_config_for_serve_command(self, monkeypatch, tmp_path):
        """load_config should be called when serve subcommand starts."""
        from jcodemunch_mcp.config import _GLOBAL_CONFIG, DEFAULTS

        _GLOBAL_CONFIG.clear()
        _GLOBAL_CONFIG.update(DEFAULTS)

        # Create a temp config with a distinctive value
        config_path = tmp_path / "config.jsonc"
        config_path.write_text('{"max_folder_files": 9999}')
        monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))

        # Track whether load_config was called
        call_count = 0

        # Import fresh — need to get the original reference before patching
        import jcodemunch_mcp.config as cfg_module
        real_load = cfg_module.load_config

        def tracked_load(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return real_load(*args, **kwargs)

        cfg_module.load_config = tracked_load

        # Patch sys.exit to prevent exit
        monkeypatch.setattr("sys.exit", lambda code=0: None)

        # Patch asyncio.run to avoid starting the actual server
        def fake_asyncio_run(coro):
            # Server startup is blocked by fake_server_run patch below,
            # so we just close the coroutine without awaiting
            coro.close()

        monkeypatch.setattr("asyncio.run", fake_asyncio_run)

        # Also patch the MCP server.run to prevent actual startup
        import jcodemunch_mcp.server as server_module
        async def fake_server_run(*args, **kwargs):
            pass
        monkeypatch.setattr(server_module.server, "run", fake_server_run)

        from jcodemunch_mcp.server import main
        main(["serve"])

        # After main() runs, config should reflect the file (not just defaults)
        assert cfg_module.get("max_folder_files") == 9999
        assert call_count >= 1, "load_config should have been called during serve"

    @pytest.mark.asyncio
    async def test_config_loaded_before_list_tools(self, monkeypatch, tmp_path):
        """After main() starts, config should be loaded and usable by list_tools."""
        # Use the SAME module object that server.py imports
        import jcodemunch_mcp.config as cfg_module
        cfg_module._GLOBAL_CONFIG.clear()
        cfg_module._GLOBAL_CONFIG.update({"max_folder_files": 2000})  # default

        config_path = tmp_path / "config.jsonc"
        config_path.write_text('{"max_folder_files": 7777}')
        monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))

        monkeypatch.setattr("sys.exit", lambda code=0: None)

        # Patch asyncio.run to avoid starting the actual server
        def fake_asyncio_run(coro):
            # Server startup is blocked by fake_server_run patch below,
            # so we just close the coroutine without awaiting
            coro.close()

        monkeypatch.setattr("asyncio.run", fake_asyncio_run)

        import jcodemunch_mcp.server as server_module
        async def fake_server_run(*args, **kwargs):
            pass
        monkeypatch.setattr(server_module.server, "run", fake_server_run)

        from jcodemunch_mcp.server import main
        main(["serve"])

        # After main() runs, config should reflect the file (not just defaults)
        assert cfg_module.get("max_folder_files") == 7777

    def test_load_all_project_configs_called_at_startup(self, monkeypatch, tmp_path):
        """Server startup should call load_all_project_configs()."""
        from jcodemunch_mcp import config as config_module

        load_calls = []
        load_all_calls = []

        def tracked_load(*args, **kwargs):
            load_calls.append((args, kwargs))
            config_module._GLOBAL_CONFIG = config_module.DEFAULTS.copy()

        def tracked_load_all(*args, **kwargs):
            load_all_calls.append((args, kwargs))

        monkeypatch.setattr(config_module, "load_config", tracked_load)
        monkeypatch.setattr(config_module, "load_all_project_configs", tracked_load_all)

        # Patch asyncio.run to close the coroutine without awaiting
        def fake_asyncio_run(coro):
            coro.close()

        monkeypatch.setattr("asyncio.run", fake_asyncio_run)

        import sys
        old_argv = sys.argv
        sys.argv = ["jcodemunch-mcp"]
        try:
            from jcodemunch_mcp.server import main
            main([])
        finally:
            sys.argv = old_argv

        assert len(load_calls) >= 1, "load_config should be called"
        assert len(load_all_calls) >= 1, "load_all_project_configs should be called"


# ── Env Var List Comma-Separated Fallback Test (E4) ───────────────────────────────


def test_parse_env_value_list_comma_separated_fallback():
    """_parse_env_value list type falls back to comma-separated on parse failure (E4)."""
    from jcodemunch_mcp.config import _parse_env_value

    # Legacy comma-separated format (*.log,*.tmp) should parse as list
    result = _parse_env_value("*.log,*.tmp,*.cache", list)
    assert result == ["*.log", "*.tmp", "*.cache"]

    # Single value (no comma) should still work
    result = _parse_env_value("*.log", list)
    assert result == ["*.log"]

    # JSON array format should still take priority
    result = _parse_env_value('["*.log", "*.tmp"]', list)
    assert result == ["*.log", "*.tmp"]

    # Empty string should return [] (allows clearing list via env var)
    result = _parse_env_value("", list)
    assert result == []

    # Whitespace-only tokens should be stripped
    result = _parse_env_value("*.log,  ,*.tmp", list)
    assert result == ["*.log", "*.tmp"]


# ── use_ai_summaries env var preserves string values ──────────────────────────────


def test_parse_env_value_use_ai_summaries_preserves_string():
    """_parse_env_value returns raw string for use_ai_summaries to preserve 'auto'."""
    from jcodemunch_mcp.config import _parse_env_value

    # "auto" must be preserved as a string, not coerced to False by bool parsing
    assert _parse_env_value("auto", (bool, str), key="use_ai_summaries") == "auto"

    # "true" stored as string — downstream handlers accept both str and bool
    assert _parse_env_value("true", (bool, str), key="use_ai_summaries") == "true"

    # "false" stored as string
    assert _parse_env_value("false", (bool, str), key="use_ai_summaries") == "false"

    # Case-insensitive
    assert _parse_env_value("AUTO", (bool, str), key="use_ai_summaries") == "auto"


# ── Comprehensive Config File Edge Case Tests ────────────────────────────────────


class TestJSONCSyntaxErrors:
    """Test JSONC parser handles malformed syntax gracefully."""

    @pytest.mark.parametrize("text", [
        '{"key": "unclosed string}',
        '{"key": "value"',
        '{"arr": [1, 2, 3',
        '{"key": "value",',
    ], ids=["unclosed_string", "missing_closing_brace", "missing_closing_bracket", "trailing_comma_no_brace"])
    def test_invalid_json_raises_decode_error(self, text):
        """Malformed JSON should raise JSONDecodeError."""
        result = _strip_jsonc(text)
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)

    def test_unclosed_block_comment_stripped_to_end(self):
        """Unclosed block comment should strip to end of file."""
        text = '{"key": "value" /* this never ends'
        result = _strip_jsonc(text)
        # The block comment should be stripped
        assert "/*" not in result
        assert "this never ends" not in result
        assert '"key"' in result

    def test_duplicate_keys_allowed(self):
        """JSON allows duplicate keys (last wins) - verify our parser doesn't break."""
        text = '{"key": "first", "key": "second"}'
        result = _strip_jsonc(text)
        parsed = json.loads(result)
        assert parsed["key"] == "second"  # Last value wins


class TestJSONCEdgeCases:
    """Test JSONC parser handles edge cases correctly."""

    @pytest.mark.parametrize("text", [
        "",
        "// This is just a comment\n/* and a block comment */\n// More comments",
    ], ids=["empty_file", "only_comments"])
    def test_invalid_json_raises(self, text):
        """Invalid JSON should raise JSONDecodeError."""
        result = _strip_jsonc(text)
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)

    @pytest.mark.parametrize("text,expected", [
        ('{}', {}),
        ('{\n    // This is empty\n}', {}),
        ('{"greeting": "Hello 世界 🌍"}', {"greeting": "Hello 世界 🌍"}),
        ('{"text": "line1\\nline2\\nline3"}', {"text": "line1\nline2\nline3"}),
        (r'{"path": "C:\\Users\\test\\file.txt"}', {"path": "C:\\Users\\test\\file.txt"}),
        ('{"a": {"b": {"c": {"d": {"e": {"f": "deep"}}}}}}', {"a": {"b": {"c": {"d": {"e": {"f": "deep"}}}}}}),
    ], ids=["empty_object", "empty_object_comments", "unicode", "escaped_newlines", "backslashes", "nested_structure"])
    def test_valid_json_parsing(self, text, expected):
        """Valid JSON-like content should parse correctly."""
        result = _strip_jsonc(text)
        parsed = json.loads(result)
        assert parsed == expected

    def test_large_array(self):
        """Large arrays should parse correctly."""
        items = ", ".join(str(i) for i in range(100))
        text = f'{{"arr": [{items}]}}'
        result = _strip_jsonc(text)
        parsed = json.loads(result)
        assert len(parsed["arr"]) == 100

    @pytest.mark.parametrize("text,check", [
        ('{"text": "line1\\tline2"}', lambda p: "\t" in p["text"]),
        ('{"text": "say \\"hello\\""}', lambda p: '"' in p["text"]),
        (r'{"path": "C:\\Users\\test"}', lambda p: "\\" in p["path"]),
    ], ids=["tab_char", "quote_char", "backslash_char"])
    def test_special_characters_in_strings(self, text, check):
        """Special characters in strings should be preserved."""
        result = _strip_jsonc(text)
        parsed = json.loads(result)
        assert check(parsed)


class TestConfigTypeValidation:
    """Test config type validation for all config keys."""

    @pytest.mark.parametrize("key,bad_value,expected_default", [
        ("context_providers", '"true"', True),   # String instead of bool
        ("max_folder_files", "2000.5", 2000),    # Float instead of int
        ("disabled_tools", '{"tool": "name"}', ["test_summarizer"]),  # Object instead of list
        ("extra_extensions", '[".lua"]', {}),     # List instead of dict
        ("meta_fields", '{"invalid": "dict"}', []),  # Dict instead of list
        ("server_output", '"banana"', "adaptive"),
        ("server_output_threshold", '-0.25', 0.15),
    ], ids=[
        "bool_string",
        "int_float",
        "list_object",
        "dict_list",
        "meta_fields_dict",
        "server_output_invalid",
        "server_output_threshold_negative",
    ])
    def test_type_mismatch_uses_default(self, key, bad_value, expected_default):
        """Type mismatch should fall back to default."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text(f'{{"{key}": {bad_value}}}')
            load_config(tmpdir)
            assert get(key) == expected_default

    def test_int_type_mismatch_negative_accepted(self):
        """Negative int should be accepted (no range validation)."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": -1}')

            load_config(tmpdir)
            # Negative is still a valid int (range validation is elsewhere)
            assert get("max_folder_files") == -1

    @pytest.mark.parametrize("key,value,expected", [
        ("languages", "null", None),
        ("log_level", '""', ""),
    ], ids=["languages_null", "empty_string"])
    def test_optional_and_string_types(self, key, value, expected):
        """Null for optional types and empty string for string types should be accepted."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text(f'{{"{key}": {value}}}')
            load_config(tmpdir)
            assert get(key) is expected


class TestProjectConfigEdgeCases:
    """Test project-level config edge cases."""

    def test_project_config_invalid_syntax(self):
        """Invalid project config should fall back to global."""
        from jcodemunch_mcp.config import (
            load_config, load_project_config, get,
            _GLOBAL_CONFIG, _PROJECT_CONFIGS
        )

        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Set up global config
            global_config = Path(tmpdir) / "global" / "config.jsonc"
            global_config.parent.mkdir()
            global_config.write_text('{"max_folder_files": 2000}')

            load_config(str(global_config.parent))

            # Set up project config with invalid JSON
            project_root = Path(tmpdir) / "project"
            project_root.mkdir()
            project_config = project_root / ".jcodemunch.jsonc"
            project_config.write_text('{"max_folder_files": invalid}')

            load_project_config(str(project_root))

            # Should fall back to global config
            repo_key = str(project_root.resolve())
            assert get("max_folder_files", repo=repo_key) == 2000

    def test_project_config_type_mismatch(self):
        """Project config with type mismatch should use global value."""
        from jcodemunch_mcp.config import (
            load_config, load_project_config, get,
            _GLOBAL_CONFIG, _PROJECT_CONFIGS
        )

        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Set up global config
            global_config = Path(tmpdir) / "global" / "config.jsonc"
            global_config.parent.mkdir()
            global_config.write_text('{"max_folder_files": 2000}')

            load_config(str(global_config.parent))

            # Set up project config with type mismatch
            project_root = Path(tmpdir) / "project"
            project_root.mkdir()
            project_config = project_root / ".jcodemunch.jsonc"
            project_config.write_text('{"max_folder_files": "not_an_int"}')

            load_project_config(str(project_root))

            # Should use global value (type mismatch rejected)
            repo_key = str(project_root.resolve())
            assert get("max_folder_files", repo=repo_key) == 2000

    def test_project_config_unknown_key_ignored(self):
        """Project config with unknown key should ignore it."""
        from jcodemunch_mcp.config import (
            load_config, load_project_config, get,
            _GLOBAL_CONFIG, _PROJECT_CONFIGS
        )

        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Set up global config
            global_config = Path(tmpdir) / "global" / "config.jsonc"
            global_config.parent.mkdir()
            global_config.write_text('{"max_folder_files": 2000}')

            load_config(str(global_config.parent))

            # Set up project config with unknown key
            project_root = Path(tmpdir) / "project"
            project_root.mkdir()
            project_config = project_root / ".jcodemunch.jsonc"
            project_config.write_text('{"unknown_key": "value", "max_folder_files": 5000}')

            load_project_config(str(project_root))

            # Unknown key should be ignored, known key should work
            repo_key = str(project_root.resolve())
            assert get("max_folder_files", repo=repo_key) == 5000
            assert get("unknown_key", repo=repo_key) is None


class TestConfigFileEncoding:
    """Test config file encoding edge cases."""

    def test_utf8_bom_handled(self):
        """UTF-8 BOM should be handled correctly."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            # Write UTF-8 BOM + JSON
            config_path.write_bytes(b'\xef\xbb\xbf{"max_folder_files": 5000}')

            load_config(tmpdir)
            assert get("max_folder_files") == 5000

    def test_utf8_with_bom_and_comments(self):
        """UTF-8 BOM with comments should work."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            # Write UTF-8 BOM + JSONC with comments
            config_path.write_bytes(b'\xef\xbb\xbf{"max_folder_files": 5000, // comment\n}')

            load_config(tmpdir)
            assert get("max_folder_files") == 5000


class TestAllConfigKeys:
    """Test that all config keys can be loaded correctly."""

    @pytest.mark.parametrize("key", [
        "transport", "host", "freshness_mode", "log_level", "server_output"
    ], ids=[
        "string_transport",
        "string_host",
        "string_freshness_mode",
        "string_log_level",
        "string_server_output",
    ])
    def test_all_string_keys(self, key):
        """Test all string-typed config keys."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()
        with tempfile.TemporaryDirectory() as tmpdir:
            value = "raw" if key == "server_output" else "test_value"
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text(f'{{"{key}": "{value}"}}')
            load_config(tmpdir)
            assert get(key) == value, f"Key {key} failed"

    @pytest.mark.parametrize("key", [
        "max_folder_files", "max_index_files", "staleness_days",
        "max_results", "port", "rate_limit", "watch_debounce_ms",
        "stats_file_interval", "summarizer_concurrency"
    ], ids=["int_max_folder", "int_max_index", "int_staleness", "int_max_results",
            "int_port", "int_rate_limit", "int_watch_debounce", "int_stats_interval", "int_summarizer_concurrency"])
    def test_all_int_keys(self, key):
        """Test all int-typed config keys."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text(f'{{"{key}": 42}}')
            load_config(tmpdir)
            assert get(key) == 42, f"Key {key} failed"

    @pytest.mark.parametrize("key", [
        "context_providers", "redact_source_root",
        "share_savings", "allow_remote_summarizer", "watch"
    ], ids=["bool_context_providers", "bool_redact_source_root", "bool_share_savings",
            "bool_allow_remote_summarizer", "bool_watch"])
    def test_all_bool_keys(self, key):
        """Test all bool-typed config keys."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text(f'{{"{key}": false}}')
            load_config(tmpdir)
            assert get(key) is False, f"Key {key} failed"

    @pytest.mark.parametrize("key", [
        "disabled_tools", "extra_ignore_patterns", "meta_fields"
    ], ids=["list_disabled_tools", "list_extra_ignore_patterns", "list_meta_fields"])
    def test_all_list_keys(self, key):
        """Test all list-typed config keys."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text(f'{{"{key}": ["item1", "item2"]}}')
            load_config(tmpdir)
            assert get(key) == ["item1", "item2"], f"Key {key} failed"

    def test_all_list_keys_trusted_folders(self, tmp_path):
        """trusted_folders requires absolute paths and is normalized on load."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()
        trusted1 = tmp_path / "trusted1"
        trusted2 = tmp_path / "trusted2"
        config_path = tmp_path / "config.jsonc"
        config_path.write_text(
            json.dumps({"trusted_folders": [str(trusted1), str(trusted2)]})
        )

        load_config(str(tmp_path))
        result = get("trusted_folders")
        # Order is not guaranteed (set-based deduplication)
        assert len(result) == 2
        assert trusted1.expanduser() in result
        assert trusted2.expanduser() in result

    def test_all_list_keys_languages(self):
        """Test languages list-typed config key with valid language names."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"languages": ["python", "javascript"]}')
            load_config(tmpdir)
            assert get("languages") == ["python", "javascript"], "Key languages failed"

    @pytest.mark.parametrize("key", [
        "extra_extensions", "descriptions"
    ], ids=["dict_extra_extensions", "dict_descriptions"])
    def test_all_dict_keys(self, key):
        """Test all dict-typed config keys."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text(f'{{"{key}": {{"nested": "value"}}}}')
            load_config(tmpdir)
            assert get(key) == {"nested": "value"}, f"Key {key} failed"

    @pytest.mark.parametrize("key,value", [
        ("claude_poll_interval", 1.25),
        ("server_output_threshold", 0.2),
    ], ids=["float_claude_poll_interval", "float_server_output_threshold"])
    def test_all_float_keys(self, key, value):
        """Test all float-typed config keys."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text(f'{{"{key}": {value}}}')
            load_config(tmpdir)
            assert get(key) == value, f"Key {key} failed"

    def test_all_nullable_keys(self):
        """Test all nullable config keys accept null."""
        from jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG, CONFIG_TYPES

        # Find all keys with tuple types that include None
        nullable_keys = [k for k, v in CONFIG_TYPES.items()
                        if isinstance(v, tuple) and type(None) in v]

        for key in nullable_keys:
            _GLOBAL_CONFIG.clear()
            with tempfile.TemporaryDirectory() as tmpdir:
                config_path = Path(tmpdir) / "config.jsonc"
                config_path.write_text(f'{{"{key}": null}}')
                load_config(tmpdir)
                assert get(key) is None, f"Key {key} failed"


class TestConfigInit:
    """Test the config --init CLI subcommand."""

    def test_config_init_creates_template(self, tmp_path, monkeypatch, capsys):
        """config --init should create config.jsonc template."""
        from jcodemunch_mcp.server import main

        storage_path = str(tmp_path)
        monkeypatch.setenv("CODE_INDEX_PATH", storage_path)

        main(["config", "--init"])

        captured = capsys.readouterr()
        assert "Created config template" in captured.out

        config_path = tmp_path / "config.jsonc"
        assert config_path.exists()

        content = config_path.read_text()
        from jcodemunch_mcp.config import _strip_jsonc
        stripped = _strip_jsonc(content)
        parsed = json.loads(stripped)
        assert "languages" in parsed

    def test_config_init_refuses_overwrite(self, tmp_path, monkeypatch, capsys):
        """config --init should refuse to overwrite existing file."""
        from jcodemunch_mcp.server import main

        storage_path = str(tmp_path)
        monkeypatch.setenv("CODE_INDEX_PATH", storage_path)

        config_path = tmp_path / "config.jsonc"
        config_path.write_text('{"existing": true}')

        main(["config", "--init"])

        captured = capsys.readouterr()
        assert "Config file already exists" in captured.out
        assert "Refusing to overwrite" in captured.out

        assert json.loads(config_path.read_text()) == {"existing": True}


def test_config_report_shape_and_source_attribution(tmp_path, monkeypatch):
    """config_report() emits one structured entry per known key, attributing
    each value to default / global / project."""
    from jcodemunch_mcp import config as cfg

    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "idx"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".jcodemunch.jsonc").write_text('{"staleness_days": 3}', encoding="utf-8")

    report = cfg.config_report(repo=str(proj))
    assert isinstance(report, list) and report
    assert all({"key", "type", "value", "default", "source"} <= e.keys() for e in report)

    by_key = {e["key"]: e for e in report}
    # Project override is attributed to the project layer with the merged value.
    assert by_key["staleness_days"]["source"] == "project"
    assert by_key["staleness_days"]["value"] == 3
    # An untouched key stays default.
    assert by_key["max_results"]["source"] == "default"
    # Types are labeled, not raw Python types.
    assert by_key["staleness_days"]["type"] == "int"
    assert by_key["server_output"]["type"] == "string"
