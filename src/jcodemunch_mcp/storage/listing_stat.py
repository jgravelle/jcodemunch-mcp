"""File stats from one directory listing per folder instead of one stat per file.

Windows reports each entry's size and modification time in the listing itself
(``os.scandir``), so a caller that needs the stats of many files in few folders
reads each folder once. On three jcodemunch content caches untouched for
months (1,372 to 1,994 files each), the first listing cost 2.1x to 2.8x less
than an ``os.stat`` pass over the same files run right after it, with
identical size and mtime for every file. Elsewhere a listing entry's stat costs
one syscall per file anyway, so ``ENABLED`` is False there and callers keep
``os.stat``.

⚠⚠ **Only for files jcodemunch writes itself (the content cache), never the
user's source tree.** NTFS stores a copy of each file's size and mtime in its
folder's listing, and a write through a HARD LINK in another folder updates
the file and that folder's copy, not this one: measured here, ``os.stat`` gave
the new size and mtime while the listing still gave the old ones. A freshness
check reading the listing would call an edited file unchanged. The listing
cannot tell a hard-linked file apart (Windows reports ``st_nlink`` 0 from it).
Nothing under ``src/`` creates a hard link in the content cache.

⚠ A listing answers for the names it holds, exactly as spelled. A name it does
not hold (a case variant, a file that vanished, an unreadable folder) answers
None, and every caller treats None as "stat it the ordinary way" or "not
provably unchanged", never as a match.

A leaf, stdlib only.
"""

from __future__ import annotations

import os
from typing import Optional

ENABLED = os.name == "nt"


def rel_parts(rel_path: str) -> Optional[list]:
    """``rel_path``'s components when it stays under its root, else None."""
    parts = rel_path.replace("\\", "/").split("/")
    if (
        not rel_path
        or rel_path.startswith("/")
        or any(p in ("", ".", "..") for p in parts)
        or ":" in parts[0]
    ):
        return None
    return parts


def list_folder(folder: str) -> dict:
    """name -> ``os.stat_result`` for the regular files in ``folder``.

    Symlinks are followed, as ``os.stat`` follows them. An unreadable folder
    or entry is left out.
    """
    out: dict = {}
    try:
        with os.scandir(folder) as it:
            for e in it:
                try:
                    if e.is_file():
                        out[e.name] = e.stat()
                except OSError:
                    continue
    except (OSError, ValueError):
        return {}
    return out


class ListingStat:
    """Stats under ``root``, one listing per folder, held for this object's life.

    Build one per question (one search, one freshness check): the listing is
    a snapshot, and holding it longer would answer for a moment that passed.
    """

    def __init__(self, root) -> None:
        self.root = str(root)
        self._folders: dict = {}

    def stat(self, rel_path: str):
        """The listing's ``os.stat_result`` for ``rel_path``, or None."""
        parts = rel_parts(rel_path)
        if parts is None:
            return None
        folder = os.path.join(self.root, *parts[:-1])
        entries = self._folders.get(folder)
        if entries is None:
            entries = self._folders[folder] = list_folder(folder)
        return entries.get(parts[-1])
