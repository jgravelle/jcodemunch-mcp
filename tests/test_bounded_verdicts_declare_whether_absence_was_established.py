"""Every bounded verdict declares whether its absence was established (LEDGER L-82).

`_stop_rule._BOUNDED` held every absence-claim verdict and `_UNSETTLED` the
hand-kept subset whose meaning is that the absence could NOT be established.
Those must never be terminal (L-80). A fifth verdict of that kind could be
added to `_BOUNDED` alone and, with every channel open, read `terminal: True`
-- stop checking before a delete on a claim the tool says proves nothing.
`test_unsettled_verdicts_are_never_terminal.py` pinned the four it knew and
could not see a fifth.

The property: `_BOUNDED` is derived from two declared sets, so no verdict is
bounded without saying which it is, and each unsettled verdict names the gap
argument that carries its cause.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from jcodemunch_mcp.tools import _stop_rule

_OPEN = dict(cross_repo=True, include_runtime=True, runtime_data_present=True)
_SRC = Path(inspect.getsourcefile(_stop_rule)).read_text(encoding="utf-8")


def test_bounded_is_exactly_established_plus_unsettled_and_they_are_disjoint():
    tools = set(_stop_rule._BOUNDED)
    assert tools == set(_stop_rule._ESTABLISHED) | set(_stop_rule._UNSETTLED)
    for tool in tools:
        est = set(_stop_rule._ESTABLISHED.get(tool, ()))
        uns = set(_stop_rule._UNSETTLED.get(tool, ()))
        assert not est & uns, (tool, sorted(est & uns))
        assert set(_stop_rule._BOUNDED[tool]) == est | uns, tool


def test_bounded_is_not_a_hand_written_literal():
    """A literal `_BOUNDED` is where a fifth verdict can be added without choosing."""
    for node in ast.parse(_SRC).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_BOUNDED" for t in node.targets
        ):
            assert not isinstance(node.value, ast.Dict), (
                "_BOUNDED must be derived from _ESTABLISHED and _UNSETTLED"
            )
            return
    pytest.fail("_BOUNDED is not assigned at module level")


def _gap_kwargs() -> set[str]:
    sig = inspect.signature(_stop_rule.build_stop_rule)
    return {n for n in sig.parameters if n.endswith("_gap")}


@pytest.mark.parametrize(
    "tool,verdict",
    sorted((t, v) for t, vs in _stop_rule._UNSETTLED.items() for v in vs),
)
def test_each_unsettled_verdict_names_the_gap_that_carries_its_cause(tool, verdict):
    kwarg = _stop_rule._UNSETTLED[tool][verdict]
    assert kwarg in _gap_kwargs(), (tool, verdict, kwarg)
    out = _stop_rule.build_stop_rule(tool, verdict, **_OPEN)
    assert out["terminal"] is False
    assert any(kwarg in g["why"] for g in out["would_change_verdict"]), out


# The spec, stated independently of the module: which argument each producer
# passes with each unsettled verdict (`check_delete_safe` passes corpus_gap,
# dynamic_gap and name_gap; `check_edit_safe` passes dynamic_gap). Read off the
# module instead, the check below is circular, and a wrong mapping passed it.
_CAUSE = {
    ("check_delete_safe", "corpus_inadequate"): "corpus_gap",
    ("check_delete_safe", "name_not_searchable"): "name_gap",
    ("check_delete_safe", "dynamic_import_boundary"): "dynamic_gap",
    ("check_edit_safe", "dynamic_import_boundary"): "dynamic_gap",
}


def test_every_unsettled_verdict_has_a_stated_cause():
    declared = {(t, v) for t, vs in _stop_rule._UNSETTLED.items() for v in vs}
    assert declared == set(_CAUSE), sorted(declared ^ set(_CAUSE))


@pytest.mark.parametrize("tool,verdict", sorted(_CAUSE))
def test_passing_the_named_gap_and_only_it_settles_the_fallback(tool, verdict):
    """Review: a wrong mapping (`name_not_searchable` -> `dynamic_gap`) told every
    caller that passed `name_gap` it had passed nothing, with the suite green."""
    kwarg = _CAUSE[(tool, verdict)]
    assert _stop_rule._UNSETTLED[tool][verdict] == kwarg
    mine = {"action": f"settle {verdict}", "why": "the cause this verdict names"}
    out = _stop_rule.build_stop_rule(tool, verdict, **_OPEN, **{kwarg: mine})
    assert out["terminal"] is False
    assert mine in out["would_change_verdict"]
    assert not any(g["action"] == "review manually" for g in out["would_change_verdict"]), out
    for other in _gap_kwargs() - {kwarg}:
        wrong = _stop_rule.build_stop_rule(tool, verdict, **_OPEN, **{other: mine})
        assert any(g["action"] == "review manually" for g in wrong["would_change_verdict"]), (
            f"{verdict} settled by {other}, but it names {kwarg}"
        )


def test_a_passed_corpus_gap_stays_first_ahead_of_the_fallback():
    corpus = {"action": "re-index", "why": "stale"}
    out = _stop_rule.build_stop_rule(
        "check_delete_safe", "name_not_searchable", **_OPEN, corpus_gap=corpus
    )
    assert out["would_change_verdict"][0] == corpus
    assert out["would_change_verdict"][-1]["action"] == "review manually"
