"""Centralized JSONC config for jcodemunch-mcp."""

import hashlib
import json
import logging
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GLOBAL_CONFIG: dict[str, Any] = {}
_PROJECT_CONFIGS: dict[str, dict[str, Any]] = {}
_PROJECT_CONFIG_HASHES: dict[str, str] = {}
_DEPRECATED_ENV_VARS_LOGGED: set[str] = set()
_CONFIG_LOCK = threading.Lock()
_REPO_PATH_CACHE: dict[str, str] = {}

ENV_VAR_MAPPING = {
    "JCODEMUNCH_USE_AI_SUMMARIES": "use_ai_summaries",
    "JCODEMUNCH_TRUSTED_FOLDERS": "trusted_folders",
    "JCODEMUNCH_TRUSTED_FOLDERS_WHITELIST_MODE": "trusted_folders_whitelist_mode",
    "JCODEMUNCH_MAX_FOLDER_FILES": "max_folder_files",
    "JCODEMUNCH_MAX_INDEX_FILES": "max_index_files",
    "JCODEMUNCH_STALENESS_DAYS": "staleness_days",
    "JCODEMUNCH_MAX_RESULTS": "max_results",
    "JCODEMUNCH_EXTRA_IGNORE_PATTERNS": "extra_ignore_patterns",
    "JCODEMUNCH_EXTRA_EXTENSIONS": "extra_extensions",
    "JCODEMUNCH_CONTEXT_PROVIDERS": "context_providers",
    "JCODEMUNCH_REDACT_SOURCE_ROOT": "redact_source_root",
    "JCODEMUNCH_STATS_FILE_INTERVAL": "stats_file_interval",
    "JCODEMUNCH_SHARE_SAVINGS": "share_savings",
    "JCODEMUNCH_SUMMARIZER_CONCURRENCY": "summarizer_concurrency",
    "JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER": "allow_remote_summarizer",
    "JCODEMUNCH_RATE_LIMIT": "rate_limit",
    "JCODEMUNCH_TRANSPORT": "transport",
    "JCODEMUNCH_HOST": "host",
    "JCODEMUNCH_PORT": "port",
    "JCODEMUNCH_WATCH": "watch",
    "JCODEMUNCH_WATCH_DEBOUNCE_MS": "watch_debounce_ms",
    "JCODEMUNCH_FRESHNESS_MODE": "freshness_mode",
    "JCODEMUNCH_CLAUDE_POLL_INTERVAL": "claude_poll_interval",
    "JCODEMUNCH_LOG_LEVEL": "log_level",
    "JCODEMUNCH_LOG_FILE": "log_file",
    "JCODEMUNCH_PATH_MAP": "path_map",
}

DEFAULTS = {
    "use_ai_summaries": True,
    "trusted_folders": [],
    "trusted_folders_whitelist_mode": True,
    "max_folder_files": 2000,
    "max_index_files": 10000,
    "staleness_days": 7,
    "max_results": 500,
    "extra_ignore_patterns": [],
    "exclude_secret_patterns": [],
    "extra_extensions": {},
    "context_providers": True,
    "meta_fields": None,  # None = all fields
    "languages": None,  # None = all languages
    "disabled_tools": [],
    "descriptions": {},
    "transport": "stdio",
    "host": "127.0.0.1",
    "port": 8901,
    "rate_limit": 0,
    "watch": False,
    "watch_debounce_ms": 2000,
    "freshness_mode": "relaxed",
    "claude_poll_interval": 5.0,
    "log_level": "WARNING",
    "log_file": None,
    "redact_source_root": False,
    "stats_file_interval": 3,
    "share_savings": True,
    "summarizer_concurrency": 4,
    "allow_remote_summarizer": False,
    "path_map": "",
}

CONFIG_TYPES = {
    "use_ai_summaries": bool,
    "trusted_folders": list,
    "trusted_folders_whitelist_mode": bool,
    "max_folder_files": int,
    "max_index_files": int,
    "staleness_days": int,
    "max_results": int,
    "extra_ignore_patterns": list,
    "exclude_secret_patterns": list,
    "extra_extensions": dict,
    "context_providers": bool,
    "meta_fields": (list, type(None)),
    "languages": (list, type(None)),
    "disabled_tools": list,
    "descriptions": dict,
    "transport": str,
    "host": str,
    "port": int,
    "rate_limit": int,
    "watch": bool,
    "watch_debounce_ms": int,
    "freshness_mode": str,
    "claude_poll_interval": float,
    "log_level": str,
    "log_file": (str, type(None)),
    "redact_source_root": bool,
    "stats_file_interval": int,
    "share_savings": bool,
    "summarizer_concurrency": int,
    "allow_remote_summarizer": bool,
    "path_map": str,
}


