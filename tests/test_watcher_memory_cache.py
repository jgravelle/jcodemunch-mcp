"""Tests for WatcherChange NamedTuple, watcher memory cache, and fast-path integration."""

import os
import tempfile

import pytest
from pathlib import Path

from jcodemunch_mcp.reindex_state import WatcherChange


class TestWatcherChangeFormat:
    def test_watcher_change_properties(self):
        wc = WatcherChange("modified", "/path/to/file.py", "abc123")
        assert wc.change_type == "modified"
        assert wc.path == "/path/to/file.py"
        assert wc.old_hash == "abc123"

    def test_watcher_change_tuple_access(self):
        wc = WatcherChange("added", "/path/to/file.py", "")
        assert wc[0] == "added"
        assert wc[1] == "/path/to/file.py"
        assert wc[2] == ""

    def test_watcher_change_default_old_hash(self):
        wc = WatcherChange("added", "/path/to/file.py")
        assert wc.old_hash == ""


class TestWatcherMemoryCache:
    def test_watcher_change_with_old_hash(self):
        wc = WatcherChange("modified", "/path/to/file.py", "old_hash_value")
        assert wc.old_hash == "old_hash_value"
        assert wc.change_type == "modified"
        assert wc.path == "/path/to/file.py"


class TestBuildHashCacheIntegration:
    """Verify that _build_hash_cache can actually load an index via IndexStore.

    This catches the bug where _local_repo_id returns 'local/name-hash'
    but store.load_index(owner, name) rejects '/' in the name parameter.
    """

    def test_load_index_with_split_repo_id(self, tmp_path):
        """Simulate what _build_hash_cache does: split repo_id and call load_index."""
        from jcodemunch_mcp.storage.index_store import IndexStore
        from jcodemunch_mcp.watcher import _local_repo_id

        folder_path = str(tmp_path)
        repo_id = _local_repo_id(folder_path)
        assert "/" in repo_id, "repo_id must contain 'local/' prefix"

        repo_owner, repo_store_name = repo_id.split("/", 1)
        store = IndexStore(base_path=str(tmp_path / ".code-index"))

        # Must not raise ValueError — this is the exact call _build_hash_cache makes
        result = store.load_index(repo_owner, repo_store_name)
        assert result is None  # no index yet, but no crash

    def test_load_index_rejects_unsplit_repo_id(self, tmp_path):
        """Passing the full repo_id as name must raise (validates the bug existed)."""
        from jcodemunch_mcp.storage.index_store import IndexStore
        from jcodemunch_mcp.watcher import _local_repo_id

        folder_path = str(tmp_path)
        repo_id = _local_repo_id(folder_path)  # "local/name-hash"
        store = IndexStore(base_path=str(tmp_path / ".code-index"))

        with pytest.raises(ValueError, match="Path separator"):
            store.load_index("local", repo_id)  # <-- the old bug


class TestFastPathDeletedFiles:
    """Verify that deleted files are processed on the memory-cache fast path."""

    def test_deleted_file_with_memory_cache(self, tmp_path):
        """When use_memory_hash_cache=True, deleted files must still be removed from the index."""
        from jcodemunch_mcp.tools.index_folder import index_folder

        # Create a test file and index it
        test_file = tmp_path / "hello.py"
        test_file.write_text("def hello():\n    return 'world'\n")

        result = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=str(tmp_path / ".code-index"),
            incremental=False,
        )
        assert result["success"]
        assert result["symbol_count"] >= 1

        # Now delete the file and call index_folder with changed_paths simulating
        # a watcher delete event with old_hash (memory cache path)
        abs_path = str(test_file.resolve())
        test_file.unlink()

        watcher_changes = [WatcherChange("deleted", abs_path, "some_old_hash")]
        result2 = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=str(tmp_path / ".code-index"),
            incremental=True,
            changed_paths=watcher_changes,
        )
        assert result2["success"]
        assert result2.get("deleted", 0) >= 1, (
            f"Expected at least 1 deleted file, got {result2}"
        )


class TestHashCacheMissFallback:
    """Regression tests for the hash-cache miss handling in _watch_single.

    Previously, a cache miss caused the watcher to read the file from disk to
    compute old_hash — but by the time watchfiles delivers the event the file
    already has new content, so old_hash == new_hash and the change is silently
    skipped as "unchanged". The fix replaces this with a sentinel "__cache_miss__"
    that is guaranteed never to match any real content hash, forcing re-parse.
    """

    def test_cache_miss_forces_reindex(self, tmp_path):
        """A modified file whose hash is absent from the memory cache must be re-indexed.

        Simulate the scenario: file is indexed, then modified, but the watcher's
        in-memory hash cache is empty (e.g. cold start).  Pass old_hash="__cache_miss__"
        (the sentinel) and verify index_folder re-parses the file.
        """
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp.reindex_state import WatcherChange

        test_file = tmp_path / "module.py"
        test_file.write_text("def original(): pass\n")

        # Initial index
        result = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=str(tmp_path / ".code-index"),
            incremental=False,
        )
        assert result["success"]

        # Simulate file change + cache miss (sentinel old_hash)
        test_file.write_text("def updated(): return 42\n")
        abs_path = str(test_file.resolve())
        watcher_changes = [WatcherChange("modified", abs_path, "__cache_miss__")]

        result2 = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=str(tmp_path / ".code-index"),
            incremental=True,
            changed_paths=watcher_changes,
        )
        assert result2["success"]
        # Must NOT return "No changes detected" — the file change must be processed
        assert result2.get("message") != "No changes detected", (
            "Cache-miss sentinel must force re-parse, not skip the file"
        )
        assert result2.get("changed", 0) >= 1 or result2.get("new", 0) >= 1, (
            f"Expected at least 1 changed/new file, got {result2}"
        )

    def test_sentinel_never_equals_real_hash(self):
        """__cache_miss__ must not be a valid SHA-256 hex digest."""
        import hashlib
        sentinel = "__cache_miss__"
        # Real hashes are 64-char hex strings; the sentinel is neither
        assert not all(c in "0123456789abcdef" for c in sentinel), (
            "Sentinel must be distinguishable from a real content hash"
        )
        assert len(sentinel) != 64


