"""Native registration must not traverse a workspace's symlink graph."""

import asyncio
import os
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jcodemunch_mcp import watcher


@pytest.fixture
def explicit_watches(monkeypatch):
    """Exercise the bounded Linux path even on hosts with native recursion."""
    monkeypatch.setattr(watcher, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", "false")


@pytest.fixture
def workspace(tmp_path):
    for name in ("a", "b", "c"):
        package = tmp_path / "packages" / name
        (package / "src").mkdir(parents=True)
        (package / "node_modules").mkdir()
        (package / "src" / "code.py").write_text("def original(): pass\n")
    try:
        # Fan-out plus cycles: real paths stay small, alias paths do not.
        for name in ("a", "b", "c"):
            for target in ("a", "b", "c"):
                (tmp_path / "packages" / name / "node_modules" / target).symlink_to(
                    tmp_path / "packages" / target, target_is_directory=True
                )
        (tmp_path / "alias").symlink_to(tmp_path / "packages", target_is_directory=True)
        (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
        (tmp_path / "broken").symlink_to(tmp_path / "missing", target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    return tmp_path


def test_directory_walk_is_bounded_by_real_tree(workspace):
    paths = watcher._watch_directories(str(workspace))
    assert len(paths) == 8  # root, packages, three packages and their src dirs
    assert all("node_modules" not in p for p in paths)
    assert all(not os.path.islink(p) for p in paths)
    assert all(len(identity) == 2 for identity in paths.values())


def test_external_symlink_and_skipped_directory_are_not_registered(tmp_path):
    """An external symlink and a directory on discovery's skip list are not registered; a
    dot-directory that discovery does NOT skip (`.github`, `.claude`) is, because discovery
    indexes files there and an unwatched directory is an edit never seen (#629 review)."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".github").mkdir()
    (root / ".git").mkdir()
    (root / "node_modules").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "external").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    assert set(watcher._watch_directories(str(root))) == {str(root), str(root / ".github")}


def test_every_directory_discovery_indexes_is_watched(tmp_path):
    """The property behind the test above: for every file discovery would index, its parent
    directory is in the watch set, so the watcher's pruning can never be stricter than
    discovery's. Symlinked directories are the one deliberate exception (their files reach
    discovery only under `follow_symlinks`, and #629 is about never traversing them)."""
    from pathlib import Path

    from jcodemunch_mcp.tools.index_folder import discover_local_files

    root = tmp_path / "repo"
    for rel in (".github/workflows", ".claude/hooks", "src/pkg", "docs", ".git/objects", "node_modules/x"):
        (root / rel).mkdir(parents=True)
        (root / rel / "f.py").write_text("x = 1\n")
    discovered = discover_local_files(root, 1000, 512000, None, False)
    files = discovered[0] if isinstance(discovered, tuple) else discovered
    parents = {str(Path(f).parent) for f in files}
    assert parents, "discovery found nothing; the fixture is wrong"
    watched = set(watcher._watch_directories(str(root)))
    missing = sorted(p for p in parents if p not in watched)
    assert missing == [], missing
    assert str(root / ".github" / "workflows") in watched  # non-vacuity: a dot-directory discovery indexes
    assert str(root / ".git" / "objects") not in watched  # and one it skips


def test_directory_identity_changes_on_same_path_replacement(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    before = watcher._watch_directories(str(tmp_path))
    child.rename(tmp_path / "old")
    child.mkdir()
    after = watcher._watch_directories(str(tmp_path))
    assert before[str(child)] != after[str(child)]


@pytest.mark.asyncio
@pytest.mark.parametrize("platform,polling_env,auto_polling,recursive", [
    ("linux", "false", False, False),
    ("darwin", "false", False, True),
    ("win32", "disabled", False, True),
    ("darwin", "0", False, False),
    ("darwin", None, True, False),
])
async def test_backend_selection_preserves_reconciliation_and_closure(
    workspace, monkeypatch, platform, polling_env, auto_polling, recursive,
):
    watchfiles = pytest.importorskip("watchfiles")
    monkeypatch.setattr(watcher, "sys", SimpleNamespace(platform=platform))
    if polling_env is None:
        monkeypatch.delenv("WATCHFILES_FORCE_POLLING", raising=False)
    else:
        monkeypatch.setenv("WATCHFILES_FORCE_POLLING", polling_env)
    monkeypatch.setattr("watchfiles.main._auto_force_polling", lambda: auto_polling)
    if recursive:
        monkeypatch.setattr(watcher, "_watch_directories", lambda _: pytest.fail("native recursion must not census"))
    calls = []
    closed = []

    async def fake_awatch(*paths, **kwargs):
        calls.append((paths, kwargs))
        try:
            yield {(watchfiles.Change.modified, str(workspace / "file.py"))}
        finally:
            closed.append(True)

    with patch.object(watchfiles, "awatch", fake_awatch):
        async with aclosing(watcher._safe_awatch(str(workspace), 200)) as stream:
            assert await anext(stream) == {
                (watchfiles.Change.modified, str(workspace))
            }
    assert len(calls) == 1
    expected = {str(workspace)} if recursive else set(watcher._watch_directories(str(workspace)))
    assert set(calls[0][0]) == expected
    assert calls[0][1]["recursive"] is recursive
    assert calls[0][1]["yield_on_timeout"] is True
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["add", "delete", "replace"])
async def test_topology_refresh_closes_old_watch_and_requests_rescan(tmp_path, operation, explicit_watches):
    watchfiles = pytest.importorskip("watchfiles")
    root = str(tmp_path)
    child = tmp_path / "child"
    if operation != "add":
        child.mkdir()
    calls = []
    closed = []

    async def fake_awatch(*paths, **kwargs):
        calls.append(paths)
        generation = len(calls)
        if generation == 2:
            assert closed == [1]  # no accumulation of native watchers
        try:
            if generation == 1:
                yield set()  # Arm and reconcile first, then lose a topology event.
                if operation == "add":
                    (child / "nested").mkdir(parents=True)
                    (child / "nested" / "new.py").write_text("def new(): pass\n")
                elif operation == "delete":
                    child.rmdir()
                else:
                    child.rename(tmp_path / "old")
                    child.mkdir()
            yield set()  # timeout must detect topology even without events
        finally:
            closed.append(generation)

    ticks = iter(range(0, 1000, 61))
    with patch.object(watchfiles, "awatch", fake_awatch), patch.object(
        watcher, "time", SimpleNamespace(monotonic=lambda: next(ticks))
    ):
        async with aclosing(watcher._safe_awatch(root, 200)) as stream:
            assert await anext(stream) == {(watchfiles.Change.modified, root)}
            assert await anext(stream) == {(watchfiles.Change.modified, root)}
    assert len(calls) == 2
    assert set(calls[1]) == set(watcher._watch_directories(root))
    assert closed == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete", "rename"])
async def test_registration_retries_when_child_disappears(tmp_path, operation, explicit_watches):
    watchfiles = pytest.importorskip("watchfiles")
    root = str(tmp_path)
    child = tmp_path / "child"
    child.mkdir()
    native_awatch = watchfiles.awatch
    attempts = []

    def race(*paths, **kwargs):
        attempts.append(set(paths))
        if len(attempts) == 1:
            if operation == "delete":
                child.rmdir()
            else:
                child.rename(tmp_path / "renamed")
        return native_awatch(*paths, **kwargs)

    with patch.object(watchfiles, "awatch", race):
        async with aclosing(watcher._safe_awatch(root, 200)) as stream:
            assert await asyncio.wait_for(anext(stream), 5) == {(watchfiles.Change.modified, root)}
            assert len(attempts) == 2
            assert str(child) in attempts[0] and str(child) not in attempts[1]
            assert attempts[1] == set(watcher._watch_directories(root))
            target = tmp_path / "after.py"
            target.write_text("def after_retry(): pass\n")
            async def observe_edit():
                async for changes in stream:
                    if any(path == str(target) for _, path in changes):
                        return True
            assert await asyncio.wait_for(observe_edit(), 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("remove_root,error_type", [
    (True, FileNotFoundError), (False, FileNotFoundError), (False, PermissionError),
])
async def test_registration_does_not_retry_unrecoverable_errors(tmp_path, remove_root, error_type, explicit_watches):
    watchfiles = pytest.importorskip("watchfiles")
    failure = error_type("registration failed")
    attempts = []

    async def fail(*paths, **kwargs):
        attempts.append(paths)
        if remove_root:
            tmp_path.rmdir()
        raise failure
        yield set()

    with patch.object(watchfiles, "awatch", fail):
        async with aclosing(watcher._safe_awatch(str(tmp_path), 200)) as stream:
            with pytest.raises(error_type) as caught:
                await asyncio.wait_for(anext(stream), 5)
    assert caught.value is failure
    assert len(attempts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["file_edit", "polling_file_add", "directory_metadata"])
async def test_regular_edits_do_not_rescan_directory_tree(tmp_path, operation, explicit_watches):
    watchfiles = pytest.importorskip("watchfiles")
    target = tmp_path / "code.py"
    target.write_text("pass\n")

    events = {(watchfiles.Change.modified, str(target))}
    if operation == "polling_file_add":
        events = {(watchfiles.Change.added, str(target)), (watchfiles.Change.modified, str(tmp_path))}
    elif operation == "directory_metadata":
        events = {(watchfiles.Change.modified, str(tmp_path))}

    async def fake_awatch(*paths, **kwargs):
        for _ in range(3):
            yield events

    with patch.object(watchfiles, "awatch", fake_awatch), patch.object(
        watcher, "_watch_directories", wraps=watcher._watch_directories
    ) as discover, patch.object(watcher, "time", SimpleNamespace(monotonic=lambda: 0)):
        batches = [batch async for batch in watcher._safe_awatch(str(tmp_path), 200)]
    assert batches[0] == {(watchfiles.Change.modified, str(tmp_path))}
    assert batches[1:] == [events] * 3
    assert discover.call_count == 2  # initial enumeration and post-arm verification only


@pytest.mark.asyncio
async def test_missing_root_fails_instead_of_silently_stopping(tmp_path):
    pytest.importorskip("watchfiles")
    async with aclosing(watcher._safe_awatch(str(tmp_path / "gone"), 200)) as stream:
        with pytest.raises(FileNotFoundError):
            await anext(stream)


@pytest.mark.asyncio
@pytest.mark.parametrize("polling", [False, True], ids=["native", "polling"])
async def test_removing_watched_root_fails(tmp_path, monkeypatch, polling):
    pytest.importorskip("watchfiles")
    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", "true" if polling else "false")
    async with aclosing(watcher._safe_awatch(str(tmp_path), 200)) as stream:
        await asyncio.wait_for(anext(stream), 5)
        tmp_path.rmdir()
        async def consume():
            async for _ in stream:
                pass
        with pytest.raises(FileNotFoundError):
            await asyncio.wait_for(consume(), 5)


@pytest.mark.asyncio
async def test_real_watcher_rearms_new_tree_and_reconciles_deletion(workspace, explicit_watches):
    """Exercise nested edits and topology with real native watches and symlink cycles."""
    watchfiles = pytest.importorskip("watchfiles")
    root = str(workspace)
    marker = workspace / "packages" / "a" / "src" / "code.py"
    observed = asyncio.Queue()

    async def consume():
        async with aclosing(watcher._safe_awatch(root, 200)) as stream:
            async for batch in stream:
                await observed.put(batch)

    async def wait_for_path(path):
        while True:
            batch = await observed.get()
            if any(p == str(path) for _, p in batch):
                return batch

    async def write_until_observed(path):
        async def edit():
            while True:
                path.write_text("def changed(): pass\n")
                await asyncio.sleep(0.1)
        editor = asyncio.create_task(edit())
        try:
            await asyncio.wait_for(wait_for_path(path), 10)
        finally:
            editor.cancel()
            await asyncio.gather(editor, return_exceptions=True)

    consumer = asyncio.create_task(consume())
    try:
        await write_until_observed(marker)  # proves native registration is ready
        target = workspace / "new" / "nested" / "code.py"
        target.parent.mkdir(parents=True)
        target.write_text("def before_registration(): pass\n")
        assert await asyncio.wait_for(wait_for_path(root), 10) == {
            (watchfiles.Change.modified, root)
        }
        await write_until_observed(target)  # new nested directory is watched
        target.unlink()
        target.parent.rmdir()
        target.parent.parent.rmdir()
        await asyncio.wait_for(wait_for_path(root), 10)
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
