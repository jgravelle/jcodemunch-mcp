"""Markdown + HTML language registration and section-symbol extraction."""
from pathlib import Path

from jcodemunch_mcp.parser import parse_file
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


def _md_symbols():
    content = (FIXTURES / "markdown" / "sample.md").read_text(encoding="utf-8")
    return parse_file(content, "sample.md", "markdown")


def test_markdown_sections_extracted():
    symbols = _md_symbols()
    names = {s.name for s in symbols}
    assert {"Alpha Top", "Beta Sub", "Gamma Sub", "Delta Top"} <= names
    assert all(s.kind == "class" for s in symbols if s.name in names)


def test_markdown_section_spans_nest():
    symbols = {s.name: s for s in _md_symbols()}
    alpha, beta, gamma, delta = (symbols[n] for n in
        ("Alpha Top", "Beta Sub", "Gamma Sub", "Delta Top"))
    # section span covers its body, and subsections nest inside the parent span
    assert alpha.line < beta.line and beta.end_line <= alpha.end_line
    assert alpha.line < gamma.line and gamma.end_line <= alpha.end_line
    assert delta.line > alpha.end_line - 1  # Delta is a sibling, not nested
    assert beta.end_line >= beta.line + 2   # span includes the body lines


def test_markdown_headingless_file_no_crash():
    symbols = parse_file("just prose\nno headings here\n", "plain.md", "markdown")
    assert all(s.name for s in symbols)  # nothing unnamed; empty list acceptable
