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
reported lines. A SENTENCE that gives route@1 a bar with a percentage must name
`route.control_at1`, or quote only that entry's floor or target with the
control subset named as route@1's corpus. A third copy written next fails here
on arrival.

⚠⚠ The unit is the sentence, and a `Label:` line starts a new one. Review round
1 found the first draft scanned paragraphs: STANDARD §4's Metric, Current,
Floor and Target lines are one paragraph, the Floor line names the id, so the
retired bar put back on the Current line passed. The planted cases include
edits to the REAL §4 block for that reason (Standing lesson 08-22).

⚠ Dated records keep their numbers: a CHANGELOG entry, a survey or a findings
row describes what was true when written, and rewriting it would falsify the
history. Each exclusion below says why. ROADMAP is a plan with one dated
record in it, so it is scanned too, and only its moratorium section may hold
the retired bar, beside the note that names the gate.
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
STANDARD = "docs/standard/STANDARD.md"

#: Records of a past state. Each keeps its numbers.
DATED_RECORDS = {
    "CHANGELOG.md": "release history; each entry is dated",
    "ISSUE-HISTORY.md": "rotated closed-issue forensics, dated",
    "docs/harness/ARCHAEOLOGY.md": "the per-test rationale ledger; its rows quote the retired spellings they record",
    "docs/harness/COVERAGE-MAP.md": "the 2026-09-03 coverage survey",
    "docs/harness/FINDINGS.md": "the findings log, one dated row per finding",
    "docs/standard/DISCOVERY.md": "the discovery-phase inventory of 2026-09-03",
}

#: ROADMAP's one dated record: the 2026-08 exit conditions, kept as written.
ROADMAP_RECORD = "## Catalog moratorium"

#: ONE spelling of the route token, shared by every regex that names route@1.
#: Round 4: widening `_ROUTE` alone left `_CONTROL_CORPUS` on the literal, so a
#: correct control-corpus sentence in a new spelling was refused.
_ROUTE_TOKEN = r"route[-\s]*(?:recall\s*)?@\s*1"
_ROUTE = re.compile(_ROUTE_TOKEN, re.I)
#: A bar written as a ratio ("route@1 >= 0.60"), read as its percentage.
_RATIO = re.compile(r"(?i)(?:>=|≥|at\s+least|reach(?:es)?)\s*\*{0,2}(0?\.\d+)\b")
_BAR = re.compile(
    r"(?i)\b(bars?|floors?|gate[sd]?|gating|falls?\s+below|reach(es)?|at\s+(or\s+)?(above|below|least|most)"
    r"|exit|minimum|min|maximum|max|must|needs?|requires?|should|hits?|above|below|under|threshold|ceiling|target)\b"
    r"|>=|<=|≥|≤"  # never a bare > or <: an arrow in a measured line is not a bar (round 2)
)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.I)
#: A retired spelling QUOTED in a correction ('the earlier "route@1 >= 60%" was never a gate').
_QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”')
#: The id with a comparator: the number must be the entry's floor (`route.control_at1>=55` was not).
_ID_COMPARED = re.compile(r"route\.control_at1`?\s*,?\s*(?:>=|≥|floor)\s*(\d+(?:\.\d+)?)")
#: The control subset named as route@1's corpus, not merely present in the sentence.
_CONTROL_CORPUS = re.compile(
    rf"(?i)control[-\s]+(subset\s+)?{_ROUTE_TOKEN}|{_ROUTE_TOKEN}\s+(on\s+|over\s+)?(the\s+)?(held-out\s+)?control"
)
_LEAK = re.compile(r"(?i)leakage")
_LEAK_BAR = re.compile(
    r"(?i)(?:at\s+or\s+below|at\s+most|<=|≤|<|ceiling|under|below|max(?:imum)?|stays?|exceed|bar\s+of)"
    r"[^0-9\n]{0,24}?(?P<n>\d+(?:\.\d+)?)\s*(?P<pct>%|percent\b)?"
    r"|(?P<n2>\d+(?:\.\d+)?)\s*(?P<pct2>%|percent\b)?\s+or\s+less"
)


def _tracked_markdown() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=str(REPO), check=True, capture_output=True,
        text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
    ).stdout
    return [p for p in out.splitlines() if p and (REPO / p).is_file()]


