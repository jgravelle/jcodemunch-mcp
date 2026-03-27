"""MCP server for jcodemunch-mcp."""

import argparse
import asyncio
import functools
import hmac
import json
import jsonschema
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

from mcp.server import Server
from mcp.types import Tool, TextContent, Resource

from . import __version__
from . import config as config_module
from .tools.index_repo import index_repo
from .tools.index_folder import index_folder
from .tools.index_file import index_file
from .tools.list_repos import list_repos
from .tools.resolve_repo import resolve_repo
from .tools.get_file_tree import get_file_tree
from .tools.get_file_outline import get_file_outline
from .tools.get_file_content import get_file_content
from .tools.get_symbol import get_symbol_source
from .tools.search_symbols import search_symbols
from .tools.invalidate_cache import invalidate_cache
from .tools.search_text import search_text
from .tools.get_repo_outline import get_repo_outline
from .tools.find_importers import find_importers
from .tools.find_references import find_references
from .tools.check_references import check_references
from .tools.get_session_stats import get_session_stats
from .tools.get_dependency_graph import get_dependency_graph
from .tools.get_blast_radius import get_blast_radius
from .tools.get_symbol_diff import get_symbol_diff
from .tools.get_class_hierarchy import get_class_hierarchy
from .tools.get_related_symbols import get_related_symbols
from .tools.get_symbol_importance import get_symbol_importance
from .tools.find_dead_code import find_dead_code
from .tools.get_changed_symbols import get_changed_symbols
from .tools.suggest_queries import suggest_queries
from .tools.search_columns import search_columns
from .tools.get_context_bundle import get_context_bundle
from .tools.get_ranked_context import get_ranked_context
from .tools.embed_repo import embed_repo
from .parser.symbols import VALID_KINDS
from .summarizer import get_provider_name
from .reindex_state import await_freshness_if_strict
from .path_map import ENV_VAR as _PATH_MAP_ENV_VAR

try:
    from .watcher import watch_folders, WatcherError
except ImportError:
    watch_folders = None  # type: ignore[assignment, misc]
    WatcherError = type("WatcherError", (Exception,), {})  # type: ignore[assignment, misc]


# Tools excluded from strict freshness mode (don't wait for reindex)
_EXCLUDED_FROM_STRICT = frozenset({
    "list_repos",
    "resolve_repo",
    "get_session_stats",
    "index_repo",
    "index_folder",
    "index_file",
    "invalidate_cache",
})


logger = logging.getLogger(__name__)


def _default_use_ai_summaries() -> bool:
    """Return the default for use_ai_summaries, respecting config (including env var fallback)."""
    return config_module.get("use_ai_summaries", True)


def _parse_watcher_flag(value: Optional[str]) -> bool:
    """Parse the --watcher flag value.

    None = not provided (disabled).
    'true'/'1'/'yes' = enabled (const from nargs='?').
    'false'/'0'/'no' = explicitly disabled.
    """
    if value is None:
        return False
    return value.lower() not in ("0", "no", "false")


def _get_watcher_enabled(args) -> bool:
    """Determine if the watcher should be enabled for the serve subcommand.

    Precedence (highest to lowest):
      1. --watcher CLI flag
      2. config file "watch" key  (JCODEMUNCH_WATCH env var is a fallback for this key
         when it is absent from config.jsonc — handled by config._apply_env_var_fallback)
    """
    flag = getattr(args, "watcher", None)
    if flag is not None:
        return _parse_watcher_flag(flag)
    return config_module.get("watch", False)


_BOOL_TRUE = frozenset(("true", "1", "yes", "on"))
_BOOL_FALSE = frozenset(("false", "0", "no", "off"))


def _coerce_arguments(arguments: dict, schema: dict) -> dict:
    """Coerce stringified values to their expected types per JSON schema.

    Handles boolean ("true"/"false"), integer ("5"), and number ("3.14")
    without eval. Unknown or already-correct types are passed through unchanged.
    """
    props = schema.get("properties", {})
    if not props:
        return arguments
    result = {}
    for k, v in arguments.items():
        if k in props and isinstance(v, str):
            expected = props[k].get("type")
            if expected == "boolean":
                if v.lower() in _BOOL_TRUE:
                    v = True
                elif v.lower() in _BOOL_FALSE:
                    v = False
            elif expected == "integer":
                try:
                    v = int(v)
                except (ValueError, TypeError):
                    pass
            elif expected == "number":
                try:
                    v = float(v)
                except (ValueError, TypeError):
                    pass
        result[k] = v
    return result


_TOOL_SCHEMAS: dict[str, dict] | None = None


def _build_language_enum() -> list[str]:
    """Build language enum from config, falling back to all registry languages."""
    languages = config_module.get("languages")
    if languages is None:
        from .parser.languages import LANGUAGE_REGISTRY
        return sorted(LANGUAGE_REGISTRY.keys())
    return languages


async def _ensure_tool_schemas() -> dict[str, dict]:
    """Lazy-initialize the tool name → inputSchema lookup for type coercion.

    Uses our own list_tools() — no coupling to private MCP SDK internals.
    Populated once on the first tool call, then cached for the process lifetime.
    """
    global _TOOL_SCHEMAS
    if _TOOL_SCHEMAS is None:
        tools = await list_tools()
        _TOOL_SCHEMAS = {t.name: t.inputSchema for t in tools if t.inputSchema}
    return _TOOL_SCHEMAS


