"""jcodemunch-mcp init — one-command onboarding for MCP clients."""

import contextlib
import functools
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CLAUDE_MD_MARKER = "## Code Exploration Policy"

_CLAUDE_MD_POLICY = """\
## Code Exploration Policy

Always use jCodemunch-MCP tools for code navigation. Never fall back to Read, Grep, Glob, or Bash for code exploration.
**Exception:** Use `Read` when you need to edit a file — the agent harness requires a `Read` before `Edit`/`Write` will succeed. Use jCodemunch tools to *find and understand* code, then `Read` only the specific file you're about to modify.

**Start any session:**
1. `resolve_repo { "path": "." }` — confirm the project is indexed. If not: `index_folder { "path": "." }`
2. `suggest_queries` — when the repo is unfamiliar

**Finding code:**
- symbol by name → `search_symbols` (add `kind=`, `language=`, `file_pattern=`, `decorator=` to narrow)
- decorator-aware queries → `search_symbols(decorator="X")` to find symbols with a specific decorator (e.g. `@property`, `@route`); combine with set-difference to find symbols *lacking* a decorator (e.g. "which endpoints lack CSRF protection?")
- string, comment, config value → `search_text` (supports regex, `context_lines`)
- database columns (dbt/SQLMesh) → `search_columns`

**Reading code:**
- before opening any file → `get_file_outline` first
- one or more symbols → `get_symbol_source` (single ID → flat object; array → batch)
- symbol + its imports → `get_context_bundle`
- specific line range only → `get_file_content` (last resort)

**Repo structure:**
- `get_repo_outline` → dirs, languages, symbol counts
- `get_file_tree` → file layout, filter with `path_prefix`

**Relationships & impact:**
- what imports this file → `find_importers`
- where is this name used → `find_references`
- is this identifier used anywhere → `check_references`
- file dependency graph → `get_dependency_graph`
- what breaks if I change X → `get_blast_radius`
- what symbols actually changed since last commit → `get_changed_symbols`
- find unreachable/dead code → `find_dead_code`
- class hierarchy → `get_class_hierarchy`

## Session-Aware Routing

**Opening move for any task:**
1. `plan_turn { "repo": "...", "query": "your task description", "model": "<your-model-id>" }` — get confidence + recommended files; the `model` parameter narrows the exposed tool list to match your capabilities at zero extra requests.
2. Obey the confidence level:
   - `high` → go directly to recommended symbols, max 2 supplementary reads
   - `medium` → explore recommended files, max 5 supplementary reads
   - `low` → the feature likely doesn't exist. Report the gap to the user. Do NOT search further hoping to find it.
3. **One-call shortcut for a concrete task** — `assemble_task_context { "repo": "...", "task": "..." }` returns a single token-budgeted, source-attributed context capsule. It auto-classifies the task (explore / debug / refactor / extend / audit / review), auto-extracts anchor symbols, and runs the intent-appropriate sequence of the tools below end-to-end — so you get the whole context in one request instead of chaining the primitives by hand. Prefer it over a manual chain when the task is well-defined; fall back to step 1's routing when you need to decide *whether* the feature exists first.

**Interpreting search results:**
- If `search_symbols` returns `negative_evidence` with `verdict: "no_implementation_found"`:
  - Do NOT re-search with different terms hoping to find it
  - Do NOT assume a related file (e.g. auth middleware) implements the missing feature (e.g. CSRF)
  - DO report: "No existing implementation found for X. This would need to be created."
  - DO check `related_existing` files — they show what's nearby, not what exists
- If `verdict: "low_confidence_matches"`: examine the matches critically before assuming they implement the feature

**After editing files:**
- If PostToolUse hooks are installed (Claude Code only), edited files are auto-reindexed
- Otherwise, call `register_edit` with edited file paths to invalidate caches and keep the index fresh
- For bulk edits (5+ files), always use `register_edit` with all paths to batch-invalidate

**Token efficiency:**
- If `_meta` contains `budget_warning`: stop exploring and work with what you have. Results are never silently shortened — the warning is advisory, and what you got is complete
- Use `get_session_context` to check what you've already read — avoid re-reading the same files

## Model-Driven Tool Tiering

Your jcodemunch-mcp server narrows the exposed tool list based on the model you are running as. To avoid wasting requests on primitives when a composite would do, always include `model="<your-model-id>"` in your opening `plan_turn` call.

Replace `<your-model-id>` with your active model:
- Claude Opus variants → `claude-opus-4-7` (or any `claude-opus-*`)
- Claude Sonnet variants → `claude-sonnet-4-6`
- Claude Haiku variants → `claude-haiku-4-5`
- GPT-4o / GPT-5 / o1 / Llama → use the model id as printed by your runner

The `model=` parameter rides on the existing `plan_turn` call — it does **not** add a separate tool invocation. If `plan_turn` is not appropriate for a non-code task, call `announce_model(model="...")` once instead.
"""

# Policy for `tool_surface="counter"`, the default on a genuinely first-ever
# install. The full policy above names ~25 tools directly; under the front door
# the server advertises only `order`/`menu`/`route`, so that text describes calls
# the client never offers the model. The tools stay callable by name, which is
# why the mismatch was silent rather than loud (#397).
#
# Deliberately short: the point of the front door is that the agent discovers
# capabilities at need instead of carrying 91 schemas plus a long policy in every
# turn. Naming the workflow, not the catalogue, is what keeps that promise.
_CLAUDE_MD_POLICY_COUNTER = """\
## Code Exploration Policy

Always use jCodeMunch-MCP for code navigation. Never fall back to Read, Grep, Glob, or Bash for code exploration.
**Exception:** use `Read` when you are about to edit a file — the harness requires a `Read` before `Edit`/`Write`. Use jCodeMunch to *find and understand* code, then `Read` only the file you are changing.

This server runs the **front door** surface: three tools reach every jCodeMunch capability, so the tool list stays small and the catalogue is fetched only when you need it.

**Start any session:**
1. `order { "action": "resolve_repo", "args": { "path": "." } }` — confirm the project is indexed. If it is not: `order { "action": "index_folder", "args": { "path": "." } }`

**Then, for any task:**
- Know what you want → `order { "action": "<name>", "args": { ... } }`
- Know the goal, not the tool → `route { "query": "your task in a sentence" }` picks the action and shapes the arguments
- Want to see what exists → `menu { "query": "what you are trying to do" }` returns matching actions with example arguments
- Want the whole catalogue and the usage rules → `jcodemunch_guide`

`menu` and `jcodemunch_guide` list every action this server can run, including ones absent from your tool list. That is expected: the front door is the way to call them.

**Interpreting results:**
- A `verdict` of `no_implementation_found` is evidence of absence. Report the gap; do not re-search with different wording.
- A `verdict` of `degraded` means a channel was unavailable, so absence is NOT proven. Read the note before relying on the result.
- `source: ""` alongside `source_status` means the body could not be read, not that the symbol is empty.

**After editing files:**
- With PostToolUse hooks installed (Claude Code), edited files are reindexed automatically.
- Otherwise `order { "action": "register_edit", "args": { "paths": [...] } }` after an edit, batched for bulk changes.

**Announce your model once per session** so the server can size its answers: `announce_model { "model": "<your-model-id>" }`.
"""

_MCP_ENTRY = {
    "command": "uvx",
    "args": ["jcodemunch-mcp"],
}

def _hook_invocation() -> str:
    """Return the executable path used in hook command strings.

    Claude Code spawns hooks via /bin/sh on macOS/Linux and via bash on
    Windows (Git Bash / MSYS), which uses a minimal PATH that excludes
    ~/.local/bin, ~/Library/Python/*/bin, pipx venvs, etc. Writing the
    bare name ``jcodemunch-mcp`` works only when the subshell's PATH
    happens to match — fragile. Resolve to an absolute path at install
    time so hooks work regardless of the spawning shell.

    On Windows, normalise the resolved path to forward slashes. The
    backslash form (e.g. ``C:\\Python314\\Scripts\\jcodemunch-mcp.EXE``)
    survives JSON serialisation fine, but bash treats every ``\\`` as an
    escape character and silently eats them — the path becomes
    ``C:Python314Scriptsjcodemunch-mcp.EXE`` at execution time and the
    hook fails with "command not found." Forward slashes work in every
    Windows API that accepts a path and don't trigger bash escape
    parsing.
    """
    resolved = shutil.which("jcodemunch-mcp")
    if not resolved:
        # Fall back to bare name; user will get a clear error if PATH is wrong.
        return "jcodemunch-mcp"
    if platform.system() == "Windows":
        resolved = resolved.replace("\\", "/")
    if " " in resolved:
        return f'"{resolved}"'
    return resolved


def _worktree_hooks() -> dict[str, Any]:
    exe = _hook_invocation()
    return {
        "WorktreeCreate": [{
            "matcher": "",
            "hooks": [{"type": "command", "command": f"{exe} hook-event create"}],
        }],
        "WorktreeRemove": [{
            "matcher": "",
            "hooks": [{"type": "command", "command": f"{exe} hook-event remove"}],
        }],
    }


def _enforcement_hooks() -> dict[str, Any]:
    exe = _hook_invocation()
    return {
        "PreToolUse": [{
            "matcher": "Read|Grep",
            "hooks": [{"type": "command", "command": f"{exe} hook-pretooluse"}],
        }],
        "PostToolUse": [{
            "matcher": "Edit|Write",
            "hooks": [{"type": "command", "command": f"{exe} hook-posttooluse"}],
        }],
        "PreCompact": [{
            "matcher": "",
            "hooks": [{"type": "command", "command": f"{exe} hook-precompact"}],
        }],
        # Restore only sources whose persisted journal still describes this session.
        "SessionStart": [{
            "matcher": "compact|resume|fork",
            "hooks": [{"type": "command", "command": f"{exe} hook-sessionstart"}],
        }],
        "TaskCompleted": [{
            "matcher": "",
            "hooks": [{"type": "command", "command": f"{exe} hook-taskcomplete"}],
        }],
        "SubagentStart": [{
            "matcher": "",
            "hooks": [{"type": "command", "command": f"{exe} hook-subagent-start"}],
        }],
    }

