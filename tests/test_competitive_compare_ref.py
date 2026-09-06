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
- no total or mean appears on the page (F-13), and a file of another
  schema, or an out-dir holding two result files, is refused;
- the page is produced by the script from the two files: the command that
  prints it types nothing.
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


def _result(commit, rows, pins=("null_grep", "jcodemunch", "other")):
    return {"header": {"schema": "jcm-competitive-result/v1", "date": "2026-09-06T00:00:00Z", "jcm_commit": commit,
                       "jcm_version": "1.0", "runs": 3, "sandbox": "docker", "tree_dirty": False,
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


def test_the_command_calls_the_script_and_types_nothing():
    cmd = (REPO / ".claude" / "commands" / "competitive-compare.md").read_text(encoding="utf-8")
    assert "compare_ref.py" in cmd
    assert "Nothing here types a number" in cmd
