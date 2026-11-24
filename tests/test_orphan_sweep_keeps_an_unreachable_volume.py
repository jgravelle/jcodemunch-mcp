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

⚠ NON-VACUITY. The kept-index tests fail on the pre-fix sweep. The control in
`test_pack_index_survives_orphan_sweep.py` (a folder removed from a present
volume is still deleted) passes on both sides, so this file cannot go green by
the sweep ceasing to delete anything.
"""
from __future__ import annotations

import os
import sqlite3
import string
import uuid

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.index_folder import index_folder


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


def _sweep_keeps(tmp_path, root):
    store_dir, db = _make_indexed_store(tmp_path)
    _point_index_at(db, root)
    deleted = IndexStore(base_path=str(store_dir)).cleanup_orphan_indexes()
    return deleted, db.exists()


def _absent_drive_letter():
    for letter in reversed(string.ascii_uppercase):
        if not os.path.exists(f"{letter}:\\"):
            return letter
    pytest.skip("every drive letter is mapped on this host")


@pytest.mark.skipif(os.name != "nt", reason="a Windows drive letter")
def test_index_on_an_absent_drive_letter_is_kept(tmp_path):
    """The defect, stated directly: an unplugged USB drive E:."""
    root = f"{_absent_drive_letter()}:\\proj"
    deleted, exists = _sweep_keeps(tmp_path, root)
    assert deleted == 0, f"the index of {root} was deleted while its drive was absent"
    assert exists


@pytest.mark.skipif(os.name == "nt", reason="a POSIX mount folder")
def test_index_under_an_unmounted_media_folder_is_kept(tmp_path):
    """An unmounted volume under /mnt is not a removed folder."""
    root = f"/mnt/jcm-absent-{uuid.uuid4().hex[:8]}/proj"
    deleted, exists = _sweep_keeps(tmp_path, root)
    assert deleted == 0, f"the index of {root} was deleted while its volume was not mounted"
    assert exists


@pytest.mark.skipif(os.name == "nt", reason="a Windows path read on a POSIX host")
def test_a_windows_path_on_a_posix_host_is_kept(tmp_path):
    """A store read on another OS cannot check the volume: unknown keeps."""
    deleted, exists = _sweep_keeps(tmp_path, "E:\\proj")
    assert deleted == 0 and exists


@pytest.mark.skipif(os.name != "nt", reason="a POSIX path read on a Windows host")
def test_a_posix_media_path_on_a_windows_host_is_kept(tmp_path):
    deleted, exists = _sweep_keeps(tmp_path, "/media/usb/proj")
    assert deleted == 0 and exists


@pytest.mark.skipif(os.name != "nt", reason="a Windows drive letter")
def test_a_removed_folder_on_a_present_drive_is_still_deleted(tmp_path):
    """CONTROL on Windows: the drive of tmp_path is present, the folder is not."""
    drive = os.path.splitdrive(str(tmp_path))[0]
    root = f"{drive}\\jcm-removed-{uuid.uuid4().hex[:8]}\\proj"
    deleted, exists = _sweep_keeps(tmp_path, root)
    assert deleted == 1 and not exists