# Cursor rules use MDC format (frontmatter + markdown).
# alwaysApply: true ensures the rule is in context for every agent turn,
# including subagents — which is the main reliability complaint.
_CURSOR_RULES_CONTENT = """\
---
description: Use jCodemunch MCP tools for all code navigation instead of built-in search
alwaysApply: true
---

""" + _CLAUDE_MD_POLICY

# Windsurf uses a plain-text .windsurfrules file in the project root.
_WINDSURF_RULES_CONTENT = _CLAUDE_MD_POLICY


# ---------------------------------------------------------------------------
# Client detection
# ---------------------------------------------------------------------------

# Every configuration method `configure_client` knows how to dispatch.
# ⚠ This is the ONE list. It used to be a comment on MCPClient.method plus a
# hardcoded tuple inside a test, and adding a method meant remembering both --
# `test_detect_clients_returns_list` caught `toml_codex` precisely because it
# had its own copy. A declared method with no dispatch branch would otherwise
# return "unknown method for X" at runtime, which reads as a client we support.
CONFIGURE_METHODS = frozenset(
    {"cli", "json_patch", "toml_codex", "json_opencode", "json_vscode"}
)


class MCPClient:
    """Represents a detected MCP client and how to configure it."""

    def __init__(self, name: str, config_path: Optional[Path], method: str):
        self.name = name
        self.config_path = config_path
        self.method = method  # one of CONFIGURE_METHODS

    def __repr__(self) -> str:
        if self.config_path:
            return f"{self.name} ({self.config_path})"
        return self.name


def _find_executable(name: str) -> Optional[str]:
    """Return path to executable or None."""
    return shutil.which(name)


def _expand_appdata(*parts: str) -> Path:
    """Expand %APPDATA% on Windows, ~/ on others."""
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(appdata, *parts)
    return Path.home().joinpath(*parts)


def _detect_clients() -> list[MCPClient]:
    """Detect installed MCP clients."""
    clients: list[MCPClient] = []

    # Claude Code CLI
    if _find_executable("claude"):
        clients.append(MCPClient("Claude Code", None, "cli"))

    # Claude Desktop
    if platform.system() == "Darwin":
        p = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif platform.system() == "Windows":
        p = _expand_appdata("Claude", "claude_desktop_config.json")
    else:
        p = Path.home() / ".config" / "claude" / "claude_desktop_config.json"
    if p.parent.exists():
        clients.append(MCPClient("Claude Desktop", p, "json_patch"))

    # Cursor
    cursor_dir = Path.home() / ".cursor"
    if cursor_dir.exists():
        clients.append(MCPClient("Cursor", cursor_dir / "mcp.json", "json_patch"))

    # Windsurf
    for d in [Path.home() / ".windsurf", Path.home() / ".codeium" / "windsurf"]:
        if d.exists():
            clients.append(MCPClient("Windsurf", d / "mcp_config.json", "json_patch"))
            break

    # Continue
    continue_dir = Path.home() / ".continue"
    if continue_dir.exists():
        clients.append(MCPClient("Continue", continue_dir / "config.json", "json_patch"))

    # Codex CLI. Detected by its config directory OR the executable: `codex`
    # can be on PATH before ~/.codex exists on a fresh install, and the
    # directory can exist without the binary on a machine it was removed from.
    if (Path.home() / ".codex").exists() or _find_executable("codex"):
        clients.append(MCPClient("Codex", _codex_config_path(), "toml_codex"))

    # opencode
    if (Path.home() / ".config" / "opencode").exists() or _find_executable("opencode"):
        clients.append(MCPClient("opencode", _opencode_config_path(), "json_opencode"))

    # Gemini CLI. ⚠ Keyed on settings.json / the executable, NOT on ~/.gemini
    # existing: Antigravity shares that directory but reads a DIFFERENT file
    # (~/.gemini/config/mcp_config.json), so a directory check would offer to
    # configure Gemini CLI on a machine that only has Antigravity and write a
    # settings.json nothing reads.
    gemini_settings = Path.home() / ".gemini" / "settings.json"
    if gemini_settings.exists() or _find_executable("gemini"):
        clients.append(MCPClient("Gemini CLI", gemini_settings, "json_patch"))

    # Cline. ⚠ The documented path is the CLI's ~/.cline/mcp.json. The VS Code
    # extension keeps its own settings under an editor globalStorage directory
    # that Cline does not document per-platform, so it is deliberately NOT
    # guessed at here -- CLIENTS.md points extension users at the marketplace UI.
    cline_config = Path.home() / ".cline" / "mcp.json"
    if cline_config.exists() or _find_executable("cline"):
        clients.append(MCPClient("Cline", cline_config, "json_patch"))

    # VS Code / GitHub Copilot. Workspace-scoped, so this requires an existing
    # .vscode/ directory rather than merely finding `code` on PATH: the latter
    # is true on most developer machines and would CREATE .vscode/mcp.json in
    # whatever directory init happened to run in.
    vscode_dir = Path.cwd() / ".vscode"
    if vscode_dir.exists():
        clients.append(MCPClient("VS Code (Copilot)", vscode_dir / "mcp.json", "json_vscode"))

    return clients


# ---------------------------------------------------------------------------
# Config patching
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file, returning {} if it doesn't exist."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: dict[str, Any], *, backup: bool = True) -> None:
    """Write JSON, optionally creating a .bak backup first."""
    if backup and path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _has_jcodemunch_entry(data: dict[str, Any]) -> bool:
    """Check if jcodemunch is already configured in an MCP config."""
    servers = data.get("mcpServers", {})
    return "jcodemunch" in servers


def _patch_mcp_config(path: Path, *, backup: bool = True, dry_run: bool = False) -> str:
    """Add jcodemunch entry to an MCP client JSON config.

    Returns a status message.
    """
    data = _read_json(path)
    if _has_jcodemunch_entry(data):
        return f"  already configured in {path}"

    if dry_run:
        return f"  would add jcodemunch to {path}"

    if "mcpServers" not in data:
        data["mcpServers"] = {}
    data["mcpServers"]["jcodemunch"] = _MCP_ENTRY
    _write_json(path, data, backup=backup)
    return f"  added jcodemunch to {path}"


# ---------------------------------------------------------------------------
# Codex CLI (~/.codex/config.toml)
# ---------------------------------------------------------------------------

def _codex_config_path() -> Path:
    """Return the Codex CLI MCP config path."""
    return Path.home() / ".codex" / "config.toml"


def _toml_string(value: str) -> str:
    """Render a path as a TOML string.

    Prefers a LITERAL string (single quotes), which performs no escape
    processing at all — the point being Windows paths, where a basic string
    would turn ``C:\\Users\\j`` into an invalid escape sequence and a parser
    would either reject the file or silently mangle the path. Falls back to a
    basic string only when the value contains a single quote, which a literal
    string cannot represent.
    """
    if "'" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _codex_command() -> Optional[str]:
    """Resolve the jcodemunch-mcp binary for a Codex server entry.

    ⚠⚠ Returns None rather than falling back to ``uvx``, and that refusal is
    the whole point of this function. Codex's rmcp transport is strict about
    the first JSON-RPC frame on stdout, and uvx's install chatter on a cold
    run poisons the handshake — the documented symptom is a SILENT multi-hour
    hang, not an error (see CLIENTS.md). Every other client in this module
    gets ``_MCP_ENTRY``'s ``uvx`` form; Codex must not, so a caller that
    cannot resolve a real binary has to say so instead of writing a config
    that appears to work.
    """
    return shutil.which("jcodemunch-mcp")


def _has_codex_entry(text: str) -> bool:
    """True when config.toml already declares the jcodemunch server."""
    return re.search(r"^\s*\[mcp_servers\.jcodemunch\]", text, re.MULTILINE) is not None


def _patch_codex_config(
    path: Path, *, backup: bool = True, dry_run: bool = False
) -> str:
    """Append an ``[mcp_servers.jcodemunch]`` block to Codex's config.toml.

    APPENDS rather than parse-and-rewrite. config.toml is a user-owned file
    holding unrelated Codex settings; round-tripping it through a serialiser
    would drop their comments and reorder their keys, and Python has no TOML
    *writer* in the stdlib at any version this package supports.
    """
    existing = ""
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError as exc:
            return f"  could not read {path}: {exc}"

    if _has_codex_entry(existing):
        return f"  already configured in {path}"

    exe = _codex_command()
    if not exe:
        return (
            "  skipped — Codex needs a resolved binary, not uvx "
            "(uvx's first-run output breaks its handshake). "
            "Run `uv tool install jcodemunch-mcp`, then re-run init."
        )

    if dry_run:
        return f"  would add [mcp_servers.jcodemunch] to {path} (command = {exe})"

    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(".toml.bak"))

    block = (
        "\n[mcp_servers.jcodemunch]\n"
        f"command = {_toml_string(exe)}\n"
    )
    # Keep exactly one blank line between our block and whatever precedes it.
    prefix = "" if (not existing or existing.endswith("\n")) else "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(existing + prefix + block, encoding="utf-8")
    return f"  added [mcp_servers.jcodemunch] to {path}"


# ---------------------------------------------------------------------------
# VS Code / GitHub Copilot (.vscode/mcp.json)
# ---------------------------------------------------------------------------

def _vscode_mcp_config_path() -> Path:
    """Return the workspace MCP config VS Code reads for Copilot.

    Workspace-scoped on purpose. VS Code's USER-level MCP file is reached
    through an editor command ("MCP: Open User Configuration") rather than a
    documented path, and it moves with the active profile, so an installer
    that guessed at it would write somewhere the editor may never read.
    `.vscode/mcp.json` is the documented, stable target.
    """
    return Path.cwd() / ".vscode" / "mcp.json"


def _patch_vscode_mcp_config(
    path: Path, *, backup: bool = True, dry_run: bool = False
) -> str:
    """Add jcodemunch to VS Code's MCP config for Copilot.

    ⚠ The top-level key is `servers`, NOT `mcpServers`. That is the third
    distinct schema in this module (generic `mcpServers`, opencode's `mcp`,
    and this), and like opencode's it fails silently: VS Code reads a file
    whose servers live under the wrong key, finds none, and reports nothing.
    Per-server fields are `command`/`args` as usual -- `type` defaults to
    "stdio" for a local server, so it is left off rather than asserted.
    """
    data = _read_json(path)
    servers = data.get("servers")
    if isinstance(servers, dict) and "jcodemunch" in servers:
        return f"  already configured in {path}"

    if dry_run:
        return f"  would add jcodemunch to {path}"

    if not isinstance(servers, dict):
        servers = {}
    servers["jcodemunch"] = dict(_MCP_ENTRY)
    data["servers"] = servers
    _write_json(path, data, backup=backup)
    return f"  added jcodemunch to {path}"


