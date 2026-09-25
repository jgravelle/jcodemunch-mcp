"""The installed agent prose grants absence only on a scan that proved it (#719).

The policy told every installed agent that `negative_evidence.verdict:
"no_implementation_found"` alone proves absence, and to stop searching. It
doesn't: the product refuses absence on a stale or truncated index while the
verdict still reads `no_implementation_found` and the state still reads
`absent` (`handoff.absence_refusal`). The agent reported a gap a re-index would
have filled and was told not to look again. It is the conflation #711 removed
from `plan_turn` and `get_session_snapshot`, in prose.

⚠⚠ The proof is CITABILITY, never the state. Review round 1 of the first fix
probed a stale index through the dispatcher and got `state: absent`,
`absence_citable: false` -- a rule keyed on `state` granted exactly the #719
case. What the product counts as proof is an `absent:` evidence token:
`_meta.verdict.evidence_ref` where the verdict is shown, and
`_meta.absence_evidence.citable` where the shipped default `meta_fields: []`
strips it (#377 item 10). On that default the encoded body carries no
`negative_evidence` either, so the citable carrier must be enough by itself.

⚠ The assertion is about the RULE, not the token: the corrected text still
says `no_implementation_found`. A block is a grant if it names that verdict or
tells the agent to stop re-searching, and every grant must name both citable
carriers and say the verdict alone is not proof.
"""

import pathlib
import re

import pytest

from jcodemunch_mcp.cli import policy
from jcodemunch_mcp.cli.skills import _build_skill_content
from jcodemunch_mcp.handoff import ABSENCE_REF_PREFIX

_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Every copy an agent reads. AGENTS.md is this repo's own copy of the full
#: surface; the skill is what `init` installs beside the policy. A fix to one
#: copy is a fix to one copy -- round 1 found the skill still granting.
_SURFACES = {
    "full": policy._CLAUDE_MD_POLICY,
    "counter": policy._CLAUDE_MD_POLICY_COUNTER,
    "AGENTS.md": (_ROOT / "AGENTS.md").read_text(encoding="utf-8"),
    "skill": _build_skill_content(),
}

#: Several ways of saying "stop looking", not one verb.
_STOP_SEARCHING = re.compile(
    r"(?i)(\bnot\b|\bnever\b|don't|avoid)[^.\n]*"
    r"(re-?search|search(ing)?\s+again|different\s+(keywords|terms|wording))"
)


def _bullet_groups(text: str) -> list[str]:
    """A top-level `- ` bullet with every indented line under it."""
    groups, cur = [], None
    for line in text.splitlines():
        if line.startswith("- "):
            cur = [line]
            groups.append(cur)
        elif cur is not None and line.startswith((" ", "\t")) and line.strip():
            cur.append(line)
        else:
            cur = None
    return ["\n".join(g) for g in groups]


def _grants(text: str) -> list[str]:
    return [
        g
        for g in _bullet_groups(text)
        if "no_implementation_found" in g or _STOP_SEARCHING.search(g)
    ]


@pytest.mark.parametrize("surface", sorted(_SURFACES))
def test_every_grant_of_absence_names_the_proof_it_needs(surface):
    grants = _grants(_SURFACES[surface])
    assert grants, f"{surface}: no block grants absence; the scan found nothing to check"
    for g in grants:
        missing = [
            need
            for need in ("_meta.verdict.evidence_ref", "`absent:`", "_meta.absence_evidence.citable")
            if need not in g
        ]
        assert not missing, (
            f"{surface}: this block grants absence and never names the citable "
            f"carrier(s) {missing}, so a refused scan's verdict reads as proof (#719):\n{g}"
        )
        assert "not proof" in g, (
            f"{surface}: this block never says the verdict or the state alone is "
            f"not proof, which is the conflation #719 removed:\n{g}"
        )


@pytest.mark.parametrize("surface", sorted(_SURFACES))
def test_an_unproven_scan_is_worth_running_again(surface):
    """The opposite advice, for the case the old text could not distinguish."""
    for g in _grants(_SURFACES[surface]):
        assert re.search(r"(?i)re-?index", g), (
            f"{surface}: the block never says a scan that did not prove absence "
            f"is worth re-running after a re-index:\n{g}"
        )


def test_the_detector_sees_every_old_spelling():
    """Non-vacuity: each pre-fix grant, planted, is found and fails the rule."""
    planted = [
        '- If `search_symbols` returns `negative_evidence` with `verdict: "no_implementation_found"`:\n'
        "  - Do NOT re-search with different terms hoping to find it",
        "- A `verdict` of `no_implementation_found` is evidence of absence. Report the gap; do not re-search with different wording.",
        '- Searching with different keywords after `negative_evidence: "no_implementation_found"`. Report the gap.',
        "- Absence is proven when `_meta.verdict.state` is `absent`. Never search again.",
    ]
    for block in planted:
        (g,) = _grants(block)
        assert "_meta.verdict.evidence_ref" not in g or "not proof" not in g


def test_the_names_the_policy_cites_are_the_ones_the_server_emits():
    """A renamed carrier would leave the rule pointing at nothing."""
    server = (_ROOT / "src" / "jcodemunch_mcp" / "server.py").read_text(encoding="utf-8")
    assert '_v["evidence_ref"] = _ref' in server
    assert '["absence_evidence"] = _absence_carrier' in server
    assert '{"ref": _ref, "citable": True}' in server
    assert '_v["absence_blocked_by"] = _why' in server
    assert ABSENCE_REF_PREFIX == "absent:"
