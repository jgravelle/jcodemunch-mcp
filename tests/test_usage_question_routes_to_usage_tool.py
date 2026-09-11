"""The question "where is this name used" reaches the tool that answers it.

Why this exists (docs/competitive/FINDINGS.md CF-51, CF-63): our own competitive
adapter scored 0 on every reference-finding task because it asked
``find_references``, the IMPORT-GRAPH tool, a usage-site question, and a
single-file library has no importers of ``map``. The adapter did what every
steering surface of the product told it to: the tool description, the
``initialize`` instructions hint, the CLAUDE.md policy block ``init`` writes,
the PreToolUse steering hook and the Counter's ``route`` rule all paired
"where is X used" with ``find_references``. ``check_references`` (imports plus
every content match) and ``search_text`` answer that question.

Every assertion here is over the PROPERTY (a "used" question names a usage
tool), one per surface, plus a spelling scan over ``src/`` so a seventh surface
written next inherits the rule. The scan alone would be a guard against a
spelling; the per-surface assertions alone would miss a new surface.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import jcodemunch_mcp.server as server
from jcodemunch_mcp import counter
from jcodemunch_mcp.cli import policy
from jcodemunch_mcp.cli.hooks import steering

SRC = Path(server.__file__).resolve().parent

USAGE_TOOLS = {"check_references", "search_text"}
IMPORT_TOOL = "find_references"

# A "usage" question, by any spelling this project has used for it.
_USED_Q = re.compile(r"\b(used|usage|use of|every use|call sites?)\b", re.I)
_TOOL_NAME = re.compile(r"\b(find_references|check_references|search_text|find_importers)\b")


def _descriptions() -> dict[str, str]:
    return {t.name: t.description for t in server._build_tools_list()}


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]


# --- 1. the two descriptions -------------------------------------------------- #

def test_find_references_describes_the_import_graph_and_names_the_usage_route():
    desc = _descriptions()[IMPORT_TOOL]
    assert "import" in _first_sentence(desc).lower(), (
        "find_references' first sentence must say it answers over the import "
        "graph; a reader stops at the first sentence when the name already "
        "sounds like the answer"
    )
    assert USAGE_TOOLS & set(_TOOL_NAME.findall(desc)), (
        "find_references must name check_references or search_text as the "
        "route for a usage-site question, since its own name invites that "
        "question"
    )
    # It may not CLAIM the usage question anywhere: every sentence that talks
    # about "used"/"usage" must be the sentence that hands the question off.
    for sentence in re.split(r"(?<=[.!?])\s", desc):
        if _USED_Q.search(sentence):
            assert USAGE_TOOLS & set(_TOOL_NAME.findall(sentence)), (
                f"this sentence talks about usage without naming the usage "
                f"tool: {sentence!r}"
            )


def test_check_references_leads_with_where_an_identifier_is_used():
    desc = _descriptions()["check_references"]
    assert re.search(r"\bused\b", _first_sentence(desc), re.I), (
        "check_references' first sentence must say it answers where an "
        "identifier is USED; today it opens with the boolean shape and the "
        "reader with a usage question never reaches for it"
    )


def test_find_references_description_did_not_grow_in_tokens():
    """A core tool sits under schema.core_compact_ceiling with two tokens of
    room (CLAUDE.md, Tool-description quality). Measured on origin/main
    4dbc37bd with the tokenizer test_schema_budget.py uses."""
    tiktoken = pytest.importorskip("tiktoken")
    enc = tiktoken.get_encoding("cl100k_base")
    assert len(enc.encode(_descriptions()[IMPORT_TOOL])) <= 99


# --- 2. the instructions hint -------------------------------------------------- #

def test_instructions_hint_for_find_references_is_about_imports():
    hint = dict(server._INSTRUCTION_TOOLS_FULL)[IMPORT_TOOL]
    assert "import" in hint.lower()
    assert not re.search(r"\bevery use\b", hint, re.I), hint


# --- 3. the policy block init writes ------------------------------------------ #

def _bullets(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith("- ")]


def test_policy_block_routes_the_usage_question_to_a_usage_tool():
    block = policy._CLAUDE_MD_POLICY
    used = [b for b in _bullets(block) if _USED_Q.search(b) and _TOOL_NAME.search(b)]
    assert used, "the policy block has no bullet for the usage question at all"
    for b in used:
        named = set(_TOOL_NAME.findall(b))
        assert named & USAGE_TOOLS and IMPORT_TOOL not in named, b
    importers = [b for b in _bullets(block) if IMPORT_TOOL in b]
    assert importers and all("import" in b.lower() for b in importers), importers


def test_policy_block_survives_the_core_filter_with_the_import_tool_named():
    """On the core profile check_references is filtered out (standard tier);
    the surviving find_references bullet must still say imports, not usage."""
    filtered = policy._filter_policy_for_tools(policy._CLAUDE_MD_POLICY, set(server._TOOL_TIER_CORE))
    rows = [b for b in _bullets(filtered) if IMPORT_TOOL in b]
    assert rows, "core profile lost the find_references bullet"
    for b in rows:
        assert "import" in b.lower() and not _USED_Q.search(b), b


# --- 4. the PreToolUse steering hook ------------------------------------------ #

def test_steering_grep_route_pairs_used_with_a_usage_tool():
    route = steering._SEARCH_ROUTES["Grep"]
    for line in route.splitlines():
        named = set(_TOOL_NAME.findall(line))
        if _USED_Q.search(line):
            assert named & USAGE_TOOLS and IMPORT_TOOL not in named, line
        if IMPORT_TOOL in named:
            assert "import" in line.lower(), line


# --- 5. the Counter's route ---------------------------------------------------- #

def _route(task: str) -> list[str]:
    names = {t.name for t in server._build_tools_list()}
    return [r["action"] for r in counter.classify_intent(task, names)]


@pytest.mark.parametrize("task", [
    "where is parse_config used",
    "find every place parse_config is used",
    "what references parse_config",
])
def test_route_sends_a_usage_question_to_check_references_first(task):
    ranked = _route(task)
    assert ranked and ranked[0] == "check_references", (task, ranked)


@pytest.mark.parametrize("task", [
    "who imports parse_config",
    "which files import parse_config",
    "where is parse_config imported or re-exported",
])
def test_route_still_reaches_find_references_for_the_import_question(task):
    ranked = _route(task)
    assert IMPORT_TOOL in ranked, (task, ranked)


def test_route_menu_hint_for_find_references_says_imports():
    hints = {action: why for _, action, why in counter._INTENT_RULES}
    assert IMPORT_TOOL in hints, "find_references has no route rule left"
    assert "import" in hints[IMPORT_TOOL].lower(), hints[IMPORT_TOOL]


# --- 6. the ratchet over src/: any spelling, any future surface --------------- #

# A line (or the hint string it opens) that pairs a usage question with the
# import tool and names no usage tool beside it.
def test_no_source_line_pairs_a_usage_question_with_find_references_alone():
    offenders = []
    for path in SRC.rglob("*.py"):
        for no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if IMPORT_TOOL in line and _USED_Q.search(line):
                if not (USAGE_TOOLS & set(_TOOL_NAME.findall(line))):
                    offenders.append(f"{path.relative_to(SRC)}:{no}: {line.strip()[:120]}")
    assert not offenders, "\n".join(offenders)
