"""mermaid_viewer — spawn mmd-viewer for render_diagram output.

Provides:
  resolve_viewer_path()  — configured path or $PATH lookup
  open_diagram()         — write .mmd file + spawn viewer
  cleanup_temp_dir()     — purge only jcodemunch-owned temp files

Files are prefixed with `jcm-` so cleanup is selective and safe.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from .. import config as config_module

logger = logging.getLogger(__name__)

# Track whether the viewer was ever invoked this session.
_viewer_used = False

# Filename prefix — makes cleanup selective and safe.
_FILE_PREFIX = "jcm-"

# Stale-file age threshold for per-call purge (seconds).
_STALE_FILE_AGE_SEC = 3600


def _prune_stale_files(d: Path) -> None:
    """Best-effort removal of jcm- temp files older than _STALE_FILE_AGE_SEC."""
    cutoff = time.time() - _STALE_FILE_AGE_SEC
    try:
        entries = list(d.iterdir())
    except OSError:
        return
    for entry in entries:
        if not (entry.is_file() and entry.name.startswith(_FILE_PREFIX)):
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            logger.debug("Failed to prune stale mermaid temp file %s", entry, exc_info=True)


def _temp_dir(storage_path: Path | None = None) -> Path:
    base = Path(storage_path) if storage_path else config_module._global_storage_path()
    return base / "temp" / "mermaid"


_WINDOWS_EXEC_SUFFIXES = {".exe", ".cmd", ".bat", ".com"}


def _looks_executable(path: Path) -> bool:
    """Platform-appropriate executability check.

    The existence probe uses `Path.exists()` so existing tests that mock it
    continue to work. On Windows, require a known exec suffix; on POSIX,
    require the execute bit.
    """
    if not path.exists():
        return False
    if os.name == "nt":
        return path.suffix.lower() in _WINDOWS_EXEC_SUFFIXES
    return os.access(str(path), os.X_OK)


def _looks_like_bare_name(value: str) -> bool:
    """True when the string looks like a command name (no path separators)."""
    if not value:
        return False
    if "/" in value or "\\" in value:
        return False
    # Windows drive-relative like ``C:viewer.exe`` still counts as a path.
    if os.name == "nt" and len(value) >= 2 and value[1] == ":":
        return False
    return True


def resolve_viewer_path() -> str | None:
    """Return the mmd-viewer executable path, or None if not found.

    Users commonly configure a bare command name (``"mmd-viewer"`` on POSIX,
    ``"mmd-viewer.exe"`` on Windows) rather than a full path. When the
    configured value is a bare name we defer to ``shutil.which`` so it
    resolves against ``$PATH`` — the prior implementation rejected it as
    "not an executable file on disk" (audit finding F6). Absolute / relative
    paths continue to go through the strict `_looks_executable` check so a
    misspelled path doesn't get silently replaced by whatever is on ``PATH``.
    """
    configured = config_module.get("mermaid_viewer_path", "")
    if configured:
        if _looks_like_bare_name(configured):
            return shutil.which(configured)
        return configured if _looks_executable(Path(configured)) else None
    return shutil.which("mmd-viewer")


def open_diagram(mermaid: str, storage_path: Path | None = None) -> dict:
    """Write mermaid to a timestamped .mmd file and spawn mmd-viewer on it.

    Returns {opened: bool, path: str, error?: str}. Non-fatal on failure.
    Sets _viewer_used=True when a file is written (attempt was made).
    """
    global _viewer_used
    viewer = resolve_viewer_path()
    if not viewer:
        return {"opened": False, "error": "viewer_not_found"}
    d = _temp_dir(storage_path)
    try:
        d.mkdir(parents=True, exist_ok=True)
        _prune_stale_files(d)
        fname = f"{_FILE_PREFIX}diagram-{os.getpid()}-{time.time_ns()}.mmd"
        p = d / fname
        p.write_text(mermaid, encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to write mermaid temp file: %s", e, exc_info=True)
        return {"opened": False, "error": f"write_failed: {e}"}
    _viewer_used = True
    try:
        with open(p, "rb") as f:
            subprocess.Popen(
                [viewer],
                stdin=f,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=(os.name != "nt"),
            )
    except Exception as e:
        return {"opened": False, "path": str(p), "error": f"spawn_failed: {e}"}
    return {"opened": True, "path": str(p)}


def was_viewer_used() -> bool:
    """Return True if open_diagram was called at least once this session."""
    return _viewer_used


def cleanup_temp_dir(storage_path: Path | None = None) -> int:
    """Remove only jcodemunch-owned files (jcm- prefix) from temp/mermaid/.

    Returns count of removed files. Safe to call even if viewer was never used.
    On Windows, retries with a short delay to handle file locks from viewer processes.
    """
    d = _temp_dir(storage_path)
    if not d.exists():
        return 0
    removed = 0
    for entry in d.iterdir():
        if entry.is_file() and entry.name.startswith(_FILE_PREFIX):
            try:
                entry.unlink()
                removed += 1
            except OSError:
                # Windows: viewer process may still hold the file. Retry once.
                time.sleep(0.5)
                try:
                    entry.unlink()
                    removed += 1
                except OSError:
                    logger.debug("Failed to remove mermaid temp file %s", entry, exc_info=True)
    return removed