class TestFastPathExtraIgnorePatterns:
    """Regression: #300 follow-up (reported by @domis86 on v1.108.18). The
    watcher fast path in index_folder skipped `discover_local_files`, which
    is where extra_ignore_patterns get applied. A file under an ignored
    prefix that was correctly absent from the initial index would slip
    back in on the next modify event.
    """

    def test_modified_file_under_ignore_pattern_stays_unindexed(self, tmp_path):
        """An 'modified' watcher event on a file matching extra_ignore_patterns
        must be skipped on the fast path."""
        from jcodemunch_mcp.tools.index_folder import index_folder

        # Project shape: one file under docs/legacy/ (ignored), one outside.
        legacy_dir = tmp_path / "docs" / "legacy"
        legacy_dir.mkdir(parents=True)
        ignored_file = legacy_dir / "file123.py"
        ignored_file.write_text("def in_ignored():\n    return 1\n")
        kept_file = tmp_path / "main.py"
        kept_file.write_text("def kept():\n    return 1\n")

        storage = str(tmp_path / ".code-index")

        # Initial full index with extra_ignore_patterns excluding docs/legacy/.
        result = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=False,
            extra_ignore_patterns=["docs/legacy/"],
        )
        assert result["success"]
        # Sanity: the ignored file is not in the index.
        from jcodemunch_mcp.storage import IndexStore
        store = IndexStore(base_path=storage)
        owner, name = result["repo"].split("/", 1)
        index = store.load_index(owner, name)
        files = set(index.file_hashes.keys()) if index.file_hashes else set()
        assert "docs/legacy/file123.py" not in files, (
            "initial index leaked an ignored file"
        )

        # Modify the ignored file and trigger the watcher fast-path reindex.
        ignored_file.write_text("def in_ignored():\n    return 2  # changed\n")
        watcher_changes = [
            WatcherChange("modified", str(ignored_file.resolve()), "__cache_miss__"),
        ]
        result2 = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=True,
            extra_ignore_patterns=["docs/legacy/"],
            changed_paths=watcher_changes,
        )
        assert result2["success"]

        # Re-load index and assert the ignored file is STILL not in it.
        index_after = store.load_index(owner, name)
        files_after = set(index_after.file_hashes.keys()) if index_after.file_hashes else set()
        assert "docs/legacy/file123.py" not in files_after, (
            "watcher fast path re-indexed an ignored file (#300 follow-up); "
            f"index files: {sorted(files_after)}"
        )

    def test_added_file_under_ignore_pattern_stays_unindexed(self, tmp_path):
        """An 'added' watcher event on a new file matching extra_ignore_patterns
        must also be skipped."""
        from jcodemunch_mcp.tools.index_folder import index_folder

        legacy_dir = tmp_path / "docs" / "legacy"
        legacy_dir.mkdir(parents=True)
        kept_file = tmp_path / "main.py"
        kept_file.write_text("def kept():\n    return 1\n")

        storage = str(tmp_path / ".code-index")

        # Initial index without the ignored file present.
        result = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=False,
            extra_ignore_patterns=["docs/legacy/"],
        )
        assert result["success"]

        # Now create a file under the ignored prefix and fire an "added" event.
        new_file = legacy_dir / "newcomer.py"
        new_file.write_text("def newcomer():\n    return 1\n")
        watcher_changes = [
            WatcherChange("added", str(new_file.resolve()), ""),
        ]
        result2 = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=True,
            extra_ignore_patterns=["docs/legacy/"],
            changed_paths=watcher_changes,
        )
        assert result2["success"]

        from jcodemunch_mcp.storage import IndexStore
        store = IndexStore(base_path=storage)
        owner, name = result["repo"].split("/", 1)
        index_after = store.load_index(owner, name)
        files_after = set(index_after.file_hashes.keys()) if index_after.file_hashes else set()
        assert "docs/legacy/newcomer.py" not in files_after, (
            "watcher fast path indexed a newly-added ignored file; "
            f"index files: {sorted(files_after)}"
        )

    def test_modified_file_outside_ignore_still_indexed(self, tmp_path):
        """Sanity: non-ignored files should still flow through the fast path
        normally. The filter must not over-match."""
        from jcodemunch_mcp.tools.index_folder import index_folder

        kept_file = tmp_path / "main.py"
        kept_file.write_text("def kept():\n    return 1\n")

        storage = str(tmp_path / ".code-index")
        result = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=False,
            extra_ignore_patterns=["docs/legacy/"],
        )
        assert result["success"]

        # Modify the kept file; watcher fast path should re-index it.
        kept_file.write_text("def kept():\n    return 99  # updated\n")
        watcher_changes = [
            WatcherChange("modified", str(kept_file.resolve()), "__cache_miss__"),
        ]
        result2 = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=True,
            extra_ignore_patterns=["docs/legacy/"],
            changed_paths=watcher_changes,
        )
        assert result2["success"]
        # changed should be >=1 since the file content actually changed.
        assert result2.get("changed", 0) >= 1, (
            f"non-ignored file should have been re-indexed: {result2}"
        )


