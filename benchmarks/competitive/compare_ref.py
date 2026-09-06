"""The `/competitive-compare` table: two result files (the working tree and a
ref) side by side, per row (docs/workflows/DESIGN.md s2.7; competitive
DESIGN s9.1).

purpose:  put every (axis, tool, corpus) row of two competitive-tier runs
          on one line, the ref's `measured` and delta beside the current
          tree's, with the current band and the gap's movement between
          the two jcm commits, so a session that changed retrieval reads
          how the change moved each row without typing a number; the jcm
          rows first with the signed difference, because our own movement
          is the reason the command exists; the row counts and the wall
          times under the tables, and `--findings-row` renders a FINDINGS
          row from them, so a dry run is recorded without a typed figure
invokes:  nothing outside the process: the two result JSON files run.py
          wrote (`jcm-competitive-result/v1`), score.py's axis lists and
          trend.py's `classify`/`norm_key`
produces: `compare(cur, ref)` records, `summary(cur, ref, records)` counts,
          `render(...)`, the markdown page the command writes to
          .claude/state/evidence/competitive_compare.md, and
          `findings_row(...)` for docs/competitive/FINDINGS.md
refuses:  to print a total or a mean over measurements (F-13: per row,
          never per total; the counts under the tables count ROWS); to
          print 0 for a value absent on either side (`n/a`); to classify a
          movement when the current row has no band (`no band recorded`);
          to read anything but the two files given
pinned:   the result schema `jcm-competitive-result/v1`; a file of another
          schema is refused
fairness: the page prints each side's scorer sha256 and interpreter from
          its own header and says in its first lines when they differ (a
          ref that predates a scorer change was scored by different code,
          and the movement column then compares two scorers' outputs); a
          ref that predates a row prints `n/a` there, never a copied
          value; the header repeats the tier's fairness line
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from score import DIFF_AXES, RATIO_AXES
from trend import JCM, classify, norm_key

SCHEMA = "jcm-competitive-result/v1"


def load(path: Path) -> dict:
    """One result file, or the single result file of a directory."""
    if path.is_dir():
        files = sorted(p for p in path.glob("*.json") if not p.name.startswith("checkpoint-"))
        if len(files) != 1:
            raise SystemExit(f"{path}: expected one result file, found {len(files)}")
        path = files[0]
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("header", {}).get("schema") != SCHEMA:
        raise SystemExit(f"{path}: not a {SCHEMA} file")
    return result


def _rows(result: dict) -> dict[str, dict]:
    return {norm_key(f"{r['axis']}/{r['tool']}/{r['corpus']}"): r for r in result["rows"]}


def _gap(r: dict | None) -> float | None:
    if r is None or r.get("measured") is None or r.get("jcm") is None:
        return None
    return r["measured"] - r["jcm"]


def compare(cur: dict, ref: dict | None) -> list[dict]:
    """One record per row present on either side; jcm rows first, then the
    others sorted by axis, tool, corpus. `ref` None means the ref tree had
    no competitive tier (every ref cell n/a)."""
    c_rows, r_rows = _rows(cur), (_rows(ref) if ref else {})
    out = []
    for key in sorted(set(c_rows) | set(r_rows)):
        axis, tool, corpus = key.split("/", 2)
        if axis not in RATIO_AXES + DIFF_AXES:
            continue
        c, r = c_rows.get(key), r_rows.get(key)
        rec = {"axis": axis, "tool": tool, "corpus": corpus,
               "ref_measured": r.get("measured") if r else None,
               "ref_delta": r.get("delta") if r else None,
               "cur_measured": c.get("measured") if c else None,
               "cur_delta": c.get("delta") if c else None,
               "band": c.get("band") if c else None,
               "note": (c.get("note") or "") if c else "absent on the current tree",
               "movement": None, "difference": None}
        if tool == JCM:
            if rec["cur_measured"] is not None and rec["ref_measured"] is not None:
                rec["difference"] = round(rec["cur_measured"] - rec["ref_measured"], 6)
        else:
            g_now, g_ref = _gap(c), _gap(r)
            if g_now is None or g_ref is None:
                rec["movement"] = "n/a"
            else:
                rec["movement"] = classify(g_now, g_ref, rec["band"])
        out.append(rec)
    out.sort(key=lambda x: (x["tool"] != JCM, x["axis"], x["tool"], x["corpus"]))
    return out


def _side(h: dict | None) -> dict:
    if not h:
        return {"commit": None, "scorer_sha256": None, "python": None, "wall_seconds": None}
    return {"commit": h.get("jcm_commit"), "scorer_sha256": h.get("scorer_sha256"),
            "python": (h.get("runner") or {}).get("python"), "wall_seconds": h.get("wall_seconds")}


def summary(cur: dict, ref: dict | None, records: list[dict]) -> dict:
    """Counts of ROWS (never of measurements) and the two sides' provenance."""
    jcm = [r for r in records if r["tool"] == JCM]
    others = [r for r in records if r["tool"] != JCM]
    diffs = [r for r in jcm if r["difference"] is not None]
    c, r = _side(cur["header"]), _side(ref["header"] if ref else None)
    return {
        "rows": len(records), "jcm_rows": len(jcm), "jcm_compared": len(diffs),
        "jcm_zero_difference": sum(1 for x in diffs if x["difference"] == 0),
        "jcm_moved_past_band": sum(1 for x in diffs if x["band"] is not None and abs(x["difference"]) > x["band"]),
        "other_rows": len(others),
        "movement": dict(sorted(Counter(x["movement"] for x in others).items())),
        "tools": sorted({x["tool"] for x in others}),
        "cur": c, "ref": r,
        "same_scorer": (c["scorer_sha256"] == r["scorer_sha256"]) if ref else None,
        "same_python": (c["python"] == r["python"]) if ref else None,
    }