# Create server
server = Server("jcodemunch-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    tools = [
        Tool(
            name="index_repo",
            description="Index a GitHub repository's source code. Fetches files, parses ASTs, extracts symbols, and saves to local storage. Set JCODEMUNCH_USE_AI_SUMMARIES=false to disable AI summaries globally.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "GitHub repository URL or owner/repo string"
                    },
                    "use_ai_summaries": {
                        "type": "boolean",
                        "description": "Use AI to generate symbol summaries. Supports Anthropic, Gemini, OpenAI-compatible endpoints, MiniMax, and GLM-5 via env vars. When false, uses docstrings or signature fallback.",
                        "default": True
                    },
                    "extra_ignore_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional gitignore-style patterns to exclude from indexing (merged with JCODEMUNCH_EXTRA_IGNORE_PATTERNS env var)"
                    },
                    "incremental": {
                        "type": "boolean",
                        "description": "When true and an existing index exists, only re-index changed files.",
                        "default": True
                    }
                },
                "required": ["url"]
            }
        ),
        Tool(
            name="index_folder",
            description="Index a local folder containing source code. Response includes `discovery_skip_counts` (files filtered per reason), `no_symbols_count`/`no_symbols_files` (files with no extractable symbols) for diagnosing missing files. Set JCODEMUNCH_USE_AI_SUMMARIES=false to disable AI summaries globally.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to local folder (absolute or relative, supports ~ for home directory)"
                    },
                    "use_ai_summaries": {
                        "type": "boolean",
                        "description": "Use AI to generate symbol summaries. Supports Anthropic, Gemini, OpenAI-compatible endpoints, MiniMax, and GLM-5 via env vars. When false, uses docstrings or signature fallback.",
                        "default": True
                    },
                    "extra_ignore_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional gitignore-style patterns to exclude from indexing (merged with JCODEMUNCH_EXTRA_IGNORE_PATTERNS env var)"
                    },
                    "follow_symlinks": {
                        "type": "boolean",
                        "description": "Whether to include symlinked files in indexing. Symlinked directories are never followed (prevents infinite loops from circular symlinks). Default false for security.",
                        "default": False
                    },
                    "incremental": {
                        "type": "boolean",
                        "description": "When true and an existing index exists, only re-index changed files.",
                        "default": True
                    }
                },
                "required": ["path"]
            }
        ),
        Tool(
            name="index_file",
            description="Index a single file within an existing index. Faster than index_folder for surgical updates after editing a file. The file must be within an already-indexed folder's source_root. Can also add new files not yet in the index.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file to index"
                    },
                    "use_ai_summaries": {
                        "type": "boolean",
                        "description": "Use AI to generate symbol summaries. Supports Anthropic, Gemini, OpenAI-compatible endpoints, MiniMax, and GLM-5 via env vars. When false, uses docstrings or signature fallback.",
                        "default": True
                    },
                    "context_providers": {
                        "type": "boolean",
                        "description": "Whether to run context providers",
                        "default": True
                    }
                },
                "required": ["path"]
            }
        ),
        Tool(
            name="list_repos",
            description="List all indexed repositories.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="resolve_repo",
            description="Resolve a filesystem path to its indexed repo identifier. O(1) lookup — faster than list_repos for finding a single repo. Accepts repo root, worktree, subdirectory, or file path.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute filesystem path (repo root, worktree, subdirectory, or file)"
                    }
                },
                "required": ["path"]
            }
        ),
        Tool(
            name="get_file_tree",
            description="Get the file tree of an indexed repository, optionally filtered by path prefix.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "path_prefix": {
                        "type": "string",
                        "description": "Optional path prefix to filter (e.g., 'src/utils')",
                        "default": ""
                    },
                    "include_summaries": {
                        "type": "boolean",
                        "description": "Include file-level summaries in the tree nodes",
                        "default": False
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_file_outline",
            description="Get all symbols (functions, classes, methods) in a file with full signatures (including parameter names) and summaries. Use signatures to review naming at parameter granularity without reading the full file. Pass repo and file_path (e.g. 'src/main.py').",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file within the repository (e.g., 'src/main.py')"
                    },
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of file paths to query in batch mode. Returns a grouped results array."
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_symbol_source",
            description="Get full source of one symbol (symbol_id → flat object) or many (symbol_ids[] → {symbols, errors}). Supports verify and context_lines.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "symbol_id": {
                        "type": "string",
                        "description": "Single symbol ID — returns flat symbol object"
                    },
                    "symbol_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Multiple symbol IDs — returns {symbols, errors}"
                    },
                    "verify": {
                        "type": "boolean",
                        "description": "Verify content hash matches stored hash (detects source drift)",
                        "default": False
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Number of lines before/after symbol to include for context",
                        "default": 0
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_file_content",
            description="Get cached source for a file, optionally sliced to a line range.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file within the repository (e.g., 'src/main.py')"
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Optional 1-based start line (inclusive)"
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Optional 1-based end line (inclusive)"
                    }
                },
                "required": ["repo", "file_path"]
            }
        ),
        Tool(
            name="search_symbols",
            description="Search for symbols matching a query across the entire indexed repository. Returns matches with signatures and summaries.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query (matches symbol names, signatures, summaries, docstrings)"
                    },
                    "kind": {
                        "type": "string",
                        "description": "Optional filter by symbol kind",
                        "enum": ["function", "class", "method", "constant", "type", "template", "import"]
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Optional glob pattern to filter files (e.g., 'src/**/*.py')"
                    },
                    "language": {
                        "type": "string",
                        "description": "Optional filter by language",
                        "enum": _build_language_enum()
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return (ignored when token_budget is set)",
                        "default": 10
                    },
                    "token_budget": {
                        "type": "integer",
                        "description": "Token budget cap. When set, results are sorted by score and greedily packed until the budget is exhausted. Overrides max_results. Reports token_budget, tokens_used, and tokens_remaining in _meta."
                    },
                    "detail_level": {
                        "type": "string",
                        "description": "Controls result verbosity. 'compact' returns id/name/kind/file/line only (~15 tokens each, best for broad discovery). 'standard' returns signatures and summaries (default). 'full' inlines source code, docstring, and end_line — equivalent to search + get_symbol in one call.",
                        "enum": ["compact", "standard", "full"],
                        "default": "standard"
                    },
                    "debug": {
                        "type": "boolean",
                        "description": "When true, each result includes a score_breakdown showing per-field scoring contributions (name_exact, name_contains, name_word_overlap, signature_phrase, signature_word_overlap, summary_phrase, summary_word_overlap, keywords, docstring_word_overlap). Also adds candidates_scored to _meta.",
                        "default": False
                    }
                },
                "required": ["repo", "query"]
            }
        ),
        Tool(
            name="invalidate_cache",
            description="Delete the index and cached files for a repository. Forces a full re-index on next index_repo or index_folder call.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="search_text",
            description="Full-text search across indexed file contents. Useful when symbol search misses (e.g., string literals, comments, config values). Supports regex (is_regex=true) and context lines around matches (context_lines=N, like grep -C).",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "query": {
                        "type": "string",
                        "description": "Text to search for. Case-insensitive substring by default. Set is_regex=true for full regex (e.g. 'estimateToken|tokenEstimat|\\.length.*0\\.25')."
                    },
                    "is_regex": {
                        "type": "boolean",
                        "description": "When true, treat query as a Python regex (re.search, case-insensitive). Supports alternation (|), character classes, lookaheads, etc.",
                        "default": False
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Optional glob pattern to filter files (e.g., '*.py')"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matching lines to return",
                        "default": 20
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Lines of context to include before and after each match (like grep -C N). Essential for understanding code around matches.",
                        "default": 0
                    }
                },
                "required": ["repo", "query"]
            }
        ),
        Tool(
            name="get_repo_outline",
            description="Get a high-level overview of an indexed repository: directories, file counts, language breakdown, symbol counts. Lighter than get_file_tree.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="find_importers",
            description="Find all files that import a given file. Answers 'what uses this file?'. has_importers=false on a result means that importer is itself unreachable (dead code chain). Supports dbt {{ ref() }} edges. Use file_paths for batch queries.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier"},
                    "file_path": {"type": "string", "description": "Target file path within the repo (e.g. 'src/features/intake/IntakeService.js'). Use for single-file queries. Cannot be used together with file_paths."},
                    "file_paths": {"type": "array", "items": {"type": "string"}, "description": "List of target file paths for batch queries. Returns a results array. Cannot be used together with file_path."},
                    "max_results": {"type": "integer", "default": 50, "description": "Maximum results per file"},
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="find_references",
            description="Find all files that import or reference an identifier. Answers 'where is this used?'. Supports dbt {{ ref() }} edges. Use identifiers for batch queries.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier"},
                    "identifier": {"type": "string", "description": "Symbol or module name to search for (e.g. 'bulkImport', 'IntakeService'). Use for single-identifier queries. Cannot be used together with identifiers."},
                    "identifiers": {"type": "array", "items": {"type": "string"}, "description": "List of symbol or module names to search for (batch mode). Returns a results array. Cannot be used together with identifier."},
                    "max_results": {"type": "integer", "default": 50, "description": "Maximum results"},
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="check_references",
            description="Check if an identifier is referenced anywhere: imports + file content. Combines find_references and search_text into one call. Returns is_referenced (bool) for quick dead-code detection. Accepts multiple identifiers in one call via identifiers param.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier"},
                    "identifier": {"type": "string", "description": "Single identifier to check"},
                    "identifiers": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Multiple identifiers to check in one call. Returns grouped results.",
                    },
                    "search_content": {
                        "type": "boolean", "default": True,
                        "description": "Also search file contents (not just imports). Set false for fast import-only check.",
                    },
                    "max_content_results": {
                        "type": "integer", "default": 20,
                        "description": "Max files to return per identifier for content search.",
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="search_columns",
            description="Search column metadata across indexed models. Works with any ecosystem provider that emits column data (dbt, SQLMesh, database catalogs, etc.). Returns model name, file path, column name, and description. Use instead of grep/search_text for column discovery — 77% fewer tokens.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query (matches column names and descriptions)"
                    },
                    "model_pattern": {
                        "type": "string",
                        "description": "Optional glob to filter by model name (e.g., 'fact_*', 'dim_provider')"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 20
                    }
                },
                "required": ["repo", "query"]
            }
        ),
        Tool(
            name="get_context_bundle",
            description="Get full source + imports for one or more symbols in one call. Multi-symbol bundles deduplicate shared imports. Set include_callers=true to also list files that import the symbol's file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "symbol_id": {
                        "type": "string",
                        "description": "Single symbol ID (backward-compatible). Use symbol_ids for multi-symbol bundles."
                    },
                    "symbol_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of symbol IDs for a multi-symbol bundle. Imports are deduplicated across symbols that share a file."
                    },
                    "include_callers": {
                        "type": "boolean",
                        "description": "When true, each symbol entry includes a 'callers' list of files that directly import its defining file.",
                        "default": False
                    },
                    "output_format": {
                        "type": "string",
                        "description": "'json' (default) or 'markdown' — markdown renders a paste-ready document with imports, docstrings, and source blocks.",
                        "enum": ["json", "markdown"],
                        "default": "json"
                    }
                },
                "required": ["repo"]
            }
        ),
        Tool(
            name="get_session_stats",
            description="Get token savings stats for the current MCP session. Returns tokens saved and cost avoided (this session and all-time), per-tool breakdown, session duration, and cumulative totals. Use to see how much jCodeMunch has saved you.",
            inputSchema={
                "type": "object",
                "properties": {},
            }
        ),
        Tool(
            name="get_dependency_graph",
            description="Get the file-level dependency graph for a given file. Traverses import relationships up to 3 hops. Use to understand what a file depends on ('imports'), what depends on it ('importers'), or both. Prerequisite for blast radius analysis.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "file": {
                        "type": "string",
                        "description": "File path within the repo (e.g. 'src/server.py')"
                    },
                    "direction": {
                        "type": "string",
                        "description": "'imports' (files this file depends on), 'importers' (files that depend on this file), or 'both'",
                        "enum": ["imports", "importers", "both"],
                        "default": "imports"
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Number of hops to traverse (1–3)",
                        "default": 1
                    }
                },
                "required": ["repo", "file"]
            }
        ),
        Tool(
            name="get_symbol_diff",
            description="Diff symbol sets between two indexed snapshots. Shows added, removed, and changed symbols. Branch workflow: index branch A as repo-main, index branch B as repo-feature, then diff.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_a": {"type": "string", "description": "First repo identifier (the 'before' snapshot)"},
                    "repo_b": {"type": "string", "description": "Second repo identifier (the 'after' snapshot)"},
                },
                "required": ["repo_a", "repo_b"],
            },
        ),
        Tool(
            name="get_class_hierarchy",
            description="Get the full inheritance hierarchy for a class: ancestors (base classes via extends/implements) and descendants (subclasses/implementors). Works across Python, Java, TypeScript, C#, and any language where class signatures contain 'extends' or 'implements'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier (owner/repo or just repo name)"},
                    "class_name": {"type": "string", "description": "Name of the class to analyse"},
                },
                "required": ["repo", "class_name"],
            },
        ),
        Tool(
            name="get_related_symbols",
            description="Find symbols related to a given symbol using heuristic clustering: same-file co-location (weight 3), shared importers (weight 1.5), and name-token overlap (weight 0.5/token). Useful for discovering what else to read when exploring an unfamiliar codebase.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier (owner/repo or just repo name)"},
                    "symbol_id": {"type": "string", "description": "ID of the symbol to find relatives for"},
                    "max_results": {"type": "integer", "description": "Maximum results (default 10, max 50)", "default": 10},
                },
                "required": ["repo", "symbol_id"],
            },
        ),
        Tool(
            name="suggest_queries",
            description="Suggest search queries, entry-point files, and index stats. Good first call on an unfamiliar repo — surfaces most-imported files, top keywords, and ready-to-run example queries.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier (owner/repo or just repo name)"},
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="get_blast_radius",
            description="Find all files affected by changing a symbol. Returns confirmed files (import + name match) and potential files (import only, e.g. wildcard). Use before renaming or deleting a symbol.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "symbol": {
                        "type": "string",
                        "description": "Symbol name or ID to analyse (e.g. 'calculateScore' or a full symbol ID)"
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Import hops to traverse (1 = direct importers only, max 3). Default 1.",
                        "default": 1
                    }
                },
                "required": ["repo", "symbol"]
            }
        ),
        Tool(
            name="wait_for_fresh",
            description="Wait for a repo's in-progress watcher reindex to finish, then return the fresh result. In strict freshness mode, blocks up to timeout_ms. In relaxed mode (default), returns immediately.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "description": "Maximum time to wait in milliseconds (default 500)",
                        "default": 500
                    }
                },
                "required": ["repo"]
            }
        ),
    ]
    # Filter out disabled tools
    disabled = config_module.get("disabled_tools", [])
    if disabled:
        tools = [t for t in tools if t.name not in disabled]

    # SQL gating: auto-disable search_columns when SQL not in languages
    languages = config_module.get("languages")
    if languages is not None and "sql" not in languages:
        tools = [t for t in tools if t.name != "search_columns"]

    # Merge descriptions from config (runs after disabled_tools filter)
    _apply_description_overrides(tools)

    return tools