def _strip_jsonc(text: str) -> str:
    """Strip // and /* */ comments from JSONC, respecting quoted strings.

    Also strips trailing commas (common in JSONC but invalid in JSON).
    """
    result, i, n = [], 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            result.append(ch)
            if ch == '\\' and i + 1 < n:
                result.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
        elif ch == '"':
            in_str = True
            result.append(ch)
            i += 1
        elif ch == '/' and i + 1 < n and text[i + 1] == '/':
            # Line comment — strip trailing comma and spaces from previous content
            if result and result[-1] == ',':
                result.pop()
                while result and result[-1] in (' ', '\t'):
                    result.pop()
            end = text.find('\n', i)
            i = n if end == -1 else end
        elif ch == '/' and i + 1 < n and text[i + 1] == '*':
            # Block comment — skip to */
            end = text.find('*/', i + 2)
            if end == -1:
                i = n
            else:
                end_i = end + 2
                if end_i < n and text[end_i] == ',':
                    # Comma immediately after */ — strip it
                    i = end_i + 1
                elif end_i < n and text[end_i] == '\n':
                    # Newline after */ — strip trailing comma only
                    # Walk back to find the last non-whitespace character
                    j = len(result) - 1
                    while j >= 0 and result[j] in (' ', '\t'):
                        j -= 1
                    if j >= 0 and result[j] == ',':
                        result.pop()  # pop comma only
                    i = end_i
                else:
                    i = end_i
        else:
            result.append(ch)
            i += 1

    output = ''.join(result)
    final = []
    j = 0
    m = len(output)
    while j < m:
        ch = output[j]
        if ch == '"':
            backslash_count = 0
            k = j - 1
            while k >= 0 and output[k] == '\\':
                backslash_count += 1
                k -= 1
            if backslash_count % 2 == 1:
                final.append(ch)
                j += 1
                continue
            final.append(ch)
            j += 1
            while j < m:
                final.append(output[j])
                if output[j] == '"':
                    backslash_count = 0
                    k = j - 1
                    while k >= 0 and output[k] == '\\':
                        backslash_count += 1
                        k -= 1
                    if backslash_count % 2 == 0:
                        j += 1
                        break
                j += 1
        elif ch in ('}', ']'):
            # Strip trailing whitespace and comma before this
            while final and final[-1] in (' ', '\t', '\n', '\r'):
                final.pop()
            if final and final[-1] == ',':
                final.pop()
            final.append(ch)
            j += 1
        else:
            final.append(ch)
            j += 1

    return ''.join(final)


def _validate_type(key: str, value: Any, expected_type: type | tuple) -> bool:
    """Validate value against expected type."""
    if key == "trusted_folders":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    if isinstance(expected_type, tuple):
        return isinstance(value, expected_type)
    return isinstance(value, expected_type)


