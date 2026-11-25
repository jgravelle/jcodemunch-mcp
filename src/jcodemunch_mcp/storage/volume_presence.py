"""Is the volume that holds a folder there right now?

The serve-start orphan sweep deletes an index whose folder is not a directory.
A folder can be missing for two reasons that look identical to ``is_dir()``:
it was removed, or the drive or share that holds it is not there at the
moment (an unplugged USB drive, a disconnected network share, an unmounted
volume). Only the first makes the index an orphan. Deleting in the second case
costs the user a full re-index when the drive comes back.

``volume_present(path)`` answers the second question and is TRI-STATE:

- ``True``: the volume is there, so a missing folder was removed.
- ``False``: the volume is not there, so the folder may come back.
- ``None``: it could not be established. A caller deciding to DELETE treats
  this like ``False``. Unknown never licenses a delete.

The volume of a path:

- Windows drive path (``E:\\repo``): the drive root ``E:\\``.
- Windows UNC path (``\\\\server\\share\\repo``): the share ``\\\\server\\share\\``.
- POSIX path under a removable-media folder: ``/Volumes/<v>``, ``/mnt/<v>``,
  ``/media/<v>`` or ``/media/<user>/<v>``, ``/run/media/<user>/<v>``. Present
  means it is a mount point now.
- Any other POSIX path: on ``/``, which is always present.

Limits, both directions, because each one is a wrong answer:

- A volume mounted outside those folders (POSIX), or into an NTFS folder
  (``C:\\mnt\\usb``, Windows), reads as part of ``/`` or ``C:\\``, so while it
  is absent its folder reads as removed.
- A plain folder (not a mount) under ``/mnt``, ``/media`` or ``/Volumes`` reads
  as an absent volume, so its index is kept even after the folder is removed.
  An unmounted volume leaves the same empty mount folder behind, and nothing
  local tells the two apart.

A Windows-shaped path read on a POSIX host (or the reverse) cannot be checked
and answers ``None``.

``is_network_path(path)`` says whether checking a path could touch the network:
a UNC path in either slash spelling, or a drive letter Windows reports as a
network drive. ``isdir`` on a share whose host is unreachable blocks until
the SMB connect times out (measured 2026-11-24; the figure is in the L-347 CHANGELOG entry), so a caller
on a start path must not make that call.

Stdlib only; a leaf, so the CLI prune can share it.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Optional

logger = logging.getLogger(__name__)

_WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_WIN_UNC = re.compile(r"^\\\\[^\\/]+[\\/][^\\/]+")

# The folders removable or network volumes are mounted under by convention.
_POSIX_MEDIA_ONE = ("Volumes", "mnt")


def _is_windows_shaped(path: str) -> bool:
    return bool(_WIN_DRIVE.match(path) or _WIN_UNC.match(path))


def volume_roots(path: str) -> Optional[list[str]]:
    """The candidate volume roots of ``path``, most specific last.

    ``[]`` means the path lives on the POSIX root filesystem, which is always
    present. ``None`` means the path has no shape this module recognises.
    """
    if not path:
        return None
    if _is_windows_shaped(path):
        anchor = PureWindowsPath(path).anchor
        return [anchor] if anchor else None
    if not path.startswith("/"):
        return None
    parts = PurePosixPath(path).parts  # ('/', 'mnt', 'usb', 'repo')
    if len(parts) >= 3 and parts[1] in _POSIX_MEDIA_ONE:
        return ["/" + "/".join(parts[1:3])]
    if len(parts) >= 3 and parts[1] == "media":
        roots = ["/media/" + parts[2]]
        if len(parts) >= 4:
            roots.append("/media/" + "/".join(parts[2:4]))
        return roots
    if len(parts) >= 5 and parts[1] == "run" and parts[2] == "media":
        return ["/run/media/" + "/".join(parts[3:5])]
    return []


_DRIVE_REMOTE = 4  # GetDriveTypeW: a network drive


def is_network_path(path: str) -> Optional[bool]:
    """True when checking ``path`` could touch the network; None if unknown.

    A UNC path is True by its shape on any host. A drive letter on Windows asks
    ``GetDriveTypeW``, which reads the local drive table and does not contact
    the server. A POSIX path answers False: a network mount there has no shape
    to recognise without touching it.
    """
    try:
        if _WIN_UNC.match(path):
            return True
        if os.name == "nt":
            # Windows reads //srv/share as UNC too; ask the parser, not a
            # spelling (review of L-347).
            drive = PureWindowsPath(path).drive
            if drive.startswith(("\\\\", "//")):
                return True
        if _WIN_DRIVE.match(path):
            if os.name != "nt":
                return None
            import ctypes

            anchor = PureWindowsPath(path).anchor
            return ctypes.windll.kernel32.GetDriveTypeW(anchor) == _DRIVE_REMOTE  # type: ignore[attr-defined]
        return False
    except Exception:
        logger.debug("network path check failed for %s", path, exc_info=True)
        return None


def volume_present(path: str) -> Optional[bool]:
    """True, False, or None (could not establish); see the module docstring."""
    try:
        roots = volume_roots(path)
        if roots is None:
            return None
        if _is_windows_shaped(path):
            if os.name != "nt":
                return None
            return os.path.isdir(roots[0])
        if os.name == "nt":
            return None
        if not roots:
            return True
        return any(os.path.ismount(r) for r in roots)
    except Exception:
        logger.debug("volume presence check failed for %s", path, exc_info=True)
        return None
