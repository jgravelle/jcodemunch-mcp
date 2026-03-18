"""MCP server for jcodemunch-mcp."""

import argparse
import asyncio
import functools
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

from mcp.server import Server
from mcp.types import Tool, TextContent, Resource

from . import __version__
from .tools.index_repo import index_repo
from .tools.index_folder import index_folder
from .tools.list_repos import list_repos
from .tools.get_file_tree import get_file_tree
from .tools.get_file_outline import get_file_outline
from .tools.get_file_content import get_file_content
from .tools.get_symbol import get_symbol, get_symbols
from .tools.search_symbols import search_symbols
from .tools.invalidate_cache import invalidate_cache
from .tools.search_text import search_text
from .tools.get_repo_outline import get_repo_outline
from .tools.find_importers import find_importers
from .tools.find_references import find_references
from .tools.get_session_stats import get_session_stats
from .tools.get_dependency_graph import get_dependency_graph
from .tools.get_blast_radius import get_blast_radius
from .tools.get_symbol_diff import get_symbol_diff
from .tools.get_class_hierarchy import get_class_hierarchy
from .tools.get_related_symbols import get_related_symbols
from .tools.suggest_queries import suggest_queries
from .tools.search_columns import search_columns
from .tools.get_context_bundle import get_context_bundle
from .parser.symbols import VALID_KINDS


try:
    from .watcher import watch_folders
except ImportError:
    watch_folders = None  # type: ignore[assignment, misc]


logger = logging.getLogger(__name__)


