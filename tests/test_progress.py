"""Tests for ProgressReporter and make_progress_notify."""

import asyncio
import threading
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from jcodemunch_mcp.progress import ProgressReporter, make_progress_notify


# ---------------------------------------------------------------------------
# ProgressReporter: basic lifecycle
# ---------------------------------------------------------------------------


class TestProgressReporterNoOp:
    """No-op when notify is None."""

    def test_none_notify_does_not_raise(self):
        r = ProgressReporter(None, "Index")
        r.start(total=10)
        r.update(5, 10, "file.py")
        r.finish("Done")


class TestProgressReporterStart:
    """start() emits 0% notification."""

    def test_start_emits_zero(self):
        calls = []
        r = ProgressReporter(lambda p, t, m: calls.append((p, t, m)), "Index")
        r.start(total=100, detail="Preparing")

        assert len(calls) == 1
        assert calls[0][0] == 0.0
        assert calls[0][1] == 1.0
        assert "Index" in calls[0][2]
        assert "0.0%" in calls[0][2] or "  0.0%" in calls[0][2]


class TestProgressReporterUpdate:
    """update() emits monotonically increasing progress."""

    def test_update_emits_progress(self):
        calls = []
        r = ProgressReporter(lambda p, t, m: calls.append((p, t, m)), "Index",
                             min_step=0.0, min_interval=0.0)
        r.start(total=10)
        r.update(3, 10, "file_a.py")
        r.update(7, 10, "file_b.py")

        assert len(calls) == 3  # start + 2 updates
        assert calls[1][0] == pytest.approx(0.3)
        assert calls[2][0] == pytest.approx(0.7)
        assert "3/10" in calls[1][2]
        assert "7/10" in calls[2][2]


class TestProgressReporterMonotonic:
    """Progress never goes backwards."""

    def test_backwards_update_is_dropped(self):
        calls = []
        r = ProgressReporter(lambda p, t, m: calls.append((p, t, m)), "Index",
                             min_step=0.0, min_interval=0.0)
        r.start(total=10)
        r.update(5, 10, "a")
        r.update(3, 10, "b")  # backwards — should be silently dropped
        r.update(8, 10, "c")

        # start + update(5) + update(8) = 3 calls; update(3) dropped
        assert len(calls) == 3
        assert calls[1][0] == pytest.approx(0.5)
        assert calls[2][0] == pytest.approx(0.8)


class TestProgressReporterFinish:
    """finish() emits 100%."""

    def test_finish_emits_one(self):
        calls = []
        r = ProgressReporter(lambda p, t, m: calls.append((p, t, m)), "Index")
        r.start(total=10)
        r.update(5, 10, "mid")
        r.finish("Complete")

        last = calls[-1]
        assert last[0] == 1.0
        assert "Complete" in last[2]


class TestProgressReporterDoubleFinish:
    """Second finish() is a no-op."""

    def test_double_finish_ignored(self):
        calls = []
        r = ProgressReporter(lambda p, t, m: calls.append((p, t, m)), "Index")
        r.start(total=10)
        r.finish("Done")
        r.finish("Again")

        finish_calls = [c for c in calls if c[0] == 1.0]
        assert len(finish_calls) == 1


class TestProgressReporterUpdateAfterFinish:
    """Updates after finish() are ignored."""

    def test_update_after_finish_ignored(self):
        calls = []
        r = ProgressReporter(lambda p, t, m: calls.append((p, t, m)), "Index")
        r.start(total=10)
        r.finish("Done")
        count_before = len(calls)
        r.update(5, 10, "late")
        assert len(calls) == count_before


class TestProgressReporterFormat:
    """Message format includes label, bar, percent, count, detail."""

    def test_format_contains_expected_parts(self):
        calls = []
        r = ProgressReporter(lambda p, t, m: calls.append((p, t, m)), "Embed", bar_width=10,
                             min_step=0.0, min_interval=0.0)
        r.start(total=200)
        r.update(100, 200, "MySymbol")

        msg = calls[-1][2]
        assert "Embed" in msg
        assert "[" in msg and "]" in msg
        assert "50.0%" in msg
        assert "100/200" in msg
        assert "MySymbol" in msg


class TestProgressReporterThreadSafety:
    """Concurrent updates from multiple threads don't raise."""

    def test_concurrent_updates(self):
        calls = []
        lock = threading.Lock()

        def _notify(p, t, m):
            with lock:
                calls.append((p, t, m))

        r = ProgressReporter(_notify, "Index")
        r.start(total=1000)

        def worker(start, end):
            for i in range(start, end):
                r.update(i, 1000, f"item_{i}")

        threads = [threading.Thread(target=worker, args=(i * 100, (i + 1) * 100)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        r.finish("Done")

        # All calls should have monotonically non-decreasing progress values
        progress_values = [c[0] for c in calls]
        for i in range(1, len(progress_values)):
            assert progress_values[i] >= progress_values[i - 1]


# ---------------------------------------------------------------------------
# make_progress_notify
# ---------------------------------------------------------------------------


class TestMakeProgressNotifyNoContext:
    """Returns None when no request context."""

    def test_no_context_returns_none(self):
        mock_server = MagicMock()
        mock_server.request_context = property(lambda self: (_ for _ in ()).throw(LookupError))
        type(mock_server).request_context = property(lambda self: (_ for _ in ()).throw(LookupError))
        assert make_progress_notify(mock_server) is None


class TestMakeProgressNotifyNoToken:
    """Returns None when progressToken is None."""

    def test_no_token_returns_none(self):
        mock_server = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.meta.progress_token = None
        mock_server.request_context = mock_ctx
        assert make_progress_notify(mock_server) is None


class TestMakeProgressNotifyWithToken:
    """Returns a callable when progressToken is present."""

    def test_with_token_returns_callable(self):
        mock_server = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.meta.progress_token = "tok-123"
        mock_ctx.session = MagicMock()
        mock_ctx.session.send_progress_notification = AsyncMock()
        mock_server.request_context = mock_ctx

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(_make_with_loop(mock_server))
            assert result is not None
            assert callable(result)
        finally:
            loop.close()


async def _make_with_loop(server_obj):
    """Helper to call make_progress_notify inside a running event loop."""
    return make_progress_notify(server_obj)


# ---------------------------------------------------------------------------
# Integration: ProgressReporter with tools
# ---------------------------------------------------------------------------


class TestProgressCallbackWiring:
    """progress_cb parameter accepted by tool functions."""

    def test_index_folder_accepts_progress_cb(self):
        """index_folder signature includes progress_cb."""
        import inspect
        from jcodemunch_mcp.tools.index_folder import index_folder
        sig = inspect.signature(index_folder)
        assert "progress_cb" in sig.parameters

    def test_index_repo_accepts_progress_cb(self):
        """index_repo signature includes progress_cb."""
        import inspect
        from jcodemunch_mcp.tools.index_repo import index_repo
        sig = inspect.signature(index_repo)
        assert "progress_cb" in sig.parameters

    def test_embed_repo_accepts_progress_cb(self):
        """embed_repo signature includes progress_cb."""
        import inspect
        from jcodemunch_mcp.tools.embed_repo import embed_repo
        sig = inspect.signature(embed_repo)
        assert "progress_cb" in sig.parameters

    def test_index_file_accepts_progress_cb(self):
        """index_file signature includes progress_cb."""
        import inspect
        from jcodemunch_mcp.tools.index_file import index_file
        sig = inspect.signature(index_file)
        assert "progress_cb" in sig.parameters
