"""A Python `from x import (...)` spread over several lines records the names it imports.

The extractor read a `from` import one line at a time. For

    from pkg.utils import (
        solve,
        other,
    )

the line holds `(` and nothing else, so the edge was recorded with no names:
`find_references("solve")` answered 0 for a name imported in the form every
formatter writes, `check_references` counted no import, and the per-name
module edges (`pkg.utils.solve`, #550) were never offered. A backslash
continuation recorded the names of its first line only.

The statement is read to its closing parenthesis (or to the end of the
continuation) and comments are dropped before the names are split.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.imports import extract_imports
from jcodemunch_mcp.tools.check_references import check_references
from jcodemunch_mcp.tools.find_references import find_references
from jcodemunch_mcp.tools.index_folder import index_folder

BS = chr(92)


def _edges(src: str) -> dict[str, list[str]]:
    return {e["specifier"]: e["names"] for e in extract_imports(src, "m.py", "python")}


SHAPES = {
    "one name a line": "from a.b import (\n    x,\n    y,\n)\n",
    "no trailing comma": "from a.b import (\n    x,\n    y\n)\n",
    "two names a line": "from a.b import (\n    x, y\n)\n",
    "first name on the opening line": "from a.b import (x,\n               y)\n",
    "closing parenthesis on the last name's line": "from a.b import (\n    x,\n    y)\n",
    "aliases": "from a.b import (\n    x as first,\n    y as second,\n)\n",
    "a comment after a name": "from a.b import (\n    x,  # the first\n    y,\n)\n",
    "a comment on the opening line": "from a.b import (  # noqa: F401\n    x,\n    y,\n)\n",
    "a comment line between names": "from a.b import (\n    x,\n    # the second\n    y,\n)\n",
    "a blank line between names": "from a.b import (\n    x,\n\n    y,\n)\n",
    "indented, inside a function": "def f():\n    from a.b import (\n        x,\n        y,\n    )\n    return x\n",
    "CRLF line endings": "from a.b import (\r\n    x,\r\n    y,\r\n)\r\n",
    "a backslash continuation": "from a.b import x, " + BS + "\n    y\n",
    "a backslash before the first name": "from a.b import " + BS + "\n    x, y\n",
    "one line, as before": "from a.b import x, y\n",
    "one line in parentheses, as before": "from a.b import (x, y)\n",
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_every_spelling_records_both_names(shape):
    edges = _edges(SHAPES[shape])
    assert edges.get("a.b") == ["x", "y"], edges
    # The per-name module edges (#550): `from a.b import x` may name the module a/b/x.py.
    assert edges.get("a.b.x") == ["x"] and edges.get("a.b.y") == ["y"], edges
    assert set(edges) == {"a.b", "a.b.x", "a.b.y"}, edges


def test_a_relative_import_spread_over_lines_offers_the_sibling_modules():
    edges = _edges("from . import (\n    receipts,\n    producers,\n)\n")
    assert edges == {".": ["receipts", "producers"], ".receipts": ["receipts"], ".producers": ["producers"]}


def test_what_follows_the_closing_parenthesis_is_not_a_name():
    src = "from a.b import (\n    x,\n)\nz = 1\nfrom c import (\n    w,\n)\nimport os\n"
    assert _edges(src) == {"a.b": ["x"], "a.b.x": ["x"], "c": ["w"], "c.w": ["w"], "os": []}


def test_a_star_and_a_future_import_are_unchanged():
    assert _edges("from a.b import *\n") == {"a.b": []}
    assert _edges("from __future__ import (\n    annotations,\n)\n") == {}


def test_an_unclosed_parenthesis_does_not_swallow_the_file():
    """A statement that never closes (a file cut short, or prose in a docstring) keeps what its own lines name."""
    src = "from a.b import (\n    x,\n\ndef f():\n    return 1\n\nimport os\n"
    edges = _edges(src)
    assert edges.get("a.b") == ["x"], edges
    assert "os" in edges and "def" not in " ".join(edges), edges
    for names in edges.values():
        assert all(n.isidentifier() for n in names), edges


def test_an_unclosed_parenthesis_stops_at_the_next_import_statement():
    """`from c import w` is a run of words and commas too; it is the next statement, not two more names."""
    edges = _edges("from a.b import (\n    x,\nfrom c import w\nimport os\n")
    assert edges == {"a.b": ["x"], "a.b.x": ["x"], "c": ["w"], "c.w": ["w"], "os": []}


def test_prose_that_reads_like_an_import_names_nothing_that_is_not_an_identifier():
    src = '"""Notes.\n\nfrom here import (the following, and more\nwords that go on. Nothing closes it\n"""\nimport os\n'
    edges = _edges(src)
    assert "os" in edges
    for names in edges.values():
        assert all(n.isidentifier() for n in names), edges


@pytest.fixture()
def repo(tmp_path):
    src = tmp_path / "proj"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (src / "pkg" / "utils.py").write_text("def solve():\n    return 1\n\n\ndef other():\n    return 2\n", encoding="utf-8")
    (src / "pkg" / "routing.py").write_text(
        "from pkg.utils import (\n    other,\n    solve,\n)\n\n\ndef handle():\n    return solve() + other()\n",
        encoding="utf-8",
    )
    store = tmp_path / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store), identity_mode="local")
    assert r.get("success"), r
    return r["repo"], str(store)


def test_find_references_sees_a_name_imported_over_several_lines(repo):
    name, store = repo
    out = find_references(name, "solve", storage_path=store)
    files = [r["file"] for r in out["references"]]
    assert files == ["pkg/routing.py"], out


def test_check_references_counts_the_import(repo):
    name, store = repo
    out = check_references(name, "solve", storage_path=store)
    assert out["import_count"] == 1, out
    assert out["import_references"][0]["file"] == "pkg/routing.py"
    assert out["import_references"][0]["matches"][0]["match_type"] == "named"
