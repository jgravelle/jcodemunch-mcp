"""search_text returns the source of the few small symbols its matches sit in.

After an index search on a short fix, an agent still opened the file to see the
code it was about to edit. search_text already names the innermost symbol
around each match (``in``). When a page's matches sit in a few small symbols,
the response now carries each one's source once, so the agent can edit from the
answer. The list is bounded by symbol count, lines per symbol and total bytes,
and says what it left out and why, from the first release that has it.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.encoding.schemas import search_text as st_schema
from jcodemunch_mcp.tools import search_text as st_mod
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_text import search_text

ENGINE = '''import os

TOKEN = "beacon at module level"


class Engine:
    """An engine."""

    def start(self):
        value = "beacon in start"
        return value

    def stop(self):
        return "beacon in stop"


def helper():
    return "beacon in helper"


def other():
    return "beacon in other"
'''

KEYS = ("sources", "sources_total", "sources_omitted", "sources_omitted_reason")


def _index(tmp_path, files):
    src = tmp_path / "src"
    src.mkdir()
    for name, text in files.items():
        (src / name).write_text(text)
    store = tmp_path / "store"
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True
    return res["repo"], str(store)


def _lines(text, start, end):
    return "\n".join(text.splitlines()[start - 1 : end])


def test_one_small_symbol_returns_its_source(tmp_path):
    repo, store = _index(tmp_path, {"engine.py": ENGINE})
    res = search_text(repo, "beacon in helper", storage_path=store)
    (match,) = res["results"][0]["matches"]
    sym = match["in"]
    assert res["sources"] == [
        {"id": sym["id"], "source": _lines(ENGINE, sym["line"], sym["end_line"])}
    ]
    assert res["sources_total"] == 1
    assert res["sources_omitted"] == 0
    assert "sources_omitted_reason" not in res


def test_sources_follow_first_match_order_and_are_not_repeated(tmp_path):
    repo, store = _index(
        tmp_path,
        {"engine.py": ENGINE + '\n\ndef helper2():\n    return "beacon in helper"\n'},
    )
    res = search_text(repo, "beacon in helper", storage_path=store)
    ids = [m["in"]["id"] for g in res["results"] for m in g["matches"]]
    assert [s["id"] for s in res["sources"]] == list(dict.fromkeys(ids))
    assert res["sources_total"] == 2


def test_a_page_over_many_symbols_carries_none_of_the_keys(tmp_path):
    # A broad search is a survey, not a fix: no list is shown, so there is
    # nothing to read as complete, and the common page pays no bytes for it.
    repo, store = _index(tmp_path, {"engine.py": ENGINE})
    res = search_text(repo, "beacon in", storage_path=store)
    distinct = {
        m["in"]["id"] for g in res["results"] for m in g["matches"] if "in" in m
    }
    assert len(distinct) > st_mod.SOURCE_MAX_SYMBOLS
    for key in KEYS:
        assert key not in res


def test_a_long_symbol_is_left_out_as_too_long(tmp_path):
    body = "".join(f"    x{i} = {i}\n" for i in range(st_mod.SOURCE_MAX_LINES + 5))
    text = f'def big():\n{body}    return "beacon long"\n\n\ndef small():\n    return "beacon short"\n'
    repo, store = _index(tmp_path, {"big.py": text})
    res = search_text(repo, "beacon ", storage_path=store)
    assert [s["id"].split("::")[1] for s in res["sources"]] == ["small#function"]
    assert res["sources_total"] == 2
    assert res["sources_omitted"] == 1
    assert res["sources_omitted_reason"] == "too_long"


def test_the_byte_cap_holds_and_is_named(tmp_path, monkeypatch):
    monkeypatch.setattr(st_mod, "SOURCE_MAX_BYTES", 20)
    repo, store = _index(tmp_path, {"engine.py": ENGINE})
    res = search_text(repo, "beacon in h", storage_path=store)  # helper only
    assert "sources" not in res
    assert res["sources_omitted"] == 1
    assert res["sources_omitted_reason"] == "byte_cap"


def test_module_level_matches_carry_none_of_the_keys(tmp_path):
    repo, store = _index(tmp_path, {"engine.py": ENGINE})
    res = search_text(repo, "beacon at module", storage_path=store)
    assert res["result_count"] == 1
    for key in KEYS:
        assert key not in res


def test_no_match_carries_none_of_the_keys(tmp_path):
    repo, store = _index(tmp_path, {"engine.py": ENGINE})
    res = search_text(repo, "no such beacon", storage_path=store)
    for key in KEYS:
        assert key not in res


def test_the_wire_encoding_round_trips_the_sources(tmp_path):
    repo, store = _index(tmp_path, {"engine.py": ENGINE})
    for query in ("beacon in helper", "beacon in h", "beacon in"):
        res = search_text(repo, query, storage_path=store)
        payload, _encoding = st_schema.encode("search_text", res)
        decoded = st_schema.decode(payload)
        for key in KEYS:
            assert decoded.get(key) == res.get(key), key
    assert st_schema.ENCODING_ID == "st5"
    assert "st4" in st_schema.LEGACY_ENCODING_IDS


def _write_bytes_repo(tmp_path, name, data: bytes):
    src = tmp_path / "src"
    src.mkdir()
    (src / name).write_bytes(data)
    store = tmp_path / "store"
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True
    return res["repo"], str(store)


@pytest.mark.parametrize(
    "stored", [[], ["def helper():\n"]], ids=["unreadable", "short"]
)
def test_an_unreadable_or_short_stored_copy_is_left_out_not_served_empty(stored):
    # An empty or partial body would read as the symbol's whole source.
    body: dict = {}
    results = [
        {
            "file": "a.py",
            "matches": [
                {
                    "line": 2,
                    "in": {"id": "a.py::helper#function", "line": 1, "end_line": 2},
                }
            ],
        }
    ]
    st_mod._attach_sources(body, results, lambda _f: stored)
    assert "sources" not in body
    assert body["sources_total"] == 1
    assert body["sources_omitted"] == 1
    assert body["sources_omitted_reason"] == "unreadable"


def test_several_reasons_are_joined_in_the_order_met(tmp_path):
    body_lines = "".join(
        f"    x{i} = {i}\n" for i in range(st_mod.SOURCE_MAX_LINES + 5)
    )
    text = f'def big():\n{body_lines}    return "beacon"\n\n\ndef small():\n    return "beacon"\n'
    lines = text.splitlines(keepends=True)
    results = [
        {
            "file": "f.py",
            "matches": [
                {
                    "line": 2,
                    "in": {
                        "id": "f.py::big#function",
                        "line": 1,
                        "end_line": st_mod.SOURCE_MAX_LINES + 7,
                    },
                },
                {
                    "line": len(lines),
                    "in": {
                        "id": "f.py::small#function",
                        "line": len(lines) - 1,
                        "end_line": len(lines),
                    },
                },
            ],
        }
    ]
    body: dict = {}
    import unittest.mock as _mock

    with _mock.patch.object(st_mod, "SOURCE_MAX_BYTES", 5):
        st_mod._attach_sources(body, results, lambda _f: lines)
    assert body["sources_omitted"] == 2
    assert body["sources_omitted_reason"] == "too_long,byte_cap"


def test_the_wire_encoding_round_trips_awkward_source(tmp_path):
    # A blank line (the encoding's block separator), CRLF line ends, quotes,
    # commas and non-ASCII text inside one symbol's source.
    text = (
        "def odd():\r\n"
        '    a = "beacon, \\"quoted\\", café — 漢字"\r\n'
        "\r\n"
        "    return a\r\n"
    )
    repo, store = _write_bytes_repo(tmp_path, "odd.py", text.encode("utf-8"))
    res = search_text(repo, "beacon", storage_path=store)
    (src,) = res["sources"]
    assert src["source"] == "\n".join(text.split("\r\n")[:4])
    payload, _encoding = st_schema.encode("search_text", res)
    decoded = st_schema.decode(payload)
    for key in KEYS:
        assert decoded.get(key) == res.get(key), key
