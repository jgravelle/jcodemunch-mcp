"""A watch event the watcher cannot place under its root does not fail the batch (#1582).

An indexed folder was moved to another volume and a symlink left at the old
path. watchfiles then reported RESOLVED paths, and `_watch_single` called
`Path(p).relative_to(folder_path)` on them with the symlink's spelling as the
root. The ValueError failed the whole batch: every edit in it was dropped, the
failure was recorded, and the scheduled retry named the same path again.

An event under the root's resolved spelling is the same file and is rewritten
onto the root's own spelling. An event under neither spelling is skipped, with
one warning per task, and the rest of its batch is indexed.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from jcodemunch_mcp import watcher
from jcodemunch_mcp.tools.index_folder import index_folder


@pytest.fixture()
def change():
    """watchfiles' Change enum; skips each test, visibly, when the extra is absent."""
    return pytest.importorskip("watchfiles").Change


def _moved_behind_a_link(tmp_path) -> tuple[Path, Path, str]:
    """The reported layout: the checkout lives elsewhere, the old path is a link."""
    real = tmp_path / "volume" / "checkout"
    real.mkdir(parents=True)
    (real / "code.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    link = tmp_path / "checkout"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"cannot create a directory symlink here: {exc}")
    storage = str(tmp_path / "store")
    result = index_folder(str(link), use_ai_summaries=False, storage_path=storage, context_providers=False)
    assert result["success"] is True, result
    return link, real, storage


def _run(monkeypatch, root: Path, storage: str, batches: list[set]) -> tuple[list[dict], list[str]]:
    """Feed the watch loop `batches`; return every `index_folder` call and every output line."""
    calls: list[dict] = []
    lines: list[str] = []

    async def fake_awatch(folder_path, debounce_ms, yield_idle=False):
        for batch in batches:
            yield batch
        await asyncio.sleep(3600)
        yield set()

    monkeypatch.setattr(watcher, "_safe_awatch", fake_awatch)
    monkeypatch.setattr(watcher, "index_folder", lambda **kw: calls.append(kw) or {"success": True, "message": "No changes detected"})
    monkeypatch.setattr(watcher, "_watcher_output", lambda msg, **kw: lines.append(msg))

    async def drive():
        task = asyncio.create_task(watcher._watch_single(
            str(root), 200, False, storage, None, False,
            skip_initial_index=True, quiet=True, context_providers=False,
        ))
        for _ in range(30):
            await asyncio.sleep(0.05)
            if task.done():
                task.result()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    return calls, lines


def _failures(lines: list[str]) -> list[str]:
    return [line for line in lines if "ERROR" in line or "re-index failed" in line]


def test_an_event_under_the_resolved_root_is_indexed_under_the_roots_spelling(tmp_path, monkeypatch, change):
    """The reported shape: the edit arrives under the link's target."""
    link, real, storage = _moved_behind_a_link(tmp_path)
    (real / "code.py").write_text("def second():\n    return 2\n", encoding="utf-8")
    calls, lines = _run(monkeypatch, link, storage, [{(change.modified, str(real / "code.py"))}])
    assert not _failures(lines), lines
    assert len(calls) == 1, calls
    assert not calls[0]["discover"], calls
    assert [c.path for c in calls[0]["changed_paths"]] == [str(link / "code.py")]


def test_the_resolved_root_itself_is_read_as_the_root(tmp_path, monkeypatch, change):
    """A rescan event spelled through the target still triggers discovery."""
    link, real, storage = _moved_behind_a_link(tmp_path)
    calls, lines = _run(monkeypatch, link, storage, [{(change.modified, str(real))}])
    assert not _failures(lines), lines
    assert len(calls) == 1 and calls[0]["discover"] is True, calls


def test_an_event_outside_the_root_is_skipped_and_the_batch_still_indexed(tmp_path, monkeypatch, change):
    link, real, storage = _moved_behind_a_link(tmp_path)
    outside = tmp_path / "elsewhere" / "x.py"
    outside.parent.mkdir()
    outside.write_text("x = 1\n", encoding="utf-8")
    (real / "code.py").write_text("def second():\n    return 2\n", encoding="utf-8")
    calls, lines = _run(monkeypatch, link, storage, [
        {(change.modified, str(outside)), (change.modified, str(link / "code.py"))},
        {(change.modified, str(outside))},
    ])
    assert not _failures(lines), lines
    assert len(calls) == 1, calls
    assert [c.path for c in calls[0]["changed_paths"]] == [str(link / "code.py")]
    warnings = [line for line in lines if line.lstrip().startswith("WARNING: ignoring")]
    assert len(warnings) == 1 and str(outside) in warnings[0], lines


def test_spell_under_root(tmp_path):
    real = tmp_path / "real"
    (real / "pkg").mkdir(parents=True)
    root = str(tmp_path / "plain")
    os.mkdir(root)
    # A path already under the root's spelling is returned as given.
    assert watcher._spell_under_root(os.path.join(root, "a.py"), root) == os.path.join(root, "a.py")
    assert watcher._spell_under_root(root, root) == root
    # A path under neither spelling cannot be placed.
    assert watcher._spell_under_root(str(real / "pkg" / "a.py"), root) is None
    assert watcher._spell_under_root(str(tmp_path / "plainer" / "a.py"), root) is None