def load_config(storage_path: str | None = None) -> None:
    """Load global config.jsonc. Called once from main()."""
    global _GLOBAL_CONFIG

    # Determine config path
    if storage_path:
        config_path = Path(storage_path) / "config.jsonc"
    else:
        # Respect CODE_INDEX_PATH env var for config file location
        index_path = os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index"))
        config_path = Path(index_path) / "config.jsonc"

    # Auto-create default config if missing
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        template = generate_template()
        config_path.write_text(template, encoding="utf-8")
        logger.info("Created default config at %s", config_path)

    # Load config
    _explicit_keys: set[str] = set()  # Track keys explicitly set in config file
    if config_path.exists():
        try:
            content = config_path.read_text(encoding="utf-8-sig")  # utf-8-sig handles BOM
            stripped = _strip_jsonc(content)
            loaded = json.loads(stripped)

            # Start with defaults, then overlay valid config values
            _GLOBAL_CONFIG = deepcopy(DEFAULTS)
            for key, value in loaded.items():
                if key in CONFIG_TYPES:
                    if _validate_type(key, value, CONFIG_TYPES[key]):
                        # Special validation for languages list
                        if key == "languages" and isinstance(value, list):
                            from .parser.languages import LANGUAGE_REGISTRY

                            valid_langs = []
                            for lang in value:
                                if lang in LANGUAGE_REGISTRY:
                                    valid_langs.append(lang)
                                else:
                                    logger.warning(
                                        "Config key 'languages' contains unknown language '%s'. "
                                        "Known languages: %s...",
                                        lang,
                                        list(LANGUAGE_REGISTRY.keys())[:5],
                                    )
                            _GLOBAL_CONFIG[key] = valid_langs
                        elif key == "trusted_folders" and isinstance(value, list):
                            valid_folders = set()
                            for folder in value:
                                expanded_folder = Path(folder).expanduser()
                                if expanded_folder.is_absolute():
                                    valid_folders.add(expanded_folder.resolve())
                                else:
                                    raise ValueError(
                                        "Config key 'trusted_folders' contains non-absolute path "
                                        f"'{folder}'"
                                    )
                            _GLOBAL_CONFIG[key] = list(valid_folders)
                        else:
                            _GLOBAL_CONFIG[key] = value
                        _explicit_keys.add(key)  # Track explicitly set keys
                    else:
                        logger.warning(
                            "Config key '%s' has invalid type. "
                            "Expected %s, got %s. Using default.",
                            key,
                            CONFIG_TYPES[key],
                            type(value).__name__,
                        )
                    # Ignore unknown keys silently
        except json.JSONDecodeError as e:
            logger.error("Failed to parse config.jsonc: %s", e)
            _GLOBAL_CONFIG = deepcopy(DEFAULTS)
        except Exception as e:
            logger.error("Failed to load config.jsonc: %s", e)
            _GLOBAL_CONFIG = deepcopy(DEFAULTS)
    else:
        _GLOBAL_CONFIG = DEFAULTS.copy()

    # Apply env var fallback for keys not explicitly set in config
    _apply_env_var_fallback(_explicit_keys)


def _parse_env_value(value: str, expected_type: type | tuple) -> Any:
    """Parse env var string to expected type."""
    try:
        if isinstance(expected_type, tuple):
            for t in expected_type:
                if t == type(None):
                    continue
                parsed = _parse_env_value(value, t)
                if parsed is not None:
                    return parsed
            return None
        if expected_type == bool:
            return value.lower() in ("true", "1", "yes", "on")
        elif expected_type == int:
            return int(value)
        elif expected_type == float:
            return float(value)
        elif expected_type == str:
            return value
        elif expected_type == list:
            try:
                return json.loads(value)
            except (ValueError, json.JSONDecodeError):
                result = []
                for token in value.split(","):
                    token = token.strip()
                    if token:
                        result.append(token)
                return result
        elif expected_type == dict:
            try:
                return json.loads(value)
            except (ValueError, json.JSONDecodeError):
                result = {}
                for token in value.split(","):
                    token = token.strip()
                    if not token or ":" not in token:
                        continue
                    ext, _, lang = token.partition(":")
                    ext = ext.strip()
                    lang = lang.strip()
                    if ext and lang:
                        result[ext] = lang
                return result
        else:
            logger.warning("Unknown config type %s for env var value: %s", expected_type, value)
            return None
    except (ValueError, json.JSONDecodeError):
        logger.warning("Failed to parse env var value: %s", value)
        return None


def _apply_env_var_fallback(explicit_keys: set[str] | None = None) -> None:
    """Apply deprecated env var fallback for keys not explicitly set in config."""
    global _GLOBAL_CONFIG

    if explicit_keys is None:
        explicit_keys = set()

    for env_var, config_key in ENV_VAR_MAPPING.items():
        # Skip if config key was explicitly set in config file
        if config_key in explicit_keys:
            continue

        env_value = os.environ.get(env_var)
        if env_value is not None:
            # Log warning once per var
            if env_var not in _DEPRECATED_ENV_VARS_LOGGED:
                logger.warning(
                    f"Deprecated: Using {env_var} environment variable. "
                    f"This will be removed in v2.0. Use config.jsonc instead."
                )
                _DEPRECATED_ENV_VARS_LOGGED.add(env_var)

            # Parse and apply value
            expected_type = CONFIG_TYPES.get(config_key)
            if expected_type is None:
                continue
            parsed = _parse_env_value(env_value, expected_type)  # type: ignore[arg-type]
            if parsed is not None:
                _GLOBAL_CONFIG[config_key] = parsed


