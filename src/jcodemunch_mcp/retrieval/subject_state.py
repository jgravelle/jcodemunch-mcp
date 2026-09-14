"""What a scan's answer depends on, captured cheaply enough to re-check.

#377 hardening item 3. The search result cache keys on the index's
``indexed_at``, so it invalidates on a REINDEX and on nothing else. That is
right for a positive result — the rows it returned were really in the index,
and stale disclosure is enough. It is wrong for a negative one:

    1. the query is absent against snapshot A
    2. the result and its `fresh` verdict are cached
    3. the source changes without the index being rebuilt
    4. the same query hits the cache
    5. the old fresh/absent verdict is replayed, and an absence proof is
       minted over a tree that no longer exists

A cached negative has to prove it still describes the subject it was measured
against. This module captures the subject's state at cache-write time and
compares it at cache-read time, so the refusal names WHAT moved rather than
asserting a generic doubt.

Deliberately cheap. The git HEAD lookup is the process-wide stat-signature
cache in ``freshness``; the index generation and the .db mtime are one stat.
The one genuinely expensive signal, the working tree, is captured ONLY for a
response that reached ``absent`` — the only case where it can change the
answer. A positive cache hit pays nothing.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Bursts of tool calls share one `git status`. Short enough that an edit is
# picked up within a couple of seconds, long enough that a fan-out of searches
# does not spawn a subprocess each.
_TREE_CACHE_TTL_S = 2.0
_tree_cache: dict[str, tuple[Optional[tuple[str, tuple]], float]] = {}


def _clear_tree_cache() -> None:
    """Test hook: drop cached working-tree readings."""
    _tree_cache.clear()


def _parse_porcelain(raw: bytes) -> tuple:
    """Repo-relative paths from ``git status --porcelain`` output.

    A rename reports ``R  old -> new``; the NEW path is the one that exists in
    the tree, and it is the one a search would have had to see. Quoted paths
    (spaces, non-ASCII) are unquoted so they compare against index paths.
    """
    paths: list[str] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if len(line) < 4:
            continue
        entry = line[3:]
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        entry = entry.strip()
        if entry.startswith('"') and entry.endswith('"') and len(entry) > 1:
            try:
                entry = entry[1:-1].encode().decode("unicode_escape")
            except (UnicodeDecodeError, ValueError):
                entry = entry[1:-1]
        if entry:
            paths.append(entry.replace("\\", "/"))
    return tuple(paths)


def _working_tree_reading(source_root: Optional[str]) -> Optional[tuple[str, tuple]]:
    """``(fingerprint, dirty_paths)`` for the tree, or None when unknown.

    None means "could not be determined" (not a git checkout, git missing, the
    command failed) and must never be compared as equality with a real reading —
    unknown is not unchanged. TTL-cached so a burst of searches shares one
    subprocess.

    ``stdin=subprocess.DEVNULL`` is load-bearing, not tidiness (jcm#392,
    @rknighton). This probe runs inside the MCP server process, whose stdin IS
    the live JSON-RPC channel. Without it Git for Windows inherits that stdin,
    and the wrapper's child can outlive the ``timeout=`` that kills the
    immediate process — leaving the captured stdout/stderr pipes open and Python
    blocked in the follow-up ``communicate()`` long past the deadline (observed:
    225-300s on a zero-result ``search_text``). Every other in-server git probe
    already passes it; this one was added later and missed the convention.
    """
    if not source_root:
        return None
    now = time.monotonic()
    hit = _tree_cache.get(source_root)
    if hit is not None and (now - hit[1]) < _TREE_CACHE_TTL_S:
        return hit[0]
    reading: Optional[tuple[str, tuple]] = None
    try:
        # (#685) Porcelain prints paths from the git TOP LEVEL even from a
        # subdirectory (it ignores `status.relativePaths`), while the index
        # holds them from `source_root`. For an index rooted below the top level
        # every dirty path missed `file_mtimes` and read as unindexed, so any
        # uncommitted file anywhere in the monorepo refused absence claims.
        # `-- .` keeps the reading to the corpus; the prefix is stripped so the
        # paths are index paths. At the top level the prefix is empty.
        prefix = subprocess.run(
            ["git", "rev-parse", "--show-prefix"],
            cwd=source_root,
            capture_output=True,
            timeout=10,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal", "--", "."],
            cwd=source_root,
            capture_output=True,
            timeout=10,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        if out.returncode == 0 and prefix.returncode == 0:
            lead = prefix.stdout.decode("utf-8", errors="replace").strip()
            paths = _parse_porcelain(out.stdout)
            if lead:
                paths = tuple(p[len(lead):] if p.startswith(lead) else p for p in paths)
            reading = (
                hashlib.sha256(out.stdout).hexdigest()[:16],
                paths,
            )
    except (OSError, subprocess.SubprocessError):
        logger.debug("Working-tree probe failed for %s", source_root, exc_info=True)
    _tree_cache[source_root] = (reading, now)
    return reading


def working_tree_fingerprint(source_root: Optional[str]) -> Optional[str]:
    """Digest of the working tree's uncommitted state, or None when unknown."""
    reading = _working_tree_reading(source_root)
    return reading[0] if reading else None


