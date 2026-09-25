"""The installed agent policy grants absence only on a scan that proved it (#719).

The policy told every installed agent that `negative_evidence.verdict:
"no_implementation_found"` alone proves absence, and to stop searching. That
verdict and the scan's state are two different fields, and one response can
carry both: a stale or rewritten index yields `no_implementation_found` WITH
`_meta.verdict.state: "degraded"`. Written as two sibling bullets about "a
`verdict`", the degraded one reads as unreachable once the first has fired, so
the agent reported a gap a re-index would have filled and was told not to look
again. It is the conflation #711 removed from `plan_turn` and
`get_session_snapshot`, in prose.

⚠⚠ Two carriers, because the shipped default `meta_fields: []` strips
`_meta.verdict`: on a default install the only surviving sign is
`_meta.absence_evidence.citable` (#377 item 10, `test_v1_108_184.py`). A rule
naming only the state would be unreadable on most installs.

⚠ The assertion is about the RULE, not the token: a scan for
`no_implementation_found` fires on the corrected text too. What is checked is
that every block telling the agent to stop re-searching also names the proof it
must see first, and says what to do when the scan did not prove it.
"""

import pathlib
import re

import pytest

from jcodemunch_mcp.cli import policy
from jcodemunch_mcp.retrieval import verdict

_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Every copy of the policy an agent reads. AGENTS.md is this repo's own copy
#: of the full surface; a fix to one copy is a fix to one copy.
_SURFACES = {
    "full": policy._CLAUDE_MD_POLICY,
    "counter": policy._CLAUDE_MD_POLICY_COUNTER,
    "AGENTS.md": (_ROOT / "AGENTS.md").read_text(encoding="utf-8"),
}

_STOP_SEARCHING = re.compile(r"(?i)\bnot\b[^.\n]*\bre-?search")


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
    return [g for g in _bullet_groups(text) if _STOP_SEARCHING.search(g)]


@pytest.mark.parametrize("surface", sorted(_SURFACES))
def test_every_grant_of_absence_names_the_proof_it_needs(surface):
    grants = _grants(_SURFACES[surface])
    assert grants, f"{surface}: no block tells the agent to stop re-searching; the scan found nothing to check"
    for g in grants:
        missing = [
            need
            for need in ("_meta.verdict.state", f"`{verdict.STATE_ABSENT}`", "_meta.absence_evidence.citable")
            if need not in g
        ]
        assert not missing, (
            f"{surface}: this block tells the agent to stop searching and never names "
            f"{missing}, so a degraded scan's `no_implementation_found` reads as proof (#719):\n{g}"
        )


@pytest.mark.parametrize("surface", sorted(_SURFACES))
def test_an_unproven_scan_is_worth_running_again(surface):
    """The opposite advice, for the case the old text could not distinguish."""
    for g in _grants(_SURFACES[surface]):
        assert re.search(r"(?i)re-?index", g), (
            f"{surface}: the block never says a scan that did not prove absence "
            f"is worth re-running after a re-index:\n{g}"
        )


def test_the_names_the_policy_cites_are_the_ones_the_server_emits():
    """A renamed field would leave the rule pointing at nothing."""
    server = (_ROOT / "src" / "jcodemunch_mcp" / "server.py").read_text(encoding="utf-8")
    assert verdict.STATE_ABSENT == "absent"
    assert '["absence_evidence"] = _absence_carrier' in server
    assert '{"ref": _ref, "citable": True}' in server