def _blocks(text: str) -> list[str]:
    """Paragraphs, split again at each list item, table row, `Label:` line and fenced-code line.

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
        starts = fenced or bool(re.match(r"([-*]\s|\d+\.\s|\||[A-Z][\w ]{0,30}:\s)", s))
        if not s or starts:
            if cur:
                blocks.append(cur)
            cur = [line] if s else []
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)
    return ["\n".join(b) for b in blocks]


def _sentences(text: str) -> list[str]:
    out = []
    for b in _blocks(text):
        out.extend(s for s in re.split(r"(?<=[.!?])(?:\*\*)?\s+", b) if s.strip())
    return out


def _authorised_values() -> set[float]:
    e = T.load(announce=False)[ROUTE_ID]
    return {float(e["floor"]), float(e["target"])}


def _route_violations(text: str) -> list[str]:
    allowed = _authorised_values()
    bad = []
    for s in _sentences(text):
        if not (_ROUTE.search(s) and _BAR.search(s)):
            continue
        unquoted = _QUOTED.sub("", s)
        pcts = [float(m) for m in _PERCENT.findall(unquoted)]
        pcts += [round(float(m) * 100, 6) for m in _RATIO.findall(unquoted)]
        if not pcts:
            continue
        if ROUTE_ID in s:
            # Round 2: naming the id is not a licence for any number beside it.
            if all(p in allowed for p in pcts):
                continue
            bad.append(s)
            continue
        if all(p in allowed for p in pcts) and _CONTROL_CORPUS.search(s):
            continue
        bad.append(s)
    floor = min(allowed)
    for s in _sentences(text):
        for m in _ID_COMPARED.finditer(s):
            if float(m.group(1)) != floor and s not in bad:
                bad.append(s)
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
    for s in _sentences(text):
        if not _LEAK.search(s) or "EXIT_MAX_NAME_LEAKAGE" in s:
            continue
        for m in _LEAK_BAR.finditer(s[_LEAK.search(s).start():]):
            n, pct = (m.group("n"), m.group("pct")) if m.group("n") else (m.group("n2"), m.group("pct2"))
            value = float(n) / (100 if pct else 1)
            if abs(value - ceiling) > 1e-9:
                bad.append(s)
                break
    return bad


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8", errors="replace")


def _normative() -> list[str]:
    return [p for p in _tracked_markdown() if p not in DATED_RECORDS and p != "ROADMAP.md"]


def _roadmap_outside_record() -> str:
    text = _read("ROADMAP.md")
    start = text.index(ROADMAP_RECORD)
    nxt = re.search(r"(?m)^## ", text[start + len(ROADMAP_RECORD):])
    end = start + len(ROADMAP_RECORD) + nxt.start() if nxt else len(text)
    return text[:start] + text[end:]


def _report(found: dict[str, list[str]]) -> str:
    return "\n".join(f"--- {k}\n" + "\n...\n".join(v) for k, v in found.items())


def test_every_normative_route_bar_names_the_one_authority():
    found = {rel: bad for rel in _normative() if (bad := _route_violations(_read(rel)))}
    if bad := _route_violations(_roadmap_outside_record()):
        found["ROADMAP.md (outside the moratorium record)"] = bad
    assert not found, (
        f"these sentences give route@1 a bar that is not `{ROUTE_ID}` (the held-out "
        f"CONTROL subset, harness/thresholds.json), so two reviewers reading "
        f"different documents reach different verdicts (#715):\n" + _report(found)
    )


def test_every_normative_leakage_ceiling_is_the_one_the_test_enforces():
    found = {rel: bad for rel in _normative() if (bad := _leak_violations(_read(rel)))}
    if bad := _leak_violations(_roadmap_outside_record()):
        found["ROADMAP.md (outside the moratorium record)"] = bad
    assert not found, (
        "these sentences state a name-leakage ceiling that is not "
        "EXIT_MAX_NAME_LEAKAGE in tests/test_catalog_moratorium.py (#715):\n" + _report(found)
    )


def test_both_standard_sites_resolve_to_the_same_criterion():
    """§4's Floor line and the Definition of Regression name the same id and corpus."""
    text = _read(STANDARD)
    floor = next(line for line in text.splitlines() if line.startswith("Floor:") and "route@1" in line)
    regression = text.split("## Definition of Regression", 1)[1].split("\n## ", 1)[0]
    (item,) = [line for line in regression.splitlines() if re.search(r"(?i)route@1", line)]
    for site in (floor, item):
        assert ROUTE_ID in site, site
        assert "CONTROL" in site.upper(), site


