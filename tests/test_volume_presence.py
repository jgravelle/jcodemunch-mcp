"""`storage.volume_presence`: which volume holds a path, and is it there (L-347).

Pinned per spelling, on every host, because the sweep's decision to delete
rests on it and a host only ever exercises its own spellings. Branches on
`os.name` instead of skipping, so no CI leg gains a skip.
"""
from __future__ import annotations

import os

import pytest

from jcodemunch_mcp.storage import volume_presence as vp

B = chr(92)


@pytest.mark.parametrize("path, roots", [
    (f"E:{B}repo", [f"E:{B}"]),
    ("e:/repo/sub", [f"e:{B}"]),
    (f"{B}{B}server{B}share{B}repo", [f"{B}{B}server{B}share{B}"]),
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


def test_the_host_root_volume_is_present():
    if os.name == "nt":
        drive = os.environ.get("SystemDrive", "C:")
        assert vp.volume_present(f"{drive}{B}some{B}removed{B}folder") is True
        assert vp.volume_present("/home/jo/repo") is None
    else:
        assert vp.volume_present("/home/jo/repo") is True
        assert vp.volume_present(f"E:{B}repo") is None


def test_a_failing_check_is_unknown(monkeypatch):
    def boom(path):
        raise OSError("device not ready")

    monkeypatch.setattr(vp, "volume_roots", boom)
    assert vp.volume_present(f"E:{B}repo") is None


def test_a_unc_path_is_a_network_path_on_any_host():
    assert vp.is_network_path(f"{B}{B}server{B}share{B}repo") is True
    assert vp.is_network_path(f"{B}{B}server/share/repo") is True


def test_a_forward_slash_unc_path_is_a_network_path_on_windows():
    """Windows reads //srv/share as UNC; POSIX reads it as a root path."""
    expected = True if os.name == "nt" else False
    assert vp.is_network_path("//server/share/repo") is expected


def test_a_posix_path_is_not_a_network_path():
    assert vp.is_network_path("/home/jo/repo") is False


def test_a_drive_letter_asks_the_drive_table(monkeypatch):
    path = f"E:{B}repo"
    if os.name != "nt":
        assert vp.is_network_path(path) is None
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    monkeypatch.setattr(kernel32, "GetDriveTypeW", lambda root: 4)
    assert vp.is_network_path(path) is True
    monkeypatch.setattr(kernel32, "GetDriveTypeW", lambda root: 2)
    assert vp.is_network_path(path) is False
