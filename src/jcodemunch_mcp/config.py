"""Centralized JSONC config for jcodemunch-mcp."""

import hashlib
import json
import logging
import os
import re
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
    "JCODEMUNCH_FILE_TREE_MAX_FILES": "file_tree_max_files",
    "JCODEMUNCH_GITIGNORE_WARN_THRESHOLD": "gitignore_warn_threshold",
    "JCODEMUNCH_EXTRA_IGNORE_PATTERNS": "extra_ignore_patterns",
    "JCODEMUNCH_EXTRA_EXTENSIONS": "extra_extensions",
    "JCODEMUNCH_CONTEXT_PROVIDERS": "context_providers",
    "JCODEMUNCH_REDACT_SOURCE_ROOT": "redact_source_root",
    "JCODEMUNCH_STATS_FILE_INTERVAL": "stats_file_interval",
    "JCODEMUNCH_SHARE_SAVINGS": "share_savings",
    "JCODEMUNCH_SUMMARIZER_CONCURRENCY": "summarizer_concurrency",
    "JCODEMUNCH_SUMMARIZER_MAX_FAILURES": "summarizer_max_failures",
    "JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER": "allow_remote_summarizer",
    "JCODEMUNCH_RATE_LIMIT": "rate_limit",
    "JCODEMUNCH_TRANSPORT": "transport",
    "JCODEMUNCH_HOST": "host",
    "JCODEMUNCH_PORT": "port",
    "JCODEMUNCH_WATCH": "watch",
    "JCODEMUNCH_WATCH_DEBOUNCE_MS": "watch_debounce_ms",
    "JCODEMUNCH_WATCH_EXTRA_IGNORE": "watch_extra_ignore",
    "JCODEMUNCH_WATCH_FOLLOW_SYMLINKS": "watch_follow_symlinks",
    "JCODEMUNCH_WATCH_IDLE_TIMEOUT": "watch_idle_timeout",
    "JCODEMUNCH_WATCH_LOG": "watch_log",
    "JCODEMUNCH_WATCH_PATHS": "watch_paths",
    "JCODEMUNCH_FRESHNESS_MODE": "freshness_mode",
    "JCODEMUNCH_SUMMARIZER_PROVIDER": "summarizer_provider",
    "JCODEMUNCH_SUMMARIZER_MODEL": "summarizer_model",
    "JCODEMUNCH_EMBED_MODEL": "embed_model",
    "JCODEMUNCH_CLAUDE_POLL_INTERVAL": "claude_poll_interval",
    "JCODEMUNCH_LOG_LEVEL": "log_level",
    "JCODEMUNCH_LOG_FILE": "log_file",
    "JCODEMUNCH_PATH_MAP": "path_map",
    "JCODEMUNCH_TRUSTED_FOLDERS_ENV": "trusted_folders",
    "JCODEMUNCH_CROSS_REPO_DEFAULT": "cross_repo_default",
}


def _global_config_path() -> Path:
    """Return the path to the global config.jsonc."""
    storage = os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index"))
    return Path(storage) / "config.jsonc"


