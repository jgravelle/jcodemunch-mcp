"""Which indexes a session sees: the current workspace by default (repo_visibility).

Every session sharing an index store used to see every repository indexed on
the machine. jjg's ruling 5 (2026-10-13) scopes the default to the workspace,
with `repo_visibility: "all"` to widen it; the edge rulings (2026-10-15) keep an
exact `owner/name` or a path resolvable anywhere, leave a remote (folder-less)
index off the list, and list everything, saying so, when the workspace holds no
index (a Claude Desktop launch has cwd = home or a system folder).

The workspace is the server process's working directory, the signal the server
already reads for the project `.jcodemunch.jsonc`. An index belongs to it when
its `source_root` is that folder, inside it, or contains it (a session opened
in a subfolder of an indexed repo).

Scope governs LISTING and BARE-NAME choice only. It is not an access control:
an exact id still loads, and the answer says what it hid so a scoped listing
never reads as the whole store.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

WORKSPACE = "workspace"
ALL = "all"
_HINT = (
    "Indexes outside this workspace are not listed. Set \"repo_visibility\": \"all\" "
    "in config.jsonc (or JCODEMUNCH_REPO_VISIBILITY=all) to list every index, or "
    "pass an exact repo id to use one."
)


def visibility_mode() -> str:
    """`workspace` (default) or `all`. Anything else reads as the default."""
    from .. import config  # noqa: PLC0415

    value = str(config.get("repo_visibility", WORKSPACE) or WORKSPACE).strip().lower()
    return ALL if value == ALL else WORKSPACE


def workspace_root() -> Optional[Path]:
    """The server's working directory, resolved; None when it cannot be read."""
    try:
        return Path.cwd().resolve()
    except OSError:
        logger.debug("workspace_root: cwd unreadable", exc_info=True)
        return None


def is_project_folder(workspace: Optional[Path]) -> bool:
    """False for a folder too broad to be one project: the home folder, anything
    above it, or a filesystem root.

    Claude Desktop starts the server in the home folder (or `/` on macOS), and
    "the indexes under it" would then be nearly every index on the machine while
    reading as a scoped list. jjg ruled (2026-10-15) that such a launch lists
    everything and says it is unscoped.
    """
    if workspace is None:
        return False
    if workspace == Path(workspace.anchor):
        return False
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        logger.debug("is_project_folder: home unresolvable", exc_info=True)
        return True
    return not (workspace == home or workspace in home.parents)


def in_workspace(entry: dict, workspace: Optional[Path]) -> bool:
    """True when the index's folder is the workspace, inside it, or contains it."""
    root = entry.get("source_root") or ""
    if not root or workspace is None:
        return False
    try:
        r = Path(root).resolve()
    except OSError:
        logger.debug("in_workspace: source_root unresolvable: %s", root, exc_info=True)
        return False
    return r == workspace or workspace in r.parents or r in workspace.parents


def scope_repos(
    repos: list[dict],
    *,
    mode: Optional[str] = None,
    workspace: Optional[Path] = None,
) -> tuple[list[dict], Optional[dict]]:
    """Return (the entries this session sees, the visibility block or None).

    The block is None when nothing was hidden and the listing is scoped as
    configured (`all`, or every index is in the workspace), so the answer only
    grows when there is something to say.
    """
    mode = mode or visibility_mode()
    if mode == ALL or not repos:
        return repos, None
    if workspace is None:
        workspace = workspace_root()
    if not is_project_folder(workspace):
        return repos, {
            "mode": ALL,
            "reason": "workspace_is_not_a_project",
            "hint": (
                "This server was started in the home folder or a filesystem root, "
                "which is not one project, so every index is listed. Start the "
                "server in a project folder to scope the list."
            ),
        }
    visible = [r for r in repos if in_workspace(r, workspace)]
    if not visible:
        return repos, {
            "mode": ALL,
            "reason": "no_index_in_workspace",
            "hint": (
                "No index belongs to this server's working directory, so every index "
                "is listed. Start the server in a project folder to scope the list."
            ),
        }
    hidden = len(repos) - len(visible)
    if not hidden:
        return visible, None
    return visible, {"mode": WORKSPACE, "hidden_count": hidden, "hint": _HINT}


def prefer_workspace(candidates: list[str], entries: list[dict]) -> list[str]:
    """Narrow an ambiguous bare-name match to the workspace's copy when there is one."""
    if len(candidates) < 2 or visibility_mode() == ALL:
        return candidates
    ws = workspace_root()
    if not is_project_folder(ws):
        return candidates
    roots = {e.get("repo"): e for e in entries}
    mine = [c for c in candidates if c in roots and in_workspace(roots[c], ws)]
    return mine if len(mine) == 1 else candidates
