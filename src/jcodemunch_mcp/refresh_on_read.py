"""Re-index the files that changed since the index was built, before a read answers.

After a rebase, a branch switch or an edit made outside the agent, the index
still holds the old code and a search serves it; only the agent's own edits
reach the index without the watcher. With the ``refresh_on_read`` config key
on, the dispatcher calls :func:`refresh_before_read` before a read tool runs.

- **What changed** is asked of git, once per repository per cooldown: the
  indexed commit against the working tree (``git diff --name-status``) plus
  untracked files. A modified file whose mtime is not newer than the index's
  record is skipped, since the index already holds it (the edit hook ran).
- **Few changes** (at most :data:`REFRESH_MAX_FILES`) are re-indexed inline,
  through the watcher's change-set path, inside :data:`REFRESH_BUDGET_SECONDS`.
- **Otherwise** the read answers from the index as it is, and the block says
  why (``too_many_files``, ``locked``, ``budget``) and names the MCP call that
  fixes it. The block is a body key every encoder carries, never ``_meta``,
  which a default install strips.

Git repositories only: a non-git folder keeps today's per-row freshness flag.
It never waits on another writer, and never raises: any failure is a None.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Seeded, not calibrated. A check costs two git calls, so it runs at most once
# per repository per cooldown; one re-indexed file measures roughly 70-190 ms.
REFRESH_COOLDOWN_SECONDS = 30.0
REFRESH_MAX_FILES = 50
REFRESH_BUDGET_SECONDS = 2.0

_GIT_TIMEOUT_SECONDS = 5.0

# The read tools that check first: each takes `repo` and answers from the
# index's symbols or file bodies. Index-writing and session tools never do.
READ_TOOLS = frozenset(
    {
        "search_symbols",
        "search_text",
        "get_symbol_source",
        "get_file_outline",
        "get_file_content",
        "get_ranked_context",
        "get_context_bundle",
        "find_references",
        "check_references",
        "find_importers",
        "get_call_hierarchy",
        "get_blast_radius",
    }
)

_last_check: dict[str, float] = {}
_lock = threading.Lock()


def reset_cooldowns() -> None:
    """Forget every cooldown (tests, and a deliberate re-index)."""
    with _lock:
        _last_check.clear()


def _git(source_root: str, *args: str) -> Optional[bytes]:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=source_root,
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug(
            "refresh_on_read: git %s failed in %s", args[:2], source_root, exc_info=True
        )
        return None
    return out.stdout if out.returncode == 0 else None


def _is_git_worktree(source_root: str) -> bool:
    out = _git(source_root, "rev-parse", "--is-inside-work-tree")
    return out is not None and out.strip() == b"true"


def _changes(
    source_root: str, indexed_head: Optional[str], index
) -> Optional[list[tuple[str, str]]]:
    """(change_type, absolute path) for indexed files that changed, or None when git cannot say."""
    base = indexed_head or "HEAD"
    diff = _git(
        source_root,
        "diff",
        "--name-status",
        "--no-renames",
        "-z",
        "--relative",
        base,
        "--",
        ".",
    )
    if diff is None and indexed_head:
        # The indexed commit is gone (a rewritten history): compare to HEAD.
        diff = _git(
            source_root,
            "diff",
            "--name-status",
            "--no-renames",
            "-z",
            "--relative",
            "HEAD",
            "--",
            ".",
        )
    untracked = _git(
        source_root, "ls-files", "--others", "--exclude-standard", "-z", "--", "."
    )
    if diff is None or untracked is None:
        return None
    indexed = set(getattr(index, "source_files", None) or ())
    mtimes = getattr(index, "file_mtimes", None) or {}
    root = Path(source_root)
    found: dict[str, str] = {}
    parts = diff.decode("utf-8", errors="replace").split("\0")
    for status, rel in zip(parts[0::2], parts[1::2]):
        if not status or not rel:
            continue
        rel = rel.replace("\\", "/")
        if status.startswith("D"):
            if rel in indexed:
                found[rel] = "deleted"
            continue
        if rel in indexed:
            try:
                disk_ns = (root / rel).stat().st_mtime_ns
            except OSError:
                continue
            stored = mtimes.get(rel)
            if isinstance(stored, (int, float)) and disk_ns <= int(stored):
                continue  # the index already holds this edit
            found[rel] = "modified"
        else:
            found[rel] = "added"
    for rel in untracked.decode("utf-8", errors="replace").split("\0"):
        rel = rel.replace("\\", "/")
        if rel and rel not in indexed:
            found.setdefault(rel, "added")
    return [(kind, str(root / rel)) for rel, kind in sorted(found.items())]


def _index_write_locked(owner: str, name: str, storage_root: str) -> bool:
    from .storage import process_locks

    try:
        return (
            process_locks.inspect("indexwrite", f"{owner}/{name}", storage_root)
            is not None
        )
    except Exception:
        logger.debug("refresh_on_read: lock inspect failed", exc_info=True)
        return True  # cannot tell: do not write


def _reindex(source_root: str, changes: list, storage_path: Optional[str]) -> dict:
    from .tools.index_folder import index_folder

    return index_folder(
        source_root,
        use_ai_summaries=False,
        storage_path=storage_path,
        incremental=True,
        changed_paths=changes,
    )


def _unrepaired(n: int, reason: str, source_root: str) -> dict:
    return {
        "stale_files": n,
        "refreshed": 0,
        "reason": reason,
        "call": "index_folder",
        "args": {"path": source_root, "incremental": True},
    }


def refresh_before_read(
    repo: str, storage_path: Optional[str] = None
) -> Optional[dict]:
    """Re-index what changed since the index of ``repo`` was built.

    Returns None when nothing was stale, the cooldown has not passed, or the
    question could not be asked. Returns ``{"refreshed": n}`` after a repair,
    or an unrepaired block naming the reason and the call that fixes it.
    """
    try:
        from .storage import IndexStore
        from .tools._utils import resolve_repo

        owner, name = resolve_repo(repo, storage_path)
        store = IndexStore(base_path=storage_path)
        index = store.load_index(owner, name)
        if not index:
            return None
        source_root = getattr(index, "source_root", "") or ""
        if not source_root or not os.path.isdir(source_root):
            return None
        key = os.path.normcase(os.path.abspath(source_root))
        now = time.monotonic()
        with _lock:
            last = _last_check.get(key)
            if last is not None and now - last < REFRESH_COOLDOWN_SECONDS:
                return None
            _last_check[key] = now
        if not _is_git_worktree(source_root):
            return None
        changes = _changes(source_root, getattr(index, "git_head", None) or None, index)
        if not changes:
            return None
        if len(changes) > REFRESH_MAX_FILES:
            return _unrepaired(len(changes), "too_many_files", source_root)
        if _index_write_locked(owner, name, str(store.base_path)):
            return _unrepaired(len(changes), "locked", source_root)
        result: dict = {}
        worker = threading.Thread(
            target=lambda: result.update(_reindex(source_root, changes, storage_path)),
            name="jcm-refresh-on-read",
            daemon=True,
        )
        worker.start()
        worker.join(REFRESH_BUDGET_SECONDS)
        if worker.is_alive():
            # The re-index keeps running and finishes on its own; this answer
            # is served from the index as it was.
            return _unrepaired(len(changes), "budget", source_root)
        if not result.get("success", False):
            return _unrepaired(len(changes), "failed", source_root)
        return {"refreshed": len(changes)}
    except Exception:
        logger.debug("refresh_on_read failed for %s", repo, exc_info=True)
        return None