# ---------------------------------------------------------------------------
# opencode (~/.config/opencode/opencode.json)
# ---------------------------------------------------------------------------

def _opencode_config_path() -> Path:
    """Return the opencode global config path.

    opencode documents a hardcoded ``~/.config/opencode/`` for the global
    config on every platform and does not document XDG_CONFIG_HOME support,
    so this deliberately does NOT consult that variable — following a spec the
    tool does not implement would write a file it never reads.
    """
    return Path.home() / ".config" / "opencode" / "opencode.json"


def _patch_opencode_config(
    path: Path, *, backup: bool = True, dry_run: bool = False
) -> str:
    """Add jcodemunch to opencode's config.

    ⚠ opencode's schema is NOT the `mcpServers` shape every other JSON client
    in this module uses. The top-level key is `mcp`, each server needs an
    explicit `"type": "local"`, and `command` is a single ARRAY carrying the
    executable and its arguments rather than separate `command`/`args` keys.
    Writing `_MCP_ENTRY` here produces a file opencode parses and ignores.
    """
    data = _read_json(path)
    servers = data.get("mcp")
    if isinstance(servers, dict) and "jcodemunch" in servers:
        return f"  already configured in {path}"

    if dry_run:
        return f"  would add jcodemunch to {path}"

    if not isinstance(servers, dict):
        servers = {}
    servers["jcodemunch"] = {
        "type": "local",
        "command": ["uvx", "jcodemunch-mcp"],
        "enabled": True,
    }
    data["mcp"] = servers
    _write_json(path, data, backup=backup)
    return f"  added jcodemunch to {path}"


def _claude_cli_exe() -> Optional[str]:
    """Resolve the `claude` executable, or None if unavailable.

    On Windows the npm wrapper is `claude.CMD` / `claude.ps1` with no bare
    `.exe`, so a plain ``["claude", ...]`` subprocess call raises
    FileNotFoundError. ``shutil.which`` honors PATHEXT and finds the shim, so
    every Claude Code CLI integration (status detection, add, remove) must go
    through it rather than invoking ``"claude"`` directly.
    """
    return shutil.which("claude")


def _configure_claude_code(*, dry_run: bool = False) -> str:
    """Run `claude mcp add` for Claude Code CLI."""
    if dry_run:
        return "  would run: claude mcp add jcodemunch uvx jcodemunch-mcp"
    claude = _claude_cli_exe()
    if not claude:
        return "  claude CLI not found — skipped"
    try:
        result = subprocess.run(
            [claude, "mcp", "add", "jcodemunch", "uvx", "jcodemunch-mcp"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if result.returncode == 0:
            return "  ran: claude mcp add jcodemunch uvx jcodemunch-mcp"
        # Already exists or other non-fatal issue
        stderr = result.stderr.strip()
        if "already exists" in stderr.lower():
            return "  already configured in Claude Code"
        return f"  claude mcp add failed: {stderr or result.stdout.strip()}"
    except FileNotFoundError:
        return "  claude CLI not found — skipped"
    except subprocess.TimeoutExpired:
        return "  claude mcp add timed out"


def configure_client(client: MCPClient, *, backup: bool = True, dry_run: bool = False) -> str:
    """Configure a single MCP client. Returns a status message."""
    if client.method == "cli":
        return _configure_claude_code(dry_run=dry_run)
    elif client.method == "json_patch" and client.config_path:
        return _patch_mcp_config(client.config_path, backup=backup, dry_run=dry_run)
    elif client.method == "toml_codex" and client.config_path:
        return _patch_codex_config(client.config_path, backup=backup, dry_run=dry_run)
    elif client.method == "json_opencode" and client.config_path:
        return _patch_opencode_config(client.config_path, backup=backup, dry_run=dry_run)
    elif client.method == "json_vscode" and client.config_path:
        return _patch_vscode_mcp_config(client.config_path, backup=backup, dry_run=dry_run)
    return f"  unknown method for {client.name}"


# ---------------------------------------------------------------------------
# CLAUDE.md injection
# ---------------------------------------------------------------------------

def _claude_md_path(scope: str) -> Path:
    """Return the CLAUDE.md path for the given scope."""
    if scope == "global":
        return Path.home() / ".claude" / "CLAUDE.md"
    return Path.cwd() / "CLAUDE.md"


def _has_policy(path: Path) -> bool:
    """Check if the Code Exploration Policy marker already exists."""
    if not path.exists():
        return False
    return _CLAUDE_MD_MARKER in path.read_text(encoding="utf-8")


def with_scoped_config(fn):
    """Run *fn* with the config loaded, and leave the process as it was found.

    A decorator rather than a `with` inside the body so the restore covers every
    exit path, including the early returns and the error paths.
    """
    @functools.wraps(fn)
    def _wrapped(*args, **kwargs):
        with config_loaded_for_init():
            return fn(*args, **kwargs)
    return _wrapped


@contextlib.contextmanager
def config_loaded_for_init():
    """Load the config for the duration of a block, then put it back.

    ``load_config`` replaces process-global state. ``init`` is normally a
    one-shot CLI where that is harmless, but it is also an importable function,
    and swapping a host process's configuration as a side effect of writing a
    policy file is not something a caller can be expected to anticipate.

    ⚠ It is not hypothetical: adding the load made ``test_json`` fail whenever
    an ``init`` test ran first in the same process, because the config `init`
    materialised outlived the call. A test-ordering failure here is the cheap
    version of the same bug a library caller would hit silently.
    """
    from .. import config as _cfg

    saved = dict(_cfg._GLOBAL_CONFIG)
    saved_projects = dict(_cfg._PROJECT_CONFIGS)
    try:
        ensure_config_loaded()
        yield
    finally:
        _cfg._GLOBAL_CONFIG.clear()
        _cfg._GLOBAL_CONFIG.update(saved)
        _cfg._PROJECT_CONFIGS.clear()
        _cfg._PROJECT_CONFIGS.update(saved_projects)


def ensure_config_loaded() -> None:
    """Materialise and load the global config before generating any guidance.

    ``init`` used to read config values without ever calling ``load_config``, so
    every lookup returned a built-in default rather than the user's file. Two
    consequences, both reported in #397:

    * A user with ``tool_profile: "core"`` still got the full-profile policy,
      because the profile read never saw their config.
    * On a first-ever install there is no config yet at all. The server creates
      it on first start and a genuinely-new install gets ``tool_surface:
      "counter"``, so ``init`` was writing guidance for a decision that had not
      been made and would not go its way.

    Loading here makes ``init`` the point where that decision is taken and
    observed, so the config on disk and the policy written beside it come from
    the same value instead of from two code paths that never met.
    """
    try:
        from ..config import load_config
        load_config(os.environ.get("CODE_INDEX_PATH"))
    except Exception:
        logger.debug("could not load config before generating policy", exc_info=True)


def _effective_tool_surface() -> str:
    """The tool surface this install will actually serve ("full" or "counter")."""
    try:
        from ..config import get as cfg_get
        env = os.environ.get("JCODEMUNCH_TOOL_SURFACE")
        return (env or cfg_get("tool_surface", "full") or "full").strip().lower()
    except Exception:
        return "full"


def _get_active_tools() -> set[str] | None:
    """Return the set of tool names active under current config.

    Applies tool_surface, tool_profile and disabled_tools filtering.
    Returns ``None`` when the profile is "full" and nothing is disabled
    (i.e. no filtering needed).

    ⚠ Surface is checked FIRST and is not a filter over the tier: under
    ``counter`` the server advertises only the front door, whatever the profile
    says, so a policy naming direct tools describes calls the client cannot
    offer the model. The tools remain callable by name, which is exactly why
    this went unnoticed -- nothing errors, the guidance is simply unreachable
    through the tool list.
    """
    try:
        from ..config import get as cfg_get
        from ..server import _PROFILE_TIERS, _CANONICAL_TOOL_NAMES
    except Exception:
        return None

    if _effective_tool_surface() == "counter":
        return set(_front_door_tool_names())

    profile = cfg_get("tool_profile", "full")
    tier = _PROFILE_TIERS.get(profile)
    disabled = set(cfg_get("disabled_tools", []))

    if tier is None and not disabled:
        return None  # full profile, nothing disabled

    active = set(_CANONICAL_TOOL_NAMES) if tier is None else set(tier)
    active -= disabled
    return active


def _front_door_tool_names() -> set[str]:
    """Tool names the server advertises under ``tool_surface="counter"``.

    The front door itself is only three tools, but the surface it produces is
    six: ``_ALWAYS_PRESENT_TOOLS`` survives every filter, and the policy
    legitimately uses two of them (``announce_model`` to size answers,
    ``jcodemunch_guide`` to discover the catalogue). Reading both from the
    server keeps this from drifting the moment either list changes.
    """
    names: set[str] = set()
    try:
        from ..server import _ALWAYS_PRESENT_TOOLS, _counter_front_door_tools
        names = {t.name for t in _counter_front_door_tools()}
        names |= set(_ALWAYS_PRESENT_TOOLS)
    except Exception:
        logger.debug("could not read the front-door tool list", exc_info=True)
    return names or {"order", "menu", "route", "jcodemunch_guide",
                     "announce_model", "set_tool_tier"}


# Regex matching tool names in backtick contexts:
#  - `tool_name` (exact)
#  - `tool_name { ... }` (tool with inline args)
#  - `tool_name(...)` (tool with call syntax)
_TOOL_REF_RE = re.compile(r"`([a-z][a-z0-9_]*)[`(\s{]")


def active_policy() -> str:
    """The agent policy matching the surface this install actually serves.

    Every writer goes through here so the choice cannot be made two ways. Under
    the front door the direct-tool policy is not merely over-long, it names
    calls the client will not offer the model, so the counter policy replaces it
    outright rather than being filtered down to the three surviving names --
    filtering a workflow away leaves an agent with no workflow at all.
    """
    if _effective_tool_surface() == "counter":
        return _CLAUDE_MD_POLICY_COUNTER
    return _filter_policy_for_tools(_CLAUDE_MD_POLICY, _get_active_tools())


def _filter_policy_for_tools(policy: str, active_tools: set[str] | None) -> str:
    """Filter the CLAUDE.md policy to only reference available tools.

    Lines containing backtick-quoted tool names that are NOT in
    *active_tools* are removed.  Sections left empty after filtering
    are also removed.  Returns the policy unchanged when *active_tools*
    is ``None`` (full profile, nothing disabled).
    """
    if active_tools is None:
        return policy

    # Build the set of all known tool names for reference-detection.
    try:
        from ..server import _CANONICAL_TOOL_NAMES
        all_tools = set(_CANONICAL_TOOL_NAMES)
    except Exception:
        return policy

    lines = policy.splitlines(keepends=True)
    kept: list[str] = []

    for line in lines:
        refs = _TOOL_REF_RE.findall(line)
        # Only consider refs that are actual tool names
        tool_refs = [r for r in refs if r in all_tools]
        if tool_refs and any(t not in active_tools for t in tool_refs):
            continue  # drop line — references unavailable tool(s)
        kept.append(line)

    # Remove bold-label headers (e.g. "**Finding code:**") that lost all
    # their child bullets.  A bold-label is "empty" if the next non-blank
    # line is another bold-label, a ## heading, or EOF.
    # We do NOT prune ## headings here — they may legitimately sit above
    # bold-label sub-sections that survived filtering.
    result: list[str] = []
    i = 0
    while i < len(kept):
        line = kept[i]
        stripped = line.strip()

        is_bold_label = (
            stripped.startswith("**")
            and stripped.endswith(":**")
            and not stripped.startswith("## ")
        )

        if is_bold_label:
            j = i + 1
            while j < len(kept) and not kept[j].strip():
                j += 1
            if j >= len(kept):
                break  # trailing empty label — drop
            next_s = kept[j].strip()
            next_is_boundary = (
                (next_s.startswith("**") and next_s.endswith(":**"))
                or next_s.startswith("## ")
            )
            if next_is_boundary:
                i = j  # skip empty bold-label section
                continue

        result.append(line)
        i += 1

    return "".join(result)


def install_claude_md(scope: str = "global", *, dry_run: bool = False, backup: bool = True) -> str:
    """Append the Code Exploration Policy to CLAUDE.md.

    scope: "global" or "project"
    Returns a status message.
    Respects ``tool_profile`` and ``disabled_tools`` from config.
    """
    path = _claude_md_path(scope)
    if _has_policy(path):
        return f"  policy already present in {path}"
    if dry_run:
        return f"  would append policy to {path}"

    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(".md.bak"))

    policy = active_policy()
    with open(path, "a", encoding="utf-8") as f:
        if path.exists() and path.stat().st_size > 0:
            f.write("\n\n")
        f.write(policy)

    return f"  appended policy to {path}"


# ---------------------------------------------------------------------------
# Cursor rules injection
# ---------------------------------------------------------------------------

def _cursor_rules_path() -> Path:
    """Return the project-level Cursor rules path for jcodemunch."""
    return Path.cwd() / ".cursor" / "rules" / "jcodemunch.mdc"


def install_cursor_rules(*, dry_run: bool = False, backup: bool = True) -> str:
    """Write .cursor/rules/jcodemunch.mdc in the current project.

    Returns a status message.
    """
    path = _cursor_rules_path()
    if path.exists() and _CLAUDE_MD_MARKER in path.read_text(encoding="utf-8"):
        return f"  already present in {path}"
    if dry_run:
        return f"  would write {path}"

    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(".mdc.bak"))

    # Cursor gets the same surface-matched policy as everyone else; only the MDC
    # frontmatter differs. Reading the policy from `active_policy` rather than
    # re-deriving it here is what stops this writer drifting from the others.
    policy = active_policy()
    content = _CURSOR_RULES_CONTENT
    if policy != _CLAUDE_MD_POLICY:
        content = (
            "---\n"
            "description: Use jCodemunch MCP tools for all code navigation instead of built-in search\n"
            "alwaysApply: true\n"
            "---\n\n"
        ) + policy
    path.write_text(content, encoding="utf-8")
    return f"  wrote {path}"


