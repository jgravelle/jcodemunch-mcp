"""MCP server for jcodemunch-mcp."""

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from mcp.server import Server
from mcp.types import Tool, TextContent

from . import __version__
from .tools.index_repo import index_repo
from .tools.index_folder import index_folder
from .tools.list_repos import list_repos
from .tools.get_file_tree import get_file_tree
from .tools.get_file_outline import get_file_outline
from .tools.get_symbol import get_symbol, get_symbols
from .tools.search_symbols import search_symbols
from .tools.invalidate_cache import invalidate_cache
from .tools.search_text import search_text
from .tools.get_repo_outline import get_repo_outline
from .storage.token_tracker import get_savings_report


# Create server
server = Server("jcodemunch-mcp")


class _HelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter,
    argparse.RawDescriptionHelpFormatter,
):
    """Argparse formatter showing defaults while preserving epilog line breaks."""


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    return [
        Tool(
            name="index_repo",
            description="Index a GitHub repository's source code. Fetches files, parses ASTs, extracts symbols, and saves to local storage.",
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
                    "incremental": {
                        "type": "boolean",
                        "description": "When true and an existing index exists, only re-index changed files.",
                        "default": False
                    }
                },
                "required": ["url"]
            }
        ),
        Tool(
            name="index_folder",
            description="Index a local folder containing source code. Response includes `discovery_skip_counts` (files filtered per reason), `no_symbols_count`/`no_symbols_files` (files with no extractable symbols) for diagnosing missing files.",
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
                        "description": "Additional gitignore-style patterns to exclude from indexing"
                    },
                    "follow_symlinks": {
                        "type": "boolean",
                        "description": "Whether to follow symlinks. Default false for security.",
                        "default": False
                    },
                    "incremental": {
                        "type": "boolean",
                        "description": "When true and an existing index exists, only re-index changed files.",
                        "default": False
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
                        "enum": ["function", "class", "method", "constant", "type"]
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Optional glob pattern to filter files (e.g., 'src/**/*.py')"
                    },
                    "language": {
                        "type": "string",
                        "description": "Optional filter by language",
                        "enum": ["python", "javascript", "typescript", "tsx", "go", "rust", "java", "php", "dart", "csharp", "c", "cpp", "swift", "elixir", "ruby", "perl", "gdscript", "blade"]
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 10
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
            description="Full-text search across indexed file contents. Useful when symbol search misses (e.g., string literals, comments, config values).",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo or just repo name)"
                    },
                    "query": {
                        "type": "string",
                        "description": "Text to search for (case-insensitive substring match)"
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Optional glob pattern to filter files (e.g., '*.py')"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matching lines to return",
                        "default": 20
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
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    storage_path = os.environ.get("CODE_INDEX_PATH")
    
    try:
        if name == "index_repo":
            result = await index_repo(
                url=arguments["url"],
                use_ai_summaries=arguments.get("use_ai_summaries", True),
                storage_path=storage_path,
                incremental=arguments.get("incremental", False),
            )
        elif name == "index_folder":
            result = index_folder(
                path=arguments["path"],
                use_ai_summaries=arguments.get("use_ai_summaries", True),
                storage_path=storage_path,
                extra_ignore_patterns=arguments.get("extra_ignore_patterns"),
                follow_symlinks=arguments.get("follow_symlinks", False),
                incremental=arguments.get("incremental", False),
            )
        elif name == "list_repos":
            result = list_repos(storage_path=storage_path)
        elif name == "get_file_tree":
            result = get_file_tree(
                repo=arguments["repo"],
                path_prefix=arguments.get("path_prefix", ""),
                include_summaries=arguments.get("include_summaries", False),
                storage_path=storage_path
            )
        elif name == "get_file_outline":
            result = get_file_outline(
                repo=arguments["repo"],
                file_path=arguments["file_path"],
                storage_path=storage_path
            )
        elif name == "get_symbol":
            result = get_symbol(
                repo=arguments["repo"],
                symbol_id=arguments["symbol_id"],
                verify=arguments.get("verify", False),
                context_lines=arguments.get("context_lines", 0),
                storage_path=storage_path
            )
        elif name == "get_symbols":
            result = get_symbols(
                repo=arguments["repo"],
                symbol_ids=arguments["symbol_ids"],
                storage_path=storage_path
            )
        elif name == "search_symbols":
            result = search_symbols(
                repo=arguments["repo"],
                query=arguments["query"],
                kind=arguments.get("kind"),
                file_pattern=arguments.get("file_pattern"),
                language=arguments.get("language"),
                max_results=arguments.get("max_results", 10),
                storage_path=storage_path
            )
        elif name == "invalidate_cache":
            result = invalidate_cache(
                repo=arguments["repo"],
                storage_path=storage_path
            )
        elif name == "search_text":
            result = search_text(
                repo=arguments["repo"],
                query=arguments["query"],
                file_pattern=arguments.get("file_pattern"),
                max_results=arguments.get("max_results", 20),
                storage_path=storage_path
            )
        elif name == "get_repo_outline":
            result = get_repo_outline(
                repo=arguments["repo"],
                storage_path=storage_path
            )
        else:
            result = {"error": f"Unknown tool: {name}"}
        
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    
    except KeyError as e:
        return [TextContent(type="text", text=json.dumps({"error": f"Missing required argument: {e}. Check the tool schema for correct parameter names."}, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, indent=2))]


async def run_server():
    """Run the MCP server."""
    from mcp.server.stdio import stdio_server
    
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


def _format_token_stats_text(report: dict[str, Any]) -> str:
    """Format savings report for humans (non-JSON)."""
    windows = report.get("equivalent_context_windows", {})
    costs = report.get("total_cost_avoided", {})
    lines = [
        "jCodeMunch Token Savings",
        "------------------------",
        f"Total tokens saved: {report.get('total_tokens_saved', 0):,}",
        f"Approx raw bytes avoided: {report.get('approx_raw_bytes_avoided', 0):,}",
        f"Cost avoided (Claude Opus): ${costs.get('claude_opus', 0):.4f}",
        f"Cost avoided (GPT-5 latest): ${costs.get('gpt5_latest', 0):.4f}",
        f"Equivalent 32k windows: {windows.get('32k', 0)}",
        f"Equivalent 128k windows: {windows.get('128k', 0)}",
        f"Equivalent 1m windows: {windows.get('1m', 0)}",
    ]
    if "telemetry_enabled" in report:
        telemetry = "enabled" if report.get("telemetry_enabled") else "disabled"
        lines.append(f"Telemetry: {telemetry}")
    if "anon_id_present" in report:
        anon = "yes" if report.get("anon_id_present") else "no"
        lines.append(f"Anon ID present: {anon}")
    if report.get("savings_file"):
        lines.append(f"Savings file: {report.get('savings_file')}")

    return "\n".join(lines)


def _token_stats_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Return a smaller, user-focused token stats summary."""
    return {
        "total_tokens_saved": report.get("total_tokens_saved", 0),
        "approx_raw_bytes_avoided": report.get("approx_raw_bytes_avoided", 0),
        "total_cost_avoided": report.get("total_cost_avoided", {}),
        "equivalent_context_windows": report.get("equivalent_context_windows", {}),
    }


def _token_stats_fields_explainer() -> str:
    """Help epilog explaining what token-stat fields are based on."""
    return (
        "token-stats fields:\n"
        "jCodeMunch Token Savings\n"
        "------------------------\n"
        "Cost avoided (Claude Opus): Estimated savings using Claude Opus input pricing (total_tokens_saved × $15 / 1M).\n"
        "Cost avoided (GPT-5 latest): Estimated savings using GPT-5 latest input pricing (total_tokens_saved × $10 / 1M).\n"
        "Equivalent 32k windows: How many 32,000-token context windows the saved tokens equal.\n"
        "Equivalent 128k windows: How many 128,000-token context windows the saved tokens equal.\n"
        "Equivalent 1m windows: How many 1,000,000-token context windows the saved tokens equal.\n"
        "Anon ID present: Whether _savings.json has an anonymous install ID for optional community meter sharing.\n"
    )


def _print_token_stats(output: str) -> None:
    """Print token stats text, preferring Rich when available."""
    import importlib.util

    if importlib.util.find_spec("rich") is not None:
        from rich.console import Console

        Console().print(output)
    else:
        print(output)


def main(argv: Optional[list[str]] = None):
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="jcodemunch-mcp",
        description="Run the jCodeMunch MCP stdio server.",
        formatter_class=_HelpFormatter,
        epilog=_token_stats_fields_explainer(),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
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
    parser.add_argument(
        "--token-stats",
        action="store_true",
        help="Print a concise token-savings summary and exit.",
    )
    parser.add_argument(
        "--token-stats-all",
        action="store_true",
        help="Print the full token-savings report (includes telemetry/savings-file metadata) and exit.",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "json"],
        default="text",
        help="Output format used by --token-stats and --token-stats-all.",
    )
    args = parser.parse_args(argv)

    if args.token_stats or args.token_stats_all:
        full_report = get_savings_report(os.environ.get("CODE_INDEX_PATH"))
        report = full_report if args.token_stats_all else _token_stats_summary(full_report)
        if args.output_format == "json":
            print(json.dumps(report, indent=2))
        else:
            _print_token_stats(_format_token_stats_text(report))
        return

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

    asyncio.run(run_server())


if __name__ == "__main__":
    main()