class TestFastPathHonorsAllDiscoveryFilters:
    """Regression: #306 (audit follow-up to #300). The watcher fast path in
    ``index_folder`` skipped ``discover_local_files`` entirely, so every
    filter applied during a full walk was missing on the fast path. v1.108.19
    fixed ``extra_ignore_patterns`` (covered by ``TestFastPathExtraIgnorePatterns``
    above). #306 extracted ``_should_index_file`` as the single source of
    truth and routed both paths through it. These tests lock the invariant:
    any new filter added to ``_should_index_file`` automatically applies on
    the fast path too — but the High/Medium risks called out in #306
    (gitignore, size cap, symlink protection, skip-dirs) get explicit
    coverage here so regressions surface as test failures, not silent
    re-indexing of files the user expects to be excluded.
    """

    def test_modified_file_under_gitignore_stays_unindexed(self, tmp_path):
        """High-risk per #306: a .gitignore-matched file modified after the
        initial index must not be re-indexed by the fast path."""
        from jcodemunch_mcp.tools.index_folder import index_folder

        # Project shape: root .gitignore excludes build/; one file inside
        # build/ (ignored), one outside (kept).
        (tmp_path / ".gitignore").write_text("build/\n")
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        ignored_file = build_dir / "artifact.py"
        ignored_file.write_text("def artifact():\n    return 1\n")
        kept_file = tmp_path / "main.py"
        kept_file.write_text("def kept():\n    return 1\n")

        storage = str(tmp_path / ".code-index")

        # Initial full index. The full walk already honours .gitignore, so
        # build/artifact.py is correctly absent.
        result = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=False,
        )
        assert result["success"]
        from jcodemunch_mcp.storage import IndexStore
        store = IndexStore(base_path=storage)
        owner, name = result["repo"].split("/", 1)
        index = store.load_index(owner, name)
        files = set(index.file_hashes.keys()) if index.file_hashes else set()
        assert "build/artifact.py" not in files, "initial walk leaked a gitignored file"

        # Modify the gitignored file and trigger a fast-path reindex.
        ignored_file.write_text("def artifact():\n    return 2  # changed\n")
        watcher_changes = [
            WatcherChange("modified", str(ignored_file.resolve()), "__cache_miss__"),
        ]
        result2 = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=True,
            changed_paths=watcher_changes,
        )
        assert result2["success"]

        index_after = store.load_index(owner, name)
        files_after = set(index_after.file_hashes.keys()) if index_after.file_hashes else set()
        assert "build/artifact.py" not in files_after, (
            "watcher fast path re-indexed a .gitignore-matched file (#306); "
            f"index files: {sorted(files_after)}"
        )

    def test_modified_oversize_file_skipped_by_fast_path(self, tmp_path):
        """Medium-risk per #306: a file that exceeds the size cap must be
        skipped on the fast path, not silently re-indexed."""
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp.tools import index_folder as idx_mod

        kept_file = tmp_path / "main.py"
        kept_file.write_text("def kept():\n    return 1\n")
        # Pre-create a small placeholder; we'll grow it past the cap below.
        big_file = tmp_path / "big.py"
        big_file.write_text("def small():\n    return 1\n")

        storage = str(tmp_path / ".code-index")

        # Initial index includes big.py at its small size.
        result = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=False,
        )
        assert result["success"]
        from jcodemunch_mcp.storage import IndexStore
        store = IndexStore(base_path=storage)
        owner, name = result["repo"].split("/", 1)

        # Grow big.py past the size cap. Set the cap through the CONFIG route
        # (real cap is 500 KB; writing that many bytes per test is wasteful).
        #
        # v1.108.193: this used to patch idx_mod.DEFAULT_MAX_FILE_SIZE, which
        # index_folder passed straight through as a hardcoded constant. It now
        # resolves via security.get_max_file_size(), so the constant is no
        # longer consulted and patching it silently did nothing -- the fast path
        # indexed the oversize file and this assertion caught it. Setting the
        # config value exercises the override a user actually has, which makes
        # this a stronger test than it was.
        from jcodemunch_mcp import config as _cfg_mod
        original_cap = _cfg_mod._GLOBAL_CONFIG.get("max_file_size")
        try:
            _cfg_mod._GLOBAL_CONFIG["max_file_size"] = 200  # bytes
            big_file.write_text("# padding\n" * 100)  # ~1000 bytes
            watcher_changes = [
                WatcherChange("modified", str(big_file.resolve()), "__cache_miss__"),
            ]
            result2 = index_folder(
                path=str(tmp_path),
                use_ai_summaries=False,
                storage_path=storage,
                incremental=True,
                changed_paths=watcher_changes,
            )
            assert result2["success"]
            # The modify should NOT have re-parsed big.py — fast path saw
            # too_large and skipped it. The bookkeeping signal: no parse =
            # no "changed" increment.
            assert result2.get("changed", 0) == 0, (
                "watcher fast path re-indexed an oversize file (#306); "
                f"result: {result2}"
            )
        finally:
            if original_cap is None:
                _cfg_mod._GLOBAL_CONFIG.pop("max_file_size", None)
            else:
                _cfg_mod._GLOBAL_CONFIG["max_file_size"] = original_cap

    def test_added_file_under_skip_dir_not_indexed(self, tmp_path):
        """Per #306: watchfiles can emit events under build/cache dirs that
        os.walk would prune. The fast path must apply the skip-dirs regex."""
        from jcodemunch_mcp.tools.index_folder import index_folder

        kept_file = tmp_path / "main.py"
        kept_file.write_text("def kept():\n    return 1\n")
        # node_modules is one of the default skip dirs.
        nm_dir = tmp_path / "node_modules" / "left-pad"
        nm_dir.mkdir(parents=True)

        storage = str(tmp_path / ".code-index")
        result = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=False,
        )
        assert result["success"]

        # Fire an "added" event for a file inside node_modules/.
        new_file = nm_dir / "index.js"
        new_file.write_text("module.exports = function(){};\n")
        watcher_changes = [
            WatcherChange("added", str(new_file.resolve()), ""),
        ]
        result2 = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=True,
            changed_paths=watcher_changes,
        )
        assert result2["success"]

        from jcodemunch_mcp.storage import IndexStore
        store = IndexStore(base_path=storage)
        owner, name = result["repo"].split("/", 1)
        index_after = store.load_index(owner, name)
        files_after = set(index_after.file_hashes.keys()) if index_after.file_hashes else set()
        assert "node_modules/left-pad/index.js" not in files_after, (
            "watcher fast path indexed a file under a skipped directory (#306); "
            f"index files: {sorted(files_after)}"
        )

    def test_added_symlinked_file_skipped_when_follow_symlinks_false(self, tmp_path):
        """Per #306: with follow_symlinks=False (the default), an 'added'
        event for a symlink must be skipped on the fast path."""
        from jcodemunch_mcp.tools.index_folder import index_folder

        kept_file = tmp_path / "main.py"
        kept_file.write_text("def kept():\n    return 1\n")

        # Target outside the repo to make the case unambiguous, even if
        # symlink_escape didn't fire first.
        external_target = tmp_path.parent / f"external-{tmp_path.name}.py"
        external_target.write_text("def external():\n    return 1\n")

        storage = str(tmp_path / ".code-index")
        result = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=False,
        )
        assert result["success"]

        symlink_path = tmp_path / "link.py"
        try:
            os.symlink(str(external_target), str(symlink_path))
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this platform/user")

        watcher_changes = [
            WatcherChange("added", str(symlink_path), ""),
        ]
        result2 = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=True,
            follow_symlinks=False,
            changed_paths=watcher_changes,
        )
        assert result2["success"]

        from jcodemunch_mcp.storage import IndexStore
        store = IndexStore(base_path=storage)
        owner, name = result["repo"].split("/", 1)
        index_after = store.load_index(owner, name)
        files_after = set(index_after.file_hashes.keys()) if index_after.file_hashes else set()
        assert "link.py" not in files_after, (
            "watcher fast path indexed a symlink with follow_symlinks=False (#306); "
            f"index files: {sorted(files_after)}"
        )

    def test_deleted_event_bypasses_filters(self, tmp_path):
        """Per #306: deletions intentionally bypass the filter set so that
        an entry indexed before an ignore rule existed can still be
        removed. This locks that asymmetry — modifies are filtered, deletes
        are not."""
        from jcodemunch_mcp.tools.index_folder import index_folder

        # Index a file, then later "ignore" it. The delete event should
        # still remove it from the index even though the filter would now
        # reject an "added" or "modified" event for the same path.
        target = tmp_path / "to_remove.py"
        target.write_text("def to_remove():\n    return 1\n")
        kept = tmp_path / "main.py"
        kept.write_text("def kept():\n    return 1\n")

        storage = str(tmp_path / ".code-index")
        result = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=False,
        )
        assert result["success"]
        from jcodemunch_mcp.storage import IndexStore
        store = IndexStore(base_path=storage)
        owner, name = result["repo"].split("/", 1)
        index = store.load_index(owner, name)
        files = set(index.file_hashes.keys()) if index.file_hashes else set()
        assert "to_remove.py" in files, "setup failed: file should be in initial index"

        # Now delete + fire delete event. Use an ignore pattern that would
        # have blocked add/modify, to prove deletes are unaffected.
        target.unlink()
        watcher_changes = [
            WatcherChange("deleted", str((tmp_path / "to_remove.py").resolve()), ""),
        ]
        result2 = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=True,
            extra_ignore_patterns=["to_remove.py"],
            changed_paths=watcher_changes,
        )
        assert result2["success"]
        assert result2.get("deleted", 0) == 1, (
            f"delete event should remove the entry even when filter would reject "
            f"an add/modify for the same path: {result2}"
        )

        index_after = store.load_index(owner, name)
        files_after = set(index_after.file_hashes.keys()) if index_after.file_hashes else set()
        assert "to_remove.py" not in files_after, (
            "delete event failed to remove the file from the index"
        )


