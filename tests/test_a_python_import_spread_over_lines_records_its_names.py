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

The following lines are believed only when the statement is whole: it closes,
and every item is `name` or `name as alias`. A parenthesis that never closes,
or prose that reads like an import, records what the opening line holds.
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
    "a backslash inside the parentheses": "from a.b import (\n    x, " + BS + "\n    y,\n)\n",
    "a backslash before the parenthesis": "from a.b import " + BS + "\n    (x,\n     y)\n",
    "an alias split over two lines": "from a.b import (\n    x as\n    first,\n    y,\n)\n",
    "a parenthesis inside a comment": "from a.b import (  # (see below\n    x,  # not this)\n    y,\n)\n",
    "tabs": "from a.b import (\n\tx,\n\ty,\n)\n",
    "code after the closing parenthesis": "from a.b import (\n    x,\n    y,\n); z = 1\n",
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


UNCLOSED = {
    "code follows": "from a.b import (\n    x,\n\ndef f():\n    return 1\n\nimport os\n",
    "the next import follows": "from a.b import (\n    x,\nfrom c import w\nimport os\n",
    "bare words follow": "from a.b import (\n    x,\npass\nreturn\ny\n",
    "the file ends": "from a.b import (\n    x,\n    y,\n",
    "a backslash at the end of the file": "from a.b import x, " + BS,
}


@pytest.mark.parametrize("shape", sorted(UNCLOSED))
def test_an_unclosed_parenthesis_adds_no_name(shape):
    """A statement that never closes is not believed past its opening line: what main recorded, no more."""
    src = UNCLOSED[shape]
    edges = _edges(src)
    opening_line_names = ["x"] if shape.startswith("a backslash") else []
    assert edges.get("a.b") == opening_line_names, edges
    assert not any(s.startswith("a.b.") and s != "a.b.x" for s in edges), edges
    if "import os" in src:
        assert "os" in edges, edges
    if "from c import w" in src:
        assert edges.get("c") == ["w"], edges


PROSE = {
    "words and commas, never closed": '"""Notes.\n\nfrom here import (\nsome words, more words\nand a last line\n"""\nimport os\n',
    "words and commas, closed": '"""Notes.\n\nfrom here import (\nsome words, more words)\n"""\nimport os\n',
    "a sentence on the opening line": '"""Notes.\n\nfrom here import (the following, and more\nwords that go on. Nothing closes it\n"""\nimport os\n',
}


@pytest.mark.parametrize("shape", sorted(PROSE))
def test_prose_after_an_opening_parenthesis_is_not_read_as_names(shape):
    """Two words with no `as` between them are not an import item, so the lines are not believed."""
    edges = _edges(PROSE[shape])
    assert "os" in edges
    assert not any(s.startswith("here.") and s not in ("here.the", "here.and") for s in edges), edges
    assert edges.get("here") in ([], ["the", "and"]), edges


def _steps(src: str) -> int:
    """How many lines the clause reader fetched, counted on the string it searches."""

    class Counted(str):
        finds = 0

        def find(self, *args):  # the reader's only way to the next line
            Counted.finds += 1
            return str.find(self, *args)

    from jcodemunch_mcp.parser import imports

    text = Counted(src)
    for m in imports._PY_FROM.finditer(text):
        imports._py_from_clause(text, m.group(2), m.end())
    return Counted.finds


@pytest.mark.parametrize("line", ["from a import x, " + BS, "from a import ("], ids=["backslash", "parenthesis"])
def test_no_line_is_read_twice_however_many_statements_never_close(line):
    """Each statement that never closes stops at the next import, so the work is one step a line.

    Counted, not timed: without the stop, 2,000 such lines cost 2,000,000 steps.
    """
    n = 2000
    assert _steps((line + "\n") * n) <= 2 * n


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
