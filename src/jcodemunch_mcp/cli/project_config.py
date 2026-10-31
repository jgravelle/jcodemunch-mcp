"""MCP config files a team commits to its repository (`init --project`).

`init` registers the server per user, so every teammate has to run it. The
files here live in the repository and reach the whole team on their next
pull. Paths and keys follow each client's documentation as read on
2026-10-31.

Rules, each with a test in ``tests/test_init_project_config.py``:

- The launcher is ``uvx jcodemunch-mcp``, unpinned, with no path and no
  environment variable: a committed file must work on every teammate's
  machine, and each client expands variables with a different syntax.
- An existing file is merged, never replaced. Every other server and key is
  kept, and an existing ``jcodemunch`` entry is left exactly as written (a
  team may have pinned it on purpose).
- A file that does not parse is refused and named. A file that parses only
  once comments are stripped (JSONC) is left untouched and the entry is
  printed for the user to add, because writing it back would drop the
  comments.
- A client the repository does not already use gets no file unless
  ``all_files`` is set, so nobody finds a folder for a tool nobody runs.

Codex is absent on purpose: it needs a resolved binary path (plain init
refuses it ``uvx``), which is machine-specific. Cline documents no project
file. Stdlib only.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .backup import backup_path

logger = logging.getLogger(__name__)

SERVER_NAME = "jcodemunch"
_COMMAND = "uvx"
_ARGS = ["jcodemunch-mcp"]


@dataclass(frozen=True)
class ProjectFile:
    """One client's project config file."""

    rel: str
    key: str
    entry: dict
    # Write only when this path already exists in the repo; None = always.
    when_exists: Optional[str] = None


_PLAIN = {"command": _COMMAND, "args": list(_ARGS)}
_STDIO = {"type": "stdio", "command": _COMMAND, "args": list(_ARGS)}

FILES: tuple[ProjectFile, ...] = (
    # Claude Code, VS Code (portable format), GitHub Copilot CLI.
    ProjectFile(".mcp.json", "mcpServers", _PLAIN),
    # Cursor's documentation shows `type` as required.
    ProjectFile(".cursor/mcp.json", "mcpServers", _STDIO),
    # VS Code's own file keys servers under `servers`.
    ProjectFile(".vscode/mcp.json", "servers", _STDIO),
    ProjectFile(".junie/mcp/mcp.json", "mcpServers", _PLAIN, ".junie"),
    ProjectFile(".gemini/settings.json", "mcpServers", _PLAIN, ".gemini"),
    # opencode keys servers under `mcp` and takes the command as one array.
    ProjectFile(
        "opencode.json",
        "mcp",
        {"type": "local", "command": [_COMMAND, *_ARGS]},
        "opencode.json",
    ),
    ProjectFile(".devin/mcp_config.json", "mcpServers", _PLAIN, ".devin"),
)


@dataclass
class ProjectResult:
    root: Path
    changed: list[str] = field(default_factory=list)  # written or would be
    kept: list[str] = field(default_factory=list)  # entry already present
    skipped: list[str] = field(default_factory=list)  # client not used here
    refused: list[str] = field(default_factory=list)  # file does not parse
    manual: list[str] = field(default_factory=list)  # JSONC: add by hand


def repo_root(start: Optional[Path] = None) -> Optional[Path]:
    """The top of the git work tree holding ``start``; None outside one."""
    cwd = Path(start) if start is not None else Path.cwd()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("git rev-parse failed in %s", cwd, exc_info=True)
        return None
    top = out.stdout.strip()
    if out.returncode != 0 or not top:
        return None
    return Path(top).resolve()


def _load(path: Path) -> tuple[Optional[dict], str]:
    """Parse ``path``. Returns (data, state); state is ok, missing, jsonc or bad."""
    if not path.exists():
        return {}, "missing"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.debug("cannot read %s", path, exc_info=True)
        return None, "bad"
    if not text.strip():
        return {}, "ok"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        from ..config import _strip_jsonc

        try:
            json.loads(_strip_jsonc(text))
        except json.JSONDecodeError:
            return None, "bad"
        return None, "jsonc"
    if not isinstance(data, dict):
        return None, "bad"
    return data, "ok"


def _entry_line(spec: ProjectFile) -> str:
    return (
        f'{spec.rel}: add under "{spec.key}": "{SERVER_NAME}": {json.dumps(spec.entry)}'
    )


def install_project_config(
    root: Path,
    *,
    all_files: bool = False,
    dry_run: bool = False,
    backup: bool = True,
) -> ProjectResult:
    """Add the ``jcodemunch`` entry to every applicable project file in ``root``."""
    root = Path(root)
    result = ProjectResult(root=root)
    for spec in FILES:
        if (
            spec.when_exists is not None
            and not all_files
            and not (root / spec.when_exists).exists()
        ):
            result.skipped.append(spec.rel)
            continue
        path = root / spec.rel
        data, state = _load(path)
        if state == "bad":
            result.refused.append(spec.rel)
            continue
        if state == "jsonc":
            result.manual.append(_entry_line(spec))
            continue
        assert data is not None
        servers = data.get(spec.key)
        if servers is None:
            servers = {}
        if not isinstance(servers, dict):
            result.refused.append(spec.rel)
            continue
        if SERVER_NAME in servers:
            result.kept.append(spec.rel)
            continue
        result.changed.append(spec.rel)
        if dry_run:
            continue
        new: dict[str, Any] = dict(data)
        new[spec.key] = {**servers, SERVER_NAME: dict(spec.entry)}
        if backup and path.exists():
            shutil.copy2(path, backup_path(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
    return result


def report_lines(result: ProjectResult, *, dry_run: bool = False) -> list[str]:
    """What happened, and the ``git add`` line for the files to commit."""
    verb = "would add" if dry_run else "added"
    lines = [f"  {rel}: {verb} {SERVER_NAME}" for rel in result.changed]
    lines += [
        f"  {rel}: {SERVER_NAME} already present, left as written"
        for rel in result.kept
    ]
    lines += [f"  {rel}: does not parse as JSON, not changed" for rel in result.refused]
    if result.manual:
        lines.append("  These files carry comments; add the entry by hand:")
        lines += [f"    {line}" for line in result.manual]
    if result.skipped:
        lines.append(
            "  Not written (the repo does not use the client; --project-all writes them): "
            + ", ".join(result.skipped)
        )
    if result.changed and not dry_run:
        lines.append("")
        lines.append("  Commit them so the team gets the server on their next pull:")
        lines.append("    git add " + " ".join(result.changed))
    return lines
