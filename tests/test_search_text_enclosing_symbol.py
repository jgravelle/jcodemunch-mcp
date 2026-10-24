"""search_text says which function or class each match sits in.

After a text search, an agent usually needs the function each match sits in,
which took one file outline (or a native read) per file. The index already holds every
symbol's line span, so each match now carries the innermost function, method
or class around it, as ``in: {id, line, end_line}``; the id
(``file::Name#kind``) names it. A match at module level has no ``in``.

Two companions: a list cut at ``max_results`` or by the regex time budget
said so only in ``_meta`` (stripped on a default install), so ``truncated``
and ``timed_out`` are now in the body; and a ``file_pattern`` that matches no
file (a brace pattern such as ``{a,b}`` among them) returned an empty result
that read like an absence, so the body now carries a ``note`` saying nothing
was searched.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.encoding.schemas import search_text as st_schema
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_text import search_text

SOURCE = '''import os

TOKEN = "needle at module level"


class Engine:
    """An engine."""

    def start(self):
        value = "needle in a method"
        return value

    def stop(self):
        def inner():
            return "needle in a nested function"
        return inner()


def helper():
    return "needle in a function"
'''


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "engine.py").write_text(SOURCE)
    (src / "oneline.js").write_text('class C { m() { return "needle on one line"; } }\n')
    for i in range(30):
        (src / f"many_{i}.py").write_text(f"def f{i}():\n    return 'needle {i}'\n")
    store = tmp_path / "store"
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True
    return res["repo"], str(store)


def _matches(res, file):
    for group in res["results"]:
        if group["file"] == file:
            return group["matches"]
    return []


def test_a_match_carries_its_enclosing_symbol(repo):
    rid, store = repo
    res = search_text(rid, "needle", file_pattern="engine.py", storage_path=store)
    by_text = {m["text"].strip(): m for m in _matches(res, "engine.py")}
    method = by_text['value = "needle in a method"']
    assert method["in"]["id"] == "engine.py::Engine.start#method"
    assert method["in"]["line"] <= method["line"] <= method["in"]["end_line"]
    assert set(method["in"]) == {"id", "line", "end_line"}
    assert by_text['return "needle in a function"']["in"]["id"] == "engine.py::helper#function"


def test_the_innermost_symbol_wins(repo):
    rid, store = repo
    res = search_text(rid, "nested function", file_pattern="engine.py", storage_path=store)
    (match,) = _matches(res, "engine.py")
    assert match["in"]["id"].split("#")[0].endswith("inner"), match


def test_on_equal_spans_the_deeper_symbol_wins(repo):
    # A one-line class and its one-line method share a span; the method is
    # the innermost symbol, not the class.
    rid, store = repo
    res = search_text(rid, "needle on one line", storage_path=store)
    (match,) = _matches(res, "oneline.js")
    assert match["in"]["id"] == "oneline.js::C.m#method", match


def test_a_module_level_match_has_no_enclosing_symbol(repo):
    rid, store = repo
    res = search_text(rid, "module level", file_pattern="engine.py", storage_path=store)
    (match,) = _matches(res, "engine.py")
    assert "in" not in match, match


def test_truncation_is_in_the_body(repo):
    rid, store = repo
    res = search_text(rid, "needle", max_results=5, storage_path=store)
    assert res["result_count"] == 5
    assert res["truncated"] is True
    assert res["timed_out"] is False
    res = search_text(rid, "needle in a function", storage_path=store)
    assert res["truncated"] is False


def test_a_regex_timeout_is_in_the_body(repo, monkeypatch):
    from jcodemunch_mcp.tools import search_text as st_mod
    monkeypatch.setattr(st_mod, "_REGEX_BUDGET_SEC", -1.0)
    rid, store = repo
    res = search_text(rid, "needle", is_regex=True, storage_path=store)
    assert res["timed_out"] is True, res


def test_a_scope_that_matches_no_file_says_so_in_the_body(repo):
    rid, store = repo
    res = search_text(rid, "needle", file_pattern="nomatch/*.zz", storage_path=store)
    assert res["result_count"] == 0
    assert "matched no indexed file" in res["note"], res
    # Brace alternation is expanded now, so this pattern
    # now searches both files instead of returning the empty-scope note.
    res = search_text(rid, "needle", file_pattern="{engine,many_1}.py", storage_path=store)
    assert "note" not in res, res
    assert {g["file"] for g in res["results"]} == {"engine.py", "many_1.py"}, res


def test_a_literal_brace_in_a_real_path_still_searches(tmp_path):
    src = tmp_path / "src"
    tpl = src / "{{cookiecutter.name}}"
    tpl.mkdir(parents=True)
    (tpl / "app.py").write_text("def run():\n    return 'needle in a template'\n")
    store = tmp_path / "store"
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    out = search_text(res["repo"], "needle", file_pattern="{{cookiecutter.name}}/*.py",
                      storage_path=str(store))
    assert out["result_count"] == 1 and "note" not in out, out



def test_the_compact_encoding_keeps_the_enclosing_symbol(repo):
    rid, store = repo
    res = search_text(rid, "needle in a method", file_pattern="engine.py", storage_path=store)
    payload, _encoding = st_schema.encode("search_text", res)
    decoded = st_schema.decode(payload)
    (match,) = _matches(decoded, "engine.py")
    assert match["in"] == _matches(res, "engine.py")[0]["in"]
    assert decoded["truncated"] is False
    assert decoded["timed_out"] is False
    res = search_text(rid, "needle", file_pattern="nomatch/*.zz", storage_path=store)
    payload, _encoding = st_schema.encode("search_text", res)
    assert st_schema.decode(payload)["note"] == res["note"]


def test_check_references_content_matches_carry_the_enclosing_symbol(tmp_path):
    from jcodemunch_mcp.tools.check_references import check_references

    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.py").write_text("def target():\n    return 1\n")
    (src / "use.py").write_text(
        "from lib import target\n\n\nclass Caller:\n    def run(self):\n        return target()\n"
    )
    store = tmp_path / "store"
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    out = check_references(res["repo"], "target", storage_path=str(store))
    groups = {g["file"]: g["matches"] for g in out["content_references"]}
    run_line = [m for m in groups["use.py"] if "return target()" in m["text"]]
    assert run_line and run_line[0]["in"]["id"] == "use.py::Caller.run#method", groups
