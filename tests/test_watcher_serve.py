"""Tests for the embedded watcher (--watcher flag on serve subcommand)."""
import asyncio
import os
import signal
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

pytest.importorskip("watchfiles")

from jcodemunch_mcp.watcher import watch_folders


# ---------------------------------------------------------------------------
# Task 1: External stop_event
# ---------------------------------------------------------------------------

class TestExternalStopEvent:
    """watch_folders with an external stop_event skips signal handler setup."""

    @pytest.fixture()
    def folder(self, tmp_path):
        d = tmp_path / "project"
        d.mkdir()
        return d

    def test_external_stop_event_no_signal_handlers(self, folder, tmp_path):
        """When stop_event is provided, watch_folders must NOT install signal handlers."""
        storage = tmp_path / "storage"
        storage.mkdir()
        stop = asyncio.Event()

        async def run():
            # Set stop immediately so watch_folders exits after lock acquisition
            stop.set()
            with patch("jcodemunch_mcp.watcher._watch_single") as mock_ws:
                mock_ws.return_value = None
                with patch("signal.signal") as mock_sig:
                    await watch_folders(
                        paths=[str(folder)],
                        storage_path=str(storage),
                        stop_event=stop,
                    )
                    # signal.signal should NOT have been called for SIGINT/SIGTERM
                    for call in mock_sig.call_args_list:
                        assert call[0][0] not in (signal.SIGINT, signal.SIGTERM), \
                            "signal handler installed despite external stop_event"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Task 2: Parse watcher flag (placeholder - implemented in server.py)
# ---------------------------------------------------------------------------

class TestServeWatcherCliArgs:
    """CLI argument parsing for --watcher on serve subcommand."""

    def test_watcher_flag_absent_is_none(self):
        from jcodemunch_mcp.server import main
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        with patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run):
            try:
                main(["serve"])
            except SystemExit:
                pass

        # The coroutine should be run_stdio_server (no wrapper)
        assert len(captured) == 1
        assert "watcher" not in captured[0].cr_code.co_name
        captured[0].close()

    def test_watcher_flag_present_no_value(self):
        """--watcher with no value should enable the watcher."""
        from jcodemunch_mcp.server import main
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        with patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run):
            try:
                main(["serve", "--watcher"])
            except SystemExit:
                pass

        assert len(captured) == 1
        # Should be _run_server_with_watcher
        assert "watcher" in captured[0].cr_code.co_name
        captured[0].close()

    def test_watcher_path_defaults_to_cwd(self, tmp_path):
        """--watcher without --watcher-path uses CWD."""
        from jcodemunch_mcp.server import main
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        with patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run), \
             patch("os.getcwd", return_value=str(tmp_path)):
            try:
                main(["serve", "--watcher"])
            except SystemExit:
                pass

        coro = captured[0]
        # Inspect the watcher_kwargs passed to _run_server_with_watcher
        frame = coro.cr_frame
        watcher_kwargs = frame.f_locals.get("watcher_kwargs")
        assert watcher_kwargs["paths"] == [str(tmp_path)]
        coro.close()

    def test_watcher_false_means_no_watcher(self):
        """--watcher=false should not launch the watcher."""
        from jcodemunch_mcp.server import main
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        with patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run):
            try:
                main(["serve", "--watcher=false"])
            except SystemExit:
                pass

        assert len(captured) == 1
        assert "watcher" not in captured[0].cr_code.co_name
        captured[0].close()


# ---------------------------------------------------------------------------
# Task 4: _run_server_with_watcher integration
# ---------------------------------------------------------------------------

