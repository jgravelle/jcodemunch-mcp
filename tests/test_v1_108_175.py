"""An ignored argument cannot back an absence claim (v1.108.175).

Found live while auditing #375. A `search_text` call passed `regex=true`; the
parameter is `is_regex`. Every tool reads arguments key-by-key, so the flag was
dropped in silence, the regex SOURCE TEXT was searched as a literal substring,
nothing matched, and the response reached `state: "absent"` with the note
"treat this as strong evidence the target is not present" plus a citable
`evidence_ref` -- over a corpus that plainly contained the target.

The rule mirrors the `rebuilding` gate: disclose on every state, refuse only the
absence CLAIM, and do the refusing by downgrading to `degraded` so the existing
"only `absent` proves absence" check needs no new rule.
"""

from __future__ import annotations

import json

import pytest

from jcodemunch_mcp.encoding.schema_driven import UNIVERSAL_META_JSON
from jcodemunch_mcp.tools._arg_contract import (
    apply_argument_contract,
    unrecognized_keys,
)


# --- which keys count as unrecognized -----------------------------------------

DECLARED = ("repo", "query", "is_regex")


def test_a_misspelled_parameter_is_reported():
    assert unrecognized_keys({"repo": "r", "regex": True}, DECLARED) == ["regex"]


def test_declared_parameters_are_silent():
    assert unrecognized_keys({"repo": "r", "is_regex": True}, DECLARED) == []


def test_protocol_meta_is_never_the_callers_mistake():
    """`_meta` is reserved by MCP (progress tokens ride there)."""
    assert unrecognized_keys({"repo": "r", "_meta": {"progressToken": 1}}, DECLARED) == []


def test_unknown_schema_accuses_nobody():
    """An absent declaration is not evidence that a key is wrong.

    Guessing here would manufacture a warning on every tool whose schema we
    failed to read.
    """
    assert unrecognized_keys({"anything": 1}, None) == []
    assert unrecognized_keys({"anything": 1}, ()) == []


def test_multiple_unknown_keys_are_sorted():
    assert unrecognized_keys(
        {"zeta": 1, "alpha": 2, "repo": "r"}, DECLARED
    ) == ["alpha", "zeta"]


# --- what the gate does to the response ---------------------------------------

def _absent_response():
    return {
        "result_count": 0,
        "results": [],
        "_meta": {
            "verdict": {
                "state": "absent",
                "note": "No match found after scanning the index.",
                "channels": {"index": "fresh"},
            }
        },
    }


def test_absence_claim_is_downgraded_not_kept():
    r = _absent_response()
    apply_argument_contract(r, ["regex"])
    assert r["_meta"]["verdict"]["state"] == "degraded"


def test_the_note_names_the_offending_argument():
    """A gate that degrades without saying why makes the agent guess."""
    r = _absent_response()
    apply_argument_contract(r, ["regex"])
    note = r["_meta"]["verdict"]["note"]
    assert "regex" in note
    assert "not the call that was requested" in note


def test_the_ignored_keys_are_disclosed_top_level():
    r = _absent_response()
    apply_argument_contract(r, ["regex"])
    assert r["_meta"]["ignored_arguments"] == ["regex"]


def test_note_reads_correctly_for_one_and_for_many():
    one = _absent_response()
    apply_argument_contract(one, ["regex"])
    assert "An argument this tool does not accept was ignored" in one["_meta"]["verdict"]["note"]

    many = _absent_response()
    apply_argument_contract(many, ["alpha", "zeta"])
    assert "2 arguments this tool does not accept were ignored" in many["_meta"]["verdict"]["note"]


def test_a_confident_result_is_disclosed_but_not_downgraded():
    """DISCLOSE on every state, refuse only the absence claim.

    The matches an `ok` scan returned were really in the index; they are still
    the best available answer. Only the claim that nothing exists is unfounded.
    """
    r = {"result_count": 3, "_meta": {"verdict": {"state": "ok", "note": "Confident."}}}
    apply_argument_contract(r, ["regex"])
    assert r["_meta"]["verdict"]["state"] == "ok"
    assert r["_meta"]["verdict"]["note"] == "Confident."
    assert r["_meta"]["ignored_arguments"] == ["regex"]


def test_low_confidence_and_degraded_are_left_alone():
    for state in ("low_confidence", "degraded"):
        r = {"_meta": {"verdict": {"state": state, "note": "n"}}}
        apply_argument_contract(r, ["regex"])
        assert r["_meta"]["verdict"]["state"] == state


def test_clean_calls_add_no_key_and_no_tokens():
    r = {"result_count": 0, "_meta": {"verdict": {"state": "absent"}}}
    apply_argument_contract(r, [])
    assert "ignored_arguments" not in r["_meta"]
    assert r["_meta"]["verdict"]["state"] == "absent"


