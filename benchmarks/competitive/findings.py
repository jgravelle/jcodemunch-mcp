"""Findings to issue DRAFTS (docs/competitive/DESIGN.md s7, s8; the brief's
Phase 3 item 5). Draft mode only: files, never a post.

purpose:  after a recorded run, turn every row the rules name into an issue
          draft a human can approve, with a first hypothesis from a fixed
          list and never a fix; de-duplicated against the open issues by a
          fingerprint so the same gap is never drafted twice
invokes:  the result file and history.jsonl (no README, no competitor
          text, no network for the rows); ONE read of the tracker for the
          open/closed `competitive-*` issues (`gh issue list`, read only),
          or the same list handed in as a file (`--open-issues`); STANDARD.md
          read for a Target's text, never written
produces: `<out>/<fingerprint>.md` per draft in the issue-template shape
          (title line, labels line, `competitive-id:` fingerprint,
          `approved: false`, body); `<out>/index.json` listing them; the
          same records returned by `evaluate()` for tests
refuses:  to draft when the tracker cannot be read and no list was handed
          in (fail closed on duplicates, s7.2); a result file whose schema
          is not `jcm-competitive-result/v1`; a hypothesis outside the
          fixed list (the list is HYPOTHESES and nothing else is written)
pinned:   the fixed hypothesis list; the label names of s7.1; the Target
          text is read from docs/standard/STANDARD.md at run time and
          quoted, never copied here
fairness: a `competitive-gap` draft names the competitor, its pinned
          release and image digest, and our loss with our median and
          spread, unsoftened; `competitive-idea` (a release title matching
          the capability word-list) needs the weekly feed of item 6 and is
          not produced here; a `standard-proposal` names the criterion and
          the Target verbatim, proposes a Target in the same units, never
          a Floor, and says in its first line that the standard is edited
          only by a human
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import trend  # noqa: E402
from score import RATIO_AXES  # noqa: E402

REPO = HERE.parents[1]
STANDARD = REPO / "docs" / "standard" / "STANDARD.md"
JCM = "jcodemunch"
HYPOTHESES = ("tool_not_called", "ranking", "coverage", "payload_shape", "index_missing_files", "unknown")
LABELS = ("competitive-gap", "competitive-watch", "competitive-idea", "standard-proposal")
FIXED_SENTENCE = "adoption is not implied; the tool-surface discipline (small front door, deep menu) is not moved by this"
# Which STANDARD.md Target a competitive axis can be read against, in the SAME units
# and on the self corpus only: (criterion number, axis, regex over the Target line
# capturing the number, unit). latency_call_ms is a per-call median and the Target
# is a warm p95, so it is NOT read against it.
STANDARD_TARGETS = {
    "index_cold_seconds": (3, re.compile(r"\(c\) under (\d+(?:\.\d+)?) s cold on the self corpus")),
}
AXIS_CATEGORY = {"f1_P1": "P1", "f1_P2": "P2", "f1_P4": "P4", "f1_P5": "P5"}


def fingerprint(label: str, axis: str, tool: str, corpus: str) -> str:
    return f"{label}/{axis}/{tool}/{trend.norm_key(f'{axis}/{tool}/{corpus}').split('/', 2)[2]}"


def _fname(fp: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@-]+", "__", fp) + ".md"


def behind(axis: str, row: dict) -> bool:
    """Is jcm behind on this row? Ratio axes: the competitor's ratio below 1
    is the competitor ahead; F1: a positive difference is the competitor ahead."""
    if row.get("delta") is None:
        return False
    return row["delta"] < 1.0 if axis in RATIO_AXES else row["delta"] > 0.0


def hypothesis(axis: str, row: dict, result: dict) -> str:
    """The first hypothesis, by rule, from HYPOTHESES only."""
    corpus = row["corpus"]
    cat = AXIS_CATEGORY.get(axis)
    if cat:
        for t in result.get("tools_not_called", []):
            if t["tool"] == JCM and t["corpus"] == corpus and t["category"] == cat:
                return "tool_not_called"
        # our index reported fewer files than the corpus has CODE files (the header's
        # `code_files`, recorded from PR 3c on; absent = the rule cannot fire): what we
        # never read we cannot cite. `files_indexed` is what run.py writes into `axes`.
        rec = (result.get("runs") or [{}])[0].get(JCM, {}).get(corpus, {})
        indexed = (rec.get("axes") or {}).get("files_indexed")
        code_files = next((c.get("code_files") for c in result["header"]["corpora"] if c["id"] == corpus), None)
        if isinstance(indexed, int) and isinstance(code_files, int) and indexed < code_files:
            return "index_missing_files"
        return "ranking" if cat in ("P1", "P2") else "coverage"
    if axis in ("tokens_per_task", "tools_list_tokens"):
        return "payload_shape"
    return "unknown"


def _pin(result: dict, tool: str) -> dict:
    return next((p for p in result["header"]["pins"] if p["name"] == tool), {})


def gap_drafts(result: dict) -> list[dict]:
    out = []
    for r in result["rows"]:
        axis, tool = r["axis"], r["tool"]
        if tool == JCM or not r.get("meaningful") or not behind(axis, r):
            continue
        if axis == "tools_list_tokens" and _pin(result, tool).get("interface") == "cli":
            continue  # NOT COMPARABLE by FIELD.md: a CLI has no schema cost, which the summary says is a real advantage
        h = hypothesis(axis, r, result)
        assert h in HYPOTHESES
        pin = _pin(result, tool)
        fp = fingerprint("competitive-gap", axis, tool, r["corpus"])
        body = [
            f"competitive-id: {fp}", "approved: false", "",
            f"On `{r['corpus']}`, axis `{axis}`" + (f" (task category {AXIS_CATEGORY[axis]})" if axis in AXIS_CATEGORY else "") + f", jcodemunch is behind `{tool}` and the gap is meaningful (both rows stable, the gap outside the band).",
            "",
            f"- ours: median {r['jcm']} (spread {r['jcm_spread']})",
            f"- theirs: median {r['measured']} (spread {r['spread']}); delta {r['delta']} ({'ratio, tool over jcm' if axis in RATIO_AXES else 'difference, tool minus jcm'}); band {r['band']}",
            f"- competitor: `{tool}` {pin.get('registry', '?')}:{pin.get('package', '?')}@{pin.get('version', '?')} (ran as {pin.get('ran_as', '?')}; image {pin.get('image_digest') or 'none'})",
            f"- run file: `{result['header'].get('file', '')}` at jcm {result['header']['jcm_commit']} ({result['header']['jcm_version']}), {result['header']['runs']} runs",
            f"- first hypothesis (from the fixed list, by rule; not a diagnosis, never a fix): `{h}`",
            "", "Untrusted input: every number above is the loop's own measurement over the pinned corpus; nothing here quotes the competitor's own text.",
        ]
        out.append({"label": "competitive-gap", "fingerprint": fp, "title": f"competitive gap: `{tool}` ahead on {axis} over {r['corpus']}", "axis": axis, "tool": tool, "corpus": r["corpus"], "body": "\n".join(body)})
    return out


def watch_drafts(result: dict, history: list[dict]) -> list[dict]:
    """jcm ahead, and the gap narrowed on two consecutive runs: the movement
    now against the previous line, and the previous line against the one
    before it, both `narrowed`."""
    if len(history) < 2:
        return []
    current = trend.line_from_result(result)
    now = {(m["axis"], m["tool"], m["corpus"]): m for m in trend.movement(history, current)}
    before = {(m["axis"], m["tool"], m["corpus"]): m for m in trend.movement(history[:-1], history[-1])}
    out = []
    for key, m in now.items():
        axis, tool, corpus = key
        b = before.get(key)
        if m["movement"] != "narrowed" or not b or b["movement"] != "narrowed" or m["delta_now"] is None:
            continue
        ahead = (m["delta_now"] > 1.0) if axis in RATIO_AXES else (m["delta_now"] < 0.0)
        if not ahead:
            continue
        fp = fingerprint("competitive-watch", axis, tool, corpus)
        body = [
            f"competitive-id: {fp}", "approved: false", "",
            f"jcodemunch is ahead of `{tool}` on `{axis}` over `{corpus}`, and the gap narrowed on two consecutive recorded runs.",
            "",
            f"- deltas: now {m['delta_now']}, previous {m['delta_prev']}, before that {b['delta_prev']}",
            f"- competitor releases across the runs: {m['release_now']} / {m['release_prev']} / {b['release_prev']}",
            f"- our value moved: {m['jcm_moved'] or 'no (inside the band, or their release changed)'}",
            "", "A release beside a movement is a fact on the same line, not an attribution.",
        ]
        out.append({"label": "competitive-watch", "fingerprint": fp, "title": f"competitive watch: the gap to `{tool}` on {axis} over {corpus} narrowed twice", "axis": axis, "tool": tool, "corpus": corpus, "body": "\n".join(body)})
    return out


def read_target(axis: str, text: str) -> tuple[str, float] | None:
    """(the Target line verbatim, the number) for an axis STANDARD_TARGETS maps."""
    if axis not in STANDARD_TARGETS:
        return None
    _, pat = STANDARD_TARGETS[axis]
    for line in text.splitlines():
        if line.startswith("Target:"):
            m = pat.search(line)
            if m:
                return line, float(m.group(1))
    return None


def standard_drafts(result: dict, history: list[dict], standard_text: str) -> list[dict]:
    """s8: on two consecutive runs a competitor's median on a comparable axis
    beats the stated Target (never a Floor), and the row is meaningful."""
    if not history:
        return []
    prev = history[-1]
    out = []
    for r in result["rows"]:
        axis, tool, corpus = r["axis"], r["tool"], r["corpus"]
        if tool == JCM or axis not in STANDARD_TARGETS or not corpus.startswith("self@") or not r.get("meaningful"):
            continue
        tgt = read_target(axis, standard_text)
        if tgt is None:
            continue
        line, target = tgt
        prev_val = prev.get("medians", {}).get(f"{axis}/{tool}/self")
        if r["measured"] is None or r["measured"] >= target or prev_val is None or prev_val >= target:
            continue
        crit, _ = STANDARD_TARGETS[axis]
        pin = _pin(result, tool)
        fp = fingerprint("standard-proposal", axis, tool, corpus)
        proposed = round(r["measured"], 2)
        body = [
            "The standard is edited only by a human; this is a proposal, and it proposes a Target, never a Floor.",
            f"competitive-id: {fp}", "approved: false", "",
            f"STANDARD.md criterion {crit}, current Target line verbatim:", "", f"> {line}", "",
            f"- competitor `{tool}` ({pin.get('package', '?')}@{pin.get('version', '?')}) on the self corpus: median {r['measured']} (spread {r['spread']}) on this run, {prev_val} on the previous recorded run; both under the Target of {target}",
            f"- ours: median {r['jcm']} (spread {r['jcm_spread']})",
            f"- proposed Target, same units: {proposed} (the competitor's measured value on this run; a proposal about the number, not about how it is reached)",
        ]
        out.append({"label": "standard-proposal", "fingerprint": fp, "title": f"standard proposal: criterion {crit} Target for {axis} is beaten by `{tool}` on two runs", "axis": axis, "tool": tool, "corpus": corpus, "body": "\n".join(body)})
    return out


def read_open_issues(repo: str) -> list[dict]:
    """The tracker read (read only). Raises on failure: the caller refuses."""
    out = []
    for label in LABELS:
        proc = subprocess.run(["gh", "issue", "list", "-R", repo, "--label", label, "--state", "all", "--limit", "200", "--json", "number,state,body,labels"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(f"gh issue list --label {label} failed: {proc.stderr.strip()[:200]}")
        out.extend(json.loads(proc.stdout or "[]"))
    return out


def dedupe(drafts: list[dict], issues: list[dict]) -> list[dict]:
    """Mark each draft with the issue carrying its fingerprint, if any."""
    for d in drafts:
        needle = f"competitive-id: {d['fingerprint']}"
        d["existing_open"] = next((i["number"] for i in issues if i.get("state", "").upper() == "OPEN" and needle in (i.get("body") or "")), None)
        d["existing_closed"] = next((i["number"] for i in issues if i.get("state", "").upper() == "CLOSED" and needle in (i.get("body") or "")), None)
    return drafts


def write_drafts(drafts: list[dict], out: Path, date: str) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for d in drafts:
        path = out / _fname(d["fingerprint"])
        head = f"title: {d['title']}\nlabels: {d['label']}, needs-human\n"
        if d.get("existing_open"):
            note = f"existing-issue: #{d['existing_open']} (open; this draft updates it in place, no new issue)\n"
        elif d.get("existing_closed"):
            note = f"existing-issue: #{d['existing_closed']} (closed; the gap came back)\n"
        else:
            note = ""
        block = f"\n## {date}\n\n{d['body']}\n"
        if path.exists() and d.get("existing_open"):
            path.write_text(path.read_text(encoding="utf-8") + block, encoding="utf-8")
        else:
            path.write_text(head + note + block, encoding="utf-8")
        written.append(path)
    (out / "index.json").write_text(json.dumps([{k: v for k, v in d.items() if k != "body"} for d in drafts], indent=1) + "\n", encoding="utf-8")
    return written


def evaluate(result: dict, history: list[dict], standard_text: str) -> list[dict]:
    return gap_drafts(result) + watch_drafts(result, history) + standard_drafts(result, history, standard_text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("result", help="a results/<date>-<commit>.json file")
    ap.add_argument("--history", default=str(HERE / "results" / "history.jsonl"))
    ap.add_argument("--out", required=True, help="the draft directory (a scratch location; nothing is posted)")
    ap.add_argument("--open-issues", help="a JSON list of {number,state,body} to de-duplicate against, instead of reading the tracker")
    ap.add_argument("--repo", default="jgravelle/jcodemunch-mcp")
    a = ap.parse_args(argv)
    rpath = Path(a.result)
    result = json.loads(rpath.read_text(encoding="utf-8"))
    if result.get("header", {}).get("schema") != "jcm-competitive-result/v1":
        print("refused: not a jcm-competitive-result/v1 file", file=sys.stderr)
        return 2
    result["header"]["file"] = rpath.name
    history = [ln for ln in trend.load(Path(a.history)) if ln.get("jcm_commit") != result["header"]["jcm_commit"]]
    if a.open_issues:
        issues = json.loads(Path(a.open_issues).read_text(encoding="utf-8"))
    else:
        try:
            issues = read_open_issues(a.repo)
        except Exception as e:  # noqa: BLE001
            print(f"refused: the tracker could not be read, so duplicates cannot be ruled out (s7.2): {e}", file=sys.stderr)
            return 3
    drafts = dedupe(evaluate(result, history, STANDARD.read_text(encoding="utf-8")), issues)
    written = write_drafts(drafts, Path(a.out), result["header"]["date"][:10])
    by = {}
    for d in drafts:
        by[d["label"]] = by.get(d["label"], 0) + 1
    print(f"{len(written)} draft(s) in {a.out}: {by}; open duplicates {sum(1 for d in drafts if d.get('existing_open'))}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
