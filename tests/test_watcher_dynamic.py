"""Tests for WatcherManager dynamic watching and auto-watch on demand."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from importlib.util import find_spec

# ⚠⚠ `find_spec`, NOT `pytest.importorskip`. At module scope importorskip
# RAISES during import, so this whole file would collapse to a single
# "1 skipped" line however many tests it holds -- a CI box quietly missing
# watchfiles would look like a healthy run rather than one 16 tests short.
# find_spec asks the same question without raising: every test is collected
# and skipped individually. The imports below need the package present, so
# they move under the flag.
_HAS_WATCHFILES = find_spec("watchfiles") is not None
pytestmark = pytest.mark.skipif(
    not _HAS_WATCHFILES, reason="watchfiles not installed"
)

if _HAS_WATCHFILES:
    from jcodemunch_mcp import watcher
    from jcodemunch_mcp.watcher import WatcherManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_folder(tmp_path: Path, name: str = "testfolder") -> Path:
    """Create a temp subfolder and return its Path."""
    folder = tmp_path / name
    folder.mkdir()
    return folder


# ---------------------------------------------------------------------------
# WatcherManager: Basic API
# ---------------------------------------------------------------------------

class TestManagerAddFolder:
    """test_manager_add_folder — Task created, in _watched."""

    @pytest.mark.asyncio
    async def test_add_folder_creates_task(self, tmp_path):
        """Adding a folder creates an asyncio task and marks it as watched."""
        folder = _make_folder(tmp_path)
        mgr = WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)

        # Initially not watched
        assert not mgr.is_watched(str(folder))
        assert str(folder) not in mgr._watched

        # Add folder
        result = await mgr.add_folder(str(folder))

        assert result["status"] == "started"
        assert str(folder) in mgr._watched
        assert str(folder) in mgr._active
        assert isinstance(mgr._active[str(folder)], asyncio.Task)

        # Cleanup
        await mgr.remove_folder(str(folder))


class TestManagerAddDuplicate:
    """test_manager_add_duplicate — Returns 'already watched'."""

    @pytest.mark.asyncio
    async def test_add_duplicate_returns_already_watched(self, tmp_path):
        """Adding the same folder twice returns 'already_watched' status."""
        folder = _make_folder(tmp_path)
        mgr = WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)

        # First add
        r1 = await mgr.add_folder(str(folder))
        assert r1["status"] == "started"

        # Second add
        r2 = await mgr.add_folder(str(folder))
        assert r2["status"] == "already_watched"

        # Cleanup
        await mgr.remove_folder(str(folder))


class TestManagerRemoveFolder:
    """test_manager_remove_folder — Task cancelled, lock released."""

    @pytest.mark.asyncio
    async def test_remove_folder_cancels_task_and_releases_lock(self, tmp_path):
        """Removing a folder cancels its task and releases the file lock."""
        folder = _make_folder(tmp_path)
        storage = tmp_path / "storage"
        storage.mkdir()
        mgr = WatcherManager(storage_path=str(storage), quiet=True)

        # Add folder
        r1 = await mgr.add_folder(str(folder))
        assert r1["status"] == "started"

        # Remove folder
        r2 = await mgr.remove_folder(str(folder))
        assert r2["status"] == "stopped"

        # No longer watched
        assert not mgr.is_watched(str(folder))
        assert str(folder) not in mgr._watched
        assert str(folder) not in mgr._active


class TestManagerIsWatched:
    """test_manager_is_watched — O(1) lookup correctness."""

    @pytest.mark.asyncio
    async def test_is_watched_is_o1_and_correct(self, tmp_path):
        """is_watched returns True only for watched folders."""
        folder1 = _make_folder(tmp_path, "a")
        folder2 = _make_folder(tmp_path, "b")
        mgr = WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)

        assert not mgr.is_watched(str(folder1))
        assert not mgr.is_watched(str(folder2))

        await mgr.add_folder(str(folder1))
        assert mgr.is_watched(str(folder1))
        assert not mgr.is_watched(str(folder2))

        await mgr.add_folder(str(folder2))
        assert mgr.is_watched(str(folder1))
        assert mgr.is_watched(str(folder2))

        await mgr.remove_folder(str(folder1))
        assert not mgr.is_watched(str(folder1))
        assert mgr.is_watched(str(folder2))

        await mgr.remove_folder(str(folder2))


class TestManagerListFolders:
    """test_manager_list_folders — Returns sorted list."""

    @pytest.mark.asyncio
    async def test_list_folders_returns_sorted(self, tmp_path):
        """list_folders returns a sorted list of watched folders."""
        folder_a = _make_folder(tmp_path, "aaa")
        folder_b = _make_folder(tmp_path, "bbb")
        folder_c = _make_folder(tmp_path, "ccc")
        mgr = WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)

        # Empty initially
        assert mgr.list_folders() == []

        # Add out of order
        await mgr.add_folder(str(folder_b))
        await mgr.add_folder(str(folder_a))
        await mgr.add_folder(str(folder_c))

        # Should be sorted
        assert mgr.list_folders() == sorted([str(folder_a), str(folder_b), str(folder_c)])

        # Remove one
        await mgr.remove_folder(str(folder_b))
        assert mgr.list_folders() == [str(folder_a), str(folder_c)]

        await mgr.remove_folder(str(folder_a))
        await mgr.remove_folder(str(folder_c))


# ---------------------------------------------------------------------------
# WatcherManager: ensure_indexed
# ---------------------------------------------------------------------------

class TestEnsureIndexedSingle:
    """test_ensure_indexed_single — Reindex runs, folder added to _pending then removed."""

    @pytest.mark.asyncio
    async def test_ensure_indexed_single_run(self, tmp_path):
        """ensure_indexed runs reindex and returns indexed status."""
        folder = _make_folder(tmp_path)
        mgr = WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)

        # ensure_indexed should complete without raising
        result = await mgr.ensure_indexed(str(folder))

        # Should have result with status
        assert result["status"] in ("indexed", "error")
        # Folder should not be stuck in pending
        assert str(folder) not in mgr._pending


class TestEnsureIndexedConcurrent:
    """test_ensure_indexed_concurrent — Two concurrent calls → one reindex, both get result."""

    @pytest.mark.asyncio
    async def test_ensure_indexed_concurrent(self, tmp_path):
        """Two concurrent ensure_indexed calls for the same folder only runs one reindex.

        ⚠ The interleaving is PINNED: the first reindex is held until the second
        caller is waiting on the manager's condition. Without the hold, the test
        relied on the reindex suspending before the second call ran. It failed
        `2 == 1` once in a fast tier and passed on rerun; the cause there was not
        identified. The one shape found that reproduces it is a reindex that
        completes without yielding: the second call then starts after the first
        has finished, and a second, sequential reindex is correct behavior.
        """
        folder = _make_folder(tmp_path)
        mgr = WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)

        reindex_count = 0
        started = asyncio.Event()
        release = asyncio.Event()

        original_do_reindex = mgr._do_reindex

        async def counting_reindex(f, **kwargs):
            nonlocal reindex_count
            reindex_count += 1
            started.set()
            await release.wait()
            return await original_do_reindex(f, **kwargs)

        mgr._do_reindex = counting_reindex

        first = asyncio.ensure_future(mgr.ensure_indexed(str(folder)))
        second = None
        try:
            await asyncio.wait_for(started.wait(), timeout=10)
            second = asyncio.ensure_future(mgr.ensure_indexed(str(folder)))
            # Let the second caller reach the condition while the first is held.
            for _ in range(1000):
                if mgr._condition._waiters:
                    break
                await asyncio.sleep(0)
            assert mgr._condition._waiters, "the second caller never waited on the reindex in flight"
        finally:
            # Never leave a held reindex behind a failed assertion.
            release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), timeout=60)

        # Both callers should get the real reindex result, not a placeholder
        assert all(r.get("status") in ("indexed", "error") for r in results)
        assert results[0]["status"] == results[1]["status"]

        # Only one actual reindex should have run
        assert reindex_count == 1

    @pytest.mark.asyncio
    async def test_a_reindex_that_never_yields_still_runs_once_per_concurrent_pair(self, tmp_path, monkeypatch):
        """The pinned test holds even when the reindex completes without suspending.

        This is the shape that failed the unpinned test: `to_thread` replaced by
        an inline call, so the first reindex never yields on its own.
        """
        async def inline(func, *args, **kwargs):
            return func(*args, **kwargs)

        monkeypatch.setattr(watcher.asyncio, "to_thread", inline)
        await self.test_ensure_indexed_concurrent(tmp_path)


# ---------------------------------------------------------------------------
# Auto-watch integration tests
# ---------------------------------------------------------------------------

class TestAutoWatchNoWatcher:
    """test_auto_watch_no_watcher — _watcher_manager is None → graceful skip."""

    @pytest.mark.asyncio
    async def test_auto_watch_no_watcher_skips_gracefully(self, tmp_path, monkeypatch):
        """When _watcher_manager is None, auto-watch should not raise."""
        from jcodemunch_mcp import server

        # Ensure watcher manager is None
        monkeypatch.setattr(server, "_watcher_manager", None)

        # Should not raise
        await server._auto_watch_if_needed("search_symbols", {"repo": "test/repo"}, str(tmp_path))


class TestAutoWatchPathFromPathArg:
    """test_auto_watch_path_from_path_arg — Tools with path arg → correct folder."""

    @pytest.mark.asyncio
    async def test_auto_watch_extracts_path_from_path_arg(self, tmp_path, monkeypatch):
        """Auto-watch should extract folder from 'path' argument."""
        from jcodemunch_mcp import server

        folder = _make_folder(tmp_path)

        # Mock watcher manager
        mock_mgr = AsyncMock(spec=WatcherManager)
        mock_mgr.is_watched.return_value = False
        mock_mgr.maybe_takeover = AsyncMock(return_value={"status": "lock_failed"})
        mock_mgr.ensure_indexed = AsyncMock(return_value={"status": "indexed"})
        mock_mgr.add_folder = AsyncMock(return_value={"status": "started"})

        monkeypatch.setattr(server, "_watcher_manager", mock_mgr)
        monkeypatch.setattr(server.config_module, "get", lambda k, d=False: True if k == "watch" else d)

        # index_folder indexes its own path argument, so registration is
        # DEFERRED to after dispatch (#384) — the hook must do nothing now.
        deferred = await server._auto_watch_if_needed(
            "index_folder", {"path": str(folder)}, str(tmp_path)
        )

        assert deferred == str(folder)
        mock_mgr.ensure_indexed.assert_not_called()
        mock_mgr.add_folder.assert_not_called()

        # The post-dispatch half registers the watch WITHOUT a second index.
        await server._auto_watch_after_tool(deferred)

        mock_mgr.ensure_indexed.assert_not_called()
        mock_mgr.add_folder.assert_called_once()
        assert mock_mgr.add_folder.call_args[0][0] == str(folder)
        assert mock_mgr.add_folder.call_args[1]["skip_initial_index"] is True

    async def test_auto_watch_still_eager_for_non_indexing_tools(self, tmp_path, monkeypatch):
        """A tool that does NOT index its own path keeps the pre-dispatch index.

        The deferral is scoped to _AUTO_WATCH_DEFERRED. Everything else must
        behave exactly as it did before v1.108.189, or a search would run
        against an index nothing refreshed.
        """
        from jcodemunch_mcp import server

        folder = _make_folder(tmp_path)

        mock_mgr = AsyncMock(spec=WatcherManager)
        mock_mgr.is_watched.return_value = False
        mock_mgr.maybe_takeover = AsyncMock(return_value={"status": "lock_failed"})
        mock_mgr.ensure_indexed = AsyncMock(return_value={"status": "indexed"})
        mock_mgr.add_folder = AsyncMock(return_value={"status": "started"})

        monkeypatch.setattr(server, "_watcher_manager", mock_mgr)
        monkeypatch.setattr(server.config_module, "get", lambda k, d=False: True if k == "watch" else d)

        deferred = await server._auto_watch_if_needed(
            "get_file_outline", {"path": str(folder)}, str(tmp_path)
        )

        assert deferred is None
        # The eager pre-dispatch index is what this path exists for and it stays.
        mock_mgr.ensure_indexed.assert_called_once()
        mock_mgr.add_folder.assert_called_once()
        # v1.108.191: exactly ONE index. The watch task adopts ensure_indexed's
        # work instead of walking the same tree again, and it must NOT write a
        # synthetic reindex record over the real one ensure_indexed already
        # wrote.
        assert mock_mgr.add_folder.call_args[1]["skip_initial_index"] is True
        assert mock_mgr.add_folder.call_args[1].get("record_index_ready", False) is False


class TestAutoWatchPathFromRepoArg:
    """test_auto_watch_path_from_repo_arg — Tools with repo arg → source_root lookup."""

    @pytest.mark.asyncio
    async def test_auto_watch_extracts_path_from_repo_arg(self, tmp_path, monkeypatch):
        """Auto-watch should look up source_root when 'repo' argument is provided."""
        from jcodemunch_mcp import server

        folder = _make_folder(tmp_path)

        # Mock watcher manager
        mock_mgr = AsyncMock(spec=WatcherManager)
        mock_mgr.is_watched.return_value = False
        mock_mgr.maybe_takeover = AsyncMock(return_value={"status": "lock_failed"})
        mock_mgr.ensure_indexed = AsyncMock(return_value={"status": "indexed"})
        mock_mgr.add_folder = AsyncMock(return_value={"status": "started"})

        monkeypatch.setattr(server, "_watcher_manager", mock_mgr)
        monkeypatch.setattr(server.config_module, "get", lambda k, d=False: True if k == "watch" else d)

        # Mock _get_source_root to return our folder
        monkeypatch.setattr(server, "_get_source_root", lambda r, s: str(folder))

        await server._auto_watch_if_needed("search_symbols", {"repo": "local/test-repo"}, str(tmp_path))

        # Should have called ensure_indexed and add_folder
        mock_mgr.ensure_indexed.assert_called_once()
        mock_mgr.add_folder.assert_called_once()


class TestAutoWatchTriggersOnUnwatched:
    """test_auto_watch_triggers_on_unwatched — Unwatched repo → reindex + watch before tool runs."""

    @pytest.mark.asyncio
    async def test_auto_watch_on_unwatched_repo(self, tmp_path, monkeypatch):
        """When a repo is not watched, auto-watch should trigger ensure_indexed + add_folder."""
        from jcodemunch_mcp import server

        folder = _make_folder(tmp_path)

        # Mock watcher manager
        mock_mgr = AsyncMock(spec=WatcherManager)
        mock_mgr.is_watched.return_value = False  # Not watched
        mock_mgr.maybe_takeover = AsyncMock(return_value={"status": "lock_failed"})
        mock_mgr.ensure_indexed = AsyncMock(return_value={"status": "indexed"})
        mock_mgr.add_folder = AsyncMock(return_value={"status": "started"})

        monkeypatch.setattr(server, "_watcher_manager", mock_mgr)
        monkeypatch.setattr(server.config_module, "get", lambda k, d=False: True if k == "watch" else d)
        monkeypatch.setattr(server, "_get_source_root", lambda r, s: str(folder))

        await server._auto_watch_if_needed("search_symbols", {"repo": "local/test-repo"}, str(tmp_path))

        # Should trigger ensure_indexed then add_folder
        mock_mgr.ensure_indexed.assert_called_once()
        mock_mgr.add_folder.assert_called_once()


class TestAutoWatchSkipsWatched:
    """test_auto_watch_skips_watched — Watched repo → no reindex."""

    @pytest.mark.asyncio
    async def test_auto_watch_skips_already_watched(self, tmp_path, monkeypatch):
        """When a repo is already watched, auto-watch should skip reindexing."""
        from jcodemunch_mcp import server

        folder = _make_folder(tmp_path)

        # Mock watcher manager — already watched
        mock_mgr = AsyncMock(spec=WatcherManager)
        mock_mgr.is_watched.return_value = True  # Already watched
        mock_mgr.ensure_indexed = AsyncMock()
        mock_mgr.add_folder = AsyncMock()

        monkeypatch.setattr(server, "_watcher_manager", mock_mgr)
        monkeypatch.setattr(server.config_module, "get", lambda k, d=False: True if k == "watch" else d)
        monkeypatch.setattr(server, "_get_source_root", lambda r, s: str(folder))

        await server._auto_watch_if_needed("search_symbols", {"repo": "local/test-repo"}, str(tmp_path))

        # Should NOT call ensure_indexed or add_folder
        mock_mgr.ensure_indexed.assert_not_called()
        mock_mgr.add_folder.assert_not_called()


class TestAutoWatchExcludedTools:
    """Auto-watch should skip excluded tools."""

    @pytest.mark.asyncio
    async def test_auto_watch_skips_list_repos(self, tmp_path, monkeypatch):
        """list_repos should be excluded from auto-watch."""
        from jcodemunch_mcp import server

        mock_mgr = AsyncMock(spec=WatcherManager)
        mock_mgr.ensure_indexed = AsyncMock()
        mock_mgr.add_folder = AsyncMock()

        monkeypatch.setattr(server, "_watcher_manager", mock_mgr)
        monkeypatch.setattr(server.config_module, "get", lambda k, d=False: True if k == "watch" else d)

        await server._auto_watch_if_needed("list_repos", {}, str(tmp_path))

        mock_mgr.ensure_indexed.assert_not_called()
        mock_mgr.add_folder.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_watch_skips_get_session_stats(self, tmp_path, monkeypatch):
        """get_session_stats should be excluded from auto-watch."""
        from jcodemunch_mcp import server

        mock_mgr = AsyncMock(spec=WatcherManager)
        mock_mgr.ensure_indexed = AsyncMock()
        mock_mgr.add_folder = AsyncMock()

        monkeypatch.setattr(server, "_watcher_manager", mock_mgr)
        monkeypatch.setattr(server.config_module, "get", lambda k, d=False: True if k == "watch" else d)

        await server._auto_watch_if_needed("get_session_stats", {}, str(tmp_path))

        mock_mgr.ensure_indexed.assert_not_called()
        mock_mgr.add_folder.assert_not_called()


class TestAutoWatchWatchDisabled:
    """Auto-watch should skip when watch config is False."""

    @pytest.mark.asyncio
    async def test_auto_watch_skips_when_watch_config_false(self, tmp_path, monkeypatch):
        """When watch config is False, auto-watch should not trigger."""
        from jcodemunch_mcp import server

        folder = _make_folder(tmp_path)

        mock_mgr = AsyncMock(spec=WatcherManager)
        mock_mgr.ensure_indexed = AsyncMock()
        mock_mgr.add_folder = AsyncMock()

        monkeypatch.setattr(server, "_watcher_manager", mock_mgr)
        # watch config returns False
        monkeypatch.setattr(server.config_module, "get", lambda k, d=False: False if k == "watch" else d)
        monkeypatch.setattr(server, "_get_source_root", lambda r, s: str(folder))

        await server._auto_watch_if_needed("search_symbols", {"repo": "local/test-repo"}, str(tmp_path))

        # Should NOT call ensure_indexed or add_folder
        mock_mgr.ensure_indexed.assert_not_called()
        mock_mgr.add_folder.assert_not_called()
