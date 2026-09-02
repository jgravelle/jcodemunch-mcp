"""Focused Markdown (.md / .markdown) regression tests.

Blueprint: tests/test_svelte.py. Markdown docs are parsed by the bundled
tree-sitter ``markdown`` grammar: ATX (`#`) and setext (underlined) headings
become a hierarchical ``heading`` outline (parented by heading level), and
fenced code blocks (``` / ~~~) become ``code_block`` child symbols named by
their info-string language. Files with no headings/fences return no symbols
and stay indexed for text search (the SASS precedent).
"""

from pathlib import Path

from jcodemunch_mcp.parser import parse_file
from jcodemunch_mcp.parser.imports import extract_imports
from jcodemunch_mcp.parser.languages import (
    LANGUAGE_EXTENSIONS,
    LANGUAGE_REGISTRY,
    get_language_for_path,
)
from jcodemunch_mcp.parser.symbols import VALID_KINDS
from jcodemunch_mcp.summarizer.file_summarize import _heuristic_summary
from jcodemunch_mcp.tools.index_folder import discover_local_files


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "markdown"


def _read_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Registry wiring
# ---------------------------------------------------------------------------

def test_markdown_extension_and_registry_present():
    assert LANGUAGE_EXTENSIONS.get(".md") == "markdown"
    assert LANGUAGE_EXTENSIONS.get(".markdown") == "markdown"
    assert "markdown" in LANGUAGE_REGISTRY
    assert get_language_for_path("docs/README.md") == "markdown"
    assert get_language_for_path("docs/notes.markdown") == "markdown"
    # .mdx is NOT routed to markdown (no JSX-aware grammar available).
    assert get_language_for_path("docs/page.mdx") is None


def test_markdown_kinds_are_valid_kind_filters():
    # search_symbols(kind=...) validates against VALID_KINDS; both new kinds
    # must be accepted there (and "field" must not have regressed).
    for kind in ("heading", "code_block", "field"):
        assert kind in VALID_KINDS


# ---------------------------------------------------------------------------
# 2. Heading outline
# ---------------------------------------------------------------------------

def test_markdown_headings_extracted_with_hierarchy():
    symbols = parse_file(_read_fixture("sample.md"), "docs/sample.md", "markdown")
    headings = [s for s in symbols if s.kind == "heading"]
    assert headings, "no heading symbols extracted"

    by_qn = {s.qualified_name: s for s in headings}
    # Top-level h1
    assert "Sample Document" in by_qn
    h1 = by_qn["Sample Document"]
    assert h1.parent is None
    assert h1.line == 1
    # h2 nested under the h1
    h2 = by_qn["Sample Document.Getting Started"]
    assert h2.parent == h1.id
    # h3 nested under the h2
    h3 = by_qn["Sample Document.Getting Started.Prerequisites"]
    assert h3.parent == h2.id
    # Setext (underlined) heading treated as h1 → top-level
    assert "Setext Style" in by_qn
    assert by_qn["Setext Style"].parent is None
    assert by_qn["Setext Style"].signature == "Setext Style"


def test_markdown_duplicate_headings_disambiguated_inline():
    symbols = parse_file(_read_fixture("sample.md"), "docs/sample.md", "markdown")
    headings = [s for s in symbols if s.kind == "heading"]
    qns = [s.qualified_name for s in headings]
    # Two `## Getting Started` h2s under the same h1 → second gets ~2.
    assert "Sample Document.Getting Started" in qns
    assert "Sample Document.Getting Started~2" in qns
    # Disambiguated inline (not via the shared pass) so parent ids stay valid:
    # every symbol's parent id must resolve to an existing symbol id.
    ids = {s.id for s in symbols}
    assert all(s.parent is None or s.parent in ids for s in symbols)


def test_markdown_emphasis_stripped_from_heading_names():
    src = "## **Usage** and `CLI` guide\n"
    symbols = parse_file(src, "docs/x.md", "markdown")
    assert symbols[0].name == "Usage and CLI guide"


def test_markdown_empty_heading_gets_placeholder_name():
    src = "##\n\nbody\n"
    symbols = parse_file(src, "docs/x.md", "markdown")
    assert [s.name for s in symbols if s.kind == "heading"] == ["(untitled)"]


def test_markdown_heading_docstring_is_first_paragraph():
    src = (
        "## Install\n"
        "\n"
        "Run the installer first.\n"
        "\n"
        "- list items do not count\n"
    )
    symbols = parse_file(src, "docs/x.md", "markdown")
    heading = next(s for s in symbols if s.kind == "heading")
    assert heading.docstring == "Run the installer first."


# ---------------------------------------------------------------------------
# 3. Fenced code blocks
# ---------------------------------------------------------------------------

def test_markdown_fenced_code_blocks_named_by_language():
    symbols = parse_file(_read_fixture("sample.md"), "docs/sample.md", "markdown")
    blocks = [s for s in symbols if s.kind == "code_block"]
    names = sorted(s.name for s in blocks)
    assert names == ["bash", "python"]
    # Signature is the opening fence line
    py = next(s for s in blocks if s.name == "python")
    assert py.signature == "```python"


