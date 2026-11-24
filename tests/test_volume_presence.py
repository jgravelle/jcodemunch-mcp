"""`storage.volume_presence`: which volume holds a path, and is it there (L-347).

Pinned per spelling, on every host, because the sweep's decision to delete
rests on it and a host only ever exercises its own spellings.
"""
from __future__ import annotations

import os

import pytest

from jcodemunch_mcp.storage import volume_presence as vp


@pytest.mark.parametrize("path, roots", [
    ("E:\\repo", ["E:\\"]),
    ("e:/repo/sub", ["e:\\"]),
    ("\\\\server\\share\\repo", ["\\\\server\\share\\"]),
    ("/Volumes/USB/repo", ["/Volumes/USB"]),
    ("/mnt/data/repo", ["/mnt/data"]),
    ("/media/usb/repo", ["/media/usb", "/media/usb/repo"]),
    ("/media/jo/USB/repo", ["/media/jo", "/media/jo/USB"]),
    ("/run/media/jo/USB/repo", ["/run/media/jo/USB"]),
    ("/home/jo/repo", []),
    ("/tmp/x", []),
])
def test_volume_roots_per_spelling(path, roots):
    assert vp.volume_roots(path) == roots


@pytest.mark.parametrize("path", ["", "relative/repo", "proj"])
def test_an_unrecognised_shape_has_no_volume(path):
    assert vp.volume_roots(path) is None
    assert vp.volume_present(path) is None


def test_a_root_filesystem_path_is_present_on_posix():
    if os.name == "nt":
        assert vp.volume_present("/home/jo/repo") is None
    else:
        assert vp.volume_present("/home/jo/repo") is True


def test_a_cross_os_path_is_unknown():
    if os.name == "nt":
        assert vp.volume_present("/mnt/usb/repo") is None
    else:
        assert vp.volume_present("E:\\repo") is None


@pytest.mark.skipif(os.name != "nt", reason="Windows drive letters")
def test_the_system_drive_is_present_on_windows():
    drive = os.environ.get("SystemDrive", "C:")
    assert vp.volume_present(drive + "\\some\\removed\\folder") is True


def test_a_failing_check_is_unknown(monkeypatch):
    def boom(path):
        raise OSError("device not ready")

    monkeypatch.setattr(vp, "volume_roots", boom)
    assert vp.volume_present("E:\\repo") is None
