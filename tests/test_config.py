"""Tests for JSONC config parsing."""

import json
import tempfile
from pathlib import Path

import pytest

from tests import _platform_path, _platform_path_str
from src.jcodemunch_mcp.config import _strip_jsonc


class TestJSONCParser:
    """Test JSONC comment stripping."""

    def test_strips_line_comments(self):
        """Should strip // comments to end of line."""
        text = '{"key": "value" // this is a comment\n}'
        result = _strip_jsonc(text)
        # Result must be valid JSON
        json.loads(result)  # Must be valid JSON

    def test_strips_line_comment_no_trailing_newline(self):
        """Should strip // comment at end of file."""
        text = '{"key": "value"} // comment'
        result = _strip_jsonc(text)
        json.loads(result)  # Must be valid JSON

    def test_strips_block_comments(self):
        """Should strip /* */ block comments."""
        text = '{"key" /* comment */: "value"}'
        result = _strip_jsonc(text)
        json.loads(result)  # Must be valid JSON

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

    def test_preserves_strings_with_comment_chars(self):
        """Should not strip // or /* inside quoted strings."""
        text = '{"url": "http://example.com", "note": "use /* here*/"}'
        result = _strip_jsonc(text)
        json.loads(result)  # Must be valid JSON
        parsed = json.loads(result)
        assert parsed["url"] == "http://example.com"
        assert parsed["note"] == "use /* here*/"

    def test_trailing_comma_in_object(self):
        """Should strip trailing commas in objects (valid JSONC, invalid JSON)."""
        text = '{"a": 1, "b": 2,}'
        result = _strip_jsonc(text)
        json.loads(result)  # Must be valid JSON
        assert json.loads(result) == {"a": 1, "b": 2}

    def test_trailing_comma_in_nested_object(self):
        """Should strip trailing commas in nested objects."""
        text = '{"a": {"b": 1,}, "c": 2,}'
        result = _strip_jsonc(text)
        json.loads(result)  # Must be valid JSON

    def test_trailing_comma_in_array(self):
        """Should strip trailing commas in arrays."""
        text = '{"arr": [1, 2, 3,]}'
        result = _strip_jsonc(text)
        json.loads(result)  # Must be valid JSON
        assert json.loads(result)["arr"] == [1, 2, 3]

    def test_trailing_comma_with_comment(self):
        """Should strip trailing commas when followed by line comment."""
        text = '{\n  "key": "value", // comment\n}'
        result = _strip_jsonc(text)
        json.loads(result)  # Must be valid JSON

    def test_comment_before_closing_brace_with_trailing_comma(self):
        """Should handle comment before closing brace with trailing comma."""
        text = '{"key": "value", // comment\n}'
        result = _strip_jsonc(text)
        json.loads(result)  # Must be valid JSON

    def test_escaped_quotes_in_strings(self):
        """Should preserve escaped quotes inside strings."""
        text = r'{"key": "value with \"quote\""}'
        result = _strip_jsonc(text)
        json.loads(result)  # Must be valid JSON
        assert json.loads(result)["key"] == 'value with "quote"'

    def test_comment_like_content_in_string(self):
        """Should preserve // and /* inside quoted strings."""
        text = '{"url": "http://example.com", "regex": "/*.*?*/"}'
        result = _strip_jsonc(text)
        json.loads(result)  # Must be valid JSON
        parsed = json.loads(result)
        assert parsed["url"] == "http://example.com"
        assert parsed["regex"] == "/*.*?*/"

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

    def test_multiple_trailing_commas_nested(self):
        """Should handle multiple trailing commas in deeply nested structures."""
        text = '{"a": {"b": {"c": 1,},}, "d": [{"e": 2,},],}'
        result = _strip_jsonc(text)
        json.loads(result)  # Must be valid JSON


