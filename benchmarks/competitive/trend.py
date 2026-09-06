"""Trend tracking over results/history.jsonl (docs/competitive/DESIGN.md s6;
the brief's Phase 3 item 4): the summary's *Movement* section.

purpose:  say, per (axis, tool, corpus) row, how the gap to jcm moved
          between this run, the previous one and the first one, with the
          competitor's release on each of the three runs beside it, so a
          movement that coincides with their release is visible without
          being attributed; and name a row whose jcm value moved while the
          competitor's release did not as OUR regression or improvement
invokes:  nothing outside the process: history.jsonl's lines (one per
          recorded run: medians, and from PR 3b on, bands) and the current
          result's rows
produces: `movement(history, current)`: one record per row of the current
          run, and `render()` the markdown section run.py appends
refuses:  to classify a movement without a band on the current run (a
          history line older than this module carries medians only: the
          row says `no band recorded` instead of inventing one)
pinned:   nothing of its own; the history file is append-only under
          `--record` and every line names its jcm commit and the pins
fairness: the gap is measured minus jcm in the axis's own units, the same
          quantity the band judges; `flipped` is a sign change of that gap,
          `widened`/`narrowed` its magnitude, `unchanged` within the band.
          A competitor's release beside a movement is a fact, never a
          cause; the section says so in its header line.
"""

from __future__ import annotations

import json
from pathlib import Path

from score import DIFF_AXES, RATIO_AXES

JCM = "jcodemunch"
CLASSES = ("widened", "narrowed", "flipped", "unchanged")


def norm_key(key: str) -> str:
    """`axis/tool/self@<commit>` -> `axis/tool/self`: the self corpus is this
    tree at the running commit, so its id changes every run and would make
    every self row a `first run` forever (found on the first render)."""
    axis, tool, corpus = key.split("/", 2)
    return f"{axis}/{tool}/self" if corpus.startswith("self@") else key


def _normalised(line: dict) -> dict:
    out = dict(line)
    for field in ("medians", "bands", "gaps"):
        if field in line:
            out[field] = {norm_key(k): v for k, v in line[field].items()}
    return out


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [_normalised(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def line_from_result(result: dict) -> dict:
    """The history line for one result (what run.py appends under --record):
    medians, bands and the gap for every comparable row."""
    h = result["header"]
    line = {"date": h["date"], "jcm_commit": h["jcm_commit"], "jcm_version": h["jcm_version"],
            "wall_seconds": h.get("wall_seconds"),
            "pins": {p["name"]: p["version"] for p in h["pins"]},
            "medians": {}, "bands": {}, "gaps": {}}
    for r in result["rows"]:
        key = norm_key(f"{r['axis']}/{r['tool']}/{r['corpus']}")
        if r["measured"] is not None:
            line["medians"][key] = r["measured"]
        if r.get("band") is not None:
            line["bands"][key] = r["band"]
        if r["tool"] != JCM and r.get("measured") is not None and r.get("jcm") is not None:
            line["gaps"][key] = round(r["measured"] - r["jcm"], 6)
    return line


def _gap(line: dict, key: str) -> float | None:
    if "gaps" in line and key in line["gaps"]:
        return line["gaps"][key]
    axis, tool, corpus = key.split("/", 2)
    m = line.get("medians", {})
    jkey = f"{axis}/{JCM}/{corpus}"
    if key in m and jkey in m:
        return m[key] - m[jkey]
    return None


def _delta(axis: str, line: dict, key: str) -> float | None:
    """The reported delta (ratio or difference) recomputed from medians."""
    _, tool, corpus = key.split("/", 2)
    m = line.get("medians", {})
    jkey = f"{axis}/{JCM}/{corpus}"
    if key not in m or jkey not in m:
        return None
    if axis in RATIO_AXES:
        return round(m[key] / m[jkey], 4) if m[jkey] else None
    return round(m[key] - m[jkey], 4)


def classify(gap_now: float, gap_prev: float, band_now: float | None) -> str:
    if band_now is None:
        return "no band recorded"
    if abs(gap_now - gap_prev) <= band_now:
        return "unchanged"
    if gap_now * gap_prev < 0:
        return "flipped"
    return "widened" if abs(gap_now) > abs(gap_prev) else "narrowed"


def movement(history: list[dict], current: dict) -> list[dict]:
    """One record per comparable row of `current` (a history line). `history`
    is every earlier line, oldest first; the current line is not in it."""
    if not history:
        return []
    prev, first = history[-1], history[0]
    out = []
    for key in sorted(current.get("medians", {})):
        axis, tool, corpus = key.split("/", 2)
        if tool == JCM or axis not in RATIO_AXES + DIFF_AXES:
            continue
        g_now = _gap(current, key)
        if g_now is None:
            continue
        g_prev, g_first = _gap(prev, key), _gap(first, key)
        band_now = current.get("bands", {}).get(key)
        rec = {"axis": axis, "tool": tool, "corpus": corpus,
               "delta_now": _delta(axis, current, key), "delta_prev": _delta(axis, prev, key), "delta_first": _delta(axis, first, key),
               "release_now": current.get("pins", {}).get(tool), "release_prev": prev.get("pins", {}).get(tool), "release_first": first.get("pins", {}).get(tool),
               "band": band_now,
               "movement": "first run" if g_prev is None else classify(g_now, g_prev, band_now),
               "jcm_moved": None}
        # our own value moved while their release did not: ours to own, either way
        jkey = f"{axis}/{JCM}/{corpus}"
        j_now, j_prev = current.get("medians", {}).get(jkey), prev.get("medians", {}).get(jkey)
        if j_now is not None and j_prev is not None and rec["release_now"] == rec["release_prev"] and band_now is not None and abs(j_now - j_prev) > band_now:
            better = (j_now < j_prev) if axis in RATIO_AXES else (j_now > j_prev)
            rec["jcm_moved"] = "our improvement" if better else "our regression"
        out.append(rec)
    return out


def _f(v) -> str:
    return "n/a" if v is None else (f"{v:.4g}" if isinstance(v, (int, float)) else str(v))


def render(records: list[dict], history_len: int) -> str:
    lines = ["## Movement", ""]
    if history_len == 0 or not records:
        lines.append("First recorded run: nothing to compare against yet (history.jsonl has no earlier line for this table).")
        lines.append("")
        return "\n".join(lines)
    lines.append(f"Per row: the delta now, on the previous recorded run, and on the first ({history_len} earlier line(s) in history.jsonl); the gap's movement judged against this run's band; the competitor's release on each of the three runs beside it. A release beside a movement is a fact on the same line, not an attribution. A row marked `our improvement`/`our regression` is one where jcm's own value moved past the band while the competitor's release did not.")
    lines.append("")
    lines.append("| axis | tool | corpus | now | previous | first | movement | release now / previous / first | jcm |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in records:
        lines.append(f"| {r['axis']} | {r['tool']} | {r['corpus']} | {_f(r['delta_now'])} | {_f(r['delta_prev'])} | {_f(r['delta_first'])} | {r['movement']} | {_f(r['release_now'])} / {_f(r['release_prev'])} / {_f(r['release_first'])} | {r['jcm_moved'] or ''} |")
    lines.append("")
    return "\n".join(lines)
