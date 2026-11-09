"""A missing identifier is marked in the BODY, so a default install sees it.

Reported by a long-time customer: `search_symbols(repo,
"zzqqxxNotARealSymbolAtAll")` returned ten ranked rows. BM25 scores camelCase
sub-tokens, so one common word (not / symbol / at / all) qualifies a row. The
tool already knew no symbol has the name -- `_meta.exact_match` said
`found: false` and the verdict read `low_confidence` (v1.108.173) -- but
`meta_fields: []` is the shipped default and the dispatcher strips `_meta`, so
the caller got ten rows and no marker. A zero-row query keeps its body-level
`negative_evidence` and warning through the same dispatcher; this one did not.

The fix reuses that body channel: `negative_evidence.verdict ==
"no_exact_match"` plus the `⚠ warning` key, on every exit that returns rows
(lexical, semantic, fusion). Rows and their order do not change.

⚠ `no_exact_match` is not an absence: rows WERE returned. The verdict state
stays `low_confidence`, and the session journal must not file it as a dead end.
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


def _toward(texts, *_a, **_k):
    """Every symbol and the query on one axis, so every cosine is positive."""
    return [[1.0] + [0.0] * 7 for _ in texts]


_PROVIDER = ("sentence_transformers", "all-MiniLM-L6-v2")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A real folder index whose symbols share common words with QUERY."""
    from jcodemunch_mcp.tools.embed_repo import embed_repo
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.search_symbols import _result_cache

    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    monkeypatch.setenv("JCODEMUNCH_EMBED_MODEL", _PROVIDER[1])
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
        "class SymbolAtlas:\n    pass\n",
        encoding="utf-8",
    )
    # Shares "verdict" with `buildVerdict`, whose prefix match lives in table.py:
    # a `file_pattern` on this file puts the prefix match off the page.
    (proj / "pkg" / "verdicts.py").write_text(
        "def verdict_cache():\n    return 0\n",
        encoding="utf-8",
    )
    res = index_folder(str(proj), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True
    with (
        patch(
            "jcodemunch_mcp.tools.embed_repo._detect_provider", return_value=_PROVIDER
        ),
        patch("jcodemunch_mcp.tools.embed_repo.embed_texts", side_effect=_toward),
    ):
        embed_repo(res["repo"], storage_path=str(store))
    _result_cache.clear()
    yield res["repo"]
    _result_cache.clear()


async def _call(args: dict) -> dict:
    from jcodemunch_mcp import server
    from jcodemunch_mcp.tools.search_symbols import _result_cache

    _result_cache.clear()
    with (
        patch(
            "jcodemunch_mcp.tools.embed_repo._detect_provider", return_value=_PROVIDER
        ),
        patch("jcodemunch_mcp.tools.embed_repo.embed_texts", side_effect=_toward),
    ):
        out = _content(
            await server.call_tool("search_symbols", {**args, "format": "json"})
        )
    return json.loads(out[0].text)


EXITS = {
    "lexical": {},
    "semantic": {"semantic": True},
    "fusion": {"fusion": True},
}


def _ids(body):
    return [r["id"] for r in body.get("results", [])]


# --- the reported shape, at every exit -------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_name", list(EXITS))
async def test_a_missing_identifier_is_marked_on_a_default_install(
    repo, monkeypatch, exit_name
):
    _set_meta_fields(monkeypatch, [])
    body = await _call({"repo": repo, "query": QUERY, **EXITS[exit_name]})

    assert body.get("result_count", 0) > 0, (
        f"precondition: {exit_name} returns rows: {body}"
    )
    assert "_meta" not in body, "precondition: the default install strips _meta"
    ne = body.get("negative_evidence")
    assert ne is not None and ne.get("verdict") == "no_exact_match", (
        f"{exit_name}: rows came back with no body-level marker: {body}"
    )
    assert ne.get("queried") == QUERY
    assert ne.get("exact") == 0 and ne.get("prefix") == 0
    warning = body.get(WARNING_KEY, "")
    assert f"No symbol named '{QUERY}' is in this index" in warning, body


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_name", list(EXITS))
async def test_the_marker_does_not_change_the_rows(repo, monkeypatch, exit_name):
    """Same ids, same order, with and without the marker step."""
    from jcodemunch_mcp.tools import search_symbols as _ss

    _set_meta_fields(monkeypatch, [])
    marked = await _call({"repo": repo, "query": QUERY, **EXITS[exit_name]})
    monkeypatch.setattr(_ss, "_attach_no_exact_match", lambda *a, **k: None)
    plain = await _call({"repo": repo, "query": QUERY, **EXITS[exit_name]})

    assert _ids(marked) and _ids(marked) == _ids(plain)
    assert plain.get("negative_evidence", {}).get("verdict") != "no_exact_match", (
        "non-vacuity: with the step disabled the marker must be gone"
    )


# --- no marker where it would be false or noise -----------------------------


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
    assert body.get("result_count", 0) > 0, f"precondition ({why}): rows returned"
    assert (body.get("negative_evidence") or {}).get("verdict") != "no_exact_match", (
        f"{exit_name}: marker emitted although {why}: {body}"
    )
    assert "No symbol named" not in body.get(WARNING_KEY, "")


@pytest.mark.asyncio
async def test_a_filter_that_hides_the_exact_symbol_does_not_claim_the_index_lacks_it(
    repo, monkeypatch
):
    """`kind="function"` excludes the class `SymbolAtlas`. The rows that remain
    share the word "symbol", but the index HAS a symbol of that name, so "No
    symbol named ... is in this index" would be false."""
    _set_meta_fields(monkeypatch, [])
    body = await _call({"repo": repo, "query": "SymbolAtlas", "kind": "function"})
    assert body.get("result_count", 0) > 0, f"precondition: rows returned: {body}"
    assert (body.get("negative_evidence") or {}).get("verdict") != "no_exact_match", (
        body
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_name", list(EXITS))
async def test_a_prefix_match_off_the_page_is_still_a_prefix_match(
    repo, monkeypatch, exit_name
):
    """`file_pattern="pkg/verdicts.py"` leaves only `verdict_cache` on the page;
    `buildVerdictForIndex` is a prefix match for `buildVerdict` elsewhere in the
    index. The page alone would read `prefix: 0` and emit the marker."""
    _set_meta_fields(monkeypatch, [])
    body = await _call(
        {
            "repo": repo,
            "query": "buildVerdict",
            "file_pattern": "pkg/verdicts.py",
            **EXITS[exit_name],
        }
    )
    names = [r["name"] for r in body.get("results", [])]
    assert names, f"precondition: rows returned: {body}"
    assert "buildVerdictForIndex" not in names, (
        "precondition: prefix match off the page"
    )
    assert (body.get("negative_evidence") or {}).get("verdict") != "no_exact_match", (
        f"{exit_name}: {body}"
    )
    assert "No symbol named" not in body.get(WARNING_KEY, "")


# --- not an absence --------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_name", list(EXITS))
async def test_no_exact_match_is_not_a_citable_absence(repo, monkeypatch, exit_name):
    from jcodemunch_mcp.tools.session_journal import SessionJournal, get_journal

    _set_meta_fields(monkeypatch, ["verdict"])
    body = await _call({"repo": repo, "query": QUERY, **EXITS[exit_name]})
    verdict = body["_meta"]["verdict"]
    assert verdict["state"] != "absent"
    # `ok` beside "No symbol named X" contradicts itself; every exit downgrades.
    assert verdict["state"] == "low_confidence", (exit_name, verdict)
    assert not str(verdict.get("evidence_ref", "")).startswith("absent:")
    assert "absence_evidence" not in body["_meta"]

    entries = [
        e for e in get_journal().get_negative_evidence_log() if e.get("query") == QUERY
    ]
    assert entries, "precondition: the journal recorded the marker"
    assert not any(SessionJournal.entry_is_citable(e) for e in entries), (
        "no_exact_match was filed as a dead end; rows were returned"
    )


def test_the_marker_survives_compact_encoding(repo):
    from jcodemunch_mcp.encoding import encode_response, registry
    from jcodemunch_mcp.encoding.decoder import decode
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    result = search_symbols(repo, QUERY, max_results=5)
    assert (result.get("negative_evidence") or {}).get("verdict") == "no_exact_match"
    # The schema encoder itself, not `encode_response`: the dispatcher falls back
    # to JSON when an encoder drops a universal body key, so a skip on that
    # fallback would pass exactly when the encoder is broken.
    encoder = registry.for_tool("search_symbols")
    assert encoder is not None, "search_symbols has a schema encoder"
    payload, _enc_id = encoder.encode("search_symbols", result)
    assert isinstance(payload, str), "the schema encoder produced a compact payload"
    restored = decode(payload)
    assert restored.get("negative_evidence") == result["negative_evidence"]
    assert restored.get(WARNING_KEY) == result[WARNING_KEY]

    # And the dispatcher keeps the compact form rather than falling back.
    _payload, info = encode_response("search_symbols", result, "compact")
    assert info.get("encoding") != "json", info
