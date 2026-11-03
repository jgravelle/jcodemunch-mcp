"""jcodemunch-mcp: MCP server that gives AI coding agents the exact function or class they asked for, not the whole file. Runs on your machine, 70+ languages. Free for personal use; work use needs a one-time license."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("jcodemunch-mcp")
except PackageNotFoundError:
    __version__ = "unknown"
