"""#740: the full tier spent a large share of its wall clock inside coverage's tracer.

CI opened a `suite.full_seconds` regression on `main` at 379.77 s against a 360 s
Floor. The suite was not slower per unit of work: over eight SAMPLED `main.yml`
runs ending there -- a sample, not a consecutive window -- the test count grew
**2.4%** (10518 -> 10772) while the measurement swung
**52%** (249.80 -> 379.77) and did not track the count at all -- 10550 tests
measured 355.39 s and 10679 measured 257.59 s. The distribution straddled the
Floor and one run crossed it.

⚠⚠ **What WAS recoverable is the tracer.** `harness.tier_full` asks pytest for
coverage's `sys.monitoring` core instead of its C tracer. The measurement is in
`docs/harness/FINDINGS.md` (F-32) and written from a result file, never typed:
three conditions run back to back on the measuring box, same tree, same command.

⚠⚠ **The coverage NUMBER is not weakened, and a CONTROL is what shows it, not
an assertion.** `coverage.min` is itself a Floor, so a tracer counting fewer
lines as missed would be a loosening by a side door. Run twice on the SAME core,
ctrace reported 9475 then 9471 missed; sysmon reported 9470. The between-run
difference inside one core (4) is larger than the between-core difference (1),
and every delta sits in `server.py` -- the async dispatcher -- which reported
1132, then 1127, then 1127. Statements are identical at 52,487 throughout.

⚠ Older interpreters are safe and we do not check for them: coverage decides
whether `sys.monitoring` is usable -- version, branch support, dynamic contexts,
concurrency -- then warns and falls back to its default core (`coverage/core.py`,
slug `no-sysmon`). A `sys.version_info` gate here would be a second copy of that
decision, wrong the first time coverage widens support.

⚠⚠ **This file asserts the ENV THE TIER BUILDS, by calling `tier_full` with a
stubbed `_run`. Its first version scanned the source text and was WORTHLESS** --
review mutated the function to `_label = "sysmon"` with
`os.environ.get("COVERAGE_CORE", "ctrace")` and all four predicates stayed green,
because "the constant `sysmon` appears" and "the string `COVERAGE_CORE` appears"
are two independent existence checks that never bind to each other. A ratchet
passing against the defect it names, in the guard over the instrument that gates
every other change -- and the docstring cited that very lesson while doing it.
`[[a-ratchet-can-pass-against-the-defect-it-names]]`, Practice 9.

⚠ It deliberately does NOT assert a wall time. A "finishes in under N seconds"
test would fail on a loaded box and would be measuring the runner, which the
eight-run table above shows is the unstable thing.
"""

import ast
import inspect
import textwrap

import pytest


_FAKE_PYTEST_OUT = (
    "10780 passed, 24 skipped, 75 warnings in 200.00s (0:03:20)\n"
    "TOTAL                     52510   9471    82%\n"
)


def _call_tier_full(monkeypatch, *, preset=None):
    """Run `tier_full` with its subprocess stubbed, and return the env it built.

    ⚠ Calls the real function rather than reading it. That is the entire point
    of this file's second draft: the env dict is the OUTCOME, and a test that
    reads source can only ever check a spelling.
    """
    from harness import __main__ as harness_main

    captured = {}

    def fake_run(cmd, *, env=None):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env or {})
        return 0, _FAKE_PYTEST_OUT, 1.0

    monkeypatch.setattr(harness_main, "_run", fake_run)
    monkeypatch.setattr(harness_main, "warm_assets", lambda: True)
    if preset is None:
        monkeypatch.delenv("COVERAGE_CORE", raising=False)
    else:
        monkeypatch.setenv("COVERAGE_CORE", preset)

    harness_main.tier_full({"tiers": {}})
    return captured