def test_markdown_code_blocks_parented_under_enclosing_heading():
    symbols = parse_file(_read_fixture("sample.md"), "docs/sample.md", "markdown")
    h2 = next(s for s in symbols if s.qualified_name == "Sample Document.Getting Started")
    py = next(s for s in symbols if s.kind == "code_block" and s.name == "python")
    assert py.parent == h2.id


def test_markdown_code_block_without_info_string_named_code():
    src = "## Title\n\n```\nplain fence\n```\n"
    symbols = parse_file(src, "docs/x.md", "markdown")
    blocks = [s for s in symbols if s.kind == "code_block"]
    assert [s.name for s in blocks] == ["code"]


def test_markdown_tilde_fences_supported():
    src = "## Title\n\n~~~ruby\nputs 'hi'\n~~~\n"
    symbols = parse_file(src, "docs/x.md", "markdown")
    blocks = [s for s in symbols if s.kind == "code_block"]
    assert [s.name for s in blocks] == ["ruby"]


# ---------------------------------------------------------------------------
# 4. Byte ranges + content hash (get_symbol_source / drift detection)
# ---------------------------------------------------------------------------

def test_markdown_symbols_carry_byte_ranges_and_hashes():
    src = _read_fixture("sample.md")
    symbols = parse_file(src, "docs/sample.md", "markdown")
    assert all(s.byte_length > 0 and s.content_hash for s in symbols)
    for s in symbols:
        seg = src[s.byte_offset:s.byte_offset + s.byte_length]
        assert seg, f"{s.qualified_name} byte range is empty"
    # The h1 section spans the whole document body
    h1 = next(s for s in symbols if s.kind == "heading" and s.parent is None)
    assert h1.byte_offset == 0
    assert src[h1.byte_offset:h1.byte_offset + h1.byte_length].startswith("# Sample Document")


def test_markdown_setext_section_extents_do_not_bleed_into_siblings():
    src = (
        "First\n"
        "======\n"
        "\n"
        "body one\n"
        "\n"
        "Second\n"
        "------\n"
        "\n"
        "body two\n"
    )
    symbols = parse_file(src, "docs/x.md", "markdown")
    by_qn = {s.qualified_name: s for s in symbols}
    # Setext `======` is h1, `------` is h2 → Second nests under First,
    # mirroring how ATX h2 nests under h1.
    first, second = by_qn["First"], by_qn["First.Second"]
    assert second.parent == first.id
    # First's extent ends where Second's heading starts (flat setext sections).
    assert first.end_line < second.line
    assert first.byte_offset + first.byte_length <= second.byte_offset
    assert first.docstring == "body one"
    assert second.docstring == "body two"


# ---------------------------------------------------------------------------
# 5. Non-structure content
# ---------------------------------------------------------------------------

def test_markdown_frontmatter_and_plain_prose_produce_no_symbols():
    src = (
        "---\n"
        "title: frontmatter is skipped\n"
        "---\n"
        "\n"
        "Just a paragraph, no headings or fences.\n"
    )
    assert parse_file(src, "docs/plain.md", "markdown") == []


def test_markdown_no_import_edges_from_doc_text():
    # Code snippets inside docs must not leak import edges.
    src = (
        "# Example\n"
        "\n"
        "```js\n"
        "import { foo } from './real-file.js';\n"
        "```\n"
    )
    assert extract_imports(src, "docs/example.md", "markdown") == []


def test_markdown_symbols_report_markdown_language():
    symbols = parse_file(_read_fixture("sample.md"), "docs/sample.md", "markdown")
    assert symbols
    assert all(s.language == "markdown" for s in symbols)


# ---------------------------------------------------------------------------
# 6. File summary
# ---------------------------------------------------------------------------

def test_markdown_file_summary_describes_outline():
    symbols = parse_file(_read_fixture("sample.md"), "docs/sample.md", "markdown")
    summary = _heuristic_summary("docs/sample.md", symbols)
    assert summary.startswith("Markdown doc:")
    assert "3 code blocks" in summary or "2 code blocks" in summary
    # No false "0 functions / 0 classes" style output
    assert "0 " not in summary


# ---------------------------------------------------------------------------
# 7. End-to-end discovery
# ---------------------------------------------------------------------------

def test_discovery_indexes_md_not_wrong_extension(tmp_path):
    (tmp_path / "README.md").write_text(_read_fixture("sample.md"), encoding="utf-8")
    (tmp_path / "NOTES.markdown").write_text("# Notes\n\nbody\n", encoding="utf-8")
    (tmp_path / "plain.ts").write_text("export const k = 1;\n", encoding="utf-8")
    (tmp_path / "page.mdx").write_text("# Mdx\n\nbody\n", encoding="utf-8")

    files, _warnings, skip_counts = discover_local_files(tmp_path.resolve())
    names = {p.name for p in files}

    assert "README.md" in names
    assert "NOTES.markdown" in names
    assert "plain.ts" in names
    # .mdx stays unindexed (deliberate: no JSX-aware markdown grammar)
    assert "page.mdx" not in names
    assert skip_counts.get("wrong_extension", 0) == 1