def _apply_description_overrides(tools: list) -> None:
    """Apply description overrides from config to tool schemas."""
    descriptions = config_module.get_descriptions()
    if not descriptions:
        return

    shared = descriptions.get("_shared", {})

    for tool in tools:
        raw = descriptions.get(tool.name)
        if raw is None:
            tool_desc: dict = {}
        elif isinstance(raw, str):
            # Flat format: "tool_name": "description" → override tool description only
            tool.description = raw
            tool_desc = {}
        else:
            tool_desc = raw

        # Nested format: override tool-level description via "_tool" key
        # "_tool": "" means "use hardcoded minimal base only" (empty string override)
        if "_tool" in tool_desc:
            tool.description = tool_desc["_tool"]

        # Override parameter descriptions (applies even if only _shared is set)
        if isinstance(tool.inputSchema, dict):
            props = tool.inputSchema.get("properties", {})
            for param_name, param_schema in props.items():
                if not isinstance(param_schema, dict):
                    continue
                # Tool-specific override takes precedence over _shared
                # Empty string means "use hardcoded minimal base only"
                desc_override = tool_desc.get(param_name)
                if desc_override is None:
                    desc_override = shared.get(param_name)
                if desc_override is not None:
                    props[param_name] = {**param_schema, "description": desc_override}


@server.list_resources()
async def list_resources() -> list[Resource]:
    """Return empty resource list for client compatibility (e.g. Windsurf)."""
    return []


@server.list_prompts()
async def list_prompts() -> list:
    """Return empty prompt list for client compatibility (e.g. Windsurf)."""
    return []


