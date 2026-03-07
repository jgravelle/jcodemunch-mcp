"""Version metadata tests."""

from jcodemunch_mcp import __version__


def test_version_not_unknown_in_source_checkout():
    """Source checkout should fall back to pyproject version instead of unknown."""
    assert __version__ != "unknown"
