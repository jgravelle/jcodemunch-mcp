"""Action-safety verdicts carry an executable stop rule.

`confidence` says how sure we are, which invites a caller to go get surer.
A stop rule says whether anything COULD make it surer. We shipped the first
and not the second, and arXiv 2608.01347 measures what that costs: verification
loops are a distinct, tool-borne waste carrier, with the highest observed
redundant-verification runs costing 18x the clean-run median and executing 2.5x
the tool calls, at no success gain. Its prescription is to replace certainty
language with an executable stop rule.

⚠ `terminal` means FINAL, not SAFE. A blocking verdict is terminal too.

⚠⚠ The dangerous direction here is a false `terminal: true` on a destructive
action, which would tell an agent to stop checking before deleting something
that is still used. Every test below that asserts True is naming a case where
we found positive evidence; every uncertainty resolves to False.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from jcodemunch_mcp.tools._stop_rule import (
    ALREADY_CONSULTED,
    build_stop_rule,
    known_verdicts,
)

_SRC = Path(__file__).resolve().parent.parent / "src" / "jcodemunch_mcp"

_ALL_CHANNELS = dict(cross_repo=True, include_runtime=True, runtime_data_present=True)


class TestHardBlockersAreTerminal:
    """A found blocker is final. More looking adds blockers, never removes one."""

    @pytest.mark.parametrize("verdict", [
        "entry_point", "external_uses_blocking", "cross_repo_blocking",
        "internal_uses_blocking", "runtime_observed", "scip_referenced",
    ])
    def test_delete_blockers_terminal_regardless_of_channels(self, verdict):
        # Deliberately the WORST channel state: everything off or empty. A
        # blocker already in hand does not become less found.
        out = build_stop_rule(
            "check_delete_safe", verdict,
            cross_repo=False, include_runtime=False, runtime_data_present=False,
        )
        assert out["terminal"] is True
        assert out["would_change_verdict"] == []

    @pytest.mark.parametrize("verdict", [
        "signature_impact", "complexity_risk", "runtime_critical",
    ])
    def test_edit_blockers_terminal_regardless_of_channels(self, verdict):
        out = build_stop_rule(
            "check_edit_safe", verdict,
            cross_repo=False, include_runtime=False, runtime_data_present=False,
        )
        assert out["terminal"] is True


class TestBoundedVerdictsRespectChannelGaps:
    """Every bound-style verdict is an absence claim underneath."""

    @pytest.mark.parametrize("tool,verdict", [
        ("check_delete_safe", "safe_to_delete"),
        ("check_delete_safe", "internal_only"),
        ("check_delete_safe", "test_coverage_only"),
        ("check_edit_safe", "safe_to_edit"),
        ("check_edit_safe", "untested"),
    ])
    def test_terminal_when_every_channel_was_consulted(self, tool, verdict):
        out = build_stop_rule(tool, verdict, **_ALL_CHANNELS)
        assert out["terminal"] is True
        assert out["would_change_verdict"] == []

    @pytest.mark.parametrize("tool,verdict", [
        ("check_delete_safe", "safe_to_delete"),
        ("check_edit_safe", "safe_to_edit"),
    ])
    def test_no_runtime_traces_is_not_terminal(self, tool, verdict):
        """The case this generalises. check_delete_safe already said this in
        prose on exactly one branch; now it is machine-readable on all of them."""
        out = build_stop_rule(
            tool, verdict,
            cross_repo=True, include_runtime=True, runtime_data_present=False,
        )
        assert out["terminal"] is False
        assert any(g["action"] == "import-trace" for g in out["would_change_verdict"])

    def test_cross_repo_disabled_is_not_terminal(self):
        out = build_stop_rule(
            "check_delete_safe", "safe_to_delete",
            cross_repo=False, include_runtime=True, runtime_data_present=True,
        )
        assert out["terminal"] is False
        assert any("cross_repo" in g["action"] for g in out["would_change_verdict"])

    def test_runtime_disabled_names_the_flag_not_the_ingest(self):
        """Disabled and empty are different gaps, and the fix differs."""
        out = build_stop_rule(
            "check_delete_safe", "safe_to_delete",
            cross_repo=True, include_runtime=False, runtime_data_present=False,
        )
        actions = [g["action"] for g in out["would_change_verdict"]]
        assert any("include_runtime" in a for a in actions)
        assert "import-trace" not in actions


class TestUncertaintyResolvesToKeepChecking:
    @pytest.mark.parametrize("verdict", [None, "", "some_future_verdict"])
    def test_unclassified_verdict_is_never_terminal(self, verdict):
        out = build_stop_rule("check_delete_safe", verdict, **_ALL_CHANNELS)
        assert out["terminal"] is False
        assert out["would_change_verdict"], "must say what to do, not just refuse"

    def test_unknown_tool_is_never_terminal(self):
        out = build_stop_rule("check_rename_safe", "safe", **_ALL_CHANNELS)
        assert out["terminal"] is False


class TestVerdictCoverage:
    """Every verdict a tool can actually emit must be classified.

    ⚠ Without this, adding a verdict tier silently routes it to the unknown
    branch, which is safe but useless: it would tell every caller to review
    manually forever and nobody would notice.
    """

    @pytest.mark.parametrize("tool,module", [
        ("check_delete_safe", "check_delete_safe.py"),
        ("check_edit_safe", "check_edit_safe.py"),
    ])
    def test_every_emitted_verdict_is_classified(self, tool, module):
        src = (_SRC / "tools" / module).read_text(encoding="utf-8")
        tree = ast.parse(src)
        emitted = set()
        for node in ast.walk(tree):
            # `verdict = "..."` assignments inside the tool.
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "verdict":
                        if isinstance(node.value.value, str):
                            emitted.add(node.value.value)
        assert emitted, f"found no verdict assignments in {module}; the scraper broke"
        unclassified = emitted - known_verdicts(tool)
        assert not unclassified, (
            f"{tool} can emit {sorted(unclassified)} but _stop_rule.py does not "
            "classify them, so they fall to the never-terminal branch. Add them "
            "to _HARD_BLOCKER (a blocker was found), _ESTABLISHED (an absence "
            "established on the channels consulted) or _UNSETTLED (an absence "
            "that could not be established, naming its *_gap). _BOUNDED is "
            "derived from the last two; never edit it (LEDGER L-82)."
        )


class TestAlreadyConsultedIsBoundToReality:
    """The list we publish must match the tools actually called.

    ⚠⚠ This list lives in the tool DESCRIPTION, not the response, because it is
    static per call and the description is cached. That makes it prose nobody
    diffs, and a stale entry tells an agent to skip a check we no longer do.
    Hence a binding test rather than a convention.
    """

    @pytest.mark.parametrize("tool,module", [
        ("check_delete_safe", "check_delete_safe.py"),
        ("check_edit_safe", "check_edit_safe.py"),
    ])
    def test_named_tools_are_actually_imported(self, tool, module):
        src = (_SRC / "tools" / module).read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                for alias in node.names:
                    imported.add(alias.name)
        for name in ALREADY_CONSULTED[tool]:
            assert name in imported, (
                f"{tool}'s description claims it already consulted {name!r}, but "
                f"{module} does not import it. Either it stopped calling it and "
                "the claim is now false, or the name is wrong."
            )

    @pytest.mark.parametrize("tool", sorted(ALREADY_CONSULTED))
    def test_description_names_every_consulted_tool(self, tool):
        server = (_SRC / "server.py").read_text(encoding="utf-8")
        start = server.index(f'name="{tool}"')
        block = server[start:start + 3000]
        assert "ALREADY CONSULTED" in block, f"{tool} description lost its stop-rule guidance"
        for name in ALREADY_CONSULTED[tool]:
            assert name in block, (
                f"{tool} consults {name} but its description does not say so, so an "
                "agent has no way to know it need not re-run it."
            )

    @pytest.mark.parametrize("tool", sorted(ALREADY_CONSULTED))
    def test_description_says_terminal_is_not_safe(self, tool):
        """The one misreading that would actually hurt someone."""
        server = (_SRC / "server.py").read_text(encoding="utf-8")
        start = server.index(f'name="{tool}"')
        block = server[start:start + 3000]
        assert "does NOT mean safe" in block


def test_the_classifier_can_return_both_values():
    """Non-vacuity. Assertions that everything is terminal would be satisfied
    by a builder hardcoded to True, and vice versa."""
    yes = build_stop_rule("check_delete_safe", "external_uses_blocking", **_ALL_CHANNELS)
    no = build_stop_rule(
        "check_delete_safe", "safe_to_delete",
        cross_repo=True, include_runtime=True, runtime_data_present=False,
    )
    assert yes["terminal"] is True and no["terminal"] is False
