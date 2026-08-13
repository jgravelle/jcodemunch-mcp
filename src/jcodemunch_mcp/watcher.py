"""Filesystem watcher — monitors folders and triggers incremental re-indexing."""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, IO, Optional

from .hook_event import default_manifest_path, read_manifest
from .tools.index_folder import index_folder
from .tools.invalidate_cache import invalidate_cache
from .reindex_state import (
    WatcherChange,
    mark_reindex_start,
    mark_reindex_done,
    mark_reindex_failed,
)
from .storage import IndexStore
from .storage import process_locks
from .path_map import parse_path_map, remap

logger = logging.getLogger(__name__)

# Default debounce in milliseconds
DEFAULT_DEBOUNCE_MS = 200

# Poll interval (ms) used ONLY when watchfiles falls back to polling instead of
# native FS events. watchfiles auto-enables polling whenever it detects WSL
# (inotify is unreliable across the WSL boundary), and its own default of 300ms
# re-stats the entire watched tree ~3x/second per repo — pegging the CPU on a
# many-repo / large-tree host (issue #356). For a background *freshness* daemon a
# ~1s cadence is invisible, so we raise the floor and let the user tune it.
# Ignored entirely when native events are in use (every non-WSL Linux/mac/Win).
DEFAULT_WATCH_POLL_DELAY_MS = 1000


def _watch_poll_delay_ms() -> int:
    """Resolve the polling interval, honoring JCODEMUNCH_WATCH_POLL_DELAY_MS
    (then watchfiles' own WATCHFILES_POLL_DELAY_MS as a fallback the user may
    already know), else DEFAULT_WATCH_POLL_DELAY_MS. Non-positive / unparseable
    values fall back to the default."""
    for var in ("JCODEMUNCH_WATCH_POLL_DELAY_MS", "WATCHFILES_POLL_DELAY_MS"):
        raw = os.environ.get(var)
        if raw is None:
            continue
        try:
            val = int(raw)
        except ValueError:
            continue
        if val > 0:
            return val
    return DEFAULT_WATCH_POLL_DELAY_MS


def _is_wsl() -> bool:
    """True when running under the Windows Subsystem for Linux (watchfiles polls
    here regardless of where the repo lives)."""
    if sys.platform != "linux":
        return False
    try:
        with open("/proc/version", "rt", encoding="utf-8", errors="ignore") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


class WatcherError(Exception):
    """Base exception for watcher errors that should not kill the embedding process."""

    pass


class WatcherDependencyError(WatcherError):
    """A required watcher dependency is missing (e.g. the `watch` extra).

    Non-transient: the per-repo task cannot watch files until the dependency is
    installed, so the WatcherManager must NOT restart it into the same crash.
    """

    pass


def _watchfiles_missing_msg() -> str:
    """Install hint for the optional ``watch`` extra, aware of how jcm was installed.

    A bare ``pip install 'jcodemunch-mcp[watch]'`` is wrong on pipx/uv installs
    (no reachable pip) — the same blind spot fixed for ``upgrade`` in #357. Lazy
    import keeps the core watcher free of a hard dependency on the CLI package,
    and falls back to the canonical pip form if detection is unavailable.
    """
    try:
        from .cli.upgrade import watch_extra_install_command

        cmd = watch_extra_install_command()
    except Exception:
        cmd = "pip install 'jcodemunch-mcp[watch]'"
    return f"watchfiles is required for the watch subcommand. Install it with: {cmd}"


def _require_watchfiles() -> None:
    """Raise WatcherDependencyError if the optional watcher dependency is absent.

    Called before any per-repo initial index so a missing dependency fails fast
    (and visibly) instead of running the initial reindex, marking it done, then
    crash-looping on the import afterwards (#353).
    """
    try:
        import watchfiles  # noqa: F401
    except ImportError as exc:
        raise WatcherDependencyError(_watchfiles_missing_msg()) from exc


# Platform-specific: fcntl for Unix (advisory locking)
try:
    import fcntl
except ImportError:
    fcntl = None  # Windows

# Module-level lock file descriptors (Unix flock)
_lock_fds: dict[str, int] = {}


def _watcher_output(msg: str, *, quiet: bool, log_file_handle: Optional[IO] = None) -> None:
    """Route watcher output to stderr, a log file, or nowhere."""
    if log_file_handle is not None:
        print(msg, file=log_file_handle, flush=True)
    elif not quiet:
        print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Lock file helpers
# ---------------------------------------------------------------------------

# v1.106.0: lock primitives moved to storage.process_locks so save_index and
# other write paths can share the same scheme. These thin wrappers preserve
# the original watcher-only API surface so existing callers/tests keep working.

_WATCHER_SCOPE = "watcher"


def _lock_dir(storage_path: Optional[str]) -> Path:
    """Return the directory for lock files, creating it if needed."""
    if storage_path:
        d = Path(storage_path)
    else:
        d = Path.home() / ".code-index"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _folder_hash(folder_path: str) -> str:
    """Return SHA-256 hash (first 12 hex chars) of a normalized folder path."""
    return process_locks._path_hash(folder_path)


def _lock_path(folder_path: str, storage_path: Optional[str]) -> Path:
    """Return the lock file Path for a given folder."""
    return process_locks.lock_path(_WATCHER_SCOPE, folder_path, storage_path)


def _acquire_lock(folder_path: str, storage_path: Optional[str]) -> bool:
    """Attempt to acquire an exclusive watcher-slot lock for the given folder."""
    return process_locks.acquire(_WATCHER_SCOPE, folder_path, storage_path)


def _release_lock(folder_path: str, storage_path: Optional[str]) -> None:
    """Release and remove the watcher-slot lock for the given folder."""
    process_locks.release(_WATCHER_SCOPE, folder_path, storage_path)
    _touch_watcher_signal(folder_path, storage_path)


def _watcher_signal_path(folder_path: str, storage_path: Optional[str]) -> Path:
    """Return the per-folder watcher release signal path."""
    return process_locks.lock_path(_WATCHER_SCOPE, folder_path, storage_path).with_suffix(".signal")


