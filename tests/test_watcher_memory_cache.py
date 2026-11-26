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

    def test_a_cyclic_symlink_does_not_fail_the_batch(self, tmp_path, monkeypatch):
        """On Python 3.10-3.12, `Path.resolve()` raises RuntimeError for a
        cyclic symlink. The .gitignore chain once resolved the file before
        `_should_index_file` refused the symlink, so one `link.py` failed the
        whole batch (Codex review on #1567). The loop error is emulated, so
        the test holds on every Python version."""
        from pathlib import Path as _Path

        from jcodemunch_mcp.storage import IndexStore
        from jcodemunch_mcp.tools.index_folder import index_folder

        kept = tmp_path / "kept.py"
        kept.write_text("def before():\n    return 1\n")
        link = tmp_path / "link.py"
        try:
            os.symlink(str(link), str(link))
        except (OSError, NotImplementedError):
            pytest.skip("this host cannot create a symlink")
        storage = str(tmp_path / ".code-index")
        result = index_folder(
            path=str(tmp_path), use_ai_summaries=False, storage_path=storage, incremental=False,
        )
        assert result["success"], result
        real_resolve = _Path.resolve

        def resolve(self, *args, **kwargs):
            if self.name == "link.py":
                raise RuntimeError(f"Symlink loop from {self!r}")
            return real_resolve(self, *args, **kwargs)

        monkeypatch.setattr(_Path, "resolve", resolve)
        kept.write_text("def after():\n    return 2\n")
        result2 = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=True,
            changed_paths=[
                WatcherChange("added", str(link), ""),
                WatcherChange("modified", str(kept), "__cache_miss__"),
            ],
        )
        monkeypatch.undo()
        assert result2["success"], result2
        owner, name = result["repo"].split("/", 1)
        index = IndexStore(base_path=storage).load_index(owner, name)
        assert {symbol["name"] for symbol in index.symbols} == {"after"}
        assert "link.py" not in (index.file_hashes or {})

    @pytest.mark.parametrize("change", ["added", "modified"])
    def test_file_under_nested_gitignore_stays_unindexed(self, tmp_path, change):
        """The fast path reads the .gitignore of each folder from the root down
        to the changed file, as the walk does. It once read the root's only
        (#306), so an event indexed a file that a nested .gitignore excludes
        (Codex review on #1567)."""
        from jcodemunch_mcp.storage import IndexStore
        from jcodemunch_mcp.tools.index_folder import index_folder

        sub = tmp_path / "sub"
        deep = sub / "deep"
        deep.mkdir(parents=True)
        (sub / ".gitignore").write_text("private.py\n")
        (deep / ".gitignore").write_text("secret.py\n")
        kept = sub / "kept.py"
        kept.write_text("def kept():\n    return 1\n")
        private = sub / "private.py"
        secret = deep / "secret.py"
        storage = str(tmp_path / ".code-index")
        if change == "modified":
            private.write_text("def private():\n    return 1\n")
            secret.write_text("def secret():\n    return 1\n")
        result = index_folder(
            path=str(tmp_path), use_ai_summaries=False, storage_path=storage, incremental=False,
        )
        assert result["success"]
        for path in (private, secret):
            path.write_text("def leaked():\n    return 2\n")
        kept.write_text("def kept():\n    return 2\n")
        old_hash = "__cache_miss__" if change == "modified" else ""
        result2 = index_folder(
            path=str(tmp_path),
            use_ai_summaries=False,
            storage_path=storage,
            incremental=True,
            changed_paths=[
                WatcherChange(change, str(private.resolve()), old_hash),
                WatcherChange(change, str(secret.resolve()), old_hash),
                WatcherChange("modified", str(kept.resolve()), "__cache_miss__"),
            ],
        )
        assert result2["success"], result2
        owner, name = result["repo"].split("/", 1)
        files = set(IndexStore(base_path=storage).load_index(owner, name).file_hashes or {})
        assert "sub/kept.py" in files
        assert "sub/private.py" not in files and "sub/deep/secret.py" not in files, sorted(files)

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


@pytest.mark.parametrize("shape", ["retry", "edit_at_retry_deadline", "folder_event"])
async def test_retry_rereads_failed_files_past_the_folder_cap(tmp_path, monkeypatch, shape):
    """A walk finds changes by mtime, and index_folder keeps a file past
    max_folder_files without reading it again unless a changed path names it.
    An edit to such a file must reach the index after a full retry, after a
    full retry whose wake-up also carries a new edit, and after a batch that a
    folder event made into a walk."""
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

    blocked = shape != "folder_event"

    def index(**kwargs):
        if blocked:
            return {"success": False, "error": "writer busy"}
        return real_index(**kwargs)

    now = 0.0

    def edit(paths):
        for path in paths:
            path.write_text(f"def new{files.index(path)}(): pass\n", encoding="utf-8")
        return {(Change.modified, str(path)) for path in paths}

    async def events(*args, **kwargs):
        nonlocal now, blocked
        if shape == "folder_event":
            # The root event (the rescan _safe_awatch yields) makes it a walk.
            yield edit(files) | {(Change.modified, str(root))}
        else:
            failed = files if shape == "retry" else files[:1]
            yield edit(failed)
            assert names() == {"old0", "old1", "old2"}
            blocked = False
            now = 5.0
            # At the deadline the batch becomes the root event; a new edit in
            # that batch must still be named to the walk.
            yield edit(files[len(failed):]) if len(failed) < len(files) else set()
        assert names() == {"new0", "new1", "new2"}, "the walk kept a capped file's old copy"
        index = store.load_index(owner, name)
        # Every file is read now, so the stored cap status and coverage must
        # not still call the index partial (search_text refuses absence then).
        assert (index.file_cap_status or {}).get("truncated") is not True, index.file_cap_status
        assert (index.coverage or {}).get("complete") is not False, index.coverage
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


