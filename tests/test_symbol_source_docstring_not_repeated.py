"""get_symbol_source does not send a docstring twice.

The served `source` already holds a Python docstring verbatim, so a separate
`docstring` field carrying the same text sends it twice. The measured
saving on this repository's own index is in the CHANGELOG entry.

The field stays, with the same type, so a reader that indexes it does not
break: it is "" with `docstring_in_source: true` when, and only when, the
source this reply returns contains the docstring verbatim. A bounded or outline
view that leaves the docstring out keeps it, and so does a language whose
stored docstring is not the source text (comment markers stripped).
"""

from __future__ import annotations

import json

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools._utils import resolve_repo
from jcodemunch_mcp.tools.get_symbol import get_symbol_source
from jcodemunch_mcp.tools.index_folder import index_folder

PY = '''
def first_line_body(x):
    """Return x doubled.

    A second paragraph, so the docstring is longer than one line and the
    test sees a real share of the reply.
    """
    y = x * 2
    return y


def no_doc(x):
    return x


class Router:
    """Routes requests."""

    def add(self, path):
        """Add one route."""
        self.routes.append(path)
        return path
'''

JS = """
/**
 * Adds two numbers.
 * @param {number} a
 */
function add(a, b) {
  return a + b;
}

module.exports = add;
"""


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "mod.py").write_text(PY, encoding="utf-8")
    (root / "add.js").write_text(JS, encoding="utf-8")
    store = tmp_path / "store"
    out = index_folder(
        str(root),
        use_ai_summaries=False,
        storage_path=str(store),
        identity_mode="local",
    )
    assert out.get("success"), out
    return out["repo"], str(store)


def _stored_docstring(repo, sid):
    rid, store = repo
    owner, name = resolve_repo(rid, store)
    index = IndexStore(base_path=store).load_index(owner, name)
    return (index.get_symbol(sid) or {}).get("docstring") or ""


def _get(repo, sid, **kw):
    rid, store = repo
    return get_symbol_source(repo=rid, symbol_id=sid, storage_path=store, **kw)


def _assert_rule(entry, stored):
    """The property: dropped exactly when the served source holds it verbatim."""
    assert isinstance(entry["docstring"], str), "the field keeps its type"
    if stored and stored in entry["source"]:
        assert entry["docstring"] == "", (
            "a docstring the source carries is not sent twice"
        )
        assert entry["docstring_in_source"] is True
    else:
        assert entry["docstring"] == stored, (
            "a docstring the source does not carry is kept"
        )
        assert "docstring_in_source" not in entry


def test_a_python_docstring_is_not_sent_twice(repo):
    sid = "mod.py::first_line_body#function"
    stored = _stored_docstring(repo, sid)
    assert "Return x doubled." in stored, stored  # the fixture has a real docstring
    r = _get(repo, sid)
    assert stored in r["source"]
    assert r["docstring"] == ""
    assert r["docstring_in_source"] is True
    _assert_rule(r, stored)


def test_no_docstring_means_no_flag(repo):
    r = _get(repo, "mod.py::no_doc#function")
    assert r["docstring"] == ""
    assert "docstring_in_source" not in r


def test_a_bounded_view_without_the_docstring_keeps_it(repo):
    sid = "mod.py::first_line_body#function"
    stored = _stored_docstring(repo, sid)
    r = _get(repo, sid, max_source_lines=1)
    assert stored not in r["source"], r["source"]  # one line cannot hold the docstring
    assert r["docstring"] == stored
    assert "docstring_in_source" not in r


def test_a_method_and_a_class_outline_follow_the_same_rule(repo):
    for sid in ("mod.py::Router.add#method", "mod.py::Router#class"):
        _assert_rule(_get(repo, sid), _stored_docstring(repo, sid))


def test_a_js_doc_comment_follows_the_rule(repo):
    # JSDoc is stored with its markers stripped, so it is not the source text
    # and stays; if a future parser stores it verbatim, the rule still holds.
    sid = "add.js::add#function"
    stored = _stored_docstring(repo, sid)
    _assert_rule(_get(repo, sid), stored)


def test_batch_mode_applies_it_per_symbol(repo):
    rid, store = repo
    ids = [
        "mod.py::first_line_body#function",
        "mod.py::no_doc#function",
        "add.js::add#function",
    ]
    r = get_symbol_source(repo=rid, symbol_ids=ids, storage_path=store)
    by_id = {s["id"]: s for s in r["symbols"]}
    for sid in ids:
        _assert_rule(by_id[sid], _stored_docstring(repo, sid))
    assert by_id["mod.py::first_line_body#function"]["docstring_in_source"] is True


def test_the_served_reply_carries_the_rule(repo):
    # At the user's entry point: the dispatcher's encoding and _meta handling
    # must keep the empty field and the flag.
    import asyncio

    from jcodemunch_mcp import server

    rid, store = repo
    import os

    old = os.environ.get("CODE_INDEX_PATH")
    os.environ["CODE_INDEX_PATH"] = store
    try:
        out = asyncio.run(
            server.call_tool(
                "get_symbol_source",
                {"repo": rid, "symbol_id": "mod.py::first_line_body#function"},
            )
        )
    finally:
        if old is None:
            os.environ.pop("CODE_INDEX_PATH", None)
        else:
            os.environ["CODE_INDEX_PATH"] = old
    text = "".join(
        getattr(p, "text", "") for p in (out if isinstance(out, list) else out.content)
    )
    d = json.loads(text)
    assert d["docstring"] == ""
    assert d["docstring_in_source"] is True
    assert "Return x doubled." in d["source"]
