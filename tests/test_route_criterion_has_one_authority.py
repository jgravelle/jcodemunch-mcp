"""Every normative statement of the route bar resolves to one authority (#715).

`STANDARD.md` §4 carried the correction (FINDINGS F-02): the gated route
criterion is route@1 on the held-out CONTROL subset of
`benchmarks/route_recall/holdout.json`, floor and target in
`harness/thresholds.json` under `route.control_at1`, and "route@1 >= 60%" was
never a gate. The Definition of Regression, item 7, still read "Route@1 on the
human corpus falls below 60%": a different corpus AND a different number, so a
reviewer reading one section blocked what a reviewer reading the other passed.
F-02's fix reached the paragraph that was reported and not the second site --
Standing lesson 08-19 inside the document that states it.
`CONTRIBUTING.md` told contributors the same retired bar and a leakage ceiling
the test does not use, and said both were enforced by the test.

⚠⚠ The rule is the PROPERTY over every tracked Markdown file, never the two
reported lines. A block that gives route@1 a bar with a percentage must name
`route.control_at1`, or quote only that entry's floor or target AND name the
control subset. A third copy written next fails here on arrival.

⚠ Dated records keep their numbers: a CHANGELOG entry, a survey or a findings
row describes what was true when written, and rewriting it would falsify the
history. Each exclusion below says why, and ROADMAP's is earned only by the
supersession note it must carry.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

from harness import thresholds as T

REPO = Path(__file__).resolve().parents[1]
ROUTE_ID = "route.control_at1"

#: Dated records: each describes a past state and keeps its numbers.
DATED_RECORDS = {
    "CHANGELOG.md": "release history; each entry is dated",
    "ISSUE-HISTORY.md": "rotated closed-issue forensics, dated",
    "docs/harness/ARCHAEOLOGY.md": "the 2026-09-03 survey of why each test exists",
    "docs/harness/COVERAGE-MAP.md": "the 2026-09-03 coverage survey",
    "docs/harness/FINDINGS.md": "the findings log, one dated row per finding",
    "docs/standard/DISCOVERY.md": "the discovery-phase inventory of 2026-09-03",
    "ROADMAP.md": "a plan and decision log; its exit-condition block carries a dated supersession note",
}

_ROUTE = re.compile(r"route@1", re.I)
_BAR = re.compile(r"(?i)\b(bar|floor|gate[sd]?|gating|falls?\s+below|reach(es)?|at\s+or\s+above|exit)\b|>=|≥")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_LEAK = re.compile(r"(?i)name\s+leakage")
_LEAK_BAR = re.compile(r"(?i)(at\s+or\s+below|<=|≤|ceiling)[^0-9\n]{0,20}(\d+\.\d+)")


def _tracked_markdown() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=str(REPO), check=True, capture_output=True,
        text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
    ).stdout
    return [p for p in out.splitlines() if p and (REPO / p).is_file()]


def _blocks(text: str) -> list[str]:
    """Paragraphs, split again at each list item, table row and fenced-code line.

    A fenced block has no blank lines between entries (CLAUDE.md Key Files), so
    read whole it pools one entry's bar with another's measurements.
    """
    blocks: list[list[str]] = []
    cur: list[str] = []
    fenced = False
    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith("```"):
            fenced = not fenced
        starts_item = fenced or bool(re.match(r"([-*]\s|\d+\.\s|\|)", s))
        if not s or starts_item:
            if cur:
                blocks.append(cur)
            cur = [line] if s else []
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)
    return ["\n".join(b) for b in blocks]


def _authorised_values() -> set[float]:
    e = T.load(announce=False)[ROUTE_ID]
    return {float(e["floor"]), float(e["target"])}


def _route_violations(text: str) -> list[str]:
    allowed = _authorised_values()
    bad = []
    for b in _blocks(text):
        if not (_ROUTE.search(b) and _BAR.search(b)):
            continue
        pcts = [float(m) for m in _PERCENT.findall(b)]
        if not pcts or ROUTE_ID in b:
            continue
        if all(p in allowed for p in pcts) and "control" in b.lower():
            continue
        bad.append(b)
    return bad


def _test_leakage_ceiling() -> float:
    tree = ast.parse((REPO / "tests" / "test_catalog_moratorium.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "EXIT_MAX_NAME_LEAKAGE" for t in node.targets
        ):
            return float(ast.literal_eval(node.value))
    raise AssertionError("EXIT_MAX_NAME_LEAKAGE is gone from test_catalog_moratorium.py")


def _leak_violations(text: str) -> list[str]:
    ceiling = _test_leakage_ceiling()
    bad = []
    for b in _blocks(text):
        if not _LEAK.search(b):
            continue
        for m in _LEAK_BAR.finditer(b):
            if float(m.group(2)) != ceiling and "EXIT_MAX_NAME_LEAKAGE" not in b:
                bad.append(b)
                break
    return bad


def _normative() -> list[str]:
    return [p for p in _tracked_markdown() if p not in DATED_RECORDS]


def test_every_normative_route_bar_names_the_one_authority():
    found = {}
    for rel in _normative():
        bad = _route_violations((REPO / rel).read_text(encoding="utf-8", errors="replace"))
        if bad:
            found[rel] = bad
    assert not found, (
        f"these blocks give route@1 a bar that is not `{ROUTE_ID}` (the held-out "
        f"CONTROL subset, harness/thresholds.json), so two reviewers reading "
        f"different documents reach different verdicts (#715):\n"
        + "\n".join(f"--- {k}\n" + "\n...\n".join(v) for k, v in found.items())
    )


def test_every_normative_leakage_ceiling_is_the_one_the_test_enforces():
    found = {}
    for rel in _normative():
        bad = _leak_violations((REPO / rel).read_text(encoding="utf-8", errors="replace"))
        if bad:
            found[rel] = bad
    assert not found, (
        "these blocks state a name-leakage ceiling that is not "
        "EXIT_MAX_NAME_LEAKAGE in tests/test_catalog_moratorium.py (#715):\n"
        + "\n".join(f"--- {k}\n" + "\n...\n".join(v) for k, v in found.items())
    )


def test_both_standard_sites_resolve_to_the_same_criterion():
    """§4's Floor line and the Definition of Regression name the same id and corpus."""
    text = (REPO / "docs" / "standard" / "STANDARD.md").read_text(encoding="utf-8")
    floor = next(line for line in text.splitlines() if line.startswith("Floor:") and "route@1" in line)
    regression = text.split("## Definition of Regression", 1)[1].split("\n## ", 1)[0]
    (item,) = [line for line in regression.splitlines() if re.search(r"(?i)route@1", line)]
    for site in (floor, item):
        assert ROUTE_ID in site, site
        assert "CONTROL" in site.upper(), site


