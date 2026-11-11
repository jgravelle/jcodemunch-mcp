"""get_watch_status — surface watch-all state to agents.

Reports every locally-indexed repo the watch-all daemon would cover, each
repo's current reindex state (fresh / in-progress / stale / failing), and
the OS-level service status. Intended for agents to consult before relying
on a potentially stale index.

v1.106.0: also surfaces multi-process holder info. When another MCP server
(Claude Code + Cursor + Codex on the same repo) holds the watcher slot, the
``watcher_holder`` field reports {pid, client_id, started_at, age_seconds}
so the agent knows a parallel session is live and our watcher is intentionally
idle for this repo.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from ..reindex_state import get_reindex_status, has_any_reindex_state
from ..service_installer import service_status
from ..storage import process_locks
from ..watch_all import discover_local_repo_entries, watch_all_scope

logger = logging.getLogger(__name__)


def _reindex_key(folder: str, storage_path: Optional[str]) -> str:
    """Resolve the reindex_state key a watch task registers for ``folder``.

    Watcher tasks key reindex_state by the repo_id (``local/<name>-<hash>``)
    that ``index_folder`` would use — NOT the folder path. Reading by folder
    path returned a phantom default state, so get_watch_status could never see a
    real reindex failure or crash (#353). Resolve the same repo_id here; fall
    back to the folder path if identity resolution fails for any reason.
    """
    try:
        from ..storage import IndexStore
        from ..path_map import parse_path_map, remap
        from ..watcher import _local_repo_id

        store = IndexStore(base_path=storage_path)
        return _local_repo_id(remap(folder, parse_path_map(), reverse=True), store=store)
    except Exception:
        logger.debug("Failed to resolve reindex key for %s; using folder path", folder, exc_info=True)
        return folder


def _repo_freshness(folder: str, entry: dict, check: bool) -> str:
    """`fresh` / `stale` / `unknown` / `not_tracked` for one repo.

    ⚠⚠ Routed through `FreshnessProbe.repo_freshness`, which is THE authority
    on this question and already tri-state. v1.108.180 replaced its boolean
    `repo_is_stale` for exactly the reason #565 exists — a Boolean has nowhere
    to put "I could not find out", and False was being rendered as `fresh`.
    Reproducing the SHA comparison here would be a second answer to a settled
    question.

    ⚠ `unknown` on any failure, never `fresh`: an index whose freshness was
    never established has not been shown current.
    """
    if not check:
        return "unknown"
    try:
        from ..retrieval.freshness import FreshnessProbe

        return FreshnessProbe(
            folder,
            entry.get("indexed_at") or "",
            entry.get("git_head"),
        ).repo_freshness
    except Exception:
        logger.debug("freshness probe failed for %s", folder, exc_info=True)
        return "unknown"


#: Below this share of the tracked source files, an index reads `incomplete`
#: (#1395). The indexer's per-file rule already takes out what it skips on
#: purpose, so the gap left is files over the size cap and files added since
#: the last index; the reporter measured deliberate exclusions at 4-5% of a
#: large repo, which a 0.90 floor leaves room for.
COMPLETENESS_FLOOR = 0.90


def _repo_completeness(
    folder: str, entry: dict, check: bool, storage_path: Optional[str]
) -> dict:
    """How much of the tree the index holds, measured (#1395).

    `index_freshness` answers whether the index is at the current revision. It
    cannot see an index built at HEAD that holds 14 of 9,400 files, which reads
    `fresh`. This counts the tracked files the indexer would take
    (`index_folder.indexable_paths`, the indexer's own per-file rule) and how
    many of them the index holds, and reports both with their ratio.

    A cheap pass counts tracked files by extension alone. That denominator can
    only be larger than the exact one, so a ratio that clears the floor on it
    clears it exactly too, and the per-file rule runs only when it does not.
    `tracked_source_files_basis` says which count was reported.

    ⚠ `unknown` when the count cannot be taken (not a git work tree, git
    failed, the index's file list unreadable, or the check skipped), never
    `complete`: an index nobody counted has not been shown complete.
    """
    out: dict = {"index_completeness": "unknown"}
    if not check:
        return out
    try:
        from ..storage import IndexStore
        from .index_folder import (
            git_tracked_paths,
            has_indexable_extension,
            indexable_paths,
        )

        tracked = git_tracked_paths(Path(folder))
        if tracked is None:
            return out
        owner, _, name = (entry.get("repo") or "").partition("/")
        if not owner or not name:
            return out
        view = IndexStore(base_path=storage_path).open_selective(owner, name)
        held = set(getattr(view, "source_files", None) or ())
        candidates = [p for p in tracked if has_indexable_extension(p)]
        basis = "extension"
        if (
            not candidates
            or len(held.intersection(candidates)) / len(candidates) < COMPLETENESS_FLOOR
        ):
            candidates = indexable_paths(Path(folder), candidates)
            basis = "index_rules"
        indexed = len(held.intersection(candidates))
        total = len(candidates)
        coverage = round(indexed / total, 4) if total else 1.0
        out.update(
            {
                "index_completeness": "complete"
                if coverage >= COMPLETENESS_FLOOR
                else "incomplete",
                "indexed_files": indexed,
                "tracked_source_files": total,
                "tracked_source_files_basis": basis,
                "index_coverage": coverage,
            }
        )
    except Exception:
        logger.debug("completeness check failed for %s", folder, exc_info=True)
        return {"index_completeness": "unknown"}
    return out


def get_watch_status(
    storage_path: Optional[str] = None,
    *,
    check_freshness: bool = True,
) -> dict:
    """Return a summary of watch-all coverage and health.

    ⚠⚠ `index_freshness` compares the INDEX against the TREE. The field it
    replaces, `index_stale`, did not: it was
    `state.reindexing or state.stale_since is not None` over `_RepoState`,
    an in-memory per-process container that only the watcher ever writes.
    A process that never watched anything has no state, so the answer was
    False for every repo, forever, and **a repo nothing had ever looked at
    was indistinguishable from one measured fresh** (#565). Measured on the
    box that reported it: 33 repos, `any_stale` False, and the truth was 1
    stale, 2 unknown and 10 not_tracked.

    ⚠ The watcher bookkeeping is KEPT, under `watcher_flagged_stale`, which
    is what it always meant. It answers "has the watcher queued this for
    reindex in THIS process" — a real question, just not the one the name
    `index_stale` was being read for.

    ⚠ `check_freshness=False` skips the comparison and reports `unknown`
    rather than inventing `fresh`. The index side of the comparison is free —
    `indexed_at` and `git_head` ride on the discovery entry — so the whole
    added cost is one `git rev-parse` per repo. Measured over 33 repos as the
    delta against `check_freshness=False`: **1.28 s cold, 0.12 s warm** (39 vs
    4 ms/repo). The spread is the OS and git caches, not variance; the cold
    figure is the one a first call after boot pays. Both sit under the ~2.4 s
    this tool already spends on discovery and lock inspection, so the default
    is on and the opt-out is for a caller listing hundreds.
    """
    entries = discover_local_repo_entries(storage_path)
    discovered = list(entries)
    # Which of these watch-all leaves unwatched (temp folder, idle), from the
    # same discovery: a repo it skips is otherwise indistinguishable here from
    # one it watches.
    try:
        skipped = watch_all_scope(storage_path, entries=entries)[1]
    except Exception:
        logger.debug("watch-all scope could not be read", exc_info=True)
        # Not knowing is not "watched": say so per repo.
        skipped = {folder: "unknown" for folder in discovered}
    repos_out = []
    any_stale = False
    any_freshness_unknown = False
    any_incomplete = False
    any_in_progress = False
    any_failing = False
    any_watched_by_another_process = False
    my_pid = os.getpid()
    # get_reindex_status reads in-memory per-repo state. A process that never
    # tracked a reindex (notably a cold `list-repos` CLI) has none, so resolving
    # each folder's repo_id key — a git identity probe via _reindex_key — is pure
    # waste: one git subprocess per repo, which scaled list-repos to 60s+ on
    # many-repo hosts. Only pay that resolution when there's state to look up; a
    # process with no reindex state always returns the defaults below anyway.
    track_reindex = has_any_reindex_state()
    for folder in discovered:
        if track_reindex:
            # reindex_state is keyed by the repo_id watcher._watch_single registers
            # (local/<name>-<hash>), not the folder path. Resolve the same key so a
            # failing/crash-looping watcher task is actually visible here (#353).
            status = get_reindex_status(_reindex_key(folder, storage_path))
        else:
            status = {"index_stale": False, "reindex_in_progress": False, "stale_since_ms": None}
        # Rename in place: the flag is watcher bookkeeping and now says so.
        watcher_flagged = bool(status.pop("index_stale", False))
        entry = {
            "source_root": folder,
            "exists": Path(folder).is_dir(),
            "index_freshness": _repo_freshness(
                folder, entries.get(folder) or {}, check_freshness
            ),
            "watcher_flagged_stale": watcher_flagged,
            **status,
        }
        entry.update(
            _repo_completeness(
                folder, entries.get(folder) or {}, check_freshness, storage_path
            )
        )
        if folder in skipped:
            entry["watch_all_skipped"] = skipped[folder]
        # Multi-process holder info: which process currently owns the watcher
        # slot for this folder. Holder == None means lock file is absent OR
        # holder PID is dead (stale lock — inspect() filters those out).
        holder = process_locks.inspect("watcher", folder, storage_path)
        if holder is not None and holder.pid != my_pid:
            entry["watched_by_another_process"] = True
            entry["watcher_holder"] = holder.as_dict()
            any_watched_by_another_process = True
        elif holder is not None:
            entry["watched_by_another_process"] = False
            entry["watcher_holder"] = holder.as_dict()  # us — still useful context
        if entry["index_freshness"] == "stale":
            any_stale = True
        elif entry["index_freshness"] == "unknown":
            any_freshness_unknown = True
        if entry["index_completeness"] == "incomplete":
            any_incomplete = True
        if status.get("reindex_in_progress"):
            any_in_progress = True
        if status.get("reindex_failures") or status.get("reindex_fatal"):
            any_failing = True
        repos_out.append(entry)

    try:
        svc = service_status()
    except Exception as exc:
        logger.debug("service_status failed", exc_info=True)
        svc = {"active": False, "error": str(exc)}

    return {
        "service": svc,
        "repo_count": len(repos_out),
        "any_stale": any_stale,
        "any_freshness_unknown": any_freshness_unknown,
        "any_incomplete": any_incomplete,
        "freshness_checked": check_freshness,
        "any_in_progress": any_in_progress,
        "any_failing": any_failing,
        "any_watched_by_another_process": any_watched_by_another_process,
        "repos": repos_out,
    }
