"""github-codemunch-mcp - Token-efficient MCP server for GitHub source code exploration."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _fallback_local_version() -> str:
    """Best-effort fallback version for source checkouts.

    When the package isn't installed (common in local dev), importlib.metadata
    cannot resolve distribution metadata and returns PackageNotFoundError.
    In that case, parse pyproject.toml's `version = "..."` entry.
    """
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not pyproject.exists():
        return "unknown"

    for line in pyproject.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("version") and "=" in stripped:
            _, raw = stripped.split("=", 1)
            return raw.strip().strip('"').strip("'")

    return "unknown"

try:
    __version__ = version("jcodemunch-mcp")
except PackageNotFoundError:
    __version__ = _fallback_local_version()
