"""Filesystem watcher — monitors folders and triggers incremental re-indexing."""

import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, IO, Optional

from .hook_event import DEFAULT_MANIFEST_PATH, read_manifest
from .tools.index_folder import index_folder
from .tools.invalidate_cache import invalidate_cache

logger = logging.getLogger(__name__)

# Default debounce in milliseconds
DEFAULT_DEBOUNCE_MS = 200


class WatcherError(Exception):
    """Base exception for watcher errors that should not kill the embedding process."""

    pass


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
    resolved = str(Path(folder_path).resolve())
    if sys.platform == "win32":
        resolved = resolved.lower()
    return hashlib.sha256(resolved.encode()).hexdigest()[:12]


def _lock_path(folder_path: str, storage_path: Optional[str]) -> Path:
    """Return the lock file Path for a given folder."""
    return _lock_dir(storage_path) / f"_watcher_{_folder_hash(folder_path)}.lock"


def _is_pid_alive(pid: int) -> bool:
    """Return True if a process with the given PID is running."""
    if sys.platform == "win32":
        import ctypes

        class _BOOL(ctypes.Structure):
            _fields_ = [("value", ctypes.c_int)]

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle == 0:
            return False
        try:
            kernel32.CloseHandle(handle)
            return True
        except OSError:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def _acquire_lock(folder_path: str, storage_path: Optional[str]) -> bool:
    """
    Attempt to acquire an exclusive lock for the given folder.

    Uses O_EXCL for atomic file creation, eliminating the TOCTOU race window
    that exists when checking lock existence before writing. On Unix, also
    uses fcntl.flock() for OS-level advisory locking.

    Returns True if the lock was acquired, False if another watcher
    is already running (active PID).
    """
    lock_fp = _lock_path(folder_path, storage_path)

    data = {
        "pid": os.getpid(),
        "folder": folder_path,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    data_bytes = json.dumps(data).encode("utf-8")

    def _try_atomic_create() -> bool:
        """Attempt atomic lock file creation using O_EXCL. Returns True on success."""
        try:
            fd = os.open(str(lock_fp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                os.write(fd, data_bytes)
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            return False
        except OSError:
            return False

    # Step 1: Try atomic creation (fast path - no pre-check)
    if _try_atomic_create():
        # Success - apply OS-level flock on Unix
        if fcntl is not None:
            fd = os.open(str(lock_fp), os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                # Race: another process grabbed the lock between create and flock
                # (should be rare on Unix). Clean up our file.
                try:
                    lock_fp.unlink()
                except OSError:
                    pass
                return False
            _lock_fds[folder_path] = fd
        return True

    # Step 2: Lock file exists - check if it's stale or active
    try:
        data = json.loads(lock_fp.read_text(encoding="utf-8"))
        pid = data.get("pid")
        if pid is None:
            logger.info("Removing stale lock for %s (no pid key)", folder_path)
        elif _is_pid_alive(pid):
            logger.info("Watcher already running for %s (PID %s)", folder_path, pid)
            return False
        else:
            logger.info("Removing stale lock for %s (PID %s is dead)", folder_path, pid)
    except (json.JSONDecodeError, OSError):
        logger.info("Removing corrupted lock for %s", folder_path)

    # Step 3: Clean up stale lock and retry
    try:
        lock_fp.unlink()
    except OSError:
        # Couldn't delete (locked by another process on Windows, or race).
        # Proceed to retry anyway - O_EXCL will handle it.
        pass

    time.sleep(0.05)  # Brief pause to reduce collision window

    if _try_atomic_create():
        # Success on retry
        if fcntl is not None:
            fd = os.open(str(lock_fp), os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                try:
                    lock_fp.unlink()
                except OSError:
                    pass
                return False
            _lock_fds[folder_path] = fd
        return True

    # Step 4: Still can't create - either another process won the race, or
    # the OS is holding the file locked (Windows). Give up gracefully.
    logger.warning("Could not acquire lock for %s", folder_path)
    return False


def _release_lock(folder_path: str, storage_path: Optional[str]) -> None:
    """Release and remove the lock for the given folder."""
    # Release flock on Unix
    if folder_path in _lock_fds:
        try:
            os.close(_lock_fds[folder_path])
        except OSError:
            pass
        del _lock_fds[folder_path]

    # Delete lock file
    try:
        _lock_path(folder_path, storage_path).unlink()
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
) -> None:
    """Watch a single folder and re-index on changes."""
    _watcher_output(f"Watching {folder_path} (debounce={debounce_ms}ms)", quiet=quiet, log_file_handle=log_file_handle)

    # Do an initial incremental index to ensure the index is current
    _watcher_output(f"  Initial index for {folder_path}...", quiet=quiet, log_file_handle=log_file_handle)
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
        # Count initial index as activity (only if it actually did work)
        if on_reindex is not None and result.get("message") != "No changes detected":
            on_reindex()
    else:
        _watcher_output(f"  WARNING: initial index failed for {folder_path}: {result.get('error')}", quiet=quiet, log_file_handle=log_file_handle)

    try:
        from watchfiles import awatch, Change
    except ImportError as exc:
        raise ImportError(
            "watchfiles is required for the watch subcommand. "
            "Install it with: pip install 'jcodemunch-mcp[watch]'"
        ) from exc

    async for changes in awatch(
        folder_path,
        debounce=debounce_ms,
        recursive=True,
        step=200,
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
            # Map watchfiles Change enum to string labels for index_folder
            _change_map = {Change.added: "added", Change.modified: "modified", Change.deleted: "deleted"}
            watcher_changes = [
                (_change_map[ct], p) for ct, p in relevant
            ]

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
                else:
                    changed = result.get("changed", 0)
                    new = result.get("new", 0)
                    deleted = result.get("deleted", 0)
                    _watcher_output(
                        f"  Re-indexed {folder_path}: "
                        f"changed={changed} new={new} deleted={deleted} ({duration}s)",
                        quiet=quiet, log_file_handle=log_file_handle,
                    )
                    # Report re-index activity (only if it actually did work)
                    if on_reindex is not None:
                        on_reindex()
            else:
                _watcher_output(
                    f"  WARNING: re-index failed for {folder_path}: {result.get('error')}",
                    quiet=quiet, log_file_handle=log_file_handle,
                )
        except Exception as e:
            logger.exception("Re-index error for %s: %s", folder_path, e)
            _watcher_output(f"  ERROR: re-index failed for {folder_path}: {e}", quiet=quiet, log_file_handle=log_file_handle)


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

    # --- Acquire locks ---
    locked_folders: list[str] = []
    for folder in resolved[:]:  # iterate over a copy so we can modify resolved
        if _acquire_lock(folder, storage_path):
            locked_folders.append(folder)
        else:
            resolved.remove(folder)

    if not resolved:
        _watcher_output("All folders already have active watchers.", quiet=quiet, log_file_handle=None)
        return

    # --- Log file setup ---
    _this_handlers: list[logging.Handler] = []
    _watcher_logger = logging.getLogger("jcodemunch_mcp.watcher")
    _saved_propagate = _watcher_logger.propagate
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
            _watcher_output_stream: Optional[IO] = None
        else:
            _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            _watcher_logger.addHandler(_fh)
            _this_handlers.append(_fh)
            _watcher_logger.propagate = False
            # Use FileHandler's stream for _watcher_output (no separate open)
            _watcher_output_stream: Optional[IO] = _fh.stream
    elif quiet:
        _nh = logging.NullHandler()
        _watcher_logger.addHandler(_nh)
        _this_handlers.append(_nh)
        _watcher_logger.propagate = False
        _watcher_output_stream: Optional[IO] = None
    else:
        _watcher_output_stream: Optional[IO] = None

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

    # Idle timeout tracking
    last_reindex_time = time.monotonic()

    def update_reindex_time() -> None:
        nonlocal last_reindex_time
        last_reindex_time = time.monotonic()

    tasks: list[asyncio.Task[None]] = [
        asyncio.create_task(
            _watch_single(
                folder_path=folder,
                debounce_ms=debounce_ms,
                use_ai_summaries=use_ai_summaries,
                storage_path=storage_path,
                extra_ignore_patterns=extra_ignore_patterns,
                follow_symlinks=follow_symlinks,
                on_reindex=update_reindex_time,
                quiet=quiet,
                log_file_handle=_watcher_output_stream,
            ),
            name=f"watch:{folder}",
        )
        for folder in resolved
    ]

    # Optionally add idle timeout watchdog
    if idle_timeout_minutes is not None and idle_timeout_minutes > 0:
        watchdog_task = asyncio.create_task(
            _idle_timeout_watchdog(
                stop_event=stop_event,
                idle_minutes=idle_timeout_minutes,
                get_last_reindex=lambda: last_reindex_time,
            ),
            name="idle-watchdog",
        )
        tasks.append(watchdog_task)

    # Wait until stop signal or a task crashes
    done_waiter = asyncio.ensure_future(
        asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    )
    stop_waiter = asyncio.ensure_future(stop_event.wait())

    await asyncio.wait(
        [done_waiter, stop_waiter],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Clean up
    try:
        _watcher_output("\nShutting down watchers...", quiet=quiet, log_file_handle=_watcher_output_stream)
        for t in tasks:
            t.cancel()
        done_waiter.cancel()
        stop_waiter.cancel()
        # Gather all tasks including the waiter tasks to ensure they're fully cleaned up
        # before returning. This prevents Python 3.14+ "coroutine never awaited" warnings.
        await asyncio.gather(
            *tasks, done_waiter, stop_waiter, return_exceptions=True
        )
    finally:
        # Release locks
        for folder in locked_folders:
            _release_lock(folder, storage_path)
        # Print "Done." before closing handlers (stream is still open)
        _watcher_output("Done.", quiet=quiet, log_file_handle=_watcher_output_stream if log_file else None)
        # Clean up only handlers THIS invocation added
        _wl = logging.getLogger("jcodemunch_mcp.watcher")
        for h in _this_handlers:
            h.close()
            _wl.removeHandler(h)
        _wl.propagate = _saved_propagate


# ---------------------------------------------------------------------------
# worktree helpers
# ---------------------------------------------------------------------------

def _local_repo_id(folder_path: str) -> str:
    """Compute the repo identifier that index_folder would use for a local path."""
    p = Path(folder_path).resolve()
    digest = hashlib.sha1(str(p).encode("utf-8")).hexdigest()[:8]
    return f"local/{p.name}-{digest}"


def parse_git_worktrees(repo_path: str) -> set[str]:
    """Run ``git worktree list --porcelain`` and return paths of non-main worktrees.

    Skips the first entry (the main working copy) and prunable entries.
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
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
    manifest_path = DEFAULT_MANIFEST_PATH
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
        repo_id = _local_repo_id(folder)
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
        """Watch the JSONL manifest for new lines and react to create/remove events."""
        # Track file position to only read new lines
        if manifest_path.is_file():
            last_size = manifest_path.stat().st_size
        else:
            last_size = 0

        from watchfiles import awatch  # noqa: PLC0415 — lazy import (optional dep)
        async for _changes in awatch(
            str(manifest_path.parent),
            debounce=500,
            recursive=False,
            step=100,
        ):
            if not manifest_path.is_file():
                continue
            current_size = manifest_path.stat().st_size
            if current_size <= last_size:
                last_size = current_size
                continue

            # Read only new lines
            import json as _json

            with open(manifest_path) as f:
                f.seek(last_size)
                new_lines = f.read()
            last_size = current_size

            for line in new_lines.strip().splitlines():
                try:
                    entry = _json.loads(line)
                except _json.JSONDecodeError:
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