def _skip_service_probe(monkeypatch):
    """get_watch_status probes the OS service manager (about 8 s on Windows).

    These tests read only the per-repo rows, so the probe is stubbed.
    """
    import importlib

    module = importlib.import_module("jcodemunch_mcp.tools.get_watch_status")
    monkeypatch.setattr(module, "service_status", lambda: {"active": None, "status": "not_probed"})


async def test_failed_watcher_changes_recover_without_another_edit(tmp_path, monkeypatch):
    """A failed save must not consume the only events announcing those edits.

    The raised-exception branch is driven by
    test_persistent_failure_backs_off_then_resets_after_recovery.
    """
    from types import SimpleNamespace

    from jcodemunch_mcp import reindex_state, watcher
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.get_watch_status import get_watch_status

    Change = pytest.importorskip("watchfiles").Change
    _skip_service_probe(monkeypatch)
    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    gone = root / "gone.py"
    code.write_text("def before(): pass\n", encoding="utf-8")
    gone.write_text("def removed(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    real_index = watcher.index_folder
    result = real_index(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    owner, name = result["repo"].split("/", 1)
    store = IndexStore(base_path=storage)

    def names():
        index = store.load_index(owner, name)
        assert index is not None
        return {symbol["name"] for symbol in index.symbols}

    failures_left = 2
    idle = False
    idle_work = []

    def temporarily_locked(**kwargs):
        nonlocal failures_left
        if idle:
            idle_work.append(kwargs["path"])
        if failures_left:
            failures_left -= 1
            return {"success": False, "error": "index-write lock is busy"}
        return real_index(**kwargs)

    now = 0.0
    monkeypatch.setattr(watcher, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(reindex_state, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(watcher, "index_folder", temporarily_locked)

    async def events(*args, **kwargs):
        nonlocal now, idle
        code.write_text("def first_edit(): pass\n", encoding="utf-8")
        yield {(Change.modified, str(code))}
        assert names() == {"before", "removed"}
        pending = get_watch_status(storage, check_freshness=False, check_completeness=False)
        row = next(entry for entry in pending["repos"] if entry["source_root"] == str(root))
        assert pending["any_retry_pending"] is True
        assert row["last_reindex_error"] == "index-write lock is busy"
        assert row["retry_in_ms"] > 0
        now = 1.0
        yield set()
        assert names() == {"before", "removed"}
        assert reindex_state.get_reindex_status(result["repo"])["stale_since_ms"] == 1000

        # Reconciliation must use today's tree, not replay obsolete event types.
        code.write_text("def latest(): pass\n", encoding="utf-8")
        gone.unlink()
        added = root / "added.py"
        added.write_text("def added(): pass\n", encoding="utf-8")
        now = 61.0
        yield set()
        assert names() == {"before", "removed"}, "the retry is still blocked"
        now = 62.0
        yield {(Change.added, str(added))}
        assert names() == {"before", "removed", "added"}, "new files need not wait for full recovery"
        now = 122.0
        yield set()
        assert names() == {"latest", "added"}, "failed edits were lost after the events were consumed"

        code.write_text("def after_recovery(): pass\n", encoding="utf-8")
        yield {(Change.modified, str(code))}
        assert names() == {"after_recovery", "added"}

        idle = True
        now += 365 * 24 * 60 * 60
        yield set()
        assert idle_work == [], "an unattended, current project needs no retry"
        status = reindex_state.get_reindex_status(result["repo"])
        assert status["index_stale"] is False
        assert status["stale_since_ms"] is None
        assert "retry_in_ms" not in status
        assert "last_reindex_error" not in status
        assert "reindex_elapsed_ms" not in status
        assert get_watch_status(storage, check_freshness=False, check_completeness=False)["any_retry_pending"] is False

    monkeypatch.setattr(watcher, "_safe_awatch", events)
    await watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    )


@pytest.mark.parametrize("failure", ["result", "cache_reload"])
async def test_retry_refreshes_hashes_when_the_failed_attempt_already_saved(tmp_path, monkeypatch, failure):
    """A post-save failure must not leave old hashes that hide a later revert."""
    from types import SimpleNamespace

    from jcodemunch_mcp import reindex_state, watcher
    from jcodemunch_mcp.storage import IndexStore

    Change = pytest.importorskip("watchfiles").Change
    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    original = "def before(): pass\n"
    code.write_text(original, encoding="utf-8")
    storage = str(tmp_path / "store")
    real_index = watcher.index_folder
    result = real_index(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    owner, name = result["repo"].split("/", 1)
    store = IndexStore(base_path=storage)
    fail_after_save = True
    fail_cache_reload = False
    real_load = type(store).load_index

    def load_index(self, *args, **kwargs):
        nonlocal fail_cache_reload
        if fail_cache_reload:
            fail_cache_reload = False
            raise RuntimeError("post-save cache reload failed")
        return real_load(self, *args, **kwargs)

    def post_save_failure(**kwargs):
        nonlocal fail_after_save, fail_cache_reload
        saved = real_index(**kwargs)
        assert saved["success"], saved
        if fail_after_save:
            fail_after_save = False
            if failure == "result":
                return {"success": False, "error": "post-save step failed"}
            saved.pop("file_hashes_delta", None)
            fail_cache_reload = True
        return saved

    now = 0.0

    async def events(*args, **kwargs):
        nonlocal now
        code.write_text("def changed(): pass\n", encoding="utf-8")
        yield {(Change.modified, str(code))}
        assert reindex_state.get_reindex_status(result["repo"])["index_stale"] is True
        now = 61.0
        yield set()
        code.write_text(original, encoding="utf-8")
        yield {(Change.modified, str(code))}

    monkeypatch.setattr(watcher, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(watcher, "index_folder", post_save_failure)
    monkeypatch.setattr(type(store), "load_index", load_index)
    monkeypatch.setattr(watcher, "_safe_awatch", events)
    await watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    )
    index = store.load_index(owner, name)
    assert index is not None
    assert {symbol["name"] for symbol in index.symbols} == {"before"}


async def test_persistent_failure_backs_off_then_resets_after_recovery(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from jcodemunch_mcp import reindex_state, watcher
    from jcodemunch_mcp.storage import IndexStore

    Change = pytest.importorskip("watchfiles").Change
    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    code.write_text("def before(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    real_index = watcher.index_folder
    result = real_index(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    now = 0.0
    attempts = 0
    blocked = True

    def failing_index(**kwargs):
        nonlocal attempts
        attempts += 1
        if blocked:
            if attempts % 2:
                return {"success": False, "error": "disk full"}
            raise RuntimeError("disk full")
        return real_index(**kwargs)

    async def events(*args, **kwargs):
        nonlocal now, blocked
        code.write_text("def recovered(): pass\n", encoding="utf-8")
        yield {(Change.modified, str(code))}
        for delay in (5, 10, 20, 40, 80, 160, 300, 300):
            status = reindex_state.get_reindex_status(result["repo"])
            assert status["retry_in_ms"] == delay * 1000
            previous_attempts = attempts
            now += delay - 1
            yield set()
            assert attempts == previous_attempts, "idle wake-ups must respect backoff"
            now += 1
            yield set()
            assert attempts == previous_attempts + 1
        status = reindex_state.get_reindex_status(result["repo"])
        assert status.get("retry_attempts") == attempts
        blocked = False
        now += 300
        yield set()
        status = reindex_state.get_reindex_status(result["repo"])
        assert "retry_in_ms" not in status
        assert "retry_attempts" not in status
        assert status["index_stale"] is False
        owner, name = result["repo"].split("/", 1)
        index = IndexStore(base_path=storage).load_index(owner, name)
        assert index is not None
        assert {symbol["name"] for symbol in index.symbols} == {"recovered"}

        blocked = True
        code.write_text("def later(): pass\n", encoding="utf-8")
        yield {(Change.modified, str(code))}
        status = reindex_state.get_reindex_status(result["repo"])
        assert status["retry_in_ms"] == 5000
        assert status.get("retry_attempts") == 1

    monkeypatch.setattr(watcher, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(reindex_state, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(watcher, "index_folder", failing_index)
    monkeypatch.setattr(watcher, "_safe_awatch", events)
    await watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    )


async def test_stopped_watcher_does_not_advertise_a_retry(tmp_path, monkeypatch):
    """Cancellation is the exit a loop-end clear would miss; a return or a
    raised error leaves through the same ``finally``."""
    import asyncio

    from jcodemunch_mcp import reindex_state, watcher
    from jcodemunch_mcp.tools.get_watch_status import get_watch_status

    Change = pytest.importorskip("watchfiles").Change
    _skip_service_probe(monkeypatch)
    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    code.write_text("def before(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = watcher.index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    waiting = asyncio.Event()

    async def events(*args, **kwargs):
        yield {(Change.modified, str(code))}
        waiting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(watcher, "_safe_awatch", events)
    monkeypatch.setattr(watcher, "index_folder", lambda **kwargs: {"success": False, "error": "disk full"})
    task = asyncio.create_task(watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    ))
    await asyncio.wait_for(waiting.wait(), timeout=10)
    assert "retry_in_ms" in reindex_state.get_reindex_status(result["repo"])
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    status = reindex_state.get_reindex_status(result["repo"])
    assert "retry_in_ms" not in status
    assert "retry_attempts" not in status
    assert status["last_reindex_error"] == "disk full"
    assert status["index_stale"] is True
    assert get_watch_status(storage, check_freshness=False, check_completeness=False)["any_retry_pending"] is False


@pytest.mark.parametrize("failed_first", [True, False])
async def test_retry_rereads_failed_files_past_the_folder_cap(tmp_path, monkeypatch, failed_first):
    """A walk has no changed_paths, and index_folder keeps a file past
    max_folder_files without reading it again unless a changed path names it.
    An edit to such a file must reach the index after a full retry
    (failed_first) and after a batch that a folder event made into a walk."""
    from types import SimpleNamespace

    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp import reindex_state, watcher
    from jcodemunch_mcp.storage import IndexStore

    Change = pytest.importorskip("watchfiles").Change
    root = tmp_path / "repo"
    root.mkdir()
    files = [root / f"m{i}.py" for i in range(3)]
    for i, path in enumerate(files):
        path.write_text(f"def old{i}(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    real_index = watcher.index_folder
    result = real_index(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    owner, name = result["repo"].split("/", 1)
    store = IndexStore(base_path=storage)
    # The index holds all 3 files; the watcher now runs under a cap of 1.
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_folder_files", 1)

    def names():
        return {symbol["name"] for symbol in store.load_index(owner, name).symbols}

    blocked = failed_first

    def index(**kwargs):
        if blocked:
            return {"success": False, "error": "writer busy"}
        return real_index(**kwargs)

    now = 0.0

    async def events(*args, **kwargs):
        nonlocal now, blocked
        for i, path in enumerate(files):
            path.write_text(f"def new{i}(): pass\n", encoding="utf-8")
        batch = {(Change.modified, str(path)) for path in files}
        if failed_first:
            yield batch
            assert names() == {"old0", "old1", "old2"}
            blocked = False
            now = 5.0
            yield set()
        else:
            # The root event (the rescan _safe_awatch yields) makes it a walk.
            yield batch | {(Change.modified, str(root))}
        assert names() == {"new0", "new1", "new2"}, "the walk kept a capped file's old copy"
        status = reindex_state.get_reindex_status(result["repo"])
        assert "retry_in_ms" not in status
        assert status["index_stale"] is False

    monkeypatch.setattr(watcher, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(reindex_state, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(watcher, "index_folder", index)
    monkeypatch.setattr(watcher, "_safe_awatch", events)
    await watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    )


async def test_watch_tasks_sharing_a_repo_key_keep_each_others_retry(tmp_path, monkeypatch):
    """Two watched subfolders of one repository share one repo key under git
    identity. One task's success or exit must not clear the other's retry."""
    import asyncio

    from jcodemunch_mcp import reindex_state, watcher

    Change = pytest.importorskip("watchfiles").Change
    folder_a = tmp_path / "a"
    folder_b = tmp_path / "b"
    for folder in (folder_a, folder_b):
        folder.mkdir()
        (folder / "code.py").write_text("def f(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    repo = "shared/repo"
    a_failed = asyncio.Event()
    b_indexed = asyncio.Event()

    def index(**kwargs):
        if kwargs["path"] == str(folder_a):
            return {"success": False, "error": "writer busy"}
        return {"success": True, "file_hashes_delta": {}}

    async def events(folder_path, *args, **kwargs):
        if folder_path == str(folder_a):
            yield {(Change.modified, str(folder_a / "code.py"))}
            a_failed.set()
        else:
            await a_failed.wait()
            yield {(Change.modified, str(folder_b / "code.py"))}
            b_indexed.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(watcher, "_local_repo_id", lambda *args, **kwargs: repo)
    monkeypatch.setattr(watcher, "index_folder", index)
    monkeypatch.setattr(watcher, "_safe_awatch", events)
    tasks = {
        folder: asyncio.create_task(watcher._watch_single(
            str(folder), 200, False, storage, None, False,
            skip_initial_index=True, quiet=True, context_providers=False,
        ))
        for folder in (folder_a, folder_b)
    }

    async def stop(folder):
        tasks[folder].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[folder]

    try:
        await asyncio.wait_for(b_indexed.wait(), timeout=10)
        status = reindex_state.get_reindex_status(repo)
        assert "retry_in_ms" in status, "the other task's success cleared this task's retry"
        assert status.get("retry_attempts") == 1
        assert status["index_stale"] is True
        assert status["last_reindex_error"] == "writer busy"

        await stop(folder_b)
        status = reindex_state.get_reindex_status(repo)
        assert "retry_in_ms" in status, "the other task's exit cleared this task's retry"
        assert status["index_stale"] is True

        await stop(folder_a)
        status = reindex_state.get_reindex_status(repo)
        assert "retry_in_ms" not in status
        assert "retry_attempts" not in status
    finally:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)


@pytest.mark.parametrize("other_success", [False, True])
async def test_other_reindex_does_not_hide_the_watchers_retry(tmp_path, monkeypatch, other_success):
    from types import SimpleNamespace

    from jcodemunch_mcp import reindex_state, watcher
    from jcodemunch_mcp.tools.get_watch_status import get_watch_status

    Change = pytest.importorskip("watchfiles").Change
    _skip_service_probe(monkeypatch)
    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    code.write_text("def before(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    real_index = watcher.index_folder
    result = real_index(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    now = 0.0
    blocked = True
    manager = watcher.WatcherManager(storage_path=storage, use_ai_summaries=False, context_providers=False)
    other_status = []

    def index(**kwargs):
        if now == 1:
            other_status.append(reindex_state.get_reindex_status(result["repo"]))
        if blocked:
            return {"success": False, "error": "writer busy"}
        return real_index(**kwargs)

    async def events(*args, **kwargs):
        nonlocal now, blocked
        code.write_text("def recovered(): pass\n", encoding="utf-8")
        yield {(Change.modified, str(code))}
        now = 1.0
        blocked = not other_success
        other = await manager._do_reindex(str(root))
        assert other["result"]["success"] is other_success
        assert other_status[0].get("retry_in_ms") == 4000, "another writer hid the pending retry"
        pending = get_watch_status(storage, check_freshness=False, check_completeness=False)
        row = next(entry for entry in pending["repos"] if entry["source_root"] == str(root))
        assert pending["any_retry_pending"] is True
        assert row["retry_in_ms"] == 4000
        assert row.get("retry_attempts") == 1
        assert row["last_reindex_error"] == "writer busy"
        assert row["stale_since_ms"] == 1000
        blocked = False
        now = 5.0
        yield set()
        status = reindex_state.get_reindex_status(result["repo"])
        assert "retry_in_ms" not in status
        assert "retry_attempts" not in status
        assert status["index_stale"] is False

    monkeypatch.setattr(watcher, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(reindex_state, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(watcher, "index_folder", index)
    monkeypatch.setattr(watcher, "_safe_awatch", events)
    await watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    )


@pytest.mark.parametrize("targeted_failure", [False, True])
async def test_new_edits_stay_targeted_without_postponing_full_recovery(tmp_path, monkeypatch, targeted_failure):
    from types import SimpleNamespace

    from jcodemunch_mcp import reindex_state, watcher
    from jcodemunch_mcp.storage import IndexStore

    Change = pytest.importorskip("watchfiles").Change
    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    missed = root / "missed.py"
    original = "def before(): pass\n"
    code.write_text(original, encoding="utf-8")
    missed.write_text("def missed_before(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    real_index = watcher.index_folder
    result = real_index(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    owner, name = result["repo"].split("/", 1)
    store = IndexStore(base_path=storage)
    real_load = type(store).load_index
    now = 0.0
    requests = []
    allow_targeted = False
    recovered = False
    reject_hydration = False

    def load_index(self, *args, **kwargs):
        assert not reject_hydration, "a targeted edit must not hydrate the full index"
        return real_load(self, *args, **kwargs)

    def names():
        index = real_load(store, owner, name)
        assert index is not None
        return {symbol["name"] for symbol in index.symbols}

    def index(**kwargs):
        requests.append(kwargs["changed_paths"])
        if len(requests) == 1:
            saved = real_index(**kwargs)
            assert saved["success"], saved
        if not recovered and (not allow_targeted or targeted_failure):
            return {"success": False, "error": "writer busy"}
        return real_index(**kwargs)

    async def events(*args, **kwargs):
        nonlocal now, allow_targeted, recovered, reject_hydration
        code.write_text("def saved_before_failure(): pass\n", encoding="utf-8")
        missed.write_text("def missed_after(): pass\n", encoding="utf-8")
        yield {(Change.modified, str(code))}
        for delay in (5, 10, 20, 40, 80, 160):
            now += delay
            yield set()
        assert reindex_state.get_reindex_status(result["repo"])["retry_in_ms"] == 300000
        full_walks = sum(paths is None for paths in requests)
        allow_targeted = True
        reject_hydration = True
        for _ in range(2):
            now += 1
            code.write_text(original, encoding="utf-8")
            # Root directory notifications must not turn a leaf edit into a walk.
            yield {(Change.modified, str(code)), (Change.modified, str(root))}
            assert requests[-1] is not None
            assert sum(paths is None for paths in requests) == full_walks
            expected = "saved_before_failure" if targeted_failure else "before"
            assert names() == {expected, "missed_before"}
            status = reindex_state.get_reindex_status(result["repo"])
            assert status["retry_in_ms"] == round((615 - now) * 1000)
            assert status["last_reindex_error"] == "writer busy"
            assert status["index_stale"] is True
        reject_hydration = False
        recovered = True
        now = 615.0
        yield set()
        assert names() == {"before", "missed_after"}
        assert sum(paths is None for paths in requests) == full_walks + 1
        assert "retry_in_ms" not in reindex_state.get_reindex_status(result["repo"])

        reject_hydration = True
        code.write_text("def normal_edit(): pass\n", encoding="utf-8")
        yield {(Change.modified, str(code))}
        assert names() == {"normal_edit", "missed_after"}
        yield {(Change.modified, str(code))}  # unchanged-content fast return
        assert sum(paths is None for paths in requests) == full_walks + 1
        indexed_requests = len(requests)
        now += 365 * 24 * 60 * 60
        yield set()
        yield set()
        assert len(requests) == indexed_requests, "healthy idle time must not index anything"
        reject_hydration = False

    monkeypatch.setattr(watcher, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(reindex_state, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(watcher, "index_folder", index)
    monkeypatch.setattr(type(store), "load_index", load_index)
    monkeypatch.setattr(watcher, "_safe_awatch", events)
    await watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    )


async def test_failed_initial_index_recovers_files_with_no_events(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from jcodemunch_mcp import reindex_state, watcher
    from jcodemunch_mcp.storage import IndexStore

    pytest.importorskip("watchfiles")
    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    code.write_text("def before(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    real_index = watcher.index_folder
    result = real_index(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    code.write_text("def changed_before_watch(): pass\n", encoding="utf-8")
    added = root / "added.py"
    added.write_text("def added_before_watch(): pass\n", encoding="utf-8")
    failures_left = 3
    now = 0.0

    def index(**kwargs):
        nonlocal failures_left
        if failures_left:
            failures_left -= 1
            return {"success": False, "error": "writer busy"}
        return real_index(**kwargs)

    async def events(*args, **kwargs):
        nonlocal now
        for delay in (5, 10, 20):
            status = reindex_state.get_reindex_status(result["repo"])
            assert status.get("retry_in_ms") == delay * 1000
            now += delay
            yield set()
        owner, name = result["repo"].split("/", 1)
        index = IndexStore(base_path=storage).load_index(owner, name)
        assert index is not None
        assert {symbol["name"] for symbol in index.symbols} == {"changed_before_watch", "added_before_watch"}
        assert reindex_state.get_reindex_status(result["repo"])["index_stale"] is False

    monkeypatch.setattr(watcher, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(reindex_state, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(watcher, "index_folder", index)
    monkeypatch.setattr(watcher, "_safe_awatch", events)
    await watcher._watch_single(str(root), 200, False, storage, None, False, quiet=True, context_providers=False)