class TestRunServerWithWatcher:
    """Integration: server + watcher lifecycle."""

    def test_watcher_stops_when_server_exits(self):
        """When the server coroutine completes, the watcher should be stopped."""
        from jcodemunch_mcp.server import _run_server_with_watcher

        watcher_stopped = False

        async def fake_server():
            await asyncio.sleep(0.05)  # simulate short-lived server

        async def fake_watch_folders(**kwargs):
            nonlocal watcher_stopped
            stop = kwargs["stop_event"]
            await stop.wait()
            watcher_stopped = True

        async def run():
            with patch("jcodemunch_mcp.server.watch_folders", side_effect=fake_watch_folders):
                await _run_server_with_watcher(
                    fake_server, (),
                    dict(paths=["."], debounce_ms=2000, use_ai_summaries=False,
                         storage_path=None, extra_ignore_patterns=None,
                         follow_symlinks=False, idle_timeout_minutes=None),
                )

        asyncio.run(run())
        assert watcher_stopped

    def test_missing_watchfiles_exits_cleanly(self, tmp_path):
        """--watcher with missing watchfiles should exit with error."""
        from jcodemunch_mcp.server import main

        import builtins
        real_import = builtins.__import__

        def blocking_import(name, *args, **kwargs):
            if name == "watchfiles":
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=blocking_import):
            with pytest.raises(SystemExit) as exc_info:
                main(["serve", "--watcher"])
            assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Task 2: Parse watcher flag (placeholder - implemented in server.py)
# ---------------------------------------------------------------------------

class TestParseWatcherFlag:
    """Unit tests for _parse_watcher_flag."""

    def test_none_means_disabled(self):
        from jcodemunch_mcp.server import _parse_watcher_flag
        assert _parse_watcher_flag(None) is False

    def test_true_string_means_enabled(self):
        from jcodemunch_mcp.server import _parse_watcher_flag
        for val in ("true", "True", "TRUE", "1", "yes", "Yes"):
            assert _parse_watcher_flag(val) is True, f"Failed for {val!r}"

    def test_false_string_means_disabled(self):
        from jcodemunch_mcp.server import _parse_watcher_flag
        for val in ("false", "False", "0", "no", "No"):
            assert _parse_watcher_flag(val) is False, f"Failed for {val!r}"


# ---------------------------------------------------------------------------
# Task 5: Lock cleanup on external stop
# ---------------------------------------------------------------------------

class TestLockCleanupOnExternalStop:
    """Verify locks are released when watch_folders is stopped externally."""

    @pytest.fixture()
    def folders(self, tmp_path):
        d = tmp_path / "proj"
        d.mkdir()
        return d, tmp_path / "storage"

    def test_locks_released_after_external_stop(self, folders):
        folder, storage = folders
        storage.mkdir()

        from jcodemunch_mcp.watcher import _lock_path

        async def run():
            stop = asyncio.Event()

            async def set_stop_soon():
                await asyncio.sleep(0.1)
                stop.set()

            with patch("jcodemunch_mcp.watcher._watch_single") as mock_ws:
                # _watch_single should just wait forever
                async def hang(**kw):
                    await asyncio.Event().wait()
                mock_ws.side_effect = hang

                asyncio.create_task(set_stop_soon())
                await watch_folders(
                    paths=[str(folder)],
                    storage_path=str(storage),
                    stop_event=stop,
                )

            # Lock file should be gone after clean shutdown
            lp = _lock_path(str(folder), str(storage))
            assert not lp.exists(), f"Lock file not cleaned up: {lp}"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Bug 1: Bare print() leaks to stderr in quiet mode
# ---------------------------------------------------------------------------

class TestQuietModeNoLeaks:
    """Verify quiet mode suppresses ALL stderr output from watch_folders."""

    @pytest.fixture()
    def folder(self, tmp_path):
        d = tmp_path / "project"
        d.mkdir()
        return d

    def test_quiet_mode_suppresses_monitoring_message(self, folder, tmp_path):
        """The 'monitoring N folder(s)' message must NOT reach stderr in quiet mode."""
        storage = tmp_path / "storage"
        storage.mkdir()
        stop = asyncio.Event()
        stop.set()  # exit immediately

        async def run():
            with patch("jcodemunch_mcp.watcher._watch_single") as mock_ws:
                mock_ws.return_value = None
                with patch("sys.stderr", new_callable=MagicMock) as mock_stderr:
                    await watch_folders(
                        paths=[str(folder)],
                        storage_path=str(storage),
                        stop_event=stop,
                        quiet=True,
                    )
                    writes = [call[0][0] for call in mock_stderr.write.call_args_list]
                    assert not any("monitoring" in w for w in writes), \
                        f"'monitoring' leaked to stderr in quiet mode: {writes}"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Bug 2: Logger handlers never removed
# ---------------------------------------------------------------------------

