"""An agent's question finds the analysis tool that answers it, by name and description alone.

`tests/test_question_queries_find_core_tools.py` holds the core tools to this.
An audit of the other 87 found 22 whose descriptions opened with internal terms
("Composite preflight", "Task-aware single-call orchestrator", "Mine the
ranking telemetry ledger") and carried none of the words an agent types when
it needs them. A host that defers schemas searches names and descriptions, so
those tools could not be found by the question they answer.

The same two checks as the core file, over the live catalog:

- **Vocabulary.** Every content word of the question, as the Counter
  tokenizes it, appears in the target tool's description.
- **Rank.** The Counter's catalog search (`counter.search_catalog`) puts the
  tool in the top three. A host's search is its own; this is a proxy.

`set_tool_tier` and `announce_model` also open with internal terms and are
left as they are: they sit on the byte-pinned `counter` surface
(`tests/test_counter_surface_stability.py`), where any rewording is a
full-rate cache write for every user, and an agent reaches them through
`menu` or the server instructions rather than by searching.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp import counter, server

TOP = 3

QUESTIONS = [
    ("is it safe to delete this function", "check_delete_safe"),
    ("is it safe to edit this function", "check_edit_safe"),
    ("gather the context for a task", "assemble_task_context"),
    ("map of the repository", "get_repo_map"),
    ("brief me on what changed", "digest"),
    ("how much AI written code survived", "get_delivery_metrics"),
    ("what has already been ported", "get_parity_map"),
    ("where is every decorator", "get_decorator_census"),
    ("how modular is this codebase", "get_architecture_metrics"),
    ("which tools are slow", "analyze_perf"),
    ("did the embedding model change", "check_embedding_drift"),
    ("tune the search weights", "tune_weights"),
    ("why do searches keep missing", "suggest_corrections"),
    ("what are the real modules of this codebase", "get_tectonic_map"),
    ("which APIs do these repos share", "get_group_contracts"),
    ("how risky is this pull request", "get_pr_risk_profile"),
    ("where is the riskiest code", "get_hotspots"),
    ("how coupled is this module", "get_coupling_metrics"),
    ("summaries missing after indexing", "summarize_repo"),
    ("read lines of a file", "get_file_content"),
]


@pytest.fixture(scope="module")
def rows():
    return server._catalog_rows()


def _description(rows, tool):
    (row,) = [r for r in rows if r["action"] == tool]
    return row["_description"]


@pytest.mark.parametrize("question,tool", QUESTIONS)
def test_the_description_carries_every_word_of_the_question(rows, question, tool):
    words = counter._query_tokens(question)
    assert words, question
    have = set(counter._tokens(_description(rows, tool)))
    assert [w for w in words if w not in have] == [], (question, tool)


@pytest.mark.parametrize("question,tool", QUESTIONS)
def test_the_question_ranks_its_tool_near_the_top(rows, question, tool):
    ranked = [r["action"] for r in counter.search_catalog(rows, question, TOP)]
    assert tool in ranked, (question, tool, ranked)


def test_every_target_is_a_published_tool(rows):
    actions = {r["action"] for r in rows}
    assert [t for _, t in QUESTIONS if t not in actions] == []