def capture(index, *, include_tree: bool = False, fresh_head: bool = False) -> dict:
    """Live state of the subject this index describes. Never raises."""
    state: dict = {}
    try:
        source_root = getattr(index, "source_root", "") or None
        # #398 Arc 1: one contract answers "which index is this", so this
        # surface and `evidence.producers._snapshot` cannot disagree about
        # whether an index with no stored revision reports "" or None.
        from ..storage.generation import describe

        gen = describe(index)
        state["generation"] = gen.generation
        state["index_sha"] = gen.indexed_revision
        state["db_mtime_ns"] = gen.db_mtime_ns
        live = None
        if source_root:
            try:
                if fresh_head:
                    # #377 item 6: the HEAD lookup is cached with a short TTL, so
                    # a before/after comparison inside that window compares one
                    # reading with itself. A negative is the one answer worth
                    # paying the subprocess to close that window for.
                    from .freshness import _git_head_uncached

                    live = _git_head_uncached(Path(source_root))
                else:
                    from .freshness import _git_head

                    live = _git_head(Path(source_root))
            except Exception:
                live = None
        state["live_sha"] = live
        if include_tree:
            state["tree"] = working_tree_fingerprint(source_root)
    except Exception:
        logger.debug("Subject-state capture failed", exc_info=True)
    return state


#: When the two readings were taken, phrased for the sentence each reason ends up in.
WHEN_CACHED = "after this scan was cached"
WHEN_SCANNING = "while the scan was running"


def changed(
    before: Optional[dict], after: Optional[dict], *, when: str = WHEN_CACHED
) -> Optional[str]:
    """Why *after* no longer describes the same subject as *before*.

    Returns None when nothing observable moved. An unknown value on either
    side is never reported as a change: guessing here would refuse every
    absence on a non-git checkout, where nothing was ever knowable and nothing
    changed either.
    """
    if not before or not after:
        return None
    if before.get("generation") != after.get("generation"):
        return f"the index was rebuilt {when}"
    b_db, a_db = before.get("db_mtime_ns"), after.get("db_mtime_ns")
    if b_db is not None and a_db is not None and b_db != a_db:
        return f"the index was rewritten {when}"
    b_sha, a_sha = before.get("live_sha"), after.get("live_sha")
    if b_sha and a_sha and b_sha != a_sha:
        return (
            f"the checkout moved to a different commit {when}, so the scan describes "
            f"{b_sha[:12]} and the question is about {a_sha[:12]}"
        )
    b_tree, a_tree = before.get("tree"), after.get("tree")
    if b_tree and a_tree and b_tree != a_tree:
        return (
            f"the working tree changed {when}, so the target may sit in an edit the "
            "scan never saw"
        )
    return None


def _in_scope(path: str, scope: Optional[str]) -> bool:
    """Would a scan restricted to *scope* have had to look at *path*?

    No scope means the whole repository, so everything is in it. A glob is
    matched the way the tools match one: against the full repo-relative path
    and against the bare basename, since ``*.py`` is meant to mean "any Python
    file", not "a Python file at the root".
    """
    if not scope:
        return True
    from fnmatch import fnmatch

    return fnmatch(path, scope) or fnmatch(path, f"*/{scope}")


