"""v1.108.231 — only signals that discriminate get a vote (#408).

`confidence` was `len(signals) / 3.0`, an unweighted vote in which a signal
firing on every symbol in the repository still contributed a full third. The
instrument shipped in v1.108.230 measured what that was worth: a mean 57.3%
of all analysed functions reported as dead across 31 repos, two of them at
100.0%.

Now a signal whose measured fire rate is degenerate gets no vote. The
denominator stays 3, so the CEILING falls instead of a lone survivor being
scaled back up to 1.0 — which is what makes a repository where nothing
discriminates return nothing through the caller's existing `min_confidence`
rather than through a second, invisible suppression rule.
"""

import pytest

from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.get_dead_code_v2 import (
    get_dead_code_v2,
    _informative_signals,
    _DEGENERACY_CUTOFF,
    _SIGNAL_NAMES,
)


def _build(tmp_path, files):
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    store.mkdir()
    for rel, body in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True, r
    return r["repo"], str(store)


# ⚠ Both fixtures need a real cross-file import. Without one ``index.imports``
# is empty and the tool takes ``_call_graph_only_dead_code``, a different
# single-signal methodology (``confidence`` fixed at 0.5, self-labelled
# ``_meta.mode = "call_graph_only"``) that emits no signal_diagnostics at all.
# There is no three-way vote there to reweight, so #408 leaves it alone — but a
# fixture that lands on it silently tests nothing.

# No entry point and no barrel, so signals 1 and 3 saturate at 1.0 and only
# no_callers discriminates.
_ONE_SURVIVOR = {
    "a.py": "def one():\n    return two()\n\ndef two():\n    return 1\n",
    "b.py": "from a import one\n\ndef three():\n    return one()\n",
    "c.py": "def four():\n    return 4\n",
}

# An entry point, a barrel, and a detached module, so all three signals land
# strictly inside the cutoff.
_ALL_THREE = {
    # Imports pkg.core directly: `from pkg import ...` did not resolve through
    # the package to core.py, leaving every file unreachable and the fixture
    # silently exercising only two signals.
    "main.py": "from pkg.core import exported_a\n\nif __name__ == '__main__':\n    print(exported_a(1))\n",
    "pkg/__init__.py": "from .core import exported_a, exported_b\n",
    "pkg/core.py": (
        "def exported_a(x):\n    return helper(x)\n\n"
        "def exported_b(x):\n    return x + 1\n\n"
        "def helper(x):\n    return x * 2\n\n"
        "def lonely(x):\n    return x - 1\n"
    ),
    "detached/mod.py": (
        "def orphan_a(x):\n    return x\n\n"
        "def orphan_b(x):\n    return x\n"
    ),
}


class TestInformativeSignals:
    def test_saturated_signal_gets_no_vote(self):
        counts = {"unreachable_file": 100, "no_callers": 40, "not_barrel_exported": 99}
        assert _informative_signals(100, counts) == {"no_callers"}

    def test_silent_signal_gets_no_vote(self):
        counts = {"unreachable_file": 0, "no_callers": 40, "not_barrel_exported": 50}
        inf = _informative_signals(100, counts)
        assert "unreachable_file" not in inf
        assert inf == {"no_callers", "not_barrel_exported"}

    def test_all_three_kept_when_all_discriminate(self):
        counts = {"unreachable_file": 30, "no_callers": 45, "not_barrel_exported": 60}
        assert _informative_signals(100, counts) == set(_SIGNAL_NAMES)

    def test_empty_repo_treats_every_signal_as_informative(self):
        """Refusing to score on nothing would be inventing a verdict."""
        assert _informative_signals(0, {s: 0 for s in _SIGNAL_NAMES}) == set(_SIGNAL_NAMES)

    def test_cutoff_is_symmetric(self):
        n = 1000
        hi = int(_DEGENERACY_CUTOFF * n) + 1
        lo = n - hi
        assert "unreachable_file" not in _informative_signals(
            n, {"unreachable_file": hi, "no_callers": 500, "not_barrel_exported": 500})
        assert "unreachable_file" not in _informative_signals(
            n, {"unreachable_file": lo, "no_callers": 500, "not_barrel_exported": 500})


