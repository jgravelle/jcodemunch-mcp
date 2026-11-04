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

# Seeded, not calibrated. A check costs up to four git calls, so it runs at
# most once per repository per cooldown, in each server process.
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

# Changed or untracked paths that are not indexed are asked of the walk's own
# rule one at a time. Past this many refusals the check stops asking, so a tree
# of files nobody indexes (an un-ignored `node_modules/`) costs a bounded scan;
# the answer then says the rest went unchecked, never that nothing is stale.
_MAX_REFUSALS_CHECKED = 200

_last_check: dict[str, float] = {}
# Repositories with a re-index running that outlived its answer's budget. A
# check skips them until it finishes, so writers never pile up per repository.
_in_flight: set[str] = set()
_lock = threading.Lock()


def reset_cooldowns() -> None:
    """Forget every cooldown (tests, and a deliberate re-index)."""
    with _lock:
        _last_check.clear()
        _in_flight.clear()


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
) -> Optional[tuple[list[tuple[str, str]], bool]]:
    """((change_type, absolute path) list, every candidate checked), or None when git cannot say."""
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
    candidates: list[str] = []
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
            candidates.append(rel)
    for rel in untracked.decode("utf-8", errors="replace").split("\0"):
        rel = rel.replace("\\", "/")
        if rel and rel not in indexed:
            candidates.append(rel)
    added, complete = _indexable(root, index, dict.fromkeys(candidates))
    for rel in added:
        found.setdefault(rel, "added")
    return [(kind, str(root / rel)) for rel, kind in sorted(found.items())], complete


def _indexable(root: Path, index, candidates) -> tuple[list[str], bool]:
    """(the not-yet-indexed paths a full walk would index, whether every candidate was asked).

    Asked of `index_file`'s `_walk_refusal`, the walk's own rule for one path,
    so a binary, a log or an un-ignored dependency tree is never counted as a
    stale file: re-indexing it changes nothing, and the walk would drop it.
    Stops once more than :data:`REFRESH_MAX_FILES` are found, since the answer
    is then "too many" whatever the rest are, and after
    :data:`_MAX_REFUSALS_CHECKED` refusals, reporting the rest as unchecked.
    """
    from .tools.index_file import _walk_refusal, _walk_root_for

    source_root = root.resolve()
    out: list[str] = []
    refused = 0
    for rel in candidates:
        path = source_root / rel
        if not path.is_file():
            continue
        try:
            refusal = _walk_refusal(
                path, source_root, _walk_root_for(index, source_root, rel), rel
            )
        except Exception:
            logger.debug("refresh_on_read: walk rule failed for %s", rel, exc_info=True)
            refusal = {"error": "unknown"}
        if refusal is None:
            out.append(rel)
            if len(out) > REFRESH_MAX_FILES:
                return out, True
        else:
            refused += 1
            if refused >= _MAX_REFUSALS_CHECKED:
                return out, False
    return out, True


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
    block = {
        "stale_files": n,
        "refreshed": 0,
        "reason": reason,
        "call": "index_folder",
        "args": {"path": source_root, "incremental": True},
    }
    if reason == "too_many_files":
        # `_indexable` stops counting once the answer is "too many".
        block["stale_files_basis"] = "at_least"
    return block


def _undetermined() -> dict:
    """More not-indexed paths than the check asks about: it cannot say the index is current."""
    return {
        "reason": "undetermined",
        "refreshed": None,
        "unchecked": "not_indexed_paths",
        # `index_folder` cannot clear this: the paths are ones it refuses.
        "hint": "Add the directory of files you never index (a dependency or build tree) to .gitignore.",
    }


def _in_progress(n: Optional[int]) -> dict:
    """A re-index is still running: its outcome is unknown, so no count and no call."""
    block: dict = {"reason": "in_progress", "refreshed": None}
    if n is not None:
        block["stale_files"] = n
    return block


def refresh_before_read(
    repo: str, storage_path: Optional[str] = None
) -> Optional[dict]:
    """Re-index what changed since the index of ``repo`` was built.

    Returns None when nothing was stale, the cooldown has not passed, or the
    question could not be asked. Returns ``{"refreshed": n}`` after a repair,
    ``in_progress`` while an earlier re-index of this repository is still
    running, or an unrepaired block naming the reason and the call that fixes it.
    """
    try:
        from .storage import IndexStore
        from .tools._utils import resolve_repo

        owner, name = resolve_repo(repo, storage_path)
        store = IndexStore(base_path=storage_path)
        key = (
            f"{os.path.normcase(os.path.abspath(str(store.base_path)))}::{owner}/{name}"
        )
        now = time.monotonic()
        with _lock:
            if key in _in_flight:
                return _in_progress(None)
            last = _last_check.get(key)
            if last is not None and now - last < REFRESH_COOLDOWN_SECONDS:
                return None
            _last_check[key] = now
        # Metadata only: a selective view, so this never hydrates the symbols
        # ahead of a tool that avoids loading them (#557, #398).
        index = store.open_selective(owner, name, symbol_ids=())
        if index is None:
            index = store.load_index(owner, name)
        if not index:
            return None
        source_root = getattr(index, "source_root", "") or ""
        if not source_root or not os.path.isdir(source_root):
            return None
        if not _is_git_worktree(source_root):
            return None
        asked = _changes(source_root, getattr(index, "git_head", None) or None, index)
        if asked is None:
            return None
        changes, complete = asked
        if not changes:
            # A check that could not ask every path must not read as "nothing stale".
            return None if complete else _undetermined()
        if len(changes) > REFRESH_MAX_FILES:
            return _unrepaired(len(changes), "too_many_files", source_root)
        if _index_write_locked(owner, name, str(store.base_path)):
            return _unrepaired(len(changes), "locked", source_root)
        with _lock:
            if key in _in_flight:
                return _in_progress(len(changes))
            _in_flight.add(key)
        result: dict = {}

        def _run() -> None:
            try:
                result.update(_reindex(source_root, changes, storage_path))
            except Exception:
                logger.debug(
                    "refresh_on_read: re-index failed for %s", repo, exc_info=True
                )
            finally:
                with _lock:
                    _in_flight.discard(key)

        worker = threading.Thread(target=_run, name="jcm-refresh-on-read", daemon=True)
        worker.start()
        worker.join(REFRESH_BUDGET_SECONDS)
        if worker.is_alive():
            # The re-index keeps running and finishes on its own; this answer
            # is served from the index as it was, and its outcome is unknown.
            return _in_progress(len(changes))
        if not result.get("success", False):
            return _unrepaired(len(changes), "failed", source_root)
        done: dict = {"refreshed": len(changes)}
        if not complete:
            done["unchecked"] = "not_indexed_paths"
        return done
    except Exception:
        logger.debug("refresh_on_read failed for %s", repo, exc_info=True)
        return None