def _f(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, (int, float)):
        return f"{v:.10g}"
    return str(v)


def _signed(v) -> str:
    return "n/a" if v is None else f"{v:+.10g}"


def provenance_lines(s: dict) -> list[str]:
    """What each side ran, and whether the two agree; printed before any number."""
    c, r = s["cur"], s["ref"]
    lines = [f"Scorer (current): sha256 `{c['scorer_sha256']}`; interpreter {c['python']}; wall {_f(c['wall_seconds'])} s."]
    if s["ref"]["commit"] is None:
        return lines
    lines.append(f"Scorer (ref): sha256 `{r['scorer_sha256']}`; interpreter {r['python']}; wall {_f(r['wall_seconds'])} s.")
    if not s["same_scorer"]:
        lines.append("⚠ The two sides were scored by DIFFERENT scorer code (run.py, score.py, an adapter or the sandbox changed between the two commits): every `movement` below compares two scorers' outputs, and a row that moved may have moved with the scorer.")
    if not s["same_python"]:
        lines.append("⚠ The two sides ran on different interpreters; a latency or index-time row is not a like-for-like measurement.")
    return lines


def render(cur: dict, ref: dict | None, records: list[dict], note: str = "") -> str:
    h, rh = cur["header"], (ref["header"] if ref else None)
    s = summary(cur, ref, records)
    lines = ["# Competitive compare: the working tree against a ref", ""]
    lines.append(f"Current: jcm `{h['jcm_commit']}` ({h['jcm_version']}), {h['date']}, runs {h['runs']}, sandbox `{h.get('sandbox')}`, tree dirty {h.get('tree_dirty')}.")
    if rh:
        lines.append(f"Ref: jcm `{rh['jcm_commit']}` ({rh['jcm_version']}), {rh['date']}, runs {rh['runs']}, sandbox `{rh.get('sandbox')}`.")
    else:
        lines.append("Ref: no competitive tier at that ref; every ref cell is n/a.")
    lines.extend(provenance_lines(s))
    lines.append("")
    lines.append("Corpora (current): " + "; ".join(f"`{c['id']}` {c['files']} files, sha256 `{c['sha256']}`" for c in h["corpora"]))
    if rh:
        lines.append("Corpora (ref): " + "; ".join(f"`{c['id']}` {c['files']} files, sha256 `{c['sha256']}`" for c in rh["corpora"]))
    lines.append("Tools (current): " + ", ".join(f"`{p['name']}`@{p['version']}" for p in h["pins"]))
    if note:
        lines.append(note)
    lines.append("")
    lines.append("A competitor's README figure is not on this page. Every number was produced by one of these two runs on its corpus with this tokenizer (cl100k_base); `measured` is the median of the runs; a delta is the row's ratio (or difference) to its own side's jcm; `band` is the current run's; `movement` is `trend.classify` over the two gaps to jcm, judged against that band. Per row, never per total. `n/a` is a value one side did not produce, never 0.")
    lines.append("")
    lines.append("## Our rows (jcodemunch): current minus ref, signed, in the axis's own unit")
    lines.append("")
    lines.append("| axis | corpus | ref measured | current measured | difference | note |")
    lines.append("|---|---|---|---|---|---|")
    for r in records:
        if r["tool"] != JCM:
            continue
        lines.append(f"| {r['axis']} | {r['corpus']} | {_f(r['ref_measured'])} | {_f(r['cur_measured'])} | {_signed(r['difference'])} | {r['note']} |")
    lines.append("")
    lines.append("## Every other row: the gap to jcm on each side")
    lines.append("")
    lines.append("| axis | tool | corpus | ref measured | ref delta | current measured | current delta | band | movement | note |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in records:
        if r["tool"] == JCM:
            continue
        lines.append(f"| {r['axis']} | {r['tool']} | {r['corpus']} | {_f(r['ref_measured'])} | {_f(r['ref_delta'])} | {_f(r['cur_measured'])} | {_f(r['cur_delta'])} | {_f(r['band'])} | {r['movement']} | {r['note']} |")
    lines.append("")
    lines.append("## Counts (of rows on this page, not of measurements)")
    lines.append("")
    lines.append(f"Rows {s['rows']}: jcm rows {s['jcm_rows']}, of which {s['jcm_compared']} have both sides, {s['jcm_zero_difference']} differ by exactly 0 and {s['jcm_moved_past_band']} moved past the current band; other rows {s['other_rows']}, movement " + ", ".join(f"{k} {v}" for k, v in s["movement"].items()) + ".")
    lines.append("")
    return "\n".join(lines)


def findings_row(fid: str, cur: dict, ref: dict | None, records: list[dict], page_path: str, note: str = "") -> str:
    """A docs/competitive/FINDINGS.md row for a dry run, every figure from the summary."""
    s = summary(cur, ref, records)
    c, r = s["cur"], s["ref"]
    tools = ", ".join(f"`{t}`" for t in s["tools"])
    prov = ("same scorer code on both sides" if s["same_scorer"] else "DIFFERENT scorer code on the two sides (the page says so before its first number)")
    interp = (f"interpreter {c['python']} on both sides" if s["same_python"] else f"interpreters {c['python']} (current) and {r['python']} (ref), disclosed on the page")
    return (f"| {fid} | **`/competitive-compare` ran end to end against a ref, and its page, this row's figures included, come from `compare_ref.py`, not from anyone's typing.** "
            f"Working tree at `{c['commit']}` against `{r['commit']}`, adapters {tools} plus jcodemunch, {cur['header']['runs']} runs each side in the container"
            + (f" ({note})" if note else "") + f"; wall {_f(c['wall_seconds'])} s current, {_f(r['wall_seconds'])} s ref; {prov}; {interp}. "
            f"Rows {s['rows']}: {s['jcm_rows']} jcm rows, {s['jcm_compared']} with both sides, {s['jcm_zero_difference']} differing by exactly 0 and {s['jcm_moved_past_band']} moved past the current band; "
            f"the other {s['other_rows']} rows classified as " + ", ".join(f"{k} {v}" for k, v in s["movement"].items()) + f". "
            f"Page: `{page_path}`. | `.claude/commands/competitive-compare.md`; `benchmarks/competitive/compare_ref.py --findings-row` | RECORDED; the full-set form is CF-53's measurement |")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cur", required=True, help="the current tree's result file or its out-dir")
    ap.add_argument("--ref", help="the ref's result file or out-dir; omit when the ref has no competitive tier")
    ap.add_argument("--out", required=True, help="where to write the markdown page")
    ap.add_argument("--note", default="", help="one header line, e.g. the --only filter used")
    ap.add_argument("--findings-row", metavar="ID", help="print a FINDINGS row with this id (e.g. CF-59) instead of the page; the page is still written")
    ap.add_argument("--page-path", default=".claude/state/evidence/competitive_compare.md", help="the path the FINDINGS row names for the page")
    a = ap.parse_args(argv)
    cur = load(Path(a.cur))
    ref = load(Path(a.ref)) if a.ref else None
    records = compare(cur, ref)
    text = render(cur, ref, records, a.note)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(text, encoding="utf-8")
    if a.findings_row:
        print(findings_row(a.findings_row, cur, ref, records, a.page_path, a.note))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
