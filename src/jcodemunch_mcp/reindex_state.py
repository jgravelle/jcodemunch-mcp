"""Per-repo reindex state container and backpressure primitives.

Provides:
- Per-repo state with __slots__ for memory efficiency.
- Reindex lifecycle: start → done / failed.
- Query: is_any_reindex_in_progress(), get_reindex_status().
- Freshness mode: relaxed / strict (for waiting on watcher reindex to complete).
- wait_for_fresh_result() — wait for a repo's reindex to finish, return fresh result.

IMPORTANT: threading.Event.wait() must NEVER be called from async code directly.
Always use asyncio.to_thread or run inside a thread-pool thread.
"""

from __future__ import annotations

import threading
import time
from typing import Optional, NamedTuple
from dataclasses import dataclass


# ── NamedTuple for watcher changes ────────────────────────────────────────────

class WatcherChange(NamedTuple):
    """A watcher change with (change_type, path, old_hash).

    change_type: str  — "added" | "modified" | "deleted"
    path: str        — absolute file path
    old_hash: str    — content hash BEFORE the change (empty for "added")
    """
    change_type: str
    path: str
    old_hash: str = ""


# ── Per-repo state ────────────────────────────────────────────────────────────

@dataclass(slots=True)
class _RepoState:
    """Lightweight per-repo reindex state with __slots__ for memory efficiency."""
    reindexing: bool = False
    reindex_finished: bool = False
    reindex_error: Optional[str] = None
    last_reindex_start: float = 0.0
    last_reindex_done: float = 0.0
    last_result: Optional[dict] = None
    # Incremented each time a reindex starts — used by deferred summarization
    # threads to detect when a newer reindex has started (cancelling their work).
    deferred_generation: int = 0
    # Monotonic timestamp when the index first became stale; cleared on success.
    stale_since: Optional[float] = None
    # Reset to 0 on success; incremented on each failure for escalation.
    consecutive_failures: int = 0


# ── Module-level state ───────────────────────────────────────────────────────

_states_lock = threading.RLock()
_repo_states: dict[str, _RepoState] = {}
# Per-repo threading.Events for signaling reindex completion.
# Separate from _RepoState to avoid dataclass+threading.Event typing issues.
_repo_events: dict[str, threading.Event] = {}

# Freshness mode: "relaxed" (default) or "strict"
# strict = await_freshness_if_strict() blocks callers until reindex is done
_freshness_mode: dict[str, str] = {}
_DEFAULT_FRESHNESS = "relaxed"


# ── Core state access ─────────────────────────────────────────────────────────

def _get_state(repo: str) -> _RepoState:
    """Get or create the per-repo state container."""
    with _states_lock:
        if repo not in _repo_states:
            _repo_states[repo] = _RepoState()
            _repo_events[repo] = threading.Event()
            _repo_events[repo].set()  # starts signaled (not reindexing)
        return _repo_states[repo]


# ── Reindex lifecycle ─────────────────────────────────────────────────────────

def mark_reindex_start(repo: str) -> None:
    """Mark a repo as actively reindexing."""
    with _states_lock:
        state = _get_state(repo)
        state.reindexing = True
        state.reindex_finished = False
        state.reindex_error = None
        state.last_reindex_start = time.monotonic()
        state.deferred_generation += 1
        # Record when the index first became stale (don't overwrite if already set)
        if state.stale_since is None:
            state.stale_since = time.monotonic()
        _repo_events[repo].clear()


def mark_reindex_done(repo: str, result: Optional[dict] = None) -> None:
    """Mark a repo's reindex as successfully completed."""
    with _states_lock:
        state = _get_state(repo)
        state.reindexing = False
        state.reindex_finished = True
        state.reindex_error = None
        state.last_reindex_done = time.monotonic()
        state.stale_since = None          # index is now fresh
        state.consecutive_failures = 0   # reset failure counter on success
        if result is not None:
            state.last_result = result
        _repo_events[repo].set()


def mark_reindex_failed(repo: str, error: str) -> None:
    """Mark a repo's reindex as failed.

    stale_since is intentionally NOT cleared — the index IS still stale.
    consecutive_failures is incremented; error details are only surfaced
    in get_reindex_status() after the 2nd+ consecutive failure.
    """
    with _states_lock:
        state = _get_state(repo)
        state.reindexing = False
        state.reindex_finished = True
        state.reindex_error = error          # stored internally always
        state.consecutive_failures += 1
        state.last_reindex_done = time.monotonic()
        # stale_since stays set — index remains stale after a failed reindex
        _repo_events[repo].set()


# ── Query functions ──────────────────────────────────────────────────────────