def test_a_response_without_a_verdict_still_discloses():
    r = {"result": []}
    apply_argument_contract(r, ["bogus"])
    assert r["_meta"]["ignored_arguments"] == ["bogus"]


def test_non_dict_results_are_survivable():
    """Some tools return lists; the gate must never raise into a live call."""
    apply_argument_contract(["a"], ["bogus"])
    apply_argument_contract(None, ["bogus"])


# --- the absence-refusal rule falls out of the existing check ------------------

def test_degraded_scan_cannot_be_cited_as_absence_evidence():
    """No new refusal rule: `absence_refusal` already rejects non-absent states."""
    from jcodemunch_mcp.handoff import note_absence

    v = _absent_response()["_meta"]["verdict"]
    apply_argument_contract({"_meta": {"verdict": v}}, ["regex"])
    ref, why = note_absence("search_text", "local/x", "needle", v)
    assert ref is None
    # #872: the refusal is named (it read the generic "only 'absent' can prove
    # absence" before), like every other gate `absence_refusal` names first.
    assert why and "ignored (regex)" in why


def test_a_clean_absent_scan_still_mints_a_ref():
    """Non-vacuity: the refusal above is caused by the gate, not by the fixture."""
    from jcodemunch_mcp.handoff import note_absence

    v = _absent_response()["_meta"]["verdict"]
    ref, why = note_absence("search_text", "local/x", "needle-clean", v)
    assert why is None
    assert ref and ref.startswith("absent:")


# --- the disclosure must survive MUNCH compaction -----------------------------

def test_ignored_arguments_is_a_universal_meta_blob():
    """v1.108.169's bug class: a structured `_meta` value off the allowlist is
    silently dropped by compaction, so the field that says "don't trust this"
    is the one that disappears."""
    assert "ignored_arguments" in UNIVERSAL_META_JSON
    assert "verdict" in UNIVERSAL_META_JSON


def test_round_trips_through_a_schema_that_never_declared_it():
    """The point of the universal set: search_text's own _META_JSON lists only
    `verdict`, and the key must survive anyway."""
    from jcodemunch_mcp.encoding.schemas import search_text as st_schema

    assert "ignored_arguments" not in getattr(st_schema, "_META_JSON", ())
    payload = st_schema.encode(
        "search_text",
        {
            "result_count": 0,
            "results": [],
            "_meta": {
                "timing_ms": 1.0,
                "ignored_arguments": ["regex"],
                "verdict": {"state": "degraded", "note": "x"},
            },
        },
    )
    payload = payload[0] if isinstance(payload, tuple) else payload
    decoded = st_schema.decode(payload)
    assert decoded["_meta"]["ignored_arguments"] == ["regex"]
    assert decoded["_meta"]["verdict"]["state"] == "degraded"


# --- end to end, against the real dispatcher ----------------------------------

def _content(res):
    """call_tool returns a plain content list, or CallToolResult on isError."""
    from mcp.types import CallToolResult

    return res.content if isinstance(res, CallToolResult) else res


@pytest.fixture
def dispatch_repo(small_index, monkeypatch):
    """Point the dispatcher's default store at the fixture's index.

    call_tool resolves storage from the environment, so a fixture store is
    invisible to it without this.
    """
    monkeypatch.setenv("CODE_INDEX_PATH", small_index["store"])
    return small_index


@pytest.mark.asyncio
async def test_the_original_defect_no_longer_proves_absence(dispatch_repo):
    """The exact call that fooled a maintainer: `regex` instead of `is_regex`."""
    from jcodemunch_mcp import server

    args = {
        "repo": dispatch_repo["repo"],
        "query": r"def \w+|class \w+",
        "regex": True,
        "format": "json",
    }
    out = await server.call_tool("search_text", args)
    body = json.loads(_content(out)[0].text)
    meta = body.get("_meta", {})
    assert meta.get("ignored_arguments") == ["regex"]
    verdict = meta.get("verdict") or {}
    assert verdict.get("state") != "absent"
    assert "evidence_ref" not in verdict


@pytest.mark.asyncio
async def test_the_correct_call_is_byte_identical_to_before(dispatch_repo):
    """Non-vacuity + zero blast radius: a well-formed call gains no key."""
    from jcodemunch_mcp import server

    args = {
        "repo": dispatch_repo["repo"],
        "query": r"def \w+|class \w+",
        "is_regex": True,
        "format": "json",
    }
    out = await server.call_tool("search_text", args)
    body = json.loads(_content(out)[0].text)
    assert "ignored_arguments" not in body.get("_meta", {})


def test_declared_keys_come_from_the_published_catalog():
    """The contract must be the schema the agent was shown, not a second list
    that can drift away from it."""
    from jcodemunch_mcp import server

    declared = server._declared_arg_keys("search_text")
    assert declared is not None
    assert "is_regex" in declared and "regex" not in declared
    assert server._declared_arg_keys("no_such_tool") is None