def _resolve_repo_key(repo: str) -> str | None:
    """Resolve a repo identifier to the absolute path key used in _PROJECT_CONFIGS.

    _PROJECT_CONFIGS is keyed by resolved absolute paths (e.g. "D:\\...\\project").
    The 'repo' argument from tool calls may be:
    - An absolute path (already a valid key)
    - A repo identifier like "jcodemunch-mcp" or "local/jcodemunch-mcp-384d867b"

    Returns the resolved key if found, None otherwise.
    """
    if repo in _PROJECT_CONFIGS:
        return repo
    if repo in _REPO_PATH_CACHE:
        cached = _REPO_PATH_CACHE[repo]
        # None = negative cache (unknown repo), str = resolved path
        return cached
    try:
        from .storage.index_store import IndexStore
        storage_path = os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index"))
        store = IndexStore(base_path=storage_path)
        repos = store.list_repos()
        for entry in repos:
            source_root = entry.get("source_root", "")
            if not source_root:
                continue
            resolved = str(Path(source_root).resolve())
            display_name = entry.get("display_name", "")
            repo_name = entry.get("repo", "")
            if display_name:
                _REPO_PATH_CACHE[display_name] = resolved
            if repo_name:
                _REPO_PATH_CACHE[repo_name] = resolved
            if repo == display_name or repo == repo_name or repo == resolved:
                return resolved
    except Exception:
        pass
    # Negative cache: avoids repeated IndexStore scans for unknown identifiers
    _REPO_PATH_CACHE[repo] = None  # type: ignore[assignment]
    return None


def get(key: str, default: Any = None, repo: str | None = None) -> Any:
    """Get config value. If repo is given, uses merged project config."""
    if repo:
        resolved = _resolve_repo_key(repo)
        if resolved and resolved in _PROJECT_CONFIGS:
            return _PROJECT_CONFIGS[resolved].get(key, default)
    return _GLOBAL_CONFIG.get(key, default)