def _default_use_ai_summaries() -> bool:
    """Return the default for use_ai_summaries, respecting JCODEMUNCH_USE_AI_SUMMARIES env var."""
    val = os.environ.get("JCODEMUNCH_USE_AI_SUMMARIES", "").strip().lower()
    if val in ("0", "false", "no", "off"):
        return False
    return True  # default on


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

    Precedence: --watcher CLI flag > JCODEMUNCH_WATCH env var > disabled.
    """
    flag = getattr(args, "watcher", None)
    if flag is not None:
        return _parse_watcher_flag(flag)
    env_val = os.environ.get("JCODEMUNCH_WATCH", "")
    if env_val:
        return _parse_watcher_flag(env_val)
    return False


# Create server
server = Server("jcodemunch-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    return [
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
                        "description": "Use AI to generate symbol summaries (requires ANTHROPIC_API_KEY or GOOGLE_API_KEY). Anthropic takes priority if both are set. When false, uses docstrings or signature fallback.",
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
                        "description": "Use AI to generate symbol summaries (requires ANTHROPIC_API_KEY or GOOGLE_API_KEY). Anthropic takes priority if both are set. When false, uses docstrings or signature fallback.",
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
            name="list_repos",
            description="List all indexed repositories.",
            inputSchema={
                "type": "object",
                "properties": {}
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
            description="Get all symbols (functions, classes, methods) in a file with signatures and summaries. Pass repo and file_path (e.g. 'src/main.py').",
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
                    }
                },
                "required": ["repo", "file_path"]
            }
        ),
        Tool(
            name="get_symbol",
            description="Get the full source code of a specific symbol. Use after identifying relevant symbols via get_file_outline or search_symbols.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "symbol_id": {
                        "type": "string",
                        "description": "Symbol ID from get_file_outline or search_symbols"
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
                "required": ["repo", "symbol_id"]
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
            name="get_symbols",
            description="Get full source code of multiple symbols in one call. Efficient for loading related symbols.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "symbol_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of symbol IDs to retrieve"
                    }
                },
                "required": ["repo", "symbol_ids"]
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
                        "enum": ["python", "javascript", "typescript", "tsx", "go", "rust", "java", "php", "dart", "csharp", "c", "cpp", "swift", "elixir", "ruby", "perl", "gdscript", "blade", "kotlin", "scala", "haskell", "julia", "r", "lua", "bash", "css", "sql", "toml", "erlang", "fortran", "gleam", "nix", "vue", "ejs", "verse", "groovy", "objc", "proto", "hcl", "graphql", "autohotkey", "asm", "xml", "openapi", "al"]
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
            description="Find all files that import from a given file path. Answers 'what uses this file?'. For dbt, resolves {{ ref() }} edges; {{ source() }} edges are extracted but not resolvable to files since sources are external. Requires re-indexing with v1.3.0+.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier"},
                    "file_path": {"type": "string", "description": "Target file path within the repo (e.g. 'src/features/intake/IntakeService.js')"},
                    "max_results": {"type": "integer", "default": 50, "description": "Maximum results"},
                },
                "required": ["repo", "file_path"],
            },
        ),
        Tool(
            name="find_references",
            description="Find all files that import or reference a given identifier (symbol name, module name, or class name). Answers 'where is this used?'. For dbt, traces {{ ref() }} edges; {{ source() }} specifiers are extracted but not resolvable since sources are external. Requires re-indexing with v1.3.0+.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository identifier"},
                    "identifier": {"type": "string", "description": "Symbol or module name to search for (e.g. 'bulkImport', 'IntakeService')"},
                    "max_results": {"type": "integer", "default": 50, "description": "Maximum results"},
                },
                "required": ["repo", "identifier"],
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
            description="Get a context bundle: full source + imports for one or more symbols. Multi-symbol bundles deduplicate imports when symbols share a file. Set include_callers=true to also get the list of files that directly import each symbol's file — useful for understanding usage before refactoring.",
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
            description="Diff the symbol sets of two indexed repositories (or two branches indexed separately). Shows added, removed, and changed symbols using content_hash for change detection. Index the same repo under two names to compare branches.",
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
            description="Scan the index and suggest useful search queries, key entry-point files, and index statistics. Great first call when starting to explore an unfamiliar repository — surfaces the most-imported files, top keywords, kind/language distribution, and ready-to-run example queries.",
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
            description="Analyse the blast radius of changing a symbol: find every file that imports its defining file and (optionally) references the symbol by name. Returns 'confirmed' files (import + name match) and 'potential' files (import only, e.g. wildcard). Use before renaming, deleting, or changing a function/class signature.",
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
    ]


@server.list_resources()
async def list_resources() -> list[Resource]:
    """Return empty resource list for client compatibility (e.g. Windsurf)."""
    return []


@server.list_prompts()
async def list_prompts() -> list:
    """Return empty prompt list for client compatibility (e.g. Windsurf)."""
    return []


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    storage_path = os.environ.get("CODE_INDEX_PATH")
    logger.info("tool_call: %s args=%s", name, {k: v for k, v in arguments.items() if k != "content"})

    try:
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
        elif name == "list_repos":
            result = await asyncio.to_thread(
                functools.partial(list_repos, storage_path=storage_path)
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
                    file_path=arguments.get("file_path") or arguments["file"],
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
        elif name == "get_symbol":
            result = await asyncio.to_thread(
                functools.partial(
                    get_symbol,
                    repo=arguments["repo"],
                    symbol_id=arguments["symbol_id"],
                    verify=arguments.get("verify", False),
                    context_lines=arguments.get("context_lines", 0),
                    storage_path=storage_path,
                )
            )
        elif name == "get_symbols":
            result = await asyncio.to_thread(
                functools.partial(
                    get_symbols,
                    repo=arguments["repo"],
                    symbol_ids=arguments["symbol_ids"],
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
                    file_path=arguments["file_path"],
                    max_results=arguments.get("max_results", 50),
                    storage_path=storage_path,
                )
            )
        elif name == "find_references":
            result = await asyncio.to_thread(
                functools.partial(
                    find_references,
                    repo=arguments["repo"],
                    identifier=arguments["identifier"],
                    max_results=arguments.get("max_results", 50),
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
        else:
            result = {"error": f"Unknown tool: {name}"}
        
        if isinstance(result, dict):
            result.setdefault("_meta", {})["powered_by"] = "jcodemunch-mcp by jgravelle · https://github.com/jgravelle/jcodemunch-mcp"
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    except KeyError as e:
        return [TextContent(type="text", text=json.dumps({"error": f"Missing required argument: {e}. Check the tool schema for correct parameter names."}, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, indent=2))]


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

    if log_path:
        try:
            open(log_path, "w", encoding="utf-8").close()
        except (PermissionError, OSError) as exc:
            print(f"WARNING: could not open watcher log {log_path!r}: {exc}", file=sys.stderr)
            log_path = None
        else:
            logger.info("Watcher log: %s", log_path)

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

    try:
        await server_coro_func(*server_args)
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(watcher_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass


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
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


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
            if auth != f"Bearer {token}":
                return JSONResponse(
                    {"error": "Unauthorized. Set Authorization: Bearer <JCODEMUNCH_HTTP_TOKEN> header."},
                    status_code=401,
                )
            return await call_next(request)

    return Middleware(BearerAuthMiddleware)


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


def main(argv: Optional[list[str]] = None):
    """Main entry point."""
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
        default=int(os.environ.get("JCODEMUNCH_WATCH_DEBOUNCE_MS", "2000")),
        help="Watcher debounce interval in ms (default: 2000, also via JCODEMUNCH_WATCH_DEBOUNCE_MS)",
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
        default=int(os.environ.get("JCODEMUNCH_WATCH_DEBOUNCE_MS", "2000")),
        help="Debounce interval in milliseconds (default: 2000, also via JCODEMUNCH_WATCH_DEBOUNCE_MS)",
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
        default=float(os.environ.get("JCODEMUNCH_CLAUDE_POLL_INTERVAL", "5")),
        help="How often (in seconds) to poll git for worktrees (default: 5, only with --repos)",
    )
    wc_parser.add_argument(
        "--debounce",
        type=int,
        default=int(os.environ.get("JCODEMUNCH_WATCH_DEBOUNCE_MS", "2000")),
        help="Debounce interval in milliseconds for file watching (default: 2000)",
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
        known_commands = {"serve", "watch", "hook-event", "watch-claude"}
        has_subcommand = any(arg in known_commands for arg in raw_argv if not arg.startswith("-"))
        if not has_subcommand:
            raw_argv = ["serve"] + list(raw_argv)
        args = parser.parse_args(raw_argv)

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
    else:
        # serve (default)
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
        else:
            if args.transport == "sse":
                asyncio.run(run_sse_server(args.host, args.port))
            elif args.transport == "streamable-http":
                asyncio.run(run_streamable_http_server(args.host, args.port))
            else:
                asyncio.run(run_stdio_server())


if __name__ == "__main__":
    main()
