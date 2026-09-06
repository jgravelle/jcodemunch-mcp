"""The competitive tier's trend tracking and summary sections
(docs/competitive/DESIGN.md s5.3, s6, s10; the brief's Phase 3 item 4).

What each test pins, and why (for docs/harness/ARCHAEOLOGY.md):

- a history line carries medians, bands and gaps, keyed with the self
  corpus normalised to `self` (its id carries the commit, so without the
  normalisation every self row is a `first run` forever: found on the
  first render);
- the four movement classes and the two refusals: `unchanged` within this
  run's band, `flipped` on a sign change of the gap, `widened`/`narrowed`
  on its magnitude, `no band recorded` when the current run has none (a
  band is never invented), `first run` with no earlier line;
- a jcm value that moved past the band while the competitor's release did
  not is `our improvement`/`our regression` by the axis's direction, and
  is NOT said when the release changed;
- the competitor's release on each of the three runs rides beside the row;
- the summary carries the Movement section, the tools-not-called section,
  and labels a variant adapter under its default (DESIGN s5.3).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMPETE = REPO / "benchmarks" / "competitive"
if not COMPETE.is_dir():
    pytest.skip("benchmarks/competitive is not in this tree (not shipped in the sdist)", allow_module_level=True)
sys.path.insert(0, str(COMPETE))

import trend  # noqa: E402


def _line(date, medians, bands=None, pins=None, gaps=None):
    line = {"date": date, "jcm_commit": "c", "jcm_version": "v", "pins": pins or {"other": "1.0"}, "medians": medians}
    if bands is not None:
        line["bands"] = bands
    if gaps is not None:
        line["gaps"] = gaps
    return line


def test_norm_key_folds_the_self_commit():
    assert trend.norm_key("f1_P1/x/self@0e3a1706") == "f1_P1/x/self"
    assert trend.norm_key("f1_P1/x/lodash/lodash@f299b52") == "f1_P1/x/lodash/lodash@f299b52"


def test_line_from_result_carries_medians_bands_and_gaps():
    result = {"header": {"date": "d", "jcm_commit": "c", "jcm_version": "v", "wall_seconds": 12.5,
                         "pins": [{"name": "jcodemunch", "version": "c"}, {"name": "other", "version": "2.0"}]},
              "rows": [{"axis": "tokens_per_task", "tool": "other", "corpus": "self@c", "measured": 200.0, "jcm": 100.0, "band": 5.0},
                       {"axis": "tokens_per_task", "tool": "jcodemunch", "corpus": "self@c", "measured": 100.0, "jcm": 100.0, "band": None},
                       {"axis": "f1_P1", "tool": "other", "corpus": "self@c", "measured": None, "jcm": 0.5, "band": None}]}
    line = trend.line_from_result(result)
    assert line["medians"] == {"tokens_per_task/other/self": 200.0, "tokens_per_task/jcodemunch/self": 100.0}
    assert line["bands"] == {"tokens_per_task/other/self": 5.0}
    assert line["gaps"] == {"tokens_per_task/other/self": 100.0}
    assert line["pins"] == {"jcodemunch": "c", "other": "2.0"} and line["wall_seconds"] == 12.5


@pytest.mark.parametrize("gap_now,gap_prev,band,expected", [
    (10.0, 9.0, 2.0, "unchanged"),      # within the band
    (10.0, 11.5, 2.0, "unchanged"),     # within the band, the other way
    (10.0, 5.0, 2.0, "widened"),
    (5.0, 10.0, 2.0, "narrowed"),
    (-4.0, 4.0, 2.0, "flipped"),
    (10.0, 5.0, None, "no band recorded"),
])
def test_classify(gap_now, gap_prev, band, expected):
    assert trend.classify(gap_now, gap_prev, band) == expected


def test_first_run_and_no_history():
    cur = _line("2", {"tokens_per_task/other/self": 200.0, "tokens_per_task/jcodemunch/self": 100.0}, bands={"tokens_per_task/other/self": 5.0})
    assert trend.movement([], cur) == []
    prev = _line("1", {"f1_P1/other/self": 0.5, "f1_P1/jcodemunch/self": 0.4})  # a different row only
    recs = trend.movement([prev], cur)
    assert [r["movement"] for r in recs] == ["first run"]
    assert recs[0]["delta_now"] == 2.0 and recs[0]["delta_prev"] is None


def test_movement_row_reads_deltas_releases_and_gap_from_medians():
    first = _line("1", {"tokens_per_task/other/self": 150.0, "tokens_per_task/jcodemunch/self": 100.0}, pins={"other": "1.0"})
    prev = _line("2", {"tokens_per_task/other/self": 180.0, "tokens_per_task/jcodemunch/self": 100.0}, pins={"other": "1.1"})
    cur = _line("3", {"tokens_per_task/other/self": 300.0, "tokens_per_task/jcodemunch/self": 100.0}, bands={"tokens_per_task/other/self": 5.0}, pins={"other": "1.2"})
    (r,) = trend.movement([first, prev], cur)
    assert (r["delta_now"], r["delta_prev"], r["delta_first"]) == (3.0, 1.8, 1.5)
    assert (r["release_now"], r["release_prev"], r["release_first"]) == ("1.2", "1.1", "1.0")
    assert r["movement"] == "widened" and r["jcm_moved"] is None
    # an older line without `gaps` still yields a gap from its medians
    assert trend._gap(prev, "tokens_per_task/other/self") == 80.0


def test_jcm_moved_is_named_only_when_the_release_did_not_change():
    prev = _line("1", {"tokens_per_task/other/self": 200.0, "tokens_per_task/jcodemunch/self": 100.0}, pins={"other": "1.0"})
    cur = _line("2", {"tokens_per_task/other/self": 200.0, "tokens_per_task/jcodemunch/self": 150.0}, bands={"tokens_per_task/other/self": 5.0}, pins={"other": "1.0"})
    (r,) = trend.movement([prev], cur)
    assert r["jcm_moved"] == "our regression"  # ratio axis: up is worse for us
    cur["medians"]["tokens_per_task/jcodemunch/self"] = 50.0
    (r,) = trend.movement([prev], cur)
    assert r["jcm_moved"] == "our improvement"
    cur["pins"] = {"other": "1.1"}  # their release changed: not attributed to us
    (r,) = trend.movement([prev], cur)
    assert r["jcm_moved"] is None
    # an F1 axis: up is better for us
    prev = _line("1", {"f1_P1/other/self": 0.5, "f1_P1/jcodemunch/self": 0.4})
    cur = _line("2", {"f1_P1/other/self": 0.5, "f1_P1/jcodemunch/self": 0.6}, bands={"f1_P1/other/self": 0.05})
    (r,) = trend.movement([prev], cur)
    assert r["jcm_moved"] == "our improvement"
    # inside the band: nothing said
    cur["medians"]["f1_P1/jcodemunch/self"] = 0.42
    (r,) = trend.movement([prev], cur)
    assert r["jcm_moved"] is None


def test_render_has_the_section_and_the_release_columns():
    md = trend.render([], 0)
    assert md.startswith("## Movement") and "nothing to compare" in md
    rec = {"axis": "f1_P1", "tool": "other", "corpus": "self", "delta_now": 0.1, "delta_prev": None, "delta_first": 0.2,
           "release_now": "1.2", "release_prev": None, "release_first": "1.0", "band": 0.05, "movement": "widened", "jcm_moved": "our regression"}
    md = trend.render([rec], 2)
    assert "| f1_P1 | other | self | 0.1 | n/a | 0.2 | widened | 1.2 / n/a / 1.0 | our regression |" in md
    assert "not an attribution" in md


def test_summary_labels_variants_and_lists_tools_not_called():
    import run as runner

    result = {"header": {"date": "d", "jcm_commit": "c", "jcm_version": "v", "runs": 3,
                         "corpora": [{"id": "self@c", "files": 1, "sha256": "0" * 64}], "sandbox": "none", "tree_dirty": False, "scorer_sha256": "0" * 64,
                         "pins": [{"name": "jcodemunch", "registry": "tree", "package": "jcodemunch-mcp", "version": "c", "ran_as": "c", "variant_of": None},
                                  {"name": "jcodemunch_counter", "registry": "tree", "package": "jcodemunch-mcp", "version": "c", "ran_as": "c", "variant_of": "jcodemunch"}]},
              "rows": [{"axis": ax, "tool": t, "corpus": "self@c", "measured": 1.0, "spread": 0.0, "jcm": 1.0, "jcm_spread": 0.0, "delta": 1.0, "band": 0.05, "meaningful": False, "stable": True, "note": ""}
                       for ax in runner.RATIO_AXES + runner.DIFF_AXES for t in ("jcodemunch", "jcodemunch_counter")],
              "runs": [], "capability_only": [], "not_runnable": [],
              "tools_not_called": [{"tool": "x", "corpus": "self@c", "category": "P2", "tasks": 3, "hypothesis": "tool_not_called"}]}
    md = runner.render_md(result, [])
    assert "| jcodemunch_counter (variant of jcodemunch) |" in md
    assert "## Tools not called" in md and "`x` P2 on `self@c` (3 tasks" in md
    assert "## Movement" in md
    assert "claims" not in md  # D4 over the PRODUCED summary, not the fixture (a check over the input cannot fail against the producer)
    assert "claims" not in json.dumps(trend.line_from_result(result))  # and over the history line --record writes
