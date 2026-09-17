"""Executable stop rules for action-safety verdicts.

`confidence` is certainty language. It says how sure we are, which invites a
caller to go get surer. A stop rule says whether anything *could* make it
surer. We shipped the first and not the second.

Motivated by arXiv 2608.01347 (Weinberger and Hozez, "Same Task, Different
Work: Prompt-Induced Waste in Coding Agents"), which measures two distinct
waste carriers in coding agents. Branch tournaments are token-borne;
verification loops are tool-borne. Runs at the highest observed
redundant-verification level cost 18x the clean-run median, execute 2.5x the
tool calls, and take 3x the wall-clock, with no success gradient. Their
prescription is to replace certainty language with an executable stop rule.

⚠ `terminal` means FINAL, not SAFE. `external_uses_blocking` is terminal:
we found importers, and no further jcodemunch call unfinds them. It says
nothing about whether deleting is a good idea.

⚠⚠ On any uncertainty `terminal` is False. Telling an agent to stop checking
before a destructive delete is a worse error than the redundancy this removes,
so the asymmetry is deliberate and matches `_is_first_ever_install`, which
returns False whenever it cannot tell.

`already_consulted` is deliberately NOT in this payload. It is static per tool
and byte-identical on every call, so it belongs in the cached tool description
rather than being re-transmitted per response. That is the same fixed-prefix
versus per-turn split the paper measures in its Appendix B.1. The names live in
ALREADY_CONSULTED below and are bound to real call sites by
tests/test_stop_rule.py, because a list of tools we claim to have consulted is
a lie with a ratchet's confidence if it drifts.
"""
from __future__ import annotations

from typing import Optional

# Verdicts asserting a blocker was FOUND. Positive evidence: more looking can
# add blockers, never remove the one already in hand, so the answer is final
# regardless of which channels were enabled.
_HARD_BLOCKER = {
    "check_delete_safe": frozenset({
        "entry_point",
        "external_uses_blocking",
        "cross_repo_blocking",
        "internal_uses_blocking",
        "runtime_observed",
        "scip_referenced",
    }),
    "check_edit_safe": frozenset({
        "signature_impact",
        "complexity_risk",
        "runtime_critical",
    }),
}

# Verdicts asserting an upper BOUND on usage. Every one of these is an absence
# claim underneath ("no callers", "only tests", "no test coverage"), so a
# disabled or empty evidence channel can overturn them. Terminal only when no
# channel gap remains.
_BOUNDED = {
    "check_delete_safe": frozenset({
        "safe_to_delete",
        "internal_only",
        "test_coverage_only",
        # (#566) The corpus could not back an absence claim. It belongs here
        # rather than among the hard blockers because nothing was PROVEN to use
        # the symbol — re-indexing can still move it either way.
        "corpus_inadequate",
        # (#714) Nothing was proven to use the symbol and nothing could be:
        # the name never reaches a call site. Reading the call sites or
        # ingesting runtime evidence can still move it either way, so it is
        # bounded rather than terminal.
        "name_not_searchable",
    }),
    "check_edit_safe": frozenset({
        "safe_to_edit",
        "untested",
    }),
}

# Tools whose evidence is already folded into each verdict. Named in the tool
# DESCRIPTION so an agent does not re-derive them, not in the response.
ALREADY_CONSULTED = {
    "check_delete_safe": ("find_dead_code", "find_importers", "check_references"),
    "check_edit_safe": ("find_importers", "check_references"),
}


def known_verdicts(tool: str) -> frozenset:
    """Every verdict this module classifies for `tool`."""
    return _HARD_BLOCKER.get(tool, frozenset()) | _BOUNDED.get(tool, frozenset())


def _channel_gaps(
    *,
    cross_repo: bool,
    include_runtime: bool,
    runtime_data_present: bool,
    corpus_gap: Optional[dict] = None,
) -> list[dict]:
    """Evidence channels that could still move a bound-style verdict."""
    gaps: list[dict] = []
    # (#566) The corpus itself is a channel. A stale index or a withheld file
    # is not a disabled probe -- it is the ground every other probe stands on --
    # so it is listed FIRST: re-indexing can change what the other channels see.
    if corpus_gap:
        gaps.append(corpus_gap)
    if not cross_repo:
        gaps.append({
            "action": "re-run with cross_repo=true",
            "why": "other indexed repos in the suite were not consulted on this call",
        })
    if not include_runtime:
        gaps.append({
            "action": "re-run with include_runtime=true",
            "why": "the runtime channel was disabled on this call",
        })
    elif not runtime_data_present:
        gaps.append({
            "action": "import-trace",
            "why": (
                "no runtime traces have been ingested for this repo, so production "
                "traffic was never consulted"
            ),
        })
    return gaps


def build_stop_rule(
    tool: str,
    verdict: Optional[str],
    *,
    cross_repo: bool,
    include_runtime: bool,
    runtime_data_present: bool,
    corpus_gap: Optional[dict] = None,
) -> dict:
    """Return the ``stop_rule`` block for one verdict.

    ``terminal`` is True only when no further jcodemunch call against the
    current index changes this verdict. An unrecognised verdict is never
    terminal: a value this module has not classified is exactly the case where
    it does not know, and not knowing resolves to "keep checking".
    """
    if verdict in _HARD_BLOCKER.get(tool, frozenset()):
        return {"terminal": True, "would_change_verdict": []}

    gaps = _channel_gaps(
        cross_repo=cross_repo,
        include_runtime=include_runtime,
        runtime_data_present=runtime_data_present,
        corpus_gap=corpus_gap,
    )

    if verdict in _BOUNDED.get(tool, frozenset()):
        return {"terminal": not gaps, "would_change_verdict": gaps}

    # Unclassified verdict, or an error path that never set one.
    unknown = list(gaps)
    unknown.append({
        "action": "review manually",
        "why": f"verdict {verdict!r} is not classified by the stop-rule contract",
    })
    return {"terminal": False, "would_change_verdict": unknown}