def _global_storage_path() -> Path:
    """Return the global storage directory path."""
    return Path(os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index")))


_LANG_BLOCK_RE = re.compile(
    r'("languages"\s*:\s*)(\[.*?\]|null)',
    re.DOTALL
)
# NOTE: The non-greedy \[.*?\] pattern will break if a ] character appears
# inside a comment within the languages block (e.g., // see note [1]).
# This cannot happen with auto-generated content but is a limitation for
# hand-edited configs containing such patterns.


def _parse_active_languages(content: str) -> set[str] | None:
    """Extract uncommented language names from the languages array in JSONC content.

    Returns:
        set of active language names, or
        None if the languages key is null or absent (meaning "all languages").
    """
    m = _LANG_BLOCK_RE.search(content)
    if not m:
        return None
    block = m.group(2)
    if block.strip() == "null":
        return None
    active = set()
    for line in block.splitlines():
        # Strip inline // comments before matching (handle "python", // comment style)
        code_part = line.split("//")[0]
        code_stripped = code_part.strip()
        if code_stripped.startswith("//"):
            continue
        for lang_m in re.finditer(r'"([a-z_+#]+)"', code_stripped):
            active.add(lang_m.group(1))
    return active


def _build_languages_block(detected: set[str]) -> str:
    """Build a languages array block with detected languages uncommented."""
    from .parser.languages import LANGUAGE_REGISTRY
    all_langs = sorted(LANGUAGE_REGISTRY.keys())
    lines = []
    for lang in all_langs:
        if lang in detected:
            lines.append(f'     "{lang}",')
        else:
            lines.append(f'     // "{lang}",')
    return '"languages": [\n' + '\n'.join(lines) + '\n  ]'


def invalidate_project_config_cache(source_root: str) -> None:
    """Evict source_root from the project config cache, forcing reload on next access."""
    resolved = str(Path(source_root).resolve())
    with _CONFIG_LOCK:
        _PROJECT_CONFIGS.pop(resolved, None)
        _PROJECT_CONFIG_HASHES.pop(resolved, None)


def _check_raw_local_adaptive(local_path: Path) -> tuple[bool, str]:
    """Check if languages_adaptive is True in the raw (unmerged) local config file.

    Reads and parses the JSONC file directly — does NOT use the merged
    _PROJECT_CONFIGS cache, because the user requires that when a local
    config exists, ONLY the local file's languages_adaptive value matters
    (absent = False, not inherited from global).

    Returns:
        Tuple of (is_adaptive, content) — content is the raw file text.
    """
    try:
        content = local_path.read_text(encoding="utf-8-sig")
        raw = json.loads(_strip_jsonc(content))
        return bool(raw.get("languages_adaptive", False)), content
    except (json.JSONDecodeError, ValueError, OSError):
        return False, ""


def _apply_languages_adaptation(content: str, detected: set[str]) -> str | None:
    """Apply language adaptation to content, replacing the languages block.

    Returns the adapted content, or None if no languages block exists to adapt.

    Note: The regex uses non-greedy matching which may break if a ] character
    appears inside a comment within the languages block (e.g., // see note [1]).
    This cannot happen with auto-generated content but is a limitation for
    hand-edited configs.
    """
    active = _parse_active_languages(content)
    # active is None when languages key is null/absent → always update (convert to array)
    if active is not None and active == detected:
        return None  # no change needed

    new_block = _build_languages_block(detected)
    m = _LANG_BLOCK_RE.search(content)
    if not m:
        logger.debug("No languages block found — cannot apply adaptation")
        return None

    new_content = content[:m.start()] + new_block + content[m.end():]
    return new_content


def apply_adaptive_languages(source_root: str, detected: set[str]) -> bool:
    """Apply adaptive language configuration to {source_root}/.jcodemunch.jsonc.

    Decision tree:
      No local config + global languages_adaptive=True  → create from global copy + adapt
      Local config   + raw local languages_adaptive=True → surgical update
      Otherwise                                          → no-op

    Returns True if the file was created or modified.
    """
    if not detected:
        return False

    local_path = Path(source_root) / ".jcodemunch.jsonc"
    created = False

    if not local_path.exists():
        # ─── Stage 1: no local config — check global ─────────────────────────
        if not _GLOBAL_CONFIG.get("languages_adaptive", False):
            return False
        global_path = _global_config_path()
        if global_path.exists():
            content = global_path.read_text(encoding="utf-8")
        else:
            content = generate_template()
        # Ensure languages_adaptive: true is written to the new local config
        # Handle both commented-out (// "languages_adaptive": false,) and active keys
        lines = content.splitlines()
        new_lines = []
        key_found = False
        for line in lines:
            if '"languages_adaptive"' in line:
                # Replace any version of this line (commented or not)
                new_lines.append('  "languages_adaptive": true,')
                key_found = True
            else:
                new_lines.append(line)
        if not key_found:
            # Insert after opening brace line
            final_lines = []
            for line in new_lines:
                final_lines.append(line)
                if line.strip() == "{":
                    final_lines.append('  "languages_adaptive": true,')
            new_lines = final_lines
        content = "\n".join(new_lines)

        # Apply language adaptation BEFORE the first write (avoids double-write)
        adapted = _apply_languages_adaptation(content, detected)
        if adapted is not None:
            content = adapted
        # Always write in Stage 1 — the file doesn't exist yet and needs
        # languages_adaptive: true at minimum, even if languages already match.
        local_path.write_text(content, encoding="utf-8")
        invalidate_project_config_cache(source_root)
        logger.info("Created project config from global: %s", local_path)
        return True
    else:
        # ─── Stage 2: local config exists — check RAW local value ─────────────
        is_adaptive, content = _check_raw_local_adaptive(local_path)
        if not is_adaptive:
            return False
        # content is already loaded — no second read needed

    # ─── Apply adaptation ────────────────────────────────────────────────────────
    new_content = _apply_languages_adaptation(content, detected)
    if new_content is None:
        return False  # no change needed or no block to adapt

    if new_content == content:
        return False

    local_path.write_text(new_content, encoding="utf-8")
    invalidate_project_config_cache(source_root)
    logger.info("Adaptive languages: %s → %s", local_path, sorted(detected))
    return True

DEFAULTS = {
    "use_ai_summaries": "auto",
    "trusted_folders": [],
    "trusted_folders_whitelist_mode": True,
    "max_folder_files": 2000,
    "max_index_files": 10000,
    "staleness_days": 7,
    "max_results": 500,
    "file_tree_max_files": 500,
    "gitignore_warn_threshold": 500,
    "extra_ignore_patterns": [],
    "exclude_secret_patterns": [],
    "exclude_skip_directories": [],
    "extra_extensions": {},
    "context_providers": True,
    "meta_fields": [],  # [] = no _meta (token-efficient; set null in config for all fields)
    "languages": None,  # None = all languages
    "languages_adaptive": False,
    "disabled_tools": ["test_summarizer"],
    "descriptions": {},
    "transport": "stdio",
    "host": "127.0.0.1",
    "port": 8901,
    "rate_limit": 0,
    "watch": False,
    "watch_debounce_ms": 2000,
    "watch_extra_ignore": [],
    "watch_follow_symlinks": False,
    "watch_idle_timeout": None,
    "watch_log": None,
    "watch_paths": [],
    "freshness_mode": "relaxed",
    "strict_timeout_ms": 500,
    "summarizer_provider": "",
    "summarizer_model": "",
    "embed_model": "",
    "claude_poll_interval": 5.0,
    "log_level": "WARNING",
    "log_file": None,
    "redact_source_root": False,
    "stats_file_interval": 3,
    "share_savings": True,
    "summarizer_concurrency": 4,
    "summarizer_max_failures": 3,
    "allow_remote_summarizer": False,
    "path_map": "",
    "cross_repo_default": False,
    "discovery_hint": True,
    # Session-aware routing (Feature 6)
    "negative_evidence_threshold": 0.5,
    "search_result_cache_max": 128,
    "session_journal": True,
    "plan_turn_high_threshold": 2.0,
    "plan_turn_medium_threshold": 0.5,
    "turn_budget_tokens": 20000,
    "turn_gap_seconds": 30.0,
    "session_resume": False,
    "session_max_age_minutes": 30,
    "session_max_queries": 50,
}

CONFIG_TYPES = {
    "use_ai_summaries": (bool, str),
    "trusted_folders": list,
    "trusted_folders_whitelist_mode": bool,
    "max_folder_files": int,
    "max_index_files": int,
    "staleness_days": int,
    "max_results": int,
    "file_tree_max_files": int,
    "gitignore_warn_threshold": int,
    "extra_ignore_patterns": list,
    "exclude_secret_patterns": list,
    "exclude_skip_directories": list,
    "extra_extensions": dict,
    "context_providers": bool,
    "meta_fields": (list, type(None)),
    "languages": (list, type(None)),
    "languages_adaptive": bool,
    "disabled_tools": list,
    "descriptions": dict,
    "transport": str,
    "host": str,
    "port": int,
    "rate_limit": int,
    "watch": bool,
    "watch_debounce_ms": int,
    "watch_extra_ignore": list,
    "watch_follow_symlinks": bool,
    "watch_idle_timeout": (int, type(None)),
    "watch_log": (str, type(None)),
    "watch_paths": list,
    "freshness_mode": str,
    "strict_timeout_ms": int,
    "summarizer_provider": str,
    "summarizer_model": str,
    "embed_model": str,
    "claude_poll_interval": float,
    "log_level": str,
    "log_file": (str, type(None)),
    "redact_source_root": bool,
    "stats_file_interval": int,
    "share_savings": bool,
    "summarizer_concurrency": int,
    "summarizer_max_failures": int,
    "allow_remote_summarizer": bool,
    "path_map": str,
    "cross_repo_default": bool,
    "discovery_hint": bool,
    "version": str,
    "architecture": dict,
    # Session-aware routing
    "negative_evidence_threshold": float,
    "search_result_cache_max": int,
    "session_journal": bool,
    "plan_turn_high_threshold": float,
    "plan_turn_medium_threshold": float,
    "turn_budget_tokens": int,
    "turn_gap_seconds": float,
    "session_resume": bool,
    "session_max_age_minutes": int,
    "session_max_queries": int,
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
    if key == "use_ai_summaries":
        if isinstance(value, bool):
            return True
        if isinstance(value, str):
            return value.lower() in {"true", "false", "auto"}
        return False
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
        config_path = _global_config_path()

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


def _parse_env_value(value: str, expected_type: type | tuple, key: str | None = None) -> Any:
    """Parse env var string to expected type."""
    # use_ai_summaries accepts "auto", "true", "false" as strings;
    # generic bool parsing would coerce "auto" to False.
    if key == "use_ai_summaries":
        return value.strip().lower()
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
            parsed = _parse_env_value(env_value, expected_type, key=config_key)  # type: ignore[arg-type]
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
    with _CONFIG_LOCK:
        if repo in _PROJECT_CONFIGS:
            return repo
        if repo in _REPO_PATH_CACHE:
            cached = _REPO_PATH_CACHE[repo]
            # None = negative cache (unknown repo), str = resolved path
            return cached

    # Miss: query store without holding the lock (I/O)
    try:
        from .storage.index_store import IndexStore
        store = IndexStore(base_path=str(_global_storage_path()))
        repos = store.list_repos()
        result = None
        updates: dict[str, str] = {}
        for entry in repos:
            source_root = entry.get("source_root", "")
            if not source_root:
                continue
            resolved = str(Path(source_root).resolve())
            display_name = entry.get("display_name", "")
            repo_name = entry.get("repo", "")
            if display_name:
                updates[display_name] = resolved
            if repo_name:
                updates[repo_name] = resolved
            if repo == display_name or repo == repo_name or repo == resolved:
                result = resolved
        with _CONFIG_LOCK:
            _REPO_PATH_CACHE.update(updates)
            # Prevent unbounded growth (evict oldest entries first)
            if len(_REPO_PATH_CACHE) > 512:
                excess = len(_REPO_PATH_CACHE) - 512
                for k in list(_REPO_PATH_CACHE)[:excess]:
                    del _REPO_PATH_CACHE[k]
        return result
    except Exception:
        pass
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
                if key == "use_ai_summaries":
                    issues.append(
                        f"Config key 'use_ai_summaries' has invalid value {value!r}: "
                        f'expected one of: "auto", "true", "false" (or boolean true/false)'
                    )
                else:
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


def _extract_template_keys(template: str) -> list[str]:
    """Return top-level key names that appear in the template (active or commented-out).

    Only matches keys at the top level of the JSONC object (exactly 2 spaces of
    indentation), not nested keys inside objects like "descriptions".
    Returns them in order of first appearance.
    """
    import re
    seen: set[str] = set()
    result: list[str] = []
    # Match lines with exactly 2 leading spaces (top-level in the outer {})
    # Handles both active keys and commented-out keys.
    for m in re.finditer(r'^  (?:// *)?\"(\w+)\" *:', template, re.MULTILINE):
        key = m.group(1)
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _extract_section_for_key(template: str, key: str) -> str | None:
    """Extract the comment block + key entry for a given key from the template.

    Returns the block of text (including preceding comment lines) as it appears
    in the template, ready to be appended to an existing config. Returns None if
    the key is not found.
    """
    import re
    lines = template.splitlines()

    # Find the line index where this key appears (active or commented-out)
    key_pattern = re.compile(r'^\s*(?://\s*)?"' + re.escape(key) + r'"\s*:')
    key_line_idx: int | None = None
    for i, line in enumerate(lines):
        if key_pattern.match(line):
            key_line_idx = i
            break

    if key_line_idx is None:
        return None

    # Walk backwards to find the start of the preceding comment block.
    # Stop at blank lines or section-header comments (=== ... ===).
    start_idx = key_line_idx
    for i in range(key_line_idx - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped:
            break
        if stripped.startswith("//"):
            start_idx = i
        else:
            break

    # Walk forwards to capture multi-line values (arrays/objects) or
    # consecutive comment lines after the key.
    end_idx = key_line_idx
    depth = 0
    for i in range(key_line_idx, len(lines)):
        line = lines[i]
        depth += line.count("{") + line.count("[")
        depth -= line.count("}") + line.count("]")
        end_idx = i
        if i >= key_line_idx and depth <= 0:
            break

    block = "\n".join(lines[start_idx : end_idx + 1])
    return block


def upgrade_config(config_path: "Path") -> tuple[list[str], list[str]]:
    """Add missing keys from the current template into an existing config.jsonc.

    Preserves all user values. Only appends keys that are entirely absent
    (neither active nor commented-out) from the existing config.

    Returns:
        (added_keys, warnings) — keys that were injected; warnings if any.
    """
    from . import __version__

    existing_content = config_path.read_text(encoding="utf-8")
    template = generate_template()

    # Determine which keys exist in user's config (active or commented-out)
    existing_keys = set(_extract_template_keys(existing_content))

    # Determine full ordered key list from template
    template_keys = _extract_template_keys(template)

    # Keys to inject: in template but absent from user's config
    missing_keys = [k for k in template_keys if k not in existing_keys]

    added: list[str] = []
    warnings: list[str] = []

    if not missing_keys:
        # Still update version field if present
        _update_version_field(existing_content, __version__, config_path)
        return [], []

    # Collect blocks to append
    blocks_to_append: list[str] = []
    for key in missing_keys:
        block = _extract_section_for_key(template, key)
        if block:
            blocks_to_append.append(block)
            added.append(key)
        else:
            warnings.append(f"Could not extract block for key '{key}' from template")

    if blocks_to_append:
        # Insert before the closing }
        new_content = _inject_blocks_before_closing_brace(
            existing_content, blocks_to_append
        )
        new_content = _update_version_field(new_content, __version__, config_path=None)
        config_path.write_text(new_content, encoding="utf-8")
    else:
        _update_version_field(existing_content, __version__, config_path)

    return added, warnings


def _update_version_field(content: str, version: str, config_path: "Path | None") -> str:
    """Update the version field in config content. Writes to disk if config_path given."""
    import re
    updated = re.sub(
        r'("version"\s*:\s*)"[^"]*"',
        rf'\g<1>"{version}"',
        content,
    )
    if config_path is not None:
        config_path.write_text(updated, encoding="utf-8")
    return updated


def _inject_blocks_before_closing_brace(content: str, blocks: list[str]) -> str:
    """Insert text blocks before the final closing } of a JSONC file.

    Ensures a trailing comma is added after the last existing JSON value so the
    result remains valid JSONC when active-value blocks are appended.
    """
    last_brace = content.rfind("}")
    if last_brace == -1:
        return content + "\n\n" + "\n\n".join(blocks) + "\n"

    before = content[:last_brace]

    # Ensure the last non-blank, non-comment line ends with a comma so the
    # injected blocks (which may contain active keys) form valid JSONC.
    lines = before.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("//"):
            continue
        # This is the last substantive line — add a comma if missing
        if not stripped.endswith(","):
            lines[i] = lines[i].rstrip() + ","
        break
    before = "\n".join(lines)

    separator = "\n\n  // === Added by config --upgrade ===\n"
    injection = separator + "\n\n".join(
        "\n".join("  " + line if line and not line.startswith("  ") else line
                  for line in block.splitlines())
        for block in blocks
    )
    return before + injection + "\n" + content[last_brace:]


def generate_template() -> str:
    """Return default config.jsonc content."""
    from . import __version__
    from .parser.languages import LANGUAGE_REGISTRY

    # Sorted alphabetically for readability - use .sorted() to ensure always sorted
    languages_list = sorted(LANGUAGE_REGISTRY.keys())
    lang_str = "\n  ".join(f'"{lang}",' for lang in languages_list)

    # All available tools (for disabled_tools reference) - sorted alphabetically
    # Removed: wait_for_fresh (v1.12.0 - check_freshness and wait_for_fresh tools removed)
    all_tools = sorted([
        "check_references",
        "embed_repo",
        "find_dead_code",
        "find_importers",
        "find_references",
        "get_blast_radius",
        "get_changed_symbols",
        "get_class_hierarchy",
        "get_context_bundle",
        "get_dependency_graph",
        "get_file_content",
        "get_file_outline",
        "get_file_tree",
        "get_ranked_context",
        "get_related_symbols",
        "get_repo_outline",
        "get_session_stats",
        "get_symbol_diff",
        "get_symbol_importance",
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
        "test_summarizer",
        "plan_turn",
        "register_edit",
        "get_session_context",
    ])
    tools_str = "\n  // ".join(f'"{t}",' for t in all_tools)

    # All available meta_fields (for template documentation)
    # Removed (v1.12.0): index_stale, reindex_in_progress, stale_since_ms,
    #   reindex_error, reindex_failures (staleness fields removed with check_freshness)
    meta_fields_list = sorted([
        "candidates_scored",
        "powered_by",
        "timing_ms",
        "token_budget",
        "tokens_remaining",
        "tokens_used",
    ])
    # Commented-out meta_fields list (each field on its own line, like disabled_tools)
    meta_str = "\n  // ".join(f'"{mf}",' for mf in meta_fields_list)

    return f'''// jcodemunch-mcp configuration
// Global: ~/.code-index/config.jsonc
// Project: {{project_root}}/.jcodemunch.jsonc (optional, overrides global)
//
// All values below show defaults. Uncomment to override.
// Env vars still work as fallback but are deprecated.
{{
  // Config version - do not edit. Used for additive migrations.
  "version": "{__version__}",

  // === Indexing ===
  // "trusted_folders": [],
  //   Directories allowed for indexing when whitelist_mode is true.
  //   In whitelist mode (default), only these folders can be indexed.
  //   In blacklist mode (whitelist_mode=false), these folders are blocked.

  // "trusted_folders_whitelist_mode": true,
  //   true = only trust folders in trusted_folders list (default, secure).
  //   false = trust all folders EXCEPT those in trusted_folders (blocklist mode).

  // "max_folder_files": 2000,
  //   Maximum number of files to index when indexing a local folder.
  //   Prevents accidental massive indexing jobs.

  // "gitignore_warn_threshold": 500,
  //   Emit a warning during index_folder when no root .gitignore is found
  //   and the indexed file count reaches this value. Helps catch accidental
  //   indexing of build artifacts or vendored dependencies before they
  //   bloat the index. Set 0 to disable the warning entirely.

  // "max_index_files": 10000,
  //   Maximum number of files to index when indexing a GitHub repo.
  //   Separate cap from max_folder_files for different use cases.

  // "staleness_days": 7,
  //   Days before an index is considered stale (warning only, no blocking).

  // "max_results": 500,
  //   Maximum number of results returned by search operations.

  // "file_tree_max_files": 500,
  //   Maximum number of files returned by get_file_tree in a single call.
  //   Prevents token overflow on large or bloated indexes. The response
  //   includes a hint to use path_prefix when this cap is hit.
  //   Can also be overridden per-call via the max_files tool parameter.

  // "extra_ignore_patterns": [],
  //   Additional gitignore-style patterns to exclude from indexing.
  //   Merged with JCODEMUNCH_EXTRA_IGNORE_PATTERNS env var.

  // "exclude_secret_patterns": [],
  //   Glob patterns to exclude from *secret* detection.
  //   Use when *secret* has false positives on specific paths.

  // "exclude_skip_directories": [],
  //   Directory names to remove from the built-in skip list.
  //   Example: ["proto"] to index protobuf directories.

  // "extra_extensions": {{}},
  //   Map additional file extensions to languages.
  //   Example: {{".mpl": "cpp"}} to parse .mpl files as C++.

  // "context_providers": true,
  //   Enable context providers for enhanced AI summarization.
  //   Set false to disable (faster indexing, less context).

  // === Meta Response Control ===
  // Allowlist of _meta fields to include in responses.
  // [] (default) = no _meta at all (maximum token savings).
  // null = all fields included (set explicitly to opt in).
  // Uncomment and set to a list of field names to include only those fields.
  // All available meta fields (sorted alphabetically, each on its own line):
  "meta_fields": [
  // {meta_str}
  ],

  // === Languages ===
  // All supported languages. Comment out to disable a language
  // and its dependent features (e.g. "sql" disables dbt parsing
  // and search_columns tool).
  // Each language on its own line (sorted alphabetically):
  "languages": [
     {lang_str}
  ],

  // "languages_adaptive": false,
  //   When true, jcodemunch auto-manages the languages list in this
  //   project's .jcodemunch.jsonc based on detected languages.
  //   Detected languages are uncommented; unused ones are commented out.
  //   Runs on every index_folder call (full and incremental).
  //   Set in global config to auto-create project configs on first index.
  //   Set in project config to enable ongoing adaptation.

  // === Disabled Tools ===
  // Global: tools listed here are removed from the schema entirely.
  // Project: tools listed here are rejected at call_tool() with an
  //   explanatory error (schema is global, can't be changed per-project).
  // Default: test_summarizer disabled. Uncomment others to disable them.
  "disabled_tools": [
    // test_summarizer — diagnostic: sends a probe to the AI summarizer and
    //   reports status (ok, timeout, error, misconfigured, disabled).
    //   Remove from this list to enable it, then call it from your MCP client.
    "test_summarizer",
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
  // Protocol for MCP server communication:
  //   stdio            - Default. Uses stdin/stdout. Works everywhere.
  //   sse              - Server-Sent Events over HTTP. Persistent connection.
  //   streamable-http  - Streamable HTTP. Alternative persistent HTTP mode.
  // When using sse or streamable-http, also set host and port.
  // "transport": "stdio",
  // "host": "127.0.0.1",
  //   Bind address for HTTP transports. Use 0.0.0.0 for all interfaces.
  // "port": 8901,
  //   Port for HTTP transports (sse, streamable-http).
  // "rate_limit": 0,
  //   Max requests per minute per client IP. 0 = disabled (default).

  // === Watcher ===
  // "watch": false,
  //   Enable automatic reindexing when files change.
  //   Use "jcodemunch-mcp watch <paths>" CLI command to activate.
  // "watch_debounce_ms": 2000,
  //   Milliseconds to wait after a file change before reindexing.
  //   Higher values reduce CPU usage but slower detection.
  // "freshness_mode": "relaxed",
  //   relaxed - Default. Index remains queryable during reindex.
  //             Best for interactive use (IDE, chat).
  //   strict  - Blocks queries until fresh index is ready.
  //             Best for automation/CI where consistency matters.
  // "strict_timeout_ms": 500,
  //   Maximum milliseconds to block queries waiting for a reindex in strict mode.
  //   After this timeout the query proceeds with the stale index.
  //   Only applies when freshness_mode is "strict". Default: 500.
  // "claude_poll_interval": 5.0,
  //   Seconds between polling Claude Code worktrees for changes.

  // === Logging ===
  // "log_level": "WARNING",
  //   DEBUG, INFO, WARNING, ERROR, CRITICAL. WARNING is default for less noise.
  // "log_file": null,
  //   Path to log file. null = write to stderr.

  // === Privacy & Telemetry ===
  // "redact_source_root": false,
  //   Replace absolute source_root paths with display_name in responses.
  //   Set true to hide project paths from clients.
  // "stats_file_interval": 3,
  //   Write session_stats.json every N tool calls. 0 = disable writes.
  //   Lower values = more disk I/O but faster stats for external consumers.
  // "share_savings": true,
  //   Enable anonymous token savings telemetry (helps project funding).
  //   Set false/0 to disable.
  // "summarizer_concurrency": 4,
  //   Number of parallel threads for AI summarization.
  //   Higher = faster indexing but more API calls.
  // "summarizer_max_failures": 3,
  //   Consecutive batch failures before the AI summarizer gives up and
  //   falls back to signature summaries for remaining symbols.
  //   Set 0 to disable the circuit breaker (never stop retrying).

  // === Session-Aware Routing ===
  // "negative_evidence_threshold": 0.5,
  //   BM25 score threshold for negative evidence in search_symbols.
  //   When the best match score is below this, the response includes
  //   structured negative_evidence to prevent AI hallucination.
  // "search_result_cache_max": 128,
  //   Maximum entries in the search_symbols result cache. 0 = disable cache.
  // "session_journal": true,
  //   Track file reads, searches, and edits during the MCP session.
  //   Disable to reduce memory usage in long-running sessions.
  // "plan_turn_high_threshold": 2.0,
  //   Minimum BM25 score for plan_turn to report "high" confidence.
  // "plan_turn_medium_threshold": 0.5,
  //   Minimum BM25 score for plan_turn to report "medium" confidence.
  // "turn_budget_tokens": 20000,
  //   Max tokens returned across all tool calls in a turn. 0 = disabled.
  // "turn_gap_seconds": 30.0,
  //   Seconds of silence before a new "turn" begins (heuristic).
  // "session_resume": false,
  //   Persist and restore session state (journal, cache) across restarts.
  //   Writes only on clean shutdown (NVME-friendly). State validated
  //   against git HEAD (or indexed_at for non-Git projects) on restore.
  // "session_max_age_minutes": 30,
  //   Discard saved session state older than this.
  // "session_max_queries": 50,
  //   Cap on persisted search cache entries.

  // === AI Summarizer ===
  // Controls whether AI is used to generate symbol summaries during indexing.
  //   "auto"  — auto-detect provider from API key env vars (default behavior)
  //   true    — use the summarizer_provider and summarizer_model values below
  //   false   — disable AI summarization entirely (signature fallback only)
  // "use_ai_summaries": "auto",

  // AI summarizer provider to use when use_ai_summaries is true.
  // Valid values: "anthropic", "gemini", "openai", "minimax", "glm", "openrouter", "none"
  // Leave empty ("") to auto-detect from available API keys.
  // "summarizer_provider": "",

  // Model name to use for the selected summarizer provider.
  // Leave empty ("") to use the provider's default model.
  // Examples: "claude-haiku-4-5-20251001" (anthropic), "gemini-2.5-flash-lite" (gemini),
  //           "gpt-4o-mini" (openai), "minimax-m2.7" (minimax), "glm-5" (glm),
  //           "meta-llama/llama-3.3-70b-instruct:free" (openrouter)
  // "summarizer_model": "",
  // "embed_model": "",
  //   Sentence-transformers model name for local (free) semantic embeddings.
  //   Example: "all-MiniLM-L6-v2". Requires sentence-transformers package.
  //   When set, takes priority over GOOGLE_API_KEY and OPENAI_API_KEY embeddings.
  // "allow_remote_summarizer": false,
  //   Allow remote LLM endpoints for summarization (security risk).
  //   Default false blocks non-local summarization.
  // "path_map": "",
  //   Cross-platform path remapping. Format: "orig1=new1,orig2=new2".
  //   Allows indexes built on Linux to work on Windows and vice versa.
}}
'''
