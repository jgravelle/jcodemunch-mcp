"""`get_ranked_context` marks a missing identifier in the BODY (LEDGER L-284).

`search_symbols` puts "no symbol has this name" in the body since the
no_exact_match fix, because `meta_fields: []` is the shipped default and the
dispatcher strips `_meta`. `get_ranked_context` returns ranked rows for the
same identifier query, and on a default install the caller got those rows
with nothing saying that none of them is the symbol asked for.

The marker now lives in `retrieval/verdict.py` (`mark_no_exact_match`) and
both tools call it, so the next tool that ranks symbols for a query inherits
it. Rows and their order do not change, and `no_exact_match` is not an
absence: rows were returned.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

QUERY = "zzqqxxNotARealSymbolAtAll"
WARNING_KEY = "⚠ warning"


def _content(res):
    from mcp.types import CallToolResult

    return res.content if isinstance(res, CallToolResult) else res


def _set_meta_fields(monkeypatch, value):
    from jcodemunch_mcp import config as _config

    real = _config.get

    def _get(key, default=None, **kw):
        if key == "meta_fields":
            return value
        return real(key, default, **kw)

    monkeypatch.setattr(_config, "get", _get)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    from jcodemunch_mcp.tools.index_folder import index_folder

    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    proj = tmp_path / "proj"
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "table.py").write_text(
        "def symbol_table():\n    return {}\n\n\n"
        "def all_items():\n    return []\n\n\n"
        "def real_path(p):\n    return p\n\n\n"
        "def not_empty(x):\n    return bool(x)\n\n\n"
        "def buildVerdictForIndex():\n    return 1\n\n\n"
        "def loadConfig():\n    return 2\n",
        encoding="utf-8",
    )
    (proj / "pkg" / "atlas.py").write_text(
        "class SymbolAtlas:\n    pass\n", encoding="utf-8"
    )
    res = index_folder(str(proj), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True
    return res["repo"]


EXITS = {"ranked": {}, "fusion": {"fusion": True}}


async def _call(args: dict) -> dict:
    from jcodemunch_mcp import server

    with patch("jcodemunch_mcp.tools.embed_repo._detect_provider", return_value=None):
        out = _content(
            await server.call_tool("get_ranked_context", {**args, "format": "json"})
        )
    return json.loads(out[0].text)


def _ids(body):
    return [r.get("symbol_id") or r.get("id") for r in body.get("context_items", [])]


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_name", list(EXITS))
async def test_a_missing_identifier_is_marked_on_a_default_install(
    repo, monkeypatch, exit_name
):
    _set_meta_fields(monkeypatch, [])
    body = await _call({"repo": repo, "query": QUERY, **EXITS[exit_name]})
    assert body.get("context_items"), f"precondition: {exit_name} returns rows: {body}"
    assert "_meta" not in body, "precondition: the default install strips _meta"
    ne = body.get("negative_evidence") or {}
    assert ne.get("verdict") == "no_exact_match", (
        f"{exit_name}: rows came back with no body-level marker: {body}"
    )
    assert ne.get("queried") == QUERY and ne.get("exact") == 0
    assert f"No symbol named '{QUERY}' is in this index" in body.get(WARNING_KEY, "")


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_name", list(EXITS))
async def test_the_marker_does_not_change_the_rows(repo, monkeypatch, exit_name):
    from jcodemunch_mcp.tools import get_ranked_context as _grc

    _set_meta_fields(monkeypatch, [])
    marked = await _call({"repo": repo, "query": QUERY, **EXITS[exit_name]})
    monkeypatch.setattr(_grc, "_mark_no_exact_match", lambda *a, **k: None)
    plain = await _call({"repo": repo, "query": QUERY, **EXITS[exit_name]})
    assert _ids(marked) and _ids(marked) == _ids(plain)
    assert (plain.get("negative_evidence") or {}).get("verdict") != "no_exact_match"


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_name", list(EXITS))
@pytest.mark.parametrize(
    "query,why",
    [
        ("loadConfig", "an exact match exists"),
        ("buildVerdict", "a prefix match exists"),
        ("symbol table for all items", "prose: the exact check does not run"),
    ],
)
async def test_no_marker_without_the_defect(repo, monkeypatch, exit_name, query, why):
    _set_meta_fields(monkeypatch, [])
    body = await _call({"repo": repo, "query": query, **EXITS[exit_name]})
    assert body.get("context_items"), f"precondition ({why}): rows returned"
    assert (body.get("negative_evidence") or {}).get("verdict") != "no_exact_match", (
        f"{exit_name}: marker emitted although {why}: {body}"
    )
    assert "No symbol named" not in body.get(WARNING_KEY, "")


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_name", list(EXITS))
async def test_the_marker_downgrades_ok_and_is_not_an_absence(
    repo, monkeypatch, exit_name
):
    """Rows came back, so the state is `low_confidence`: never `ok` beside
    "No symbol named X", and never `absent`."""
    _set_meta_fields(monkeypatch, ["verdict"])
    body = await _call({"repo": repo, "query": QUERY, **EXITS[exit_name]})
    state = ((body.get("_meta") or {}).get("verdict") or {}).get("state")
    assert state == "low_confidence", (exit_name, body.get("_meta"))


@pytest.mark.asyncio
async def test_the_fusion_parameter_takes_the_fusion_exit(repo, monkeypatch):
    """Precondition for every `[fusion]` arm: fused rows carry `fusion_score`."""
    _set_meta_fields(monkeypatch, [])
    body = await _call({"repo": repo, "query": QUERY, **EXITS["fusion"]})
    assert body["context_items"] and all(
        "fusion_score" in r for r in body["context_items"]
    ), body


def test_both_tools_read_the_one_marker():
    """search_symbols' marker is the shared one, not a second copy."""
    from jcodemunch_mcp.retrieval import verdict
    from jcodemunch_mcp.tools import get_ranked_context as grc
    from jcodemunch_mcp.tools import search_symbols as ss

    assert grc._mark_no_exact_match is verdict.mark_no_exact_match
    assert ss._mark_no_exact_match is verdict.mark_no_exact_match


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_name", list(EXITS))
async def test_the_marker_survives_the_compact_encoder(repo, monkeypatch, exit_name):
    """The default wire format is the compact encoder; the warning rides
    `schema_driven.UNIVERSAL_BODY_JSON` there."""
    from jcodemunch_mcp import server

    _set_meta_fields(monkeypatch, [])
    out = _content(
        await server.call_tool(
            "get_ranked_context",
            {"repo": repo, "query": QUERY, "format": "compact", **EXITS[exit_name]},
        )
    )
    text = out[0].text
    assert f"No symbol named '{QUERY}' is in this index" in text, text[:800]
    assert "no_exact_match" in text
