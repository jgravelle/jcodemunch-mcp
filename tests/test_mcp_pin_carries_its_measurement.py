"""The `mcp` upper bound in pyproject.toml carries the measurement that justifies it.

#609 (@kecsap, split from #574) asked why `mcp` is pinned below 2.0. The answer
was measured on 2026-09-11 against mcp 2.2.0 in a scratch venv: the low-level
`Server` no longer has the decorator registration (`@server.list_tools()` and
five siblings) this dispatcher is built on, so the server fails at import and a
stdio `initialize` never completes. A pin whose reason lives only in an issue
rots: the next person to bump it re-discovers the reason in CI.

The property, not the spelling (Practice 9, review round 1): whatever major
the ceiling excludes, the comment on that line must say it was MEASURED
against a version of that same major, when, and which API fact broke. A bump
from `<2.0.0` to `<3.0.0` that leaves a comment naming `mcp 2.2.0` fails; a
bump whose comment names `mcp 3.x.y`, a date and the fact passes. The old
reason cannot survive a bump unchanged, so the bumper re-measures.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

# A version operator is required: `"mcp",` at [project].keywords is a word, not a pin.
_PIN_LINE = re.compile(r'^\s*"mcp(?P<spec>[<>=!~][^"]*)"\s*,?\s*(?:#(?P<comment>.*))?$')
_CEILING = re.compile(r"<\s*(?P<major>\d+)\.")


def _pin() -> tuple[str, str]:
    """(specifier, comment) of the one `mcp` dependency line."""
    hits = [m for m in map(_PIN_LINE.match, PYPROJECT.read_text(encoding="utf-8").splitlines()) if m]
    assert len(hits) == 1, [m.group(0) for m in hits]
    return hits[0].group("spec"), (hits[0].group("comment") or "").strip()


def test_the_pin_has_an_upper_bound_with_a_major():
    spec, _ = _pin()
    assert _CEILING.search(spec), f"no `<N.` ceiling in the mcp specifier: {spec!r}"


def test_the_pin_line_carries_a_measured_reason_for_the_major_it_excludes():
    spec, comment = _pin()
    assert comment, "the mcp pin has no reason beside it on the same line"
    major = _CEILING.search(spec).group("major")
    assert re.search(r"\bmeasured\b", comment), comment
    assert re.search(rf"\bmcp {major}\.\d+\.\d+\b", comment), (
        f"the ceiling excludes mcp {major}.x but the reason names no measured {major}.x version: "
        f"re-measure against the major the pin excludes and rewrite the reason"
    )
    assert re.search(r"\b20\d\d-\d\d-\d\d\b", comment), "the reason must carry its date"
    assert re.search(r"#\d+", comment), "the reason must point at the issue holding the probe"


def test_the_reason_names_what_actually_breaks():
    """A fact checkable against server.py, not a preference."""
    _, comment = _pin()
    assert "decorator" in comment or "on_call_tool" in comment, comment


@pytest.mark.parametrize(
    "line, expect_pass",
    [
        ('"mcp>=1.10.0,<2.0.0",  # ceiling measured 2026-09-11 against mcp 2.2.0 (#609): decorator registration removed', True),
        # the bump that keeps the old reason: the property must fail it
        ('"mcp>=1.10.0,<3.0.0",  # ceiling measured 2026-09-11 against mcp 2.2.0 (#609): decorator registration removed', False),
        # a re-measured bump passes with no test edit
        ('"mcp>=1.10.0,<3.0.0",  # ceiling measured 2027-01-05 against mcp 3.0.1 (#900): on_call_tool signature changed', True),
        # a bare pin fails
        ('"mcp>=1.10.0,<2.0.0",', False),
    ],
)
def test_the_property_distinguishes_a_remeasured_bump_from_an_unmeasured_one(tmp_path, monkeypatch, line, expect_pass):
    """Non-vacuity over the four shapes the guard exists to tell apart; the
    real pyproject is never touched."""
    fake = tmp_path / "pyproject.toml"
    fake.write_text('[project]\ndependencies = [\n    ' + line + '\n    "httpx>=0.27.0",\n]\n', encoding="utf-8")
    me = sys.modules[__name__]
    monkeypatch.setattr(me, "PYPROJECT", fake)
    checks = (me.test_the_pin_line_carries_a_measured_reason_for_the_major_it_excludes,)
    if expect_pass:
        for check in checks:
            check()
    else:
        with pytest.raises(AssertionError):
            for check in checks:
                check()
