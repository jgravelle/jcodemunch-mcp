"""The token benchmark counts what the server serves an agent, not the tool function's dict.

Before 2026-11-08 `run_benchmark.measure_jmunch` called `search_symbols` and
`get_symbol_source` as Python functions and counted `json.dumps` of the return
value. No agent receives that: on a default install the dispatcher strips
`_meta` (`meta_fields: []`) and applies the compact encoding. On the pinned
express corpus one search counted 460 tokens that way and is served as 201.

The served method counts `server.call_tool`'s text on `config.DEFAULTS`, makes
each lookup stand alone (no steering hint, no already-delivered note carried in
from an earlier lookup), and keeps the raw method behind `--method raw`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "benchmarks" / "harness"


@pytest.fixture
def R(monkeypatch):
    pytest.importorskip("tiktoken")
    monkeypatch.syspath_prepend(str(HARNESS))
    sys.modules.pop("run_benchmark", None)
    import run_benchmark

    yield run_benchmark
    run_benchmark.MEASURE_METHOD = "served"
    run_benchmark.SESSION_COSTS = None


@pytest.fixture
def repo(tmp_path, monkeypatch):
    from jcodemunch_mcp import server
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.search_symbols import _result_cache

    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / ".idx"))
    monkeypatch.setenv("JCODEMUNCH_USE_AI_SUMMARIES", "false")
    saved = dict(server._steer_state)
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
    server._steer_state.update(saved)


def _served_total(R, repo: str, query: str) -> tuple[int, list[str]]:
    """The served workflow recomputed by hand: one search, then the top 3 fetches."""
    text = R._served_text(
        "search_symbols",
        {"repo": repo, "query": query, "max_results": 5, "detail_level": "standard"},
    )
    ids = [r["id"] for r in R._decode_served(text)["results"]][:3]
    total = R.count_tokens(text)
    for sid in ids:
        total += R.count_tokens(
            R._served_text("get_symbol_source", {"repo": repo, "symbol_id": sid})
        )
    return total, ids


def test_the_served_method_counts_the_text_the_server_serves(R, repo):
    R._use_default_install_config()
    R.MEASURE_METHOD = "served"
    got = R.measure_jmunch(repo, "register route")
    R._start_lookup_session()
    expected, ids = _served_total(R, repo, "register route")
    assert got["hits_fetched"] == len(ids) == 3
    assert got["tokens"] == expected


def test_the_raw_method_is_kept_and_counts_more(R, repo):
    R._use_default_install_config()
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

    R._use_default_install_config()
    R.MEASURE_METHOD = "served"
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

    R._use_default_install_config()
    R.MEASURE_METHOD = "served"
    first = R.measure_jmunch(repo, "register route")["tokens"]
    for _ in range(4):
        again = R.measure_jmunch(repo, "register route")["tokens"]
        assert not server._steer_state["nudged"], (
            "the steering hint fired inside a measured lookup"
        )
        assert again == first


def test_the_session_costs_are_measured_not_assumed(R, repo):
    R._use_default_install_config()
    costs = R.served_session_costs(repo)
    assert set(costs) == {"license_notice_tokens", "steering_hint_tokens"}
    assert costs["steering_hint_tokens"] > 0, (
        "a forced hint adds tokens to the one search that carries it"
    )
