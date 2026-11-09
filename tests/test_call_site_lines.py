"""A caller row says on which lines it calls the target.

`get_call_hierarchy` returned each caller's DEFINITION line, so "where is f
called" still sent the agent into every caller to find the call. Each caller
row now carries `call_lines` (the lines in that caller's body that call the
target), and the response carries a flat `call_sites` list of `file:line` for
the direct callers. Both are capped and say when they were cut, on every row
and every answer, so the compact encoding decodes them as sent. A row whose
lines could not be read says so (`call_lines: None` and a reason) instead of
reading as a caller with no call. A bare name with several definitions still
refuses as ambiguous.
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


def test_a_bare_name_with_two_definitions_still_refuses(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text("def h():\n    return 1\n", encoding="utf-8")
    (src / "b.py").write_text("def h():\n    return 2\n", encoding="utf-8")
    store = tmp_path / "store"
    rid = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))[
        "repo"
    ]
    for direction in ("callers", "callees", "both"):
        res = get_call_hierarchy(
            rid, "h", direction=direction, depth=1, storage_path=str(store)
        )
        assert res["error"] == (
            "Ambiguous symbol 'h': found 2 definitions. "
            "Use the symbol 'id' field to disambiguate."
        )
        assert {c["file"] for c in res["candidates"]} == {"a.py", "b.py"}
        assert set(res) == {"error", "candidates"}
    assert not hasattr(gch, "DEFINITIONS_CAP")


def test_the_compact_encoding_keeps_call_lines_and_call_sites(repo):
    from jcodemunch_mcp.encoding.schemas import get_call_hierarchy as schema

    _, rid, store = repo
    res = get_call_hierarchy(rid, "f", direction="callers", depth=1, storage_path=store)
    payload, _ = schema.encode("get_call_hierarchy", res)
    back = schema.decode(payload)
    assert back["call_sites"] == res["call_sites"]
    assert _caller(back, "g")["call_lines"] == [5, 9]


def test_twelve_calls_give_ten_lines_at_the_shipped_cap(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "lib.py").write_text(LIB, encoding="utf-8")
    body = "".join(f"    f({i})\n" for i in range(12))
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
    assert gch.CALL_LINES_CAP == 10
    assert g["call_lines"] == list(range(4, 14))
    assert g["call_lines_total"] == 12
    assert g["call_lines_truncated"] is True
    # The flat list keeps every site under its own cap.
    assert res["call_sites_total"] == 12
    assert res["call_sites_truncated"] is False

    from jcodemunch_mcp.encoding.schemas import get_call_hierarchy as schema

    back = schema.decode(schema.encode("get_call_hierarchy", res)[0])
    bg = _caller(back, "g")
    assert bg["call_lines"] == list(range(4, 14))
    assert bg["call_lines_total"] == 12
    assert bg["call_lines_truncated"] is True


_ROW_KEYS = (
    "call_lines",
    "call_lines_total",
    "call_lines_truncated",
    "call_lines_unknown",
)
_TOP_KEYS = (
    "call_sites",
    "call_sites_total",
    "call_sites_truncated",
    "call_sites_unknown_callers",
    "call_lines_unknown_count",
    "call_lines_basis",
)


def _round_trip(res):
    from jcodemunch_mcp.encoding.schemas import get_call_hierarchy as schema

    return schema.decode(schema.encode("get_call_hierarchy", res)[0])


def _row(row):
    return {k: row.get(k) for k in _ROW_KEYS}


def _top(res):
    return {k: res.get(k) for k in _TOP_KEYS}


def test_an_uncut_row_carries_every_key_and_decodes_as_sent(repo):
    _, rid, store = repo
    res = get_call_hierarchy(rid, "f", direction="callers", depth=1, storage_path=store)
    g = _caller(res, "g")
    assert set(_ROW_KEYS) <= set(g)
    assert _row(g) == {
        "call_lines": [5, 9],
        "call_lines_total": 2,
        "call_lines_truncated": False,
        "call_lines_unknown": None,
    }
    back = _round_trip(res)
    assert _row(_caller(back, "g")) == _row(g)
    assert _top(back) == _top(res)


def test_an_answer_with_no_callers_still_carries_the_call_site_keys(repo):
    _, rid, store = repo
    res = get_call_hierarchy(rid, "g", direction="callers", depth=1, storage_path=store)
    assert res["callers"] == []
    assert set(_TOP_KEYS) <= set(res)
    assert _top(res) == {
        "call_sites": [],
        "call_sites_total": 0,
        "call_sites_truncated": False,
        "call_sites_unknown_callers": 0,
        "call_lines_unknown_count": 0,
        "call_lines_basis": gch.CALL_LINES_BASIS,
    }
    assert _top(_round_trip(res)) == _top(res)


def _assert_unknown(res, reason):
    g = _caller(res, "g")
    assert _row(g) == {
        "call_lines": None,
        "call_lines_total": None,
        "call_lines_truncated": None,
        "call_lines_unknown": reason,
    }
    assert res["call_lines_unknown_count"] == 1
    assert res["call_sites_unknown_callers"] == 1
    assert res["call_sites"] == []
    assert res["call_sites_total"] == 0
    back = _round_trip(res)
    assert _row(_caller(back, "g")) == _row(g)
    assert _top(back) == _top(res)


def test_an_unreadable_file_is_unknown_not_zero(repo, monkeypatch):
    _, rid, store = repo
    real = gch._attach_call_lines

    class Unreadable:
        """The call graph has already read the file; this read fails."""

        def get_file_content(self, owner, name, path):
            raise OSError("unreadable")

    def with_unreadable_store(response, index, store_, owner, name):
        real(response, index, Unreadable(), owner, name)

    monkeypatch.setattr(gch, "_attach_call_lines", with_unreadable_store)
    res = get_call_hierarchy(rid, "f", direction="callers", depth=1, storage_path=store)
    _assert_unknown(res, "file_unreadable")


def test_a_missing_end_line_is_unknown_not_zero(repo, monkeypatch):
    _, rid, store = repo
    real = gch._attach_call_lines

    def without_end_line(response, index, store_, owner, name):
        class Index:
            _symbol_index = {
                k: (
                    {kk: vv for kk, vv in v.items() if kk != "end_line"}
                    if v.get("name") == "g"
                    else v
                )
                for k, v in index._symbol_index.items()
            }

        real(response, Index(), store_, owner, name)

    monkeypatch.setattr(gch, "_attach_call_lines", without_end_line)
    res = get_call_hierarchy(rid, "f", direction="callers", depth=1, storage_path=store)
    _assert_unknown(res, "no_end_line")


def test_a_deeper_unknown_row_is_counted_but_not_charged_to_call_sites():
    """`call_sites` lists direct callers only, so an unknown depth-2 row counts
    in `call_lines_unknown_count` and not in `call_sites_unknown_callers`."""

    class Store:
        def get_file_content(self, owner, name, path):
            return None if path == "deep.py" else "def g():\n    f()\n"

    class Index:
        _symbol_index = {
            "use.py::g": {"file": "use.py", "line": 1, "end_line": 2},
            "deep.py::k": {"file": "deep.py", "line": 1, "end_line": 3},
        }

    response = {
        "symbol": {"name": "f"},
        "callers": [
            {"id": "use.py::g", "file": "use.py", "line": 1, "depth": 1},
            {"id": "deep.py::k", "file": "deep.py", "line": 1, "depth": 2},
        ],
    }
    gch._attach_call_lines(response, Index(), Store(), "o", "n")
    assert response["callers"][0]["call_lines"] == [2]
    assert response["callers"][1]["call_lines"] is None
    assert response["callers"][1]["call_lines_unknown"] == "file_unreadable"
    assert response["call_lines_unknown_count"] == 1
    assert response["call_sites_unknown_callers"] == 0
    assert response["call_sites"] == ["use.py:2"]
