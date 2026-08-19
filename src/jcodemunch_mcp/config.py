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
# True once `load_config()` has run in this process. Read by `get()`, which
# lazily loads rather than answering from `default` for a config it never read
# (#426). A separate flag rather than truth-testing `_GLOBAL_CONFIG` because
# "loaded" and "non-empty" are different questions, and the emptiness test alone
# would re-load on every read for any state that legitimately produced `{}`.
_CONFIG_LOADED = False
_PROJECT_CONFIGS: dict[str, dict[str, Any]] = {}
_PROJECT_CONFIG_HASHES: dict[str, str] = {}
# Repo keys whose _PROJECT_CONFIGS entry is a MIRROR of global that WE wrote
# because the repo has no `.jcodemunch.jsonc` (v1.108.197). Only these may be
# refreshed from global — an entry installed by anyone else is theirs, and
# overwriting it is data loss, not cache maintenance.
_PROJECT_CONFIG_MIRRORS: set[str] = set()
_DEPRECATED_ENV_VARS_LOGGED: set[str] = set()
_CONFIG_LOCK = threading.Lock()
_REPO_PATH_CACHE: dict[str, str] = {}

ENV_VAR_MAPPING = {
    "JCODEMUNCH_USE_AI_SUMMARIES": "use_ai_summaries",
    "JCODEMUNCH_TRUSTED_FOLDERS": "trusted_folders",
    "JCODEMUNCH_TRUSTED_FOLDERS_WHITELIST_MODE": "trusted_folders_whitelist_mode",
    "JCODEMUNCH_MAX_FILE_SIZE": "max_file_size",
    "JCODEMUNCH_RESPECT_CACHEDIR_TAG": "respect_cachedir_tag",
    "JCODEMUNCH_RESPONSE_MAX_BYTES": "response_max_bytes",
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
    "JCODEMUNCH_GIT_ROOT_IDENTITY": "git_root_identity",
    "JCODEMUNCH_GIT_BLAME_ENABLED": "git_blame_enabled",
    "JCODEMUNCH_STATS_FILE_INTERVAL": "stats_file_interval",
    "JCODEMUNCH_SHARE_SAVINGS": "share_savings",
    "JCODEMUNCH_PERF_TELEMETRY": "perf_telemetry_enabled",
    "JCODEMUNCH_PERF_TELEMETRY_MAX_ROWS": "perf_telemetry_max_rows",
    "JCODEMUNCH_RUNTIME_MAX_ROWS": "runtime_max_rows",
    "JCODEMUNCH_RUNTIME_REDACT": "runtime_redact_enabled",
    "JCODEMUNCH_RUNTIME_INGEST_ENABLED": "runtime_ingest_enabled",
    "JCODEMUNCH_ORG_INGEST_ENABLED": "org_ingest_enabled",
    "JCODEMUNCH_LICENSE_KEY": "license_key",
    "JCODEMUNCH_RUNTIME_INGEST_MAX_BODY_BYTES": "runtime_ingest_max_body_bytes",
    "JCODEMUNCH_SUMMARIZER_CONCURRENCY": "summarizer_concurrency",
    "JCODEMUNCH_SUMMARIZER_MAX_FAILURES": "summarizer_max_failures",
    "JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER": "allow_remote_summarizer",
    "JCODEMUNCH_ALLOW_PAID_SUMMARIES": "allow_paid_summaries",
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
    "JCODEMUNCH_DEFAULT_FORMAT": "server_output",
    "JCODEMUNCH_ENCODING_THRESHOLD": "server_output_threshold",
}

_SERVER_OUTPUT_ALIASES = {
    "raw": "raw",
    "encoded": "encoded",
    "adaptive": "adaptive",
    # Legacy aliases kept for backward compatibility.
    "json": "raw",
    "compact": "encoded",
    "auto": "adaptive",
}


def _normalize_server_output(value: str) -> str | None:
    """Normalize server_output/format aliases to canonical config values."""
    return _SERVER_OUTPUT_ALIASES.get(value.strip().lower())


def _global_config_path() -> Path:
    """Return the path to the global config.jsonc."""
    storage = os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index"))
    return Path(storage) / "config.jsonc"


