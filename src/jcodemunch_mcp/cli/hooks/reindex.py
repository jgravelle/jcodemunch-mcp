"""PostToolUse auto-reindex (Claude Code and Copilot variants)."""

import json
import os
import subprocess
import sys

from ._common import _CODE_EXTENSIONS, _note_transcript_root, _read_hook_payload


def _self_invocation() -> list[str]:
    """Argv prefix that re-invokes THIS jcodemunch-mcp install.

    The hook process inherits Claude Code's minimal hook-shell PATH — the very
    reason ``init``'s ``_hook_invocation`` writes an absolute path into
    settings.json — so a bare-name child spawn dies silently on exactly the
    installs (pipx, pip --user, framework Python) that needed the absolute
    path. Ask `install_layout.own_executable` (the absolute launch path, then
    the interpreter's scripts directory); fall back to
    ``python -m jcodemunch_mcp`` which needs no PATH lookup at all.
    """
    # `own_executable` never trusts a RELATIVE argv0: it would be re-resolved
    # against the hook's cwd, the checked-out (untrusted) repo.
    from ...install_layout import own_executable

    exe = own_executable()
    if exe:
        return [exe]
    return [sys.executable, "-m", "jcodemunch_mcp"]


def _spawn_index_file(file_path: str) -> None:
    """Fire-and-forget `index-file` spawn shared by both PostToolUse handlers.

    One owner for the spawn kwargs, the Windows console flag, and the except
    tuple (ValueError covers hostile NUL-byte paths) — a hardening applied to
    one handler must not miss the other.
    """
    try:
        kwargs: dict = dict(
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # On Windows, CREATE_NO_WINDOW prevents a console flash
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        subprocess.Popen(
            _self_invocation() + ["index-file", file_path],
            **kwargs,
        )
    except (OSError, ValueError):
        pass  # executable unavailable / hostile path → skip silently


def run_posttooluse() -> int:
    """PostToolUse hook: auto-index files after Edit/Write.

    Reads hook JSON from stdin, extracts the file path, and spawns
    ``jcodemunch-mcp index-file <path>`` as a fire-and-forget background
    process to keep the index fresh.

    Non-code files are skipped.  Errors are swallowed silently.

    Returns exit code (always 0).
    """
    data = _read_hook_payload()
    if data is None:
        return 0

    _note_transcript_root(data)

    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    file_path = tool_input.get("file_path", "")
    if not isinstance(file_path, str) or not file_path:
        return 0

    # Only re-index code files
    _, ext = os.path.splitext(file_path)
    if ext.lower() not in _CODE_EXTENSIONS:
        return 0

    # Fire-and-forget: spawn index-file in background
    _spawn_index_file(file_path)
    return 0


def run_copilot_posttooluse() -> int:
    """GitHub Copilot ``postToolUse`` hook: auto-index files after Edit/Write.

    Adapter for the Copilot CLI / cloud-agent hook payload shape, which
    differs from Claude Code's:

    Copilot stdin JSON::

        {
            "timestamp": "...",
            "cwd": "...",
            "toolName": "edit" | "write" | "create_file" | ...,
            "toolArgs": "{\\"path\\": \\"/abs/path/to/file.py\\", ...}",
            "toolResult": "..."
        }

    ``toolArgs`` arrives as a JSON-encoded **string**, not a nested object.
    Tool names vary across Copilot tool implementations, so we extract a
    file path heuristically: any value at the top level of toolArgs whose
    key matches ``path``/``file_path``/``filename``/``filePath`` and points
    at an existing file. If the file is a code file under a directory that
    has been indexed, spawn ``jcodemunch-mcp index-file <path>`` as a
    fire-and-forget background process. Errors are swallowed silently —
    Copilot ignores postToolUse stdout/exit code, so a failing reindex
    must never disrupt the agent flow.
    """
    data = _read_hook_payload()
    if data is None:
        return 0

    tool_args_raw = data.get("toolArgs", "")
    if isinstance(tool_args_raw, str):
        try:
            tool_args = json.loads(tool_args_raw) if tool_args_raw else {}
        except (json.JSONDecodeError, ValueError):
            return 0
        if not isinstance(tool_args, dict):
            return 0  # toolArgs decoded to a non-dict (list/str/number)
    elif isinstance(tool_args_raw, dict):
        tool_args = tool_args_raw
    else:
        return 0

    file_path = ""
    for key in ("file_path", "filePath", "path", "filename"):
        v = tool_args.get(key)
        if isinstance(v, str) and v:
            file_path = v
            break
    if not file_path:
        return 0

    _, ext = os.path.splitext(file_path)
    if ext.lower() not in _CODE_EXTENSIONS:
        return 0

    _spawn_index_file(file_path)
    return 0