@server.call_tool(validate_input=False)
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    storage_path = os.environ.get("CODE_INDEX_PATH")
    logger.info("tool_call: %s args=%s", name, {k: v for k, v in arguments.items() if k != "content"})

    try:   # main handler try starts here, before coerce
        # Coerce stringified booleans/integers/numbers before routing
        schema = (await _ensure_tool_schemas()).get(name)
        if schema:
            arguments = _coerce_arguments(arguments, schema)
            try:
                jsonschema.validate(instance=arguments, schema=schema)
            except jsonschema.ValidationError as e:
                return [TextContent(type="text", text=json.dumps(
                    {"error": f"Input validation error: {e.message}"}, indent=2
                ))]

        # Strict freshness mode: wait for any in-progress reindex to complete
        # before serving query results (except for write/index tools).
        # MUST use asyncio.to_thread — threading.Event.wait() cannot run on the event loop.
        repo_arg = arguments.get("repo")
        if (name not in _EXCLUDED_FROM_STRICT and repo_arg):
            await asyncio.to_thread(await_freshness_if_strict, repo_arg, timeout_ms=500)

        # Project-level tool disabling: check if tool is disabled for this project
        # Global disabled tools are filtered out in list_tools() schema; project-level
        # rejection happens here since schema is global (can't be changed per-project).
        if config_module.is_tool_disabled(name, repo=repo_arg):
            return [TextContent(type="text", text=json.dumps({
                "error": (
                    f"Tool '{name}' is disabled in this project's configuration. "
                    f"Project-level tool disabling is set via the 'disabled_tools' key "
                    f"in the .jcodemunch.jsonc file. Remove '{name}' from 'disabled_tools' to re-enable."
                )
            }, indent=2))]

        if name == "index_repo":
            result = await index_repo(
                url=arguments["url"],
                use_ai_summaries=arguments.get("use_ai_summaries", _default_use_ai_summaries()),
                storage_path=storage_path,
                incremental=arguments.get("incremental", True),
                extra_ignore_patterns=arguments.get("extra_ignore_patterns"),
            )
        elif name == "index_folder":
            _ai = arguments.get("use_ai_summaries", _default_use_ai_summaries())
            result = await asyncio.to_thread(
                functools.partial(
                    index_folder,
                    path=arguments["path"],
                    use_ai_summaries=_ai,
                    storage_path=storage_path,
                    extra_ignore_patterns=arguments.get("extra_ignore_patterns"),
                    follow_symlinks=arguments.get("follow_symlinks", False),
                    incremental=arguments.get("incremental", True),
                )
            )
        elif name == "index_file":
            _ai = arguments.get("use_ai_summaries", _default_use_ai_summaries())
            result = await asyncio.to_thread(
                functools.partial(
                    index_file,
                    path=arguments["path"],
                    use_ai_summaries=_ai,
                    storage_path=storage_path,
                    context_providers=arguments.get("context_providers", True),
                )
            )
        elif name == "list_repos":
            result = await asyncio.to_thread(
                functools.partial(list_repos, storage_path=storage_path)
            )
        elif name == "resolve_repo":
            result = await asyncio.to_thread(
                functools.partial(
                    resolve_repo,
                    path=arguments["path"],
                    storage_path=storage_path,
                )
            )
        elif name == "get_file_tree":
            result = await asyncio.to_thread(
                functools.partial(
                    get_file_tree,
                    repo=arguments["repo"],
                    path_prefix=arguments.get("path_prefix", ""),
                    include_summaries=arguments.get("include_summaries", False),
                    storage_path=storage_path,
                )
            )
        elif name == "get_file_outline":
            result = await asyncio.to_thread(
                functools.partial(
                    get_file_outline,
                    repo=arguments["repo"],
                    file_path=arguments.get("file_path") or arguments.get("file"),
                    file_paths=arguments.get("file_paths"),
                    storage_path=storage_path,
                )
            )
        elif name == "get_file_content":
            result = await asyncio.to_thread(
                functools.partial(
                    get_file_content,
                    repo=arguments["repo"],
                    file_path=arguments["file_path"],
                    start_line=arguments.get("start_line"),
                    end_line=arguments.get("end_line"),
                    storage_path=storage_path,
                )
            )
        elif name == "get_symbol_source":
            result = await asyncio.to_thread(
                functools.partial(
                    get_symbol_source,
                    repo=arguments["repo"],
                    symbol_id=arguments.get("symbol_id"),
                    symbol_ids=arguments.get("symbol_ids"),
                    verify=arguments.get("verify", False),
                    context_lines=arguments.get("context_lines", 0),
                    storage_path=storage_path,
                )
            )
        elif name == "search_symbols":
            kind_filter = arguments.get("kind")
            if kind_filter and kind_filter not in VALID_KINDS:
                result = {"error": f"Unknown kind '{kind_filter}'. Valid values: {sorted(VALID_KINDS)}"}
            else:
                result = await asyncio.to_thread(
                    functools.partial(
                        search_symbols,
                        repo=arguments["repo"],
                        query=arguments["query"],
                        kind=kind_filter,
                        file_pattern=arguments.get("file_pattern"),
                        language=arguments.get("language"),
                        max_results=arguments.get("max_results", 10),
                        token_budget=arguments.get("token_budget"),
                        detail_level=arguments.get("detail_level", "standard"),
                        debug=arguments.get("debug", False),
                        storage_path=storage_path,
                    )
                )
        elif name == "invalidate_cache":
            result = await asyncio.to_thread(
                functools.partial(
                    invalidate_cache,
                    repo=arguments["repo"],
                    storage_path=storage_path,
                )
            )
        elif name == "search_text":
            result = await asyncio.to_thread(
                functools.partial(
                    search_text,
                    repo=arguments["repo"],
                    query=arguments["query"],
                    file_pattern=arguments.get("file_pattern"),
                    max_results=arguments.get("max_results", 20),
                    context_lines=arguments.get("context_lines", 0),
                    is_regex=arguments.get("is_regex", False),
                    storage_path=storage_path,
                )
            )
        elif name == "get_repo_outline":
            result = await asyncio.to_thread(
                functools.partial(
                    get_repo_outline,
                    repo=arguments["repo"],
                    storage_path=storage_path,
                )
            )
        elif name == "find_importers":
            result = await asyncio.to_thread(
                functools.partial(
                    find_importers,
                    repo=arguments["repo"],
                    file_path=arguments.get("file_path"),
                    file_paths=arguments.get("file_paths"),
                    max_results=arguments.get("max_results", 50),
                    storage_path=storage_path,
                )
            )
        elif name == "find_references":
            result = await asyncio.to_thread(
                functools.partial(
                    find_references,
                    repo=arguments["repo"],
                    identifier=arguments.get("identifier"),
                    identifiers=arguments.get("identifiers"),
                    max_results=arguments.get("max_results", 50),
                    storage_path=storage_path,
                )
            )
        elif name == "check_references":
            result = await asyncio.to_thread(
                functools.partial(
                    check_references,
                    repo=arguments["repo"],
                    identifier=arguments.get("identifier"),
                    identifiers=arguments.get("identifiers"),
                    search_content=arguments.get("search_content", True),
                    max_content_results=arguments.get("max_content_results", 20),
                    storage_path=storage_path,
                )
            )
        elif name == "search_columns":
            result = await asyncio.to_thread(
                functools.partial(
                    search_columns,
                    repo=arguments["repo"],
                    query=arguments["query"],
                    model_pattern=arguments.get("model_pattern"),
                    max_results=arguments.get("max_results", 20),
                    storage_path=storage_path,
                )
            )
        elif name == "get_context_bundle":
            result = await asyncio.to_thread(
                functools.partial(
                    get_context_bundle,
                    repo=arguments["repo"],
                    symbol_id=arguments.get("symbol_id"),
                    symbol_ids=arguments.get("symbol_ids"),
                    include_callers=arguments.get("include_callers", False),
                    output_format=arguments.get("output_format", "json"),
                    storage_path=storage_path,
                )
            )
        elif name == "get_session_stats":
            result = await asyncio.to_thread(
                functools.partial(
                    get_session_stats,
                    storage_path=storage_path,
                )
            )
        elif name == "get_dependency_graph":
            result = await asyncio.to_thread(
                functools.partial(
                    get_dependency_graph,
                    repo=arguments["repo"],
                    file=arguments["file"],
                    direction=arguments.get("direction", "imports"),
                    depth=arguments.get("depth", 1),
                    storage_path=storage_path,
                )
            )
        elif name == "get_blast_radius":
            result = await asyncio.to_thread(
                functools.partial(
                    get_blast_radius,
                    repo=arguments["repo"],
                    symbol=arguments["symbol"],
                    depth=arguments.get("depth", 1),
                    storage_path=storage_path,
                )
            )
        elif name == "get_symbol_diff":
            result = await asyncio.to_thread(
                functools.partial(
                    get_symbol_diff,
                    repo_a=arguments["repo_a"],
                    repo_b=arguments["repo_b"],
                    storage_path=storage_path,
                )
            )
        elif name == "get_class_hierarchy":
            result = await asyncio.to_thread(
                functools.partial(
                    get_class_hierarchy,
                    repo=arguments["repo"],
                    class_name=arguments["class_name"],
                    storage_path=storage_path,
                )
            )
        elif name == "get_related_symbols":
            result = await asyncio.to_thread(
                functools.partial(
                    get_related_symbols,
                    repo=arguments["repo"],
                    symbol_id=arguments["symbol_id"],
                    max_results=arguments.get("max_results", 10),
                    storage_path=storage_path,
                )
            )
        elif name == "suggest_queries":
            result = await asyncio.to_thread(
                functools.partial(
                    suggest_queries,
                    repo=arguments["repo"],
                    storage_path=storage_path,
                )
            )
        elif name == "wait_for_fresh":
            result = await asyncio.to_thread(
                wait_for_fresh_result,
                repo=arguments["repo"],
                timeout_ms=arguments.get("timeout_ms", 500),
            )
        else:
            result = {"error": f"Unknown tool: {name}"}
        
        if isinstance(result, dict):
            meta_fields = config_module.get("meta_fields")
            if meta_fields == [] or arguments.get("suppress_meta"):
                result.pop("_meta", None)
            elif meta_fields is None:
                _meta = result.setdefault("_meta", {})
                # Inject staleness fields for per-repo tools
                repo_arg = arguments.get("repo")
                if repo_arg:
                    # get_reindex_status returns spec fields: index_stale, reindex_in_progress,
                    # stale_since_ms, and conditionally reindex_error / reindex_failures.
                    _meta.update(get_reindex_status(repo_arg))
                elif name not in ("list_repos", "resolve_repo", "get_session_stats", "index_repo", "index_folder"):
                    # For non-repo tools, report global reindex activity
                    from .reindex_state import is_any_reindex_in_progress
                    any_in_progress = is_any_reindex_in_progress()
                    _meta["index_stale"] = any_in_progress
                    _meta["reindex_in_progress"] = any_in_progress
                    _meta["stale_since_ms"] = None
            elif isinstance(meta_fields, list):
                # Partial field inclusion - build _meta with only specified fields
                # Save existing _meta (may contain tool-generated fields like timing_ms, tokens_saved)
                existing_meta = result.pop("_meta", {})
                _meta: dict[str, Any] = {}
                if "powered_by" in meta_fields:
                    _meta["powered_by"] = "jcodemunch-mcp by jgravelle · https://github.com/jgravelle/jcodemunch-mcp"
                repo_arg = arguments.get("repo")
                if repo_arg:
                    status = get_reindex_status(repo_arg)
                    for field in meta_fields:
                        if field in status:
                            _meta[field] = status[field]
                        elif field in existing_meta:
                            _meta[field] = existing_meta[field]
                elif name not in ("list_repos", "get_session_stats", "index_repo", "index_folder"):
                    from .reindex_state import is_any_reindex_in_progress
                    any_in_progress = is_any_reindex_in_progress()
                    if "index_stale" in meta_fields:
                        _meta["index_stale"] = any_in_progress
                    if "reindex_in_progress" in meta_fields:
                        _meta["reindex_in_progress"] = any_in_progress
                    if "stale_since_ms" in meta_fields:
                        _meta["stale_since_ms"] = None
                # Preserve tool-generated fields (timing_ms, tokens_saved, candidates_scored)
                # This runs for ALL tools, including list_repos, get_session_stats, etc.
                for field in meta_fields:
                    if field not in _meta and field in existing_meta:
                        _meta[field] = existing_meta[field]
                if _meta:
                    result["_meta"] = _meta
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    except KeyError as e:
        return [TextContent(type="text", text=json.dumps({"error": f"Missing required argument: {e}. Check the tool schema for correct parameter names."}, indent=2))]
    except Exception:
        logger.error("call_tool %s failed", name, exc_info=True)
        return [TextContent(type="text", text=json.dumps({"error": f"Internal error processing {name}"}, indent=2))]


