"""Test package."""

import sys
from pathlib import Path


def _platform_path(unix_path: str) -> Path:
    """Convert Unix-style path to platform-appropriate path for testing.

    On Unix: returns Path(unix_path) unchanged.
    On Windows: converts "/work" to "C:/work" to ensure is_absolute() is True.
    """
    if sys.platform == "win32":
        if unix_path.startswith("/"):
            return Path("C:" + unix_path.replace("/", "/"))
    return Path(unix_path)


def _resolve_only(target, resolved: Path):
    """Build a ``Path.resolve`` side effect that answers for ONE path.

    ⚠ Narrow, NOT a blanket ``return_value``. ``patch`` replaces ``resolve`` on
    the ``Path`` CLASS, so a blanket value answers for every path in the
    process — including the storage path ``IndexStore`` resolves when it is
    constructed. A test that gets past the breadth guard then creates its index
    directory at the faked location, or dies there (``FileNotFoundError``,
    #479). Every other path keeps the real ``resolve``.

    Patch with ``autospec=True`` so ``self`` reaches this side effect; a plain
    ``patch`` installs a ``MagicMock`` on the class, which is not a descriptor
    and so is called with no arguments at all.
    """
    real_resolve = Path.resolve
    wanted = {str(target), str(resolved)}

    def _side_effect(self, *args, **kwargs):
        # ``resolved`` answers for itself because real ``resolve`` is
        # idempotent, and the folder is resolved more than once on the way in
        # (``index_folder`` then ``resolve_index_identity``). Sending the
        # second call to the real one stats a path that does not exist.
        if str(self) in wanted:
            return resolved
        return real_resolve(self, *args, **kwargs)

    return _side_effect


def _platform_path_str(unix_path: str) -> str:
    """Convert Unix-style path to platform-appropriate path string for config files.

    Returns forward-slash paths on Windows so the result is safe to embed in JSON.
    Python's pathlib accepts forward slashes on Windows, so this is valid at runtime.
    """
    return _platform_path(unix_path).as_posix()
