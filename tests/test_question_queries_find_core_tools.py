"""A question an agent would ask finds the tool that answers it, by name and description alone.

Hosts that defer MCP tool schemas (Claude Code, Codex) show the model bare tool
names and let it search for the rest, and the search reads each tool's name and
description. Questions worded the way an agent asks them ("find where this
function is defined", "who calls this function", "show me the code of this
function") shared no word with the descriptions of `search_symbols`,
`get_call_hierarchy` and `get_symbol_source`, which said "symbols matching a
query", "incoming callers and outgoing callees" and "full source of one
symbol". A host search ranked other tools first, `find_references` and
`find_importers` among them.

Two checks, over the live catalog:

- **Vocabulary.** Every content word of the question, as the Counter
  tokenizes it, appears in the target tool's description. A keyword search can
  only match a word that is there.
- **Rank.** The Counter's own catalog search (`counter.search_catalog`, IDF over
  name and description) puts the tool in the top three. It is a proxy: a
  host's search is its own. One question is held to a weaker rank: in
  "find where this function is defined", the ranker's name boost on "find"
  puts six of the seven `find_*` tools ahead of any description match. There
  the target must be the first tool whose name does not start with `find_`.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp import counter, server

TOP = 3

QUESTIONS = [
    ("find where this function is defined", "search_symbols"),
    ("where is this class defined", "search_symbols"),
    ("locate the definition of a method", "search_symbols"),
    ("read the source code of a function", "get_symbol_source"),
    ("show me the code of this function", "get_symbol_source"),
    ("who calls this function", "get_call_hierarchy"),
    ("what calls this method", "get_call_hierarchy"),
    ("where is this function used", "check_references"),
]

# A name match on a word several tools share outranks every description match.
VOCABULARY_ONLY = {"find where this function is defined"}


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


@pytest.mark.parametrize("question,tool", [q for q in QUESTIONS if q[0] not in VOCABULARY_ONLY])
def test_the_question_ranks_its_tool_near_the_top(rows, question, tool):
    ranked = [r["action"] for r in counter.search_catalog(rows, question, TOP)]
    assert tool in ranked, (question, tool, ranked)


@pytest.mark.parametrize("question,tool", [q for q in QUESTIONS if q[0] in VOCABULARY_ONLY])
def test_the_find_question_ranks_its_tool_first_after_the_find_tools(rows, question, tool):
    ranked = [r["action"] for r in counter.search_catalog(rows, question, 20)]
    after_find = [a for a in ranked if not a.startswith("find_")]
    assert after_find[:1] == [tool], (question, tool, ranked)


def test_every_target_is_a_published_tool(rows):
    """Non-vacuity: a renamed tool cannot pass by being absent from both sides."""
    names = {r["action"] for r in rows}
    assert {tool for _, tool in QUESTIONS} <= names
    assert VOCABULARY_ONLY <= {q for q, _ in QUESTIONS}
