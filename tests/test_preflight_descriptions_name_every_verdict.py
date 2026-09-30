"""A preflight's tool description names every verdict it can return (LEDGER L-79).

`check_delete_safe` and `check_edit_safe` publish a "Verdict tiers:" list in
their descriptions. The tiers grew after the list was written: `corpus_inadequate`
(#566), `name_not_searchable` (#714), `scip_referenced` and
`dynamic_import_boundary` (L-70, L-75) were classified in `_stop_rule.py` and
never named, so a caller met verdicts the description said did not exist.

The property binds the description to `_stop_rule.known_verdicts`, which
`test_stop_rule.TestVerdictCoverage` already binds to the verdicts the tools
emit. So a tier added to a tool fails here until the description names it, and
a tier retired fails here until the description drops it.
"""
from __future__ import annotations

import re

import pytest

from jcodemunch_mcp.server import _build_tools_list
from jcodemunch_mcp.tools._stop_rule import ALREADY_CONSULTED, known_verdicts

_TIERS = re.compile(r"Verdict tiers:\s*([a-z_ /]+?)\.\s")


def _published_tiers(tool: str) -> set[str]:
    tools = _build_tools_list(profile_override="full", surface_override="full")
    desc = next(t.description for t in tools if t.name == tool)
    m = _TIERS.search(desc)
    assert m, f"{tool}'s description has no 'Verdict tiers: ... .' list"
    return {v.strip() for v in m.group(1).split("/") if v.strip()}


@pytest.mark.parametrize("tool", sorted(ALREADY_CONSULTED))
def test_the_description_names_every_classified_verdict(tool):
    missing = known_verdicts(tool) - _published_tiers(tool)
    assert not missing, f"{tool} can return {sorted(missing)} and its description never names them"


@pytest.mark.parametrize("tool", sorted(ALREADY_CONSULTED))
def test_the_description_names_no_verdict_the_tool_cannot_return(tool):
    stale = _published_tiers(tool) - known_verdicts(tool)
    assert not stale, f"{tool}'s description names {sorted(stale)}, which it never returns"