async def test_retry_reparses_a_failed_edit_that_kept_its_mtime(tmp_path, monkeypatch):
    """A walk finds changes by mtime. An edit that keeps the old mtime (a
    timestamp-preserving replacement) and whose save failed is still known to
    the watcher, so the full retry must parse that file again."""
    import os
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
    owner, name = result["repo"].split("/", 1)
    store = IndexStore(base_path=storage)
    stat = code.stat()
    blocked = True

    def index(**kwargs):
        if blocked:
            return {"success": False, "error": "writer busy"}
        return real_index(**kwargs)

    now = 0.0

    async def events(*args, **kwargs):
        nonlocal now, blocked
        code.write_text("def after(): pass\n", encoding="utf-8")
        os.utime(code, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        yield {(Change.modified, str(code))}
        blocked = False
        now = 5.0
        yield set()
        names = {symbol["name"] for symbol in store.load_index(owner, name).symbols}
        assert names == {"after"}, "the retry trusted the old mtime"
        assert reindex_state.get_reindex_status(result["repo"])["index_stale"] is False

    monkeypatch.setattr(watcher, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(reindex_state, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(watcher, "index_folder", index)
    monkeypatch.setattr(watcher, "_safe_awatch", events)
    await watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    )


async def test_a_capped_walk_keeps_files_a_nested_gitignore_excludes(tmp_path, monkeypatch):
    """The walk applies every discovery filter. Naming a file to it must not
    index a file that a nested .gitignore excludes."""
    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp import watcher
    from jcodemunch_mcp.storage import IndexStore

    Change = pytest.importorskip("watchfiles").Change
    root = tmp_path / "repo"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (sub / ".gitignore").write_text("private.py\n", encoding="utf-8")
    sources = [root / "a.py", root / "b.py"]
    for i, path in enumerate(sources):
        path.write_text(f"def old{i}(): pass\n", encoding="utf-8")
    private = sub / "private.py"
    storage = str(tmp_path / "store")
    result = watcher.index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    owner, name = result["repo"].split("/", 1)
    store = IndexStore(base_path=storage)
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_folder_files", 1)

    async def events(*args, **kwargs):
        for i, path in enumerate(sources):
            path.write_text(f"def new{i}(): pass\n", encoding="utf-8")
        private.write_text("def secret(): pass\n", encoding="utf-8")
        yield {(Change.modified, str(root)), (Change.added, str(private))} | {
            (Change.modified, str(path)) for path in sources
        }
        index = store.load_index(owner, name)
        assert "sub/private.py" not in (index.file_hashes or {}), "a nested .gitignore was undone"
        assert {symbol["name"] for symbol in index.symbols} == {"new0", "new1"}

    monkeypatch.setattr(watcher, "_safe_awatch", events)
    await watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    )


async def test_a_targeted_edit_during_a_retry_keeps_a_nested_gitignore(tmp_path, monkeypatch):
    """During a pending retry, a batch with the root event takes the targeted
    path, because the walk waits for the retry deadline. A file that a nested
    .gitignore excludes must stay out of the index (Codex review on #1567)."""
    from types import SimpleNamespace

    from jcodemunch_mcp import reindex_state, watcher
    from jcodemunch_mcp.storage import IndexStore

    Change = pytest.importorskip("watchfiles").Change
    root = tmp_path / "repo"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (sub / ".gitignore").write_text("private.py\n", encoding="utf-8")
    code = root / "code.py"
    code.write_text("def before(): pass\n", encoding="utf-8")
    private = sub / "private.py"
    storage = str(tmp_path / "store")
    real_index = watcher.index_folder
    result = real_index(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    owner, name = result["repo"].split("/", 1)
    store = IndexStore(base_path=storage)
    calls = []

    def index(**kwargs):
        calls.append(kwargs.get("discover"))
        if len(calls) == 1:
            return {"success": False, "error": "writer busy"}
        return real_index(**kwargs)

    now = 0.0

    async def events(*args, **kwargs):
        nonlocal now
        code.write_text("def failed(): pass\n", encoding="utf-8")
        yield {(Change.modified, str(code))}
        assert reindex_state.get_reindex_status(result["repo"])["retry_in_ms"] > 0
        now = 1.0
        code.write_text("def targeted(): pass\n", encoding="utf-8")
        private.write_text("def secret(): pass\n", encoding="utf-8")
        yield {(Change.modified, str(root)), (Change.added, str(private)), (Change.modified, str(code))}
        assert calls[-1] is not True, "the batch must take the targeted path"
        index_now = store.load_index(owner, name)
        assert "sub/private.py" not in (index_now.file_hashes or {}), "a nested .gitignore was undone"
        assert {symbol["name"] for symbol in index_now.symbols} == {"targeted"}

    monkeypatch.setattr(watcher, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(reindex_state, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(watcher, "index_folder", index)
    monkeypatch.setattr(watcher, "_safe_awatch", events)
    await watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    )


def _fail_reads_of(monkeypatch, path, discovery=False):
    """Make `index_folder`'s own reads of one file raise, as a sharing
    violation does on Windows. With ``discovery``, the binary sniff of the
    discovery filters (`security.is_binary_file`) fails too, so discovery
    rejects the file before it is read. Returns a switch that ends the failure."""
    import builtins

    from jcodemunch_mcp import security
    from jcodemunch_mcp.tools import index_folder as module

    state = {"fail": True}
    real_open = builtins.open

    def fake_open(file, *args, **kwargs):
        if state["fail"] and os.path.normcase(os.path.abspath(str(file))) == os.path.normcase(str(path)):
            raise PermissionError(13, "The process cannot access the file", str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(module, "open", fake_open, raising=False)
    if discovery:
        monkeypatch.setattr(security, "open", fake_open, raising=False)
    return state


def _same_files(paths):
    return sorted(os.path.normcase(str(Path(p).resolve())) for p in paths)


@pytest.mark.parametrize("route", ["walk", "fast_path", "paths"])
def test_a_file_discovery_cannot_read_keeps_its_symbols(tmp_path, monkeypatch, route):
    """A file whose binary sniff fails is on disk: the discovery filters
    return `unreadable`, not a verdict on the file. The walk and the listed
    route once treated it as deleted and the fast path dropped the event,
    each with `success`, so the watcher cleared its retry and the file stayed
    out of the index (Codex review on #1567)."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    code.write_text("def before(): pass\n", encoding="utf-8")
    (root / "other.py").write_text("def other(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    owner, name = result["repo"].split("/", 1)
    store = IndexStore(base_path=storage)
    code.write_text("def after(): pass\n", encoding="utf-8")
    reads = _fail_reads_of(monkeypatch, code, discovery=True)

    def run():
        if route == "paths":
            return index_folder(
                str(root), use_ai_summaries=False, context_providers=False,
                storage_path=storage, incremental=True, paths=[str(code)],
            )
        return index_folder(
            str(root), use_ai_summaries=False, context_providers=False, storage_path=storage,
            incremental=True, discover=route == "walk",
            changed_paths=[WatcherChange("modified", str(code), "__cache_miss__")],
        )

    failed = run()
    assert failed["success"], failed
    names = {symbol["name"] for symbol in store.load_index(owner, name).symbols}
    assert names == {"before", "other"}, "an unreadable file was deleted"
    assert _same_files(failed.get("unread_files") or []) == _same_files([code]), failed
    reads["fail"] = False
    recovered = run()
    assert "unread_files" not in recovered, recovered
    names = {symbol["name"] for symbol in store.load_index(owner, name).symbols}
    assert names == {"after", "other"}


def test_a_listed_directory_drops_an_unreadable_file_an_ancestor_ignores(tmp_path, monkeypatch):
    """For `paths=['sub']` the subtree walk reads only the .gitignore files
    inside `sub`; the ones above it are applied after. An unreadable file
    there once skipped that filter, so a file the root .gitignore now
    excludes kept its rows and was reported as pending (Codex review on
    #1567)."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    sub = root / "sub"
    sub.mkdir(parents=True)
    private = sub / "private.py"
    private.write_text("def private(): pass\n", encoding="utf-8")
    (sub / "public.py").write_text("def public(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    owner, name = result["repo"].split("/", 1)
    (root / ".gitignore").write_text("sub/private.py\n", encoding="utf-8")
    _fail_reads_of(monkeypatch, private, discovery=True)
    listed = index_folder(
        str(root), use_ai_summaries=False, context_providers=False,
        storage_path=storage, incremental=True, paths=[str(sub)],
    )
    assert listed["success"], listed
    assert "unread_files" not in listed, listed
    names = {symbol["name"] for symbol in IndexStore(base_path=storage).load_index(owner, name).symbols}
    assert names == {"public"}, "a file the root .gitignore excludes kept its rows"


def test_a_no_change_walk_records_an_unreadable_file_in_coverage(tmp_path, monkeypatch):
    """A walk whose only delta is a file it cannot read returns `No changes
    detected`. That return wrote coverage only when the cap status moved, so
    the index kept `complete: True` while it held old rows for the file, and
    `search_text` reported its text as absent (Codex review on #1567). The
    first walk that reads the file again clears the count."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    code.write_text("def before(): pass\n", encoding="utf-8")
    (root / "other.py").write_text("def other(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    owner, name = result["repo"].split("/", 1)
    store = IndexStore(base_path=storage)
    assert store.load_index(owner, name).coverage["complete"] is True
    code.write_text("def after(): pass\n", encoding="utf-8")
    reads = _fail_reads_of(monkeypatch, code, discovery=True)

    def walk():
        return index_folder(
            str(root), use_ai_summaries=False, context_providers=False,
            storage_path=storage, incremental=True,
        )

    locked = walk()
    assert locked["message"] == "No changes detected", locked
    coverage = store.load_index(owner, name).coverage
    assert coverage["complete"] is False, coverage
    assert coverage["withheld"] == {"unreadable": 1}, coverage
    reads["fail"] = False
    walk()
    coverage = store.load_index(owner, name).coverage
    assert coverage["complete"] is True, coverage
    assert "withheld" not in coverage, coverage


def test_a_kept_file_is_read_again_when_its_mtime_did_not_move(tmp_path, monkeypatch):
    """A replacement that keeps the mtime and is unreadable during a walk keeps
    the old rows. When it is readable again, the mtime check alone skipped it
    and its text stayed absent (Codex review on #1567). The unreadable record
    makes the next walk read it."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    code.write_text("def before(): pass\n", encoding="utf-8")
    (root / "other.py").write_text("def other(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    owner, name = result["repo"].split("/", 1)
    stat = code.stat()
    code.write_text("def after(): pass\n", encoding="utf-8")
    os.utime(code, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    reads = _fail_reads_of(monkeypatch, code, discovery=True)

    def walk():
        return index_folder(
            str(root), use_ai_summaries=False, context_providers=False,
            storage_path=storage, incremental=True,
        )

    walk()
    reads["fail"] = False
    walk()
    names = {symbol["name"] for symbol in IndexStore(base_path=storage).load_index(owner, name).symbols}
    assert names == {"after", "other"}, "a kept file was never read again"


@pytest.mark.parametrize("a_edited", [False, True])
def test_a_subfolder_walk_keeps_a_sibling_unreadable_entry(tmp_path, monkeypatch, a_edited):
    """A walk of `a/` once compared its own unreadable count with the coverage
    of the whole index and recorded `complete: True` over an unreadable file
    in `b/` (Codex review on #1567). With ``a_edited`` the walk of `a/` saves
    a change, so the save path's record is checked as well as the no-change
    path's."""
    import subprocess

    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "a" / "ok.py").write_text("def in_a(): pass\n", encoding="utf-8")
    locked = root / "b" / "locked.py"
    locked.write_text("def in_b(): pass\n", encoding="utf-8")
    (root / "b" / "fine.py").write_text("def also_b(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")

    def walk(folder):
        result = index_folder(
            str(root / folder), use_ai_summaries=False, context_providers=False,
            storage_path=storage, incremental=True,
        )
        assert result["success"], result
        return result

    walk("b")
    first = walk("a")
    owner, name = first["repo"].split("/", 1)
    locked.write_text("def in_b_changed(): pass\n", encoding="utf-8")
    _fail_reads_of(monkeypatch, locked, discovery=True)
    walk("b")
    if a_edited:
        (root / "a" / "ok.py").write_text("def in_a_changed(): pass\n", encoding="utf-8")
    walked = walk("a")
    assert (walked.get("message") == "No changes detected") is not a_edited, walked
    coverage = IndexStore(base_path=storage).load_index(owner, name).coverage
    assert coverage["complete"] is False, coverage
    assert "b/locked.py" in coverage.get("unreadable_files", []), coverage


def test_a_changed_directory_names_its_unreadable_children(tmp_path, monkeypatch):
    """A tree moved in sends 1 directory event. A locked file under it was
    not named in `unread_files`, so the watcher cleared its retry and the
    file stayed out of the index (Codex review on #1567)."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("def a(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    moved = root / "moved"
    moved.mkdir()
    locked = moved / "x.py"
    locked.write_text("def x(): pass\n", encoding="utf-8")
    _fail_reads_of(monkeypatch, locked, discovery=True)
    walked = index_folder(
        str(root), use_ai_summaries=False, context_providers=False, storage_path=storage,
        incremental=True, discover=True, changed_paths=[WatcherChange("added", str(moved), "")],
    )
    assert walked["success"], walked
    assert _same_files(walked.get("unread_files") or []) == _same_files([locked]), walked


def test_a_root_event_names_only_the_files_named_beside_it(tmp_path, monkeypatch):
    """A rescan or a full retry names the root, whose path is `.`. A root
    event that named every recorded unreadable file put a file that stays
    locked, with no edit of it pending, into a permanent full-walk retry from
    the first rescan of every watch session. The root names nothing by
    itself; a file a failed batch named, sent beside it as the watcher's full
    retry does, is still named."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    (root / "a.py").write_text("def a(): pass\n", encoding="utf-8")
    locked = root / "sub" / "code.py"
    locked.write_text("def code(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    _fail_reads_of(monkeypatch, locked, discovery=True)

    def walk(*extra):
        result = index_folder(
            str(root), use_ai_summaries=False, context_providers=False, storage_path=storage,
            incremental=True, discover=True,
            changed_paths=[WatcherChange("modified", str(root), "__cache_miss__"), *extra],
        )
        assert result["success"], result
        return result

    assert "unread_files" not in walk()
    named = walk(WatcherChange("modified", str(locked), "__cache_miss__"))
    assert _same_files(named.get("unread_files") or []) == _same_files([locked]), named


async def test_a_rescan_over_a_locked_file_schedules_no_retry(tmp_path, monkeypatch):
    """Every watch session starts with a root rescan. With a recorded file
    that stays locked and no edit of it pending, that rescan once named the
    file, and the watcher retried a full walk at the capped interval for as
    long as it ran. The coverage record already withholds the file, so the
    retry bought nothing. One index call, no retry."""
    from types import SimpleNamespace

    from jcodemunch_mcp import reindex_state, watcher

    Change = pytest.importorskip("watchfiles").Change
    root = tmp_path / "repo"
    root.mkdir()
    locked = root / "locked.py"
    locked.write_text("def locked(): pass\n", encoding="utf-8")
    (root / "other.py").write_text("def other(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    real_index = watcher.index_folder
    result = real_index(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    _fail_reads_of(monkeypatch, locked, discovery=True)
    calls = []

    def index(**kwargs):
        outcome = real_index(**kwargs)
        calls.append(outcome.get("unread_files"))
        return outcome

    now = 0.0

    async def events(*args, **kwargs):
        nonlocal now
        yield {(Change.modified, str(root))}
        for now in (400.0, 800.0, 1200.0, 1600.0):
            yield set()
        assert "retry_in_ms" not in reindex_state.get_reindex_status(result["repo"])

    monkeypatch.setattr(watcher, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(reindex_state, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(watcher, "index_folder", index)
    monkeypatch.setattr(watcher, "_safe_awatch", events)
    await watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    )
    assert calls == [None], calls


def test_a_listed_root_that_cannot_be_listed_keeps_the_index(tmp_path, monkeypatch):
    """`paths=["."]` over a root that answers a stat but cannot be listed
    dropped the root from the record and deleted every stored file (Codex
    review on #1567)."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("def a(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    owner, name = result["repo"].split("/", 1)
    _deny_listing(monkeypatch, root)
    listed = index_folder(
        str(root), use_ai_summaries=False, context_providers=False,
        storage_path=storage, incremental=True, paths=["."],
    )
    assert listed["success"] is False, listed
    assert {symbol["name"] for symbol in IndexStore(base_path=storage).load_index(owner, name).symbols} == {"a"}


def test_a_full_rebuild_names_the_files_it_could_not_read(tmp_path, monkeypatch):
    """A full rebuild that could not read a file dropped it with no record
    and no `unread_files`, so the watcher cleared its retry and the file
    stayed out of the index (Codex review on #1567)."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    code.write_text("def code(): pass\n", encoding="utf-8")
    (root / "other.py").write_text("def other(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    owner, name = result["repo"].split("/", 1)
    _fail_reads_of(monkeypatch, code)
    rebuilt = index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage, incremental=False)
    assert rebuilt["success"], rebuilt
    assert _same_files(rebuilt.get("unread_files") or []) == _same_files([code]), rebuilt
    coverage = IndexStore(base_path=storage)._sqlite.get_coverage(owner, name)
    assert coverage.get("unreadable_files") == ["code.py"], coverage


async def test_a_head_reconcile_with_unread_files_is_not_complete(tmp_path, monkeypatch):
    """The HEAD reconcile took `success` with `unread_files` as complete and
    returned `_HEAD_RECONCILED`, so no retry was scheduled (Codex review on
    #1567)."""
    from jcodemunch_mcp import reindex_state, watcher

    locked = str(tmp_path / "locked.py")
    monkeypatch.setattr(watcher, "_get_git_head", lambda path: "new")
    monkeypatch.setattr(watcher, "_stored_git_head", lambda store, owner, name: "old")
    monkeypatch.setattr(watcher, "index_folder", lambda **kwargs: {"success": True, "unread_files": [locked]})
    pending: set = set()
    outcome = await watcher._reconcile_head_move(
        folder_path=str(tmp_path), repo_id="local/head-reconcile", store=None,
        use_ai_summaries=False, storage_path=None, extra_ignore_patterns=None,
        follow_symlinks=False, on_reindex=None, quiet=True, log_file_handle=None,
        build_hash_cache=lambda: None, unread_out=pending,
    )
    assert outcome == watcher._HEAD_FAILED
    assert pending == {locked}
    assert reindex_state.get_reindex_status("local/head-reconcile")["index_stale"] is True


def test_a_subfolder_walk_keeps_a_sibling_size_exclusion(tmp_path, monkeypatch):
    """A no-change walk of `a/` that the unreadable record makes rewrite
    coverage counted only the size exclusions of `a/`, so it dropped the
    `too_large` file of `b/` and coverage read complete (Codex review on
    #1567)."""
    import subprocess

    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_file_size", 200)
    root = tmp_path / "repo"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    locked = root / "a" / "locked.py"
    locked.write_text("def in_a(): pass\n", encoding="utf-8")
    (root / "a" / "ok.py").write_text("def also_a(): pass\n", encoding="utf-8")
    (root / "b" / "big.py").write_text("x = 1\n" * 100, encoding="utf-8")
    (root / "b" / "fine.py").write_text("def in_b(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")

    def walk(folder):
        result = index_folder(
            str(root / folder), use_ai_summaries=False, context_providers=False,
            storage_path=storage, incremental=True,
        )
        assert result["success"], result
        return result

    walk("b")
    owner, name = walk("a")["repo"].split("/", 1)
    reads = _fail_reads_of(monkeypatch, locked, discovery=True)
    walk("a")
    reads["fail"] = False
    walk("a")
    coverage = IndexStore(base_path=storage)._sqlite.get_coverage(owner, name)
    assert coverage["complete"] is False, coverage
    assert coverage["withheld"] == {"too_large": 1}, coverage


def test_an_unreadable_file_nobody_named_keeps_its_rows_and_no_retry(tmp_path, monkeypatch):
    """A walk that cannot read a stored file it was not asked about keeps the
    rows and names nothing in `unread_files`: no edit of it is pending, so a
    file locked for a long time does not hold the watcher in a retry."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    root.mkdir()
    locked = root / "locked.py"
    locked.write_text("def locked(): pass\n", encoding="utf-8")
    edited = root / "edited.py"
    edited.write_text("def before(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    owner, name = result["repo"].split("/", 1)
    edited.write_text("def after(): pass\n", encoding="utf-8")
    _fail_reads_of(monkeypatch, locked, discovery=True)
    walked = index_folder(
        str(root), use_ai_summaries=False, context_providers=False, storage_path=storage,
        incremental=True, discover=True,
        changed_paths=[WatcherChange("modified", str(edited), "__cache_miss__")],
    )
    assert walked["success"], walked
    assert "unread_files" not in walked, walked
    names = {symbol["name"] for symbol in IndexStore(base_path=storage).load_index(owner, name).symbols}
    assert names == {"locked", "after"}


def _deny_listing(monkeypatch, directory, traversal=False):
    """Make `os.walk` fail to list one directory, as a permission error does.
    With ``traversal``, a stat of a path under it fails too, as `chmod 0` does
    on POSIX. Returns a switch that ends the denial."""
    state = {"deny": True}
    real_scandir, real_stat = os.scandir, os.stat
    top = os.path.normcase(str(directory))

    def denied(path):
        return state["deny"] and os.path.normcase(os.path.abspath(os.fspath(path))) == top

    def fake_scandir(path="."):
        if denied(path):
            raise PermissionError(13, "Access is denied", os.fspath(path))
        return real_scandir(path)

    def fake_stat(path, *args, **kwargs):
        if state["deny"] and isinstance(path, (str, os.PathLike)) and denied(os.path.dirname(os.path.abspath(os.fspath(path)))):
            raise PermissionError(13, "Access is denied", os.fspath(path))
        return real_stat(path, *args, **kwargs)

    real_isfile = os.path.isfile

    def fake_isfile(path):
        # POSIX `isfile` reads a failed stat as False. Windows has a C fast
        # path that a patched `os.stat` does not reach, so it is patched too.
        try:
            fake_stat(path)
        except PermissionError:
            return False
        return real_isfile(path)

    monkeypatch.setattr(os, "scandir", fake_scandir)
    if traversal:
        monkeypatch.setattr(os, "stat", fake_stat)
        monkeypatch.setattr(os.path, "isfile", fake_isfile)
    return state


@pytest.mark.parametrize("named", [False, True])
def test_a_deleted_file_leaves_a_directory_that_cannot_be_listed(tmp_path, monkeypatch, named):
    """A directory that denies listing still answers a stat. A stored file
    deleted inside it once kept its rows as unread, because the walk read
    every stored file under an unlisted directory as unverified. The stat
    proves it gone (`_confirmed_gone`), so its rows leave; the readable
    sibling in the same directory keeps its rows."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (root / "a.py").write_text("def a(): pass\n", encoding="utf-8")
    gone = sub / "gone.py"
    gone.write_text("def gone(): pass\n", encoding="utf-8")
    (sub / "kept.py").write_text("def kept(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    owner, name = result["repo"].split("/", 1)
    gone.unlink()
    _deny_listing(monkeypatch, sub)
    changes = [WatcherChange("deleted", str(gone), "")] if named else None
    walked = index_folder(
        str(root), use_ai_summaries=False, context_providers=False, storage_path=storage,
        incremental=True, discover=True, changed_paths=changes,
    )
    assert walked["success"], walked
    names = {symbol["name"] for symbol in IndexStore(base_path=storage).load_index(owner, name).symbols}
    assert names == {"a", "kept"}, names


def test_a_targeted_update_keeps_an_unnamed_unreadable_count(tmp_path):
    """An index written before the unreadable record counts unreadable files
    with no names. A targeted update that changed a parse failure rewrote that
    count from the empty record and made coverage complete (Codex review on
    #1567)."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    code.write_text("def before(): pass\n", encoding="utf-8")
    (root / "other.py").write_text("def other(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    owner, name = result["repo"].split("/", 1)
    store = IndexStore(base_path=storage)
    legacy = dict(store._sqlite.get_coverage(owner, name))
    legacy.update(
        complete=False, parse_failed_files=["code.py"],
        skip_counts={"unreadable": 1, "parse_failed": 1},
        withheld={"unreadable": 1, "parse_failed": 1},
    )
    store._sqlite.set_coverage(owner, name, legacy)
    code.write_text("def after(): pass\n", encoding="utf-8")
    index_folder(
        str(root), use_ai_summaries=False, context_providers=False, storage_path=storage,
        incremental=True, changed_paths=[WatcherChange("modified", str(code), "__cache_miss__")],
    )
    coverage = store._sqlite.get_coverage(owner, name)
    assert coverage["complete"] is False, coverage
    assert coverage["withheld"] == {"unreadable": 1}, coverage


def test_a_root_walk_hashes_every_file_before_it_clears_an_unnamed_count(tmp_path):
    """An unreadable count with no names may cover a kept row whose
    replacement kept its mtime. The root walk cleared the count without
    reading that file, so coverage read complete over the old symbols
    (Codex review on #1567)."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    code.write_text("def before(): pass\n", encoding="utf-8")
    (root / "other.py").write_text("def other(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    owner, name = result["repo"].split("/", 1)
    store = IndexStore(base_path=storage)
    legacy = dict(store._sqlite.get_coverage(owner, name))
    legacy.update(complete=False, skip_counts={"unreadable": 1}, withheld={"unreadable": 1})
    store._sqlite.set_coverage(owner, name, legacy)
    stat = code.stat()
    code.write_text("def after(): pass\n", encoding="utf-8")
    os.utime(code, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage, incremental=True)
    index = store.load_index(owner, name)
    assert {symbol["name"] for symbol in index.symbols} == {"after", "other"}
    assert index.coverage["complete"] is True, index.coverage


def test_the_verifying_walk_stores_the_hash_of_the_parsed_bytes(tmp_path, monkeypatch):
    """The walk that hashes every file reads a changed file again to parse
    it. An edit between the 2 reads stored the first read's hash with the
    second read's symbols, so a revert to the first content read as no
    change and kept the wrong symbols (Codex review on #1567)."""
    import builtins

    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools import index_folder as module

    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    code.write_text("def v0(): pass\n", encoding="utf-8")
    (root / "other.py").write_text("def other(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = module.index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    owner, name = result["repo"].split("/", 1)
    store = IndexStore(base_path=storage)
    legacy = dict(store._sqlite.get_coverage(owner, name))
    legacy.update(complete=False, skip_counts={"unreadable": 1}, withheld={"unreadable": 1})
    store._sqlite.set_coverage(owner, name, legacy)
    code.write_text("def v1(): pass\n", encoding="utf-8")
    real_open, opens = builtins.open, []

    def fake_open(file, mode="r", *args, **kwargs):
        if "r" in mode and os.path.normcase(os.path.abspath(str(file))) == os.path.normcase(str(code)):
            opens.append(file)
            if len(opens) == 2:
                with real_open(code, "w", encoding="utf-8") as f:
                    f.write("def v2(): pass\n")
        return real_open(file, mode, *args, **kwargs)

    def walk():
        module.index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage, incremental=True)
        return {symbol["name"] for symbol in store.load_index(owner, name).symbols}

    with monkeypatch.context() as patched:
        patched.setattr(module, "open", fake_open, raising=False)
        assert walk() == {"v2", "other"}
    code.write_text("def v1(): pass\n", encoding="utf-8")
    later = code.stat().st_mtime_ns + 2_000_000_000
    os.utime(code, ns=(later, later))
    assert walk() == {"v1", "other"}, "a revert to the first read kept the second read's symbols"


def test_a_listed_directory_read_again_leaves_the_record(tmp_path, monkeypatch):
    """`paths=['sub']` records `sub/` when it cannot list it. A later call
    that lists `sub` again removed only the files it read, so `sub/` stayed
    and coverage read incomplete until a full walk (Codex review on #1567)."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (root / "a.py").write_text("def a(): pass\n", encoding="utf-8")
    (sub / "x.py").write_text("def x(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    owner, name = result["repo"].split("/", 1)

    def listed():
        return index_folder(
            str(root), use_ai_summaries=False, context_providers=False,
            storage_path=storage, incremental=True, paths=[str(sub)],
        )

    denied = _deny_listing(monkeypatch, sub)
    listed()
    coverage = IndexStore(base_path=storage)._sqlite.get_coverage(owner, name)
    assert coverage.get("unreadable_files") == ["sub/"], coverage
    denied["deny"] = False
    listed()
    coverage = IndexStore(base_path=storage)._sqlite.get_coverage(owner, name)
    assert "unreadable_files" not in coverage and coverage["complete"] is True, coverage


def test_an_unchanged_file_index_file_reads_leaves_the_record(tmp_path):
    """`index_file` reads and hashes a file whose bytes did not change and
    returns `File unchanged` before the write that amends the record, so a
    file that is readable again stayed in it (Codex review on #1567)."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_file import index_file
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    code.write_text("def same(): pass\n", encoding="utf-8")
    (root / "other.py").write_text("def other(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    owner, name = result["repo"].split("/", 1)
    store = IndexStore(base_path=storage)
    coverage = dict(store._sqlite.get_coverage(owner, name))
    coverage.update(complete=False, unreadable_files=["code.py"], skip_counts={"unreadable": 1}, withheld={"unreadable": 1})
    store._sqlite.set_coverage(owner, name, coverage)
    repaired = index_file(str(code), use_ai_summaries=False, storage_path=storage, context_providers=False)
    assert repaired["message"] == "File unchanged", repaired
    coverage = store._sqlite.get_coverage(owner, name)
    assert "unreadable_files" not in coverage and coverage["complete"] is True, coverage


def test_a_capped_walk_keeps_the_record_of_a_kept_file_under_an_unread_directory(tmp_path, monkeypatch):
    """A walk that could not list `sub/` records `sub/`. A later capped walk
    kept `sub/x.py` unread, but matched only file entries, so it dropped
    `sub/` and the kept file was never read again (Codex review on #1567)."""
    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (root / "a.py").write_text("def a(): pass\n", encoding="utf-8")
    kept = sub / "x.py"
    kept.write_text("def before(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")

    def walk():
        result = index_folder(
            str(root), use_ai_summaries=False, context_providers=False,
            storage_path=storage, incremental=True,
        )
        assert result["success"], result
        return result

    owner, name = walk()["repo"].split("/", 1)
    stat = kept.stat()
    kept.write_text("def after(): pass\n", encoding="utf-8")
    os.utime(kept, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with monkeypatch.context() as denied:
        _deny_listing(denied, sub)
        walk()
    with monkeypatch.context() as capped:
        capped.setitem(config_module._GLOBAL_CONFIG, "max_folder_files", 1)
        walk()
    walk()
    names = {symbol["name"] for symbol in IndexStore(base_path=storage).load_index(owner, name).symbols}
    assert names == {"a", "after"}, "a kept file under an unread directory was never read again"


def test_a_named_file_under_an_unlisted_directory_stays_pending(tmp_path, monkeypatch):
    """A walk that cannot list the directory of a named change keeps the file's
    rows. It named only unreadable files in `unread_files`, so the watcher
    cleared the retry with the edit unindexed (Codex review on #1567)."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (root / "a.py").write_text("def a(): pass\n", encoding="utf-8")
    edited = sub / "x.py"
    edited.write_text("def before(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    edited.write_text("def after(): pass\n", encoding="utf-8")
    _deny_listing(monkeypatch, sub)
    walked = index_folder(
        str(root), use_ai_summaries=False, context_providers=False, storage_path=storage,
        incremental=True, discover=True,
        changed_paths=[WatcherChange("modified", str(edited), "__cache_miss__")],
    )
    assert walked["success"], walked
    assert _same_files(walked.get("unread_files") or []) == _same_files([edited]), walked


@pytest.mark.parametrize("route", ["walk", "fast_path"])
def test_an_unread_named_file_keeps_its_symbols(tmp_path, monkeypatch, route):
    """A named file that `index_folder` cannot read keeps its stored rows, and
    the result names it in `unread_files`. The walk once kept it in `changed`
    with no content, so its symbols were deleted and the call still reported
    success (Codex review on #1567)."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    code.write_text("def before(): pass\n", encoding="utf-8")
    (root / "other.py").write_text("def other(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    owner, name = result["repo"].split("/", 1)
    store = IndexStore(base_path=storage)
    stat = code.stat()
    code.write_text("def after(): pass\n", encoding="utf-8")
    # The mtime stays, so only the named reread can see the edit.
    os.utime(code, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    reads = _fail_reads_of(monkeypatch, code)

    def run():
        return index_folder(
            str(root), use_ai_summaries=False, context_providers=False, storage_path=storage,
            incremental=True, discover=route == "walk",
            changed_paths=[WatcherChange("modified", str(code), "__cache_miss__")],
        )

    failed = run()
    assert failed["success"], failed
    names = {symbol["name"] for symbol in store.load_index(owner, name).symbols}
    assert names == {"before", "other"}, "an unread file lost its symbols"
    assert failed.get("unread_files") == [str(code)], failed
    reads["fail"] = False
    recovered = run()
    assert "unread_files" not in recovered, recovered
    names = {symbol["name"] for symbol in store.load_index(owner, name).symbols}
    assert names == {"after", "other"}


@pytest.mark.parametrize("where", ["read", "discovery", "directory", "deleted", "added_directory"])
async def test_a_retry_with_an_unread_file_stays_pending(tmp_path, monkeypatch, where):
    """A full retry that cannot read a named file keeps the retry, the stale
    flag and the file. It once cleared them and lost the edit. With
    ``discovery``, the walk's filters reject the file before the read, and
    the walk once deleted it. With ``directory``, its folder denies listing
    and stat, and the watcher once dropped the name as a deleted file. With
    ``deleted``, the failed batch deleted the file in that folder, and with
    ``added_directory`` it added a folder that cannot be listed: the watcher
    once kept neither pending (Codex review on #1567)."""
    from types import SimpleNamespace

    from jcodemunch_mcp import reindex_state, watcher
    from jcodemunch_mcp.storage import IndexStore

    Change = pytest.importorskip("watchfiles").Change
    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    code = root / "sub" / "code.py"
    code.write_text("def before(): pass\n", encoding="utf-8")
    # A second readable file, so the walk does not stop on an empty corpus.
    (root / "other.py").write_text("def other(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    real_index = watcher.index_folder
    result = real_index(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    owner, name = result["repo"].split("/", 1)
    store = IndexStore(base_path=storage)
    calls = 0

    def index(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"success": False, "error": "writer busy"}
        return real_index(**kwargs)

    now = 0.0

    def names():
        return {symbol["name"] for symbol in store.load_index(owner, name).symbols}

    added = root / "added"
    expected = {
        "deleted": {"other"},
        "added_directory": {"before", "other", "new"},
    }.get(where, {"after", "other"})

    async def events(*args, **kwargs):
        nonlocal now
        if where == "deleted":
            code.unlink()
            yield {(Change.deleted, str(code))}
        elif where == "added_directory":
            added.mkdir()
            (added / "new.py").write_text("def new(): pass\n", encoding="utf-8")
            yield {(Change.added, str(added))}
        else:
            stat = code.stat()
            code.write_text("def after(): pass\n", encoding="utf-8")
            os.utime(code, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            yield {(Change.modified, str(code))}
        if where in ("directory", "deleted"):
            reads = _deny_listing(monkeypatch, code.parent, traversal=True)
        elif where == "added_directory":
            reads = _deny_listing(monkeypatch, added)
        else:
            reads = _fail_reads_of(monkeypatch, code, discovery=where == "discovery")
        now = 5.0
        yield set()
        assert calls == 2
        assert names() == {"before", "other"}, "an unread file lost its symbols"
        status = reindex_state.get_reindex_status(result["repo"])
        assert status["index_stale"] is True
        assert status["retry_in_ms"] == 10000
        # A full retry names the root, so an unlisted folder is named beside
        # its file: the count is not the property, the recorded failure is.
        assert "could not read " in status["last_reindex_error"]
        reads["fail"] = reads["deny"] = False
        now = 15.0
        yield set()
        assert names() == expected
        assert "retry_in_ms" not in reindex_state.get_reindex_status(result["repo"])

    monkeypatch.setattr(watcher, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(reindex_state, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(watcher, "index_folder", index)
    monkeypatch.setattr(watcher, "_safe_awatch", events)
    await watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    )


async def test_an_initial_index_with_unread_files_schedules_a_retry(tmp_path, monkeypatch):
    """The initial index re-reads a recorded unreadable file. If it is still
    locked, the index returns `success` with `unread_files`, and the watcher
    once marked the index current and scheduled no retry, so the old symbols
    stayed after the lock ended (Codex review on #1567)."""
    from types import SimpleNamespace

    from jcodemunch_mcp import reindex_state, watcher
    from jcodemunch_mcp.storage import IndexStore

    root = tmp_path / "repo"
    root.mkdir()
    code = root / "code.py"
    code.write_text("def before(): pass\n", encoding="utf-8")
    (root / "other.py").write_text("def other(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = watcher.index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    owner, name = result["repo"].split("/", 1)
    store = IndexStore(base_path=storage)
    coverage = dict(store._sqlite.get_coverage(owner, name))
    coverage.update(complete=False, unreadable_files=["code.py"], skip_counts={"unreadable": 1}, withheld={"unreadable": 1})
    store._sqlite.set_coverage(owner, name, coverage)
    stat = code.stat()
    code.write_text("def after(): pass\n", encoding="utf-8")
    os.utime(code, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    reads = _fail_reads_of(monkeypatch, code)
    now = 0.0

    async def events(*args, **kwargs):
        nonlocal now
        status = reindex_state.get_reindex_status(result["repo"])
        assert status["index_stale"] is True and "retry_in_ms" in status, status
        reads["fail"] = False
        now = 5.0
        yield set()
        assert {symbol["name"] for symbol in store.load_index(owner, name).symbols} == {"after", "other"}

    monkeypatch.setattr(watcher, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(reindex_state, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(watcher, "_safe_awatch", events)
    await watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=False, quiet=True, context_providers=False,
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
        # A walk (discover=True) is recorded as None, a targeted run as its paths.
        requests.append(None if kwargs.get("discover") else kwargs["changed_paths"])
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


async def test_the_tool_response_bounds_unread_files_and_the_watcher_keeps_all(tmp_path, monkeypatch):
    """A `paths=` call names every unreadable file in scope in `unread_files`.
    The MCP response caps the list and says so (`unread_files_total`,
    `unread_files_truncated`), as a list field on 1.x must before it ships.
    A direct call, which the watcher and `refresh` make, keeps every name:
    their retry needs each one."""
    import builtins
    import json
    from unittest.mock import patch

    from jcodemunch_mcp import security
    from jcodemunch_mcp.server import call_tool
    from jcodemunch_mcp.tools import index_folder as module
    from jcodemunch_mcp.tools.index_folder import UNREAD_FILES_CAP, index_folder

    root = tmp_path / "repo"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (root / "a.py").write_text("def a(): pass\n", encoding="utf-8")
    locked = []
    for i in range(UNREAD_FILES_CAP + 5):
        f = sub / f"m{i}.py"
        f.write_text(f"def m{i}(): pass\n", encoding="utf-8")
        locked.append(f)
    storage = str(tmp_path / "store")
    index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    denied = {os.path.normcase(str(f)) for f in locked}
    real_open = builtins.open

    def fake_open(file, *args, **kwargs):
        if os.path.normcase(os.path.abspath(str(file))) in denied:
            raise PermissionError(13, "The process cannot access the file", str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(module, "open", fake_open, raising=False)
    monkeypatch.setattr(security, "open", fake_open, raising=False)
    direct = index_folder(
        str(root), use_ai_summaries=False, context_providers=False, storage_path=storage,
        incremental=True, paths=["sub"],
    )
    assert direct["success"], direct
    assert _same_files(direct["unread_files"]) == _same_files(locked), direct
    assert "unread_files_total" not in direct, direct

    with patch("jcodemunch_mcp.tools.index_folder.index_folder", return_value=dict(direct)):
        served = json.loads((await call_tool("index_folder", {"path": str(root), "paths": ["sub"]}))[0].text)
    assert len(served["unread_files"]) == UNREAD_FILES_CAP, served
    assert served["unread_files_total"] == len(locked), served
    assert served["unread_files_truncated"] is True, served

    one = {"success": True, "unread_files": [str(locked[0])]}
    with patch("jcodemunch_mcp.tools.index_folder.index_folder", return_value=one):
        served = json.loads((await call_tool("index_folder", {"path": str(root)}))[0].text)
    assert served["unread_files"] == [str(locked[0])], served
    assert served["unread_files_total"] == 1 and served["unread_files_truncated"] is False, served


def test_a_repaired_new_file_leaves_coverage_complete(tmp_path, monkeypatch):
    """A new file whose read failed after discovery accepted it was counted
    twice: as accepted and not indexed (`unaccounted`), and as unreadable. A
    later `index_file` stored it and cleared the record, but the
    `unaccounted` count stayed, and the no-change walks after it rewrote no
    coverage, so absence claims stayed refused for good (Codex review on
    #1567). The file now counts once, as withheld."""
    import builtins

    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools import index_folder as module
    from jcodemunch_mcp.tools.index_file import index_file
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("def a(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    owner, name = result["repo"].split("/", 1)
    new = root / "b.py"
    new.write_text("", encoding="utf-8")
    real_open = builtins.open
    reads = {"n": 0}
    target = os.path.normcase(str(new))

    def fake_open(file, *args, **kwargs):
        # The hash read succeeds; the read for the parse is refused, as a
        # sharing violation does between the two.
        if os.path.normcase(os.path.abspath(str(file))) == target:
            reads["n"] += 1
            if reads["n"] >= 2:
                raise PermissionError(13, "The process cannot access the file", str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(module, "open", fake_open, raising=False)
    walked = index_folder(
        str(root), use_ai_summaries=False, context_providers=False, storage_path=storage, incremental=True,
    )
    assert _same_files(walked.get("unread_files") or []) == _same_files([new]), walked
    monkeypatch.setattr(module, "open", real_open, raising=False)
    store = IndexStore(base_path=storage)
    failed = store._sqlite.get_coverage(owner, name)
    assert failed["complete"] is False and "unaccounted" not in failed, failed
    new.write_text("def b(): pass\n", encoding="utf-8")
    assert index_file(str(new), use_ai_summaries=False, storage_path=storage, context_providers=False)["success"]
    index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage, incremental=True)
    coverage = store._sqlite.get_coverage(owner, name)
    assert coverage["complete"] is True, coverage
    assert {symbol["name"] for symbol in store.load_index(owner, name).symbols} == {"a", "b"}


def test_a_file_change_detection_cannot_read_leaves_the_rest_of_the_walk(tmp_path, monkeypatch):
    """Change detection reads an edited file to hash it. A file that read
    refused (a sharing violation) raised out of change detection and failed
    the whole walk, so every other edit in it stayed unindexed while the lock
    held (L-350). The file is recorded as unread with its rows kept, and the
    rest of the walk is saved."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    root.mkdir()
    locked = root / "locked.py"
    locked.write_text("def locked_before(): pass\n", encoding="utf-8")
    other = root / "other.py"
    other.write_text("def other_before(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    owner, name = result["repo"].split("/", 1)
    locked.write_text("def locked_after(): pass\n", encoding="utf-8")
    other.write_text("def other_after(): pass\n", encoding="utf-8")
    _fail_reads_of(monkeypatch, locked)
    walked = index_folder(
        str(root), use_ai_summaries=False, context_providers=False, storage_path=storage, incremental=True,
    )
    assert walked["success"], walked
    assert _same_files(walked.get("unread_files") or []) == _same_files([locked]), walked
    store = IndexStore(base_path=storage)
    assert {symbol["name"] for symbol in store.load_index(owner, name).symbols} == {"locked_before", "other_after"}
    coverage = store._sqlite.get_coverage(owner, name)
    assert coverage["complete"] is False and "locked.py" in coverage.get("unreadable_files", []), coverage


async def _watch_one_batch(watcher, monkeypatch, root, storage, batch):
    """Run _watch_single over one batch; return (task, waiter, done).

    ``waiter`` completes when the watcher asks for the next batch, which it
    does only once it has handled this one.
    """
    import asyncio

    reached = asyncio.Event()

    async def events(*args, **kwargs):
        yield batch()
        reached.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(watcher, "_safe_awatch", events)
    task = asyncio.create_task(watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    ))
    waiter = asyncio.create_task(reached.wait())
    done, _ = await asyncio.wait({task, waiter}, timeout=20, return_when=asyncio.FIRST_COMPLETED)
    return task, waiter, done


@pytest.mark.parametrize("spelling", ["outside", "symlinked_root"])
async def test_an_event_the_watcher_cannot_place_under_its_root_does_not_fail_the_batch(tmp_path, monkeypatch, spelling):
    """#1582 (@Braxhim): `Path(p).relative_to(folder_path)` raised on an event
    path outside the root's spelling. The handler caught it, so the whole
    batch failed: the edits that came with it were not indexed, the repo was
    marked failed and a retry was scheduled, on every such batch. ``outside``
    is a path under no spelling of the root, which is dropped.
    ``symlinked_root`` watches the root through a symlink while the events
    name the resolved path, the reporter's moved folder: the same file, which
    is indexed."""
    import asyncio

    from jcodemunch_mcp import reindex_state, watcher
    from jcodemunch_mcp.storage.index_store import IndexStore

    Change = pytest.importorskip("watchfiles").Change
    _skip_service_probe(monkeypatch)
    real = tmp_path / "real"
    real.mkdir()
    if spelling == "symlinked_root":
        root = tmp_path / "link"
        try:
            os.symlink(real, root, target_is_directory=True)
        except (OSError, NotImplementedError):
            # Windows without the symlink privilege: a directory junction
            # resolves the same way through os.path.realpath and needs none.
            import subprocess

            made = os.name == "nt" and subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(root), str(real)],
                capture_output=True,
            ).returncode == 0
            if not made:
                pytest.skip("this platform cannot create a directory link")
        event_dir = real
    else:
        root = real
        event_dir = real
    code = root / "code.py"
    code.write_text("def before(): pass\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    stray = elsewhere / "stray.py"
    stray.write_text("def stray(): pass\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = watcher.index_folder(str(root), use_ai_summaries=False, context_providers=False, storage_path=storage)
    assert result["success"], result
    owner, name = result["repo"].split("/", 1)

    calls = []
    real_index = watcher.index_folder

    def index(**kwargs):
        calls.append(kwargs)
        return real_index(**kwargs)

    monkeypatch.setattr(watcher, "index_folder", index)

    def first():
        code.write_text("def after(): pass\n", encoding="utf-8")
        batch = {(Change.modified, str(event_dir / "code.py"))}
        if spelling == "outside":
            batch.add((Change.modified, str(stray)))
        return batch

    task, waiter, done = await _watch_one_batch(watcher, monkeypatch, root, storage, first)
    try:
        assert task not in done, task.exception()
        assert waiter in done, "the watcher never finished the batch"
        status = reindex_state.get_reindex_status(result["repo"])
        names = {symbol["name"] for symbol in IndexStore(base_path=storage).load_index(owner, name).symbols}
    finally:
        task.cancel()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert "after" in names, names
    assert "retry_in_ms" not in status and not status.get("last_reindex_error"), status
    assert calls, "no re-index ran"
    assert all(str(stray) not in repr(call) for call in calls), calls


def test_place_under_root_respells_a_resolved_path_and_drops_a_foreign_one(tmp_path, monkeypatch):
    """#1582: every spelling `_place_under_root` meets. The symlink is
    simulated through ``os.path.realpath`` so the case runs where a directory
    symlink cannot be made (Windows without the privilege)."""
    from jcodemunch_mcp import watcher

    Change = pytest.importorskip("watchfiles").Change
    link = str(tmp_path / "link")
    real = str(tmp_path / "real")
    real_realpath = os.path.realpath

    def realpath(path):
        if path == link or path.startswith(link + os.sep):
            return real + path[len(link):]
        return real_realpath(path)

    monkeypatch.setattr(watcher.os.path, "realpath", realpath)
    inside = os.path.join(link, "pkg", "a.py")
    resolved = os.path.join(real, "pkg", "b.py")
    gone = os.path.join(real, "pkg", "deleted.py")
    foreign = str(tmp_path / "elsewhere" / "c.py")
    sibling = str(tmp_path / "linked" / "d.py")
    placed, dropped = watcher._place_under_root(
        [
            (Change.modified, link),
            (Change.modified, inside),
            (Change.added, resolved),
            (Change.deleted, gone),
            (Change.modified, real),
            (Change.modified, foreign),
            (Change.modified, sibling),
        ],
        link,
        realpath(link),
    )
    assert placed == [
        (Change.modified, link),
        (Change.modified, inside),
        (Change.added, os.path.join(link, "pkg", "b.py")),
        (Change.deleted, os.path.join(link, "pkg", "deleted.py")),
        (Change.modified, link),
    ]
    assert dropped == [foreign, sibling]

