"""The token benchmark counts what the server serves an agent, not the tool function's dict.

Before 2026-11-08 `run_benchmark.measure_jmunch` called `search_symbols` and
`get_symbol_source` as Python functions and counted `json.dumps` of the return
value. No agent receives that: on a default install the dispatcher strips
`_meta` (`meta_fields: []`) and applies the compact encoding. On the pinned
express corpus the "param bind" search counted 460 tokens that way and is
served as 201.

The served method counts `server.call_tool`'s text on `config.DEFAULTS`, makes
each lookup stand alone, measures what a session adds on top (the license
notice, the steering hint, the already-delivered note), and keeps the raw
method behind `--method raw`. A `--floor` run counts the way the committed
reference did, so a Floor never compares two ways of counting.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "benchmarks" / "harness"


@pytest.fixture
def R(monkeypatch):
    monkeypatch.syspath_prepend(str(HARNESS))
    sys.modules.pop("run_benchmark", None)
    import run_benchmark

    yield run_benchmark
    run_benchmark.MEASURE_METHOD = "served"
    run_benchmark.SESSION_COSTS = None


@pytest.fixture
def repo(tmp_path, monkeypatch):
    from jcodemunch_mcp import license_notice, license_reminder, server
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.search_symbols import _result_cache

    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / ".idx"))
    monkeypatch.setenv("JCODEMUNCH_USE_AI_SUMMARIES", "false")
    saved_steer = dict(server._steer_state)
    saved_notice = dict(license_notice.NOTICE_STATE)
    saved_reminder = dict(license_reminder.REMINDER_STATE)
    src = tmp_path / "src"
    src.mkdir()
    for i in range(8):
        (src / f"route{i}.py").write_text(
            f"def register_route_{i}(router, handler):\n"
            f"    '''Register route {i} on the router.'''\n"
            f"    router.add(handler)\n"
            f"    return handler\n",
            encoding="utf-8",
        )
    res = index_folder(str(src), use_ai_summaries=False)
    _result_cache.clear()
    yield res["repo"]
    server._steer_state.update(saved_steer)
    license_notice.NOTICE_STATE.update(saved_notice)
    license_reminder.REMINDER_STATE.update(saved_reminder)


def _call_tool_text(tool: str, args: dict) -> str:
    """`server.call_tool`'s text, read here without the harness's own helpers."""
    from mcp.types import CallToolResult

    from jcodemunch_mcp import server

    res = asyncio.run(server.call_tool(tool, dict(args)))
    items = res.content if isinstance(res, CallToolResult) else res
    return "".join(item.text for item in items)


def _rows(text: str) -> list[dict]:
    if text.lstrip().startswith("{"):
        return json.loads(text)["results"]
    from jcodemunch_mcp.encoding.decoder import decode

    return decode(text)["results"]


def _served(R):
    R._use_default_install_config()
    R._quiet_once_per_process_notices()
    R.MEASURE_METHOD = "served"


def test_the_served_method_counts_the_text_the_server_serves(R, repo):
    import tiktoken

    _served(R)
    got = R.measure_jmunch(repo, "register route")
    R._start_lookup_session()
    enc = tiktoken.get_encoding("cl100k_base")
    search = _call_tool_text(
        "search_symbols",
        {
            "repo": repo,
            "query": "register route",
            "max_results": 5,
            "detail_level": "standard",
        },
    )
    ids = [r["id"] for r in _rows(search)][:3]
    expected = len(enc.encode(search)) + sum(
        len(
            enc.encode(
                _call_tool_text("get_symbol_source", {"repo": repo, "symbol_id": sid})
            )
        )
        for sid in ids
    )
    assert got["hits_fetched"] == len(ids) == 3
    assert got["tokens"] == expected


def test_the_raw_method_is_kept_and_counts_more(R, repo):
    _served(R)
    R.MEASURE_METHOD = "raw"
    raw = R.measure_jmunch(repo, "register route")
    R.MEASURE_METHOD = "served"
    served = R.measure_jmunch(repo, "register route")
    assert raw["hits_fetched"] == served["hits_fetched"] == 3, (
        "both methods fetch the same three symbols"
    )
    assert served["search_tokens"] < raw["search_tokens"], (
        "the raw dict carries _meta the agent never receives"
    )


def test_a_user_config_does_not_change_what_is_counted(R, repo):
    from jcodemunch_mcp import config as cfg

    _served(R)
    default = R.measure_jmunch(repo, "register route")["tokens"]
    # A developer config that keeps every `_meta` field and turns encoding off.
    cfg._GLOBAL_CONFIG["meta_fields"] = None
    cfg._GLOBAL_CONFIG["server_output"] = "raw"
    assert R.measure_jmunch(repo, "register route")["tokens"] > default, (
        "precondition: that config costs more"
    )
    R._use_default_install_config()
    assert R.measure_jmunch(repo, "register route")["tokens"] == default


def test_each_lookup_stands_alone(R, repo):
    """Five lookups in one process: the steering hint never fires inside one, and a
    repeat of the same lookup costs what the first did (no already-delivered note)."""
    from jcodemunch_mcp import server

    _served(R)
    first = R.measure_jmunch(repo, "register route")["tokens"]
    for _ in range(4):
        again = R.measure_jmunch(repo, "register route")["tokens"]
        assert not server._steer_state["nudged"], (
            "the steering hint fired inside a measured lookup"
        )
        assert again == first


def test_the_one_session_cost_left_out_of_each_lookup_is_measured(R, repo):
    """The same lookups as ONE session, recomputed here without the harness's helpers:
    `served_one_session_extra` reports that total minus the per-lookup total. The
    already-delivered note rides the JSON fetch answers inside a single lookup too
    (the search listed the symbol first), so the difference can be small or zero;
    what matters is that it is measured, not assumed."""
    import tiktoken

    from jcodemunch_mcp import server

    _served(R)
    queries = ["register route", "router handler", "register route"]
    rows = [R.measure_jmunch(repo, q) for q in queries]
    results = [
        {
            "repo": repo,
            "tasks": [
                {"query": q, "jmunch_tokens": r["tokens"]}
                for q, r in zip(queries, rows)
            ],
        }
    ]
    extra = R.served_one_session_extra(results)

    enc = tiktoken.get_encoding("cl100k_base")
    R._start_lookup_session()
    server._steer_state["nudged"] = True
    one_session = 0
    for q in queries:
        search = _call_tool_text(
            "search_symbols",
            {"repo": repo, "query": q, "max_results": 5, "detail_level": "standard"},
        )
        one_session += len(enc.encode(search))
        for sid in [r["id"] for r in _rows(search)][:3]:
            one_session += len(
                enc.encode(
                    _call_tool_text(
                        "get_symbol_source", {"repo": repo, "symbol_id": sid}
                    )
                )
            )
    assert extra[repo] == one_session - sum(r["tokens"] for r in rows)


def test_the_license_notice_is_priced_without_using_up_this_machines_notice(R, repo):
    from jcodemunch_mcp import license_notice

    _served(R)
    costs = R.served_session_costs(repo)
    assert (
        costs["license_notice_tokens"] is not None
        and costs["license_notice_tokens"] > 0
    )
    assert costs["steering_hint_tokens"] > 0, (
        "a forced hint adds tokens to the one search that carries it"
    )
    assert not license_notice.marker_path().exists(), (
        "the run wrote the machine's once-per-machine marker"
    )


def test_a_notice_that_did_not_appear_is_not_measured_never_zero(R, repo, monkeypatch):
    from jcodemunch_mcp import license_notice

    _served(R)
    monkeypatch.setattr(license_notice, "notice_for", lambda *a, **k: None)
    assert R.served_session_costs(repo)["license_notice_tokens"] is None


@pytest.mark.parametrize(
    "asked,floor,writing,ref_method,expected",
    [
        (
            None,
            True,
            False,
            None,
            "raw",
        ),  # a reference that names no method predates the served one
        (None, True, False, "served", "served"),
        ("served", True, False, None, None),  # refused: two ways of counting
        ("raw", True, False, "raw", "raw"),
        (None, True, True, None, "served"),  # --reference replaces it in the same run
        (None, False, False, None, "served"),
        ("raw", False, False, "served", "raw"),
    ],
)
def test_a_floor_run_counts_the_way_the_reference_did(
    R, asked, floor, writing, ref_method, expected
):
    reference = {"method": ref_method} if ref_method else {}
    assert R.resolve_method(asked, floor, writing, reference) == expected
