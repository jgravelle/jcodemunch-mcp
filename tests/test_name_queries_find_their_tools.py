"""A query that names a tool finds that tool (R&D item 185).

Agents searching for a deferred tool most often type its NAME, not a
question: a bare name, the host's prefixed name (`mcp__<server>__<tool>`),
or several names at once. Two public Codex issues quote exactly these and
the miss that followed: a five-name query that dropped one of the five, and
a prefixed name that returned nothing while the bare name worked. Smaller
models read such a miss as "not installed" and stop.

`counter.search_catalog` (the `menu` front door and `route`'s fallback)
scored a query word by word over names and descriptions. A tool's exact name
got no more credit than its words scattered across other tools, so naming
the six core tools together dropped `resolve_repo` and `search_symbols` from
the first eight rows, and the `mcp__jcodemunch__` prefix put
`jcodemunch_guide` first on its own word "jcodemunch".

The question-shaped queries live in `test_question_queries_find_core_tools.py`
and `test_question_queries_find_analysis_tools.py`; that the server
instructions name the core tools early is `test_instructions_first_512.py`.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp import counter, server

# Codex returns eight rows for a keyword tool search: R&D item 183, cited in
# the HQ R&D note 2026-10-31-how-agents-phrase-tool-searches.md.
HOST_ROWS = 8
PREFIX = "mcp__jcodemunch__"


@pytest.fixture(scope="module")
def rows():
    return server._catalog_rows()


def _ranked(rows, query, limit):
    return [r["action"] for r in counter.search_catalog(rows, query, limit)]


def _searchable(rows):
    return [r["action"] for r in counter.search_catalog(rows, None, len(rows))]


def _core():
    return list(server._instruction_tool_names("full"))


def test_every_searchable_tool_ranks_first_for_its_bare_name(rows):
    misses = {n: _ranked(rows, n, 3) for n in _searchable(rows)}
    assert {n: r for n, r in misses.items() if r[:1] != [n]} == {}


def test_every_searchable_tool_ranks_first_for_its_prefixed_name(rows):
    misses = {n: _ranked(rows, PREFIX + n, 3) for n in _searchable(rows)}
    assert {n: r for n, r in misses.items() if r[:1] != [n]} == {}


@pytest.mark.parametrize(
    "shape",
    [
        "{names}",
        "{prefixed}",
        "{names} jcodemunch",
        "{names}, jcodemunch-mcp",
        # Review round 2: punctuated spellings fell back to word scoring.
        "select:{prefixed_csv}",
        "select:{csv}",
        "{backticked}",
        "{quoted_csv}",
        "{names}.",
        "{and_list}",
        "{upper}",
    ],
)
def test_naming_the_core_tools_together_finds_every_one(rows, shape):
    core = _core()
    query = shape.format(
        names=" ".join(core),
        prefixed=" ".join(PREFIX + n for n in core),
        prefixed_csv=",".join(PREFIX + n for n in core),
        csv=",".join(core),
        backticked=" ".join(f"`{n}`" for n in core),
        quoted_csv=", ".join(f'"{n}"' for n in core),
        and_list=", ".join(core[:-1]) + " and " + core[-1],
        upper=" ".join(n.title() for n in core),
    )
    ranked = _ranked(rows, query, HOST_ROWS)
    assert [n for n in core if n not in ranked] == [], (query, ranked)


def test_named_tools_come_back_in_the_order_named(rows):
    core = _core()
    named = list(reversed(core))
    assert _ranked(rows, " ".join(named), len(named)) == named


def test_a_question_names_no_tool_and_ranks_as_before(rows):
    """The name pin fires only on a term that IS a tool name."""
    ranked = _ranked(rows, "who calls this function", 3)
    assert "get_call_hierarchy" in ranked, ranked


def test_the_server_name_alone_finds_the_guide(rows):
    """Every tool carries the server name in a host index, so a server-name
    query is arbitrary there; ours answers it with the guide, which says
    where to start."""
    assert _ranked(rows, "jcodemunch", 1) == ["jcodemunch_guide"]


def test_the_core_list_is_the_published_one(rows):
    names = set(_searchable(rows))
    assert _core() and [n for n in _core() if n not in names] == []


def test_a_task_that_mentions_a_tool_is_not_pinned_to_it(rows):
    """Review round 1: `route` dispatches its first row, so a tool mentioned
    as context must not jump ahead of the tool the task wants."""
    task = "compare two radar payloads from get_repo_health"
    assert counter.named_rows(task, rows) == []
    assert _ranked(rows, task, 1) == ["diff_health_radar"]


def test_no_tool_summary_reads_as_name_shaped(rows):
    """Two summaries name another tool in passing ("find_references +
    search_text", "get_repo_health.radar"); prose is never a name query."""
    shaped = {
        r["action"]: [n["action"] for n in counter.named_rows(r["summary"], rows)]
        for r in rows
        if counter.named_rows(r["summary"], rows)
    }
    assert shaped == {}


@pytest.mark.parametrize(
    "query",
    [
        "get_repo_health get_file_outline get_symbol_source diff_health_radar",
        PREFIX + "index_repo",
        "search_symbols, jcodemunch-mcp",
        "compare two radar payloads from get_repo_health",
    ],
)
def test_the_counterfactual_reads_the_same_order_as_search_catalog(rows, query):
    """Review round 1: `explain_route` kept its own copy of the ordering and
    reported a rank `route` never produced."""
    from jcodemunch_mcp.investigator.retrieval_counterfactual import explain_route

    top = _ranked(rows, query, 3)
    for target in {*top, "diff_health_radar", "search_symbols"}:
        r = explain_route(query, target, catalog_rows=rows, limit=3)
        if "top" in r:
            assert r["top"] == top, (query, target, r)
        if target in top:
            assert (
                r.get("rank") == top.index(target) + 1
                or r["outcome"] == "rule_preempted"
            ), (query, target, r)


def test_a_named_target_cut_by_the_limit_is_not_called_a_scoring_loss(rows):
    """Review round 2: the experiment text said ranking work would help, when
    the order named and the limit cut the target."""
    from jcodemunch_mcp.investigator.retrieval_counterfactual import explain_route

    core = _core()
    r = explain_route(" ".join(core), core[-1], catalog_rows=rows, limit=3)
    assert r["outcome"] == "ranked_below_cutoff", r
    assert r["beaten_by"].get("named") is True
    assert "order named" in r["experiment"]


def test_an_unnamed_target_behind_named_tools_keeps_the_scoring_text(rows):
    """Review round 3: the order-named text fired for a target the query never
    named, which lost on score."""
    from jcodemunch_mcp.investigator.retrieval_counterfactual import explain_route

    r = explain_route(
        "search_symbols get_file_outline",
        "get_symbol_source",
        catalog_rows=rows,
        limit=2,
    )
    assert r["outcome"] == "ranked_below_cutoff", r
    assert "order named" not in r["experiment"]
