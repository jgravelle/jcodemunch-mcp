"""A class fetch returns the class's own lines with each method collapsed to its
signature, unless the caller asks for the full body.

A class body repeats every method body, and each method is a symbol of its own,
one fetch away. fastapi's `_IncludedRouter` is 232 lines; fetched in full it
was most of the token cost of the benchmark question that returned it.
`class_body="full"` keeps the old reply, as do the bounded-source arguments.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.tools.get_symbol import get_symbol_source
from jcodemunch_mcp.tools.index_folder import index_folder

PY = '''
class Router:
    """Routes requests."""

    prefix: str = "/"

    def add_route(self, path, handler):
        self.routes.append((path, handler))
        self.version += 1
        return handler

    @property
    def count(self):
        total = len(self.routes)
        return total


def helper(x):
    return x * 2


class Empty:
    pass
'''

JS = """
class Route {
  constructor(path) {
    this.path = path;
    this.stack = [];
  }

  dispatch(req, res) {
    for (const layer of this.stack) {
      layer.handle(req, res);
    }
  }
}

module.exports = Route;
"""


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "router.py").write_text(PY, encoding="utf-8")
    (root / "route.js").write_text(JS, encoding="utf-8")
    store = tmp_path / "store"
    out = index_folder(
        str(root), use_ai_summaries=False, storage_path=str(store), identity_mode="local"
    )
    assert out.get("success"), out
    return out["repo"], str(store)


def _get(repo, sid, **kw):
    rid, store = repo
    return get_symbol_source(repo=rid, symbol_id=sid, storage_path=store, **kw)


def test_a_class_comes_back_with_its_methods_collapsed(repo):
    r = _get(repo, "router.py::Router#class")
    src = r["source"]
    assert "def add_route(self, path, handler)" in src
    assert "def count(self)" in src
    for body_line in ("self.routes.append", "self.version += 1", "total = len"):
        assert body_line not in src, src
    # The class's own lines stay: docstring and field.
    assert '"""Routes requests."""' in src and 'prefix: str = "/"' in src
    assert r["source_outline"] is True
    assert r["source_outline_members"] == 2
    assert r["source_total_lines"] == r["end_line"] - r["line"] + 1
    assert r["source_is_bounded_view"] is True


def test_a_decorator_is_kept_once(repo):
    src = _get(repo, "router.py::Router#class")["source"]
    assert src.count("@property") == 1, src
    lines = src.replace("\r\n", "\n").split("\n")
    assert lines[lines.index("    @property") + 1].strip() == "def count(self)", src


def test_full_returns_the_whole_body(repo):
    r = _get(repo, "router.py::Router#class", class_body="full")
    assert "self.routes.append((path, handler))" in r["source"]
    assert not any(k.startswith("source_") for k in r), sorted(r)


def test_a_bound_returns_the_body_not_the_outline(repo):
    r = _get(repo, "router.py::Router#class", max_source_lines=200)
    assert "self.routes.append" in r["source"]
    assert "source_outline" not in r


def test_a_class_with_no_methods_is_unchanged(repo):
    r = _get(repo, "router.py::Empty#class")
    assert "pass" in r["source"]
    assert "source_outline" not in r and "source_is_bounded_view" not in r


def test_a_function_is_unchanged(repo):
    r = _get(repo, "router.py::helper#function")
    assert "return x * 2" in r["source"] and "source_outline" not in r


def test_a_method_is_one_fetch_away_in_full(repo):
    r = _get(repo, "router.py::Router.add_route#method")
    assert "self.version += 1" in r["source"] and "source_outline" not in r


def test_verify_still_hashes_the_full_body(repo):
    r = _get(repo, "router.py::Router#class", verify=True)
    assert r["source_outline"] is True
    assert r["content_verified"] is True


def test_a_javascript_class_is_outlined(repo):
    r = _get(repo, "route.js::Route#class")
    src = r["source"]
    assert "dispatch(req, res)" in src and "layer.handle" not in src, src
    assert r["source_outline_members"] == 2


def test_batch_mode_outlines_each_class(repo):
    rid, store = repo
    out = get_symbol_source(
        repo=rid,
        symbol_ids=["router.py::Router#class", "router.py::helper#function"],
        storage_path=store,
    )
    by_id = {s["id"]: s for s in out["symbols"]}
    assert by_id["router.py::Router#class"]["source_outline"] is True
    assert "source_outline" not in by_id["router.py::helper#function"]


def test_an_unknown_mode_is_refused(repo):
    r = _get(repo, "router.py::Router#class", class_body="summary")
    assert "error" in r and "class_body" in r["error"]
