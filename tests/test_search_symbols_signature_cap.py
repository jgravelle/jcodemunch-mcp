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


# The reported shape: five rows, one of them carrying a FastAPI-sized
# multi-line signature (that lookup carried 29,748 characters).
_HUGE_PARAMS = ",\n".join(
    f"    widget_parameter_{i}: Annotated[str, Doc('The widget parameter number {i}.')] = 'x'"
    for i in range(380)
)
GATE_FILES = {
    "app/widgets.py": (
        f"def add_widget_huge(\n{_HUGE_PARAMS},\n) -> None:\n    return None\n\n\n"
        + "".join(
            f"def add_widget_{n}(path: str) -> None:\n    return None\n\n\n"
            for n in ("alpha", "beta", "gamma", "delta")
        )
    ),
}


def test_a_reply_with_a_huge_signature_passes_the_savings_gate(tmp_path: Path, monkeypatch):
    """On the default (gated) path the reply is served compact. Before the cap
    the one signature was nearly the whole reply, the compact form saved less
    than the gate's threshold, and the reply was served as JSON."""
    from jcodemunch_mcp import config as _config

    monkeypatch.setitem(_config._GLOBAL_CONFIG, "refresh_on_read", False)
    root = tmp_path / "repo"
    for rel, text in GATE_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    storage = str(tmp_path / "store")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    assert res.get("success"), res
    resp = _search(res["repo"], storage)
    assert len(resp["results"]) == 5
    sid = next(r["id"] for r in resp["results"] if r["name"] == "add_widget_huge")
    whole = get_symbol_source(repo=res["repo"], symbol_id=sid, storage_path=storage)["signature"]
    assert len(whole) > 25000, len(whole)
    for fmt in (None, "auto"):
        _, meta = encode_response("search_symbols", resp, fmt)
        assert meta["encoding"] == "ss2", (fmt, meta)


# Reads of a symbol's signature are allowed only inside these functions: the
# cap itself, the summary dedupe (reads the whole signature on purpose) and the
# BM25 breakdown (scores it, never serves it).
_SIGNATURE_READERS = {"_row_signature", "_row_summary", "_bm25_breakdown"}


def _signature_reads_outside_the_cap(source: str) -> list[int]:
    """Lines that read `x["signature"]` or `x.get("signature", ...)` outside
    `_SIGNATURE_READERS`. Any spelling that serves a raw signature reads it
    first, so this catches a dict literal, an assignment to a row key and a
    read into a local alike."""
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def owner(node: ast.AST) -> str:
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
        return ""

    out = []
    for node in ast.walk(tree):
        is_read = (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "signature"
        ) or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and bool(node.args)
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "signature"
        )
        if is_read and owner(node) not in _SIGNATURE_READERS:
            out.append(node.lineno)
    return out


def test_the_reuse_audit_candidate_keeps_the_flag():
    from jcodemunch_mcp.investigator.reuse_audit import _collect

    into: dict = {}
    rows = [
        {"id": "a.py::f#function", "name": "f", "kind": "function", "file": "a.py",
         "line": 1, "signature": "def f(" + "x" * 194, "signature_truncated": True},
        {"id": "a.py::g#function", "name": "g", "kind": "function", "file": "a.py",
         "line": 9, "signature": "def g()"},
    ]
    _collect(rows, channel="lexical", ceiling=1.0, why="t", include_tests=True, into=into)
    assert into["a.py::f#function"].to_dict()["signature_truncated"] is True
    assert "signature_truncated" not in into["a.py::g#function"].to_dict()


@pytest.mark.parametrize("spelling", [
    'def build(sym):\n    return {"signature": sym["signature"]}\n',
    'def build(sym):\n    s = sym["signature"]\n    return {"signature": s}\n',
    'def build(sym, entry):\n    entry["signature"] = sym["signature"]\n',
    'def build(sym):\n    return {"signature": sym.get("signature", "")}\n',
])
def test_the_ratchet_catches_each_spelling_of_the_defect(spelling):
    assert _signature_reads_outside_the_cap(spelling) == [2]


def test_no_row_builder_copies_the_raw_signature():
    """Every row search_symbols builds goes through the one cap (BM25, fuzzy,
    semantic and fusion paths), so a fifth path cannot ship uncapped."""
    offenders = _signature_reads_outside_the_cap(SRC.read_text(encoding="utf-8"))
    assert offenders == [], f"raw signature read outside the cap at lines {offenders}"