async def _run_server_with_watcher(
    server_coro_func,
    server_args: tuple,
    watcher_kwargs: dict,
    log_path: Optional[str] = None,
) -> None:
    """Run MCP server with a background watcher in the same event loop.

    Watcher runs in quiet mode (no stderr output). If log_path is provided,
    watcher output and errors go to that file. If log_path is "auto", a temp
    file is created in the system temp directory.
    """
    if watch_folders is None:
        raise ImportError(
            "watchfiles is required for --watcher. "
            "Install with: pip install 'jcodemunch-mcp[watch]'"
        )

    import sys
    import tempfile

    # Resolve log file path
    if log_path == "auto":
        log_path = os.path.join(
            tempfile.gettempdir(),
            f"jcw_{os.getpid()}.log",
        )

    stop_event = asyncio.Event()
    watcher_task = asyncio.create_task(
        watch_folders(
            **watcher_kwargs,
            stop_event=stop_event,
            quiet=True,
            log_file=log_path,
        ),
        name="embedded-watcher",
    )

    # Give watcher a moment to start; detect early failures before blocking on server
    await asyncio.sleep(0.1)
    if watcher_task.done() and not watcher_task.cancelled():
        exc = watcher_task.exception()
        if exc is not None:
            logger.warning("Embedded watcher failed to start: %s", exc)

    try:
        await server_coro_func(*server_args)
    except asyncio.CancelledError:
        pass  # Clean shutdown via Ctrl+C
    finally:
        stop_event.set()
        from .storage import IndexStore
        IndexStore(base_path=watcher_kwargs.get("storage_path") or os.environ.get("CODE_INDEX_PATH")).close()
        try:
            await asyncio.wait_for(watcher_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
        except (WatcherError, Exception) as exc:
            logger.warning("Watcher stopped with error: %s", exc)


async def run_stdio_server():
    """Run the MCP server over stdio (default)."""
    import sys
    from mcp.server.stdio import stdio_server
    print(f"jcodemunch-mcp {__version__} by jgravelle · https://github.com/jgravelle/jcodemunch-mcp", file=sys.stderr)
    logger.info(
        "startup version=%s transport=stdio storage=%s ai_summaries=%s",
        __version__,
        os.environ.get("CODE_INDEX_PATH", "~/.code-index/"),
        _default_use_ai_summaries(),
    )
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        from .storage import IndexStore
        IndexStore(base_path=os.environ.get("CODE_INDEX_PATH")).close()


def _make_auth_middleware():
    """Return a Starlette middleware class that checks JCODEMUNCH_HTTP_TOKEN if set."""
    token = os.environ.get("JCODEMUNCH_HTTP_TOKEN")
    if not token:
        return None

    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            auth = request.headers.get("authorization", "")
            if not hmac.compare_digest(auth, f"Bearer {token}"):
                return JSONResponse(
                    {"error": "Unauthorized. Set Authorization: Bearer <JCODEMUNCH_HTTP_TOKEN> header."},
                    status_code=401,
                )
            return await call_next(request)

    return Middleware(BearerAuthMiddleware)


def _make_rate_limit_middleware():
    """Return a Starlette middleware that rate-limits by IP (optional, opt-in).

    Reads JCODEMUNCH_RATE_LIMIT env var.  Value is max requests per minute per
    client IP.  0 or unset disables rate limiting (default — no behaviour change
    for existing deployments).

    Returns a Middleware instance, or None when rate limiting is disabled.
    """
    try:
        limit = int(os.environ.get("JCODEMUNCH_RATE_LIMIT", "0"))
    except (ValueError, TypeError):
        limit = 0
    if limit <= 0:
        return None

    import collections
    import time as _time

    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    _WINDOW = 60.0  # seconds
    _buckets: dict[str, collections.deque] = {}

    class RateLimitMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            ip = request.client.host if request.client else "unknown"
            now = _time.monotonic()
            bucket = _buckets.setdefault(ip, collections.deque())
            # Evict timestamps outside the sliding window
            while bucket and now - bucket[0] >= _WINDOW:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = int(_WINDOW - (now - bucket[0])) + 1
                return JSONResponse(
                    {"error": f"Rate limit exceeded. Max {limit} requests per minute per IP."},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)
            return await call_next(request)

    return Middleware(RateLimitMiddleware)


async def run_sse_server(host: str, port: int):
    """Run the MCP server with SSE transport (persistent HTTP mode)."""
    import sys
    try:
        import uvicorn
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.routing import Mount, Route
    except ImportError as e:
        raise ImportError(
            f"SSE transport requires additional packages: {e}. "
            'Install them with: pip install "jcodemunch-mcp[http]"'
        ) from e
    from mcp.server.sse import SseServerTransport

    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request: Request):
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    middleware = []
    auth_mw = _make_auth_middleware()
    if auth_mw:
        middleware.append(auth_mw)
    rate_mw = _make_rate_limit_middleware()
    if rate_mw:
        middleware.append(rate_mw)

    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ],
        middleware=middleware,
    )

    print(
        f"jcodemunch-mcp {__version__} by jgravelle · SSE server at http://{host}:{port}/sse",
        file=sys.stderr,
    )
    logger.info(
        "startup version=%s transport=sse host=%s port=%d storage=%s",
        __version__, host, port,
        os.environ.get("CODE_INDEX_PATH", "~/.code-index/"),
    )
    config = uvicorn.Config(starlette_app, host=host, port=port, log_level="warning")
    await uvicorn.Server(config).serve()


async def run_streamable_http_server(host: str, port: int):
    """Run the MCP server with streamable-http transport (persistent HTTP mode)."""
    import sys
    try:
        import anyio
        import uvicorn
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.routing import Route
    except ImportError as e:
        raise ImportError(
            f"Streamable-http transport requires additional packages: {e}. "
            'Install them with: pip install "jcodemunch-mcp[http]"'
        ) from e
    from mcp.server.streamable_http import StreamableHTTPServerTransport

    async def handle_mcp(request: Request):
        transport = StreamableHTTPServerTransport(mcp_session_id=None)
        async with transport.connect() as (read_stream, write_stream):
            async with anyio.create_task_group() as tg:
                tg.start_soon(
                    server.run,
                    read_stream,
                    write_stream,
                    server.create_initialization_options(),
                )
                await transport.handle_request(
                    request.scope, request.receive, request._send
                )

    middleware = []
    auth_mw = _make_auth_middleware()
    if auth_mw:
        middleware.append(auth_mw)
    rate_mw = _make_rate_limit_middleware()
    if rate_mw:
        middleware.append(rate_mw)

    starlette_app = Starlette(
        routes=[
            Route("/mcp", endpoint=handle_mcp, methods=["GET", "POST", "DELETE"]),
        ],
        middleware=middleware,
    )

    print(
        f"jcodemunch-mcp {__version__} by jgravelle · streamable-http server at http://{host}:{port}/mcp",
        file=sys.stderr,
    )
    logger.info(
        "startup version=%s transport=streamable-http host=%s port=%d storage=%s",
        __version__, host, port,
        os.environ.get("CODE_INDEX_PATH", "~/.code-index/"),
    )
    config = uvicorn.Config(starlette_app, host=host, port=port, log_level="warning")
    await uvicorn.Server(config).serve()


