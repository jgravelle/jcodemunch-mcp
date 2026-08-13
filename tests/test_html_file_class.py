"""HTML as a text-searchable file class (jcm#452, HTML half).

`.html`/`.htm` register on the bundled html grammar with EMPTY
symbol_node_types: an indexed template contributes zero symbols (so
symbol-driven consumers — find_dead_code's per-symbol sweep, the health-radar
axes, importance maths — are unaffected) but the FILE enters
``index.source_files``, which is what ``flow_edges._resolve_template`` needs.

The test that makes this change worth having is
``test_resolve_template_resolves_indexed_html``: before this feature,
``_resolve_template(index, "page.html")`` returns ``None`` on every Django/
Flask/Express/Rails repo because the template file never enters the index —
the ``views`` annotation on ``get_signal_chains`` and the render edges in
``get_endpoint_impact`` degrade silently. Registry-coverage assertions alone
would pass just as happily against a version that resolves nothing.
"""
from pathlib import Path

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.languages import (
    LANGUAGE_REGISTRY,
    get_language_for_path,
    get_language_extensions,
)
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.flow_edges import _resolve_template
from jcodemunch_mcp.tools.index_folder import index_folder


_HTML_DOC = """<!doctype html>
<html>
  <head><title>Probe</title></head>
  <body>
    <h1>Rendered template</h1>
    <p>A needle for text search: osprey-template-probe.</p>
  </body>
</html>
"""


def test_html_extensions_in_registry():
    ext = get_language_extensions()
    assert ext[".html"] == "html"
    assert ext[".htm"] == "html"
    assert "html" in LANGUAGE_REGISTRY
    assert get_language_for_path("templates/index.html") == "html"


def test_html_emits_no_symbols():
    """Empty symbol_node_types: a parsed .html contributes nothing to
    index.symbols — the file class is text-searchable, not a symbol source."""
    assert parse_file(_HTML_DOC, "page.html", "html") == []


def _indexed(tmp_path: Path):
    src = tmp_path / "src"
    (src / "templates").mkdir(parents=True)
    (src / "app.py").write_text(
        "def home(request):\n"
        "    return render(request, \"page.html\")\n",
        encoding="utf-8",
    )
    (src / "templates" / "page.html").write_text(_HTML_DOC, encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert result["success"], result
    owner, name = result["repo"].split("/", 1)
    return IndexStore(base_path=str(store)).load_index(owner, name), result


def test_resolve_template_resolves_indexed_html(tmp_path):
    """THE proof: _resolve_template resolves a template it returns None for
    without this feature (the .html never entered index.source_files)."""
    index, _ = _indexed(tmp_path)
    assert "templates/page.html" in index.source_files
    assert _resolve_template(index, "page.html") == "templates/page.html"
    # Suffix-path form used by Django/Flask render calls resolves too.
    assert _resolve_template(index, "templates/page.html") == "templates/page.html"


def test_indexed_html_adds_file_but_no_symbols(tmp_path):
    """The dead-file interaction, stated as an assertion: the html file is
    indexed (file-level counts move by one file class) while symbol counts
    stay untouched — no .html-derived entries appear in index.symbols."""
    index, result = _indexed(tmp_path)
    html_symbols = [
        s for s in index.symbols
        if str(s.get("file") or s.get("filename") or "").endswith(".html")
    ]
    assert html_symbols == []
    assert "templates/page.html" in result.get("no_symbols_files", [])