def get_reindex_status(repo: str) -> dict:
    """Return _meta-ready reindex status fields for a repo.

    Returns:
        index_stale: True when actively reindexing or stale_since is set.
        reindex_in_progress: True only while actively reindexing.
        stale_since_ms: Milliseconds since index first became stale, or None.
        reindex_error: Error string (only on 2nd+ consecutive failure).
        reindex_failures: Failure count (only on 2nd+ consecutive failure).
    """
    with _states_lock:
        state = _get_state(repo)
        now = time.monotonic()
        stale_since_ms = (
            round((now - state.stale_since) * 1000)
            if state.stale_since is not None
            else None
        )
        status: dict = {
            "index_stale": state.reindexing or state.stale_since is not None,
            "reindex_in_progress": state.reindexing,
            "stale_since_ms": stale_since_ms,
        }
        # Expose error details only on 2nd+ consecutive failure (transient tolerance)
        if state.consecutive_failures >= 2 and state.reindex_error:
            status["reindex_error"] = state.reindex_error
            status["reindex_failures"] = state.consecutive_failures
        return status


def is_any_reindex_in_progress() -> bool:
    """Return True if any repo is currently being reindexed."""
    with _states_lock:
        return any(s.reindexing for s in _repo_states.values())


# ── Freshness mode ────────────────────────────────────────────────────────────

def set_freshness_mode(mode: str) -> None:
    """Set freshness mode: 'relaxed' (default) or 'strict'."""
    if mode not in ("relaxed", "strict"):
        raise ValueError(f"Invalid freshness mode: {mode!r}. Must be 'relaxed' or 'strict'.")
    with _states_lock:
        _freshness_mode["_global"] = mode


def get_freshness_mode() -> str:
    """Get the current global freshness mode."""
    with _states_lock:
        return _freshness_mode.get("_global", _DEFAULT_FRESHNESS)


def await_freshness_if_strict(repo: str, timeout_ms: int = 500) -> bool:
    """Block the caller until the repo's reindex finishes (strict mode only).

    Uses threading.Event.wait() — MUST be called from a thread-pool thread
    (via asyncio.to_thread), NEVER from the async event loop directly.

    In relaxed mode, this returns immediately without waiting.
    In strict mode, waits up to timeout_ms for reindexing to complete.
    Returns True (always — callers use _meta fields to inspect actual state).
    """
    if get_freshness_mode() != "strict":
        return True
    # Ensure the event exists (creates it if repo not seen yet)
    _get_state(repo)
    with _states_lock:
        event = _repo_events[repo]
    event.wait(timeout=timeout_ms / 1000.0)
    return True


# ── wait_for_fresh ────────────────────────────────────────────────────────────

def wait_for_fresh_result(
    repo: str,
    timeout_ms: int = 500,
) -> dict:
    """Wait for a repo's in-progress reindex to finish, then return status.

    Uses threading.Event — MUST be called from a thread-pool thread
    (via asyncio.to_thread), NEVER from the async event loop directly.

    Args:
        repo: Repository identifier.
        timeout_ms: Maximum time to wait in milliseconds (default 500).

    Returns:
        {"fresh": True, "waited_ms": 0}                              — already fresh
        {"fresh": True, "waited_ms": N}                              — waited, now fresh
        {"fresh": False, "waited_ms": N, "reason": "timeout"}        — timed out
        {"fresh": False, "waited_ms": 0, "reason": "reindex_failed",
         "reindex_error": "...", "reindex_failures": N}              — persistent failure
    """
    # Check if repo exists before creating phantom state
    with _states_lock:
        if repo not in _repo_states:
            # Never indexed — no stale data exists, so it's trivially "fresh"
            return {"fresh": True, "waited_ms": 0}
        event = _repo_events[repo]

    # Already fresh — return without waiting
    if event.is_set():
        with _states_lock:
            state = _repo_states[repo]
            if state.consecutive_failures >= 2 and state.reindex_error:
                return {
                    "fresh": False,
                    "waited_ms": 0,
                    "reason": "reindex_failed",
                    "reindex_error": state.reindex_error,
                    "reindex_failures": state.consecutive_failures,
                }
        return {"fresh": True, "waited_ms": 0}

    t0 = time.monotonic()
    signaled = event.wait(timeout=timeout_ms / 1000.0)
    waited_ms = round((time.monotonic() - t0) * 1000)

    if not signaled:
        return {"fresh": False, "waited_ms": waited_ms, "reason": "timeout"}

    with _states_lock:
        state = _repo_states.get(repo)
        if state and state.consecutive_failures >= 2 and state.reindex_error:
            return {
                "fresh": False,
                "waited_ms": waited_ms,
                "reason": "reindex_failed",
                "reindex_error": state.reindex_error,
                "reindex_failures": state.consecutive_failures,
            }
    return {"fresh": True, "waited_ms": waited_ms}
