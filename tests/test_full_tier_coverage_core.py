"""#740: the full tier spent half its wall clock inside coverage's tracer.

CI opened a `suite.full_seconds` regression on `main` at 379.77 s against a 360 s
Floor. The suite was not slower per unit of work -- over the eight main runs
before it, the test count grew **2.4%** (10518 -> 10772) while the measurement
swung **52%** (249.80 -> 379.77) and did not track the count at all: 10550 tests
measured 355.39 s and 10679 measured 257.59 s. The distribution straddled the
Floor and one run crossed it.

⚠⚠ **What WAS recoverable is the tracer.** Measured on this box, same command,
same tree, one environment variable apart: default core **306.81 s**,
`COVERAGE_CORE=sysmon` **150.09 s** -- and an uninstrumented run is 148.25 s, so
coverage under `sys.monitoring` is close to free where the C tracer cost ~158 s.
Python 3.12 ships `sys.monitoring`; coverage 7.x uses it on request.

⚠⚠ **The coverage NUMBER is unchanged, which had to be proven rather than
assumed, because `coverage.min` is itself a Floor and a faster tracer that
counted fewer lines as missed would be a loosening by a side door.** Two full
runs, term-missing, diffed per file: **52,510 statements and 9,471 missed under
BOTH cores**. Exactly two files differ by one line each, in opposite directions,
and both are concurrency modules (`storage/process_locks.py`,
`storage/token_tracker.py`) where a timing-dependent line flips between runs. An
earlier pair of runs differed by 12 lines in total, which is the same
run-to-run variance and not a property of the core.

⚠ Older interpreters are safe: coverage warns and falls back to its default core
when `sys.monitoring` is unavailable (`coverage/core.py`, slug `no-sysmon`), so
the 3.10 and 3.11 legs of the PR-gate matrix keep measuring exactly as before.

⚠ This file asserts the REQUEST, not the wall time. A test that asserted "the
suite runs in under N seconds" would fail on a loaded box and would be measuring
the runner, which is the very thing the eight-run table above shows is unstable.
What must not regress silently is that the tier asks for the fast core.
"""

import ast
import inspect
import textwrap

import pytest


def _tier_full_source() -> str:
    from harness import __main__ as harness_main

    return textwrap.dedent(inspect.getsource(harness_main.tier_full))


def test_the_full_tier_requests_the_sysmon_coverage_core():
    """The tier must pass `COVERAGE_CORE=sysmon` to its pytest subprocess.

    Read off the AST rather than by running the tier: a full run is five
    minutes, and this is a property of the command the harness builds.
    """
    source = _tier_full_source()
    tree = ast.parse(source)

    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == "sysmon"
    ]

    assert found, (
        "harness.tier_full no longer requests COVERAGE_CORE=sysmon. The C tracer "
        "costs ~158 s of the full tier's wall clock on the measuring box "
        "(306.81 s vs 150.09 s), which is what put `suite.full_seconds` over its "
        "Floor on main in #740. If this was removed deliberately, say why in the "
        "CHANGELOG and delete this test with a harness/retired.json entry."
    )
    assert "COVERAGE_CORE" in source, (
        "`sysmon` appears in tier_full but not as COVERAGE_CORE -- the variable "
        "name is what coverage reads"
    )


def test_the_core_is_requested_through_the_environment_not_a_pytest_flag():
    """`COVERAGE_CORE` is read by coverage itself; there is no pytest-cov flag.

    A `--cov-core=` argument does not exist, and inventing one would be accepted
    by nothing and fail the tier at startup. This pins the mechanism because the
    obvious wrong version of this change is a command-line flag.
    """
    source = _tier_full_source()

    assert "--cov-core" not in source, "no such pytest-cov option exists"
    assert "env=" in source, (
        "tier_full must hand the core to `_run(env=...)`; `_run` merges it over "
        "`os.environ` for the subprocess"
    )


def test_a_caller_supplied_coverage_core_is_not_overridden():
    """Someone debugging a tracer difference must be able to force the old core.

    ⚠ The point is falsifiability: the two-core comparison in this file's
    docstring is a claim about THIS tree, and the next person to doubt it needs
    to be able to re-run it. `setdefault` semantics keep
    `COVERAGE_CORE=ctrace python -m harness full` meaningful; an unconditional
    assignment would make the comparison impossible to reproduce through the
    harness.
    """
    source = _tier_full_source()

    assert "os.environ.get(\"COVERAGE_CORE\"" in source or "COVERAGE_CORE\", " in source, (
        "tier_full must defer to an already-set COVERAGE_CORE rather than "
        "overwriting it"
    )


@pytest.mark.parametrize("unavailable", [(3, 10), (3, 11)])
def test_the_fallback_is_coverages_own_and_is_not_reimplemented(unavailable):
    """We do not version-gate this ourselves, and that is deliberate.

    coverage decides whether `sys.monitoring` is usable -- it checks the version,
    branch support, dynamic contexts and the concurrency setting, then warns and
    falls back (`coverage/core.py`). A `sys.version_info` check in the harness
    would be a SECOND copy of that decision, correct on the day it was written
    and wrong the first time coverage widens support. The standing lesson is to
    ask the authority instead of reproducing its logic.

    ⚠ Reads the AST, not the text. The first draft asserted `"version_info" not
    in source` and failed against a correct tree, because the comment explaining
    why there is no version gate SAYS `sys.version_info`. A guard written
    against a spelling, firing on prose that describes the guard -- caught by
    running it.
    """
    tree = ast.parse(_tier_full_source())

    gates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "version_info"
    ]

    assert not gates, (
        f"tier_full gates the coverage core on the interpreter version for "
        f"{unavailable}; coverage already makes that decision and warns when it "
        f"falls back. Delete the gate."
    )