# ---------------------------------------------------------------------------
# Windsurf rules injection
# ---------------------------------------------------------------------------

def _windsurf_rules_path() -> Path:
    """Return the project-level .windsurfrules path."""
    return Path.cwd() / ".windsurfrules"


def install_windsurf_rules(*, dry_run: bool = False, backup: bool = True) -> str:
    """Append the Code Exploration Policy to .windsurfrules.

    Returns a status message.
    """
    path = _windsurf_rules_path()
    if path.exists() and _CLAUDE_MD_MARKER in path.read_text(encoding="utf-8"):
        return f"  already present in {path}"
    if dry_run:
        return f"  would append policy to {path}"

    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(".windsurfrules.bak"))

    policy = active_policy()
    with open(path, "a", encoding="utf-8") as f:
        if path.exists() and path.stat().st_size > 0:
            f.write("\n\n")
        f.write(policy)

    return f"  appended policy to {path}"


# ---------------------------------------------------------------------------
# AGENTS.md (OpenCode, Codex, etc.)
# ---------------------------------------------------------------------------

def install_agents_md(*, dry_run: bool = False, backup: bool = True) -> str:
    """Write ./AGENTS.md with the plan_turn(model=...) directive.

    OpenCode, Codex, and several other agent runners read AGENTS.md as
    their per-project system-prompt augmentation. Mirrors CLAUDE.md
    policy so agents swapped via those runners observe the same
    tier-switching convention.
    """
    target = Path.cwd() / "AGENTS.md"
    policy = active_policy()
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if _CLAUDE_MD_MARKER in existing:
            return f"  already present in {target}"
        if dry_run:
            return f"  would append policy to {target}"
        if backup:
            shutil.copy2(target, target.with_suffix(".md.bak"))
        target.write_text(existing.rstrip() + "\n\n" + policy + "\n", encoding="utf-8")
    else:
        if dry_run:
            return f"  would create {target}"
        target.write_text(policy + "\n", encoding="utf-8")

    return f"  wrote {target}"


# ---------------------------------------------------------------------------
# Hooks injection
# ---------------------------------------------------------------------------

def _settings_json_path() -> Path:
    """Return the Claude Code settings.json path."""
    if platform.system() == "Windows":
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude" / "settings.json"
    return Path.home() / ".claude" / "settings.json"


_JCM_SUBCOMMAND_RE = re.compile(
    r'jcodemunch[-_]mcp(?:\.[Ee][Xx][Ee])?["\']?\s+(\S+(?:\s+\S+)?)',
)


def _extract_jcm_subcommand(cmd: str) -> Optional[str]:
    """Return the jcm subcommand (e.g. ``hook-pretooluse``) embedded in a hook
    command string, or None when cmd doesn't invoke jcodemunch-mcp.

    Survives all the path-shape variations we've seen in the wild:

      * Bare name: ``jcodemunch-mcp hook-pretooluse``
      * Absolute (POSIX-slash): ``C:/Python314/Scripts/jcodemunch-mcp.EXE hook-pretooluse``
      * Absolute (back-slash, post-JSON): ``C:\\Python314\\Scripts\\jcodemunch-mcp.EXE hook-pretooluse``
      * Quoted (path with spaces): ``"C:/Program Files/jcodemunch-mcp" hook-pretooluse``

    Returns up to two whitespace-separated tokens so multi-arg subcommands
    like ``hook-event create`` round-trip cleanly.
    """
    if not cmd:
        return None
    m = _JCM_SUBCOMMAND_RE.search(cmd)
    return m.group(1).strip().strip('"').strip("'") if m else None


def _merge_hooks(
    data: dict[str, Any],
    hook_defs: dict[str, list],
    marker: str,
) -> list[str]:
    """Merge hook definitions into settings data, returning names of added events.

    Duplicate detection is path-shape-agnostic: two commands that invoke
    the same jcm subcommand (e.g. ``hook-pretooluse``) are considered the
    same hook, whether one is written as the bare ``jcodemunch-mcp`` and
    the other as a fully-resolved absolute path. This prevents the
    accumulation of duplicate entries each time ``shutil.which`` resolves
    to a different shape (bare → absolute → forward-slashed absolute).

    ``marker`` is kept for backwards compatibility but only used as a
    legacy substring fallback when subcommand extraction fails.
    """
    hooks = data.setdefault("hooks", {})
    added: list[str] = []

    for event_name, event_hooks in hook_defs.items():
        existing_cmds: list[str] = []
        existing_subcommands: set[str] = set()
        if event_name in hooks:
            for rule in hooks[event_name]:
                for h in rule.get("hooks", []):
                    cmd = h.get("command", "") or ""
                    existing_cmds.append(cmd)
                    sub = _extract_jcm_subcommand(cmd)
                    if sub:
                        existing_subcommands.add(sub)

        new_rules = []
        for rule in event_hooks:
            rule_cmds = [h.get("command", "") for h in rule.get("hooks", [])]
            rule_subcommands = {
                s for s in (_extract_jcm_subcommand(c) for c in rule_cmds) if s
            }
            # Primary check: any jcm subcommand already installed for this event?
            if rule_subcommands and rule_subcommands & existing_subcommands:
                continue
            # Exact-match check (covers non-jcm hooks like sync_memory.py).
            if any(cmd in existing_cmds for cmd in rule_cmds if cmd):
                continue
            # Legacy substring marker fallback.
            if marker and any(marker in cmd for cmd in existing_cmds):
                if any(marker in cmd for cmd in rule_cmds):
                    continue
            new_rules.append(rule)

        if new_rules:
            if event_name in hooks:
                hooks[event_name].extend(new_rules)
            else:
                hooks[event_name] = new_rules
            added.append(event_name)

    return added


