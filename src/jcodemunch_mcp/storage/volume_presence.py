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
  means it is a mount point now. A volume mounted anywhere else reads as part
  of ``/``, so its folder reads as removed: that limit is documented.
- Any other POSIX path: on ``/``, which is always present.

A Windows-shaped path read on a POSIX host (or the reverse) cannot be checked
and answers ``None``. Stdlib only; a leaf, so the CLI prune can share it.
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
