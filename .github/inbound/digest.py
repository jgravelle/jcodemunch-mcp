"""Weekly digest numbers from the ledger, with no model (docs/inbound/DESIGN.md
section 6). The model, when it runs at all, renders one paragraph from the
JSON this prints; every number in the issue comes from here.

purpose:  count the week's records by job, category and outcome; list the
          escalations, the failures, the declined runs, the drafts awaiting
          approval, the budget consumed per day, the kill-switch flips, and
          the streak table; render the issue body
invokes:  the ledger checkout (ledger/*.jsonl, streaks.json, drafts/); the
          sweep summaries; `gh` reads for open drafts' items when run with
          --repo
produces: JSON (--json) or the Markdown issue body (--markdown); the title
          `inbound digest <ISO week>`
refuses:  to invent a number for a field the ledger cannot support: an
          unmeasured field is `null` and rendered as "not recorded", never 0
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ledger import as_text  # noqa: E402  sibling module; one spelling per value (#690)

RUNS_URL = "https://github.com/{repo}/actions/runs/{run_id}"


def iso_week(day: _dt.date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def week_bounds(day: _dt.date) -> tuple[_dt.date, _dt.date]:
    start = day - _dt.timedelta(days=day.weekday())
    return start, start + _dt.timedelta(days=7)


def read_ledger(ledger_dir: Path) -> list[dict]:
    rows = []
    for p in sorted(Path(ledger_dir).glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"_malformed": line[:200]})
    return rows


def _when(r: dict) -> _dt.datetime | None:
    # `recorded_at` is the POLICY 6.1 field ledger.py writes.
    s = r.get("recorded_at") or r.get("timestamp")
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def in_week(rows: list[dict], start: _dt.date, end: _dt.date) -> list[dict]:
    out = []
    for r in rows:
        t = _when(r)
        if t is None:
            continue
        if start <= t.date() < end:
            out.append(r)
    return out


def summarise(rows: list[dict], streaks: dict, drafts_pending: list[str], repo: str, sweep_summary: dict | None = None) -> dict:
    """`sweep_summary` is the last sweep's `sweep-summary.json` (DESIGN 6
    step 4: the needs-human items older than 7 days live there, not on the
    ledger branch); None means the digest could not read one, which is
    rendered as "not recorded", never as an empty list."""
    by_job_outcome: dict[str, Counter] = defaultdict(Counter)
    by_category_outcome: dict[str, Counter] = defaultdict(Counter)
    escalated, failed, declined = [], [], []
    cost_by_day: dict[str, float] = defaultdict(float)
    cost_unknown = 0
    switch_flips = []
    last_state: str | None = None
    malformed = 0
    for r in sorted(rows, key=lambda x: (_when(x) or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc))):
        if "_malformed" in r:
            malformed += 1
            continue
        job, outcome = r.get("job", "?"), r.get("outcome", "?")
        by_job_outcome[job][outcome] += 1
        cat = ((r.get("classification") or {}).get("category")) or "none"
        by_category_outcome[cat][outcome] += 1
        link = RUNS_URL.format(repo=repo, run_id=r.get("run_id", "")) if r.get("run_id") else None
        item = {"job": job, "item": r.get("item"), "decision": r.get("decision"), "run": link}
        if outcome == "escalated":
            escalated.append(item)
        elif outcome == "failed":
            failed.append(item)
        elif outcome == "skipped":
            declined.append(item)
        t = _when(r)
        cost = r.get("cost_usd")
        if isinstance(cost, (int, float)) and t is not None:
            cost_by_day[t.date().isoformat()] += float(cost)
        elif outcome in ("acted", "drafted", "escalated", "failed"):
            cost_unknown += 1
        # records written before #690 carry a boolean; read them in one spelling
        state = as_text(r.get("kill_switch_state"))
        if state is not None and state != "n/a":
            if last_state is not None and state != last_state:
                switch_flips.append({"at": r.get("recorded_at"), "from": last_state, "to": state, "job": job})
            last_state = state
    return {
        "by_job_outcome": {k: dict(v) for k, v in by_job_outcome.items()},
        "by_category_outcome": {k: dict(v) for k, v in by_category_outcome.items()},
        "escalated": escalated,
        "failed": failed,
        "declined": declined,
        "cost_by_day_usd": dict(sorted(cost_by_day.items())),
        "runs_with_no_cost_recorded": cost_unknown,
        "kill_switch_flips": switch_flips,
        "streaks": streaks,
        "drafts_awaiting_approval": drafts_pending,
        "malformed_ledger_lines": malformed,
        "records": sum(sum(c.values()) for c in by_job_outcome.values()),
        "stale_needs_human": (sweep_summary or {}).get("stale_needs_human") if sweep_summary is not None else None,
        "last_sweep_at": (sweep_summary or {}).get("ran_at") if sweep_summary is not None else None,
    }


def pending_drafts(ledger_root: Path) -> list[str]:
    d = Path(ledger_root) / "drafts"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        if "approved: true" not in text.split("---", 2)[1] if text.startswith("---") else True:
            out.append(p.name)
    return out


def _table(counter_map: dict[str, dict], first: str) -> list[str]:
    outcomes = sorted({o for c in counter_map.values() for o in c})
    if not counter_map:
        return ["none"]
    lines = ["| " + first + " | " + " | ".join(outcomes) + " |", "|---|" + "---|" * len(outcomes)]
    for k in sorted(counter_map):
        lines.append("| " + k + " | " + " | ".join(str(counter_map[k].get(o, 0)) for o in outcomes) + " |")
    return lines


def _items(items: list[dict]) -> list[str]:
    if not items:
        return ["none"]
    out = []
    for i in items:
        link = f" ([run]({i['run']}))" if i.get("run") else ""
        out.append(f"- `{i['job']}` #{i.get('item')}: {i.get('decision') or 'no decision recorded'}{link}")
    return out


def render(week: str, s: dict, repo: str, ledger_branch_url: str, prose: str | None = None) -> str:
    L = [f"# inbound digest {week}", ""]
    if prose:
        L += [prose.strip(), ""]
    L += [f"{s['records']} audit records this week"
          + (f"; {s['malformed_ledger_lines']} malformed ledger lines skipped" if s["malformed_ledger_lines"] else "") + ".", ""]
    L += ["## Handled, by job and outcome", ""] + _table(s["by_job_outcome"], "job") + [""]
    L += ["## Handled, by category and outcome", ""] + _table(s["by_category_outcome"], "category") + [""]
    L += ["## Escalated (needs a human)", ""] + _items(s["escalated"]) + [""]
    L += ["## Job failures", ""] + _items(s["failed"]) + [""]
    L += ["## Declined runs (kill switch, budget, pre-flight)", ""] + _items(s["declined"]) + [""]
    L += ["## needs-human older than 7 days (from the last sweep)", ""]
    stale = s.get("stale_needs_human")
    if stale is None:
        L += ["not recorded (no sweep summary readable)"]
    elif stale:
        L += [f"- #{n}" for n in stale] + ["", f"as of the sweep at {s.get('last_sweep_at') or 'unknown'}"]
    else:
        L += [f"none as of the sweep at {s.get('last_sweep_at') or 'unknown'}"]
    L += ["", "## Drafts awaiting approval", ""]
    L += [f"- [{n}]({ledger_branch_url}/drafts/{n})" for n in s["drafts_awaiting_approval"]] or ["none"]
    L += ["", "## Budget consumed per day (USD)", ""]
    if s["cost_by_day_usd"]:
        L += [f"- {d}: {c:.2f}" for d, c in s["cost_by_day_usd"].items()]
    else:
        L += ["not recorded"]
    if s["runs_with_no_cost_recorded"]:
        L += [f"- {s['runs_with_no_cost_recorded']} model runs carry no cost figure (the action does not report one); the daily ceiling is enforced by run count and the per-run ceiling in POLICY section 7"]
    L += ["", "## Kill-switch flips", ""]
    L += [f"- {f['at']}: {f['from']} -> {f['to']} (seen by `{f['job']}`)" for f in s["kill_switch_flips"]] or ["none seen between consecutive records"]
    L += ["", "## Graduation streaks (POLICY section 9)", ""]
    if s["streaks"]:
        L += ["| category | unedited posts in a row | first | last | note |", "|---|---|---|---|---|"]
        for cat, row in sorted(s["streaks"].items()):
            L.append(f"| {cat} | {row.get('count', 0)} | {row.get('first') or ''} | {row.get('last') or ''} | {row.get('reset_reason') or ''} |")
    else:
        L += ["no posts counted yet"]
    L += ["", "Every number above is computed by `.github/inbound/digest.py` from the `inbound-ledger` branch; the model wrote at most the opening paragraph. Nothing here merges, closes, or enables anything."]
    return "\n".join(L) + "\n"


_NUMBER_WORDS = re.compile(
    r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
    r"thousand|dozen|none)\b",  # not `no`/`once`/`half`/`twice`: ordinary prose (review round 2, note 1)
    re.I,
)
_NUMERIC_TOKEN = re.compile(r"\d{4}-\d{2}-\d{2}|\d{4}-W\d{2}|\d+(?:\.\d+)?")
PROSE_MAX_CHARS = 1200


def _scalar_tokens(obj) -> set[str]:
    """Every number in the JSON as the token a paragraph would carry: an
    int or float scalar, a numeric or date-shaped string scalar, and a
    numeric or date-shaped dict KEY (`cost_by_day_usd` is keyed by date).
    A run URL or a `recorded_at` timestamp contributes NOTHING: a digit-run
    that is merely a substring of one is not a number the code computed
    (review round 1, finding 1: `45` passed via a run id, `3` via a
    date)."""
    out: set[str] = set()
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        out.add(str(obj))
        if isinstance(obj, float) and obj == int(obj):
            out.add(str(int(obj)))
        return out
    if isinstance(obj, str):
        if _NUMERIC_TOKEN.fullmatch(obj):
            out.add(obj)
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and _NUMERIC_TOKEN.fullmatch(k):
                out.add(k)
            out |= _scalar_tokens(v)
        return out
    if isinstance(obj, (list, tuple)):
        for v in obj:
            out |= _scalar_tokens(v)
    return out


def prose_admissible(prose: str, numbers: dict) -> tuple[bool, str]:
    """The model's paragraph is admitted only when every numeric token in
    it (a whole number, a decimal, a date, an ISO week) is a scalar value
    or key of the JSON it was given, and it spells no number in words; a
    number the JSON does not carry is a number the model invented."""
    allowed = _scalar_tokens(numbers)
    for n in sorted(set(_NUMERIC_TOKEN.findall(prose))):
        if n not in allowed:
            return False, f"paragraph carries {n}, which the numbers do not"
    m = _NUMBER_WORDS.search(prose)
    if m:
        return False, f"paragraph spells a number in words ({m.group(0)!r}); digits only"
    if len(prose.strip()) > PROSE_MAX_CHARS:
        return False, f"paragraph over {PROSE_MAX_CHARS} characters"
    return True, "admitted"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger-root", type=Path, required=True, help="checkout of inbound-ledger")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--week-of", default=None, help="ISO date inside the week to report (default: yesterday, UTC)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", type=Path, default=None)
    ap.add_argument("--prose", type=Path, default=None, help="the model's paragraph, if any")
    ap.add_argument("--render-only", action="store_true", help="render from --numbers instead of reading the ledger")
    ap.add_argument("--numbers", type=Path, default=None)
    ap.add_argument("--sweep-summary", type=Path, default=None, help="the last sweep's sweep-summary.json (needs-human older than 7 days)")
    args = ap.parse_args(argv)
    if args.render_only:
        s = json.loads(args.numbers.read_text(encoding="utf-8"))
        prose = args.prose.read_text(encoding="utf-8") if args.prose and args.prose.exists() else None
        ok, why = prose_admissible(prose, s) if prose else (False, "no paragraph")
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(
            render(s["week"], s, args.repo, f"https://github.com/{args.repo}/blob/inbound-ledger", prose if ok else None),
            encoding="utf-8", newline="\n",
        )
        print(json.dumps({"title": s["title"], "prose": why}, sort_keys=True))
        return 0
    day = _dt.date.fromisoformat(args.week_of) if args.week_of else (_dt.datetime.now(_dt.timezone.utc).date() - _dt.timedelta(days=1))
    start, end = week_bounds(day)
    rows = in_week(read_ledger(args.ledger_root / "ledger"), start, end)
    sp = args.ledger_root / "streaks.json"
    streaks = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    sweep = None
    if args.sweep_summary and args.sweep_summary.exists():
        try:
            sweep = json.loads(args.sweep_summary.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            sweep = None
    s = summarise(rows, streaks, pending_drafts(args.ledger_root), args.repo, sweep)
    s["week"] = iso_week(day)
    s["window"] = [start.isoformat(), end.isoformat()]
    s["title"] = f"inbound digest {s['week']}"
    if args.json:
        print(json.dumps(s, sort_keys=True))
    if args.markdown:
        prose = args.prose.read_text(encoding="utf-8") if args.prose and args.prose.exists() else None
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(
            render(s["week"], s, args.repo, f"https://github.com/{args.repo}/blob/inbound-ledger", prose),
            encoding="utf-8", newline="\n",
        )
    if not args.json and not args.markdown:
        print(s["title"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
