"""`/competitive-compare`'s table script, benchmarks/competitive/compare_ref.py
(docs/workflows/DESIGN.md s2.7; competitive DESIGN s9.1).

What each test pins, and why (for docs/harness/ARCHAEOLOGY.md):

- every row of either side is on the page, jcm rows first, with the jcm
  difference SIGNED (current minus ref) and the other rows' movement from
  `trend.classify` over the two gaps against the current band;
- a value absent on one side prints `n/a`, never 0, and a row with no
  current band says `no band recorded` rather than inventing one (a ref
  that predates an adapter is the ordinary case);
- the self corpus is matched across the two commits (`self@<commit>` on
  each side normalises to `self`; without that every self row is
  `n/a` on one side forever, the trend module's first-render lesson);
- no total or mean appears in any table row (F-13); counts under the
  tables count rows; a file of another schema, or an out-dir holding two
  result files, is refused;
- a count is printed in full (`23650`, never `2.365e+04`), so a movement
  can be checked against the band from the page;
- each side's scorer sha256 and interpreter are on the page from its own
  header, and a mismatch is said before the first number (the dry run's
  ref predated a run.py change and a patch release of the interpreter);
- the FINDINGS row for a dry run comes from `--findings-row`, its figures
  equal to the page's counts;
- the command names the script and carries no table row of its own.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMPETE = REPO / "benchmarks" / "competitive"
if not COMPETE.is_dir():
    pytest.skip("benchmarks/competitive is not in this tree (not shipped in the sdist)", allow_module_level=True)
sys.path.insert(0, str(COMPETE))

import compare_ref  # noqa: E402


def _row(axis, tool, corpus, measured, jcm, band=None, note=""):
    delta = None
    if measured is not None and jcm:
        delta = round(measured / jcm, 4) if axis == "tokens_per_task" else round(measured - jcm, 4)
    return {"axis": axis, "tool": tool, "corpus": corpus, "runs": [measured] * 3, "measured": measured,
            "spread": 0.0, "jcm": jcm, "jcm_spread": 0.0, "delta": delta, "band": band,
            "meaningful": False, "stable": True, "note": note}


def _result(commit, rows, pins=("null_grep", "jcodemunch", "other"), scorer="s" * 64, python="3.12.4", wall=10.0):
    return {"header": {"schema": "jcm-competitive-result/v1", "date": "2026-09-06T00:00:00Z", "jcm_commit": commit,
                       "jcm_version": "1.0", "runs": 3, "sandbox": "docker", "tree_dirty": False,
                       "scorer_sha256": scorer, "runner": {"python": python}, "wall_seconds": wall,
                       "corpora": [{"id": f"self@{commit}", "files": 3, "sha256": "ab" * 32}],
                       "pins": [{"name": p, "version": "1.0"} for p in pins]},
            "rows": rows, "runs": [], "capability_only": [], "not_runnable": []}


CUR = _result("c2", [
    _row("tokens_per_task", "jcodemunch", "self@c2", 100.0, 100.0, band=5.0),
    _row("tokens_per_task", "other", "self@c2", 130.0, 100.0, band=5.0),
    _row("f1_P1", "jcodemunch", "self@c2", 0.9, 0.9, band=0.05),
    _row("f1_P1", "other", "self@c2", 0.7, 0.9, band=0.05),
    _row("f1_P1", "newtool", "self@c2", 0.8, 0.9, band=0.05),
    _row("tokens_per_task", "null_grep", "self@c2", None, 100.0, note="NOT COMPARABLE"),
])
REF = _result("c1", [
    _row("tokens_per_task", "jcodemunch", "self@c1", 120.0, 120.0, band=6.0),
    _row("tokens_per_task", "other", "self@c1", 130.0, 120.0, band=6.0),
    _row("f1_P1", "jcodemunch", "self@c1", 0.9, 0.9, band=0.05),
    _row("f1_P1", "other", "self@c1", 0.5, 0.9, band=0.05),
    _row("tokens_per_task", "null_grep", "self@c1", 400.0, 120.0, band=6.0),
], pins=("null_grep", "jcodemunch", "other"))


def _by(records):
    return {(r["axis"], r["tool"]): r for r in records}


def test_jcm_rows_first_with_signed_difference():
    recs = compare_ref.compare(CUR, REF)
    assert [r["tool"] for r in recs[:2]] == ["jcodemunch", "jcodemunch"]
    tok = _by(recs)[("tokens_per_task", "jcodemunch")]
    assert tok["difference"] == pytest.approx(-20.0)
    assert tok["movement"] is None
    page = compare_ref.render(CUR, REF, recs)
    assert "| tokens_per_task | self | 120 | 100 | -20 |" in page


def test_movement_from_the_two_gaps_against_the_current_band():
    by = _by(compare_ref.compare(CUR, REF))
    # other's token gap: 10 at the ref, 30 now, band 5 -> widened
    assert by[("tokens_per_task", "other")]["movement"] == "widened"
    # other's f1 gap: -0.4 at the ref, -0.2 now, band 0.05 -> narrowed
    assert by[("f1_P1", "other")]["movement"] == "narrowed"


def test_absent_side_is_na_never_zero():
    recs = compare_ref.compare(CUR, REF)
    by = _by(recs)
    new = by[("f1_P1", "newtool")]
    assert new["ref_measured"] is None and new["movement"] == "n/a"
    grep = by[("tokens_per_task", "null_grep")]
    assert grep["cur_measured"] is None and grep["ref_measured"] == 400.0 and grep["movement"] == "n/a"
    page = compare_ref.render(CUR, REF, recs)
    line = next(ln for ln in page.splitlines() if "| newtool |" in ln)
    assert "| n/a | n/a | 0.8 |" in line
    assert " 0 |" not in line


def test_no_band_is_said_not_invented():
    cur = _result("c2", [
        _row("f1_P1", "jcodemunch", "self@c2", 0.9, 0.9),
        _row("f1_P1", "other", "self@c2", 0.7, 0.9),
    ])
    by = _by(compare_ref.compare(cur, REF))
    assert by[("f1_P1", "other")]["movement"] == "no band recorded"


def test_ref_without_the_tier_prints_na_everywhere():
    recs = compare_ref.compare(CUR, None)
    assert all(r["ref_measured"] is None for r in recs)
    page = compare_ref.render(CUR, None, recs)
    assert "no competitive tier at that ref" in page


def test_no_total_or_mean_on_the_page():
    page = compare_ref.render(CUR, REF, compare_ref.compare(CUR, REF))
    table_lines = [ln for ln in page.splitlines() if ln.startswith("|")]
    assert table_lines
    assert not any(re.search(r"(?i)\b(total|mean|average|overall|sum)\b", ln) for ln in table_lines)
    assert "Per row, never per total" in page


def test_cli_writes_the_page_and_refuses_the_wrong_schema(tmp_path):
    cur_dir, ref_dir = tmp_path / "cur", tmp_path / "ref"
    cur_dir.mkdir()
    ref_dir.mkdir()
    (cur_dir / "r.json").write_text(json.dumps(CUR), encoding="utf-8")
    (cur_dir / "checkpoint-c2.json").write_text("{}", encoding="utf-8")   # ignored, as run.py leaves one on a crash
    (ref_dir / "r.json").write_text(json.dumps(REF), encoding="utf-8")
    out = tmp_path / "compare.md"
    p = subprocess.run([sys.executable, str(COMPETE / "compare_ref.py"), "--cur", str(cur_dir), "--ref", str(ref_dir),
                        "--out", str(out), "--note", "filter: --only self"], capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stderr
    text = out.read_text(encoding="utf-8")
    assert "filter: --only self" in text and "| tokens_per_task | self | 120 | 100 | -20 |" in text
    assert p.stdout.strip() == text.strip()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"header": {"schema": "something-else"}, "rows": []}), encoding="utf-8")
    p2 = subprocess.run([sys.executable, str(COMPETE / "compare_ref.py"), "--cur", str(bad), "--out", str(out)],
                        capture_output=True, text=True, encoding="utf-8")
    assert p2.returncode != 0 and "not a jcm-competitive-result/v1" in p2.stderr
    (cur_dir / "second.json").write_text(json.dumps(CUR), encoding="utf-8")
    p3 = subprocess.run([sys.executable, str(COMPETE / "compare_ref.py"), "--cur", str(cur_dir), "--out", str(out)],
                        capture_output=True, text=True, encoding="utf-8")
    assert p3.returncode != 0 and "expected one result file" in p3.stderr


def test_counts_print_in_full_never_in_exponent():
    cur = _result("c2", [_row("tools_list_tokens", "jcodemunch", "self@c2", 23650.0, 23650.0, band=1182.5),
                         _row("tools_list_tokens", "other", "self@c2", 118000.0, 23650.0, band=1182.5)])
    page = compare_ref.render(cur, None, compare_ref.compare(cur, None))
    assert "| 23650 |" in page and "| 118000 |" in page
    assert "e+0" not in page


def test_provenance_is_on_the_page_and_a_mismatch_is_said_before_the_first_number():
    ref = _result("c1", REF["rows"], scorer="r" * 64, python="3.12.6", wall=11.0)
    recs = compare_ref.compare(CUR, ref)
    page = compare_ref.render(CUR, ref, recs)
    head = page.split("## Our rows")[0]
    assert "sha256 `" + "s" * 64 + "`" in head and "sha256 `" + "r" * 64 + "`" in head
    assert "interpreter 3.12.4" in head and "interpreter 3.12.6" in head
    assert "DIFFERENT scorer code" in head and "different interpreters" in head
    same = compare_ref.render(CUR, REF, compare_ref.compare(CUR, REF)).split("## Our rows")[0]
    assert "DIFFERENT scorer code" not in same and "different interpreters" not in same
    s = compare_ref.summary(CUR, ref, recs)
    assert s["same_scorer"] is False and s["same_python"] is False
    assert compare_ref.summary(CUR, REF, compare_ref.compare(CUR, REF))["same_scorer"] is True


def test_findings_row_figures_equal_the_pages_counts(tmp_path):
    cur_dir = tmp_path / "cur"
    cur_dir.mkdir()
    (cur_dir / "r.json").write_text(json.dumps(CUR), encoding="utf-8")
    ref_file = tmp_path / "ref.json"
    ref_file.write_text(json.dumps(REF), encoding="utf-8")
    out = tmp_path / "compare.md"
    p = subprocess.run([sys.executable, str(COMPETE / "compare_ref.py"), "--cur", str(cur_dir), "--ref", str(ref_file),
                        "--out", str(out), "--findings-row", "CF-0", "--page-path", "docs/x.md"],
                       capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stderr
    row = p.stdout.strip()
    assert row.startswith("| CF-0 |") and row.endswith("|") and "`docs/x.md`" in row
    s = compare_ref.summary(CUR, REF, compare_ref.compare(CUR, REF))
    assert f"Rows {s['rows']}: {s['jcm_rows']} jcm rows" in row
    assert f"{s['jcm_zero_difference']} differing by exactly 0 and {s['jcm_moved_past_band']} moved past" in row
    assert "wall 10 s current, 10 s ref" in row
    page = out.read_text(encoding="utf-8")
    assert f"Rows {s['rows']}: jcm rows {s['jcm_rows']}" in page
    # the same figures on the page and in the row: neither was typed
    for k in ("rows", "jcm_rows", "other_rows"):
        assert str(s[k]) in row and str(s[k]) in page


def test_the_command_calls_the_script_and_carries_no_table_of_its_own():
    cmd = (REPO / ".claude" / "commands" / "competitive-compare.md").read_text(encoding="utf-8")
    assert "compare_ref.py" in cmd and "--findings-row" in cmd
    # a markdown table row carrying a digit would be a value typed into the command
    assert not re.search(r"^\|.*\d.*\|\s*$", cmd, re.M)
    assert "retype none of it" in cmd
