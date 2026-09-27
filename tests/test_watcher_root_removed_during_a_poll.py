"""A watched root removed under the poller is `FileNotFoundError` (FINDINGS F-26).

`test_removing_watched_root_fails[polling]` failed three times on Windows:
in the local full tier on 2026-09-12 and 2026-09-26, then on CI on 2026-09-27
(run 36292769487). The third run kept the error:
`WatchfilesRustInternalError('error in underlying watcher: Access is denied.
(os error 5)')`, inside an ExceptionGroup. The poller can hit the removed root
before `_safe_awatch` gets a batch to stat it, and Windows answers a directory
in delete-pending state with access denied, not not-found. So which exception
escaped depended on who reached the gone directory first.

That test cannot choose the order; this one does. A fake stream yields one
batch, then removes the root and raises the error the poller raised, so the
race is lost every time.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import aclosing

import pytest

from jcodemunch_mcp import watcher

_DENIED = "error in underlying watcher: Access is denied. (os error 5)"


def _group(exc: Exception) -> Exception:
    if sys.version_info >= (3, 11):
        return ExceptionGroup("unhandled errors in a TaskGroup", [exc])  # noqa: F821
    eg = pytest.importorskip("exceptiongroup")
    return eg.ExceptionGroup("unhandled errors in a TaskGroup", [exc])


@pytest.fixture
def wf():
    """watchfiles and its Rust error type; skips per test, never per module."""
    watchfiles = pytest.importorskip("watchfiles")
    rust = pytest.importorskip("watchfiles._rust_notify")
    return watchfiles, rust.WatchfilesRustInternalError


def _fake_awatch(wf, root, remove_root: bool, wrap: bool):
    watchfiles, rust_error = wf

    async def fake(*_paths, **_kwargs):
        yield {(watchfiles.Change.modified, str(root / "a.py"))}
        if remove_root:
            (root / "a.py").unlink()
            root.rmdir()
        err = rust_error(_DENIED)
        raise _group(err) if wrap else err

    return fake


async def _drain(folder: str):
    async with aclosing(watcher._safe_awatch(folder, 200)) as stream:
        async for _ in stream:
            pass


@pytest.mark.parametrize("polling", [False, True], ids=["native", "polling"])
@pytest.mark.parametrize("wrap", [True, False], ids=["in-group", "bare"])
def test_the_poller_losing_the_race_still_reads_as_a_removed_root(wf, tmp_path, monkeypatch, polling, wrap):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", "true" if polling else "false")
    monkeypatch.setattr(wf[0], "awatch", _fake_awatch(wf, root, remove_root=True, wrap=wrap))

    with pytest.raises(FileNotFoundError, match="disappeared"):
        asyncio.run(asyncio.wait_for(_drain(str(root)), 10))


@pytest.mark.parametrize("polling", [False, True], ids=["native", "polling"])
def test_the_same_error_with_the_root_present_is_not_masked(wf, tmp_path, monkeypatch, polling):
    """Only a GONE root is translated; a real watcher error still surfaces."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", "true" if polling else "false")
    monkeypatch.setattr(wf[0], "awatch", _fake_awatch(wf, root, remove_root=False, wrap=False))

    with pytest.raises(wf[1]):
        asyncio.run(asyncio.wait_for(_drain(str(root)), 10))
