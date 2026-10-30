"""watch-all — auto-discover every locally-indexed repo and keep it fresh.

The design deliberately stays inside the existing `WatcherManager` abstraction
instead of shelling out per-event. Discovery is registry-driven (read the
same SQLite index files jcodemunch already maintains) rather than
polling `~/.code-index/*.db` from outside.

Public surface:

    discover_local_repos(storage_path=None) -> list[str]
        Returns absolute, existing `source_root` paths for every local index.

    async watch_all(...)
        Long-running coroutine that watches every discovered repo, rediscovers
        on an interval, and shuts down cleanly on SIGINT/SIGTERM.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import IO, Optional

from . import watch_scope
from .storage import IndexStore
from .storage.sqlite_store import SQLiteIndexStore
from .watcher import (
    DEFAULT_DEBOUNCE_MS,
    WatcherManager,
    _is_wsl,
    _watch_poll_delay_ms,
    _watcher_output,
)

logger = logging.getLogger(__name__)

DEFAULT_REDISCOVER_INTERVAL_S = 30.0


def discover_local_repo_entries(
    storage_path: Optional[str] = None,
) -> "dict[str, dict]":
    """Resolved on-disk path -> that repo's `list_repos()` entry.

    THE authority for "which local repos would watch-all cover". GitHub repos
    (empty `source_root`) and indexes whose source_root no longer exists on
    disk are skipped — the latter protects against watchdog blowing up when a
    repo was deleted out from under the index.

    ⚠ The entry is carried alongside the path because it already holds
    `indexed_at` and `git_head`, which is the index half of a freshness
    comparison. Re-reading those per repo costs a SQLite open apiece, and
    resolving a repo id from a path costs a git subprocess apiece — the #353
    probe that scaled `list-repos` past 60s on many-repo hosts. They are free
    here.

    ⚠ Two source roots can resolve to one path; last entry wins, which matches
    the de-duplication `discover_local_repos` has always done.
    """
    store = IndexStore(base_path=storage_path) if storage_path else IndexStore()
    found: dict[str, dict] = {}
    for entry in store.list_repos():
        src = (entry.get("source_root") or "").strip()
        if not src:
            continue
        path = Path(src).expanduser()
        try:
            if not path.is_dir():
                continue
            found[str(path.resolve())] = entry
        except OSError:
            logger.debug("Unreachable source_root: %s", src, exc_info=True)
    return {k: found[k] for k in sorted(found)}


def discover_local_repos(storage_path: Optional[str] = None) -> list[str]:
    """Resolved on-disk paths for every locally-indexed repo.

    Derived from :func:`discover_local_repo_entries` rather than repeating its
    filtering — one authority, two shapes.
    """
    return list(discover_local_repo_entries(storage_path))


def watch_all_scope(
    storage_path: Optional[str] = None,
    *,
    skip_temp: Optional[bool] = None,
    max_idle_days: Optional[int] = None,
    entries: "Optional[dict[str, dict]]" = None,
) -> "tuple[list[str], dict[str, str]]":
    """(paths watch-all watches, {path: reason} for the ones it leaves).

    Reasons come from `watch_scope.skip_reason`: ``"temp"`` or ``"idle"``.
    `None` settings read config (`watch_all_skip_temp`,
    `watch_all_max_idle_days`). `entries` reuses a discovery the caller
    already made (`get_watch_status`), so it is not paid twice.
    """
    cfg_skip, cfg_days = watch_scope.settings()
    skip_temp = cfg_skip if skip_temp is None else skip_temp
    max_idle_days = cfg_days if max_idle_days is None else max_idle_days
    store = None
    kept: list[str] = []
    skipped: dict[str, str] = {}
    if entries is None:
        entries = discover_local_repo_entries(storage_path)
    for folder, entry in entries.items():
        db_path = None
        repo_id = (entry.get("repo") or "").strip()
        if "/" in repo_id:
            owner, name = repo_id.split("/", 1)
            try:
                if store is None:
                    store = SQLiteIndexStore(base_path=storage_path)
                db_path = store._db_path(owner, name)
            except Exception:
                logger.debug("no index path for %s", repo_id, exc_info=True)
        reason = watch_scope.skip_reason(
            folder, entry, db_path, skip_temp=skip_temp, max_idle_days=max_idle_days,
        )
        if reason is None:
            kept.append(folder)
        else:
            skipped[folder] = reason
    return kept, skipped


def discover_watch_all_repos(storage_path: Optional[str] = None) -> list[str]:
    """The repos watch-all watches: every local index minus temp and idle ones."""
    return watch_all_scope(storage_path)[0]


def _skipped_summary(skipped: "dict[str, str]") -> Optional[str]:
    if not skipped:
        return None
    temp = sum(1 for r in skipped.values() if r == watch_scope.SKIP_TEMP)
    idle = sum(1 for r in skipped.values() if r == watch_scope.SKIP_IDLE)
    _, days = watch_scope.settings()
    parts = []
    if temp:
        parts.append(f"{temp} under a temp folder (watch_all_skip_temp)")
    if idle:
        parts.append(
            f"{idle} with no file or commit change in {days} days (watch_all_max_idle_days)"
        )
    return (
        f"jcodemunch-mcp watch-all: not watching {len(skipped)} indexed repo(s): "
        + "; ".join(parts)
        + ". Their indexes stay searchable; set the named config key to change this. "
        "`watch-status` lists them."
    )


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    def _request_stop() -> None:
        if not stop.is_set():
            stop.set()

    if sys.platform == "win32":
        # add_signal_handler is not supported on Windows ProactorEventLoop.
        # KeyboardInterrupt from Ctrl-C surfaces as CancelledError in asyncio.run().
        return
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError):
            logger.debug("Could not install handler for %s", sig, exc_info=True)


async def watch_all(
    debounce_ms: int = DEFAULT_DEBOUNCE_MS,
    use_ai_summaries: bool = True,
    context_providers: bool = True,
    storage_path: Optional[str] = None,
    extra_ignore_patterns: Optional[list[str]] = None,
    follow_symlinks: bool = False,
    rediscover_interval_s: float = DEFAULT_REDISCOVER_INTERVAL_S,
    quiet: bool = False,
    log_file_handle: Optional[IO] = None,
) -> None:
    """Watch every locally-indexed repo; rediscover on an interval.

    Repos added to the registry while running are picked up on the next
    rediscovery pass. Repos whose source_root disappears are dropped.
    """
    manager = WatcherManager(
        debounce_ms=debounce_ms,
        use_ai_summaries=use_ai_summaries,
        context_providers=context_providers,
        storage_path=storage_path,
        extra_ignore_patterns=extra_ignore_patterns,
        follow_symlinks=follow_symlinks,
        quiet=quiet,
        log_file_handle=log_file_handle,
    )

    # WSL forces watchfiles into polling (inotify is unreliable across the
    # boundary), which re-stats every watched tree on an interval and can peg the
    # CPU on a many-repo host (#356). Surface the levers once at startup so the
    # user isn't left guessing why the daemon is busy.
    if _is_wsl():
        _watcher_output(
            "jcodemunch-mcp watch-all: WSL detected -> watchfiles is polling "
            f"(every {_watch_poll_delay_ms()}ms). To cut CPU: raise "
            "JCODEMUNCH_WATCH_POLL_DELAY_MS, or for repos on the Linux filesystem "
            "set WATCHFILES_FORCE_POLLING=false to use native inotify (near-zero "
            "idle CPU). Repos under /mnt/* need polling; moving them onto the "
            "Linux filesystem is faster all round.",
            quiet=quiet, log_file_handle=log_file_handle,
        )

    stop_event = asyncio.Event()
    try:
        loop = asyncio.get_running_loop()
        _install_signal_handlers(loop, stop_event)
    except RuntimeError:
        pass

    async def _rediscover_loop() -> None:
        while not stop_event.is_set():
            try:
                discovered = set(discover_watch_all_repos(storage_path))
                watched = set(manager.list_folders())
                for folder in sorted(discovered - watched):
                    await manager.add_folder(folder)
                for folder in sorted(watched - discovered):
                    await manager.remove_folder(folder)
            except Exception:
                logger.warning("rediscover pass failed", exc_info=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=rediscover_interval_s)
            except asyncio.TimeoutError:
                continue

    # Seed discovery before run() starts so initial repos are watched
    # by the time the caller sees log lines.
    initial, skipped = watch_all_scope(storage_path)
    for folder in initial:
        await manager.add_folder(folder)
    skipped_line = _skipped_summary(skipped)
    if skipped_line:
        _watcher_output(skipped_line, quiet=quiet, log_file_handle=log_file_handle)

    # watch-all is a long-lived foreground daemon: it indexes, then blocks
    # watching for edits. Say so once up front so the post-index silence isn't
    # mistaken for a hang, and point at the background-service alternative.
    if initial:
        _watcher_output(
            f"jcodemunch-mcp watch-all: watching {len(initial)} repo(s). It stays "
            "running and re-indexes on every file change. Press Ctrl+C to stop. "
            "To run it in the background as a login service instead: "
            "jcodemunch-mcp watch-install",
            quiet=quiet, log_file_handle=log_file_handle,
        )

    rediscover_task = asyncio.create_task(_rediscover_loop(), name="watch-all:rediscover")
    run_task = asyncio.create_task(manager.run(), name="watch-all:manager")

    try:
        done, pending = await asyncio.wait(
            {rediscover_task, run_task, asyncio.create_task(stop_event.wait())},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        stop_event.set()
        manager.stop()
        for folder in list(manager.list_folders()):
            try:
                await manager.remove_folder(folder)
            except Exception:
                logger.debug("remove_folder failed on shutdown: %s", folder, exc_info=True)
        for t in (rediscover_task, run_task):
            t.cancel()
            try:
                await asyncio.wait_for(t, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass


def storage_path_default() -> str:
    return os.environ.get("CODE_INDEX_PATH") or str(Path.home() / ".code-index")
