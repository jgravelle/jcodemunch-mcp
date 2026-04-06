"""Claude Code hook handlers for jCodemunch enforcement.

PreToolUse  — intercept Read on large code files, suggest jCodemunch tools.
PostToolUse — auto-reindex after Edit/Write to keep the index fresh.

Both read JSON from stdin and write JSON to stdout per the Claude Code
hooks specification.
"""

import json
import os
import subprocess
import sys


# Extensions that benefit from jCodemunch structural navigation.
# Kept intentionally broad — mirrors languages.py LANGUAGE_REGISTRY.
_CODE_EXTENSIONS: set[str] = {
    ".py", ".pyi",
    ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx", ".mts", ".cts",
    ".go",
    ".rs",
    ".java",
    ".php",
    ".rb",
    ".cs", ".cshtml", ".razor",
    ".cpp", ".c", ".h", ".hpp", ".cc", ".cxx",
    ".swift",
    ".kt", ".kts",
    ".scala",
    ".dart",
    ".lua", ".luau",
    ".ex", ".exs",
    ".erl", ".hrl",
    ".vue", ".svelte",
    ".sql",
    ".gd",       # GDScript
    ".al",       # AL (Business Central)
    ".gleam",
    ".nix",
    ".hcl", ".tf",
    ".proto",
    ".graphql", ".gql",
    ".verse",
    ".jl",       # Julia
    ".r", ".R",
    ".hs",       # Haskell
    ".f90", ".f95", ".f03", ".f08",  # Fortran
    ".groovy",
    ".pl", ".pm",  # Perl
    ".bash", ".sh", ".zsh",
}

# Minimum file size to trigger jCodemunch suggestion.
# Override with JCODEMUNCH_HOOK_MIN_SIZE env var.
_MIN_SIZE_BYTES = int(os.environ.get("JCODEMUNCH_HOOK_MIN_SIZE", "4096"))


def run_pretooluse() -> int:
    """PreToolUse hook: intercept Read calls on large code files.

    Reads hook JSON from stdin.  If the target is a code file above the
    size threshold, returns a ``deny`` decision with a message directing
    Claude to use jCodemunch tools instead.

    Small files, non-code files, and unreadable paths are silently allowed.

    Returns exit code (always 0 — errors are swallowed to avoid blocking).
    """
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # Unparseable → allow

    file_path: str = data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return 0

    # Check extension
    _, ext = os.path.splitext(file_path)
    if ext.lower() not in _CODE_EXTENSIONS:
        return 0  # Not a code file → allow

    # Check size
    try:
        size = os.path.getsize(file_path)
    except OSError:
        return 0  # Can't stat → allow (file may not exist yet)

    if size < _MIN_SIZE_BYTES:
        return 0  # Small file → allow

    # Deny with actionable suggestion
    reason = (
        f"This is a {size:,}-byte code file. "
        "Use jCodemunch for efficient navigation: "
        "get_file_outline to see the file structure, "
        "then get_symbol_source for specific symbols you need. "
        "Only fall back to Read when you need exact line numbers for Edit, "
        "or for non-code files."
    )

    result = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    json.dump(result, sys.stdout)
    return 0


def run_posttooluse() -> int:
    """PostToolUse hook: auto-index files after Edit/Write.

    Reads hook JSON from stdin, extracts the file path, and spawns
    ``jcodemunch-mcp index-file <path>`` as a fire-and-forget background
    process to keep the index fresh.

    Non-code files are skipped.  Errors are swallowed silently.

    Returns exit code (always 0).
    """
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    file_path: str = data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return 0

    # Only re-index code files
    _, ext = os.path.splitext(file_path)
    if ext.lower() not in _CODE_EXTENSIONS:
        return 0

    # Fire-and-forget: spawn index-file in background
    try:
        kwargs: dict = dict(
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # On Windows, CREATE_NO_WINDOW prevents a console flash
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        subprocess.Popen(
            ["jcodemunch-mcp", "index-file", file_path],
            **kwargs,
        )
    except (OSError, FileNotFoundError):
        pass  # jcodemunch-mcp not in PATH → skip silently

    return 0


def run_precompact() -> int:
    """PreCompact hook: generate session snapshot before context compaction.

    Reads hook JSON from stdin. Builds a compact snapshot of the current
    session state and returns it as a message for context injection.

    Returns exit code (always 0 — errors are swallowed to avoid blocking).
    """
    try:
        json.load(sys.stdin)  # Validate stdin is valid JSON
    except (json.JSONDecodeError, ValueError):
        return 0

    # Build snapshot in-process (no MCP round-trip needed)
    try:
        from jcodemunch_mcp.tools.get_session_snapshot import get_session_snapshot
        snapshot_result = get_session_snapshot()
        snapshot_text = snapshot_result.get("snapshot", "")
    except Exception:
        return 0  # Snapshot failure must not block compaction

    if not snapshot_text:
        return 0

    # Return snapshot as hook output for context injection.
    # PreCompact has no hookSpecificOutput variant in Claude Code's schema,
    # so we use the top-level systemMessage field instead.
    result = {
        "systemMessage": snapshot_text,
    }
    json.dump(result, sys.stdout)
    return 0
