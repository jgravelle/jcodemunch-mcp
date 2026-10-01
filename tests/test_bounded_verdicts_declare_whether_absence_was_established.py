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