class TestCeilingNotRescaling:
    def test_lone_survivor_is_not_scaled_up_to_one(self, tmp_path):
        """⚠ The whole point. One informative signal caps confidence at 0.33.

        Dividing by the number of informative signals instead would report 1.0
        off a single signal, which is more confident than the three-signal case
        it replaced.
        """
        # Every file unreachable and nothing barrel-exported: signals 1 and 3
        # saturate, leaving only no_callers.
        repo, store = _build(tmp_path, _ONE_SURVIVOR)
        r = get_dead_code_v2(repo=repo, storage_path=store, min_confidence=0.0)
        d = r["_meta"]["signal_diagnostics"]
        assert d["max_achievable_confidence"] <= 0.34, d
        for row in r["dead_symbols"]:
            assert row["confidence"] <= 0.34, row

    def test_nothing_returned_when_ceiling_is_below_threshold(self, tmp_path):
        """Suppression happens THROUGH min_confidence, not around it."""
        repo, store = _build(tmp_path, _ONE_SURVIVOR)
        r = get_dead_code_v2(repo=repo, storage_path=store, min_confidence=0.5)
        d = r["_meta"]["signal_diagnostics"]
        assert d["max_achievable_confidence"] < 0.5
        assert r["dead_symbols"] == []
        # ...and it must say why, or an empty result reads as a malfunction.
        assert "signal_warning" in r
        assert "min_confidence" in r["signal_warning"]

    def test_lowering_min_confidence_still_surfaces_the_weak_verdicts(self, tmp_path):
        """The escape hatch the warning advertises has to actually work."""
        repo, store = _build(tmp_path, _ONE_SURVIVOR)
        assert get_dead_code_v2(repo=repo, storage_path=store, min_confidence=0.5)["dead_symbols"] == []
        assert get_dead_code_v2(repo=repo, storage_path=store, min_confidence=0.3)["dead_symbols"] != []


class TestNoBlanketSuppression:
    def test_all_signals_informative_leaves_verdicts_alone(self, tmp_path):
        """When the three signals discriminate, confidence is the old arithmetic."""
        repo, store = _build(tmp_path, _ALL_THREE)
        r = get_dead_code_v2(repo=repo, storage_path=store, min_confidence=0.0)
        d = r["_meta"]["signal_diagnostics"]
        assert len(d["informative"]) == 3, (
            f"fixture must exercise all three signals or it guards nothing: {d['fire_rate']}"
        )
        assert "signal_warning" not in r
        for row in r["dead_symbols"]:
            assert row["confidence"] == round(len(row["signals"]) / 3.0, 2)
            assert "counted_signals" not in row


class TestFilterDoesNotDefineThePopulation:
    def test_file_pattern_does_not_move_the_fire_rates(self, tmp_path):
        """⚠ `file_pattern` is a view of the results, not a population.

        Applying it before the signals were measured made the rates a property
        of the caller's filter: scoping to one file drove every rate to 1.0, no
        signal discriminated, and the tool returned nothing for a question it
        could answer. Whether a signal separates live from dead code is a fact
        about the codebase.
        """
        repo, store = _build(tmp_path, _ALL_THREE)
        wide = get_dead_code_v2(repo=repo, storage_path=store, min_confidence=0.0)
        narrow = get_dead_code_v2(
            repo=repo, storage_path=store, min_confidence=0.0, file_pattern="*detached*"
        )
        wd = wide["_meta"]["signal_diagnostics"]
        nd = narrow["_meta"]["signal_diagnostics"]
        assert nd["fire_rate"] == wd["fire_rate"]
        assert nd["informative"] == wd["informative"]
        assert nd["analysed"] == wd["analysed"]
        # ...while the returned rows really are scoped.
        assert narrow["dead_symbols"], "the filter must not empty a answerable result"
        assert all("detached" in s["file"] for s in narrow["dead_symbols"])
        assert len(narrow["dead_symbols"]) < len(wide["dead_symbols"])