def install_hooks(*, dry_run: bool = False, backup: bool = True) -> str:
    """Merge worktree and tool hooks into ~/.claude/settings.json.

    Returns a status message.
    """
    path = _settings_json_path()
    data = _read_json(path)
    added = _merge_hooks(data, _worktree_hooks(), "jcodemunch-mcp hook-event")

    if not added:
        return f"  hooks already present in {path}"
    if dry_run:
        return f"  would add {', '.join(added)} hooks to {path}"

    _write_json(path, data, backup=backup)
    return f"  added {', '.join(added)} hooks to {path}"


def _install_version_path() -> Path:
    """Path to the file recording the jcodemunch-mcp version that last ran ``init``."""
    base = Path(os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index")))
    return base / "last_init_version.txt"


def _stamp_install_version() -> None:
    """Record the currently-installed jcodemunch-mcp version.

    Used by the server-startup version probe to detect when the package
    has been upgraded but ``init`` has not been re-run (so hooks/config
    may be stale).
    """
    from .. import __version__
    path = _install_version_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(__version__.strip(), encoding="utf-8")


def read_install_version() -> Optional[str]:
    """Read the version recorded by the last ``init`` run, if any."""
    path = _install_version_path()
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def install_copilot_hooks(*, dry_run: bool = False, backup: bool = True) -> str:
    """Write a ``.github/hooks/hooks.json`` for GitHub Copilot CLI / cloud agent.

    Generates a postToolUse hook that invokes
    ``jcodemunch-mcp hook-copilot-posttooluse`` so that file edits made
    by Copilot trigger an automatic re-index, parallel to the Claude
    Code PostToolUse handling.

    The file is written at ``<cwd>/.github/hooks/hooks.json``. If a
    hooks.json already exists, the postToolUse rule is appended only if
    no rule with the same command is present (idempotent).
    """
    cwd = Path.cwd()
    hooks_dir = cwd / ".github" / "hooks"
    hooks_path = hooks_dir / "hooks.json"

    rule = {
        "type": "command",
        "bash": "jcodemunch-mcp hook-copilot-posttooluse",
        "powershell": "jcodemunch-mcp hook-copilot-posttooluse",
        "timeoutSec": 30,
        "comment": "jcodemunch-mcp: auto-reindex edited files",
    }

    if hooks_path.exists():
        try:
            data = json.loads(hooks_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            return f"  failed to parse existing {hooks_path}; skipping"
        hooks = data.setdefault("hooks", {})
        existing = hooks.setdefault("postToolUse", [])
        for r in existing:
            if r.get("bash", "").startswith("jcodemunch-mcp hook-copilot"):
                return f"  Copilot hooks already present in {hooks_path}"
        if dry_run:
            return f"  would append jcodemunch postToolUse hook to {hooks_path}"
        existing.append(rule)
        data.setdefault("version", 1)
        if backup:
            hooks_path.with_suffix(".json.bak").write_text(
                hooks_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
        hooks_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return f"  appended Copilot postToolUse hook to {hooks_path}"

    if dry_run:
        return f"  would create {hooks_path} with jcodemunch postToolUse hook"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "hooks": {"postToolUse": [rule]}}
    hooks_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return f"  wrote {hooks_path} with jcodemunch postToolUse hook"


def install_enforcement_hooks(
    *, dry_run: bool = False, backup: bool = True, strict: bool = False,
) -> str:
    """Merge PreToolUse/PostToolUse enforcement hooks into ~/.claude/settings.json.

    PreToolUse (Read|Grep) — steer Claude toward jCodemunch for code files and
        searches inside an indexed repo.
    PostToolUse (Edit|Write) — auto-reindex modified files.

    When ``strict`` is True, also persist ``env.JCODEMUNCH_ENFORCE = "strict"``
    so the PreToolUse hook **denies** (rather than warns on) a native Read/Grep
    that an indexed-repo jcm route can serve. Strict is reversible: re-running
    without ``strict`` resets the flag to ``"advisory"`` (the key is only added,
    never invented, for non-strict installs).

    Returns a status message.
    """
    path = _settings_json_path()
    data = _read_json(path)
    # Marker is only the legacy substring fallback; primary duplicate detection is
    # per-subcommand via _extract_jcm_subcommand, so hooks outside the "hook-p"
    # prefix (hook-sessionstart, hook-taskcomplete, hook-subagent-start) merge
    # correctly and are added to an existing install on re-run.
    added = _merge_hooks(data, _enforcement_hooks(), "jcodemunch-mcp hook-p")  # matches hook-pretooluse & hook-posttooluse & hook-precompact

    # Persist (or revert) the strict-enforce env flag the hook reads at runtime.
    env_changed = False
    desired = ""
    if strict:
        env = data.setdefault("env", {})
        if env.get("JCODEMUNCH_ENFORCE") != "strict":
            env["JCODEMUNCH_ENFORCE"] = "strict"
            env_changed, desired = True, "strict"
    else:
        env = data.get("env")
        if isinstance(env, dict) and env.get("JCODEMUNCH_ENFORCE") not in (None, "advisory"):
            env["JCODEMUNCH_ENFORCE"] = "advisory"  # revert a prior --strict
            env_changed, desired = True, "advisory"

    if not added and not env_changed:
        return f"  enforcement hooks already present in {path}"

    def _bits(verb_add: str, verb_env: str) -> str:
        parts = []
        if added:
            parts.append(f"{verb_add} {', '.join(added)} enforcement hooks")
        if env_changed:
            tier = "strict deny" if desired == "strict" else "advisory warn"
            parts.append(f"{verb_env} JCODEMUNCH_ENFORCE={desired} ({tier})")
        return ", ".join(parts)

    if dry_run:
        return f"  would {_bits('add', 'set')} in {path}"

    _write_json(path, data, backup=backup)
    return f"  {_bits('added', 'set')} in {path}"


# ---------------------------------------------------------------------------
# Index current directory
# ---------------------------------------------------------------------------

def run_index(*, dry_run: bool = False) -> str:
    """Index the current working directory using index_folder."""
    cwd = os.getcwd()
    if dry_run:
        return f"  would index {cwd}"

    try:
        from ..tools.index_folder import index_folder
        result = index_folder(path=cwd)
        files = result.get("files_indexed", "?")
        symbols = result.get("symbols_indexed", "?")
        return f"  indexed {cwd} ({files} files, {symbols} symbols)"
    except Exception as e:
        return f"  indexing failed: {e}"


# ---------------------------------------------------------------------------
# Audit agent config
# ---------------------------------------------------------------------------

def run_audit(*, project_path: Optional[str] = None, dry_run: bool = False) -> list[str]:
    """Run audit_agent_config and return formatted output lines."""
    if dry_run:
        return ["  would audit agent config files for token waste"]

    try:
        from ..tools.audit_agent_config import audit_agent_config
        result = audit_agent_config(project_path=project_path or os.getcwd())
    except Exception as e:
        return [f"  audit failed: {e}"]

    lines: list[str] = []
    total = result.get("total_tokens", 0)
    scanned = result.get("files_scanned", 0)

    if scanned == 0:
        lines.append("  no agent config files found")
        return lines

    lines.append(f"  scanned {scanned} file(s), {total:,} tokens total per turn")

    # Token breakdown (compact)
    for entry in result.get("token_breakdown", []):
        scope_tag = " (global)" if entry["scope"] == "global" else ""
        lines.append(f"    {entry['tokens']:>5,} tokens  {entry['description']}{scope_tag}")

    # Findings
    findings = result.get("findings", [])
    if findings:
        lines.append(f"  {len(findings)} finding(s):")
        for f in findings[:10]:  # Cap display at 10
            icon = "!" if f["severity"] == "warning" else "-"
            loc = f" (line {f['line']})" if f.get("line") else ""
            lines.append(f"    {icon} [{f['category']}]{loc} {f['message']}")
        if len(findings) > 10:
            lines.append(f"    ... and {len(findings) - 10} more")
    else:
        lines.append("  no issues found")

    return lines


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------

def _prompt_yn(message: str, default: bool = True) -> bool:
    """Prompt for yes/no, with a default."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    try:
        answer = input(message + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not answer:
        return default
    return answer in ("y", "yes")


def _prompt_choice(message: str, options: list[str], allow_all: bool = True) -> list[str]:
    """Prompt user to pick from numbered options. Returns selected option labels."""
    for i, opt in enumerate(options, 1):
        print(f"  [{i}] {opt}")
    extra = "/all/none" if allow_all else "/none"
    try:
        raw = input(f"{message} [1-{len(options)}{extra}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return []
    if raw == "none" or raw == "":
        return []
    if raw == "all":
        return options
    selected = []
    for part in raw.replace(",", " ").split():
        try:
            idx = int(part) - 1
            if 0 <= idx < len(options):
                selected.append(options[idx])
        except ValueError:
            continue
    return selected


def _prompt_scope(message: str) -> Optional[str]:
    """Prompt for global/project/skip."""
    try:
        raw = input(f"{message} [global/project/skip]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if raw in ("global", "g"):
        return "global"
    if raw in ("project", "p"):
        return "project"
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

@with_scoped_config
def run_init(
    *,
    clients: Optional[list[str]] = None,
    claude_md: Optional[str] = None,
    hooks: Optional[bool] = None,
    # True only when a HUMAN typed `--hooks`. `install <agent>` passes
    # ``hooks=True`` as its own default and deliberately leaves this False, so
    # `--minimal` keeps suppressing that while honouring a typed flag (#397).
    # The signal has to come from the caller: by the time both arrive as
    # ``hooks=True`` the two cases are indistinguishable here.
    hooks_explicit: bool = False,
    copilot_hooks: bool = False,
    index: bool = False,
    audit: bool = False,
    dry_run: bool = False,
    demo: bool = False,
    yes: bool = False,
    no_backup: bool = False,
    skills: bool = False,
    skills_scope: str = "global",
    share_savings: Optional[str] = None,
    minimal: bool = False,
    strict: bool = False,
) -> int:
    """Run the init flow. Returns exit code (0 = success).

    ``share_savings`` accepts ``"on"`` or ``"off"`` (or ``None`` to leave unchanged).
    When set, writes the explicit value into ``~/.code-index/config.jsonc`` before
    any other init step runs, so the user's preference survives even if the rest
    of init is aborted partway through.

    ``minimal`` (P1.7): when True, writes only the MCP server registration for the
    targeted clients and skips every other channel — no CLAUDE.md policy paste,
    no Cursor / Windsurf rules, no AGENTS.md, no enforcement hooks, no
    .github/hooks. Equivalent to the user answering "skip / no" to every prompt
    after MCP client selection. Recommended for hardened install templates that
    don't want jcodemunch touching agent-policy files outside their existing
    source-controlled posture.
    """
    if demo:
        dry_run = True  # demo never writes anything
    backup = not no_backup
    interactive = not yes and sys.stdin.isatty()
    hooks = bool(hooks)
    # P1.7: --minimal forces all channels beyond MCP server registration to
    # OFF and turns off interactive prompts. Combined with --yes (which is
    # how `install <agent>` calls into run_init) this becomes a clean
    # "register MCP server, do nothing else" path. Suitable for hardened
    # install templates that don't want jcodemunch touching agent-policy
    # files outside their existing source-controlled posture.
    if minimal:
        interactive = False
        claude_md = "skip"
        copilot_hooks = False
        index = False
        audit = False
        skills = False
        # `--minimal` suppresses DEFAULTS, not a choice the user typed. The two
        # are distinguishable only because `--hooks` now parses with
        # ``default=None``: `None` means "not specified", `True` means the user
        # asked for it on the command line.
        #
        # ⚠ Both directions are load-bearing and they pull opposite ways.
        # `install <agent> --minimal` passes ``hooks=True`` as ITS OWN default
        # and must still install nothing (asserted by
        # test_init_minimal.py::test_minimal_under_yes_does_not_default_anything_on).
        # `init --minimal --hooks` is a user asking for hooks without the policy
        # paste and must get them (#397). A plain bool cannot tell those apart,
        # which is why a tri-state and not a reordering is the fix.
        if not hooks_explicit:
            hooks = False

    # The config is already loaded by @with_scoped_config, before anything is
    # generated, so the surface and the policy come from one value rather than
    # from `init` guessing and the server deciding later (#397).

    if demo:
        print("\njCodeMunch init -- DEMO MODE (no changes will be made)\n")
    else:
        print("\njCodeMunch init -- one-command setup\n")

    # Collects (action_label, benefit) for the demo summary
    _demo_actions: list[tuple[str, str]] = []

    # ----- Step 0: explicit share_savings opt-in / opt-out -----
    # Applied before MCP-client registration so the user's preference is durable
    # even if a later step is interrupted. Survives package upgrades because
    # config --upgrade preserves user-set values.
    if share_savings is not None:
        normalized = str(share_savings).strip().lower()
        if normalized in ("on", "true", "1", "yes"):
            _ss_value = True
        elif normalized in ("off", "false", "0", "no"):
            _ss_value = False
        else:
            print(f"  share_savings:  invalid value '{share_savings}' (expected on|off); skipped")
            _ss_value = None
        if _ss_value is not None:
            if dry_run:
                print(f"  share_savings:  would write {_ss_value} to ~/.code-index/config.jsonc")
                if demo:
                    _demo_actions.append((
                        f"Write share_savings={_ss_value} to ~/.code-index/config.jsonc",
                        "Locks the telemetry-counter setting at install time; survives package upgrades because config --upgrade preserves user-set values",
                    ))
            else:
                from .. import config as _cfg
                ss_path = _cfg.apply_share_savings(_ss_value)
                print(f"  share_savings:  wrote {_ss_value} to {ss_path}")

    # ----- Step 1: MCP client registration -----
    detected = _detect_clients()

    if clients is not None:
        # Explicit --client flag
        if "auto" in clients:
            targets = detected
        elif "none" in clients:
            targets = []
        else:
            name_map = {c.name.lower().replace(" ", "-"): c for c in detected}
            targets = [name_map[n] for n in clients if n in name_map]
    elif interactive and detected:
        print("Detected MCP clients:")
        names = [repr(c) for c in detected]
        selected = _prompt_choice("Configure which?", names)
        targets = [c for c in detected if repr(c) in selected]
    elif detected:
        targets = detected  # non-interactive + no flag = configure all
    else:
        targets = []
        print("No MCP clients detected.\n")

    for client in targets:
        msg = configure_client(client, backup=backup, dry_run=dry_run)
        print(f"  {client.name}:{msg}")
        if demo and "would" in msg:
            loc = str(client.config_path) if client.config_path else "via CLI"
            _demo_actions.append((
                f"Register jcodemunch with {client.name} ({loc})",
                "Your AI assistant could immediately call all jCodemunch tools without any manual setup or restart",
            ))

    # ----- Step 2: Agent policies -----
    selected_names = {c.name for c in targets}

    # 2a: CLAUDE.md (Claude Code / Claude Desktop)
    md_scope = claude_md
    if md_scope is None and interactive:
        print()
        md_scope = _prompt_scope("Install CLAUDE.md policy?")
    elif md_scope is None and yes:
        md_scope = "global"  # default for --yes mode

    if md_scope in ("global", "project"):
        msg = install_claude_md(md_scope, dry_run=dry_run, backup=backup)
        print(f"  CLAUDE.md:{msg}")
        if demo and "would" in msg:
            where = "globally (all projects)" if md_scope == "global" else "in this project only"
            _demo_actions.append((
                f"Inject Code Exploration Policy into CLAUDE.md {where}",
                "Every future Claude session would automatically navigate code via jCodemunch — no slow, token-heavy file reads",
            ))

    # 2b: Cursor rules (.cursor/rules/jcodemunch.mdc)
    if "Cursor" in selected_names and not minimal:
        do_cursor_rules = yes or not interactive
        if interactive:
            print()
            do_cursor_rules = _prompt_yn(
                "Install Cursor rules (.cursor/rules/jcodemunch.mdc)?",
            )
        if do_cursor_rules:
            msg = install_cursor_rules(dry_run=dry_run, backup=backup)
            print(f"  Cursor rules:{msg}")
            if demo and "would" in msg:
                _demo_actions.append((
                    "Write .cursor/rules/jcodemunch.mdc (alwaysApply: true)",
                    "Cursor and its subagents would prefer jCodemunch tools over built-in search on every turn — no more unreliable fallbacks",
                ))

    # 2c: Windsurf rules (.windsurfrules)
    if "Windsurf" in selected_names and not minimal:
        do_windsurf_rules = yes or not interactive
        if interactive:
            print()
            do_windsurf_rules = _prompt_yn(
                "Install Windsurf rules (.windsurfrules)?",
            )
        if do_windsurf_rules:
            msg = install_windsurf_rules(dry_run=dry_run, backup=backup)
            print(f"  Windsurf rules:{msg}")
            if demo and "would" in msg:
                _demo_actions.append((
                    "Append Code Exploration Policy to .windsurfrules",
                    "Windsurf Cascade would prefer jCodemunch tools over built-in search on every turn",
                ))

    # 2d: AGENTS.md (OpenCode, Codex, etc.)
    do_agents_md = (yes or not interactive) and not minimal
    if interactive:
        print()
        do_agents_md = _prompt_yn(
            "Install AGENTS.md (OpenCode/Codex policy)?",
        )
    if do_agents_md:
        msg = install_agents_md(dry_run=dry_run, backup=backup)
        print(f"  AGENTS.md:{msg}")
        if demo and "would" in msg:
            _demo_actions.append((
                "Create AGENTS.md with Code Exploration Policy",
                "OpenCode, Codex, and other AGENTS.md-reading agents would prefer jCodemunch tools over built-in search",
            ))

    # ----- Step 2e: Claude Agent Skill bundle (opt-in via --skills) -----
    if skills:
        from .skills import install_claude_skill
        msg = install_claude_skill(
            scope=skills_scope, dry_run=dry_run, backup=backup,
        )
        print(f"  Claude Skill ({skills_scope}):{msg}")
        if demo and "would" in msg:
            where = "globally" if skills_scope == "global" else "in this project"
            _demo_actions.append((
                f"Write .claude/skills/jcodemunch/SKILL.md {where}",
                "Claude loads the skill on demand for code-navigation tasks instead of carrying the policy block in baseline context every turn",
            ))

    # ----- Step 3: Agent hooks -----
    do_hooks = hooks
    if not do_hooks and interactive:
        print()
        do_hooks = _prompt_yn("Install worktree hooks?", default=False)
    if do_hooks:
        msg = install_hooks(dry_run=dry_run, backup=backup)
        print(f"  Hooks:{msg}")
        if demo and "would" in msg:
            _demo_actions.append((
                "Install WorktreeCreate/WorktreeRemove hooks in ~/.claude/settings.json",
                "New git worktrees would be automatically indexed so jCodemunch stays in sync with every branch you check out",
            ))

    # ----- Step 3b: Enforcement hooks (PreToolUse + PostToolUse) -----
    do_enforce = hooks  # same flag enables enforcement hooks
    if strict:
        do_enforce = True  # --strict implies installing the hooks it strengthens
    elif not do_enforce and interactive:
        print()
        do_enforce = _prompt_yn(
            "Install enforcement hooks (intercept Read on large code files, auto-reindex after Edit/Write)?",
            default=True,
        )
    elif not do_enforce and yes and not minimal:
        do_enforce = True  # default for --yes mode (suppressed under --minimal)
    if do_enforce:
        msg = install_enforcement_hooks(dry_run=dry_run, backup=backup, strict=strict)
        print(f"  Enforcement:{msg}")
        if strict and not dry_run:
            print(
                "  Strict mode: native Read/Grep inside an indexed repo will be "
                "DENIED (use jcm tools; offset/limit reads still pass). Restart "
                "your client to load it; revert with `init` (no --strict)."
            )
        # touch the install-version stamp so `serve` startup can detect drift
        try:
            _stamp_install_version()
        except Exception:
            pass
        if demo and "would" in msg:
            _demo_actions.append((
                "Install PreToolUse + PostToolUse enforcement hooks in ~/.claude/settings.json",
                "Large code files would be routed through jCodemunch (get_file_outline + get_symbol_source) "
                "instead of raw Read, and the index would auto-update after every Edit/Write — "
                "eliminating staleness anxiety and enforcing token-efficient navigation",
            ))

    # ----- Step 3c: Copilot hooks (.github/hooks/hooks.json) -----
    if copilot_hooks:
        msg = install_copilot_hooks(dry_run=dry_run, backup=backup)
        print(f"  Copilot hooks:{msg}")
        if demo and "would" in msg:
            _demo_actions.append((
                "Write .github/hooks/hooks.json with a jcodemunch postToolUse rule",
                "GitHub Copilot CLI / cloud-agent runs would auto-reindex edited files, "
                "keeping jCodemunch fresh without any manual `index-file` calls",
            ))

    # ----- Step 4: Index -----
    do_index = index
    if not do_index and interactive:
        print()
        do_index = _prompt_yn(f"Index current directory ({os.getcwd()})?", default=True)
    if do_index:
        msg = run_index(dry_run=dry_run)
        print(f"  Index:{msg}")
        if demo and "would" in msg:
            _demo_actions.append((
                f"Index {os.getcwd()}",
                "Symbol search, find-references, and repo exploration would be available immediately — without opening a single file",
            ))

    # ----- Step 5: Audit agent config -----
    do_audit = audit
    if not do_audit and interactive:
        print()
        do_audit = _prompt_yn("Audit agent config files for token waste?", default=True)
    elif not do_audit and yes and not minimal:
        do_audit = True  # default for --yes mode (suppressed under --minimal)

    if do_audit:
        print()
        print("  Audit:")
        for line in run_audit(project_path=os.getcwd(), dry_run=dry_run):
            print(line)
        if demo:
            _demo_actions.append((
                "Audit agent config files (CLAUDE.md, .cursorrules, etc.) for token waste",
                "Stale symbols, oversized instructions, and repeated boilerplate would be flagged — reducing context overhead on every Claude turn",
            ))

    # ----- Done -----
    print()
    if demo:
        print("Demo complete — no changes were made.\n")
        if _demo_actions:
            print("Had this NOT been a demo, I would have:\n")
            for action, benefit in _demo_actions:
                print(f"  • {action}")
                print(f"    Benefit: {benefit}")
                print()
        else:
            print("(Nothing to do — everything is already configured.)")
        print()
    elif dry_run:
        print("Dry run complete -- no changes were made.")
    else:
        print("Done. Restart your MCP client(s) to connect.")
    print()
    return 0


# ---------------------------------------------------------------------------
# Uninstall — reverses every install_* function above
# ---------------------------------------------------------------------------

# Headings emitted by _CLAUDE_MD_POLICY. Used by uninstall to recognise the
# region we own when stripping the policy back out of a CLAUDE.md / AGENTS.md /
# .windsurfrules file.
_POLICY_HEADINGS: tuple[str, ...] = (
    "## Code Exploration Policy",
    "## Session-Aware Routing",
    "## Model-Driven Tool Tiering",
)


def _strip_policy_blocks(text: str) -> tuple[str, bool]:
    """Remove the jCodemunch policy region from a markdown body.

    Treats the first `## Code Exploration Policy` heading as the start of the
    region. Consumes any of `_POLICY_HEADINGS` blocks that follow contiguously
    (so partial / tier-filtered installs are still removed cleanly). Stops at
    the first `## ` heading that is *not* one of ours, preserving any
    user-added sections after the policy.

    Returns (new_text, changed).
    """
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n")
        if stripped in _POLICY_HEADINGS:
            start = i
            break
    if start is None:
        return text, False

    # Walk forward, consuming contiguous policy blocks.
    end = len(lines)
    i = start + 1
    while i < len(lines):
        stripped = lines[i].rstrip("\n")
        if stripped.startswith("## "):
            if stripped in _POLICY_HEADINGS:
                i += 1
                continue
            end = i
            break
        i += 1

    # Trim trailing blank lines we leave behind.
    before = "".join(lines[:start]).rstrip() + ("\n" if start > 0 else "")
    after = "".join(lines[end:])
    new_text = before + ("\n" + after if after.strip() else "")
    return new_text, True


def _unpatch_mcp_config(path: Path, *, backup: bool, dry_run: bool) -> str:
    """Remove the jcodemunch entry from an MCP client JSON config."""
    if not path.exists():
        return f"  no config at {path}"
    data = _read_json(path)
    servers = data.get("mcpServers", {})
    if "jcodemunch" not in servers:
        return f"  jcodemunch not present in {path}"
    if dry_run:
        return f"  would remove jcodemunch from {path}"
    del servers["jcodemunch"]
    if not servers:
        data.pop("mcpServers", None)
    _write_json(path, data, backup=backup)
    return f"  removed jcodemunch from {path}"


def _unconfigure_claude_code(*, dry_run: bool) -> str:
    """Run `claude mcp remove jcodemunch`."""
    if dry_run:
        return "  would run: claude mcp remove jcodemunch"
    claude = _claude_cli_exe()
    if not claude:
        return "  claude CLI not found -- skipped"
    try:
        result = subprocess.run(
            [claude, "mcp", "remove", "jcodemunch"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if result.returncode == 0:
            return "  ran: claude mcp remove jcodemunch"
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        msg = stderr or stdout
        if any(s in msg.lower() for s in ("not found", "does not exist", "no such")):
            return "  not configured in Claude Code"
        return f"  claude mcp remove failed: {msg}"
    except FileNotFoundError:
        return "  claude CLI not found -- skipped"
    except subprocess.TimeoutExpired:
        return "  claude mcp remove timed out"


def unconfigure_client(client: MCPClient, *, backup: bool = True, dry_run: bool = False) -> str:
    """Remove jcodemunch from a single MCP client. Returns a status message."""
    if client.method == "cli":
        return _unconfigure_claude_code(dry_run=dry_run)
    if client.method == "json_patch" and client.config_path:
        return _unpatch_mcp_config(client.config_path, backup=backup, dry_run=dry_run)
    return f"  unknown method for {client.name}"


def uninstall_claude_md(scope: str = "global", *, dry_run: bool = False, backup: bool = True) -> str:
    path = _claude_md_path(scope)
    if not path.exists():
        return f"  no file at {path}"
    text = path.read_text(encoding="utf-8")
    new_text, changed = _strip_policy_blocks(text)
    if not changed:
        return f"  policy not present in {path}"
    if dry_run:
        return f"  would strip policy from {path}"
    if backup:
        shutil.copy2(path, path.with_suffix(".md.bak"))
    if new_text.strip():
        path.write_text(new_text, encoding="utf-8")
        return f"  stripped policy from {path}"
    # File is empty after stripping -- we created it; safe to remove.
    path.unlink()
    return f"  removed empty {path}"


def uninstall_cursor_rules(*, dry_run: bool = False, backup: bool = True) -> str:
    path = _cursor_rules_path()
    if not path.exists():
        return f"  not present at {path}"
    if dry_run:
        return f"  would remove {path}"
    if backup:
        shutil.copy2(path, path.with_suffix(".mdc.bak"))
    path.unlink()
    return f"  removed {path}"


def uninstall_windsurf_rules(*, dry_run: bool = False, backup: bool = True) -> str:
    path = _windsurf_rules_path()
    if not path.exists():
        return f"  no file at {path}"
    text = path.read_text(encoding="utf-8")
    new_text, changed = _strip_policy_blocks(text)
    if not changed:
        return f"  policy not present in {path}"
    if dry_run:
        return f"  would strip policy from {path}"
    if backup:
        shutil.copy2(path, path.with_suffix(".windsurfrules.bak"))
    if new_text.strip():
        path.write_text(new_text, encoding="utf-8")
        return f"  stripped policy from {path}"
    path.unlink()
    return f"  removed empty {path}"


def uninstall_agents_md(*, dry_run: bool = False, backup: bool = True) -> str:
    path = Path.cwd() / "AGENTS.md"
    if not path.exists():
        return f"  no file at {path}"
    text = path.read_text(encoding="utf-8")
    new_text, changed = _strip_policy_blocks(text)
    if not changed:
        return f"  policy not present in {path}"
    if dry_run:
        return f"  would strip policy from {path}"
    if backup:
        shutil.copy2(path, path.with_suffix(".md.bak"))
    if new_text.strip():
        path.write_text(new_text, encoding="utf-8")
        return f"  stripped policy from {path}"
    path.unlink()
    return f"  removed empty {path}"


def _strip_jcm_hooks(data: dict[str, Any]) -> list[str]:
    """Walk settings.json hooks and drop any rule whose command mentions jcodemunch-mcp.

    Returns the names of events that lost rules (for status reporting).
    Empty events and an empty top-level `hooks` key are also pruned.
    """
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return []

    touched: list[str] = []
    for event_name in list(hooks.keys()):
        rules = hooks[event_name]
        if not isinstance(rules, list):
            continue
        kept_rules = []
        dropped = False
        for rule in rules:
            cmds = [h.get("command", "") for h in rule.get("hooks", []) if isinstance(h, dict)]
            if any("jcodemunch-mcp" in cmd for cmd in cmds):
                dropped = True
                continue
            kept_rules.append(rule)
        if dropped:
            touched.append(event_name)
        if kept_rules:
            hooks[event_name] = kept_rules
        else:
            del hooks[event_name]

    if not hooks:
        data.pop("hooks", None)
    return touched


def uninstall_hooks(*, dry_run: bool = False, backup: bool = True) -> str:
    """Reverse install_hooks + install_enforcement_hooks (single ~/.claude/settings.json)."""
    path = _settings_json_path()
    if not path.exists():
        return f"  no settings at {path}"
    data = _read_json(path)
    snapshot = json.dumps(data, sort_keys=True)
    touched = _strip_jcm_hooks(data)
    if not touched and json.dumps(data, sort_keys=True) == snapshot:
        return f"  no jcodemunch hooks in {path}"
    if dry_run:
        return f"  would strip jcodemunch hooks from {', '.join(touched)} in {path}"
    _write_json(path, data, backup=backup)
    return f"  stripped jcodemunch hooks from {', '.join(touched)} in {path}"


def uninstall_copilot_hooks(*, dry_run: bool = False, backup: bool = True) -> str:
    cwd = Path.cwd()
    hooks_path = cwd / ".github" / "hooks" / "hooks.json"
    if not hooks_path.exists():
        return f"  no hooks file at {hooks_path}"
    try:
        data = json.loads(hooks_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, OSError):
        return f"  failed to parse {hooks_path}; skipped"
    hooks = data.get("hooks", {})
    pt = hooks.get("postToolUse", [])
    if not isinstance(pt, list):
        return f"  unexpected shape in {hooks_path}; skipped"
    kept = [r for r in pt if not r.get("bash", "").startswith("jcodemunch-mcp hook-copilot")]
    if len(kept) == len(pt):
        return f"  no jcodemunch Copilot hook in {hooks_path}"
    if dry_run:
        return f"  would remove jcodemunch Copilot hook from {hooks_path}"
    if backup:
        hooks_path.with_suffix(".json.bak").write_text(
            hooks_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    if kept:
        hooks["postToolUse"] = kept
    else:
        hooks.pop("postToolUse", None)
    if not hooks:
        data.pop("hooks", None)
    if data == {} or data == {"version": 1}:
        hooks_path.unlink()
        return f"  removed empty {hooks_path}"
    hooks_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return f"  removed jcodemunch Copilot hook from {hooks_path}"


# Map of friendly target names accepted by `jcm install <target>` /
# `jcm uninstall <target>`. Values are canonical MCPClient.name strings (where
# applicable). `all` is a sentinel meaning "every detected client".
_AGENT_ALIASES: dict[str, str] = {
    "claude-code": "Claude Code",
    "claude-desktop": "Claude Desktop",
    "cursor": "Cursor",
    "windsurf": "Windsurf",
    "continue": "Continue",
    "all": "__all__",
}


def _resolve_target_client(target: str, detected: list[MCPClient]) -> Optional[MCPClient]:
    canonical = _AGENT_ALIASES.get(target.lower())
    if canonical is None:
        return None
    if canonical == "__all__":
        return None
    for c in detected:
        if c.name == canonical:
            return c
    return None


def run_uninstall(
    *,
    target: Optional[str] = None,
    claude_md: bool = True,
    cursor_rules: bool = True,
    windsurf_rules: bool = True,
    agents_md: bool = True,
    hooks: bool = True,
    copilot_hooks: bool = True,
    skills: bool = True,
    claude_md_scope: str = "global",
    dry_run: bool = False,
    no_backup: bool = False,
    yes: bool = False,
) -> int:
    """Reverse a prior `init` run. Returns exit code (0 = success)."""
    backup = not no_backup

    if dry_run:
        print("\njCodeMunch uninstall -- DRY RUN (no changes will be made)\n")
    else:
        print("\njCodeMunch uninstall\n")

    detected = _detect_clients()
    if target and target.lower() not in _AGENT_ALIASES:
        print(f"Unknown target: {target}")
        print(f"Valid targets: {', '.join(sorted(_AGENT_ALIASES))}")
        return 2

    # ---- MCP client config ----
    if target:
        if target.lower() == "all":
            client_targets = detected
        else:
            resolved = _resolve_target_client(target, detected)
            client_targets = [resolved] if resolved else []
            if not resolved:
                print(f"  {target}: not detected on this machine")
    else:
        client_targets = detected

    for client in client_targets:
        msg = unconfigure_client(client, backup=backup, dry_run=dry_run)
        print(f"  {client.name}:{msg}")

    # When uninstalling a single client, we leave the file-system policies and
    # hooks alone unless the caller explicitly asks otherwise. Match the
    # symmetry of `install` which writes them only when the relevant client is
    # selected.
    scoped_to_one = bool(target) and target.lower() != "all"

    if claude_md and (not scoped_to_one or target.lower() in {"claude-code", "claude-desktop"}):
        for scope in ("global", "project"):
            msg = uninstall_claude_md(scope, dry_run=dry_run, backup=backup)
            print(f"  CLAUDE.md ({scope}):{msg}")

    if cursor_rules and (not scoped_to_one or target.lower() == "cursor"):
        msg = uninstall_cursor_rules(dry_run=dry_run, backup=backup)
        print(f"  Cursor rules:{msg}")

    if windsurf_rules and (not scoped_to_one or target.lower() == "windsurf"):
        msg = uninstall_windsurf_rules(dry_run=dry_run, backup=backup)
        print(f"  Windsurf rules:{msg}")

    if agents_md and not scoped_to_one:
        msg = uninstall_agents_md(dry_run=dry_run, backup=backup)
        print(f"  AGENTS.md:{msg}")

    if hooks and not scoped_to_one:
        msg = uninstall_hooks(dry_run=dry_run, backup=backup)
        print(f"  Hooks:{msg}")

    if copilot_hooks and not scoped_to_one:
        msg = uninstall_copilot_hooks(dry_run=dry_run, backup=backup)
        print(f"  Copilot hooks:{msg}")

    if skills and not scoped_to_one:
        from .skills import uninstall_claude_skill
        for scope in ("global", "project"):
            msg = uninstall_claude_skill(scope=scope, dry_run=dry_run, backup=backup)
            print(f"  Claude Skill ({scope}):{msg}")

    print()
    if dry_run:
        print("Dry run complete -- no changes were made.")
    else:
        print("Done.")
    print()
    return 0


# ---------------------------------------------------------------------------
# Status — read-only inspection of current install state
# ---------------------------------------------------------------------------

def install_status() -> dict[str, Any]:
    """Read current state of every install target.

    Returns a dict with sub-blocks for clients / policies / hooks. Designed for
    JSON consumption (CI, dashboards) and pretty-printing by `print_status`.
    All checks are read-only.
    """
    report: dict[str, Any] = {
        "clients": [],
        "policies": {},
        "hooks": {},
    }

    for client in _detect_clients():
        entry: dict[str, Any] = {
            "name": client.name,
            "method": client.method,
            "config_path": str(client.config_path) if client.config_path else None,
            "configured": False,
        }
        if client.method == "json_patch" and client.config_path:
            data = _read_json(client.config_path)
            entry["configured"] = _has_jcodemunch_entry(data)
        elif client.method == "cli":
            claude = _claude_cli_exe()
            if not claude:
                entry["configured"] = False
            else:
                try:
                    result = subprocess.run(
                        [claude, "mcp", "list"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                    )
                    # Launcher-agnostic: matches the server name in `mcp list`
                    # output regardless of how it's launched (uvx jcodemunch-mcp,
                    # the jmunch-mcp multiplexer, a venv path, ...).
                    entry["configured"] = (
                        result.returncode == 0
                        and "jcodemunch" in (result.stdout or "")
                    )
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    entry["configured"] = False
        report["clients"].append(entry)

    # File-based policies
    for label, path in (
        ("claude_md_global", _claude_md_path("global")),
        ("claude_md_project", _claude_md_path("project")),
        ("cursor_rules", _cursor_rules_path()),
        ("windsurf_rules", _windsurf_rules_path()),
        ("agents_md", Path.cwd() / "AGENTS.md"),
    ):
        report["policies"][label] = {
            "path": str(path),
            "present": _has_policy(path),
        }

    # Hooks in ~/.claude/settings.json
    settings_path = _settings_json_path()
    settings_data = _read_json(settings_path) if settings_path.exists() else {}
    jcm_events: list[str] = []
    for event_name, rules in (settings_data.get("hooks") or {}).items():
        if not isinstance(rules, list):
            continue
        for rule in rules:
            cmds = [h.get("command", "") for h in rule.get("hooks", []) if isinstance(h, dict)]
            if any("jcodemunch-mcp" in cmd for cmd in cmds):
                jcm_events.append(event_name)
                break
    report["hooks"]["claude_settings"] = {
        "path": str(settings_path),
        "events_with_jcm_rules": jcm_events,
    }

    # Copilot hooks
    copilot_path = Path.cwd() / ".github" / "hooks" / "hooks.json"
    copilot_present = False
    if copilot_path.exists():
        try:
            cdata = json.loads(copilot_path.read_text(encoding="utf-8"))
            for r in (cdata.get("hooks") or {}).get("postToolUse", []) or []:
                if isinstance(r, dict) and r.get("bash", "").startswith("jcodemunch-mcp hook-copilot"):
                    copilot_present = True
                    break
        except (json.JSONDecodeError, ValueError, OSError):
            pass
    report["hooks"]["copilot"] = {
        "path": str(copilot_path),
        "present": copilot_present,
    }

    # Claude Agent Skill bundle (v1.107.0) — per-scope presence
    from .skills import skill_status as _skill_status
    report["skills"] = {
        "global": _skill_status("global"),
        "project": _skill_status("project"),
    }

    return report


def print_status(report: Optional[dict[str, Any]] = None, *, as_json: bool = False) -> None:
    """Pretty-print install_status() or emit it as JSON."""
    report = report if report is not None else install_status()
    if as_json:
        print(json.dumps(report, indent=2))
        return

    print("\njCodeMunch install status\n")
    print("Clients:")
    if not report["clients"]:
        print("  (none detected)")
    for c in report["clients"]:
        flag = "[x]" if c["configured"] else "[ ]"
        loc = c["config_path"] or "via CLI"
        print(f"  {flag} {c['name']}  ({loc})")

    print("\nPolicies:")
    for label, info in report["policies"].items():
        flag = "[x]" if info["present"] else "[ ]"
        print(f"  {flag} {label}  ({info['path']})")

    print("\nHooks:")
    cs = report["hooks"]["claude_settings"]
    events = cs.get("events_with_jcm_rules") or []
    flag = "[x]" if events else "[ ]"
    detail = ", ".join(events) if events else "no jcodemunch rules"
    print(f"  {flag} Claude settings.json  ({detail})")
    cp = report["hooks"]["copilot"]
    flag = "[x]" if cp["present"] else "[ ]"
    print(f"  {flag} Copilot hooks  ({cp['path']})")

    if "skills" in report:
        print("\nClaude Agent Skill (v1.107.0):")
        for scope in ("global", "project"):
            info = report["skills"].get(scope, {})
            flag = "[x]" if info.get("present") else "[ ]"
            print(f"  {flag} {scope}  ({info.get('path', '')})")
    print()


def list_targets() -> None:
    """Print the set of valid `install <target>` / `uninstall <target>` names."""
    print("\nAvailable install targets:\n")
    for alias in sorted(_AGENT_ALIASES):
        if alias == "all":
            print(f"  {alias:<16}  every detected MCP client")
        else:
            canonical = _AGENT_ALIASES[alias]
            print(f"  {alias:<16}  {canonical}")
    print()
