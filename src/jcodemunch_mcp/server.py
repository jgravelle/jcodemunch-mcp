"""MCP server for jcodemunch-mcp."""

import argparse
import asyncio
import functools
import json
import logging
import os
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
from .tools.search_columns import search_columns
from .tools.get_context_bundle import get_context_bundle


logger = logging.getLogger(__name__)


def _default_use_ai_summaries() -> bool:
    """Return the default for use_ai_summaries, respecting JCODEMUNCH_USE_AI_SUMMARIES env var."""
    val = os.environ.get("JCODEMUNCH_USE_AI_SUMMARIES", "").strip().lower()
    if val in ("0", "false", "no", "off"):
        return False
    return True  # default on


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
                        "description": "Whether to follow symlinks. Default false for security.",
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
                        "enum": ["function", "class", "method", "constant", "type"]
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Optional glob pattern to filter files (e.g., 'src/**/*.py')"
                    },
                    "language": {
                        "type": "string",
                        "description": "Optional filter by language",
                        "enum": ["python", "javascript", "typescript", "tsx", "go", "rust", "java", "php", "dart", "csharp", "c", "cpp", "swift", "elixir", "ruby", "perl", "gdscript", "blade", "kotlin", "scala", "haskell", "julia", "r", "lua", "bash", "css", "sql", "toml", "erlang", "fortran", "gleam", "nix", "vue", "ejs", "verse", "groovy", "objc", "proto", "hcl", "graphql", "autohotkey", "xml", "openapi", "asm"]
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 10
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
            description="Find all files that import from a given file path. Answers 'what uses this file?'. Requires re-indexing with v1.3.0+.",
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
            description="Find all files that import or reference a given identifier (symbol name, module name, or class name). Answers 'where is this used?'. Requires re-indexing with v1.3.0+.",
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
            description="Get a context bundle for a symbol: its full definition plus all import/require statements from the same file. Gives an AI just enough context to understand and modify a symbol without loading the entire file. Use after identifying a symbol via search_symbols or get_file_outline.",
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
                    }
                },
                "required": ["repo", "symbol_id"]
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
            result = await asyncio.to_thread(
                functools.partial(
                    search_symbols,
                    repo=arguments["repo"],
                    query=arguments["query"],
                    kind=arguments.get("kind"),
                    file_pattern=arguments.get("file_pattern"),
                    language=arguments.get("language"),
                    max_results=arguments.get("max_results", 10),
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
                    symbol_id=arguments["symbol_id"],
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


async def run_sse_server(host: str, port: int):
    """Run the MCP server with SSE transport (persistent HTTP mode)."""
    import sys
    import uvicorn
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.routing import Mount, Route
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

    starlette_app = Starlette(routes=[
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ])

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
    import anyio
    import uvicorn
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.routing import Route
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

    starlette_app = Starlette(routes=[
        Route("/mcp", endpoint=handle_mcp, methods=["GET", "POST", "DELETE"]),
    ])

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


def main(argv: Optional[list[str]] = None):
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="jcodemunch-mcp",
        description="Run the jCodeMunch MCP server.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--transport",
        default=os.environ.get("JCODEMUNCH_TRANSPORT", "stdio"),
        choices=["stdio", "sse", "streamable-http"],
        help="Transport mode: stdio (default), sse, or streamable-http (also via JCODEMUNCH_TRANSPORT env var)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("JCODEMUNCH_HOST", "127.0.0.1"),
        help="Host to bind to in HTTP transport mode (also via JCODEMUNCH_HOST env var, default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("JCODEMUNCH_PORT", "8901")),
        help="Port to listen on in HTTP transport mode (also via JCODEMUNCH_PORT env var, default: 8901)",
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
    args = parser.parse_args(argv)

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

    if args.transport == "sse":
        asyncio.run(run_sse_server(args.host, args.port))
    elif args.transport == "streamable-http":
        asyncio.run(run_streamable_http_server(args.host, args.port))
    else:
        asyncio.run(run_stdio_server())


if __name__ == "__main__":
    main()