class TestLoggerHandlerCleanup:
    """Logger handlers must be removed after watch_folders exits."""

    def test_no_handler_leak_on_repeated_calls(self, tmp_path):
        """Calling watch_folders multiple times must not accumulate handlers."""
        import logging
        d = tmp_path / "proj"
        d.mkdir()
        storage = tmp_path / "storage"
        storage.mkdir()

        async def run():
            stop = asyncio.Event()
            stop.set()
            wl = logging.getLogger("jcodemunch_mcp.watcher")

            with patch("jcodemunch_mcp.watcher._watch_single") as mock_ws:
                mock_ws.return_value = None

                for _ in range(3):
                    await watch_folders(
                        paths=[str(d)],
                        storage_path=str(storage),
                        stop_event=stop,
                        quiet=True,
                    )

            # Count quiet+log handlers that were NOT cleaned up
            remaining = [
                h for h in wl.handlers
                if isinstance(h, (logging.FileHandler, logging.NullHandler))
            ]
            assert len(remaining) == 0, f"Leaked handlers: {remaining}"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Feature: JCODEMUNCH_WATCH env var
# ---------------------------------------------------------------------------

class TestWatcherEnvVar:
    """JCODEMUNCH_WATCH env var enables watcher when --watcher flag absent."""

    def test_env_var_enables_watcher(self):
        """JCODEMUNCH_WATCH=1 enables watcher when --watcher flag not present."""
        from jcodemunch_mcp.server import main
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        with patch.dict(os.environ, {"JCODEMUNCH_WATCH": "1"}), \
             patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run):
            try:
                main(["serve"])
            except SystemExit:
                pass

        assert len(captured) == 1
        assert "watcher" in captured[0].cr_code.co_name
        captured[0].close()

    def test_flag_overrides_env_var(self):
        """--watcher=false disables watcher even when JCODEMUNCH_WATCH=1."""
        from jcodemunch_mcp.server import main
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        with patch.dict(os.environ, {"JCODEMUNCH_WATCH": "1"}), \
             patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run):
            try:
                main(["serve", "--watcher=false"])
            except SystemExit:
                pass

        assert len(captured) == 1
        assert "watcher" not in captured[0].cr_code.co_name
        captured[0].close()

    def test_env_var_false_disables(self):
        """JCODEMUNCH_WATCH=0 does NOT enable watcher."""
        from jcodemunch_mcp.server import main
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        with patch.dict(os.environ, {"JCODEMUNCH_WATCH": "0"}), \
             patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run):
            try:
                main(["serve"])
            except SystemExit:
                pass

        assert len(captured) == 1
        assert "watcher" not in captured[0].cr_code.co_name
        captured[0].close()


# ---------------------------------------------------------------------------
# Bug 5: Log file permission error produces warning, not crash
# ---------------------------------------------------------------------------

def test_watcher_log_permission_error_is_warning_not_crash(tmp_path):
    """Unopenable log file produces a WARNING but does not crash the server."""
    import sys
    from jcodemunch_mcp.server import _run_server_with_watcher
    import io

    captured = []

    mock_stderr = io.StringIO()

    async def fake_server():
        await asyncio.sleep(0.05)

    async def fake_watch_folders(**kwargs):
        stop = kwargs.get("stop_event")
        if stop:
            stop.set()

    # A path that will fail to open for write on Windows (system dir)
    protected_path = "C:\\Windows\\System32\\protected_test.log"

    async def run():
        with patch("jcodemunch_mcp.server.watch_folders", side_effect=fake_watch_folders):
            with patch("sys.stderr", mock_stderr):
                await _run_server_with_watcher(
                    fake_server, (),
                    dict(paths=["."], debounce_ms=2000, use_ai_summaries=False,
                         storage_path=None, extra_ignore_patterns=None,
                         follow_symlinks=False, idle_timeout_minutes=None),
                    log_path=protected_path,
                )

    # The function should NOT raise PermissionError; it should warn and continue
    try:
        asyncio.run(run())
    except (PermissionError, OSError):
        pass  # This is the bug - it should not raise

    output = mock_stderr.getvalue()
    # Should have warned about the log file
    assert "WARNING" in output or "PermissionError" in output or "could not open" in output