def _setup_logging(args) -> None:
    """Configure logging from parsed args."""
    log_level = getattr(logging, args.log_level)
    handlers: list[logging.Handler] = []
    if args.log_file:
        log_path = Path(args.log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path))
    else:
        handlers.append(logging.StreamHandler())

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
    )

    extra_ext = os.environ.get("JCODEMUNCH_EXTRA_EXTENSIONS", "")
    if extra_ext:
        logging.getLogger(__name__).info("JCODEMUNCH_EXTRA_EXTENSIONS: %s", extra_ext)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add logging args shared by all subcommands."""
    parser.add_argument(
        "--log-level",
        default=os.environ.get("JCODEMUNCH_LOG_LEVEL", "WARNING"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (also via JCODEMUNCH_LOG_LEVEL env var)",
    )
    parser.add_argument(
        "--log-file",
        default=os.environ.get("JCODEMUNCH_LOG_FILE"),
        help="Log file path (also via JCODEMUNCH_LOG_FILE env var). Defaults to stderr.",
    )


def _run_config(check: bool = False, init: bool = False) -> None:
    """Print the current effective configuration to stdout, or initialize config file."""
    from . import config as _cfg

    # Handle --init
    if init:
        storage_path = os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index"))
        config_path = Path(storage_path) / "config.jsonc"

        if config_path.exists():
            print(f"Config file already exists: {config_path}")
            print("Refusing to overwrite. Remove it first or use --check to validate it.")
            return

        config_path.parent.mkdir(parents=True, exist_ok=True)
        template = _cfg.generate_template()
        config_path.write_text(template, encoding="utf-8")
        print(f"Created config template: {config_path}")
        print("Edit it to customize jcodemunch-mcp settings.")
        return

    # Load config to get effective values
    _cfg.load_config()

    tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    enc = getattr(sys.stdout, "encoding", "ascii") or "ascii"

    def _safe(s, fallback):
        try:
            s.encode(enc)
            return s
        except (UnicodeEncodeError, LookupError):
            return fallback

    CHECK = _safe("✓", "OK")
    CROSS = _safe("✗", "!!")
    WARN  = _safe("!", "!")

    def dim(s):   return f"\033[2m{s}\033[0m" if tty else s
    def bold(s):  return f"\033[1m{s}\033[0m" if tty else s
    def green(s): return f"\033[32m{s}\033[0m" if tty else s
    def yellow(s): return f"\033[33m{s}\033[0m" if tty else s
    def red(s):   return f"\033[31m{s}\033[0m" if tty else s

    COL = 36

    def row(name, value, source="default"):
        tag = dim(f" [{source}]") if source != "default" else dim(" (default)")
        print(f"  {name:<{COL}} {value}{tag}")

    def env(var, default=""):
        val = os.environ.get(var)
        return (val if val is not None else default), (val is None)

    def section(title):
        print(f"\n{bold(title)}")

    def cfg_row(name, key, default, source=None, fmt=None):
        """Display a config value with source indicator."""
        val = _cfg.get(key, default)
        if fmt:
            val = fmt(val)
        effective_source = source or "default"
        print(f"  {name:<{COL}} {val}{dim(f' [{effective_source}]')}")

    print(bold(f"jcodemunch-mcp {__version__} — configuration"))

    # ── Config File ───────────────────────────────────────────────────────
    section("Config File")
    storage_path = os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index"))
    config_path = Path(storage_path) / "config.jsonc"
    if config_path.exists():
        print(f"  {green(CHECK)} config.jsonc found: {config_path}")
    else:
        print(f"  {yellow(WARN)} config.jsonc not found: {config_path}")
        print(f"  {dim('  Using defaults + env var fallbacks. Run `config --init` to create a config file.')}")

    # ── Indexing ──────────────────────────────────────────────────────────
    section("Indexing")
    # Detect source for each config key
    # Check the actual config file content (if exists) to determine if a key was
    # explicitly set in config vs defaulted
    _loaded_keys: set = set()
    if config_path.exists():
        try:
            content = config_path.read_text(encoding="utf-8")
            stripped = _cfg._strip_jsonc(content)
            import json as _json
            _loaded_keys = set(_json.loads(stripped).keys())
        except Exception:
            pass

    def _detect_source(key, default):
        if key in _loaded_keys:
            return "config"
        env_var = next((e for e, c in _cfg.ENV_VAR_MAPPING.items() if c == key), None)
        if env_var and os.environ.get(env_var) is not None:
            return "env"
        return "default"

    def _fmt_list(v):
        if isinstance(v, list):
            return f"[{len(v)} items]" if len(v) > 3 else str(v)
        return str(v)

    row("max_folder_files", _cfg.get("max_folder_files", 2000), _detect_source("max_folder_files", 2000))
    row("max_index_files", _cfg.get("max_index_files", 10000), _detect_source("max_index_files", 10000))
    row("staleness_days", _cfg.get("staleness_days", 7), _detect_source("staleness_days", 7))
    row("max_results", _cfg.get("max_results", 500), _detect_source("max_results", 500))
    patterns = _cfg.get("extra_ignore_patterns", [])
    row("extra_ignore_patterns", _fmt_list(patterns) if patterns else dim("(none)"), _detect_source("extra_ignore_patterns", []))
    exts = _cfg.get("extra_extensions", {})
    row("extra_extensions", _fmt_list(exts) if exts else dim("(none)"), _detect_source("extra_extensions", {}))
    row("context_providers", str(_cfg.get("context_providers", True)).lower(), _detect_source("context_providers", True))
    path_map_val = _cfg.get("path_map", "")
    row("path_map", path_map_val if path_map_val else dim("(none)"), _detect_source("path_map", ""))

    # ── Meta Response Control ─────────────────────────────────────────────
    section("Meta Response Control")
    meta_fields = _cfg.get("meta_fields")
    if meta_fields is None:
        row("meta_fields", dim("(all fields)"), "default")
    elif meta_fields == []:
        row("meta_fields", dim("(none)"), "config")
    else:
        row("meta_fields", _fmt_list(meta_fields), _detect_source("meta_fields", None))

    # ── Languages ─────────────────────────────────────────────────────────
    section("Languages")
    languages = _cfg.get("languages")
    if languages is None:
        row("languages", dim("(all languages)"), "default")
    else:
        row("languages", _fmt_list(languages), _detect_source("languages", None))

    # ── Disabled Tools ────────────────────────────────────────────────────
    section("Disabled Tools")
    disabled = _cfg.get("disabled_tools", [])
    row("disabled_tools", _fmt_list(disabled) if disabled else dim("(none)"), _detect_source("disabled_tools", []))

    # ── Descriptions ──────────────────────────────────────────────────────
    section("Descriptions")
    descs = _cfg.get("descriptions", {})
    row("descriptions", _fmt_list(descs) if descs else dim("(none)"), _detect_source("descriptions", {}))

    # ── AI Summarizer ─────────────────────────────────────────────────────
    section("AI Summarizer")
    use_ai_raw, use_ai_d = env("JCODEMUNCH_USE_AI_SUMMARIES", "true")
    use_ai = use_ai_raw.lower() not in ("false", "0", "no", "off")
    row("use_ai_summaries", str(use_ai).lower(), "env" if not use_ai_d else _detect_source("use_ai_summaries", True))
    provider, provider_d = env("JCODEMUNCH_SUMMARIZER_PROVIDER", "")
    row(
        "summarizer_provider",
        provider if provider else dim("(auto-detect)"),
        "env" if not provider_d else "default",
    )

    openai_base = os.environ.get("OPENAI_API_BASE", "")
    provider_name = get_provider_name()

    if not use_ai:
        print(f"  {yellow('AI summaries disabled')} — signature fallback active")
    elif provider_name == "anthropic":
        suffix = "JCODEMUNCH_SUMMARIZER_PROVIDER=anthropic" if provider == "anthropic" else "ANTHROPIC_API_KEY set"
        print(f"  Active provider:  {green('Anthropic')}  ({suffix})")
        model, d = env("ANTHROPIC_MODEL", "claude-haiku-*")
        row("  ANTHROPIC_MODEL", model, "env" if not d else "default")
    elif provider_name == "gemini":
        suffix = "JCODEMUNCH_SUMMARIZER_PROVIDER=gemini" if provider == "gemini" else "GOOGLE_API_KEY set"
        print(f"  Active provider:  {green('Google Gemini')}  ({suffix})")
        model, d = env("GOOGLE_MODEL", "gemini-flash-*")
        row("  GOOGLE_MODEL", model, "env" if not d else "default")
    elif provider_name == "openai":
        base_label = openai_base or "https://api.openai.com/v1"
        suffix = "JCODEMUNCH_SUMMARIZER_PROVIDER=openai" if provider == "openai" else "OPENAI_API_BASE set"
        print(f"  Active provider:  {green('OpenAI-compatible')}  ({suffix})")
        row("  OPENAI_API_BASE", base_label, "env" if openai_base else "default")
        model_default = "gpt-4o-mini" if provider == "openai" and not openai_base else "qwen3-coder"
        model, d = env("OPENAI_MODEL", model_default)
        row("  OPENAI_MODEL", model, "env" if not d else "default")
        v, d = env("OPENAI_TIMEOUT", "60.0")
        row("  OPENAI_TIMEOUT", v, "env" if not d else "default")
        v, d = env("OPENAI_BATCH_SIZE", "10")
        row("  OPENAI_BATCH_SIZE", v, "env" if not d else "default")
        v, d = env("OPENAI_CONCURRENCY", "1")
        row("  OPENAI_CONCURRENCY", v, "env" if not d else "default")
        v, d = env("OPENAI_MAX_TOKENS", "500")
        row("  OPENAI_MAX_TOKENS", v, "env" if not d else "default")
    elif provider_name == "minimax":
        suffix = "JCODEMUNCH_SUMMARIZER_PROVIDER=minimax" if provider == "minimax" else "MINIMAX_API_KEY set"
        print(f"  Active provider:  {green('MiniMax')}  ({suffix})")
        row("  OPENAI_API_BASE", "https://api.minimax.io/v1", "default")
        row("  OPENAI_MODEL", "minimax-m2.7", "default")
    elif provider_name == "glm":
        suffix = "JCODEMUNCH_SUMMARIZER_PROVIDER=glm" if provider == "glm" else "ZHIPUAI_API_KEY set"
        print(f"  Active provider:  {green('GLM-5')}  ({suffix})")
        row("  OPENAI_API_BASE", "https://api.z.ai/api/paas/v4/", "default")
        row("  OPENAI_MODEL", "glm-5", "default")
    elif provider == "none":
        print(f"  Active provider:  {yellow('none')} — explicitly disabled, signature fallback active")
    else:
        print(f"  Active provider:  {yellow('none')} — no API key set, signature fallback active")
        print(f"  {dim('Set ANTHROPIC_API_KEY, GOOGLE_API_KEY, OPENAI_API_BASE, MINIMAX_API_KEY, or ZHIPUAI_API_KEY to enable')}")

    # ── Transport ──────────────────────────────────────────────────────────
    section("Transport")
    transport = _cfg.get("transport", "stdio")
    row("transport", transport, _detect_source("transport", "stdio"))
    if transport != "stdio":
        row("host", _cfg.get("host", "127.0.0.1"), _detect_source("host", "127.0.0.1"))
        row("port", _cfg.get("port", 8901), _detect_source("port", 8901))
        token = os.environ.get("JCODEMUNCH_HTTP_TOKEN", "")
        row("JCODEMUNCH_HTTP_TOKEN", green("set") if token else yellow("not set"), "env")
        rate = _cfg.get("rate_limit", 0)
        rate_label = f"{rate}/min per IP" if rate != 0 else "disabled"
        row("rate_limit", rate_label, _detect_source("rate_limit", 0))
    else:
        print(f"  {dim('stdio mode — HTTP transport vars ignored')}")

    # ── Watcher ───────────────────────────────────────────────────────────
    section("Watcher")
    row("watch", str(_cfg.get("watch", False)).lower(), _detect_source("watch", False))
    row("watch_debounce_ms", _cfg.get("watch_debounce_ms", 2000), _detect_source("watch_debounce_ms", 2000))
    row("freshness_mode", _cfg.get("freshness_mode", "relaxed"), _detect_source("freshness_mode", "relaxed"))
    row("claude_poll_interval", _cfg.get("claude_poll_interval", 5.0), _detect_source("claude_poll_interval", 5.0))

    # ── Logging ──────────────────────────────────────────────────────────
    section("Logging")
    row("log_level", _cfg.get("log_level", "WARNING"), _detect_source("log_level", "WARNING"))
    log_file = _cfg.get("log_file")
    row("log_file", log_file if log_file else dim("(stderr)"), _detect_source("log_file", None))

    # ── Privacy & Telemetry ───────────────────────────────────────────────
    section("Privacy & Telemetry")
    row("redact_source_root", str(_cfg.get("redact_source_root", False)).lower(), _detect_source("redact_source_root", False))
    stats_int = _cfg.get("stats_file_interval", 3)
    row("stats_file_interval", "disabled" if stats_int == 0 else f"every {stats_int} calls", _detect_source("stats_file_interval", 3))
    share = _cfg.get("share_savings", True)
    row("share_savings", green("enabled") if share else yellow("disabled"), _detect_source("share_savings", True))
    row("summarizer_concurrency", _cfg.get("summarizer_concurrency", 4), _detect_source("summarizer_concurrency", 4))
    row("allow_remote_summarizer", str(_cfg.get("allow_remote_summarizer", False)).lower(), _detect_source("allow_remote_summarizer", False))

    # ── --check ───────────────────────────────────────────────────────────
    if check:
        section("Checks")
        issues: list[str] = []

        # Validate config.jsonc
        config_issues = _cfg.validate_config(str(config_path))
        if config_issues:
            for issue in config_issues:
                print(f"  {red(CROSS)} config.jsonc: {issue}")
            issues.append("config")
        else:
            print(f"  {green(CHECK)} config.jsonc valid: {config_path}")

        # Storage writable?
        storage = Path(storage_path)
        try:
            storage.mkdir(parents=True, exist_ok=True)
            probe = storage / ".jcm_probe"
            probe.write_text("ok")
            probe.unlink()
            print(f"  {green(CHECK)} index storage writable: {storage}")
        except Exception as e:
            print(f"  {red(CROSS)} index storage not writable: {storage} — {e}")
            issues.append("storage")

        # AI provider package installed?
        if use_ai:
            if anthropic_key:
                try:
                    import anthropic as _a
                    print(f"  {green(CHECK)} anthropic package installed (v{_a.__version__})")
                except ImportError:
                    print(f"  {red(CROSS)} anthropic not installed — run: pip install \"jcodemunch-mcp[anthropic]\"")
                    issues.append("anthropic")
            elif google_key:
                try:
                    import google.generativeai  # noqa: F401
                    print(f"  {green(CHECK)} google-generativeai package installed")
                except ImportError:
                    print(f"  {red(CROSS)} google-generativeai not installed — run: pip install \"jcodemunch-mcp[gemini]\"")
                    issues.append("gemini")
            elif openai_base:
                try:
                    import httpx  # noqa: F401
                    print(f"  {green(CHECK)} httpx available for local LLM requests")
                except ImportError:
                    print(f"  {red(CROSS)} httpx not installed (required for local LLM)")
                    issues.append("httpx")
            else:
                print(f"  {yellow(WARN)} no AI provider configured — signature fallback will be used")

        # HTTP transport packages installed?
        if transport != "stdio":
            missing = [pkg for pkg in ("uvicorn", "starlette", "anyio") if not _can_import(pkg)]
            if missing:
                print(f"  {red(CROSS)} HTTP packages missing: {', '.join(missing)} — run: pip install \"jcodemunch-mcp[http]\"")
                issues.append("http")
            else:
                print(f"  {green(CHECK)} HTTP transport packages installed (uvicorn, starlette, anyio)")

        print()
        if issues:
            print(yellow(f"  {len(issues)} issue(s) found — see above."))
            sys.exit(1)
        else:
            print(green("  All checks passed."))
    print()


def _can_import(module: str) -> bool:
    """Return True if module is importable without side effects."""
    import importlib.util
    return importlib.util.find_spec(module) is not None


def main(argv: Optional[list[str]] = None):
    """Main entry point."""
    from .security import verify_package_integrity
    verify_package_integrity()

    parser = argparse.ArgumentParser(
        prog="jcodemunch-mcp",
        description="jCodeMunch MCP server and tools.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")

    # --- serve (default when no subcommand given) ---
    serve_parser = subparsers.add_parser("serve", help="Run the MCP server (default)")
    serve_parser.add_argument(
        "--transport",
        default=os.environ.get("JCODEMUNCH_TRANSPORT", "stdio"),
        choices=["stdio", "sse", "streamable-http"],
        help="Transport mode: stdio (default), sse, or streamable-http (also via JCODEMUNCH_TRANSPORT env var)",
    )
    serve_parser.add_argument(
        "--host",
        default=os.environ.get("JCODEMUNCH_HOST", "127.0.0.1"),
        help="Host to bind to in HTTP transport mode (also via JCODEMUNCH_HOST env var, default: 127.0.0.1)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("JCODEMUNCH_PORT", "8901")),
        help="Port to listen on in HTTP transport mode (also via JCODEMUNCH_PORT env var, default: 8901)",
    )
    _add_common_args(serve_parser)

    # --- Watcher options for serve ---
    serve_parser.add_argument(
        "--watcher",
        nargs="?",
        const="true",
        default=None,
        metavar="BOOL",
        help="Enable background file watcher alongside the server. "
             "Use --watcher or --watcher=true to enable, --watcher=false to disable.",
    )
    serve_parser.add_argument(
        "--watcher-path",
        nargs="*",
        default=None,
        metavar="PATH",
        help="Folder(s) to watch (default: current working directory)",
    )
    serve_parser.add_argument(
        "--watcher-debounce",
        type=int,
        default=None,
        metavar="MS",
        help="Watcher debounce interval in ms (default: from config, also via JCODEMUNCH_WATCH_DEBOUNCE_MS)",
    )
    serve_parser.add_argument(
        "--watcher-idle-timeout",
        type=int,
        default=None,
        metavar="MINUTES",
        help="Auto-stop watcher after N minutes with no re-indexing (default: disabled)",
    )
    serve_parser.add_argument(
        "--watcher-no-ai-summaries",
        action="store_true",
        help="Disable AI-generated summaries for watcher re-indexing",
    )
    serve_parser.add_argument(
        "--watcher-extra-ignore",
        nargs="*",
        help="Additional gitignore-style patterns to exclude from watching",
    )
    serve_parser.add_argument(
        "--watcher-follow-symlinks",
        action="store_true",
        help="Include symlinked files in watcher indexing",
    )
    serve_parser.add_argument(
        "--watcher-log",
        nargs="?",
        const="auto",
        default=None,
        metavar="PATH",
        help="Log watcher output to file instead of stderr. "
             "Use --watcher-log for auto temp file, or --watcher-log=<path> for a specific file.",
    )
    serve_parser.add_argument(
        "--freshness-mode",
        default=None,
        choices=["relaxed", "strict"],
        help="Freshness mode: 'relaxed' (default) or 'strict' (block queries until watcher reindex finishes)",
    )

    # --- watch ---
    watch_parser = subparsers.add_parser(
        "watch",
        help="Watch folders for changes and auto-reindex",
    )
    watch_parser.add_argument(
        "paths",
        nargs="+",
        help="One or more folder paths to watch",
    )
    watch_parser.add_argument(
        "--debounce",
        type=int,
        default=None,
        metavar="MS",
        help="Debounce interval in ms (default: from config, also via JCODEMUNCH_WATCH_DEBOUNCE_MS)",
    )
    watch_parser.add_argument(
        "--no-ai-summaries",
        action="store_true",
        help="Disable AI-generated summaries during re-indexing",
    )
    watch_parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Include symlinked files in indexing",
    )
    watch_parser.add_argument(
        "--extra-ignore",
        nargs="*",
        help="Additional gitignore-style patterns to exclude",
    )
    watch_parser.add_argument(
        "--idle-timeout",
        type=int,
        default=None,
        metavar="MINUTES",
        help="Auto-shutdown after N minutes with no re-indexing (default: disabled)",
    )
    _add_common_args(watch_parser)

    # --- config ---
    config_parser = subparsers.add_parser(
        "config",
        help="Show current effective configuration",
    )
    config_parser.add_argument(
        "--check",
        action="store_true",
        help="Also verify prerequisites (storage writable, AI packages installed, HTTP packages present)",
    )
    config_parser.add_argument(
        "--init",
        action="store_true",
        help="Generate a template config.jsonc file in CODE_INDEX_PATH",
    )

    # --- index-file ---
    index_file_parser = subparsers.add_parser(
        "index-file",
        help="Re-index a single file within an existing indexed folder",
    )
    index_file_parser.add_argument(
        "path",
        help="Absolute path to the file to index",
    )
    index_file_parser.add_argument(
        "--no-ai-summaries",
        action="store_true",
        help="Disable AI-generated summaries for this file",
    )
    _add_common_args(index_file_parser)

    # --- hook-event ---
    hook_parser = subparsers.add_parser(
        "hook-event",
        help="Record a Claude Code worktree lifecycle event (used by hooks)",
    )
    hook_parser.add_argument(
        "event_type",
        choices=["create", "remove"],
        help="Event type: 'create' when a worktree is created, 'remove' when deleted",
    )
    _add_common_args(hook_parser)

    # --- watch-claude ---
    wc_parser = subparsers.add_parser(
        "watch-claude",
        help="Auto-discover and watch Claude Code worktrees",
    )
    wc_parser.add_argument(
        "--repos",
        nargs="+",
        help="One or more git repository paths to poll for worktrees via `git worktree list`",
    )
    wc_parser.add_argument(
        "--poll-interval",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Poll interval in seconds (default: from config, also via JCODEMUNCH_CLAUDE_POLL_INTERVAL)",
    )
    wc_parser.add_argument(
        "--debounce",
        type=int,
        default=None,
        metavar="MS",
        help="Debounce interval in ms for file watching (default: from config, also via JCODEMUNCH_WATCH_DEBOUNCE_MS)",
    )
    wc_parser.add_argument(
        "--no-ai-summaries",
        action="store_true",
        help="Disable AI-generated summaries during re-indexing",
    )
    wc_parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Include symlinked files in indexing",
    )
    wc_parser.add_argument(
        "--extra-ignore",
        nargs="*",
        help="Additional gitignore-style patterns to exclude",
    )
    _add_common_args(wc_parser)

    # Backwards compat: if first non-flag arg isn't a known subcommand,
    # prepend "serve" so legacy invocations like `jcodemunch-mcp --transport sse` still work.
    # But let --help and -V be handled by the top-level parser first.
    raw_argv = argv if argv is not None else sys.argv[1:]
    top_level_flags = {"-h", "--help", "-V", "--version"}
    if any(arg in top_level_flags for arg in raw_argv):
        args = parser.parse_args(raw_argv)
    else:
        known_commands = {"serve", "watch", "hook-event", "watch-claude", "config", "index-file"}
        has_subcommand = any(arg in known_commands for arg in raw_argv if not arg.startswith("-"))
        if not has_subcommand:
            raw_argv = ["serve"] + list(raw_argv)
        args = parser.parse_args(raw_argv)

    if args.command == "config":
        _run_config(
            check=getattr(args, "check", False),
            init=getattr(args, "init", False),
        )
        return

    # Apply config defaults for watcher keys: CLI args > config > env vars.
    # config.load_config() is called inside each subcommand handler, but we need
    # the values here to fill in None defaults from argparse.
    # load_config() is idempotent so calling it early is safe.
    config_module.load_config()

    # --watcher-debounce (serve subcommand) / --debounce (watch, watch-claude)
    # Only set if the attr exists on args and is None (not explicitly provided on CLI)
    _debounce = config_module.get("watch_debounce_ms", 2000)
    if getattr(args, "watcher_debounce", None) is None:
        args.watcher_debounce = _debounce
    if getattr(args, "debounce", None) is None:
        args.debounce = _debounce

    # --poll-interval (watch-claude subcommand)
    if getattr(args, "poll_interval", None) is None:
        args.poll_interval = config_module.get("claude_poll_interval", 5.0)

    # --freshness-mode is only relevant for serve subcommand; handled there

    _setup_logging(args)

    if args.command == "watch":
        from .watcher import watch_folders

        use_ai = not args.no_ai_summaries and _default_use_ai_summaries()
        asyncio.run(
            watch_folders(
                paths=args.paths,
                debounce_ms=args.debounce,
                use_ai_summaries=use_ai,
                storage_path=os.environ.get("CODE_INDEX_PATH"),
                extra_ignore_patterns=args.extra_ignore,
                follow_symlinks=args.follow_symlinks,
                idle_timeout_minutes=args.idle_timeout,
            )
        )
    elif args.command == "hook-event":
        from .hook_event import handle_hook_event

        handle_hook_event(event_type=args.event_type)
    elif args.command == "watch-claude":
        from .watcher import watch_claude_worktrees

        use_ai = not args.no_ai_summaries and _default_use_ai_summaries()
        asyncio.run(
            watch_claude_worktrees(
                repos=args.repos,
                poll_interval=args.poll_interval,
                debounce_ms=args.debounce,
                use_ai_summaries=use_ai,
                storage_path=os.environ.get("CODE_INDEX_PATH"),
                extra_ignore_patterns=args.extra_ignore,
                follow_symlinks=args.follow_symlinks,
            )
        )
    elif args.command == "index-file":
        from .tools.index_file import index_file as _index_file
        import json as _json

        use_ai = not args.no_ai_summaries and _default_use_ai_summaries()
        result = _index_file(
            path=args.path,
            use_ai_summaries=use_ai,
            storage_path=os.environ.get("CODE_INDEX_PATH"),
        )
        print(_json.dumps(result, indent=2))
        if not result.get("success"):
            sys.exit(1)
    else:
        # serve (default)
        # Re-run load_config() after _setup_logging() so config warnings/errors
        # go to the configured log destination (the early call at startup ran before logging was set up)
        config_module.load_config()
        config_module.load_all_project_configs()
        from .reindex_state import set_freshness_mode
        # Apply config default if --freshness-mode was not explicitly provided
        if args.freshness_mode is None:
            args.freshness_mode = config_module.get("freshness_mode", "relaxed")
        set_freshness_mode(args.freshness_mode)
        watcher_enabled = _get_watcher_enabled(args)

        if watcher_enabled:
            try:
                import watchfiles  # noqa: F401
            except ImportError:
                print(
                    "ERROR: --watcher requires watchfiles. "
                    "Install with: pip install 'jcodemunch-mcp[watch]'",
                    file=sys.stderr,
                )
                sys.exit(1)

            watcher_paths = args.watcher_path or [os.getcwd()]
            use_ai = not args.watcher_no_ai_summaries and _default_use_ai_summaries()
            watcher_kwargs = dict(
                paths=watcher_paths,
                debounce_ms=args.watcher_debounce,
                use_ai_summaries=use_ai,
                storage_path=os.environ.get("CODE_INDEX_PATH"),
                extra_ignore_patterns=args.watcher_extra_ignore,
                follow_symlinks=args.watcher_follow_symlinks,
                idle_timeout_minutes=args.watcher_idle_timeout,
            )

            log_path = getattr(args, "watcher_log", None)

            try:
                if args.transport == "sse":
                    asyncio.run(_run_server_with_watcher(
                        run_sse_server, (args.host, args.port), watcher_kwargs, log_path,
                    ))
                elif args.transport == "streamable-http":
                    asyncio.run(_run_server_with_watcher(
                        run_streamable_http_server, (args.host, args.port), watcher_kwargs, log_path,
                    ))
                else:
                    asyncio.run(_run_server_with_watcher(
                        run_stdio_server, (), watcher_kwargs, log_path,
                    ))
            except KeyboardInterrupt:
                pass
        else:
            if args.transport == "sse":
                asyncio.run(run_sse_server(args.host, args.port))
            elif args.transport == "streamable-http":
                asyncio.run(run_streamable_http_server(args.host, args.port))
            else:
                asyncio.run(run_stdio_server())


if __name__ == "__main__":
    main()