def _touch_watcher_signal(folder_path: str, storage_path: Optional[str]) -> None:
    """Notify standby watcher managers that a folder lock may be available."""
    signal_path = _watcher_signal_path(folder_path, storage_path)
    payload = {
        "scope": _WATCHER_SCOPE,
        "target": folder_path,
        "pid": os.getpid(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp_path = signal_path.with_name(f"{signal_path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
        tmp_path.replace(signal_path)
    except OSError:
        logger.debug("Failed to touch watcher signal for %s", folder_path, exc_info=True)
        try:
            tmp_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Idle timeout watchdog
# ---------------------------------------------------------------------------

async def _idle_timeout_watchdog(
    stop_event: asyncio.Event,
    idle_minutes: int,
    get_last_reindex: Callable[[], float],
    _check_interval_seconds: float = 30.0,
) -> None:
    """Auto-shutdown if no re-indexing activity for idle_minutes."""
    while not stop_event.is_set():
        await asyncio.sleep(_check_interval_seconds)
        if stop_event.is_set():
            break
        idle_seconds = idle_minutes * 60
        if time.monotonic() - get_last_reindex() > idle_seconds:
            logger.info("No re-indexing activity for %s minute(s) — shutting down.", idle_minutes)
            stop_event.set()
            break


# ---------------------------------------------------------------------------
# Core watching
# ---------------------------------------------------------------------------

async def _initial_index(
    *,
    folder_path: str,
    repo_id: str,
    use_ai_summaries: bool,
    storage_path: Optional[str],
    extra_ignore_patterns: Optional[list[str]],
    follow_symlinks: bool,
    on_reindex: Optional[Callable[[], None]],
    quiet: bool,
    log_file_handle: Optional[IO],
    build_hash_cache: Callable[[], None],
) -> None:
    """Run a watch task's initial incremental index and record its outcome.

    Extracted from ``_watch_single`` in v1.108.189 so the skip path (#384)
    is a branch around one call rather than a second copy of this body.
    Caller is responsible for ``mark_reindex_start``.
    """
    try:
        result = await asyncio.to_thread(
            index_folder,
            path=folder_path,
            use_ai_summaries=use_ai_summaries,
            storage_path=storage_path,
            extra_ignore_patterns=extra_ignore_patterns,
            follow_symlinks=follow_symlinks,
            incremental=True,
        )
        if result.get("success"):
            msg = result.get("message", f"{result.get('symbol_count', '?')} symbols")
            _watcher_output(f"  Indexed {folder_path}: {msg} ({result.get('duration_seconds', '?')}s)", quiet=quiet, log_file_handle=log_file_handle)
            # Build hash cache from the index we just created/updated
            build_hash_cache()
            mark_reindex_done(repo_id, result)
            # Count initial index as activity (only if it actually did work)
            if on_reindex is not None and result.get("message") != "No changes detected":
                on_reindex()
        else:
            _watcher_output(f"  WARNING: initial index failed for {folder_path}: {result.get('error')}", quiet=quiet, log_file_handle=log_file_handle)
            mark_reindex_failed(repo_id, result.get("error", "unknown error"))
    except Exception as exc:
        mark_reindex_failed(repo_id, str(exc))
        raise


async def _watch_single(
    folder_path: str,
    debounce_ms: int,
    use_ai_summaries: bool,
    storage_path: Optional[str],
    extra_ignore_patterns: Optional[list[str]],
    follow_symlinks: bool,
    on_reindex: Optional[Callable[[], None]] = None,
    quiet: bool = False,
    log_file_handle: Optional[IO] = None,
    skip_initial_index: bool = False,
    record_index_ready: bool = False,
) -> None:
    """Watch a single folder and re-index on changes.

    ``skip_initial_index`` suppresses the initial incremental index for the
    cases where the caller has *already* indexed this exact folder (#384, #388).
    The hash cache is still built from the on-disk index, so change detection
    is unaffected — only the redundant second full pass is skipped.

    ``record_index_ready`` says whether this task writes the reindex record for
    that caller's work. It is a SEPARATE flag from ``skip_initial_index``
    because the two skipping callers differ (v1.108.191):

    * the ``index_folder`` tool indexes but writes no reindex record, so the
      watcher records readiness on its behalf or ``get_watch_status`` never
      learns the index is current;
    * ``ensure_indexed`` writes a real record carrying the real result, and a
      synthetic one written over it would replace a measurement with a
      placeholder.
    """
    _watcher_output(f"Watching {folder_path} (debounce={debounce_ms}ms)", quiet=quiet, log_file_handle=log_file_handle)

    # Compute repo identifier for memory hash cache and reindex state.
    # _local_repo_id returns "local/name-hash" — the full identifier for reindex_state.
    # IndexStore.load_index(owner, name) requires the split components.
    _pairs = parse_path_map()
    store = IndexStore(base_path=storage_path)
    repo_id = _local_repo_id(remap(folder_path, _pairs, reverse=True), store=store)
    _repo_owner, _repo_store_name = repo_id.split("/", 1)

    # Validate the watcher dependency BEFORE the initial index. If watchfiles is
    # missing, mark the repo failed (fatal) and abort now — previously the import
    # check ran AFTER the initial index + mark_reindex_done, so a missing
    # dependency left the index marked healthy and the task crash-looped through
    # the initial reindex on every restart (#353).
    try:
        _require_watchfiles()
    except WatcherDependencyError as exc:
        mark_reindex_failed(repo_id, str(exc), fatal=True)
        _watcher_output(
            f"  FATAL: cannot watch {folder_path}: {exc}",
            quiet=quiet,
            log_file_handle=log_file_handle,
        )
        raise

    # Memory hash cache: rel_path -> content hash (for WatcherChange old_hash passthrough)
    _hash_cache: dict[str, str] = {}
    _hash_cache_built = False

    def _build_hash_cache() -> None:
        """Build the memory hash cache from the on-disk index."""
        nonlocal _hash_cache_built
        _hash_cache.clear()
        idx = store.load_index(_repo_owner, _repo_store_name)
        if idx and idx.file_hashes:
            _hash_cache.update(idx.file_hashes)
        _hash_cache_built = True

    # Do an initial incremental index to ensure the index is current.
    # ...unless the caller just did it (#384). Indexing the same tree twice for
    # one user-visible call is pure waste, and it was silent waste: this pass
    # logs below WARNING and carries no progress reporter.
    if skip_initial_index:
        _watcher_output(
            f"  Skipping initial index for {folder_path} (caller indexed it)",
            quiet=quiet, log_file_handle=log_file_handle,
        )
        # The hash cache still has to be built, or every subsequent
        # WatcherChange loses its old_hash passthrough.
        _build_hash_cache()
        if record_index_ready:
            # The caller indexed but recorded nothing (the index_folder tool
            # writes no reindex record), and get_watch_status has no other way
            # to learn the index is current. Recording who made it fresh keeps
            # the claim checkable rather than presenting the caller's work as
            # the watcher's own.
            mark_reindex_done(repo_id, {
                "success": True,
                "message": "initial index skipped; folder was indexed by the caller",
                "skipped_initial_index": True,
            })
    else:
        _watcher_output(f"  Initial index for {folder_path}...", quiet=quiet, log_file_handle=log_file_handle)
        mark_reindex_start(repo_id)
        await _initial_index(
            folder_path=folder_path,
            repo_id=repo_id,
            use_ai_summaries=use_ai_summaries,
            storage_path=storage_path,
            extra_ignore_patterns=extra_ignore_patterns,
            follow_symlinks=follow_symlinks,
            on_reindex=on_reindex,
            quiet=quiet,
            log_file_handle=log_file_handle,
            build_hash_cache=_build_hash_cache,
        )

    try:
        from watchfiles import awatch, Change
    except ImportError as exc:
        raise ImportError(_watchfiles_missing_msg()) from exc

    # The watcher now blocks here until a file changes. Without a line saying so,
    # the silence after the initial index reads as a hang (reported twice).
    _watcher_output(
        f"  Now watching {folder_path} for changes (idle until you edit a file).",
        quiet=quiet, log_file_handle=log_file_handle,
    )

    async for changes in awatch(
        folder_path,
        debounce=debounce_ms,
        recursive=True,
        step=200,
        # Only consulted when watchfiles polls (e.g. under WSL); a higher delay
        # there is the difference between idle and pegged CPU (#356).
        poll_delay_ms=_watch_poll_delay_ms(),
    ):
        relevant = [
            (change_type, path)
            for change_type, path in changes
            if change_type in (Change.added, Change.modified, Change.deleted)
            and not any(
                part.startswith(".")
                for part in Path(path).relative_to(folder_path).parts
            )
        ]

        if not relevant:
            continue

        n_added = sum(1 for c, _ in relevant if c == Change.added)
        n_modified = sum(1 for c, _ in relevant if c == Change.modified)
        n_deleted = sum(1 for c, _ in relevant if c == Change.deleted)

        _watcher_output(
            f"  Changes detected in {folder_path}: "
            f"+{n_added} ~{n_modified} -{n_deleted}",
            quiet=quiet, log_file_handle=log_file_handle,
        )

        try:
            # v1.108.190 (#388, @Bortlesboat): hydrate lazily if the initial
            # index was skipped and the eager build could not read an index yet.
            # A watch task that starts alongside an external index can reach its
            # first change with nothing cached, and an empty cache silently
            # drops every old_hash rather than failing. Their PR caught this;
            # the flag (rather than `if not _hash_cache`) keeps a legitimately
            # empty index from re-reading the store on every change.
            if not _hash_cache_built:
                _build_hash_cache()
            # Map watchfiles Change enum to WatcherChange objects with old_hash from memory cache
            _change_map = {Change.added: "added", Change.modified: "modified", Change.deleted: "deleted"}
            watcher_changes: list[WatcherChange] = []
            for ct, p in relevant:
                change_type_str = _change_map[ct]
                if ct == Change.deleted:
                    # For deletions, old_hash comes from our memory cache
                    old_hash = _hash_cache.get(Path(p).relative_to(folder_path).as_posix(), "")
                elif ct == Change.modified:
                    # Use memory cache as the source of truth for old_hash.
                    # Do NOT fall back to reading the file: by the time watchfiles
                    # delivers the event, the file already has new content, so reading
                    # it would produce old_hash == new_hash and the change would be
                    # silently skipped as "unchanged" in index_folder.
                    # Sentinel "__cache_miss__" keeps use_memory_hash_cache=True (fast
                    # path active, no full-index disk load) while guaranteeing the file
                    # is re-parsed rather than skipped.
                    cached_rel = Path(p).relative_to(folder_path).as_posix()
                    old_hash = _hash_cache.get(cached_rel, "") or "__cache_miss__"
                else:
                    # For additions, no old hash
                    old_hash = ""
                watcher_changes.append(WatcherChange(change_type_str, p, old_hash))

            mark_reindex_start(repo_id)
            result = await asyncio.to_thread(
                index_folder,
                path=folder_path,
                use_ai_summaries=use_ai_summaries,
                storage_path=storage_path,
                extra_ignore_patterns=extra_ignore_patterns,
                follow_symlinks=follow_symlinks,
                incremental=True,
                changed_paths=watcher_changes,
            )
            if result.get("success"):
                duration = result.get("duration_seconds", "?")
                if result.get("message") == "No changes detected":
                    _watcher_output(f"  Re-indexed {folder_path}: no indexable changes ({duration}s)", quiet=quiet, log_file_handle=log_file_handle)
                    mark_reindex_done(repo_id, result)
                else:
                    changed = result.get("changed", 0)
                    new = result.get("new", 0)
                    deleted = result.get("deleted", 0)
                    _watcher_output(
                        f"  Re-indexed {folder_path}: "
                        f"changed={changed} new={new} deleted={deleted} ({duration}s)",
                        quiet=quiet, log_file_handle=log_file_handle,
                    )
                    mark_reindex_done(repo_id, result)
                    # Rebuild hash cache from the index that index_folder just wrote.
                    # Previously this re-read each changed file to compute the new hash,
                    # but that introduced a double-read race: if the file changed again
                    # between index_folder's read and the watcher's re-read, the cache
                    # would record the wrong hash and silently skip the next change (T6).
                    # Reading from the store is the single authoritative source of truth.
                    _build_hash_cache()
                    # Report re-index activity (only if it actually did work)
                    if on_reindex is not None:
                        on_reindex()
            else:
                _watcher_output(
                    f"  WARNING: re-index failed for {folder_path}: {result.get('error')}",
                    quiet=quiet, log_file_handle=log_file_handle,
                )
                mark_reindex_failed(repo_id, result.get("error", "unknown error"))
        except Exception as e:
            logger.exception("Re-index error for %s: %s", folder_path, e)
            _watcher_output(f"  ERROR: re-index failed for {folder_path}: {e}", quiet=quiet, log_file_handle=log_file_handle)
            mark_reindex_failed(repo_id, str(e))


# ---------------------------------------------------------------------------
# WatcherManager — dynamic folder watching with race-safe ensure_indexed
# ---------------------------------------------------------------------------

class WatcherManager:
    """Manages dynamic folder watching with race-safe reindexing.

    Attributes:
        _active: dict[str, asyncio.Task]  — folder → watch task
        _watched: set[str]                 — folders currently watched (O(1) lookup)
        _locked: set[str]                  — folders with file locks
        _pending: set[str]                 — folders being reindexed (race guard)
        _pending_lock: asyncio.Lock         — protects _pending set
    """

    def __init__(
        self,
        debounce_ms: int = DEFAULT_DEBOUNCE_MS,
        use_ai_summaries: bool = True,
        storage_path: Optional[str] = None,
        extra_ignore_patterns: Optional[list[str]] = None,
        follow_symlinks: bool = False,
        quiet: bool = False,
        log_file_handle: Optional[IO] = None,
        on_reindex: Optional[Callable[[], None]] = None,
    ) -> None:
        self._active: dict[str, asyncio.Task] = {}
        self._watched: set[str] = set()
        self._locked: set[str] = set()
        self._pending: set[str] = set()
        self._pending_results: dict[str, dict] = {}
        self._pending_lock = asyncio.Lock()
        self._condition = asyncio.Condition(self._pending_lock)
        self._debounce_ms = debounce_ms
        self._use_ai_summaries = use_ai_summaries
        self._storage_path = storage_path
        self._extra_ignore_patterns = extra_ignore_patterns
        self._follow_symlinks = follow_symlinks
        self._quiet = quiet
        self._log_file_handle = log_file_handle
        self._on_reindex = on_reindex
        self._stop_event: Optional[asyncio.Event] = None
        # Standby tracking for failover
        self._standby: set[str] = set()
        self._standby_tasks: dict[str, asyncio.Task] = {}
        self._last_takeover_attempt: dict[str, float] = {}
        self._takeover_retry_seconds = 30.0
        self._takeover_throttle_seconds = 1.0

    # ── Public API ──────────────────────────────────────────────────────────

    def is_watched(self, folder: str) -> bool:
        """O(1) check if folder is currently watched."""
        return str(Path(folder).expanduser().resolve()) in self._watched

    def list_folders(self) -> list[str]:
        """Return sorted list of watched folders."""
        return sorted(self._watched)

    # ── Standby helpers ──────────────────────────────────────────────────────

    def _mark_standby(self, folder: str) -> None:
        self._standby.add(folder)
        task = self._standby_tasks.get(folder)
        if task is None or task.done():
            self._standby_tasks[folder] = asyncio.create_task(
                self._standby_signal_loop(folder),
                name=f"watch-standby:{folder}",
            )

    async def _standby_signal_loop(self, folder: str) -> None:
        signal_path = _watcher_signal_path(folder, self._storage_path)
        signal_dir = signal_path.parent
        try:
            from watchfiles import awatch
        except ImportError:
            return

        while True:
            if self._stop_event is not None and self._stop_event.is_set():
                return
            try:
                async for changes in awatch(str(signal_dir), recursive=False, step=200):
                    if self._stop_event is not None and self._stop_event.is_set():
                        return
                    changed_paths = {str(Path(path)) for _, path in changes}
                    if str(signal_path) in changed_paths:
                        await self.maybe_takeover(folder)
                        if folder in self._watched:
                            return
            except asyncio.CancelledError:
                raise  # Propagate without logging — cancellation is normal shutdown
            except Exception:
                logger.warning(
                    "Watcher standby signal loop failed for %s, restarting in 5s",
                    folder,
                    exc_info=True,
                )
                await asyncio.sleep(5.0)

    def _clear_standby(self, folder: str) -> None:
        self._standby.discard(folder)
        task = self._standby_tasks.pop(folder, None)
        if task is not None:
            task.cancel()
        self._last_takeover_attempt.pop(folder, None)

    def _record_task_crash(self, folder: str, exc: BaseException) -> None:
        """Record a crashed per-repo watch task in reindex state (#353).

        Keyed by the same repo_id that _watch_single writes under, so
        get_watch_status surfaces the failure. A WatcherDependencyError is marked
        fatal (surfaced immediately, no restart); other crashes are transient
        (visible after the 2nd consecutive failure).
        """
        try:
            _pairs = parse_path_map()
            store = IndexStore(base_path=self._storage_path)
            repo_id = _local_repo_id(remap(folder, _pairs, reverse=True), store=store)
            mark_reindex_failed(
                repo_id,
                str(exc) or exc.__class__.__name__,
                fatal=isinstance(exc, WatcherDependencyError),
            )
        except Exception:
            logger.debug("Failed to record watcher task crash for %s", folder, exc_info=True)

    def _start_watch_task(
        self,
        folder: str,
        skip_initial_index: bool = False,
        record_index_ready: bool = False,
    ) -> asyncio.Task:
        task = asyncio.create_task(
            _watch_single(
                folder_path=folder,
                debounce_ms=self._debounce_ms,
                use_ai_summaries=self._use_ai_summaries,
                storage_path=self._storage_path,
                extra_ignore_patterns=self._extra_ignore_patterns,
                follow_symlinks=self._follow_symlinks,
                on_reindex=self._on_reindex,
                quiet=self._quiet,
                log_file_handle=self._log_file_handle,
                skip_initial_index=skip_initial_index,
                record_index_ready=record_index_ready,
            ),
            name=f"watch:{folder}",
        )
        self._active[folder] = task
        self._watched.add(folder)
        return task

    async def maybe_takeover(
        self,
        folder: str,
        *,
        skip_initial_index: bool = False,
        record_index_ready: bool = False,
    ) -> dict:
        """Try to become the active watcher for a standby folder.

        ``skip_initial_index=True`` adopts the folder without the watch task's
        own initial index, for the caller that has just indexed it (#384, #388).

        ⚠ The default is False and must stay False: the standby signal loop and
        the fallback retry loop both call this when nothing else is indexing,
        and there the initial index is the whole point of taking over.
        """
        folder = str(Path(folder).expanduser().resolve())
        if folder in self._watched:
            return {"status": "already_watched", "folder": folder}

        now = time.monotonic()
        last_attempt = self._last_takeover_attempt.get(folder, 0.0)
        if now - last_attempt < self._takeover_throttle_seconds:
            return {"status": "throttled", "folder": folder}
        self._last_takeover_attempt[folder] = now

        if not _acquire_lock(folder, self._storage_path):
            self._mark_standby(folder)
            return {"status": "lock_failed", "folder": folder, "standby": True}

        self._locked.add(folder)
        try:
            self._clear_standby(folder)
            self._start_watch_task(
                folder,
                skip_initial_index=skip_initial_index,
                record_index_ready=record_index_ready,
            )
            _watcher_output(
                f"WatcherManager: standby took over {folder}",
                quiet=self._quiet,
                log_file_handle=self._log_file_handle,
            )
            return {"status": "started", "folder": folder}
        except Exception:
            self._locked.discard(folder)
            _release_lock(folder, self._storage_path)
            raise

    # ── Folder management ────────────────────────────────────────────────────

    async def add_folder(
        self,
        folder: str,
        skip_initial_index: bool = False,
        record_index_ready: bool = False,
    ) -> dict:
        """Add a folder to watch, acquiring lock and starting watch task.

        Returns dict with 'status' and optionally 'task' or 'already_watched' key.

        ``skip_initial_index=True`` starts the watch task WITHOUT its initial
        index. Only pass it when the caller has just indexed this exact folder
        (#384) — otherwise a folder can end up watched with a stale index.
        """
        folder = str(Path(folder).expanduser().resolve())

        # Already watched — no-op
        if folder in self._watched:
            return {"status": "already_watched", "folder": folder}

        # Acquire lock
        if not _acquire_lock(folder, self._storage_path):
            self._mark_standby(folder)
            return {"status": "lock_failed", "folder": folder, "standby": True}

        self._locked.add(folder)
        self._clear_standby(folder)

        try:
            self._start_watch_task(
                folder,
                skip_initial_index=skip_initial_index,
                record_index_ready=record_index_ready,
            )
            _watcher_output(
                f"WatcherManager: started watching {folder}",
                quiet=self._quiet,
                log_file_handle=self._log_file_handle,
            )
            return {"status": "started", "folder": folder, "task": self._active[folder]}
        except Exception as exc:
            # Clean up on failure
            self._locked.discard(folder)
            _release_lock(folder, self._storage_path)
            return {"status": "error", "folder": folder, "error": str(exc)}

    async def remove_folder(self, folder: str) -> dict:
        """Stop watching a folder, cancel task, release lock.

        Returns dict with 'status' key.
        """
        folder = str(Path(folder).expanduser().resolve())

        if folder not in self._watched:
            # Also cancel any orphaned standby task so a future lock release
            # does not unexpectedly restart watching for this folder.
            self._clear_standby(folder)
            return {"status": "not_watched", "folder": folder}

        # Cancel and await task
        task = self._active.pop(folder, None)
        if task:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except asyncio.TimeoutError:
                task.cancel()
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        self._watched.discard(folder)

        # Release lock
        if folder in self._locked:
            self._locked.discard(folder)
            _release_lock(folder, self._storage_path)

        _watcher_output(
            f"WatcherManager: stopped watching {folder}",
            quiet=self._quiet,
            log_file_handle=self._log_file_handle,
        )
        return {"status": "stopped", "folder": folder}

    async def ensure_indexed(self, folder: str, **kwargs) -> dict:
        """Race-safe reindex: only one reindex runs per folder at a time.

        If folder is already being reindexed by another caller, waits for
        that reindex to complete and returns its result.

        Uses asyncio.Condition to notify waiters when reindex completes.
        """
        folder = str(Path(folder).expanduser().resolve())

        # Fast path: not pending — acquire lock, run reindex
        async with self._pending_lock:
            if folder not in self._pending:
                self._pending.add(folder)
                # Release lock while doing expensive reindex
                self._pending_lock.release()
                try:
                    result = await self._do_reindex(folder, **kwargs)
                finally:
                    # Re-acquire, publish result for waiters, then clear pending
                    await self._pending_lock.acquire()
                    self._pending_results[folder] = result
                    self._pending.discard(folder)
                    self._condition.notify_all()
                return result
            else:
                # Slow path: wait for the ongoing reindex, then return its result
                while folder in self._pending:
                    await self._condition.wait()
                return self._pending_results.get(
                    folder, {"status": "concurrent_complete", "folder": folder}
                )

    async def _do_reindex(self, folder: str, **kwargs) -> dict:
        """Perform the actual reindex operation."""
        _pairs = parse_path_map()
        store = IndexStore(base_path=self._storage_path)
        repo_id = _local_repo_id(remap(folder, _pairs, reverse=True), store=store)
        mark_reindex_start(repo_id)
        try:
            result = await asyncio.to_thread(
                index_folder,
                path=folder,
                use_ai_summaries=kwargs.get("use_ai_summaries", self._use_ai_summaries),
                storage_path=self._storage_path,
                extra_ignore_patterns=kwargs.get(
                    "extra_ignore_patterns", self._extra_ignore_patterns
                ),
                follow_symlinks=kwargs.get("follow_symlinks", self._follow_symlinks),
                incremental=True,
            )
            if result.get("success"):
                mark_reindex_done(repo_id, result)
            else:
                mark_reindex_failed(repo_id, result.get("error", "unknown error"))
            return {"status": "indexed", "folder": folder, "result": result}
        except Exception as exc:
            mark_reindex_failed(repo_id, str(exc))
            return {"status": "error", "folder": folder, "error": str(exc)}

    async def run(self) -> None:
        """Main loop: monitor for crashed tasks and restart them. Self-restarts on crash."""
        if self._stop_event is None:
            self._stop_event = asyncio.Event()

        _restart_count = 0
        _MAX_RESTARTS = 5

        while True:
            # Exit immediately if already stopped
            if self._stop_event.is_set():
                break
            try:
                while not self._stop_event.is_set():
                    # Check for crashed tasks and restart them
                    for folder in list(self._active):
                        task = self._active.get(folder)
                        if task and task.done() and not task.cancelled():
                            exc = task.exception()
                            if exc:
                                # Record the crash in reindex state so watch-status
                                # surfaces a failing/degraded watcher instead of
                                # reporting healthy while tasks crash-loop (#353).
                                self._record_task_crash(folder, exc)
                                fatal = isinstance(exc, WatcherDependencyError)
                                verb = "not restarting (fatal)" if fatal else "restarting..."
                                _watcher_output(
                                    f"WatcherManager: task crashed for {folder}: {exc}, {verb}",
                                    quiet=self._quiet,
                                    log_file_handle=self._log_file_handle,
                                )
                                task.cancel()
                                if fatal:
                                    # A missing dependency won't self-heal — stop
                                    # watching this folder rather than spin forever.
                                    self._active.pop(folder, None)
                                    self._watched.discard(folder)
                                    if folder in self._locked:
                                        self._locked.discard(folder)
                                        _release_lock(folder, self._storage_path)
                                else:
                                    self._start_watch_task(folder)
                    # Retry standby folders on fallback interval
                    for folder in list(self._standby):
                        await self.maybe_takeover(folder)
                    sleep_seconds = self._takeover_retry_seconds if self._standby else 5.0
                    await asyncio.sleep(sleep_seconds)
                # Inner loop exited normally — reset restart counter
                _restart_count = 0
            except asyncio.CancelledError:
                raise  # Propagate cancellation — signals shutdown
            except Exception as exc:
                _restart_count += 1
                _watcher_output(
                    f"WatcherManager run() crashed ({_restart_count}/{_MAX_RESTARTS}): {exc}",
                    quiet=self._quiet,
                    log_file_handle=self._log_file_handle,
                )
                if _restart_count >= _MAX_RESTARTS:
                    _watcher_output(
                        "WatcherManager run() abandoned after 5 consecutive crashes",
                        quiet=self._quiet,
                        log_file_handle=self._log_file_handle,
                    )
                    break
                await asyncio.sleep(0.1)  # Prevent spin-loop on persistent crash

            # Inner loop exited — check if this is a graceful shutdown
            if self._stop_event is not None and self._stop_event.is_set():
                break  # Stop was requested — exit run() entirely

    def stop(self) -> None:
        """Signal the manager loop to stop."""
        if self._stop_event:
            self._stop_event.set()
        for folder, task in list(self._active.items()):
            task.cancel()
            self._active.pop(folder, None)
            self._watched.discard(folder)
            if folder in self._locked:
                self._locked.discard(folder)
                _release_lock(folder, self._storage_path)
        # Clear all standby state
        for folder in list(self._standby):
            self._clear_standby(folder)


# ---------------------------------------------------------------------------
# watch_folders — thin wrapper that creates a WatcherManager
# ---------------------------------------------------------------------------

async def watch_folders(
    paths: list[str],
    debounce_ms: int = DEFAULT_DEBOUNCE_MS,
    use_ai_summaries: bool = True,
    storage_path: Optional[str] = None,
    extra_ignore_patterns: Optional[list[str]] = None,
    follow_symlinks: bool = False,
    idle_timeout_minutes: Optional[int] = None,
    stop_event: Optional[asyncio.Event] = None,
    quiet: bool = False,
    log_file: Optional[str] = None,
) -> None:
    """Watch multiple folders concurrently."""
    resolved = []
    for p in paths:
        folder = Path(p).expanduser().resolve()
        if not folder.is_dir():
            _watcher_output(f"WARNING: skipping {p} — not a directory", quiet=quiet, log_file_handle=None)
            continue
        resolved.append(str(folder))

    if not resolved:
        _watcher_output("ERROR: no valid directories to watch", quiet=quiet, log_file_handle=None)
        if stop_event is not None:
            # Embedded mode: raise exception instead of killing the server process
            raise WatcherError("No valid directories to watch")
        sys.exit(1)  # Standalone mode: exit is acceptable

    # --- Log file setup ---
    _this_handlers: list[logging.Handler] = []
    _watcher_logger = logging.getLogger("jcodemunch_mcp.watcher")
    _saved_propagate = _watcher_logger.propagate
    _watcher_output_stream: Optional[IO] = None
    if log_file:
        _log_path = log_file
        if _log_path == "auto":
            _log_path = os.path.join(tempfile.gettempdir(), f"jcw_{os.getpid()}.log")
        try:
            _fh = logging.FileHandler(_log_path, encoding="utf-8")
        except OSError as exc:
            _watcher_output(
                f"WARNING: could not open watcher log {_log_path!r}: {exc} — falling back to quiet mode",
                quiet=False,
                log_file_handle=None,
            )
            log_file = None
            _nh = logging.NullHandler()
            _watcher_logger.addHandler(_nh)
            _this_handlers.append(_nh)
            _watcher_logger.propagate = False
        else:
            _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            _watcher_logger.addHandler(_fh)
            _this_handlers.append(_fh)
            _watcher_logger.propagate = False
            _watcher_output_stream = _fh.stream
    elif quiet:
        _nh = logging.NullHandler()
        _watcher_logger.addHandler(_nh)
        _this_handlers.append(_nh)
        _watcher_logger.propagate = False

    _watcher_output(f"jcodemunch-mcp watcher: monitoring {len(resolved)} folder(s)", quiet=quiet, log_file_handle=_watcher_output_stream)

    # Handle graceful shutdown
    _external_stop = stop_event is not None
    if stop_event is None:
        stop_event = asyncio.Event()

    if not _external_stop:
        loop = asyncio.get_running_loop()
        if sys.platform == "win32":
            # Windows: signal handlers run synchronously outside the event loop.
            # Using call_soon_threadsafe ensures stop_event.set() is scheduled
            # safely on the event loop thread rather than called directly.
            def _handle_signal(sig, frame):
                loop.call_soon_threadsafe(stop_event.set)

            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)
        else:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop_event.set)

    # Idle timeout tracking (must be created before manager so on_reindex works)
    last_reindex_time = time.monotonic()

    def update_reindex_time() -> None:
        nonlocal last_reindex_time
        last_reindex_time = time.monotonic()

    # Create WatcherManager and add initial paths
    manager = WatcherManager(
        debounce_ms=debounce_ms,
        use_ai_summaries=use_ai_summaries,
        storage_path=storage_path,
        extra_ignore_patterns=extra_ignore_patterns,
        follow_symlinks=follow_symlinks,
        quiet=quiet,
        log_file_handle=_watcher_output_stream,
        on_reindex=update_reindex_time,
    )
    manager._stop_event = stop_event  # inject stop_event for run() loop

    for folder in resolved:
        await manager.add_folder(folder)

    # Early exit if no folders were successfully locked
    if not manager._watched:
        _watcher_output("All folders already have active watchers.", quiet=quiet, log_file_handle=_watcher_output_stream)
        return

    # Create manager run task for monitoring crashed tasks
    manager_task = asyncio.create_task(
        manager.run(),
        name="watcher-manager",
    )

    # Optionally add idle timeout watchdog
    watchdog_task: Optional[asyncio.Task] = None
    if idle_timeout_minutes is not None and idle_timeout_minutes > 0:
        watchdog_task = asyncio.create_task(
            _idle_timeout_watchdog(
                stop_event=stop_event,
                idle_minutes=idle_timeout_minutes,
                get_last_reindex=lambda: last_reindex_time,
            ),
            name="idle-watchdog",
        )

    # Wait until stop signal or a task crashes
    tasks_to_wait = [manager_task]
    if watchdog_task:
        tasks_to_wait.append(watchdog_task)
    done_waiter = asyncio.ensure_future(
        asyncio.wait(tasks_to_wait, return_when=asyncio.FIRST_EXCEPTION)
    )
    stop_waiter = asyncio.ensure_future(stop_event.wait())

    await asyncio.wait(
        [done_waiter, stop_waiter],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Clean up
    try:
        _watcher_output("\nShutting down watchers...", quiet=quiet, log_file_handle=_watcher_output_stream)
        manager.stop()
        # Cancel all individual watch tasks
        watch_tasks = list(manager._active.values())
        for t in watch_tasks:
            t.cancel()
        # Release all locks synchronously (original behavior)
        # This must happen BEFORE returning, not as background tasks
        for folder in list(manager._locked):
            _release_lock(folder, storage_path)
        # Cancel manager and watchdog tasks
        manager_task.cancel()
        if watchdog_task:
            watchdog_task.cancel()
        # Await all tasks for clean shutdown
        await asyncio.gather(
            *watch_tasks, manager_task,
            *([] if watchdog_task is None else [watchdog_task]),
            return_exceptions=True,
        )
    finally:
        # Print "Done." before closing handlers (stream is still open)
        _watcher_output("Done.", quiet=quiet, log_file_handle=_watcher_output_stream if log_file else None)
        # Clean up only handlers THIS invocation added
        _wl = logging.getLogger("jcodemunch_mcp.watcher")
        for h in _this_handlers:
            h.close()
            _wl.removeHandler(h)
        _wl.propagate = _saved_propagate


# ---------------------------------------------------------------------------
# One-shot sync (watch --once)
# ---------------------------------------------------------------------------

async def sync_folders(
    paths: list[str],
    use_ai_summaries: bool = True,
    storage_path: Optional[str] = None,
    extra_ignore_patterns: Optional[list[str]] = None,
    follow_symlinks: bool = False,
) -> None:
    """Index all paths once (incremental) and return immediately — no file watching."""
    resolved = []
    for p in paths:
        folder = Path(p).expanduser().resolve()
        if not folder.is_dir():
            print(f"WARNING: skipping {p} — not a directory", file=sys.stderr)
            continue
        resolved.append(str(folder))

    if not resolved:
        print("ERROR: no valid directories to sync", file=sys.stderr)
        sys.exit(1)

    _pairs = parse_path_map()
    store = IndexStore(base_path=storage_path)
    errors = 0

    for folder in resolved:
        repo_id = _local_repo_id(remap(folder, _pairs, reverse=True), store=store)
        print(f"Syncing {folder}...", file=sys.stderr)
        mark_reindex_start(repo_id)
        try:
            result = await asyncio.to_thread(
                index_folder,
                path=folder,
                use_ai_summaries=use_ai_summaries,
                storage_path=storage_path,
                extra_ignore_patterns=extra_ignore_patterns,
                follow_symlinks=follow_symlinks,
                incremental=True,
            )
            if result.get("success"):
                msg = result.get("message", f"{result.get('symbol_count', '?')} symbols")
                duration = result.get("duration_seconds", "?")
                print(f"  {folder}: {msg} ({duration}s)", file=sys.stderr)
                mark_reindex_done(repo_id, result)
            else:
                print(f"  ERROR: {folder}: {result.get('error')}", file=sys.stderr)
                mark_reindex_failed(repo_id, result.get("error", "unknown error"))
                errors += 1
        except Exception as exc:
            print(f"  ERROR: {folder}: {exc}", file=sys.stderr)
            mark_reindex_failed(repo_id, str(exc))
            errors += 1

    store.close()

    if errors:
        sys.exit(1)


# ---------------------------------------------------------------------------
# worktree helpers
# ---------------------------------------------------------------------------

def _local_repo_id(folder_path: str, store: Optional[IndexStore] = None) -> str:
    """Compute the repo identifier that index_folder would use for a local path."""
    from .storage.git_root import resolve_index_identity

    decision = resolve_index_identity(folder_path, mode="config", store=store)
    return f"{decision.owner}/{decision.name}"


def parse_git_worktrees(repo_path: str) -> set[str]:
    """Run ``git worktree list --porcelain`` and return paths of non-main worktrees.

    Skips the first entry (the main working copy) and prunable entries.
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return set()

    if result.returncode != 0:
        return set()

    worktrees: set[str] = set()
    current_path: Optional[str] = None
    is_prunable = False
    first_path: Optional[str] = None

    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            # Flush previous entry
            if current_path and current_path != first_path and not is_prunable:
                worktrees.add(current_path)
            current_path = line[len("worktree "):]
            if first_path is None:
                first_path = current_path
            is_prunable = False
        elif line.startswith("prunable"):
            is_prunable = True
        elif line == "":
            # Blank line separates entries; flush
            if current_path and current_path != first_path and not is_prunable:
                worktrees.add(current_path)
            current_path = None
            is_prunable = False

    # Flush last entry (no trailing blank line in some git versions)
    if current_path and current_path != first_path and not is_prunable:
        worktrees.add(current_path)

    return worktrees


# ---------------------------------------------------------------------------
# watch-worktrees main
# ---------------------------------------------------------------------------


async def watch_claude_worktrees(
    repos: Optional[list[str]] = None,
    poll_interval: float = 5,
    debounce_ms: int = DEFAULT_DEBOUNCE_MS,
    use_ai_summaries: bool = True,
    storage_path: Optional[str] = None,
    extra_ignore_patterns: Optional[list[str]] = None,
    follow_symlinks: bool = False,
) -> None:
    """Watch agent worktrees via JSONL manifest and/or git repo polling."""
    manifest_path = default_manifest_path()
    use_manifest = manifest_path.is_file() or not repos
    use_repos = bool(repos)

    if not use_manifest and not use_repos:
        print(
            "ERROR: no manifest file found and no --repos specified.\n"
            "Either install agent hooks (see docs) or pass --repos.",
            file=sys.stderr,
        )
        sys.exit(1)

    modes = []
    if use_manifest:
        modes.append(f"manifest ({manifest_path})")
    if use_repos:
        modes.append(f"repos ({len(repos)} repo(s), poll every {poll_interval}s)")
    print(f"jcodemunch-mcp watch-worktrees: {' + '.join(modes)}", file=sys.stderr)

    # Handle graceful shutdown
    stop_event = asyncio.Event()
    if sys.platform == "win32":
        loop = asyncio.get_running_loop()

        def _handle_signal(sig, frame):
            loop.call_soon_threadsafe(stop_event.set)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    else:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

    # Track active watchers: path -> task
    active: dict[str, asyncio.Task] = {}

    def _start_watching(folder: str) -> asyncio.Task:
        return asyncio.create_task(
            _watch_single(
                folder_path=folder,
                debounce_ms=debounce_ms,
                use_ai_summaries=use_ai_summaries,
                storage_path=storage_path,
                extra_ignore_patterns=extra_ignore_patterns,
                follow_symlinks=follow_symlinks,
            ),
            name=f"watch:{folder}",
        )

    async def _stop_watching(folder: str) -> None:
        task = active.pop(folder, None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        _pairs = parse_path_map()
        store = IndexStore(base_path=storage_path)
        repo_id = _local_repo_id(remap(folder, _pairs, reverse=True), store=store)
        try:
            result = await asyncio.to_thread(
                invalidate_cache, repo=repo_id, storage_path=storage_path,
            )
            if result.get("success"):
                print(f"  Cleaned up index for {repo_id}", file=sys.stderr)
            else:
                print(
                    f"  WARNING: could not clean up index for {repo_id}: {result.get('error')}",
                    file=sys.stderr,
                )
        except Exception as e:
            logger.warning("Failed to invalidate cache for %s: %s", repo_id, e)

    def _ensure_watching(folder: str) -> None:
        if folder not in active and Path(folder).is_dir():
            print(f"  New worktree detected: {folder}", file=sys.stderr)
            active[folder] = _start_watching(folder)

    # --- Initial discovery ---

    if use_manifest:
        for folder in sorted(read_manifest(manifest_path)):
            _ensure_watching(folder)

    if use_repos:
        for repo in repos:
            for folder in sorted(parse_git_worktrees(repo)):
                _ensure_watching(folder)

    if active:
        print(f"  Found {len(active)} existing worktree(s)", file=sys.stderr)
    else:
        print("  No existing worktrees found, waiting for new ones...", file=sys.stderr)

    # --- Manifest watcher task ---

    async def _manifest_watcher() -> None:
        """Poll the JSONL manifest for new lines and react to create/remove events."""
        # Track file position to only read new lines
        last_size = manifest_path.stat().st_size if manifest_path.is_file() else 0

        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=0.5)
                return  # stop requested
            except asyncio.TimeoutError:
                pass

            if not manifest_path.is_file():
                continue
            current_size = manifest_path.stat().st_size
            if current_size <= last_size:
                continue

            # Read only new lines
            with open(manifest_path, encoding="utf-8", errors="replace") as f:
                f.seek(last_size)
                new_lines = f.read()
            last_size = current_size

            for line in new_lines.strip().splitlines():
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                path = entry.get("path")
                event = entry.get("event")
                if not path:
                    continue
                if event == "create":
                    _ensure_watching(path)
                elif event == "remove":
                    if path in active:
                        print(f"  Worktree removed (hook): {path}", file=sys.stderr)
                        await _stop_watching(path)

    # --- Repos poll task ---

    async def _repos_poller() -> None:
        """Poll git worktree list on each repo and start/stop watchers."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
                return
            except asyncio.TimeoutError:
                pass

            current: set[str] = set()
            for repo in repos:
                current |= await asyncio.to_thread(parse_git_worktrees, repo)

            # Only manage worktrees discovered via repos mode — don't touch
            # manifest-discovered ones. We track repos-discovered paths via task names.
            repos_known = {
                folder for folder in active
                if active[folder].get_name().startswith("watch:")
            }

            for folder in sorted(current - repos_known):
                _ensure_watching(folder)

            for folder in sorted(repos_known - current):
                if folder in active:
                    print(f"  Worktree removed (git): {folder}", file=sys.stderr)
                    await _stop_watching(folder)

            # Restart crashed watcher tasks
            for folder in list(active):
                task = active[folder]
                if task.done() and not task.cancelled():
                    exc = task.exception() if not task.cancelled() else None
                    if exc:
                        print(f"  Watcher crashed for {folder}: {exc}, restarting...", file=sys.stderr)
                        active[folder] = _start_watching(folder)

    # --- Launch tasks ---

    management_tasks: list[asyncio.Task] = []

    if use_manifest:
        management_tasks.append(
            asyncio.create_task(_manifest_watcher(), name="manifest-watcher")
        )

    if use_repos:
        management_tasks.append(
            asyncio.create_task(_repos_poller(), name="repos-poller")
        )

    # Wait until stop signal or a management task finishes
    stop_waiter = asyncio.ensure_future(stop_event.wait())
    await asyncio.wait(
        [stop_waiter] + management_tasks,
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Clean up
    print("\nShutting down watch-worktrees...", file=sys.stderr)
    for t in management_tasks:
        t.cancel()
    for t in active.values():
        t.cancel()
    all_tasks = list(active.values()) + management_tasks
    await asyncio.gather(*all_tasks, return_exceptions=True)
    print("Done.", file=sys.stderr)
