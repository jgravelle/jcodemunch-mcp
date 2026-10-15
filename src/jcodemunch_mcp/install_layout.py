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
import ntpath
import os
import posixpath
import shutil
import sys
import sysconfig
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


#: The console script every install puts in its scripts directory.
_EXECUTABLE_NAME = "jcodemunch-mcp"

#: A path component uv uses for its cache; `uvx` runs from there and
#: `uv cache clean` deletes it, so a hook written to that path breaks later.
_EPHEMERAL_COMPONENTS = frozenset({"archive-v0"})


def _scripts_dir() -> Optional[Path]:
    """The scripts directory of the interpreter running this process."""
    try:
        return Path(sysconfig.get_path("scripts"))
    except (KeyError, TypeError):
        logger.debug("no scripts directory for this interpreter", exc_info=True)
        return None


def _existing(candidate: Path) -> Optional[Path]:
    """`candidate` or `candidate.exe` when it is a file, else None."""
    for p in (candidate, candidate.with_name(candidate.name + ".exe")):
        try:
            if p.is_file():
                return p
        except OSError:
            logger.debug("could not stat %s", p, exc_info=True)
    return None


def _is_ephemeral(path: Path) -> bool:
    return any(part.lower() in _EPHEMERAL_COMPONENTS for part in path.parts)


def own_executable() -> Optional[str]:
    """The `jcodemunch-mcp` executable of THIS install, or None.

    ⚠⚠ Never `shutil.which`: PATH names the FIRST install, and a user who runs
    a newer install by full path, from a venv or a `uv tool` environment, while
    an older one sits on PATH, got hooks and server entries that call the older
    version. Asked in order: the absolute path this process was launched by
    (a `uv tool` shim stays the shim), then the running interpreter's scripts
    directory. ⚠ A RELATIVE `argv[0]` is never used: a hook resolves it against
    its cwd, the checked-out repository, where a file of that name must never
    become what runs. ⚠ An executable in uv's cache (`uvx`) is not returned,
    because `uv cache clean` removes it and every hook written to it.
    """
    argv0 = sys.argv[0] if sys.argv else ""
    if (
        argv0
        and os.path.isabs(argv0)
        and os.path.basename(argv0).lower().startswith(_EXECUTABLE_NAME)
    ):
        found = _existing(Path(argv0))
        if found is not None:
            return None if _is_ephemeral(found) else str(found)
    scripts = _scripts_dir()
    if scripts is not None:
        found = _existing(scripts / _EXECUTABLE_NAME)
        if found is not None and found.is_absolute() and not _is_ephemeral(found):
            return str(found)
    return None


def _is_absolute(path: str) -> bool:
    """Absolute in either path flavour: a drive-letter path is never a cwd hit.

    The check exists to refuse what Windows `which` returns from the current
    directory (``.\\jcodemunch-mcp.EXE``); `os.path.isabs` alone also refuses
    a Windows path on a POSIX host, which no lookup there returns.
    """
    return posixpath.isabs(path) or ntpath.isabs(path)


def _first_lasting_on_path() -> Optional[str]:
    """The first `jcodemunch-mcp` on PATH that is not inside uv's cache.

    ⚠ `uvx` puts its cached environment's scripts directory FIRST on PATH, so a
    plain `shutil.which` under uvx answers with the very executable
    `own_executable` declined.
    """
    first = shutil.which(_EXECUTABLE_NAME)
    if first is None or (_is_absolute(first) and not _is_ephemeral(Path(first))):
        return first
    # Each entry is checked directly: on Windows before Python 3.12,
    # `shutil.which(path=...)` looks in the current directory first.
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        found = _existing(Path(entry) / _EXECUTABLE_NAME)
        if found is not None and os.access(found, os.X_OK) and not _is_ephemeral(found):
            return str(found)
    return None


def running_from_uv_cache() -> bool:
    """True when this process runs from uv's cache (`uvx`)."""
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and os.path.isabs(argv0) and _is_ephemeral(Path(argv0)):
        return True
    scripts = _scripts_dir()
    return scripts is not None and _is_ephemeral(scripts)


def substitution_note() -> Optional[str]:
    """What a written command runs instead of this install, or None when it runs this one.

    Under `uvx` the running executable sits in uv's cache, so a written command
    calls another install from PATH, or the bare name. That install may be a
    different version, and `init` must say so rather than leave the user to
    find out from a hook's behaviour.
    """
    if own_executable() is not None or not running_from_uv_cache():
        return None
    chosen = _first_lasting_on_path()
    target = (
        f"`{chosen}`"
        if chosen
        else "the bare name `jcodemunch-mcp`, looked up on PATH when a hook runs"
    )
    return (
        "this run comes from uv's cache (uvx), which `uv cache clean` deletes, so the hooks call "
        f"{target} instead. That may be a different version. To have them call this version, "
        "install it (`uv tool install jcodemunch-mcp`) and run that install's `init`."
    )


def executable_for_commands() -> Optional[str]:
    """What a written hook or server entry should run: this install, else PATH's.

    Never an executable in uv's cache, from either source. ``None`` when there
    is none; each caller has its own fallback.
    """
    return own_executable() or _first_lasting_on_path()