def test_roadmap_earns_its_exclusion():
    """ROADMAP keeps the 2026-08 exit conditions as written, beside a note naming the gate."""
    text = (REPO / "ROADMAP.md").read_text(encoding="utf-8")
    head = text.split("**Exit conditions, named before the work**", 1)
    assert len(head) == 2, "the ROADMAP exit-condition block moved; re-check the exclusion"
    assert ROUTE_ID in head[1][:1500], (
        "ROADMAP.md's exit conditions restate a retired bar with no note naming "
        f"`{ROUTE_ID}`, so it is a normative copy, not a dated record"
    )


@pytest.mark.parametrize("planted", [
    "7. Route@1 on the human corpus falls below 60%.",
    "1. `route@1` reaches **60%** on `benchmarks/route_recall/queries.json`\n   (baseline **45.8%**);",
    "route@1 71.2% on the human corpus against a 60% moratorium bar.",
    "The exit bar is route@1 >= 55% on the full holdout.",
])
def test_the_scan_sees_every_old_spelling(planted):
    """Non-vacuity: each retired spelling is caught, including a right number on the wrong corpus."""
    assert _route_violations(planted), planted


def test_the_scan_passes_the_measured_statement_it_must_allow():
    assert not _route_violations("(moratorium: control route@1 40.0% vs a 55.0% bar)")
    assert _leak_violations("2. mean name leakage at that measurement stays at or below **0.15**")
