"""A caller row says on which lines it calls the target.

`get_call_hierarchy` returned each caller's DEFINITION line, so "where is f
called" still sent the agent into every caller to find the call. A public
call-site benchmark grades `file:line` of each call, and we scored near zero
there for that reason. Each caller row now carries `call_lines` (the lines in
that caller's body that call the target), and the response carries a flat
`call_sites` list of `file:line` for the direct callers. Both are capped and
say when they were cut. A bare name with several definitions answers for all
of them when only callers are asked for, instead of refusing as ambiguous.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.tools import get_call_hierarchy as gch
from jcodemunch_mcp.tools.get_call_hierarchy import get_call_hierarchy
from jcodemunch_mcp.tools.index_folder import index_folder

LIB = "def f(x):\n    return x\n"

USE = (
    "from lib import f\n"  # 1
    "\n"  # 2
    "\n"  # 3
    "def g(a):\n"  # 4
    "    y = f(a)\n"  # 5
    "    # f is called again below\n"  # 6
    "    z = a\n"  # 7
    "    w = 1\n"  # 8
    "    return f(y) + z + w\n"  # 9
)


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "lib.py").write_text(LIB, encoding="utf-8")
    (src / "use.py").write_text(USE, encoding="utf-8")
    store = tmp_path / "store"
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True
    return src, res["repo"], str(store)


def _caller(res, name):
    return next(c for c in res["callers"] if c["name"] == name)


def test_a_caller_row_names_the_lines_that_call_the_target(repo):
    _, rid, store = repo
    res = get_call_hierarchy(rid, "f", direction="callers", depth=1, storage_path=store)
    g = _caller(res, "g")
    assert g["line"] == 4  # the definition line is unchanged
    assert g["call_lines"] == [5, 9]
    assert res["call_sites"] == ["use.py:5", "use.py:9"]
    assert res["call_sites_total"] == 2
    assert res["call_sites_truncated"] is False


def test_a_method_call_counts_and_a_definition_does_not(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "svc.py").write_text(
        "class Svc:\n"
        "    def run(self):\n"
        "        return 1\n"
        "\n"
        "    def go(self):\n"
        "        return self.run()\n",
        encoding="utf-8",
    )
    store = tmp_path / "store"
    rid = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))[
        "repo"
    ]
    res = get_call_hierarchy(
        rid,
        "svc.py::Svc.run#method",
        direction="callers",
        depth=1,
        storage_path=str(store),
    )
    go = _caller(res, "go")
    assert go["call_lines"] == [6]


def test_call_lines_are_capped_and_say_so(tmp_path, monkeypatch):
    monkeypatch.setattr(gch, "CALL_LINES_CAP", 2)
    src = tmp_path / "proj"
    src.mkdir()
    (src / "lib.py").write_text(LIB, encoding="utf-8")
    body = "".join(f"    f({i})\n" for i in range(5))
    (src / "use.py").write_text(
        "from lib import f\n\ndef g():\n" + body, encoding="utf-8"
    )
    store = tmp_path / "store"
    rid = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))[
        "repo"
    ]
    res = get_call_hierarchy(
        rid, "f", direction="callers", depth=1, storage_path=str(store)
    )
    g = _caller(res, "g")
    assert g["call_lines"] == [4, 5]
    assert g["call_lines_total"] == 5
    assert g["call_lines_truncated"] is True


def test_call_sites_are_capped_and_say_so(repo, monkeypatch):
    _, rid, store = repo
    monkeypatch.setattr(gch, "CALL_SITES_CAP", 1)
    res = get_call_hierarchy(rid, "f", direction="callers", depth=1, storage_path=store)
    assert res["call_sites"] == ["use.py:5"]
    assert res["call_sites_total"] == 2
    assert res["call_sites_truncated"] is True


def test_a_bare_name_with_two_definitions_answers_for_both_callers_only(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text("def h():\n    return 1\n", encoding="utf-8")
    (src / "b.py").write_text("def h():\n    return 2\n", encoding="utf-8")
    (src / "use_a.py").write_text(
        "from a import h\n\ndef p():\n    return h()\n", encoding="utf-8"
    )
    (src / "use_b.py").write_text(
        "from b import h\n\ndef q():\n    return h()\n", encoding="utf-8"
    )
    store = tmp_path / "store"
    rid = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))[
        "repo"
    ]
    res = get_call_hierarchy(
        rid, "h", direction="callers", depth=1, storage_path=str(store)
    )
    assert "error" not in res
    assert {d["file"] for d in res["definitions"]} == {"a.py", "b.py"}
    assert {"use_a.py:4", "use_b.py:4"} <= set(res["call_sites"])
    both = get_call_hierarchy(
        rid, "h", direction="both", depth=1, storage_path=str(store)
    )
    assert "Ambiguous" in both.get("error", "")


def test_the_compact_encoding_keeps_call_lines_and_call_sites(repo):
    from jcodemunch_mcp.encoding.schemas import get_call_hierarchy as schema

    _, rid, store = repo
    res = get_call_hierarchy(rid, "f", direction="callers", depth=1, storage_path=store)
    payload, _ = schema.encode("get_call_hierarchy", res)
    back = schema.decode(payload)
    assert back["call_sites"] == res["call_sites"]
    assert _caller(back, "g")["call_lines"] == [5, 9]
