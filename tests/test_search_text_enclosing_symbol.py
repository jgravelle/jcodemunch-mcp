"""search_text says which function or class each match sits in.

In a measured agent run on a large repository, the step most often repeated
after a text search was finding the enclosing function of each match: one
file outline (or a native read) per file. The index already holds every
symbol's line span, so each match now carries the innermost function, method
or class around it, as ``in: {name, kind, id, line, end_line}``. A match at
module level has no ``in``.

Two companions from the same run: the 20-result cap was reported only in
``_meta`` (stripped on a default install), so ``truncated`` is now in the
body; and a brace pattern such as ``{a,b}`` in ``file_pattern`` matched no
file and returned an empty result, so it now gets an error that says so.
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
    assert method["in"]["name"] == "Engine.start"
    assert method["in"]["kind"] == "method"
    assert method["in"]["line"] <= method["line"] <= method["in"]["end_line"]
    assert by_text['return "needle in a function"']["in"]["name"] == "helper"


def test_the_innermost_symbol_wins(repo):
    rid, store = repo
    res = search_text(rid, "nested function", file_pattern="engine.py", storage_path=store)
    (match,) = _matches(res, "engine.py")
    assert match["in"]["name"].endswith("inner"), match


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
    res = search_text(rid, "needle in a function", storage_path=store)
    assert res["truncated"] is False


def test_a_brace_pattern_gets_an_error_not_an_empty_result(repo):
    rid, store = repo
    res = search_text(rid, "needle", file_pattern="{engine,many_1}.py", storage_path=store)
    assert "error" in res and "{" in res["error"], res


def test_the_compact_encoding_keeps_the_enclosing_symbol(repo):
    rid, store = repo
    res = search_text(rid, "needle in a method", file_pattern="engine.py", storage_path=store)
    payload, _encoding = st_schema.encode("search_text", res)
    decoded = st_schema.decode(payload)
    (match,) = _matches(decoded, "engine.py")
    assert match["in"] == _matches(res, "engine.py")[0]["in"]
    assert decoded["truncated"] is False


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
    assert run_line and run_line[0]["in"]["name"] == "Caller.run", groups
