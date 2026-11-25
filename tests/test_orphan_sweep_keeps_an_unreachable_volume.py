"""The serve-start orphan sweep keeps an index whose DRIVE is not there (L-347).

`cleanup_orphan_indexes()` runs at every server start and deleted an index
whenever `Path(source_root).is_dir()` was False. A folder on an unplugged USB
drive, a disconnected share or an unmounted volume is not a directory at that
moment either, so its index was deleted exactly like a removed worktree's, and
the user paid a full re-index when the drive came back.

Reproduced on main before the fix: a real index whose `source_root` names an
unmapped drive letter (`Z:\\proj`) -> `sweep deleted 1`, `.db` gone.

⚠ A `subst` drive does NOT reproduce it: `index_folder` resolves the subst
letter to the real folder before storing `source_root`, so the stored path
never names the letter. A real removable drive has no such alias, which is why
these tests write the drive path into the index meta directly.

⚠ A network folder is not probed at all. The sweep runs synchronously at
server start, and `isdir` on a share whose host is unreachable blocks until
the SMB connect times out (measured 2026-11-24; the figure is in the L-347 CHANGELOG entry). Keeping such
an index and probing it would put that stall on every start, so the test
fails if the sweep stats the path.

⚠ Each test branches on `os.name` instead of skipping: a skip on every POSIX
leg counts against the `ci.skips_*` Floors (review of this change).

⚠ NON-VACUITY. The kept-index tests fail on the pre-fix sweep; the control
(a folder removed from a present volume is still deleted) passes on both
sides, so this file cannot go green by the sweep ceasing to delete anything.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import string
import uuid

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.index_folder import index_folder

B = chr(92)


def _make_indexed_store(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    store_dir = tmp_path / "store"
    index_folder(
        str(project),
        use_ai_summaries=False,
        storage_path=str(store_dir),
        incremental=False,
        context_providers=False,
    )
    dbs = list(store_dir.glob("*.db"))
    assert len(dbs) == 1, dbs
    return store_dir, dbs[0]


def _point_index_at(db, root):
    conn = sqlite3.connect(str(db))
    try:
        for key in ("source_root", "git_root"):
            conn.execute("UPDATE meta SET value = ? WHERE key = ?", (root, key))
        conn.commit()
    finally:
        conn.close()


def _sweep(tmp_path, root):
    store_dir, db = _make_indexed_store(tmp_path)
    _point_index_at(db, root)
    deleted = IndexStore(base_path=str(store_dir)).cleanup_orphan_indexes()
    return deleted, db.exists()


def _absent_volume_path():
    if os.name == "nt":
        for letter in reversed(string.ascii_uppercase):
            if not os.path.exists(f"{letter}:{B}"):
                return f"{letter}:{B}proj"
        pytest.skip("every drive letter is mapped on this host")
    return f"/mnt/jcm-absent-{uuid.uuid4().hex[:8]}/proj"


def test_index_on_an_absent_volume_is_kept(tmp_path):
    """The defect: an unplugged drive (Windows) or unmounted volume (POSIX)."""
    root = _absent_volume_path()
    deleted, exists = _sweep(tmp_path, root)
    assert deleted == 0, f"the index of {root} was deleted while its volume was absent"
    assert exists


def test_a_path_from_another_os_is_kept(tmp_path):
    """A store read on another OS cannot check the volume: unknown keeps."""
    root = "/media/usb/proj" if os.name == "nt" else f"E:{B}proj"
    deleted, exists = _sweep(tmp_path, root)
    assert deleted == 0 and exists


def test_a_network_folder_is_kept_without_a_probe(tmp_path, monkeypatch):
    """No network I/O on the start path: the sweep must not stat a share."""
    root = f"{B}{B}jcm-absent-host.invalid{B}share{B}proj"
    store_dir, db = _make_indexed_store(tmp_path)
    _point_index_at(db, root)

    probed = []
    real_is_dir = pathlib.Path.is_dir
    real_isdir = os.path.isdir

    def spy_is_dir(self, *a, **k):
        if "jcm-absent-host" in str(self):
            probed.append(str(self))
        return real_is_dir(self, *a, **k)

    def spy_isdir(p):
        if "jcm-absent-host" in str(p):
            probed.append(str(p))
        return real_isdir(p)

    monkeypatch.setattr(pathlib.Path, "is_dir", spy_is_dir)
    monkeypatch.setattr(os.path, "isdir", spy_isdir)
    deleted = IndexStore(base_path=str(store_dir)).cleanup_orphan_indexes()

    assert deleted == 0 and db.exists(), "a network folder's index was deleted"
    assert probed == [], f"the start sweep probed a network path: {probed}"


def test_a_removed_folder_on_a_present_volume_is_still_deleted(tmp_path):
    """CONTROL, both sides: the volume is here, the folder is not."""
    tag = uuid.uuid4().hex[:8]
    if os.name == "nt":
        drive = os.path.splitdrive(str(tmp_path))[0]
        root = f"{drive}{B}jcm-removed-{tag}{B}proj"
    else:
        root = f"/jcm-removed-{tag}/proj"
    deleted, exists = _sweep(tmp_path, root)
    assert deleted == 1 and not exists