def _global_storage_path() -> Path:
    """Return the global storage directory path."""
    return Path(os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index")))


def _is_first_ever_install(storage_dir: "Path") -> bool:
    """True only for a genuinely first-ever install of jcodemunch.

    Signalled by the absence of any prior index/telemetry state in the storage
    directory. Used to decide whether a freshly-created config should default to
    the token-lean ``tool_surface="counter"`` front door. On ANY uncertainty we
    return False (preserve the historical ``full`` surface) so that an existing
    user who merely lacks a config file is never silently collapsed to the front
    door on a package update. The cost of a false negative is only reduced
    token savings, never a surprising surface change.
    """
    try:
        if not storage_dir.exists():
            return True
        for entry in storage_dir.iterdir():
            name = entry.name.lower()
            if name.endswith((".db", ".sqlite", ".sqlite3")):
                return False  # prior index/telemetry state → existing install
        return True
    except OSError:
        return False  # unsure → treat as existing, keep the full surface


def _fresh_config_content(storage_dir: "Path") -> str:
    """Template content for a newly-created config.

    Genuinely first-ever installs get ``tool_surface="counter"`` baked in (the
    token-lean 3-tool front door) so a new user experiences maximal savings out
    of the box. The key is written ONLY here (never into ``generate_template``),
    so ``upgrade_config`` — which back-injects template keys — can never add it
    to an existing user's config on a package update.
    """
    template = generate_template()
    if _is_first_ever_install(storage_dir):
        template = set_string_key(
            template,
            "tool_surface",
            "counter",
            comment='new-install default; set "full" to advertise all tool schemas',
        )
    return template


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
        _PROJECT_CONFIG_MIRRORS.discard(resolved)


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
    "max_file_size": 512000,
    "respect_cachedir_tag": True,
    "response_max_bytes": 1048576,
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
    "tool_profile": "full",  # "core", "standard", or "full"
    # DEFAULT for existing installs. The template ships this key COMMENTED, so an
    # existing user who lacks it (or gets it back-injected by upgrade_config)
    # stays on "full". Only a genuinely first-ever install has it ACTIVATED to
    # "counter" by _fresh_config_content — so a package update never silently
    # collapses a user's tool surface.
    "tool_surface": "full",  # "full" or "counter"
    "tool_tier_bundles": {
        "core": [
            "index_repo", "index_folder", "index_file",
            "list_repos", "resolve_repo",
            "get_repo_outline", "get_file_tree", "get_file_outline",
            "search_symbols", "get_symbol_source", "get_file_content",
            "search_text", "get_context_bundle", "get_ranked_context",
            "assemble_task_context",
            "find_importers", "find_references",
        ],
        "standard": [
            # core ∪ these additional tools
            "index_repo", "index_folder", "index_file",
            "list_repos", "resolve_repo",
            "get_repo_outline", "get_file_tree", "get_file_outline",
            "search_symbols", "get_symbol_source", "get_file_content",
            "search_text", "get_context_bundle", "get_ranked_context",
            "assemble_task_context",
            "find_importers", "find_references",
            "summarize_repo", "embed_repo", "index_dependency", "suggest_queries",
            "search_columns", "check_references",
            "get_dependency_graph", "get_class_hierarchy",
            "get_related_symbols", "get_call_hierarchy",
            "get_blast_radius", "check_rename_safe", "check_delete_safe", "check_edit_safe",
            "find_implementations",
            "get_impact_preview", "get_changed_symbols",
            "get_symbol_diff", "get_symbol_provenance",
            "get_pr_risk_profile", "get_endpoint_impact", "get_symbol_complexity",
            "get_churn_rate", "get_delivery_metrics", "get_parity_map", "get_hotspots",
            "get_symbol_importance", "get_repo_map", "find_dead_code",
            "get_dead_code_v2", "get_untested_symbols", "find_similar_symbols",
            "get_repo_health", "search_ast", "winnow_symbols",
            "get_dependency_cycles", "get_coupling_metrics",
            "get_layer_violations", "get_cross_repo_map", "get_group_contracts",
            "get_tectonic_map", "get_signal_chains", "get_decorator_census",
            "get_architecture_metrics", "render_diagram",
            "get_project_intel", "list_workspaces", "invalidate_cache", "get_watch_status",
            "analyze_perf", "tune_weights", "check_embedding_drift", "suggest_corrections",
            "digest", "finalize_handoff", "diff_health_radar", "get_file_risk",
            "import_runtime_signal", "get_runtime_coverage",
            "find_hot_paths", "find_unused_paths", "get_redaction_log",
        ],
    },
    "model_tier_map": {
        "claude-opus": "full",
        "claude-sonnet": "standard",
        "claude-haiku": "core",
        "gpt-4o": "standard",
        "gpt-5": "full",
        "o1": "full",
        "llama": "core",
        "*": "full",
    },
    "adaptive_tiering": False,
    "compact_schemas": False,
    "skill_advisor_mode": "off",  # "off" | "advise"
    "server_output": "adaptive",  # "raw", "encoded", or "adaptive"
    "server_output_threshold": 0.15,  # Minimum savings ratio for adaptive mode
    "disabled_tools": ["test_summarizer"],
    # When True, `disabled_tools` may include `set_tool_tier` and
    # `announce_model`. Default False keeps the in-session tier-switch
    # safety net intact; opt-in is for users who want to claw back two
    # tool slots (e.g. against Antigravity's 50-tool cap) and accept that
    # they cannot switch tiers mid-session. Issue #299, requested by @kecsap.
    "allow_disabling_tier_controls": False,
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
    "worktree_base_path": "",
    "log_level": "WARNING",
    "log_file": None,
    "redact_source_root": False,
    "git_root_identity": True,
    "identity_mode": None,
    "git_blame_enabled": True,
    "stats_file_interval": 3,
    "share_savings": True,
    "perf_telemetry_enabled": False,
    "perf_telemetry_max_rows": 100_000,
    "runtime_max_rows": 100_000,
    "runtime_redact_enabled": True,
    "runtime_ingest_enabled": False,
    "org_ingest_enabled": False,
    "license_key": "",  # jCodeMunch license key; gates the org-rollup (team SKU) feature only
    "runtime_ingest_max_body_bytes": 5_242_880,  # 5 MB
    "summarizer_concurrency": 4,
    "summarizer_max_failures": 3,
    "allow_remote_summarizer": False,
    "allow_paid_summaries": False,
    "openai_extra_body": {},
    "path_map": "",
    "cross_repo_default": False,
    "discovery_hint": True,
    "cache_mode": "full",
    "summarize_from_docstrings": True,
    # Session-aware routing (Feature 6)
    "negative_evidence_threshold": 0.5,
    "search_result_cache_max": 128,
    "session_journal": True,
    "plan_turn_high_threshold": 2.0,
    "plan_turn_medium_threshold": 0.5,
    "turn_budget_tokens": 20000,
    "turn_gap_seconds": 30.0,
    "session_token_budget": 0,
    "session_resume": False,
    "session_max_age_minutes": 30,
    "session_max_queries": 50,
    # Agent Selector
    "agent_selector": {},
    # LSP enrichment
    "enrichment": {},
    # Mermaid Viewer
    "render_diagram_viewer_enabled": False,
    "mermaid_viewer_path": "",
}