class TestCutoffIsCallerOverridable:
    """The cutoff is a default chosen from 31 repos, not a law about yours."""

    def test_cutoff_changes_which_signals_vote(self):
        """A rate of 0.93 sits between the two candidate cutoffs.

        ⚠ Asserted on the pure function because a fixture cannot land a rate
        there precisely. `_ONE_SURVIVOR` saturates at exactly 1.0, which is
        excluded at EVERY legal cutoff (the band is open at the top), so it
        cannot show the parameter working.
        """
        counts = {"unreachable_file": 93, "no_callers": 40, "not_barrel_exported": 50}
        assert "unreachable_file" not in _informative_signals(100, counts, 0.90)
        assert "unreachable_file" in _informative_signals(100, counts, 0.95)
        assert "unreachable_file" in _informative_signals(100, counts, 1.0)

    def test_tool_threads_the_cutoff_into_the_diagnostics(self, tmp_path):
        repo, store = _build(tmp_path, _ONE_SURVIVOR)
        default = get_dead_code_v2(repo=repo, storage_path=store, min_confidence=0.0)
        loose = get_dead_code_v2(
            repo=repo, storage_path=store, min_confidence=0.0, degeneracy_cutoff=0.95
        )
        assert default["_meta"]["signal_diagnostics"]["degeneracy_cutoff"] == _DEGENERACY_CUTOFF
        assert loose["_meta"]["signal_diagnostics"]["degeneracy_cutoff"] == 0.95

    @pytest.mark.parametrize("bad", [0.5, 0.3, 0.0, -1.0, 1.5, 2.0, "x", object()])
    def test_incoherent_cutoff_is_refused_not_clamped(self, tmp_path, bad):
        """⚠ Below 0.5 the informative band inverts and EVERY signal drops out.

        That returns nothing for every repository on earth, which is a silent
        total failure. Refusing beats clamping: a clamp would answer a question
        the caller did not ask and look like it worked.
        """
        repo, store = _build(tmp_path, _ONE_SURVIVOR)
        r = get_dead_code_v2(repo=repo, storage_path=store, degeneracy_cutoff=bad)
        assert "error" in r
        assert "degeneracy_cutoff" in r["error"]
        assert "dead_symbols" not in r

    def test_hidden_under_compact_but_still_honoured(self):
        """⚠ get_dead_code_v2 is core-tier and core_compact sits just under its
        4000-token ceiling (measured in `tests/test_schema_budget.py`, never
        transcribed here).

        A new schema property does not fit there at any description length, so
        the param is stripped from the compact schema exactly as `receipt` and
        `verify_against` are. Hidden is not removed: the handler still acts on
        it, and `_DECLARED_ARG_KEYS` is snapshotted before the strip so it never
        reads as a caller mistake.
        """
        from jcodemunch_mcp import server

        assert "degeneracy_cutoff" in server._COMPACT_STRIP_PARAMS["get_dead_code_v2"]


class TestDiagnosticsContract:
    def test_uninformative_is_the_complement_of_informative(self, tmp_path):
        repo, store = _build(tmp_path, _ONE_SURVIVOR)
        d = get_dead_code_v2(repo=repo, storage_path=store)["_meta"]["signal_diagnostics"]
        assert set(d["informative"]) | set(d["uninformative"]) == set(_SIGNAL_NAMES)
        assert not (set(d["informative"]) & set(d["uninformative"]))
        assert d["degeneracy_cutoff"] == _DEGENERACY_CUTOFF
        assert d["confidence_basis"] == "informative_signals_over_3"

    def test_counted_signals_only_present_when_it_differs(self, tmp_path):
        repo, store = _build(tmp_path, _ONE_SURVIVOR)
        r = get_dead_code_v2(repo=repo, storage_path=store, min_confidence=0.0)
        for row in r["dead_symbols"]:
            counted = row.get("counted_signals")
            if counted is not None:
                assert counted != row["signals"]
                assert set(counted) <= set(row["signals"])
