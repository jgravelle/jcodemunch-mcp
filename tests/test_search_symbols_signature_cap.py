"""A search_symbols row caps its signature, and says so on the row.

A multi-line signature is copied whole into every standard row. On FastAPI a
single `middleware` lookup served a signature of tens of thousands of characters,
and the whole reply fell back to JSON because the compact form no longer saved
enough to pass the encoder's gate. The standard row now carries at most
`SIGNATURE_MAX_CHARS` characters of it, a row that was cut says
`signature_truncated: true`, and the whole signature stays one
`get_symbol_source` call away. `detail_level="full"` keeps the whole signature:
that shape inlines the source anyway.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from jcodemunch_mcp.encoding import decoder, encode_response
from jcodemunch_mcp.tools.get_symbol import get_symbol_source
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols

SRC = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp" / "tools" / "search_symbols.py"

_PARAMS = ",\n".join(f"    parameter_number_{i}: str = 'default_value_{i}'" for i in range(40))
FILES = {
    "app/routing.py": (
        f"def add_widget_route(\n{_PARAMS},\n) -> None:\n    return None\n\n\n"
        "def add_widget_short(path: str) -> None:\n    return None\n"
    ),
}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch):
    from jcodemunch_mcp import config as _config

    monkeypatch.setitem(_config._GLOBAL_CONFIG, "refresh_on_read", False)
    root = tmp_path / "repo"
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    storage = str(tmp_path / "store")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    assert res.get("success"), res
    return res["repo"], storage


def _rows(resp: dict) -> dict[str, dict]:
    return {r["name"]: r for r in resp["results"]}


def _search(repo, storage, detail_level="standard"):
    return search_symbols(
        repo=repo, query="add widget", detail_level=detail_level,
        max_results=5, storage_path=storage,
    )


def test_a_long_signature_is_capped_and_the_row_says_so(repo):
    from jcodemunch_mcp.tools.search_symbols import SIGNATURE_MAX_CHARS

    r, s = repo
    rows = _rows(_search(r, s))
    long_row, short_row = rows["add_widget_route"], rows["add_widget_short"]
    full = get_symbol_source(repo=r, symbol_id=long_row["id"], storage_path=s)
    whole = full.get("signature") or ""
    assert len(whole) > SIGNATURE_MAX_CHARS, "fixture must carry a long signature"
    assert long_row["signature"] == whole[:SIGNATURE_MAX_CHARS]
    assert long_row["signature_truncated"] is True
    # Omitted when false, like `decorators` when empty.
    assert "signature_truncated" not in short_row
    assert short_row["signature"] == "def add_widget_short(path: str) -> None"


def test_the_full_detail_level_keeps_the_whole_signature(repo):
    from jcodemunch_mcp.tools.search_symbols import SIGNATURE_MAX_CHARS

    r, s = repo
    row = _rows(_search(r, s, "full"))["add_widget_route"]
    assert len(row["signature"]) > SIGNATURE_MAX_CHARS
    assert "signature_truncated" not in row


def test_the_served_flag_survives_the_compact_encoding(repo):
    r, s = repo
    resp = _search(r, s)
    payload, meta = encode_response("search_symbols", resp, "compact")
    assert meta["encoding"] == "ss2", meta
    back = decoder.decode(payload)
    flagged = {row["name"]: row.get("signature_truncated") for row in back["results"]}
    assert flagged["add_widget_route"] is True
    assert flagged["add_widget_short"] is None


def test_an_uncapped_reply_writes_no_disclosure_column():
    rows = [
        {"id": "a.py::f#function", "name": "f", "kind": "function", "file": "a.py",
         "line": 1, "signature": "def f()", "summary": ""},
    ]
    payload, meta = encode_response("search_symbols", {"result_count": 1, "results": rows}, "compact")
    assert meta["encoding"] == "ss2"
    assert "signature_truncated" not in payload


def test_a_capped_row_round_trips_through_the_compact_encoding():
    rows = [
        {"id": "a.py::f#function", "name": "f", "kind": "function", "file": "a.py",
         "line": 1, "signature": "def f(" + "x" * 194, "summary": "",
         "signature_truncated": True},
        {"id": "a.py::g#function", "name": "g", "kind": "function", "file": "a.py",
         "line": 9, "signature": "def g()", "summary": ""},
    ]
    payload, _ = encode_response("search_symbols", {"result_count": 2, "results": rows}, "compact")
    assert "|summary|signature_truncated" in payload
    back = decoder.decode(payload)["results"]
    assert back[0]["signature_truncated"] is True
    assert "signature_truncated" not in back[1]
    assert back[1]["signature"] == "def g()"


def test_a_payload_written_by_the_old_encoder_still_decodes():
    payload = (
        "#MUNCH/1 tool=search_symbols enc=ss1\n\n"
        "result_count=1 __stypes=result_count:int "
        "__tables=s:results:id|name|kind|file|line|score|signature|summary\n\n"
        "s,a.py::f#function,f,function,a.py,1,,def f(),\n"
    )
    back = decoder.decode(payload)
    assert back["results"][0]["signature"] == "def f()"


def test_no_row_builder_copies_the_raw_signature():
    """Every row search_symbols builds goes through the one cap (BM25, fuzzy,
    semantic and fusion paths), so a fifth path cannot ship uncapped."""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if (
                isinstance(k, ast.Constant) and k.value == "signature"
                and isinstance(v, (ast.Subscript, ast.Call))
                and "signature" in ast.unparse(v)
                and "_row_signature" not in ast.unparse(v)
            ):
                offenders.append(node.lineno)
    assert offenders == [], f"raw signature copied into a row at lines {offenders}"