def test_roadmap_earns_its_exclusion():
    """ROADMAP keeps the 2026-08 exit conditions as written, beside a note naming the gate."""
    text = _read("ROADMAP.md")
    head = text.split("**Exit conditions, named before the work**", 1)
    assert len(head) == 2, "the ROADMAP exit-condition block moved; re-check the record"
    assert text.index(ROADMAP_RECORD) < len(head[0]), "the exit conditions left the moratorium section"
    assert ROUTE_ID in head[1][:1500] and "EXIT_MAX_NAME_LEAKAGE" in head[1][:1500], (
        "ROADMAP.md's exit conditions restate a retired bar with no note naming "
        f"`{ROUTE_ID}` and EXIT_MAX_NAME_LEAKAGE"
    )


@pytest.mark.parametrize("planted", [
    "7. Route@1 on the human corpus falls below 60%.",
    "1. `route@1` reaches **60%** on `benchmarks/route_recall/queries.json`\n   (baseline **45.8%**);",
    "route@1 71.2% on the human corpus against a 60% moratorium bar.",
    "The exit bar is route@1 >= 55% on the full holdout.",
    "route@1 >= 55% on the full holdout; the control subset is measured.",
    "route@1 must be at least 60% on the human corpus.",
    "Minimum route@1: 60% (human corpus).",
    "route@1 must reach 60% [`route.control_at1`] on the human corpus.",
    "Route@1 needs 60% on the human corpus.",
    "route@1 should hit 60 percent on queries.json.",
    "Initial entries: `route.control_at1>=55` (corrected from the standard's 60).",
    # review round 3
    "route @1 must reach 60% on queries.json.",
    "Route recall@1 must reach 60% on queries.json.",
    "the gate: route@1 >= 0.60 on queries.json.",
    "The floor is `route.control_at1` floor 55.",
])
def test_the_scan_sees_every_old_spelling(planted):
    """Non-vacuity: each retired spelling is caught, including a right number on the wrong corpus."""
    assert _route_violations(planted), planted


@pytest.mark.parametrize("planted", [
    "2. mean name leakage at that measurement stays at or below **0.15**",
    "Mean name leakage must stay under 15%.",
    "Name leakage max 0.15.",
    "The name-leakage ceiling is 0.15.",
    "Leakage of tool names must stay at or below 0.15.",
    # review round 3
    "Leakage must not exceed 0.2.",
    "Name leakage of 0.2 or less is required.",
    "It holds a leakage bar of 0.20.",
])
def test_the_leak_scan_sees_every_old_spelling(planted):
    assert _leak_violations(planted), planted


@pytest.mark.parametrize("line_prefix, reintroduced", [
    ("Current:", "Current: route@1 71.2% on the human corpus against a 60% moratorium bar."),
    ("Target:", "Target: route@1 >= 60% on the human corpus."),
])
def test_the_scan_sees_a_retired_bar_put_back_into_the_real_standard_block(line_prefix, reintroduced):
    """Round 1: a paragraph-level scan let the Floor line's id exempt the whole §4 block."""
    text = _read(STANDARD)
    lines = text.splitlines()
    i = next(n for n, line in enumerate(lines) if line.startswith("Floor:") and ROUTE_ID in line)
    j = next(n for n in range(i, -1, -1) if lines[n].startswith(line_prefix)) if line_prefix == "Current:" else next(
        n for n in range(i, len(lines)) if lines[n].startswith(line_prefix)
    )
    lines[j] = reintroduced
    assert not _route_violations(text), "precondition: the real STANDARD.md is clean"
    assert _route_violations("\n".join(lines)), reintroduced


def test_the_roadmap_exemption_is_confined_to_its_record():
    """A retired bar written anywhere else in ROADMAP is scanned like any other file."""
    planted = _read("ROADMAP.md") + "\n\n## A later plan\n\nThe exit is route@1 >= 60% on queries.json.\n"
    start = planted.index(ROADMAP_RECORD)
    nxt = re.search(r"(?m)^## ", planted[start + len(ROADMAP_RECORD):])
    outside = planted[:start] + planted[start + len(ROADMAP_RECORD) + nxt.start():]
    assert _route_violations(outside)


def test_the_scan_passes_the_measured_statement_it_must_allow():
    assert not _route_violations("(moratorium: control route@1 40.0% vs a 55.0% bar)")
    assert not _route_violations("route@1 reached 71.2% (from 45.8%) -> measured only.")
    for spelling in ("route@1", "route @1", "route recall@1", "route-recall@1"):
        assert not _route_violations(f"control {spelling} must reach 55%."), spelling
        assert _route_violations(f"{spelling} must reach 60% on queries.json."), spelling
    assert not _route_violations(
        'CORRECTION: the earlier "route@1 >= 60%" was never a gate, and 55% is the EXIT bar, '
        "the target of `route.control_at1`."
    )