CONFIG_TYPES = {
    "use_ai_summaries": (bool, str),
    "trusted_folders": list,
    "trusted_folders_whitelist_mode": bool,
    "max_file_size": int,
    "respect_cachedir_tag": bool,
    "response_max_bytes": int,
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
    "tool_profile": str,
    "tool_surface": str,
    "tool_tier_bundles": dict,
    "model_tier_map": dict,
    "adaptive_tiering": bool,
    "compact_schemas": bool,
    "skill_advisor_mode": str,
    "server_output": str,
    "server_output_threshold": float,
    "disabled_tools": list,
    "allow_disabling_tier_controls": bool,
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
    "worktree_base_path": str,
    "log_level": str,
    "log_file": (str, type(None)),
    "redact_source_root": bool,
    "git_root_identity": bool,
    "identity_mode": (str, type(None)),
    "git_blame_enabled": bool,
    "stats_file_interval": int,
    "share_savings": bool,
    "perf_telemetry_enabled": bool,
    "perf_telemetry_max_rows": int,
    "runtime_max_rows": int,
    "runtime_redact_enabled": bool,
    "runtime_ingest_enabled": bool,
    "org_ingest_enabled": bool,
    "license_key": str,
    "runtime_ingest_max_body_bytes": int,
    "summarizer_concurrency": int,
    "summarizer_max_failures": int,
    "allow_remote_summarizer": bool,
    "allow_paid_summaries": bool,
    "openai_extra_body": dict,
    "path_map": str,
    "cross_repo_default": bool,
    "discovery_hint": bool,
    "cache_mode": str,
    "summarize_from_docstrings": bool,
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
    "session_token_budget": int,
    "session_resume": bool,
    "session_max_age_minutes": int,
    "session_max_queries": int,
    "agent_selector": dict,
    "enrichment": dict,
    "render_diagram_viewer_enabled": bool,
    "mermaid_viewer_path": str,
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
    if key == "server_output":
        return isinstance(value, str) and _normalize_server_output(value) is not None
    if key == "server_output_threshold":
        return isinstance(value, (int, float)) and 0.0 <= float(value) <= 1.0
    if isinstance(expected_type, tuple):
        return isinstance(value, expected_type)
    return isinstance(value, expected_type)


def load_config(storage_path: str | None = None, create_missing: bool = True) -> None:
    """Load global config.jsonc. Called once from main().

    `create_missing=False` suppresses the auto-creation of a default config file
    below. Used by the lazy load in `get()` (#426): a READ must never be the
    thing that writes a file into the user's storage directory. Value resolution
    is otherwise identical, because the created file is the template and the
    template is `DEFAULTS`.
    """
    global _GLOBAL_CONFIG, _CONFIG_LOADED

    # Determine config path
    if storage_path:
        config_path = Path(storage_path) / "config.jsonc"
    else:
        config_path = _global_config_path()

    # Auto-create default config if missing. A genuinely first-ever install
    # defaults to the token-lean "counter" front door; an existing install that
    # merely lacks a config file keeps the historical "full" surface, so a
    # package update never silently collapses a user's tool surface.
    if not config_path.exists() and create_missing:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        content = _fresh_config_content(config_path.parent)
        config_path.write_text(content, encoding="utf-8")
        logger.info(
            "Created default config at %s (tool_surface=%s)",
            config_path,
            "counter" if _is_first_ever_install(config_path.parent) else "full",
        )

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
                        elif key == "server_output" and isinstance(value, str):
                            normalized = _normalize_server_output(value)
                            if normalized is not None:
                                _GLOBAL_CONFIG[key] = normalized
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

    _CONFIG_LOADED = True


def _parse_env_value(value: str, expected_type: type | tuple, key: str | None = None) -> Any:
    """Parse env var string to expected type."""
    # use_ai_summaries accepts "auto", "true", "false" as strings;
    # generic bool parsing would coerce "auto" to False.
    if key == "use_ai_summaries":
        return value.strip().lower()
    if key == "server_output":
        return _normalize_server_output(value)
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


def _ensure_loaded() -> None:
    """Load the global config if this process never has (#426).

    Deliberately narrow. It fires ONLY for the state that means "nothing was
    ever read": not loaded AND empty. Both halves are load-bearing:

    - The flag alone would fire for a caller that populated `_GLOBAL_CONFIG`
      directly (every test that stuffs a dict in does exactly this) and would
      CLOBBER it on the next read.
    - Emptiness alone would re-load on every read for any state that
      legitimately resolves to `{}`, turning one file read into a per-key one.

    ⚠ It does NOT displace the explicit `load_config()` calls. `main()` re-runs
    it after `_setup_logging()` on purpose, so config warnings reach the
    configured log destination; that call is unconditional and still emits them.
    This only removes the ability to LOSE, it does not decide the ordering.

    ⚠ Never raises. A read that starts failing is a worse defect than the silent
    default this replaces, so a load failure falls through to `DEFAULTS`
    behaviour exactly as before.
    """
    if _CONFIG_LOADED or _GLOBAL_CONFIG:
        return
    try:
        # create_missing=False: a config READ must not write a file into the
        # user's storage directory as a side effect.
        load_config(create_missing=False)
    except Exception:
        logger.debug("Lazy config load failed; answering from defaults", exc_info=True)


def get(key: str, default: Any = None, repo: str | None = None) -> Any:
    """Get config value, resolving project -> global -> default.

    ⚠ v1.108.250 (#416 follow-up, @domis86): `_PROJECT_CONFIGS` holds ONLY the
    keys a project's `.jcodemunch.jsonc` actually declares — it is an OVERLAY,
    not a merged snapshot. Global is consulted HERE, on every read, so a key the
    project file omits resolves live.

    It used to hold `deepcopy(_GLOBAL_CONFIG)` overlaid with the project keys,
    and this function returned `default` for anything missing from it. That is
    two bugs wearing one costume:

    1. ORDERING. The snapshot is taken whenever `load_project_config` runs. In
       `config --check` that is BEFORE `load_config()` populates global, so the
       copy captured an empty global and every undeclared key reported its
       hardcoded default while `_detect_source` truthfully tagged the row
       `[config]`. Indexing loads in the other order, so runtime behaviour was
       correct and only the diagnostic lied — the exact inverse of #416.
    2. STALENESS. A file-backed entry is excluded from the mirror refresh below,
       so a later edit to global config was invisible to it forever.

    Resolving at read time kills both: there is no snapshot to be taken early or
    to go stale. Do NOT reintroduce a merge at load time to "save a lookup".

    ⚠ v1.108.258 (#426): loads lazily when nothing has been loaded yet. Before,
    a read in an unloaded process answered from `default` for EVERY env-mapped
    key -- `JCODEMUNCH_MAX_FILE_SIZE=20000000` resolved as 512000 -- and the
    caller could not tell "the value is 512000" from "I never read the config".
    That silence had already produced three one-subcommand-at-a-time
    `load_config()` patches in `server.py`; every subcommand dispatched above the
    shared call was one edit from a fourth, and nothing failed loudly.
    """
    _ensure_loaded()
    if repo:
        resolved = _resolve_repo_key(repo)
        if resolved:
            project = _PROJECT_CONFIGS.get(resolved)
            if project is not None and key in project:
                return project[key]
    return _GLOBAL_CONFIG.get(key, default)


def _type_label(t: Any) -> str:
    """Map a CONFIG_TYPES entry (a type or tuple of types) to a short label."""
    if isinstance(t, tuple):
        t = next((x for x in t if x is not type(None)), t[0])
    return {
        bool: "bool", int: "int", float: "float",
        str: "string", list: "list", dict: "dict",
    }.get(t, "string")


def _raw_jsonc_keys(path: Path) -> set[str]:
    """Keys a JSONC config file actually sets (for source attribution)."""
    try:
        if path.is_file():
            import json
            return set(json.loads(_strip_jsonc(path.read_text(encoding="utf-8-sig"))).keys())
    except (OSError, ValueError):
        pass
    return set()


def _config_meta(template: str) -> dict[str, tuple[str | None, str]]:
    """One pass over the config template: map each top-level key to its
    ``(section, description)`` — the nearest preceding ``=== Section ===`` header
    and the key's comment block.

    The template mixes two comment conventions: most keys are documented by a
    block immediately ABOVE the ``// "key":`` line, but some (``embed_model``,
    ``allow_remote_summarizer``, ``path_map``, ``render_diagram_viewer_enabled``,
    …) put an indented block immediately BELOW it. The discriminator is the blank
    line: comments contiguous with the key they follow (no blank gap) attach to
    THAT key; comments after a blank/value/header line are the lead block for the
    NEXT key. (Before this, below-style blocks landed on the following key — an
    off-by-one that mis-described several keys.) Powers grouped config UIs (the
    Console) and self-documenting dashboards."""
    import re
    header_re = re.compile(r"^\s*//\s*=+\s*(.+?)\s*=+\s*$")
    key_re = re.compile(r'^  (?:// *)?"(\w+)" *:')
    section: str | None = None
    pending: list[str] = []            # lead comments awaiting the next key (above-style)
    parts: dict[str, list[str]] = {}   # key -> description fragments (mutable; below-style appends)
    sections: dict[str, str | None] = {}  # key -> section, first occurrence wins
    last_key: str | None = None        # most recent key, for below-style comments
    after_key = False                  # True between a key line and the next blank/value/header
    for line in template.splitlines():
        hm = header_re.match(line)
        if hm:  # === Section === header ends any open block
            section = hm.group(1).strip()
            pending = []
            last_key = None
            after_key = False
            continue
        km = key_re.match(line)
        if km:  # a key entry (active or commented-out)
            k = km.group(1)
            if k not in sections:  # first occurrence wins (commented example vs active key)
                sections[k] = section
                parts[k] = list(pending)  # above-style lead block (may be empty)
            pending = []
            last_key = k
            after_key = True  # comments that follow with no blank gap describe THIS key
            continue
        s = line.strip()
        if s.startswith("//"):
            c = s[2:].strip()
            if c and "===" not in c:
                if after_key and last_key is not None:
                    parts[last_key].append(c)  # below-style comment for the key just seen
                else:
                    pending.append(c)  # lead comment for the next key
        else:  # blank line / value continuation closes the current comment block
            pending = []
            after_key = False
    return {k: (sections[k], " ".join(parts[k])) for k in sections}


def config_report(repo: str | None = None) -> list[dict[str, Any]]:
    """Structured effective configuration: one entry per known key with its
    value, default, type, source (default / global / project), plus the key's
    ``group`` (config-template section) and ``description``.

    Machine-readable counterpart to the human `config` output — for the Console,
    CI, and dashboards. Source attribution mirrors `config`'s display: a key is
    "project" if the repo's .jcodemunch.jsonc sets it, else "global" if the
    global config.jsonc sets it, else "default". ``group``/``description`` are
    derived from the JSONC template's section headers + comments.
    """
    load_config()
    storage_path = os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index"))
    global_keys = _raw_jsonc_keys(Path(storage_path) / "config.jsonc")
    project_keys: set[str] = set()
    if repo:
        load_project_config(repo)
        project_keys = _raw_jsonc_keys(Path(repo) / ".jcodemunch.jsonc")

    meta = _config_meta(generate_template())

    report: list[dict[str, Any]] = []
    for key, default in DEFAULTS.items():
        if repo and key in project_keys:
            source = "project"
        elif key in global_keys:
            source = "global"
        else:
            source = "default"
        group, description = meta.get(key, (None, ""))
        report.append({
            "key": key,
            "type": _type_label(CONFIG_TYPES.get(key)),
            "value": get(key, default, repo=repo),
            "default": default,
            "source": source,
            "group": group or "Other",
            "description": description,
        })
    return report


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
                # ⚠ An OVERLAY of the project's own keys, NOT a merge over
                # global. `get()` consults global at read time; baking a copy of
                # it in here is what made `config --check` report defaults and
                # made a later global edit invisible. See get()'s docstring.
                overlay: dict[str, Any] = {}
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
                                overlay[key] = list(valid_folders)
                            elif key == "server_output" and isinstance(value, str):
                                normalized = _normalize_server_output(value)
                                if normalized is not None:
                                    overlay[key] = normalized
                            else:
                                overlay[key] = value
                        else:
                            logger.warning(
                                "Project config key '%s' has invalid type. Using global default.",
                                key,
                            )
                # A rejected key is simply absent from the overlay, so it falls
                # through to global on read — which is what the warning promises.
                _PROJECT_CONFIGS[repo_key] = overlay
                _PROJECT_CONFIG_HASHES[repo_key] = content_hash
                # File-backed, so no longer a mirror of global.
                _PROJECT_CONFIG_MIRRORS.discard(repo_key)
        except Exception as e:
            logger.warning("Failed to load project config: %s", e)
            with _CONFIG_LOCK:
                # A file exists but did not parse. An empty overlay means every
                # key falls through to global on read. This is NOT a mirror:
                # the next call must retry the file.
                _PROJECT_CONFIGS[repo_key] = {}
                _PROJECT_CONFIG_MIRRORS.discard(repo_key)
    else:
        with _CONFIG_LOCK:
            # A repo with no `.jcodemunch.jsonc` has nothing of its own to say,
            # so its overlay is empty and every key resolves against global at
            # read time. v1.108.197 needed a REFRESH here because the entry was
            # a frozen COPY of global that went stale the moment global changed;
            # an empty overlay cannot go stale, so the refresh is now trivially
            # correct rather than load-bearing.
            #
            # ⚠ Still write ONLY entries this branch owns (`_PROJECT_CONFIG_MIRRORS`).
            # An entry installed by anyone else — a caller configuring a repo in
            # memory with no file on disk — is theirs. Overwriting it is data
            # loss wearing a cache-maintenance costume, and it is silent.
            if repo_key not in _PROJECT_CONFIGS or repo_key in _PROJECT_CONFIG_MIRRORS:
                _PROJECT_CONFIGS[repo_key] = {}
                _PROJECT_CONFIG_MIRRORS.add(repo_key)
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
    _ensure_loaded()  # sibling reader of get(); same #426 hazard
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


def set_bool_key(content: str, key: str, value: bool) -> str:
    """Set a boolean key in JSONC config content to an explicit active value.

    Handles three input shapes:
    - Commented template form:  ``  // "key": true,`` → ``  "key": <value>,``
    - Existing active form:     ``  "key": false,``   → ``  "key": <value>,``
    - Key entirely absent:      appended as ``  "key": <value>,`` before the closing brace

    Indent and trailing comma are preserved. The trailing comma is always emitted because
    JSONC permits it even on the last key in a block.

    The match is anchored on a line where the only non-whitespace content before the key
    is an optional ``//`` comment marker. Keys nested inside comment paragraphs that happen
    to contain the text are not touched.
    """
    import re

    new_literal = "true" if value else "false"
    pattern = re.compile(
        r'^(?P<indent>[ \t]*)(?://[ \t]*)?"' + re.escape(key) + r'"[ \t]*:[ \t]*(?:true|false)[ \t]*,?[ \t]*$',
        re.MULTILINE,
    )

    if pattern.search(content):
        return pattern.sub(rf'\g<indent>"{key}": {new_literal},', content, count=1)

    # Key absent — inject before the closing brace.
    return _inject_blocks_before_closing_brace(content, [f'  "{key}": {new_literal},'])


def set_string_key(content: str, key: str, value: str, comment: str | None = None) -> str:
    """Set a string key in JSONC config content to an explicit active value.

    Mirrors :func:`set_bool_key` for string values. Handles the commented
    template form, an existing active form, and an entirely-absent key (appended
    before the closing brace, with an optional inline ``// comment``). The value
    is emitted as a JSON string literal so quoting/escaping is always valid.
    """
    import re

    new_literal = json.dumps(value)  # safely-quoted JSON string literal
    pattern = re.compile(
        r'^(?P<indent>[ \t]*)(?://[ \t]*)?"' + re.escape(key)
        + r'"[ \t]*:[ \t]*"[^"]*"[ \t]*,?[ \t]*$',
        re.MULTILINE,
    )

    if pattern.search(content):
        return pattern.sub(rf'\g<indent>"{key}": {new_literal},', content, count=1)

    # Key absent — inject before the closing brace, with an optional comment.
    suffix = f"  // {comment}" if comment else ""
    return _inject_blocks_before_closing_brace(content, [f'  "{key}": {new_literal},{suffix}'])


def apply_share_savings(value: bool, storage_path: "Path | str | None" = None) -> "Path":
    """Apply an explicit share_savings setting to the user's config.jsonc.

    Creates the config from the current template if it doesn't exist yet, then sets
    ``share_savings`` to the explicit value. Returns the path written to.

    Used by ``jcodemunch-mcp init --share-savings=on|off`` and
    ``jcodemunch-mcp install <agent> --share-savings=on|off`` to give users a durable
    opt-out (or opt-in) lever that survives package upgrades.
    """
    if storage_path is None:
        config_path = _global_config_path()
    else:
        config_path = Path(storage_path) / "config.jsonc"

    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(_fresh_config_content(config_path.parent), encoding="utf-8")

    content = config_path.read_text(encoding="utf-8")
    updated = set_bool_key(content, "share_savings", value)
    if updated != content:
        config_path.write_text(updated, encoding="utf-8")
    return config_path


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


# --- General typed JSONC key writer (powers `config set`) -------------------- #

# Keys that must not be set through `config set` (managed by the tooling, not users).
_READONLY_CONFIG_KEYS = {"version"}


def _skip_ws_and_comments(content: str, i: int) -> int:
    """Advance past whitespace and // or /* */ comments starting at i."""
    n = len(content)
    while i < n:
        c = content[i]
        if c in " \t\r\n":
            i += 1
        elif c == "/" and i + 1 < n and content[i + 1] == "/":
            j = content.find("\n", i)
            i = n if j == -1 else j + 1
        elif c == "/" and i + 1 < n and content[i + 1] == "*":
            j = content.find("*/", i + 2)
            i = n if j == -1 else j + 2
        else:
            break
    return i


def _scan_jsonc_value_end(content: str, i: int) -> int:
    """Return the index just past the JSON value beginning at/after i.

    String-, bracket-, and comment-aware, so it spans multi-line arrays/objects
    (including the `// "field",` comment lines embedded in the `languages` /
    `meta_fields` template arrays). Primitive values run to the next delimiter.
    """
    n = len(content)
    i = _skip_ws_and_comments(content, i)
    if i >= n:
        return i
    c = content[i]
    if c == '"':
        i += 1
        while i < n:
            if content[i] == "\\":
                i += 2
                continue
            if content[i] == '"':
                return i + 1
            i += 1
        return i
    if c in "{[":
        depth = 0
        while i < n:
            ch = content[i]
            if ch == '"':
                i += 1
                while i < n:
                    if content[i] == "\\":
                        i += 2
                        continue
                    if content[i] == '"':
                        break
                    i += 1
                i += 1
                continue
            if ch == "/" and i + 1 < n and content[i + 1] == "/":
                j = content.find("\n", i)
                i = n if j == -1 else j + 1
                continue
            if ch == "/" and i + 1 < n and content[i + 1] == "*":
                j = content.find("*/", i + 2)
                i = n if j == -1 else j + 2
                continue
            if ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return i
    # primitive: number / true / false / null / bareword
    while i < n and content[i] not in ",}\n]":
        if content[i] == "/" and i + 1 < n and content[i + 1] in "/*":
            break
        i += 1
    while i > 0 and content[i - 1] in " \t":
        i -= 1
    return i


def set_key(content: str, key: str, value: Any) -> str:
    """Set ``key`` to ``value`` in JSONC ``content``, preserving comments.

    Generalizes ``set_bool_key`` to any JSON type. Replaces the existing value
    (active or commented-template form, single- or multi-line) in place and
    uncomments the key; appends the key before the closing brace if absent. The
    new value is written as compact single-line JSON. Trailing description
    comments after the key line are preserved.
    """
    import re

    literal = json.dumps(value, ensure_ascii=False)
    pattern = re.compile(
        r"^([ \t]*)(?://[ \t]*)?\"" + re.escape(key) + r"\"[ \t]*:",
        re.MULTILINE,
    )
    m = pattern.search(content)
    if not m:
        return _inject_blocks_before_closing_brace(content, [f'  "{key}": {literal},'])

    indent = m.group(1)
    line_start = m.start()
    val_end = _scan_jsonc_value_end(content, m.end())
    # consume an optional trailing comma so we don't leave a doubled one
    j = val_end
    while j < len(content) and content[j] in " \t":
        j += 1
    if j < len(content) and content[j] == ",":
        j += 1
    return content[:line_start] + f'{indent}"{key}": {literal},' + content[j:]


def _typename(t: Any) -> str:
    return "null" if t is type(None) else getattr(t, "__name__", str(t))


def coerce_config_value(key: str, raw: Any) -> Any:
    """Coerce + type-validate a value for ``key`` against CONFIG_TYPES.

    ``raw`` may already be a typed Python object (from a JSON API caller) or a
    string (from the CLI). Strings are JSON-parsed when possible, else kept as a
    bare string for str-typed keys. Raises ValueError on an unknown key or a
    type the key does not accept.
    """
    allowed = CONFIG_TYPES.get(key)
    if allowed is None:
        raise ValueError(f"unknown config key: {key!r}")
    allowed = allowed if isinstance(allowed, tuple) else (allowed,)

    if isinstance(raw, str):
        try:
            val = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            val = raw  # bare, unquoted string
    else:
        val = raw

    if val is None:
        if type(None) in allowed:
            return None
    elif isinstance(val, bool):
        if bool in allowed:
            return val
    elif isinstance(val, int):
        if int in allowed:
            return val
        if float in allowed:
            return float(val)
    elif isinstance(val, float):
        if float in allowed:
            return val
        if int in allowed and val.is_integer():
            return int(val)
    elif isinstance(val, list):
        if list in allowed:
            return val
    elif isinstance(val, dict):
        if dict in allowed:
            return val
    elif isinstance(val, str):
        if str in allowed:
            return val

    # Fallback: a str-typed key takes the raw text verbatim (e.g. server_output
    # "true" must stay the string "true", not the parsed boolean).
    if str in allowed and isinstance(raw, str):
        return raw

    raise ValueError(
        f"{key!r} expects {', '.join(_typename(t) for t in allowed)}; "
        f"got {_typename(type(val))}"
    )


def set_config_value(key: str, raw: Any, storage_path: "Path | str | None" = None) -> Any:
    """Validate + persist one config key to the global (or given) config.jsonc.

    Returns the coerced value actually written. Creates the file from the
    template if absent. Re-parses afterward and confirms the key now reads back
    as intended (isolating this edit from any pre-existing config issues);
    rolls the file back and raises ValueError if the write didn't land cleanly.
    """
    if key in _READONLY_CONFIG_KEYS:
        raise ValueError(f"{key!r} is read-only and cannot be set")
    value = coerce_config_value(key, raw)

    if storage_path is None:
        config_path = _global_config_path()
    else:
        config_path = Path(storage_path) / "config.jsonc"
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(_fresh_config_content(config_path.parent), encoding="utf-8")

    original = config_path.read_text(encoding="utf-8")
    updated = set_key(original, key, value)
    config_path.write_text(updated, encoding="utf-8")

    try:
        loaded = json.loads(_strip_jsonc(updated))
        if loaded.get(key) != value:
            raise ValueError("write-back did not take effect")
    except (json.JSONDecodeError, ValueError) as e:
        config_path.write_text(original, encoding="utf-8")
        raise ValueError(f"config set failed (rolled back): {e}")
    return value


def unset_key(content: str, key: str) -> str:
    """Remove the ACTIVE entry for ``key`` (and its line) from JSONC content.

    Only matches an uncommented key line so template/example comments are left
    intact; the key then falls back to its built-in default. No-op when the key
    isn't actively set.
    """
    import re

    pattern = re.compile(
        r"^([ \t]*)\"" + re.escape(key) + r"\"[ \t]*:", re.MULTILINE
    )
    m = pattern.search(content)
    if not m:
        return content
    line_start = m.start()
    j = _scan_jsonc_value_end(content, m.end())
    while j < len(content) and content[j] in " \t":
        j += 1
    if j < len(content) and content[j] == ",":
        j += 1
    nl = content.find("\n", j)
    end = len(content) if nl == -1 else nl + 1
    return content[:line_start] + content[end:]


def unset_config_value(key: str, storage_path: "Path | str | None" = None) -> bool:
    """Clear one config key so its default applies. Returns True if a change was
    written. Rolls back and raises if the result no longer parses."""
    if key in _READONLY_CONFIG_KEYS:
        raise ValueError(f"{key!r} is read-only and cannot be unset")
    if key not in CONFIG_TYPES:
        raise ValueError(f"unknown config key: {key!r}")

    if storage_path is None:
        config_path = _global_config_path()
    else:
        config_path = Path(storage_path) / "config.jsonc"
    if not config_path.exists():
        return False

    original = config_path.read_text(encoding="utf-8")
    updated = unset_key(original, key)
    if updated == original:
        return False
    config_path.write_text(updated, encoding="utf-8")
    try:
        json.loads(_strip_jsonc(updated))
    except (json.JSONDecodeError, ValueError) as e:
        config_path.write_text(original, encoding="utf-8")
        raise ValueError(f"config unset failed (rolled back): {e}")
    return True


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
        "analyze_perf",
        "announce_model",
        "audit_agent_config",
        "check_embedding_drift",
        "tune_weights",
        "check_delete_safe",
        "check_edit_safe",
        "check_references",
        "check_rename_safe",
        "diff_health_radar",
        "digest",
        "embed_repo",
        "finalize_handoff",
        "find_dead_code",
        "find_implementations",
        "find_hot_paths",
        "find_importers",
        "find_references",
        "find_unused_paths",
        "get_architecture_metrics",
        "get_blast_radius",
        "get_call_hierarchy",
        "get_changed_symbols",
        "get_churn_rate",
        "get_class_hierarchy",
        "get_context_bundle",
        "get_coupling_metrics",
        "get_cross_repo_map",
        "get_group_contracts",
        "get_dead_code_v2",
        "get_decorator_census",
        "get_delivery_metrics",
        "get_dependency_cycles",
        "get_dependency_graph",
        "get_extraction_candidates",
        "get_file_content",
        "get_file_outline",
        "get_file_risk",
        "get_file_tree",
        "get_hotspots",
        "get_endpoint_impact",
        "get_impact_preview",
        "get_layer_violations",
        "get_parity_map",
        "get_pr_risk_profile",
        "get_project_intel",
        "get_ranked_context",
        "assemble_task_context",
        "get_redaction_log",
        "get_related_symbols",
        "get_repo_health",
        "get_repo_outline",
        "get_runtime_coverage",
        "get_session_context",
        "get_session_snapshot",
        "get_session_stats",
        "get_signal_chains",
        "get_symbol_complexity",
        "get_symbol_diff",
        "get_symbol_importance",
        "get_repo_map",
        "find_similar_symbols",
        "get_symbol_provenance",
        "get_symbol_source",
        "get_tectonic_map",
        "get_untested_symbols",
        "get_watch_status",
        "import_runtime_signal",
        "index_dependency",
        "index_file",
        "index_folder",
        "index_repo",
        "invalidate_cache",
        "jcodemunch_guide",
        "list_repos",
        "list_workspaces",
        "plan_refactoring",
        "plan_turn",
        "register_edit",
        "render_diagram",
        "resolve_repo",
        "search_ast",
        "search_columns",
        "search_symbols",
        "search_text",
        "set_tool_tier",
        "suggest_corrections",
        "suggest_queries",
        "summarize_repo",
        "test_summarizer",
        "winnow_symbols",
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

  // "max_file_size": 512000,
  //   Per-file byte cap for indexing. A file larger than this is skipped with
  //   reason `too_large`, and a search over a corpus that skipped one cannot
  //   prove absence -- the file is real, current and wanted, it just never
  //   entered the index. The default is deliberately conservative; raise it if
  //   your repo has large legitimate source files, and re-index.

  // "respect_cachedir_tag": true,
  //   Honour the Cache Directory Tagging Specification
  //   (https://bford.info/cachedir/): prune any directory holding a
  //   `CACHEDIR.TAG` whose first 43 bytes are the spec signature. The
  //   signature is verified -- a file merely NAMED CACHEDIR.TAG does not
  //   exclude anything.
  //   Unlike every other exclusion here, this one is declared by whoever WROTE
  //   the directory rather than listed by us, so a tool that drops a cache into
  //   your tree is honoured without jcodemunch knowing its name, and it works
  //   for caches that are not dotted. Pruned directories are counted as
  //   `cache_dir` in `discovery_skip_counts`. Set false if you tag a directory
  //   you nonetheless want indexed; only an explicit false disables it.

  // "response_max_bytes": 1048576,
  //   Ceiling on a SINGLE MCP tool response, in bytes, enforced at the
  //   call_tool chokepoint. Over the cap the call returns a structured error
  //   naming both the size and the limit -- never a shortened body presented as
  //   complete, because a caller cannot tell the difference. This is a
  //   RESPONSE limit and is deliberately separate from `max_file_size`, which
  //   is an INDEXING limit; before this key existed the indexing cap bounded
  //   reply size by coincidence, from another subsystem, with no test pinning
  //   the relationship. Raise it if you genuinely need larger single replies.

  // "max_folder_files": 2000,
  //   Maximum number of files to index when indexing a local folder.
  //   Prevents accidental massive indexing jobs. Monorepos often exceed this;
  //   when the cap drops files the index result, resolve_repo, and search _meta
  //   all report `truncated` with the discovered/indexed counts. Raise this and
  //   re-index if files are missing from results.

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

  // "identity_mode": "git",
  //   How index_folder derives the repo identifier for a local path.
  //   Existing indexes keep their current identity regardless of this
  //   setting — this only affects NEW indexes.
  //
  //   Choices:
  //     "local" (default) — repo ID is `local/<basename>-<hash>`.
  //       No git subprocess, no remote detection. Fast and portable.
  //       Each folder gets its own index. Works for non-git projects,
  //       local-only clones, and simple git workflows.
  //
  //     "git" — repo ID is `<owner>/<repo>` derived from the origin
  //       remote URL. Runs a git subprocess on every index/reindex.
  //       Enables monorepo subdir merging (multiple subdirs of the
  //       same git root share one index). Requires a git working tree
  //       with an origin remote. Falls back to local when detection
  //       fails.
  //
  //   To switch an existing index: run invalidate_cache first, then
  //   re-index with the new mode.

  // "git_root_identity": true,
  //   Deprecated boolean alias for identity_mode. When identity_mode
  //   is not set, `true` here is equivalent to `"identity_mode": "git"`.
  //   Prefer identity_mode for new configurations.

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

  // === Tool Profile ===
  // Controls how many tools are loaded into the LLM context.
  //   "core"     — ~16 essential tools (indexing, search, retrieval). Lowest token cost.
  //   "standard" — core + analytics, architecture, quality tools (~40 tools).
  //   "full"     — all tools including refactoring, session, and diagnostics (default).
  // Tip: "core" saves ~5-6k schema tokens per session.
  // "tool_profile": "full",

  // === Tool Surface ===
  // Selects how tools are presented to the LLM. "full" advertises every tool
  // schema (composes with tool_profile above). "counter" collapses the surface
  // to a 3-tool front door (order/menu/route) that dispatches any tool on
  // demand — maximum token savings, all capability preserved. New installs
  // default to "counter"; set "full" here to advertise all tool schemas.
  // "tool_surface": "full",

  // === Compact Schemas ===
  // When true, strips rarely-used advanced parameters (debug, fusion, semantic_*,
  // fuzzy_*, etc.) from tool schemas. The server still accepts them — they're just
  // hidden from the LLM to save tokens. Saves ~1-2k tokens on top of any profile.
  // "compact_schemas": false,

  // === Skill Advisor ===
  // "advise" makes audit_agent_config / suggest_corrections flag always-resident
  // config sections whose symbol and path references resolve into a single
  // subtree of the repo — content that costs tokens on every turn but only
  // matters when you are working in that subtree. The suggestion is to move the
  // prose into a skill and leave a pointer; nothing is rewritten or generated.
  // Advisory only, and it does NOT measure whether a section went unused —
  // only what it costs and where it points.
  // "skill_advisor_mode": "off",

  // === Server Output ===
  // Controls how tool responses are emitted:
  //   "raw"      - always emit JSON output.
  //   "encoded"  - always emit MUNCH-encoded output.
  //   "adaptive" - compare JSON vs MUNCH size and encode only when savings clear
  //                the threshold below (default).
  // Legacy aliases "json"/"compact"/"auto" are still accepted.
  // "server_output": "adaptive",
  // "server_output_threshold": 0.15,
  //   Minimum savings ratio required for adaptive mode to emit MUNCH.
  //   Example: 0.15 means encoded output must be at least 15% smaller.

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

  // === Tier-control escape hatch (issue #299) ===
  // By default, `set_tool_tier` and `announce_model` survive `disabled_tools`
  // so users can't lock themselves out of in-session tier switching. Set this
  // to true to opt out of that safety net — useful when you're at a hard tool
  // cap (e.g. Antigravity's 50-tool limit) and want to claw back two slots,
  // and you accept that you can't switch tiers mid-session.
  // "allow_disabling_tier_controls": false,

  // === Tool Tier Bundles ===
  // Which tools belong to each tier. Edit freely. Both tool_profile (below)
  // and the runtime set_tool_tier / announce_model tools read from here.
  // NOTE: disabled_tools applies AFTER tier filtering — a tool listed both
  // in a bundle and in disabled_tools will not be exposed regardless of tier.
  "tool_tier_bundles": {{
    "core": [
      "index_repo", "index_folder", "index_file",
      "list_repos", "resolve_repo",
      "get_repo_outline", "get_file_tree", "get_file_outline",
      "search_symbols", "get_symbol_source", "get_file_content",
      "search_text", "get_context_bundle", "get_ranked_context",
      "assemble_task_context",
      "find_importers", "find_references"
    ],
    "standard": [
      "index_repo", "index_folder", "index_file",
      "list_repos", "resolve_repo",
      "get_repo_outline", "get_file_tree", "get_file_outline",
      "search_symbols", "get_symbol_source", "get_file_content",
      "search_text", "get_context_bundle", "get_ranked_context",
      "assemble_task_context",
      "find_importers", "find_references",
      "summarize_repo", "embed_repo", "index_dependency", "suggest_queries",
      "search_columns", "check_references",
      "get_dependency_graph", "get_class_hierarchy",
      "get_related_symbols", "get_call_hierarchy",
      "get_blast_radius", "check_rename_safe", "check_delete_safe", "check_edit_safe",
      "find_implementations",
      "get_impact_preview", "get_changed_symbols",
      "get_symbol_diff", "get_symbol_provenance",
      "get_pr_risk_profile", "get_endpoint_impact", "get_symbol_complexity",
      "get_churn_rate", "get_delivery_metrics", "get_parity_map", "get_hotspots",
      "get_symbol_importance", "get_repo_map", "find_dead_code",
      "get_dead_code_v2", "get_untested_symbols", "find_similar_symbols",
      "get_repo_health", "search_ast", "winnow_symbols",
      "get_dependency_cycles", "get_coupling_metrics",
      "get_layer_violations", "get_cross_repo_map", "get_group_contracts",
      "get_tectonic_map", "get_signal_chains", "get_decorator_census",
      "get_architecture_metrics", "render_diagram",
      "get_project_intel", "list_workspaces", "invalidate_cache"
    ]
  }},

  // === Model → Tier Map ===
  // Maps model identifiers (self-reported by the agent via plan_turn(model=...)
  // or announce_model) to a tier. Matching is fuzzy: normalize (lowercase,
  // strip provider prefix / date suffix / bracket suffix), then try exact,
  // glob, substring, "*", hardcoded "full" fallback in that order.
  // Keep keys specific where possible: very short substrings (e.g. "o1") can
  // over-match model ids that merely contain that token.
  "model_tier_map": {{
    "claude-opus": "full",
    "claude-sonnet": "standard",
    "claude-haiku": "core",
    "gpt-4o": "standard",
    "gpt-5": "full",
    "o1": "full",
    "llama": "core",
    "*": "full"
  }},

  // === Adaptive Tiering (opt-in) ===
  // When true, the exposed tool list narrows at runtime based on the model
  // identifier self-reported by the agent via plan_turn(model=...) or
  // announce_model(). When false (default), the static tool_profile above
  // controls the exposed tools for the whole session — the runtime tools
  // accept their arguments but do not switch tiers. set_tool_tier is always
  // honored regardless of this flag (explicit user override, not automatic
  // behavior).
  // "adaptive_tiering": false,

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

  // === Runtime & Org HTTP Ingest ===
  // Off-by-default write endpoints on the HTTP transports. Each is a
  // two-key turn: the flag below PLUS a JCODEMUNCH_HTTP_TOKEN bearer.
  // "runtime_ingest_enabled": false,
  //   Accept POST /runtime/otel, /runtime/sql, /runtime/stack (live trace
  //   ingest). Off by default — these are write endpoints.
  // "org_ingest_enabled": false,
  //   Accept POST /org/report so seats can report savings to this org host
  //   (team-SKU rollup). Off by default.
  // "runtime_ingest_max_body_bytes": 5242880,
  //   Per-request body cap for the ingest endpoints, in bytes (default
  //   5 MB). Checked on both the on-wire and decompressed size (gzip-bomb
  //   guard). Minimum 1024.

  // === Licensing ===
  // "license_key": "",
  //   jCodeMunch license key. Gates the org-rollup team feature ONLY —
  //   every individual tool, indexing, and search stays free. Validated
  //   online against the licensing backend (sticky-offline; 14-day grace
  //   for a new org). Also settable via JCODEMUNCH_LICENSE_KEY. Check
  //   status with the `license` CLI.

  // === Watcher ===
  // "watch": false,
  //   Enable automatic reindexing when files change.
  //   Use "jcodemunch-mcp watch <paths>" CLI command to activate.
  // "watch_debounce_ms": 2000,
  //   Milliseconds to wait after a file change before reindexing.
  //   Higher values reduce CPU usage but slower detection.
  // "watch_paths": [],
  //   Folders the `watch` CLI monitors when no paths are passed on the
  //   command line. Empty = watch the indexed repo's own source root.
  // "watch_extra_ignore": [],
  //   Extra gitignore-style patterns the watcher ignores on top of the
  //   index's own ignore rules (e.g. ["*.log", "build/"]). Changes to
  //   matching files never trigger a reindex.
  // "watch_follow_symlinks": false,
  //   Follow symlinked directories while watching. Default false avoids
  //   reindex loops and watching files outside the repo.
  // "watch_idle_timeout": null,
  //   Seconds of no file activity after which the watcher exits. null =
  //   run until stopped. Handy for one-shot "watch until quiet" CI runs.
  // "watch_log": null,
  //   Path to a file the watcher appends reindex activity to.
  //   null = log to stderr only.
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
  // "worktree_base_path": "",
  //   Absolute path for git worktrees created by hook-event.
  //   Default: <cwd>/.claude/worktrees/<name> (Claude Code convention).
  //   Set to e.g. "~/.claude-worktrees" to store all worktrees centrally.

  // === Logging ===
  // "log_level": "WARNING",
  //   DEBUG, INFO, WARNING, ERROR, CRITICAL. WARNING is default for less noise.
  // "log_file": null,
  //   Path to log file. null = write to stderr.

  // === Identity & Indexing Behavior ===
  // "git_root_identity": true,
  //   When the indexed path lives inside a git working tree, anchor
  //   `index_folder` at the git root. Indexing a subdir then walks the
  //   subdir only, but file paths are stored git-root-relative — so
  //   `index ./packages` and `index ./scripts` coalesce into one repo
  //   index per clone. Useful for monorepos and worktrees.
  //   Set false to revert to pre-v1.96 behavior: `local/<folder>-<hash>`
  //   identity derived from the resolved path, with no retargeting.
  //   Choose `false` when you deliberately want a subdir to be its own
  //   independent index, separate from any enclosing git repo.
  //   (v1.108.2: the git-root probe is now properly skipped when this
  //   is false — prior versions still paid the probe cost.)
  // "git_blame_enabled": true,
  //   Run the git_blame context provider during indexing to attach
  //   `last_author` and `last_modified` to each file's context. The
  //   walk is bounded (latest 20k commits or 2 years, whichever fires
  //   first; 10s wall-clock cap). On legacy repos with very deep
  //   history those bounds may still not be enough — set false to
  //   skip the probe entirely. Index still builds; only the blame
  //   metadata is omitted.
  // "cross_repo_default": false,
  //   When true, find_importers / get_blast_radius / get_dependency_graph
  //   traverse OTHER indexed repos by default, not just the focal one. A
  //   per-call `cross_repo` argument still overrides this. Default false
  //   keeps results scoped to the repo you asked about.
  // "discovery_hint": true,
  //   Append a short "next tools to try" hint to certain results so an
  //   agent can chain follow-up queries. Set false to suppress the hint.

  // === Summarization input policy ===
  // "summarize_from_docstrings": true,
  //   Controls the Tier 1 summarizer (docstring-first-sentence extraction).
  //     true  - Default. Extract a one-line summary from each symbol's
  //             docstring when present. Zero token cost.
  //     false - Skip Tier 1 entirely. Summaries fall through to Tier 2
  //             (AI summary, if configured) and then Tier 3 (signature
  //             fallback). Recommended for security-conscious deployments
  //             that want to eliminate the indirect-prompt-injection
  //             surface docstring-derived summaries introduce. The host
  //             agent's tool-output handling remains the primary IPI
  //             control; this flag closes the docstring channel as a
  //             defense-in-depth measure.

  // === Cache shape ===
  // "cache_mode": "full",
  //   Controls what jcodemunch persists to ~/.code-index/<repo>/ on disk.
  //     full          - Default. Symbol table + cached file bodies + outlines.
  //                     Required for get_symbol_source and get_file_content.
  //     metadata_only - Symbol table + outlines only. File bodies are extracted
  //                     in memory during indexing and discarded; no bodies/
  //                     directory is written. get_symbol_source and
  //                     get_file_content return a "metadata_only_mode" error
  //                     when invoked. All other tools (search_symbols,
  //                     find_references, get_file_outline, etc.) work
  //                     normally because they don't need bodies.
  //   Set metadata_only when policy disallows a second on-disk copy of source
  //   (managed-endpoint deployments where ~/.code-index/ would otherwise be
  //   covered by Time Machine / iCloud / OneDrive sync that the canonical
  //   clone is excluded from).

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
  // "perf_telemetry_enabled": false,
  //   Persist per-tool latency rows (tool, duration_ms, ok, repo) to
  //   ~/.code-index/telemetry.db. The in-memory ring (queryable via
  //   analyze_perf and get_session_stats) is always tracked; this flag
  //   only controls durable persistence.
  // "perf_telemetry_max_rows": 100000,
  //   Rolling cap on persisted perf rows; oldest rows trimmed in 1k batches
  //   once exceeded. Lower this on small disks or short-lived deployments.
  // "runtime_max_rows": 100000,
  //   Rolling cap on rows in the runtime_* tables (per-repo). Hits the cap →
  //   FIFO eviction in 1k batches. Phase 0 ships the schema; Phase 1+ ships
  //   the ingest tools that fill these tables.
  // "runtime_redact_enabled": true,
  //   Enforce PII redaction at the runtime trace ingest chokepoint. Set
  //   false ONLY for offline debugging on synthetic data — never on
  //   production traces.
  // "summarizer_concurrency": 4,
  //   Number of parallel threads for AI summarization.
  //   Higher = faster indexing but more API calls.
  // "summarizer_max_failures": 3,
  //   Consecutive batch failures before the AI summarizer gives up and
  //   falls back to signature summaries for remaining symbols.
  //   Set 0 to disable the circuit breaker (never stop retrying).
  // "openai_extra_body": {{}},
  //   Extra JSON merged into every OpenAI-compatible /chat/completions (and
  //   /responses) summarizer request. Use it to pass provider-specific knobs
  //   the standard payload doesn't expose. Most common case: disabling a
  //   local thinking model's reasoning so the output budget isn't spent on
  //   reasoning tokens (which silently degrades summaries to generic
  //   signatures). For llama.cpp / Qwen:
  //     "openai_extra_body": {{"chat_template_kwargs": {{"enable_thinking": false}}}}
  //   Also settable via the JCODEMUNCH_OPENAI_EXTRA_BODY env var (JSON object);
  //   config keys win per-key when both are set. See issue #323.

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
  // "session_token_budget": 0,
  //   Advisory session budget over response tokens served. When set (> 0),
  //   responses carry _meta.budget at >=80% (approaching) and >=100% (over),
  //   and get_session_stats always reports the budget block. Never blocks
  //   or truncates — awareness only. 0 = disabled. Once plan_turn has run
  //   3+ times, the block also carries actual_vs_estimated — how hot or
  //   cold plan_turn's consumption estimates run this session.
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
  //   Example: "all-MiniLM-L6-v2". Requires the sentence-transformers package
  //   (pip install 'jcodemunch-mcp[semantic]').
  //   When set, takes priority over GOOGLE_API_KEY and OPENAI_API_KEY
  //   embeddings AND over the bundled zero-config ONNX encoder. Setting it is
  //   an explicit choice, so it outranks the default (v1.108.286, #488).
  //   Leave it empty to use the bundled encoder.
  //   If the package is not installed the setting is SKIPPED rather than
  //   honoured into an error, and embed_repo reports it in provider_skipped.
  // "allow_remote_summarizer": false,
  //   Allow remote LLM endpoints for summarization (security risk).
  //   Default false blocks non-local summarization.
  // "allow_paid_summaries": false,
  //   Allow AUTO-detected PAID cloud providers (anthropic/gemini/minimax/glm/
  //   openrouter, or remote OpenAI) to summarize just because their API key is
  //   present in the environment. Default false so a stray cloud key never
  //   silently bills during indexing. Naming a provider explicitly via
  //   "summarizer_provider" is always honored and does NOT need this flag.
  // "path_map": "",
  //   Cross-platform path remapping. Format: "orig1=new1,orig2=new2".
  //   Allows indexes built on Linux to work on Windows and vice versa.

  // === Agent Selector (model routing) ===
  // "agent_selector": {{}},
  //   Config for the optional model-advisory layer (complexity scoring →
  //   model routing, modes off/manual/auto). Empty {{}} uses the built-in
  //   defaults; populate to override the per-complexity model batting order.

  // === Enrichment (LSP) ===
  // "enrichment": {{}},
  //   Opt-in compiler-grade call-graph resolution via Language Servers
  //   (pyright / gopls / ts-language-server / rust-analyzer). Empty {{}} =
  //   disabled (AST-only call graph). Populate to configure per-language
  //   server commands.

  // === Mermaid Viewer Integration ===
  // "render_diagram_viewer_enabled": false,
  //   When true, render_diagram exposes an extra boolean parameter
  //   `open_in_viewer` in its tool schema. When the caller sets
  //   open_in_viewer=true, the produced mermaid is written as a
  //   self-contained HTML file under <index_path>/temp/mermaid/ and
  //   opened with the viewer resolved via `mermaid_viewer_path`.
  //   When false (default), the parameter is hidden from the schema.
  //   The temp folder is cleaned on server startup and shutdown.

  // "mermaid_viewer_path": "",
  //   Absolute path to the mmd-viewer executable. Used only when
  //   render_diagram_viewer_enabled is true and the caller requests
  //   open_in_viewer=true.
  //   - Explicit path: used as-is (e.g. "C:/tools/mmd-viewer.exe").
  //   - Empty string: falls back to "mmd-viewer" on $PATH.
  //   If neither resolves, render_diagram still returns the mermaid
  //   markup and adds a non-fatal `viewer_error` field to the result.
}}
'''
