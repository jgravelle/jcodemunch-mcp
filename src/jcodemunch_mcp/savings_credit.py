"""Which tools may earn a savings credit (L-180). A leaf: stdlib only.

Both savings models read this: the ``receipt`` CLI (``cli/receipt.py``) and
the live meter (``storage/token_tracker.record_savings``). Each model used to
decide per tool on its own, which is how a text search came to be credited in
both for work an agent would not have done.

A TEXT-SEARCH-STYLE tool is one whose answer IS the matching lines, which an
agent with Grep would get just as well. Its counterfactual is a Grep that
returns the same lines, so the honest credit is zero, and the ruling (J,
2026-10-17) is zero until a measurement supports another number. The receipt
had credited ``search_text`` 11x its answer, citing a symbol-retrieval
benchmark that never measured a text search; the meter credited the whole size
of every file that held a match.

⚠⚠ **A tool is classified by its ANSWER, never by its name or by a word in its
description.** ``search_ast`` scans text in some modes and is not here: most of
its queries are structural and Grep cannot express them. A description that
says "regex" or "substring" usually means a name FILTER, not a file scan. The
marker pattern below only decides which tools must be DECIDED, so a new tool
that reads like a text search cannot pick up the receipt's default multiplier
unreviewed (``tests/test_text_search_no_credit.py``).
"""

from __future__ import annotations

import re

#: Tools whose answer is the matching lines. Zero credit in both models.
TEXT_SEARCH_STYLE_TOOLS: dict[str, str] = {
    "search_text": (
        "the answer is the lines a regex or substring scan matched, with "
        "optional context lines; Grep returns the same lines"
    ),
    "check_references": (
        "by default the answer is the files whose imports name an identifier "
        "plus the lines that mention it, comments and strings included; Grep "
        "for the identifier finds both (its import-only mode, "
        "search_content=false, is ledger row L-216)"
    ),
}

#: Tools the marker pattern flags, or that a reader would suspect, decided NOT
#: text-search-style. Their credit is unchanged by L-180, which is not a claim
#: that their multiplier was measured.
NOT_TEXT_SEARCH_STYLE: dict[str, str] = {
    "search_ast": (
        "most queries are structural (empty_catch, nesting depth, god_function) "
        "that Grep cannot express, and each match is attributed to its "
        "enclosing symbol; its todo_fixme, comment: and string: modes are "
        "grep-like and are ledger row L-216"
    ),
    "find_references": (
        "answers from the resolved import graph (re-exports, dbt refs, "
        "resolved specifiers), not from a scan of file text"
    ),
    "search_columns": (
        "returns column metadata that providers emit into the index (dbt "
        "manifests, catalogs), not lines of files"
    ),
    "winnow_symbols": (
        "the regex filters symbol names inside a multi-signal index query; "
        "the answer is ranked symbols, not lines"
    ),
    "get_decorator_census": (
        "the substring filters normalised decorator names; the answer is "
        "counts aggregated from the index"
    ),
    "find_hot_paths": (
        "the substring filters symbol names; the answer is runtime trace "
        "counts and latencies"
    ),
    "list_repos": (
        "the answer is the list of indexed repositories; its description "
        "mentions grep only to say what it is not"
    ),
    "announce_model": (
        "the substring is a model id match for tool tiering; it searches "
        "no code at all"
    ),
}

#: Words in a tool description that make the tool a candidate. A candidate
#: must appear in one of the two tables above.
TEXT_SEARCH_MARKERS = re.compile(
    r"grep|regex|substring|literal|full-text|text search|content match|"
    r"matching lines|file content|mentions",
    re.IGNORECASE,
)


def earns_credit(tool_name: str | None) -> bool:
    """False for a text-search-style tool; True otherwise (or when unnamed)."""
    return tool_name not in TEXT_SEARCH_STYLE_TOOLS