def _content_hash(content: str) -> str:
    """Compute SHA-256 hash of content (first 12 hex chars)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def load_project_config(source_root: str) -> None:
    """Load and cache .jcodemunch.jsonc for a project.

    Uses hash-based caching: if the config file content hasn't changed,
    the cached config is reused. This handles:
    - First-time indexing (no cache)
    - Incremental reindexes (cache hit, no parse)
    - Config file edited (hash changed, reload)
    - File touched but unchanged (hash same, no reload)
    - Index dropped and recreated (cache still valid if file unchanged)

    Thread-safe: uses _CONFIG_LOCK to protect global dict mutations.
    """
    project_config_path = Path(source_root) / ".jcodemunch.jsonc"
    repo_key = str(Path(source_root).resolve())

    if project_config_path.exists():
        try:
            content = project_config_path.read_text(encoding="utf-8-sig")
            content_hash = _content_hash(content)

            with _CONFIG_LOCK:
                if repo_key in _PROJECT_CONFIGS:
                    if _PROJECT_CONFIG_HASHES.get(repo_key) == content_hash:
                        return

            stripped = _strip_jsonc(content)
            project_config = json.loads(stripped)

            with _CONFIG_LOCK:
                merged = deepcopy(_GLOBAL_CONFIG)
                for key, value in project_config.items():
                    if key in CONFIG_TYPES:
                        if _validate_type(key, value, CONFIG_TYPES[key]):
                            if key == "trusted_folders" and isinstance(value, list):
                                valid_folders = set()
                                project_root = Path(source_root).resolve()
                                for folder in value:
                                    if folder == "." or folder == "./":
                                        expanded_folder = project_root
                                    elif folder.startswith("./"):
                                        expanded_folder = (
                                            (project_root / folder[2:])
                                            .expanduser()
                                            .resolve()
                                        )
                                        if (
                                            expanded_folder != project_root
                                            and project_root
                                            not in expanded_folder.parents
                                        ):
                                            raise ValueError(
                                                "Project config key 'trusted_folders' entry escapes project root "
                                                f"'{folder}'"
                                            )
                                    elif not Path(folder).expanduser().is_absolute():
                                        expanded_folder = (
                                            (project_root / folder)
                                            .expanduser()
                                            .resolve()
                                        )
                                        if (
                                            expanded_folder != project_root
                                            and project_root
                                            not in expanded_folder.parents
                                        ):
                                            raise ValueError(
                                                "Project config key 'trusted_folders' entry escapes project root "
                                                f"'{folder}'"
                                            )
                                    else:
                                        expanded_folder = (
                                            Path(folder).expanduser().resolve()
                                        )
                                    valid_folders.add(expanded_folder)
                                merged[key] = list(valid_folders)
                            else:
                                merged[key] = value
                        else:
                            logger.warning(
                                "Project config key '%s' has invalid type. Using global default.",
                                key,
                            )
                _PROJECT_CONFIGS[repo_key] = merged
                _PROJECT_CONFIG_HASHES[repo_key] = content_hash
        except Exception as e:
            logger.warning("Failed to load project config: %s", e)
            with _CONFIG_LOCK:
                _PROJECT_CONFIGS[repo_key] = deepcopy(_GLOBAL_CONFIG)
    else:
        with _CONFIG_LOCK:
            if repo_key not in _PROJECT_CONFIGS:
                _PROJECT_CONFIGS[repo_key] = deepcopy(_GLOBAL_CONFIG)
            _PROJECT_CONFIG_HASHES.pop(repo_key, None)


def _list_repos_for_config() -> list[dict]:
    """Get list of indexed repos for project config loading.

    Deferred import to avoid circular dependency at module load time.
    """
    from .storage.index_store import IndexStore
    storage_path = os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index"))
    store = IndexStore(base_path=storage_path)
    return store.list_repos()


def load_all_project_configs() -> None:
    """Load project configs for all already-indexed local repos.

    Called once at server startup after load_config(). Discovers all indexed
    local repos via list_repos() and loads their .jcodemunch.jsonc files.
    Remote repos (empty source_root) are skipped.
    """
    if not _GLOBAL_CONFIG:
        return

    try:
        repos = _list_repos_for_config()
        for repo_entry in repos:
            source_root = repo_entry.get("source_root", "")
            if not source_root:
                continue
            repo_key = str(Path(source_root).resolve())
            if repo_key not in _PROJECT_CONFIGS:
                load_project_config(source_root)
    except Exception as e:
        logger.warning("Failed to load project configs at startup: %s", e)


def is_tool_disabled(tool_name: str, repo: str | None = None) -> bool:
    """Check if a tool is in disabled_tools."""
    disabled = get("disabled_tools", [], repo=repo)
    return tool_name in disabled


def is_language_enabled(language: str, repo: str | None = None) -> bool:
    """Check if a language is in the languages list."""
    languages = get("languages", None, repo=repo)
    if languages is None:  # None = all enabled
        return True
    return language in languages


def get_descriptions() -> dict:
    """Get the nested descriptions dict."""
    return _GLOBAL_CONFIG.get("descriptions", {})


def validate_config(config_path: str) -> list[str]:
    """Validate a config.jsonc file and return a list of issue messages.

    Returns an empty list if the config is valid.
    Checks:
    - File exists
    - JSONC parses to valid JSON
    - All keys have correct types
    - Unknown keys are flagged (warning, not error)
    """
    issues: list[str] = []
    path = Path(config_path)

    if not path.exists():
        return [f"Config file not found: {config_path}"]

    try:
        content = path.read_text(encoding="utf-8-sig")  # utf-8-sig handles BOM
        stripped = _strip_jsonc(content)
        loaded = json.loads(stripped)
    except json.JSONDecodeError as e:
        return [f"Config parse error: {e}"]

    # Validate types
    for key, value in loaded.items():
        if key in CONFIG_TYPES:
            if not _validate_type(key, value, CONFIG_TYPES[key]):
                expected = CONFIG_TYPES[key]
                type_name = getattr(expected, "__name__", str(expected))
                issues.append(
                    f"Config key '{key}' has invalid type: "
                    f"expected {type_name}, got {type(value).__name__}"
                )
            elif key == "trusted_folders":
                for entry in value:
                    if not Path(entry).expanduser().is_absolute():
                        issues.append(
                            f"trusted_folders entry '{entry}' must be an absolute path"
                        )
        else:
            issues.append(f"Config key '{key}' is not recognized (unknown key)")

    return issues


def generate_template() -> str:
    """Return default config.jsonc content."""
    from .parser.languages import LANGUAGE_REGISTRY

    languages_list = list(LANGUAGE_REGISTRY.keys())
    lang_str = ", ".join(f'"{lang}"' for lang in languages_list)

    # All available tools (for disabled_tools reference)
    all_tools = [
        "check_references",
        "find_importers",
        "find_references",
        "get_blast_radius",
        "get_class_hierarchy",
        "get_context_bundle",
        "get_dependency_graph",
        "get_file_content",
        "get_file_outline",
        "get_file_tree",
        "get_related_symbols",
        "get_repo_outline",
        "get_session_stats",
        "get_symbol_diff",
        "get_symbol_source",
        "index_file",
        "index_folder",
        "index_repo",
        "invalidate_cache",
        "list_repos",
        "resolve_repo",
        "search_columns",
        "search_symbols",
        "search_text",
        "suggest_queries",
        "wait_for_fresh",
    ]
    tools_str = "\n  // ".join(f'"{t}",' for t in all_tools)

    # All available meta_fields (for template documentation)
    meta_fields_list = [
        "timing_ms", "powered_by", "index_stale", "reindex_in_progress",
        "stale_since_ms", "reindex_error", "reindex_failures",
        "candidates_scored", "token_budget", "tokens_used", "tokens_remaining",
    ]
    # Commented-out example for meta_fields section in template (each field on its own line)
    meta_str = "\n  //   ".join(f'"{mf}",' for mf in meta_fields_list)

    return f'''// jcodemunch-mcp configuration
// Global: ~/.code-index/config.jsonc
// Project: {{project_root}}/.jcodemunch.jsonc (optional, overrides global)
//
// All values below show defaults. Uncomment to override.
// Env vars still work as fallback but are deprecated.
{{
  // === Indexing ===
  // "use_ai_summaries": true,
  // "trusted_folders": [],
  // "max_folder_files": 2000,
  // "max_index_files": 10000,
  // "staleness_days": 7,
  // "max_results": 500,
  // "extra_ignore_patterns": [],
  // "extra_extensions": {{}},
  // "context_providers": true,

  // === Meta Response Control ===
  // Allowlist of _meta fields to include in responses.
  // Empty list = no _meta at all (maximum token savings).
  // Absent/null = all fields included (backward compatible default).
  // Uncomment and set to a list of field names to include only those fields.
  // Available fields:{meta_str}
  // "meta_fields": [
  //   "timing_ms",
  //   "powered_by"
  // ],
  "meta_fields": null,

  // === Languages ===
  // All supported languages. Comment out to disable a language
  // and its dependent features (e.g. "sql" disables dbt parsing
  // and search_columns tool).
  "languages": [{lang_str}],

  // === Disabled Tools ===
  // Global: tools listed here are removed from the schema entirely.
  // Project: tools listed here are rejected at call_tool() with an
  //   explanatory error (schema is global, can't be changed per-project).
  // Default: empty (all tools enabled). Uncomment to disable specific tools.
  "disabled_tools": [
  // {tools_str}
  ],

  // === Descriptions ===
  // Append text to shortened tool/param descriptions.
  // Empty string = use hardcoded minimal base only.
  // _tool = tool-level description, other keys = param names.
  // _shared applies across all tools (tool-specific overrides _shared).
  // Tools not listed here keep their full current descriptions unchanged.
  "descriptions": {{
    // === Example: Uncomment to enable ===
    // "search_symbols": {{
    //   "_tool": "",
    //   "debug": "",
    //   "detail_level": "",
    //   "language": ""
    // }},
    // "find_importers": {{ "_tool": "" }},
    // "find_references": {{ "_tool": "" }},
    // "get_blast_radius": {{ "_tool": "" }},
    // "get_context_bundle": {{ "_tool": "" }},
    // "suggest_queries": {{ "_tool": "" }},
    // "_shared": {{ "repo": "" }}
  }},

  // === Transport ===
  // "transport": "stdio",
  // "host": "127.0.0.1",
  // "port": 8901,
  // "rate_limit": 0,

  // === Watcher ===
  // "watch": false,
  // "watch_debounce_ms": 2000,
  // "freshness_mode": "relaxed",
  // "claude_poll_interval": 5.0,

  // === Logging ===
  // "log_level": "WARNING",
  // "log_file": null,

  // === Privacy & Telemetry ===
  // "redact_source_root": false,
  // "stats_file_interval": 3,
  // "share_savings": true,
  // "summarizer_concurrency": 4,
  // "allow_remote_summarizer": false
}}
'''
