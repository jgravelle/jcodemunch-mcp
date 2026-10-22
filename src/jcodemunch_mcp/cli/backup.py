"""Where the CLI writes a backup of a file it is about to change.

(L-203) A backup is the file's own name plus ``.bak``. The sites used to call
``path.with_suffix(".md.bak")`` and the like, guessing the file's suffix; for
``.windsurfrules``, a dotfile with no suffix, ``with_suffix`` appends instead
of replacing and the backup was ``.windsurfrules.windsurfrules.bak``. A leaf,
stdlib only, so ``init`` and ``skills`` can both import it without a cycle.
"""

from __future__ import annotations

from pathlib import Path


def backup_path(path: Path) -> Path:
    """Return ``<path>.bak``: the whole file name with ``.bak`` appended."""
    return path.with_name(path.name + ".bak")
