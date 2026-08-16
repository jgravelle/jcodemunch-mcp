"""Ratchet on tool-description quality.

Scores the live `tools/list` surface against the rubric from arXiv:2602.14878 (see
benchmarks/description_smells/README.md) and fails when a description regresses on
the two components the suite has fully cleared: Purpose and Length/Completeness.

Deliberately NOT enforced here:
  * Unstated Limitation — 42 tools still score below threshold, and a chunk of those
    are cue-matching misses rather than real gaps. Enforcing it now would freeze the
    scanner's false positives into a gate.
  * Opaque Parameters — the rubric scores schema-documented parameters as 1/5 by its
    own wording, so this server fails it by construction while documenting every
    parameter. See the two frames in the README.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

SCORER = Path(__file__).resolve().parents[1] / "benchmarks" / "description_smells" / "score_descriptions.py"


def _load_scorer():
    spec = importlib.util.spec_from_file_location("_desc_scorer", SCORER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_desc_scorer"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not SCORER.is_file(), reason="scorer not present")
@pytest.mark.asyncio
async def test_no_unclear_purpose_or_underspecified_descriptions():
    from jcodemunch_mcp.server import list_tools

    scorer = _load_scorer()
    tools = [
        {"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema}
        for t in await list_tools()
    ]
    scored = [scorer.score_tool(t, "schema") for t in tools]

    unclear = [r["name"] for r in scored if r["purpose"] < 3]
    thin = [r["name"] for r in scored if r["length_completeness"] < 3]

    assert not unclear, (
        f"Unclear Purpose smell on {unclear}. A description must say what the tool "
        f"does and what it returns, in more than one clause."
    )
    assert not thin, (
        f"Underspecified smell on {thin}. Two substantive sentences minimum: what it "
        f"does, and one boundary or usage cue."
    )
