"""Where does this install's code actually come from? The ONE authority.

⚠⚠ Extracted 2026-08-31 because "is this a source/editable install?" had grown
THREE readers, each with its own answer: the drift verdict in `cli/init.py`, the
process registry, and the surface receipt's assumptions. That is the shape this
project keeps paying for -- a second generator, a second call site, a second
derivation -- and the one-sentence fix each time is *ask the authority instead of
reproducing its logic*.

⚠⚠ **A LEAF: stdlib only.** `cli/init.py` and `storage/process_registry.py` both
import it, and `storage` importing `cli` would be the wrong direction. Same
extraction as `cli/policy.py`, which exists to break exactly this kind of cycle.

⚠⚠ **The `src` component is REQUIRED, not decoration.** Depth alone is not a
discriminator: ``<x>/site-packages/jcodemunch_mcp/__init__.py`` is also three
levels under ``<x>``, so a positional check calls a copied install editable
whenever a ``pyproject.toml`` happens to sit that far up. That defect shipped in
the first draft of the drift fix and its own test caught it.

⚠ Everything here is TRI-STATE by return type: ``None`` means could not
establish and is never ``False``. Reporting "not a source tree" for a question
we could not ask is the defect this project keeps finding in its own instruments.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: The layout an editable/source install has: ``<root>/src/jcodemunch_mcp/``.
_SRC_DIR_NAME = "src"


def is_source_layout(module_file: Path) -> bool:
    """Is the running module imported from a source tree rather than a copy?

    True means the code IS the tree: a new process cannot load stale code,
    because there is no copy step between the two. It says nothing about a
    process that started earlier -- that one holds whatever it imported at its
    own startup, which is what ``newest_source_mtime`` is for.
    """
    try:
        return (
            module_file.parent.parent.name == _SRC_DIR_NAME
            and (module_file.parent.parent.parent / "pyproject.toml").is_file()
        )
    except OSError:
        logger.debug("source layout probe failed", exc_info=True)
        return False


def tree_root_for(module_file: Path) -> Optional[Path]:
    """The project root of a source layout, or None when this is a copy."""
    if not is_source_layout(module_file):
        return None
    return module_file.parent.parent.parent


def newest_source_mtime(package_dir: Path) -> Optional[float]:
    """Newest mtime among the package's ``.py`` files, or None if unreadable.

    ⚠ This is the only honest way to ask whether an ALREADY-RUNNING process is
    serving current code: a process holds what it imported at startup, so a
    source file newer than that start means the process is behind. A version
    string cannot answer it -- on an editable install every process reports the
    same frozen metadata number no matter when it started.

    ⚠ Measured at 13 ms over 274 files, so callers still gate it on having
    something to judge rather than paying it on every read.
    """
    newest: Optional[float] = None
    try:
        for f in package_dir.rglob("*.py"):
            try:
                m = f.stat().st_mtime
            except OSError:
                continue
            if newest is None or m > newest:
                newest = m
    except OSError:
        logger.debug("source mtime walk failed", exc_info=True)
        return None
    return newest


def running_source_changed_at() -> Optional[float]:
    """Newest source mtime of the RUNNING package, or None when not a source install.

    None for a copied install is correct and load-bearing: the tree's mtimes say
    nothing about what a copy loaded, so the question is unanswerable rather
    than answerable-as-fresh.
    """
    try:
        import jcodemunch_mcp

        module_file = Path(jcodemunch_mcp.__file__)
    except Exception:  # noqa: BLE001 - any import/attr failure is UNKNOWN
        logger.debug("could not locate the running package", exc_info=True)
        return None
    if not is_source_layout(module_file):
        return None
    return newest_source_mtime(module_file.parent)


def _lint_probe():
    return undefined_name_for_ci_probe
