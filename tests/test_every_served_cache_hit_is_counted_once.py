"""Every served result-cache hit is counted once, whichever cache served it (#864).

`search_symbols` keeps its OWN cache and never calls `cache_get`, yet it
reports each served hit to `cache_hit_validated`, whose contract is "never
counts a hit -- `cache_get` already did". So its hits reached the validated
buckets and never `total_hits`, `by_tool` or `hit_rate`: two identical calls
published `total_hits: 0` beside `hits_validated_fresh: 1` and a revalidated
rate of 1.0 over zero hits. And `hits_unvalidated` is `total_hits - validated`,
so its validations were subtracted from OTHER tools' unvalidated hits, with a
clamp at zero hiding the result.

The invariant, per tool and in total: `hits_validated_fresh +
hits_validated_stale <= hits`.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

from jcodemunch_mcp import server
from jcodemunch_mcp.storage import token_tracker

SRC = Path(__file__).resolve().parent.parent / "src" / "jcodemunch_mcp"


@pytest.fixture()
def _indexed(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "idx"))
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "session.py").write_text(
        "def refresh_token(user):\n    return user\n\n\ndef caller():\n    return refresh_token(1)\n",
        encoding="utf-8",
    )
    body = json.loads(_call("index_folder", {"path": str(proj)}))
    return body["repo"]


def _call(tool: str, args: dict) -> str:
    content = asyncio.run(server.call_tool(tool, dict(args)))
    return content[0].text if isinstance(content, list) else content.content[0].text


def _assert_validated_within_hits(stats: dict) -> None:
    validated = stats["hits_validated_fresh"] + stats["hits_validated_stale"]
    assert validated <= stats["total_hits"], stats
    for tool, row in stats["by_tool"].items():
        assert row["hits_validated_fresh"] + row["hits_validated_stale"] <= row["hits"], (tool, stats)


def test_a_search_symbols_hit_is_a_hit(_indexed):
    args = {"repo": _indexed, "query": "refresh_token", "format": "json"}
    _call("search_symbols", args)
    _call("search_symbols", args)

    stats = token_tracker.result_cache_stats()

    assert stats["total_hits"] == 1, stats
    assert stats["total_misses"] == 1, stats
    row = stats["by_tool"]["search_symbols"]
    assert (row["hits"], row["misses"]) == (1, 1), stats
    assert row["hits_validated_fresh"] + row["hits_validated_stale"] == 1, stats
    assert row["hits_unvalidated"] == 0, stats
    _assert_validated_within_hits(stats)


def test_another_tools_unvalidated_hit_is_not_absorbed(_indexed):
    """The mixed case: `find_references` serves a hit nobody validates, and
    `search_symbols` a validated one. The unvalidated bucket must hold exactly
    the former; the clamp at zero used to hide the subtraction."""
    for tool, args in (
        ("search_symbols", {"query": "refresh_token"}),
        ("find_references", {"identifier": "refresh_token"}),
    ):
        args = dict(args, repo=_indexed, format="json")
        _call(tool, args)
        _call(tool, args)

    stats = token_tracker.result_cache_stats()

    assert stats["total_hits"] == 2, stats
    assert stats["hits_unvalidated"] == 1, stats
    assert stats["by_tool"]["find_references"]["hits_unvalidated"] == 1, stats
    _assert_validated_within_hits(stats)


def test_every_validating_consumer_records_its_lookups():
    """The ratchet over the PROPERTY, not the one reported site: a module that
    reports a validated hit must also record the lookup that served it, through
    the shared LRU (`result_cache_get`) or, for a private cache,
    `result_cache_record_lookup`. Otherwise the validation lands with no hit
    beside it, which is #864 in whichever tool writes the next cache."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.parent.name == "storage" and path.name in ("token_tracker.py", "__init__.py"):
            continue
        called = _called_names(path.read_text(encoding="utf-8"))
        if "result_cache_hit_validated" in called and not called & {"result_cache_get", "result_cache_record_lookup"}:
            offenders.append(str(path.relative_to(SRC)))

    assert not offenders, offenders


def _called_names(source: str) -> set[str]:
    """Names actually CALLED in a module, from its AST. Review round 1: a text
    scan counted a name in a comment or docstring as a call, so the ratchet
    could pass against the defect it names."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_a_name_in_prose_is_not_a_call():
    """The ratchet's own non-vacuity: a record call written only in a comment
    or docstring must not satisfy it."""
    source = (
        '"""result_cache_record_lookup("x", hit=True) is documented here."""\n'
        "# result_cache_get(tool, repo, key)\n"
        "def f():\n"
        "    result_cache_hit_validated('t', stale=False)\n"
    )
    called = _called_names(source)

    assert "result_cache_hit_validated" in called
    assert not called & {"result_cache_get", "result_cache_record_lookup"}
