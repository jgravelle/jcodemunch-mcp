"""A compact answer carries each row's verdict that is not the default (LEDGER L-325).

A row's `_freshness` says whether the code it points at changed since the index
read it (`edited_uncommitted`, `stale_index`, `unknown`). On the shipped default
(`meta_fields: []`) the dispatcher deletes `_meta`, so the per-row verdict is
the only place a stale answer says it is stale. The compact encoding dropped
every row key its table did not declare, and no table declared `_freshness`, so
a stale answer reached the agent byte-identical to a fresh one.

The verdict travels when a row's value is not the default. An all-fresh answer
keeps the bytes it had, so every figure measured on a fresh corpus is unmoved.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from jcodemunch_mcp.encoding import decoder, encode_response
from jcodemunch_mcp.encoding import schema_driven as sd
from jcodemunch_mcp.tools.get_context_bundle import get_context_bundle
from jcodemunch_mcp.tools.get_ranked_context import get_ranked_context
from jcodemunch_mcp.tools.get_symbol import get_symbol_source
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols

VERDICTS = ("_freshness", "_runtime_confidence", "_runtime_body")

FILES = {
    "pkg/__init__.py": "",
    "pkg/core.py": "".join(
        f"def helper_{i}(x):\n    return x + {i}\n\n\n" for i in range(6)
    )
    + "def use_helpers(x):\n    return helper_0(x) + helper_1(x)\n",
    "pkg/app.py": "from pkg.core import helper_0, use_helpers\n\n\n"
    "def run(x):\n    return use_helpers(helper_0(x))\n",
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def stale_repo(tmp_path: Path, monkeypatch):
    """A git repo indexed, then edited with no re-index and read refresh off."""
    from jcodemunch_mcp import config as _config

    monkeypatch.setitem(_config._GLOBAL_CONFIG, "refresh_on_read", False)
    root = tmp_path / "repo"
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init", "--no-verify")
    storage = str(tmp_path / "store")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    assert res.get("success"), res
    core = root / "pkg" / "core.py"
    core.write_text(
        core.read_text(encoding="utf-8") + "\n# edited after indexing\n",
        encoding="utf-8",
    )
    # The verdict reads an edit only past one second of the indexed mtime.
    later = core.stat().st_mtime + 30
    os.utime(core, (later, later))
    return res["repo"], storage


def _verdicts(obj) -> list[tuple[str, str]]:
    """Every (key, value) for a row verdict anywhere in a response, in order."""
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in VERDICTS and isinstance(v, str):
                out.append((k, v))
            elif not k.startswith("_meta"):
                out.extend(_verdicts(v))
    elif isinstance(obj, list):
        for x in obj:
            out.extend(_verdicts(x))
    return out


def _non_default(pairs):
    return sorted(p for p in pairs if not (p[0] == "_freshness" and p[1] == "fresh"))


# The tools that stamp `_freshness` on each row. find_references,
# get_call_hierarchy, get_blast_radius and search_text stamp a row verdict only
# as `_runtime_confidence`, and only when runtime traces were ingested; the
# table-level tests below carry that verdict through the shared encoder.
CALLS = {
    "search_symbols": lambda r, s: search_symbols(
        repo=r, query="helper", detail_level="compact", storage_path=s
    ),
    "search_symbols_standard": lambda r, s: search_symbols(
        repo=r, query="helper", detail_level="standard", storage_path=s
    ),
    "get_ranked_context": lambda r, s: get_ranked_context(
        repo=r, query="helper", storage_path=s
    ),
    "get_symbol_source": lambda r, s: get_symbol_source(
        repo=r, symbol_id="pkg/core.py::helper_0#function", storage_path=s
    ),
    "get_context_bundle": lambda r, s: get_context_bundle(
        repo=r, symbol_id="pkg/core.py::helper_0#function", storage_path=s
    ),
}


@pytest.mark.parametrize("call", sorted(CALLS))
def test_a_stale_row_verdict_survives_the_compact_encoding(stale_repo, call):
    repo, storage = stale_repo
    tool = call.removesuffix("_standard")
    resp = CALLS[call](repo, storage)
    resp = {k: v for k, v in resp.items() if k != "_meta"}  # the shipped default
    sent = _non_default(_verdicts(resp))
    assert sent, (call, "the fixture must give this tool a stale row", resp)
    payload, meta = encode_response(tool, resp, "compact")
    assert meta["encoding"] != "json", (call, meta)
    back = decoder.decode(payload)
    assert _non_default(_verdicts(back)) == sent, (
        call,
        meta["encoding"],
        payload[:600],
    )


def test_the_stale_search_answer_names_a_stale_row(stale_repo):
    """Non-vacuity for the property above: the fixture does produce stale rows,
    and the compact form of the default search shows them."""
    repo, storage = stale_repo
    resp = search_symbols(
        repo=repo, query="helper", detail_level="compact", storage_path=storage
    )
    resp = {k: v for k, v in resp.items() if k != "_meta"}
    assert ("_freshness", "edited_uncommitted") in _verdicts(resp), resp
    payload, meta = encode_response("search_symbols", resp, "auto")
    assert meta["encoding"] != "json", meta
    assert "edited_uncommitted" in payload, payload


SPEC = sd.TableSpec(
    key="results",
    tag="s",
    cols=["id", "name", "line"],
    intern=["id"],
    types={"line": "int"},
)


def _encode(rows):
    return sd.encode(
        "t",
        {"results": rows, "result_count": len(rows)},
        "x1",
        [SPEC],
        ("result_count",),
    )[0]


def _decode(payload):
    return sd.decode(payload, [SPEC], ("result_count",))


def _rows(**extra):
    return [
        {
            "id": f"a.py::f{i}#function",
            "name": f"f{i}",
            "line": i + 1,
            **{k: v[i] for k, v in extra.items()},
        }
        for i in range(3)
    ]


def test_an_all_fresh_answer_keeps_its_bytes():
    """`fresh` is the implied default: stamping it on every row changes nothing on the wire."""
    plain = _encode(_rows())
    assert _encode(_rows(_freshness=["fresh"] * 3)) == plain
    assert "_freshness" not in plain


def test_the_search_answer_bytes_are_frozen_for_a_fresh_corpus():
    """The bytes the 1.108.466 encoder wrote for an all-fresh search answer, so a
    benchmark measured on a fresh corpus counts the same tokens after this fix."""
    rows = [
        {
            "id": f"routes_{f}.go::Router{f}x{t}.Use#method",
            "name": "Use",
            "kind": "method",
            "file": f"routes_{f}.go",
            "line": 8 + 12 * t,
            "byte_length": 126,
            "_freshness": "fresh",
        }
        for f in range(2)
        for t in range(2)
    ]
    payload, meta = encode_response(
        "search_symbols", {"result_count": 4, "results": rows}, "compact"
    )
    assert meta["encoding"] == "ss1"
    assert payload == (
        "#MUNCH/1 tool=search_symbols enc=ss1\n\n"
        "@1=routes_0.go\n@2=routes_1.go\n\n"
        "result_count=4 __stypes=result_count:int "
        "__tables=s:results:id|name|kind|file|line|score|signature|summary\n\n"
        "s,@1::Router0x0.Use#method,Use,method,@1,8,,,\n"
        "s,@1::Router0x1.Use#method,Use,method,@1,20,,,\n"
        "s,@2::Router1x0.Use#method,Use,method,@2,8,,,\n"
        "s,@2::Router1x1.Use#method,Use,method,@2,20,,,\n"
    ), payload


@pytest.mark.parametrize(
    "extra",
    [
        {"_freshness": ["fresh", "edited_uncommitted", "fresh"]},
        {"_freshness": ["stale_index", "stale_index", "unknown"]},
        {
            "_runtime_confidence": ["confirmed", "declared_only", "unmapped"],
            "_runtime_body": ["earlier", None, None],
        },
    ],
    ids=["one-stale", "all-not-fresh", "runtime"],
)
def test_a_non_default_verdict_round_trips(extra):
    rows = _rows(**{k: v for k, v in extra.items()})
    for r in rows:
        for k in list(r):
            if r[k] is None:
                del r[k]
    back = _decode(_encode(rows))["results"]
    for want, got in zip(rows, back):
        for k in VERDICTS:
            assert got.get(k) == want.get(k), (k, want, got)


def test_a_fresh_row_beside_a_stale_one_says_fresh():
    """Once the column is on the wire, a fresh row reads `fresh`, not blank."""
    back = _decode(_encode(_rows(_freshness=["fresh", "unknown", "fresh"])))["results"]
    assert [r.get("_freshness") for r in back] == ["fresh", "unknown", "fresh"]


def test_a_decoder_that_predates_the_column_still_reads_the_rows():
    """The verdict columns follow the declared ones, so reading the declared
    columns by position, which every decoder before this fix did, is unchanged."""
    from jcodemunch_mcp.encoding.format import read_table, split_sections

    def declared_cells(payload):
        _, blocks = split_sections(payload)
        rows = [r for b in blocks for r in (read_table(b, "s") or [])]
        return [r[: len(SPEC.cols)] for r in rows]

    stale = _encode(_rows(_freshness=["edited_uncommitted"] * 3))
    assert declared_cells(stale) == declared_cells(_encode(_rows()))
    assert all(
        len(r) > len(SPEC.cols)
        for r in [
            r for b in split_sections(stale)[1] for r in (read_table(b, "s") or [])
        ]
    )
