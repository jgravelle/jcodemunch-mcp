"""A folder reported as modified does not send the watcher into a full re-walk (L-256).

Writing a file inside a folder makes Windows report the folder itself as
modified, beside the file's own event. The watcher read any changed path that
is a directory as a topology change and called `index_folder` with
`changed_paths=None`, which re-discovers the whole repo: every file stat-ed,
gitignore-checked, secret-checked and opened for the binary test. Measured on
a maintainer box over one idle hour with 23 watched repos, 40 of 109 change
batches carried such a folder path, and the repos where agent sessions write
notes all day were re-walked on every note.

A modified folder carries no information its children's own events do not:
only an ADDED folder (a tree moved in) or the watched root (the watcher's own
rescan) needs discovery. A deleted folder is handled by the cached-prefix check
and is unchanged.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

watchfiles = pytest.importorskip("watchfiles")
from watchfiles import Change  # noqa: E402

from jcodemunch_mcp import watcher  # noqa: E402
from jcodemunch_mcp.tools.index_folder import index_folder  # noqa: E402


def _indexed(tmp_path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    (root / "notes").mkdir(parents=True)
    (root / "code.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    (root / "notes" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, storage_path=storage, context_providers=False)
    assert result["success"] is True, result
    return root, storage


def _run(monkeypatch, root: Path, storage: str, batches: list[set]) -> list[dict]:
    """Feed the watch loop `batches`, return the kwargs of every `index_folder` call."""
    calls: list[dict] = []

    async def fake_awatch(folder_path, debounce_ms, yield_idle=False):
        for batch in batches:
            yield batch
        await asyncio.sleep(3600)
        yield set()

    monkeypatch.setattr(watcher, "_safe_awatch", fake_awatch)
    monkeypatch.setattr(watcher, "index_folder", lambda **kw: calls.append(kw) or {"success": True, "message": "No changes detected"})

    async def drive():
        task = asyncio.create_task(watcher._watch_single(
            str(root), 200, False, storage, None, False,
            skip_initial_index=True, quiet=True, context_providers=False,
        ))
        for _ in range(50):
            await asyncio.sleep(0.05)
            if task.done():
                task.result()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    return calls


def _rewalks(calls: list[dict]) -> int:
    return sum(1 for c in calls if c.get("changed_paths") is None)


def test_a_new_file_beside_its_modified_folder_is_a_targeted_reindex(tmp_path, monkeypatch):
    """The reported shape: a note written into an existing folder."""
    root, storage = _indexed(tmp_path)
    new = root / "notes" / "b.py"
    new.write_text("def b():\n    return 2\n", encoding="utf-8")
    calls = _run(monkeypatch, root, storage, [{(Change.modified, str(root / "notes")), (Change.added, str(new))}])
    assert calls, "the new file was not indexed"
    assert _rewalks(calls) == 0, calls
    paths = {Path(c.path) for c in calls[0]["changed_paths"]}
    assert paths == {new}, paths


def test_a_modified_folder_alone_triggers_no_rewalk(tmp_path, monkeypatch):
    root, storage = _indexed(tmp_path)
    calls = _run(monkeypatch, root, storage, [{(Change.modified, str(root / "notes"))}])
    assert _rewalks(calls) == 0, calls


def test_an_added_folder_still_triggers_discovery(tmp_path, monkeypatch):
    """A tree moved in arrives as one added folder, with no events for its files."""
    root, storage = _indexed(tmp_path)
    moved = root / "moved_in"
    moved.mkdir()
    (moved / "c.py").write_text("def c():\n    return 3\n", encoding="utf-8")
    calls = _run(monkeypatch, root, storage, [{(Change.added, str(moved))}])
    assert _rewalks(calls) == 1, calls


def test_the_watchers_own_root_rescan_still_triggers_discovery(tmp_path, monkeypatch):
    """`_safe_awatch` yields `(modified, root)` after registering, to cover the arm gap."""
    root, storage = _indexed(tmp_path)
    calls = _run(monkeypatch, root, storage, [{(Change.modified, str(root))}])
    assert _rewalks(calls) == 1, calls


def test_a_modified_file_is_still_a_targeted_reindex(tmp_path, monkeypatch):
    root, storage = _indexed(tmp_path)
    (root / "code.py").write_text("def second():\n    return 2\n", encoding="utf-8")
    calls = _run(monkeypatch, root, storage, [{(Change.modified, str(root / "code.py"))}])
    assert calls and _rewalks(calls) == 0, calls