def test_the_full_tier_asks_pytest_for_the_sysmon_core(monkeypatch):
    """The default, and the assertion the first draft could not make.

    This cannot pass against a `tier_full` that requests `ctrace`, because it
    reads the value handed to the subprocess rather than looking for a word in
    the function body.
    """
    captured = _call_tier_full(monkeypatch)

    assert captured["env"].get("COVERAGE_CORE") == "sysmon", (
        f"the full tier handed COVERAGE_CORE="
        f"{captured['env'].get('COVERAGE_CORE')!r} to pytest. The C tracer costs "
        f"a large share of this tier's wall clock (docs/harness/FINDINGS.md F-32), "
        f"which is what put `suite.full_seconds` over its Floor on main in #740. "
        f"If this changed deliberately, say why in the CHANGELOG and retire this "
        f"test through harness/retired.json."
    )


def test_a_caller_supplied_core_survives(monkeypatch):
    """`COVERAGE_CORE=ctrace ... -m harness full` must still force the old tracer.

    ⚠ The point is falsifiability. F-32's comparison is a claim about this tree,
    and whoever doubts it has to be able to re-run both sides THROUGH the
    harness. An unconditional assignment would make the slow side unreachable
    and the measurement unreproducible.
    """
    captured = _call_tier_full(monkeypatch, preset="ctrace")

    assert captured["env"].get("COVERAGE_CORE") == "ctrace", (
        "an already-set COVERAGE_CORE was overwritten; the comparison behind "
        "F-32 can no longer be reproduced through the harness"
    )


def test_the_core_rides_in_the_environment_and_not_as_a_pytest_flag(monkeypatch):
    """There is no `--cov-core` option; inventing one fails the tier at startup."""
    captured = _call_tier_full(monkeypatch)

    assert not any("cov-core" in arg for arg in captured["cmd"]), (
        f"no such pytest-cov option exists: {captured['cmd']}"
    )
    assert any(arg.startswith("--cov=") for arg in captured["cmd"]), (
        "the full tier stopped measuring coverage entirely, which would satisfy "
        "every timing Floor and silently drop `coverage.min`"
    )


def test_the_fallback_is_coverages_own_and_is_not_reimplemented():
    """No version gate in the harness -- coverage already makes that decision.

    ⚠ Reads the AST, not the text. An earlier draft asserted
    `"version_info" not in source` and failed against a CORRECT tree, because
    the comment explaining why there is no version gate says `sys.version_info`.
    A guard written against a spelling, firing on the prose that describes the
    guard.
    """
    from harness import __main__ as harness_main

    tree = ast.parse(textwrap.dedent(inspect.getsource(harness_main.tier_full)))

    gates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "version_info"
    ]

    assert not gates, (
        "tier_full gates the coverage core on the interpreter version. coverage "
        "checks sys.monitoring, branch support, dynamic contexts and concurrency "
        "itself, then warns and falls back (coverage/core.py, slug no-sysmon). "
        "Delete the gate rather than maintaining a second copy of it."
    )


@pytest.mark.parametrize("planted", ["ctrace", "pytrace", ""])
def test_the_guard_fails_against_a_tier_that_requests_another_core(monkeypatch, planted):
    """Non-vacuity, and it is the whole reason this file was rewritten.

    ⚠⚠ The first draft passed against a mutated `tier_full` requesting `ctrace`
    -- proven in review, all four of its predicates green. This plants that
    mutation and asserts the check above would FAIL on it, so the guard can
    never quietly become a word-search again.
    """
    from harness import __main__ as harness_main

    monkeypatch.setattr(
        harness_main,
        "tier_full",
        harness_main.tier_full,  # unchanged; we mutate the ENV the default reads
    )
    captured = _call_tier_full(monkeypatch, preset=planted)

    observed = captured["env"].get("COVERAGE_CORE")
    assert observed == planted, "the preset must reach the subprocess verbatim"
    assert observed != "sysmon", (
        "planting another core still yielded sysmon, so the assertion above "
        "cannot distinguish them"
    )
