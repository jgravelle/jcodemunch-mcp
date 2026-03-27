"""Test package."""

import sys
from pathlib import Path


def _platform_path(unix_path: str) -> Path:
    """Convert Unix-style path to platform-appropriate path."""
    if sys.platform == "win32":
        if unix_path.startswith("/"):
            return Path("C:" + unix_path)
    return Path(unix_path)


def _platform_path_str(unix_path: str) -> str:
    """Convert Unix-style path to platform-appropriate path string for config files."""
    return str(_platform_path(unix_path))