def working_tree_state(
    index, *, scope: Optional[str] = None, freshness: Optional[str] = None
) -> Optional[dict]:
    """Scope-level working-tree state behind an absence claim (#377 item 5).

    Git HEAD can sit still while the tree holds a modified file, a brand-new
    untracked implementation, a deletion, or a rename into or out of scope. A
    zero-result scan has no returned file to hang per-file freshness on, so the
    scope needs a state of its own:

        clean                nothing uncommitted anywhere
        dirty_outside_scope  uncommitted work exists, none of it in this scope
        dirty_in_scope       uncommitted work exists inside this scope
        unknown              the tree could not be read
        not_applicable       the subject has no revision control at all

    Only `dirty_in_scope` can block, and only when at least one of those files
    is ALSO missing from the index or older in it than on disk. A watcher that
    has already re-indexed the edit leaves the corpus current, and refusing
    there would fire on every developer with unsaved work in a repo whose index
    is up to date — a false alarm that teaches people to ignore the signal.
    """
    if freshness == "not_tracked":
        return {"state": "not_applicable", "blocks": False}
    source_root = getattr(index, "source_root", "") or None
    reading = _working_tree_reading(source_root)
    if reading is None:
        return {"state": "unknown", "blocks": False}
    _fp, dirty = reading
    if not dirty:
        return {"state": "clean", "blocks": False}
    in_scope = [p for p in dirty if _in_scope(p, scope)]
    if not in_scope:
        return {
            "state": "dirty_outside_scope",
            "files_dirty": len(dirty),
            "blocks": False,
        }
    unreflected = _unreflected_in_index(index, source_root, in_scope)
    state = {
        "state": "dirty_in_scope",
        "files_dirty": len(dirty),
        "files_dirty_in_scope": len(in_scope),
        "files_not_in_index": len(unreflected),
        "blocks": bool(unreflected),
    }
    if unreflected:
        state["sample"] = sorted(unreflected)[:5]
    return state


def _unreflected_in_index(index, source_root: Optional[str], paths) -> list:
    """Of *paths*, those the index does not hold at their current content.

    A path the index never saw, or holds at an older mtime than the file on
    disk, is a real gap: the scan could not have matched what is in it. A path
    the index already re-read is not, however dirty git considers it.
    """
    mtimes = getattr(index, "file_mtimes", None) or {}
    root = Path(source_root) if source_root else None
    missing = []
    for rel in paths:
        indexed_ns = mtimes.get(rel)
        if indexed_ns is None:
            missing.append(rel)
            continue
        if root is None:
            continue
        try:
            disk = root / rel
            if not disk.exists():
                missing.append(rel)  # deleted under the index
            elif disk.stat().st_mtime_ns > int(indexed_ns):
                missing.append(rel)
        except (OSError, TypeError, ValueError):
            missing.append(rel)
    return missing


def moved_during_scan(before: Optional[dict], index, *, result_count: int) -> Optional[str]:
    """Did the subject move between the start of this scan and now (#377 item 6)?

    A search can start against one state and finish after the source or the
    index has changed: a concurrent edit, a watcher reindex, an incremental
    save, a new generation published, or simply a long scan crossing a rebuild.
    A citable current-state negative requires the two readings to match.

    Checked only for a zero-result scan. A scan that RETURNED rows read them
    out of a generation that really held them, and the freshness channels
    already disclose that the tree moved underneath. It is the claim that
    nothing exists which cannot survive the subject changing mid-read, and
    restricting the check there is what makes the fresh HEAD read affordable.
    """
    if result_count != 0 or not before:
        return None
    return changed(before, capture(index, fresh_head=True), when=WHEN_SCANNING)


def revalidate_verdict(verdict: Optional[dict], reason: str) -> None:
    """Apply a failed revalidation to a verdict being replayed from cache.

    Disclose on every state, refuse only the absence CLAIM — the same shape as
    the stale, truncated, rebuilding and partial gates. Results a cached `ok`
    scan returned were really in the index at that generation and are still the
    best available answer; only the claim that nothing exists is unfounded.

    Any evidence token minted on the earlier pass is stripped, because it was
    issued against the state that just failed to hold.
    """
    if not isinstance(verdict, dict):
        return
    verdict["revalidated"] = {"stale_cache": True, "reason": reason}
    verdict.pop("evidence_ref", None)
    verdict.pop("absence_citable", None)
    verdict.pop("absence_blocked_by", None)
    if verdict.get("state") == "absent":
        verdict["state"] = "degraded"
        verdict["note"] = (
            f"This result was replayed from cache, and {reason}. Absence is NOT "
            "proven against the current state; re-run the search to get a scan of it."
        )
