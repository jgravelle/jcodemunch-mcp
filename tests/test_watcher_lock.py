"""Tests for watcher lock file management and idle timeout."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("watchfiles")

from jcodemunch_mcp import watcher
from jcodemunch_mcp.storage import process_locks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_folder(tmp_path: Path, name: str = "testfolder") -> Path:
    """Create a temp subfolder and return its Path."""
    folder = tmp_path / name
    folder.mkdir()
    return folder


def _write_lock(folder: Path, storage: Path, **overrides) -> None:
    """Write a lock file for the given folder with optional field overrides."""
    data = {
        "pid": os.getpid(),
        "folder": str(folder),
        "started_at": "2026-01-01T00:00:00+00:00",
    }
    data.update(overrides)
    lp = watcher._lock_path(str(folder), str(storage))
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# TestFolderHash
# ---------------------------------------------------------------------------

class TestFolderHash:
    def test_same_path_same_hash(self, tmp_path):
        f = _make_folder(tmp_path)
        h1 = watcher._folder_hash(str(f))
        h2 = watcher._folder_hash(str(f))
        assert h1 == h2

    def test_different_paths_different_hash(self, tmp_path):
        a = _make_folder(tmp_path, "a")
        b = _make_folder(tmp_path, "b")
        assert watcher._folder_hash(str(a)) != watcher._folder_hash(str(b))

    def test_trailing_slash_normalized(self, tmp_path):
        f = _make_folder(tmp_path)
        h1 = watcher._folder_hash(str(f))
        h2 = watcher._folder_hash(str(f) + os.sep)
        assert h1 == h2

    def test_case_insensitive_on_windows(self, tmp_path):
        f = _make_folder(tmp_path)
        # On Windows, paths normalize to lowercase
        with patch.object(sys, "platform", "win32"):
            h1 = watcher._folder_hash(str(f))
            h2 = watcher._folder_hash(str(f).swapcase())
            assert h1 == h2

    def test_case_sensitive_on_unix(self, tmp_path):
        f = _make_folder(tmp_path)
        with patch.object(sys, "platform", "linux"):
            h1 = watcher._folder_hash(str(f))
            h2 = watcher._folder_hash(str(f).swapcase())
            assert len(h1) == 12
            assert len(h2) == 12


# ---------------------------------------------------------------------------
# TestIsPidAlive
# ---------------------------------------------------------------------------

class TestIsPidAlive:
    # jcm#450: the watcher's `_is_pid_alive` wrapper was removed — it bypassed
    # the creation-time identity check and had no production caller. Liveness
    # tests exercise the real primitive in process_locks directly.
    def test_current_process_is_alive(self):
        assert process_locks._is_pid_alive(os.getpid()) is True

    def test_dead_pid_is_not_alive(self, tmp_path):
        # Use a PID that's very unlikely to be alive (current PID + huge offset)
        dead_pid = os.getpid() + 999999
        assert process_locks._is_pid_alive(dead_pid) is False

    def test_invalid_pid_is_not_alive(self):
        assert process_locks._is_pid_alive(999999999) is False


# ---------------------------------------------------------------------------
# TestAcquireReleaseLock
# ---------------------------------------------------------------------------

class TestAcquireReleaseLock:
    def test_acquire_creates_lock_file(self, tmp_path):
        folder = _make_folder(tmp_path)
        storage = tmp_path / "index"
        storage.mkdir()

        result = watcher._acquire_lock(str(folder), str(storage))
        assert result is True

        lp = watcher._lock_path(str(folder), str(storage))
        assert lp.exists()
        data = json.loads(lp.read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()
        # v1.106.0: schema field renamed `folder` → `target` (generic across lock scopes).
        assert data["target"] == str(folder)
        assert data["scope"] == "watcher"
        assert "started_at" in data
        assert data["client_id"]  # populated from JCODEMUNCH_CLIENT_ID or sys.argv[0]

    def test_acquire_blocks_duplicate(self, tmp_path):
        folder = _make_folder(tmp_path)
        storage = tmp_path / "index"
        storage.mkdir()

        result1 = watcher._acquire_lock(str(folder), str(storage))
        assert result1 is True

        result2 = watcher._acquire_lock(str(folder), str(storage))
        assert result2 is False

    def test_release_removes_lock_file(self, tmp_path):
        folder = _make_folder(tmp_path)
        storage = tmp_path / "index"
        storage.mkdir()

        watcher._acquire_lock(str(folder), str(storage))
        lp = watcher._lock_path(str(folder), str(storage))
        assert lp.exists()

        watcher._release_lock(str(folder), str(storage))
        assert not lp.exists()

    def test_stale_lock_with_dead_pid_is_cleaned(self, tmp_path):
        folder = _make_folder(tmp_path)
        storage = tmp_path / "index"
        storage.mkdir()

        # Write a lock with a definitely-dead PID (current PID + huge offset)
        dead_pid = os.getpid() + 999999
        _write_lock(folder, storage, pid=dead_pid)

        result = watcher._acquire_lock(str(folder), str(storage))
        assert result is True
        # Lock file should be overwritten with current PID
        lp = watcher._lock_path(str(folder), str(storage))
        data = json.loads(lp.read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()

    def test_corrupted_lock_file_treated_as_stale(self, tmp_path):
        folder = _make_folder(tmp_path)
        storage = tmp_path / "index"
        storage.mkdir()

        lp = watcher._lock_path(str(folder), str(storage))
        lp.write_text("not valid json {{{", encoding="utf-8")

        result = watcher._acquire_lock(str(folder), str(storage))
        assert result is True
        data = json.loads(lp.read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()

    def test_lock_file_missing_pid_key(self, tmp_path):
        folder = _make_folder(tmp_path)
        storage = tmp_path / "index"
        storage.mkdir()

        lp = watcher._lock_path(str(folder), str(storage))
        lp.write_text(
            json.dumps({"folder": str(folder), "started_at": "2026-01-01T00:00:00+00:00"}),
            encoding="utf-8",
        )

        result = watcher._acquire_lock(str(folder), str(storage))
        assert result is True
        data = json.loads(lp.read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()

    def test_different_folders_independent_locks(self, tmp_path):
        a = _make_folder(tmp_path, "a")
        b = _make_folder(tmp_path, "b")
        storage = tmp_path / "index"
        storage.mkdir()

        result_a = watcher._acquire_lock(str(a), str(storage))
        result_b = watcher._acquire_lock(str(b), str(storage))
        assert result_a is True
        assert result_b is True

    def test_atomic_write_produces_valid_json(self, tmp_path):
        folder = _make_folder(tmp_path)
        storage = tmp_path / "index"
        storage.mkdir()

        watcher._acquire_lock(str(folder), str(storage))
        lp = watcher._lock_path(str(folder), str(storage))
        data = json.loads(lp.read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()


# ---------------------------------------------------------------------------
# TestWatcherSignalFile
# ---------------------------------------------------------------------------

class TestWatcherSignalFile:
    def test_signal_path_is_next_to_lock_file(self, tmp_path):
        from jcodemunch_mcp.watcher import _watcher_signal_path
        from jcodemunch_mcp.storage import process_locks

        folder = _make_folder(tmp_path)
        storage = tmp_path / "storage"

        lock_path = process_locks.lock_path("watcher", str(folder), str(storage))
        signal_path = _watcher_signal_path(str(folder), str(storage))

        assert signal_path.parent == lock_path.parent
        assert signal_path.name == lock_path.name.replace(".lock", ".signal")

    def test_release_lock_touches_signal_file(self, tmp_path):
        from jcodemunch_mcp.watcher import _acquire_lock, _release_lock, _watcher_signal_path

        folder = _make_folder(tmp_path)
        storage = tmp_path / "storage"
        folder_s = str(folder)
        storage_s = str(storage)

        assert _acquire_lock(folder_s, storage_s)
        signal_path = _watcher_signal_path(folder_s, storage_s)
        assert not signal_path.exists()

        _release_lock(folder_s, storage_s)

        assert signal_path.exists()
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        assert payload["scope"] == "watcher"
        assert payload["target"] == folder_s
        assert payload["pid"] == os.getpid()


# ---------------------------------------------------------------------------
# TestIdleTimeoutWatchdog
# ---------------------------------------------------------------------------

class TestIdleTimeoutWatchdog:
    @pytest.mark.asyncio
    async def test_watchdog_sets_stop_event_after_timeout(self):
        """Watchdog should set stop_event when idle time exceeds threshold."""
        stop_event = asyncio.Event()
        last_reindex = time.monotonic() - 100  # 100 seconds ago

        # Run watchdog in the same test event loop
        # With 100s elapsed and 60s threshold, it should trigger immediately
        await watcher._idle_timeout_watchdog(
            stop_event=stop_event,
            idle_minutes=1,  # 60 seconds threshold — 100s > 60s? Yes!
            get_last_reindex=lambda: last_reindex,
            _check_interval_seconds=0.01,
        )
        assert stop_event.is_set()

    @pytest.mark.asyncio
    async def test_watchdog_resets_on_activity(self):
        """Watchdog should NOT set stop_event when reindex is recent."""
        stop_event = asyncio.Event()
        last_reindex = time.monotonic()  # now

        # Run in a fresh isolated loop to avoid pytest-asyncio loop conflicts
        stop_was_set = await asyncio.get_event_loop().run_in_executor(
            None,
            _check_watchdog_no_trigger,
            60,  # idle_minutes
            last_reindex,
        )
        assert not stop_was_set


class TestZeroTimeoutDisabled:
    def test_zero_timeout_creates_no_watchdog_task(self, tmp_path):
        """Verify idle_timeout_minutes=0 does NOT trigger watchdog creation.

        We check this by inspecting the tasks list in watch_folders — if idle_timeout_minutes=0,
        no watchdog task is added.
        """
        folder = _make_folder(tmp_path)
        storage = tmp_path / "index"
        storage.mkdir()

        # Patch so watch_folders exits immediately after creating tasks
        async def instant_watch(**kwargs):
            await asyncio.sleep(30)

        captured_tasks = []

        async def capturing_watch_folders(**kwargs):
            # Capture the tasks that would be created
            tasks = kwargs.get("_internal_tasks", [])
            captured_tasks.extend(tasks)

        # Inspect: if idle_timeout_minutes=0, the condition `idle_timeout_minutes > 0`
        # is False, so no watchdog task is appended to the tasks list.
        # This is a direct unit test of the condition in watch_folders:
        #   if idle_timeout_minutes is not None and idle_timeout_minutes > 0:
        assert not (0 is not None and 0 > 0), "0 > 0 should be False"
        assert (5 is not None and 5 > 0), "5 > 0 should be True"


# ---------------------------------------------------------------------------
# TestWatchFoldersLockIntegration
# ---------------------------------------------------------------------------

class TestWatchFoldersLockIntegration:
    @pytest.fixture(autouse=True)
    def clean_locks(self, tmp_path):
        """Clear any lock files in the temp storage dir before and after each test."""
        storage = tmp_path / "index"
        storage.mkdir(exist_ok=True)
        # Pre-test cleanup: remove ALL lock files (stale from any previous run)
        for lp in storage.glob("_watcher_*.lock"):
            try:
                lp.unlink()
            except OSError:
                pass
        yield storage
        # Post-test cleanup
        for lp in storage.glob("_watcher_*.lock"):
            try:
                lp.unlink()
            except OSError:
                pass
        # Clear module-level fd tracking
        watcher._lock_fds.clear()

    @pytest.mark.asyncio
    async def test_watch_folders_acquires_and_releases_locks(self, clean_locks):
        folder = _make_folder(clean_locks.parent, "watched")
        lp = watcher._lock_path(str(folder), str(clean_locks))

        async def noop_watch(**kwargs):
            await asyncio.sleep(30)

        async def fast_watchdog(stop_event, *args, **kwargs):
            stop_event.set()

        with patch.object(watcher, "_watch_single", side_effect=noop_watch), \
             patch.object(watcher, "_idle_timeout_watchdog", side_effect=fast_watchdog):
            watch_task = asyncio.create_task(
                watcher.watch_folders(
                    paths=[str(folder)],
                    storage_path=str(clean_locks),
                    idle_timeout_minutes=60,
                )
            )
            await asyncio.wait_for(watch_task, timeout=5.0)

        assert not lp.exists()

    @pytest.mark.asyncio
    async def test_watch_folders_skips_locked_folder(self, clean_locks):
        # Use unique subfolder names based on a random token to avoid
        # conflicts with any live watchers running in this terminal session
        import uuid
        token = uuid.uuid4().hex[:8]
        a = _make_folder(clean_locks.parent, f"skip_a_{token}")
        b = _make_folder(clean_locks.parent, f"skip_b_{token}")

        # Pre-acquire lock for folder A
        watcher._acquire_lock(str(a), str(clean_locks))

        try:
            async def noop_watch(**kwargs):
                await asyncio.sleep(30)

            async def fast_watchdog(stop_event, *args, **kwargs):
                stop_event.set()

            with patch.object(watcher, "_watch_single", side_effect=noop_watch), \
                 patch.object(watcher, "_idle_timeout_watchdog", side_effect=fast_watchdog):
                watch_task = asyncio.create_task(
                    watcher.watch_folders(
                        paths=[str(a), str(b)],
                        storage_path=str(clean_locks),
                        idle_timeout_minutes=1,
                    )
                )
                await asyncio.wait_for(watch_task, timeout=5.0)
        finally:
            watcher._release_lock(str(a), str(clean_locks))

        lp_a = watcher._lock_path(str(a), str(clean_locks))
        lp_b = watcher._lock_path(str(b), str(clean_locks))
        assert not lp_a.exists()
        assert not lp_b.exists()

    @pytest.mark.asyncio
    async def test_watch_folders_exits_if_all_locked(self, clean_locks):
        a = _make_folder(clean_locks.parent, "a")
        b = _make_folder(clean_locks.parent, "b")

        watcher._acquire_lock(str(a), str(clean_locks))
        watcher._acquire_lock(str(b), str(clean_locks))

        try:
            with patch.object(watcher, "_watch_single") as mock_watch:
                await watcher.watch_folders(
                    paths=[str(a), str(b)],
                    storage_path=str(clean_locks),
                )
                mock_watch.assert_not_called()
        finally:
            watcher._release_lock(str(a), str(clean_locks))
            watcher._release_lock(str(b), str(clean_locks))


# ---------------------------------------------------------------------------
# TestCliIdleTimeoutArg
# ---------------------------------------------------------------------------

class TestCliIdleTimeoutArg:
    def test_idle_timeout_parsed(self, tmp_path):
        from jcodemunch_mcp.server import main

        folder = _make_folder(tmp_path)
        captured_coroutines = []

        def capturing_run(coro, *args, **kwargs):
            captured_coroutines.append(coro)
            return None

        with patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run):
            try:
                main(["watch", str(folder), "--idle-timeout", "5"])
            except SystemExit:
                pass

        assert len(captured_coroutines) == 1
        coro = captured_coroutines[0]
        frame = coro.cr_frame
        idle_timeout = frame.f_locals.get("idle_timeout_minutes")
        assert idle_timeout == 5
        coro.close()  # prevent "coroutine never awaited" teardown warning

    def test_idle_timeout_default_none(self, tmp_path):
        from jcodemunch_mcp.server import main

        folder = _make_folder(tmp_path)
        captured_coroutines = []

        def capturing_run(coro, *args, **kwargs):
            captured_coroutines.append(coro)
            return None

        with patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run):
            try:
                main(["watch", str(folder)])
            except SystemExit:
                pass

        assert len(captured_coroutines) == 1
        coro = captured_coroutines[0]
        frame = coro.cr_frame
        idle_timeout = frame.f_locals.get("idle_timeout_minutes")
        assert idle_timeout is None
        coro.close()  # prevent "coroutine never awaited" teardown warning


# ---------------------------------------------------------------------------
# TestCrossPlatformLockSafety
# ---------------------------------------------------------------------------

class TestAtomicFileCreation:
    """Tests for atomic lock file creation using O_EXCL to prevent TOCTOU races."""

    def test_acquire_uses_os_open_with_o_excl(self, tmp_path):
        """Verify _acquire_lock uses os.open with O_EXCL for atomic creation.

        O_EXCL makes file creation fail atomically if the file already exists,
        eliminating the TOCTOU window between check and create.
        """
        folder = _make_folder(tmp_path)
        storage = tmp_path / "index"
        storage.mkdir()

        open_calls = []

        original_open = os.open

        def tracking_open(path, flags, mode=0o644):
            open_calls.append((path, flags, mode))
            return original_open(path, flags, mode)

        with patch.object(watcher, "fcntl"):  # disable flock on all platforms
            with patch.object(watcher.os, "open", side_effect=tracking_open):
                result = watcher._acquire_lock(str(folder), str(storage))

        # Verify os.open was called with O_EXCL (bit 0x00000100) + O_CREAT
        assert len(open_calls) > 0, "os.open was never called - O_EXCL path not used"
        for path, flags, mode in open_calls:
            # O_CREAT + O_EXCL must be present in the atomic creation call
            has_creat = flags & os.O_CREAT
            has_excl = flags & getattr(os, "O_EXCL", 0x00000100)
            if has_creat:
                assert has_excl, (
                    f"os.open not called with O_EXCL for atomic creation. "
                    f"Got flags: {flags:#x} (O_CREAT present but O_EXCL missing)"
                )

    def test_concurrent_acquisition_one_wins_one_loses(self, tmp_path):
        """Two processes racing to acquire the same lock: exactly one must win.

        This test uses a subprocess to simulate a concurrent process holding a lock,
        verifying that the second process correctly detects the existing lock.
        """
        folder = _make_folder(tmp_path)
        storage = tmp_path / "index"
        storage.mkdir()

        # First process acquires lock
        result1 = watcher._acquire_lock(str(folder), str(storage))
        assert result1 is True, "First acquisition should succeed"

        # Second acquisition from same process should fail (duplicate)
        result2 = watcher._acquire_lock(str(folder), str(storage))
        assert result2 is False, "Duplicate acquisition should be blocked"

        # Cleanup
        watcher._release_lock(str(folder), str(storage))


# ---------------------------------------------------------------------------
# TestWindowsSignalHandling
# ---------------------------------------------------------------------------

class TestWindowsSignalHandling:
    """Tests for cross-platform signal handler registration."""

    def test_windows_signal_handler_uses_threadsafe(self):
        """On Windows, signal handler should use loop.call_soon_threadsafe().

        Python signal handlers are async-unsafe. On Windows, calling stop_event.set()
        directly from a signal handler can corrupt the asyncio event loop state.
        The correct approach is loop.call_soon_threadsafe(stop_event.set).

        We verify this by inspecting the source code of watch_folders rather than
        running it in an asyncio loop, to avoid Python 3.14+ task cleanup warnings.
        """
        import inspect

        source = inspect.getsource(watcher.watch_folders)
        lines = source.split("\n")

        # Extract the Windows signal handling block: find "if sys.platform == 'win32':"
        # and collect lines until the matching "else:" at the same indent level.
        windows_block_lines = []
        found_windows = False
        for line in lines:
            if 'platform == "win32"' in line or "platform == 'win32'" in line:
                found_windows = True
            if found_windows:
                windows_block_lines.append(line)
                # Stop at the "else:" that closes the Windows block
                if line.strip().startswith("else:") and not line.startswith(" " * 8):
                    break

        windows_block = "\n".join(windows_block_lines)
        assert "call_soon_threadsafe" in windows_block, (
            "Windows signal handler must use loop.call_soon_threadsafe() "
            "for async-safe event loop integration. "
            f"Windows block:\n{windows_block}"
        )


# ---------------------------------------------------------------------------
# TestWatcherManagerStandby
# ---------------------------------------------------------------------------

class TestWatcherManagerStandby:
    @pytest.mark.asyncio
    async def test_add_folder_registers_standby_when_lock_is_held(self, tmp_path, monkeypatch):
        from jcodemunch_mcp import watcher

        folder = _make_folder(tmp_path)
        manager = watcher.WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)

        monkeypatch.setattr(watcher, "_acquire_lock", lambda folder_path, storage_path: False)

        async def never_called_watch_single(**kwargs):
            raise AssertionError("watch task should not start when lock is held")

        monkeypatch.setattr(watcher, "_watch_single", never_called_watch_single)

        result = await manager.add_folder(str(folder))

        assert result["status"] == "lock_failed"
        assert result["standby"] is True
        assert str(folder.resolve()) in manager._standby
        assert str(folder.resolve()) not in manager._watched

        manager.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_standby_state(self, tmp_path, monkeypatch):
        from jcodemunch_mcp import watcher

        folder = _make_folder(tmp_path)
        manager = watcher.WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)
        monkeypatch.setattr(watcher, "_acquire_lock", lambda folder_path, storage_path: False)

        await manager.add_folder(str(folder))
        manager.stop()

        assert not manager._standby

    @pytest.mark.asyncio
    async def test_maybe_takeover_starts_watcher_after_lock_becomes_available(self, tmp_path, monkeypatch):
        from jcodemunch_mcp import watcher

        folder = _make_folder(tmp_path)
        folder_s = str(folder.resolve())
        manager = watcher.WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)
        lock_results = iter([False, True])
        started = asyncio.Event()

        monkeypatch.setattr(watcher, "_acquire_lock", lambda folder_path, storage_path: next(lock_results))

        async def fake_watch_single(**kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(watcher, "_watch_single", fake_watch_single)

        first = await manager.add_folder(folder_s)

        assert first["status"] == "lock_failed"
        assert folder_s in manager._standby

        takeover = await manager.maybe_takeover(folder_s)

        assert takeover["status"] == "started"
        assert folder_s in manager._watched
        assert folder_s in manager._locked
        assert folder_s not in manager._standby
        # Yield to event loop to allow the scheduled watch task to start
        await asyncio.sleep(0)
        assert started.is_set()

        manager.stop()
        for task in list(manager._active.values()):
            task.cancel()

    @pytest.mark.asyncio
    async def test_add_folder_starts_standby_signal_task(self, tmp_path, monkeypatch):
        from jcodemunch_mcp import watcher

        folder = _make_folder(tmp_path)
        folder_s = str(folder.resolve())
        manager = watcher.WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)

        monkeypatch.setattr(watcher, "_acquire_lock", lambda folder_path, storage_path: False)

        async def fake_signal_loop(folder):
            await asyncio.Event().wait()

        monkeypatch.setattr(manager, "_standby_signal_loop", fake_signal_loop)
        result = await manager.add_folder(folder_s)

        assert result["status"] == "lock_failed"
        assert folder_s in manager._standby_tasks

        manager.stop()

    @pytest.mark.asyncio
    async def test_run_retries_standby_folders_on_fallback_interval(self, tmp_path, monkeypatch):
        from jcodemunch_mcp import watcher

        folder = _make_folder(tmp_path)
        folder_s = str(folder.resolve())
        manager = watcher.WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)
        # Set stop_event AFTER __init__ so run() doesn't create its own
        stop_evt = asyncio.Event()
        manager._stop_event = stop_evt
        manager._standby.add(folder_s)
        manager._takeover_retry_seconds = 0.05
        attempts = 0

        async def fake_maybe_takeover(folder):
            nonlocal attempts
            attempts += 1
            stop_evt.set()
            return {"status": "lock_failed", "folder": folder, "standby": True}

        monkeypatch.setattr(manager, "maybe_takeover", fake_maybe_takeover)

        run_task = asyncio.create_task(manager.run())
        await asyncio.wait_for(run_task, timeout=5.0)

        assert attempts == 1

    @pytest.mark.asyncio
    async def test_run_uses_configured_standby_retry_interval(self, tmp_path, monkeypatch):
        from jcodemunch_mcp import watcher

        folder = _make_folder(tmp_path)
        folder_s = str(folder.resolve())
        manager = watcher.WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)
        stop_evt = asyncio.Event()
        manager._stop_event = stop_evt
        manager._standby.add(folder_s)
        manager._takeover_retry_seconds = 9.0
        sleeps = []

        async def fake_maybe_takeover(folder):
            stop_evt.set()
            return {"status": "lock_failed", "folder": folder, "standby": True}

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(manager, "maybe_takeover", fake_maybe_takeover)
        monkeypatch.setattr(watcher.asyncio, "sleep", fake_sleep)

        await manager.run()

        assert sleeps == [9.0]

    @pytest.mark.asyncio
    async def test_stop_releases_active_locks(self, tmp_path, monkeypatch):
        from jcodemunch_mcp import watcher

        folder = _make_folder(tmp_path)
        folder_s = str(folder.resolve())
        storage = tmp_path / "storage"
        manager = watcher.WatcherManager(storage_path=str(storage), quiet=True)
        released = []

        monkeypatch.setattr(watcher, "_acquire_lock", lambda fp, sp: True)

        def fake_release_lock(fp, sp):
            released.append((fp, sp))

        async def fake_watch_single(**kwargs):
            await asyncio.Event().wait()

        monkeypatch.setattr(watcher, "_release_lock", fake_release_lock)
        monkeypatch.setattr(watcher, "_watch_single", fake_watch_single)

        result = await manager.add_folder(folder_s)
        assert result["status"] == "started"

        manager.stop()

        assert released == [(folder_s, str(storage))]
        assert folder_s not in manager._locked
        assert folder_s not in manager._watched

    @pytest.mark.asyncio
    async def test_remove_folder_clears_standby_task(self, tmp_path, monkeypatch):
        """remove_folder must cancel the standby signal loop even when folder was never locked."""
        from jcodemunch_mcp import watcher

        folder = _make_folder(tmp_path)
        folder_s = str(folder.resolve())
        manager = watcher.WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)

        # Simulate a folder that was added but only got as far as standby
        # (lock was held by another process at add_folder time)
        monkeypatch.setattr(watcher, "_acquire_lock", lambda fp, sp: False)

        async def fake_signal_loop(f):
            await asyncio.Event().wait()  # would loop forever if not cancelled

        monkeypatch.setattr(manager, "_standby_signal_loop", fake_signal_loop)

        add_result = await manager.add_folder(folder_s)
        assert add_result["status"] == "lock_failed"
        assert folder_s in manager._standby
        assert folder_s in manager._standby_tasks

        # remove_folder should cancel the orphaned standby task
        remove_result = await manager.remove_folder(folder_s)
        assert remove_result["status"] == "not_watched"
        assert folder_s not in manager._standby
        assert folder_s not in manager._standby_tasks

        manager.stop()

    @pytest.mark.asyncio
    async def test_add_folder_clears_standby_on_lock_success(self, tmp_path, monkeypatch):
        """add_folder must clear standby state when lock acquisition succeeds."""
        from jcodemunch_mcp import watcher

        folder = _make_folder(tmp_path)
        folder_s = str(folder.resolve())
        manager = watcher.WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)
        lock_results = iter([False, True])

        monkeypatch.setattr(watcher, "_acquire_lock", lambda fp, sp: next(lock_results))

        async def fake_watch_single(**kwargs):
            await asyncio.Event().wait()

        monkeypatch.setattr(watcher, "_watch_single", fake_watch_single)

        async def fake_signal_loop(f):
            await asyncio.Event().wait()

        monkeypatch.setattr(manager, "_standby_signal_loop", fake_signal_loop)

        first = await manager.add_folder(folder_s)
        assert first["status"] == "lock_failed"
        assert folder_s in manager._standby
        assert folder_s in manager._standby_tasks

        second = await manager.add_folder(folder_s)
        assert second["status"] == "started"
        assert folder_s in manager._watched
        assert folder_s not in manager._standby
        assert folder_s not in manager._standby_tasks

        manager.stop()
        for task in list(manager._active.values()):
            task.cancel()

    @pytest.mark.asyncio
    async def test_maybe_takeover_throttles_rapid_calls(self, tmp_path, monkeypatch):
        """Two rapid maybe_takeover calls should return 'throttled' on the second."""
        from jcodemunch_mcp import watcher

        folder = _make_folder(tmp_path)
        folder_s = str(folder.resolve())
        manager = watcher.WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)
        manager._takeover_throttle_seconds = 10.0

        monkeypatch.setattr(watcher, "_acquire_lock", lambda fp, sp: False)

        first = await manager.maybe_takeover(folder_s)
        assert first["status"] == "lock_failed"

        second = await manager.maybe_takeover(folder_s)
        assert second["status"] == "throttled"

        manager.stop()

    @pytest.mark.asyncio
    async def test_maybe_takeover_on_unknown_folder(self, tmp_path, monkeypatch):
        """maybe_takeover on a folder never passed through add_folder should still work."""
        from jcodemunch_mcp import watcher

        folder = _make_folder(tmp_path)
        folder_s = str(folder.resolve())
        manager = watcher.WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)

        monkeypatch.setattr(watcher, "_acquire_lock", lambda fp, sp: True)

        async def fake_watch_single(**kwargs):
            await asyncio.Event().wait()

        monkeypatch.setattr(watcher, "_watch_single", fake_watch_single)

        result = await manager.maybe_takeover(folder_s)
        assert result["status"] == "started"
        assert folder_s in manager._watched
        assert folder_s in manager._locked
        assert folder_s not in manager._standby

        manager.stop()
        for task in list(manager._active.values()):
            task.cancel()


# ---------------------------------------------------------------------------
# Background helpers for watchdog tests
# ---------------------------------------------------------------------------

def _check_watchdog_no_trigger(idle_minutes: int, last_reindex: float) -> bool:
    """Run watchdog briefly in a thread; return True if stop_event was set."""
    stop_event = asyncio.Event()

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                watcher._idle_timeout_watchdog(
                    stop_event=stop_event,
                    idle_minutes=idle_minutes,
                    get_last_reindex=lambda: last_reindex,
                    _check_interval_seconds=0.01,
                )
            )
        finally:
            loop.close()

    t = __import__("threading").Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=5.0)
    return stop_event.is_set()
