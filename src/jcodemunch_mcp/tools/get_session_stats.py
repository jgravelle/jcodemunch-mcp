"""Get token savings stats for the current session."""

from ..storage import get_session_stats as _get_session_stats
from typing import Optional


def get_session_stats(storage_path: Optional[str] = None) -> dict:
    """Return token savings stats for the current MCP session.

    Args:
        storage_path: Custom storage path (matches other tools).

    Returns:
        Dict with session and all-time token savings, cost avoided estimates,
        per-tool breakdown, and session duration.
    """
    return _get_session_stats(base_path=storage_path)
