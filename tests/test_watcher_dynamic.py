"""Tests for WatcherManager dynamic watching and auto-watch on demand."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("watchfiles")

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

    @pytest.mark.asyncio
    async def test_add_folder_can_skip_initial_index(self, tmp_path, monkeypatch):
        """The caller can start watching when it will perform the initial index."""
        folder = _make_folder(tmp_path)
        mgr = WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)
        started = asyncio.Event()
        captured = {}

        async def fake_watch_single(**kwargs):
            captured.update(kwargs)
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(watcher, "_watch_single", fake_watch_single)

        result = await mgr.add_folder(str(folder), initial_index=False)
        await started.wait()

        assert result["status"] == "started"
        assert captured["initial_index"] is False

        await mgr.remove_folder(str(folder))


class TestWatchSingleInitialIndex:
    @pytest.mark.asyncio
    async def test_initial_index_can_be_skipped(self, tmp_path, monkeypatch):
        """A watcher registered by index_folder must not index the tree itself."""
        import watchfiles

        folder = _make_folder(tmp_path)

        async def no_changes(*args, **kwargs):
            if False:
                yield None

        index_folder_mock = MagicMock()
        monkeypatch.setattr(watchfiles, "awatch", no_changes)
        monkeypatch.setattr(watcher, "index_folder", index_folder_mock)

        await watcher._watch_single(
            folder_path=str(folder),
            debounce_ms=100,
            use_ai_summaries=False,
            storage_path=str(tmp_path / "storage"),
            extra_ignore_patterns=None,
            follow_symlinks=False,
            quiet=True,
            initial_index=False,
        )

        index_folder_mock.assert_not_called()


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
        """Two concurrent ensure_indexed calls for the same folder only runs one reindex."""
        folder = _make_folder(tmp_path)
        mgr = WatcherManager(storage_path=str(tmp_path / "storage"), quiet=True)

        reindex_count = 0

        original_do_reindex = mgr._do_reindex

        async def counting_reindex(f, **kwargs):
            nonlocal reindex_count
            reindex_count += 1
            return await original_do_reindex(f, **kwargs)

        mgr._do_reindex = counting_reindex

        # Fire two concurrent ensure_indexed calls
        results = await asyncio.gather(
            mgr.ensure_indexed(str(folder)),
            mgr.ensure_indexed(str(folder)),
        )

        # Both callers should get the real reindex result, not a placeholder
        assert all(r.get("status") in ("indexed", "error") for r in results)
        assert results[0]["status"] == results[1]["status"]

        # Only one actual reindex should have run
        assert reindex_count == 1


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
    async def test_index_folder_registers_watch_without_preindex(self, tmp_path, monkeypatch):
        """index_folder supplies the initial index, so auto-watch only registers."""
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

        await server._auto_watch_if_needed("index_folder", {"path": str(folder)}, str(tmp_path))

        resolved = str(folder.resolve())
        mock_mgr.maybe_takeover.assert_awaited_once_with(resolved, initial_index=False)
        mock_mgr.ensure_indexed.assert_not_awaited()
        mock_mgr.add_folder.assert_awaited_once_with(resolved, initial_index=False)


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