class TestConfigDefaults:
    """Test default config values."""

    def test_default_max_folder_files(self):
        """Should default to 2000 max folder files."""
        from src.jcodemunch_mcp.config import DEFAULTS
        assert DEFAULTS["max_folder_files"] == 2000

    def test_default_max_index_files(self):
        """Should default to 10000 max index files."""
        from src.jcodemunch_mcp.config import DEFAULTS
        assert DEFAULTS["max_index_files"] == 10000

    def test_default_languages_is_none(self):
        """Should default to None (all languages enabled)."""
        from src.jcodemunch_mcp.config import DEFAULTS
        assert DEFAULTS["languages"] is None

    def test_default_disabled_tools_is_empty(self):
        """Should default to empty list (all tools enabled)."""
        from src.jcodemunch_mcp.config import DEFAULTS
        assert DEFAULTS["disabled_tools"] == []


class TestConfigLoading:
    """Test config file loading."""

    def test_auto_creates_default_config_if_missing(self, tmp_path):
        """load_config() should create default config.jsonc if it doesn't exist."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

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
        assert get("use_ai_summaries") is True

    def test_missing_file_uses_defaults(self, monkeypatch):
        """Should use defaults when config file doesn't exist."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        # Clear any existing config
        _GLOBAL_CONFIG.clear()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            non_existent = Path(tmpdir) / "nonexistent" / "config.jsonc"
            monkeypatch.setenv("CODE_INDEX_PATH", str(Path(tmpdir) / "nonexistent"))

            load_config(str(Path(tmpdir) / "nonexistent"))

            assert get("max_folder_files") == 2000
            assert get("use_ai_summaries") is True

    def test_loads_valid_config(self, monkeypatch):
        """Should load valid JSONC config."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

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

    def test_meta_fields_null_from_config(self):
        """meta_fields: null means all fields included (backward compatible)."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"meta_fields": null}')

            load_config(tmpdir)

            assert get("meta_fields") is None

    def test_meta_fields_empty_list_from_config(self):
        """meta_fields: [] means no _meta (maximum token savings)."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"meta_fields": []}')

            load_config(tmpdir)

            assert get("meta_fields") == []

    def test_meta_fields_partial_list_from_config(self):
        """meta_fields: ["timing_ms", "powered_by"] means only those fields."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"meta_fields": ["timing_ms", "powered_by"]}')

            load_config(tmpdir)

            assert get("meta_fields") == ["timing_ms", "powered_by"]

    def test_meta_fields_absent_uses_default(self):
        """meta_fields absent from config uses default (None = all fields)."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG, DEFAULTS

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": 5000}')

            load_config(tmpdir)

            # When not specified, should use DEFAULTS value
            assert get("meta_fields") is None

    def test_type_mismatch_logs_warning_and_uses_default(self, monkeypatch, caplog):
        """Should log warning and use default on type mismatch."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG
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
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

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
        from src.jcodemunch_mcp.config import (
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
            "src.jcodemunch_mcp.config._list_repos_for_config", return_value=mock_repos
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
        from src.jcodemunch_mcp.config import (
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
            assert get("use_ai_summaries", repo=repo_key) is True


class TestConfigGetters:
    """Test config getter functions."""

    def test_is_tool_disabled(self):
        """Should return True if tool is in disabled_tools."""
        from src.jcodemunch_mcp.config import (
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
        from src.jcodemunch_mcp.config import (
            load_config, is_language_enabled, _GLOBAL_CONFIG, DEFAULTS
        )

        _GLOBAL_CONFIG.clear()
        _GLOBAL_CONFIG.update(DEFAULTS)  # languages = None

        assert is_language_enabled("python") is True
        assert is_language_enabled("sql") is True

    def test_is_language_enabled_filtered(self):
        """Should return False for disabled languages."""
        from src.jcodemunch_mcp.config import load_config, is_language_enabled, _GLOBAL_CONFIG

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
        from src.jcodemunch_mcp.config import generate_template

        template = generate_template()

        # Should be parseable after stripping comments
        from src.jcodemunch_mcp.config import _strip_jsonc
        stripped = _strip_jsonc(template)
        parsed = json.loads(stripped)

        assert "languages" in parsed
        assert "disabled_tools" in parsed
        assert "meta_fields" in parsed

    def test_template_languages_synced_from_registry(self):
        """Should include all languages from LANGUAGE_REGISTRY."""
        from src.jcodemunch_mcp.config import generate_template
        from src.jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY

        template = generate_template()
        stripped = _strip_jsonc(template)
        parsed = json.loads(stripped)

        # All registry languages should be in template
        for lang in LANGUAGE_REGISTRY.keys():
            assert lang in parsed["languages"]


class TestGetDescriptions:
    """Test get_descriptions() function."""

    def test_returns_descriptions_dict(self):
        """Should return descriptions dict from config."""
        from src.jcodemunch_mcp.config import load_config, get_descriptions, _GLOBAL_CONFIG

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
        from src.jcodemunch_mcp.config import load_config, get_descriptions, _GLOBAL_CONFIG, DEFAULTS

        _GLOBAL_CONFIG.clear()
        _GLOBAL_CONFIG.update(DEFAULTS)  # descriptions = {}

        result = get_descriptions()
        assert result == {}


class TestEnvVarFallback:
    """Test deprecated env var fallback with warnings."""

    def test_env_var_used_when_config_key_absent(self, monkeypatch, caplog):
        """Should use env var value when config key not set."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG, _DEPRECATED_ENV_VARS_LOGGED
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
        from src.jcodemunch_mcp.config import load_config, _GLOBAL_CONFIG, _DEPRECATED_ENV_VARS_LOGGED
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
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG, _DEPRECATED_ENV_VARS_LOGGED
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
        from src.jcodemunch_mcp.config import (
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
        from src.jcodemunch_mcp.config import (
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


class TestTrustedFoldersConfig:
    def test_validate_trusted_folders_rejects_relative_entries(self, tmp_path):
        """validate_config should flag relative trusted_folders entries."""
        from src.jcodemunch_mcp.config import validate_config

        config_path = tmp_path / "config.jsonc"
        config_path.write_text('{"trusted_folders": ["work"]}')

        issues = validate_config(str(config_path))
        assert any(
            "trusted_folders entry 'work' must be an absolute path" in issue
            for issue in issues
        )

    def test_validate_trusted_folders_rejects_non_string_entries(self, tmp_path):
        """validate_config should reject trusted_folders entries that are not strings."""
        from src.jcodemunch_mcp.config import validate_config

        config_path = tmp_path / "config.jsonc"
        config_path.write_text('{"trusted_folders": [123]}')

        issues = validate_config(str(config_path))
        assert any(
            "Config key 'trusted_folders' has invalid type" in issue for issue in issues
        )

    def test_load_config_normalizes_trusted_folders(self, tmp_path):
        """load_config should normalize trusted_folders immediately."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        trusted = tmp_path / "trusted"
        config_path = tmp_path / "config.jsonc"
        config_path.write_text(
            json.dumps({"trusted_folders": [str(trusted / ".." / "trusted")]})
        )

        load_config(str(tmp_path))

        assert get("trusted_folders") == [trusted.expanduser()]

    def test_load_config_keeps_valid_trusted_folders(self, tmp_path):
        """Valid rooted trusted_folders should remain available after load."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        config_path = tmp_path / "config.jsonc"
        work_path = _platform_path_str("/work")
        config_path.write_text(f'{{"trusted_folders": ["{work_path}"]}}')

        load_config(str(tmp_path))

        assert get("trusted_folders") == [_platform_path("/work").expanduser()]

    def test_project_config_trusted_folders_expand_from_dot_slash(self, tmp_path):
        """Project config './' trusted_folders entries should expand from project root."""
        from src.jcodemunch_mcp.config import (
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
        project_config.write_text('{"trusted_folders": ["./work"]}')

        load_project_config(str(project_root))

        repo_key = str(project_root.resolve())
        assert get("trusted_folders", repo=repo_key) == [
            (project_root / "work").resolve()
        ]

    def test_project_config_trusted_folders_reject_escape_from_project_root(
        self, tmp_path
    ):
        """Project config './' trusted_folders must not escape the project root."""
        from src.jcodemunch_mcp.config import (
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
        project_config.write_text('{"trusted_folders": ["./../../outside"]}')

        load_project_config(str(project_root))

        repo_key = str(project_root.resolve())
        assert get("trusted_folders", repo=repo_key) == []

    def test_project_config_trusted_folders_allows_normalized_in_project_parent_segments(
        self, tmp_path
    ):
        """Project config './' trusted_folders may use .. as long as the resolved path stays inside the project."""
        from src.jcodemunch_mcp.config import (
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
        project_config.write_text('{"trusted_folders": ["./subdir/../work"]}')

        load_project_config(str(project_root))

        repo_key = str(project_root.resolve())
        assert get("trusted_folders", repo=repo_key) == [
            (project_root / "work").resolve()
        ]

    def test_load_config_raises_for_relative_trusted_folders(self, tmp_path):
        """Non-rooted trusted_folders entries should raise during config load."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG, DEFAULTS

        _GLOBAL_CONFIG.clear()

        config_path = tmp_path / "config.jsonc"
        config_path.write_text('{"trusted_folders": ["relative/path"]}')

        load_config(str(tmp_path))

        assert get("trusted_folders") == DEFAULTS["trusted_folders"]

    def test_project_config_dot_resolves_to_project_root(self, tmp_path):
        """Project config '.' should resolve to the project root itself."""
        from src.jcodemunch_mcp.config import (
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
        project_config.write_text('{"trusted_folders": ["."]}')

        load_project_config(str(project_root))

        repo_key = str(project_root.resolve())
        assert get("trusted_folders", repo=repo_key) == [project_root.resolve()]

    def test_project_config_implicit_relative_path_resolves(self, tmp_path):
        """Project config 'work' (without ./ prefix) should resolve relative to project."""
        from src.jcodemunch_mcp.config import (
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
        project_config.write_text('{"trusted_folders": ["work"]}')

        load_project_config(str(project_root))

        repo_key = str(project_root.resolve())
        assert get("trusted_folders", repo=repo_key) == [
            (project_root / "work").resolve()
        ]

    def test_project_config_implicit_relative_escape_rejected(self, tmp_path):
        """Project config '../outside' (without ./ prefix) should be rejected."""
        from src.jcodemunch_mcp.config import (
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
        from src.jcodemunch_mcp.config import (
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
        from src.jcodemunch_mcp.config import (
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
        from src.jcodemunch_mcp.config import (
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

    def test_global_config_deduplicates_trusted_folders(self, tmp_path):
        """Global config should deduplicate identical trusted_folders entries."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        config_path = tmp_path / "config.jsonc"
        work_path = _platform_path_str("/work")
        config_path.write_text(
            f'{{"trusted_folders": ["{work_path}", "{work_path}", "{work_path}"]}}'
        )

        load_config(str(tmp_path))

        result = get("trusted_folders")
        assert len(result) == 1
        assert result[0] == _platform_path("/work").resolve()

    def test_project_config_deduplicates_equivalent_entries(self, tmp_path):
        """Project config should deduplicate equivalent entries like '.' and './'."""
        from src.jcodemunch_mcp.config import (
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
        # ".", "./", "work", "./work" should dedupe to 2 unique paths
        project_config.write_text('{"trusted_folders": [".", "./", "work", "./work"]}')

        load_project_config(str(project_root))

        repo_key = str(project_root.resolve())
        result = get("trusted_folders", repo=repo_key)
        assert len(result) == 2
        assert project_root.resolve() in result
        assert (project_root / "work").resolve() in result

    def test_project_config_deduplicates_absolute_duplicates(self, tmp_path):
        """Project config should deduplicate duplicate absolute paths."""
        from src.jcodemunch_mcp.config import (
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
            f'{{"trusted_folders": ["{work_path}", "{work_path}", "{work_path}"]}}'
        )

        load_project_config(str(project_root))

        repo_key = str(project_root.resolve())
        result = get("trusted_folders", repo=repo_key)
        assert len(result) == 1
        assert _platform_path("/work").resolve() in result


# ── Config file validation ────────────────────────────────────────────────────


class TestConfigValidation:
    """Test validate_config() function in config module."""

    def test_validate_valid_config_returns_empty(self):
        """Should return no issues for a valid config."""
        from src.jcodemunch_mcp.config import validate_config, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": 5000}')
            issues = validate_config(str(config_path))
            assert issues == []

    def test_validate_invalid_json_returns_parse_error(self):
        """Should report JSON parse errors."""
        from src.jcodemunch_mcp.config import validate_config, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": }')  # Invalid JSON
            issues = validate_config(str(config_path))
            assert any("parse" in i.lower() for i in issues)

    def test_validate_type_mismatch_returns_warning(self):
        """Should report type mismatches."""
        from src.jcodemunch_mcp.config import validate_config, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": "not_an_int"}')
            issues = validate_config(str(config_path))
            assert any("type" in i.lower() or "invalid" in i.lower() for i in issues)

    def test_validate_unknown_key_returns_warning(self):
        """Should warn about unknown config keys."""
        from src.jcodemunch_mcp.config import validate_config, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": 5000, "unknown_key": true}')
            issues = validate_config(str(config_path))
            assert any("unknown" in i.lower() for i in issues)

    def test_validate_missing_file_returns_error(self):
        """Should report when config file is missing."""
        from src.jcodemunch_mcp.config import validate_config, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "nonexistent.jsonc"
            issues = validate_config(str(missing))
            assert any("not found" in i.lower() for i in issues)


class TestServerConfigCheck:
    """Test that `config --check` validates the config file."""

    def test_run_config_check_reports_config_parse_error(self, capsys, monkeypatch):
        """Should report config file parse errors in --check output."""
        from src.jcodemunch_mcp.config import _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": }')  # Invalid JSON

            monkeypatch.setenv("CODE_INDEX_PATH", tmpdir)

            from src.jcodemunch_mcp.server import _run_config
            with pytest.raises(SystemExit) as exc_info:
                _run_config(check=True)
            assert exc_info.value.code == 1

            captured = capsys.readouterr().out
            # Should mention config.jsonc and parse error
            assert "config" in captured.lower()
            assert "parse" in captured.lower()

    def test_run_config_check_reports_type_error(self, capsys, monkeypatch):
        """Should report config type errors in --check output."""
        from src.jcodemunch_mcp.config import _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": "wrong_type"}')

            monkeypatch.setenv("CODE_INDEX_PATH", tmpdir)

            from src.jcodemunch_mcp.server import _run_config
            with pytest.raises(SystemExit) as exc_info:
                _run_config(check=True)
            assert exc_info.value.code == 1

            captured = capsys.readouterr().out
            assert "max_folder_files" in captured.lower()
            assert "type" in captured.lower()

    def test_run_config_check_passes_for_valid_config(self, capsys, monkeypatch):
        """Should pass checks when config is valid."""
        from src.jcodemunch_mcp.config import _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": 5000}')

            monkeypatch.setenv("CODE_INDEX_PATH", tmpdir)

            from src.jcodemunch_mcp.server import _run_config
            _run_config(check=True)

            captured = capsys.readouterr().out
            # Should NOT mention config errors
            assert "config error" not in captured.lower()
            assert "parse error" not in captured.lower()


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
@pytest.mark.filterwarnings("ignore::RuntimeWarning")
class TestLoadConfigWiredIntoMain:
    """Test that load_config() is called during server startup."""

    @pytest.mark.asyncio
    async def test_main_calls_load_config_for_serve_command(self, monkeypatch, tmp_path):
        """load_config should be called when serve subcommand starts."""
        from src.jcodemunch_mcp.config import _GLOBAL_CONFIG, DEFAULTS

        _GLOBAL_CONFIG.clear()
        _GLOBAL_CONFIG.update(DEFAULTS)

        # Create a temp config with a distinctive value
        config_path = tmp_path / "config.jsonc"
        config_path.write_text('{"max_folder_files": 9999}')
        monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))

        # Track whether load_config was called
        call_count = 0

        # Import fresh — need to get the original reference before patching
        import src.jcodemunch_mcp.config as cfg_module
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
        import src.jcodemunch_mcp.server as server_module
        async def fake_server_run(*args, **kwargs):
            pass
        monkeypatch.setattr(server_module.server, "run", fake_server_run)

        from src.jcodemunch_mcp.server import main
        main(["serve"])

        # After main() runs, config should reflect the file (not just defaults)
        assert cfg_module.get("max_folder_files") == 9999
        assert call_count >= 1, "load_config should have been called during serve"

    @pytest.mark.asyncio
    async def test_config_loaded_before_list_tools(self, monkeypatch, tmp_path):
        """After main() starts, config should be loaded and usable by list_tools."""
        # Use the SAME module object that server.py imports
        import src.jcodemunch_mcp.config as cfg_module
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

        import src.jcodemunch_mcp.server as server_module
        async def fake_server_run(*args, **kwargs):
            pass
        monkeypatch.setattr(server_module.server, "run", fake_server_run)

        from src.jcodemunch_mcp.server import main
        main(["serve"])

        # After main() runs, config should reflect the file (not just defaults)
        assert cfg_module.get("max_folder_files") == 7777

    def test_load_all_project_configs_called_at_startup(self, monkeypatch, tmp_path):
        """Server startup should call load_all_project_configs()."""
        from src.jcodemunch_mcp import config as config_module

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
            from src.jcodemunch_mcp.server import main
            main([])
        finally:
            sys.argv = old_argv

        assert len(load_calls) >= 1, "load_config should be called"
        assert len(load_all_calls) >= 1, "load_all_project_configs should be called"


# ── Env Var List Comma-Separated Fallback Test (E4) ───────────────────────────────


def test_parse_env_value_list_comma_separated_fallback():
    """_parse_env_value list type falls back to comma-separated on parse failure (E4)."""
    from src.jcodemunch_mcp.config import _parse_env_value

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


# ── Comprehensive Config File Edge Case Tests ────────────────────────────────────


class TestJSONCSyntaxErrors:
    """Test JSONC parser handles malformed syntax gracefully."""

    def test_unclosed_string_returns_invalid_json(self):
        """Unclosed string should produce invalid JSON (parser doesn't fix it)."""
        text = '{"key": "unclosed string}'
        result = _strip_jsonc(text)
        # Should not crash, but result won't be valid JSON
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

    def test_missing_closing_brace(self):
        """Missing closing brace should produce invalid JSON."""
        text = '{"key": "value"'
        result = _strip_jsonc(text)
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)

    def test_missing_closing_bracket(self):
        """Missing closing bracket should produce invalid JSON."""
        text = '{"arr": [1, 2, 3'
        result = _strip_jsonc(text)
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)

    def test_trailing_comma_without_closing_brace(self):
        """Trailing comma without closing brace is still invalid JSON."""
        text = '{"key": "value",'
        result = _strip_jsonc(text)
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)

    def test_duplicate_keys_allowed(self):
        """JSON allows duplicate keys (last wins) - verify our parser doesn't break."""
        text = '{"key": "first", "key": "second"}'
        result = _strip_jsonc(text)
        parsed = json.loads(result)
        assert parsed["key"] == "second"  # Last value wins


class TestJSONCEdgeCases:
    """Test JSONC parser handles edge cases correctly."""

    def test_empty_file(self):
        """Empty file should fail JSON parse."""
        result = _strip_jsonc("")
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)

    def test_only_comments_no_json(self):
        """File with only comments should fail JSON parse."""
        text = '''// This is just a comment
/* and a block comment */
// More comments'''
        result = _strip_jsonc(text)
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)

    def test_empty_object(self):
        """Empty object should parse."""
        text = '{}'
        result = _strip_jsonc(text)
        parsed = json.loads(result)
        assert parsed == {}

    def test_empty_object_with_comments(self):
        """Empty object with comments should parse."""
        text = '''{
    // This is empty
}'''
        result = _strip_jsonc(text)
        parsed = json.loads(result)
        assert parsed == {}

    def test_unicode_in_strings(self):
        """Unicode characters in strings should be preserved."""
        text = '{"greeting": "Hello 世界 🌍"}'
        result = _strip_jsonc(text)
        parsed = json.loads(result)
        assert parsed["greeting"] == "Hello 世界 🌍"

    def test_newlines_in_strings(self):
        """Escaped newlines in strings should be preserved."""
        text = '{"text": "line1\\nline2\\nline3"}'
        result = _strip_jsonc(text)
        parsed = json.loads(result)
        assert parsed["text"] == "line1\nline2\nline3"

    def test_backslash_in_strings(self):
        """Backslashes in strings should be preserved correctly."""
        text = r'{"path": "C:\\Users\\test\\file.txt"}'
        result = _strip_jsonc(text)
        parsed = json.loads(result)
        assert parsed["path"] == "C:\\Users\\test\\file.txt"

    def test_very_nested_structure(self):
        """Deeply nested structures should parse correctly."""
        text = '{"a": {"b": {"c": {"d": {"e": {"f": "deep"}}}}}}'
        result = _strip_jsonc(text)
        parsed = json.loads(result)
        assert parsed["a"]["b"]["c"]["d"]["e"]["f"] == "deep"

    def test_large_array(self):
        """Large arrays should parse correctly."""
        items = ", ".join(str(i) for i in range(100))
        text = f'{{"arr": [{items}]}}'
        result = _strip_jsonc(text)
        parsed = json.loads(result)
        assert len(parsed["arr"]) == 100

    def test_special_characters_in_strings(self):
        """Special characters in strings should be preserved."""
        # Test tab character (escaped in JSON as \t)
        text = '{"text": "line1\\tline2"}'
        result = _strip_jsonc(text)
        parsed = json.loads(result)
        assert "\t" in parsed["text"]

        # Test quote character (escaped in JSON as \")
        text = '{"text": "say \\"hello\\""}'
        result = _strip_jsonc(text)
        parsed = json.loads(result)
        assert '"' in parsed["text"]

        # Test backslash character (escaped in JSON as \\)
        text = '{"path": "C:\\\\Users\\\\test"}'
        result = _strip_jsonc(text)
        parsed = json.loads(result)
        assert "\\" in parsed["path"]


class TestConfigTypeValidation:
    """Test config type validation for all config keys."""

    def test_bool_type_mismatch_string_true(self):
        """String 'true' should be rejected for bool type."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG
        import logging

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"use_ai_summaries": "true"}')  # String, not bool

            load_config(tmpdir)
            # Should fall back to default (True)
            assert get("use_ai_summaries") is True

    def test_int_type_mismatch_float(self):
        """Float should be rejected for int type."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": 2000.5}')  # Float, not int

            load_config(tmpdir)
            # Should fall back to default
            assert get("max_folder_files") == 2000

    def test_int_type_mismatch_negative(self):
        """Negative int should be accepted (no range validation)."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": -1}')

            load_config(tmpdir)
            # Negative is still a valid int (range validation is elsewhere)
            assert get("max_folder_files") == -1

    def test_list_type_mismatch_object(self):
        """Object should be rejected for list type."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"disabled_tools": {"tool": "name"}}')  # Object, not list

            load_config(tmpdir)
            # Should fall back to default
            assert get("disabled_tools") == []

    def test_dict_type_mismatch_list(self):
        """List should be rejected for dict type."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"extra_extensions": [".lua"]}')  # List, not dict

            load_config(tmpdir)
            # Should fall back to default
            assert get("extra_extensions") == {}

    def test_null_for_optional_type(self):
        """Null should be accepted for optional types (list | None)."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"languages": null}')

            load_config(tmpdir)
            assert get("languages") is None

    def test_empty_string_for_string_type(self):
        """Empty string should be accepted for string type."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"log_level": ""}')

            load_config(tmpdir)
            assert get("log_level") == ""

    def test_meta_fields_dict_type_mismatch(self):
        """meta_fields as dict should fall back to None (all fields)."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"meta_fields": {"invalid": "dict"}}')

            load_config(tmpdir)

            assert get("meta_fields") is None


class TestProjectConfigEdgeCases:
    """Test project-level config edge cases."""

    def test_project_config_invalid_syntax(self):
        """Invalid project config should fall back to global."""
        from src.jcodemunch_mcp.config import (
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
        from src.jcodemunch_mcp.config import (
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
        from src.jcodemunch_mcp.config import (
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
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            # Write UTF-8 BOM + JSON
            config_path.write_bytes(b'\xef\xbb\xbf{"max_folder_files": 5000}')

            load_config(tmpdir)
            assert get("max_folder_files") == 5000

    def test_utf8_with_bom_and_comments(self):
        """UTF-8 BOM with comments should work."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            # Write UTF-8 BOM + JSONC with comments
            config_path.write_bytes(b'\xef\xbb\xbf{"max_folder_files": 5000, // comment\n}')

            load_config(tmpdir)
            assert get("max_folder_files") == 5000


class TestAllConfigKeys:
    """Test that all config keys can be loaded correctly."""

    def test_all_string_keys(self):
        """Test all string-typed config keys."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        string_keys = ["transport", "host", "freshness_mode", "log_level"]

        for key in string_keys:
            _GLOBAL_CONFIG.clear()
            with tempfile.TemporaryDirectory() as tmpdir:
                config_path = Path(tmpdir) / "config.jsonc"
                config_path.write_text(f'{{"{key}": "test_value"}}')
                load_config(tmpdir)
                assert get(key) == "test_value", f"Key {key} failed"

    def test_all_int_keys(self):
        """Test all int-typed config keys."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        int_keys = [
            "max_folder_files", "max_index_files", "staleness_days",
            "max_results", "port", "rate_limit", "watch_debounce_ms",
            "stats_file_interval", "summarizer_concurrency"
        ]

        for key in int_keys:
            _GLOBAL_CONFIG.clear()
            with tempfile.TemporaryDirectory() as tmpdir:
                config_path = Path(tmpdir) / "config.jsonc"
                config_path.write_text(f'{{"{key}": 42}}')
                load_config(tmpdir)
                assert get(key) == 42, f"Key {key} failed"

    def test_all_bool_keys(self):
        """Test all bool-typed config keys."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        bool_keys = [
            "use_ai_summaries", "context_providers", "redact_source_root",
            "share_savings", "allow_remote_summarizer", "watch"
        ]

        for key in bool_keys:
            _GLOBAL_CONFIG.clear()
            with tempfile.TemporaryDirectory() as tmpdir:
                config_path = Path(tmpdir) / "config.jsonc"
                config_path.write_text(f'{{"{key}": false}}')
                load_config(tmpdir)
                assert get(key) is False, f"Key {key} failed"

    def test_all_list_keys(self):
        """Test all list-typed config keys."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        list_keys = ["disabled_tools", "extra_ignore_patterns", "meta_fields"]

        for key in list_keys:
            _GLOBAL_CONFIG.clear()
            with tempfile.TemporaryDirectory() as tmpdir:
                config_path = Path(tmpdir) / "config.jsonc"
                config_path.write_text(f'{{"{key}": ["item1", "item2"]}}')
                load_config(tmpdir)
                assert get(key) == ["item1", "item2"], f"Key {key} failed"

    def test_all_list_keys_trusted_folders(self, tmp_path):
        """trusted_folders requires absolute paths and is normalized on load."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

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
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"languages": ["python", "javascript"]}')
            load_config(tmpdir)
            assert get("languages") == ["python", "javascript"], "Key languages failed"

    def test_all_dict_keys(self):
        """Test all dict-typed config keys."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        dict_keys = ["extra_extensions", "descriptions"]

        for key in dict_keys:
            _GLOBAL_CONFIG.clear()
            with tempfile.TemporaryDirectory() as tmpdir:
                config_path = Path(tmpdir) / "config.jsonc"
                config_path.write_text(f'{{"{key}": {{"nested": "value"}}}}')
                load_config(tmpdir)
                assert get(key) == {"nested": "value"}, f"Key {key} failed"

    def test_all_nullable_keys(self):
        """Test all nullable config keys accept null."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG, CONFIG_TYPES

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
        from src.jcodemunch_mcp.server import main

        storage_path = str(tmp_path)
        monkeypatch.setenv("CODE_INDEX_PATH", storage_path)

        main(["config", "--init"])

        captured = capsys.readouterr()
        assert "Created config template" in captured.out

        config_path = tmp_path / "config.jsonc"
        assert config_path.exists()

        content = config_path.read_text()
        from src.jcodemunch_mcp.config import _strip_jsonc
        stripped = _strip_jsonc(content)
        parsed = json.loads(stripped)
        assert "languages" in parsed

    def test_config_init_refuses_overwrite(self, tmp_path, monkeypatch, capsys):
        """config --init should refuse to overwrite existing file."""
        from src.jcodemunch_mcp.server import main

        storage_path = str(tmp_path)
        monkeypatch.setenv("CODE_INDEX_PATH", storage_path)

        config_path = tmp_path / "config.jsonc"
        config_path.write_text('{"existing": true}')

        main(["config", "--init"])

        captured = capsys.readouterr()
        assert "Config file already exists" in captured.out
        assert "Refusing to overwrite" in captured.out

        assert json.loads(config_path.read_text()) == {"existing": True}
