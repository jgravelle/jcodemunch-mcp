"""Markdown + HTML language registration and section-symbol extraction."""
from pathlib import Path

from jcodemunch_mcp.parser import parse_file, Symbol
from jcodemunch_mcp.parser.languages import (
    LANGUAGE_REGISTRY, get_language_extensions, get_language_for_path,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_markdown_extension_in_registry():
    ext = get_language_extensions()
    assert ext[".md"] == "markdown"
    assert ext[".markdown"] == "markdown"
    assert "markdown" in LANGUAGE_REGISTRY
    assert get_language_for_path("docs/AGENT_STATE.md") == "markdown"


def test_html_extension_in_registry():
    ext = get_language_extensions()
    assert ext[".html"] == "html"
    assert ext[".htm"] == "html"
    assert "html" in LANGUAGE_REGISTRY
    assert get_language_for_path("templates/index.html") == "html"
