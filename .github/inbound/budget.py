"""Budget pre-flight for headless inbound jobs (docs/inbound/POLICY.md section 7).

purpose:  decline a run BEFORE it starts when the day's count, the
          concurrent count, the open agent-PR count, or the day's cost
          would exceed the policy table; never mid-run
invokes:  `gh run list`, `gh pr list` (read only); the ledger directory
          when one is checked out
produces: a JSON verdict on stdout; exit 0 to proceed, exit 78 to skip
refuses:  to run a job it has no row for; to lower a ceiling from the
          command line (the table is edited in a PR)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path

EXIT_SKIP = 78

# POLICY section 7, verbatim. Keys are workflow file stems.
BUDGETS = {
    "inbound-triage": {
        "runs_per_day": 20,
        "cost_per_run_usd": 5.0,
        "turns": 12,
        "timeout_min": 10,
    },
    "inbound-fix": {
        "runs_per_day": 3,
        "cost_per_run_usd": 25.0,
        "turns": 60,
        "timeout_min": 60,
    },
    "inbound-depeval": {
        "runs_per_day": 4,
        "cost_per_run_usd": 10.0,
        "turns": 30,
        "timeout_min": 45,
    },
    "inbound-bench-full": {
        "runs_per_day": 4,
        "cost_per_run_usd": 0.0,
        "turns": 0,
        "timeout_min": 90,
    },
    "inbound-sweep": {
        "runs_per_day": 1,
        "cost_per_run_usd": 0.0,
        "turns": 0,
        "timeout_min": 15,
    },
    "inbound-digest": {
        "runs_per_day": 1,
        "cost_per_run_usd": 2.0,
        "turns": 8,
        "timeout_min": 15,
    },
    # The competitive loop (docs/competitive/DESIGN.md s9.2): no model, no cost.
    "competitive-run": {
        "runs_per_day": 1,
        "cost_per_run_usd": 0.0,
        "turns": 0,
        "timeout_min": 240,
    },
    "competitive-feed": {
        "runs_per_day": 1,
        "cost_per_run_usd": 0.0,
        "turns": 0,
        "timeout_min": 15,
    },
    "competitive-post": {
        "runs_per_day": 1,
        "cost_per_run_usd": 0.0,
        "turns": 0,
        "timeout_min": 15,
    },
}
DAILY_COST_USD = 60.0
MAX_OPEN_AGENT_PRS = 3


def evaluate(
    job: str,
    runs_today: int,
    open_agent_prs: int,
    cost_today_usd: float,
    manual_dispatch: bool = False,
) -> tuple[bool, list[str]]:
    """Pure decision. ``runs_today`` counts runs of this job already started
    today (the current run excluded). ``manual_dispatch`` does not lift any
    ceiling; it is recorded so the digest can say a human asked."""
    if job not in BUDGETS:
        return False, [f"no budget row for job {job!r}; add one to POLICY section 7"]
    b = BUDGETS[job]
    reasons = []
    if runs_today >= b["runs_per_day"]:
        reasons.append(f"runs_per_day: {runs_today} of {b['runs_per_day']} used")
    if cost_today_usd >= DAILY_COST_USD:
        reasons.append(
            f"daily_cost: {cost_today_usd:.2f} of {DAILY_COST_USD:.2f} USD used"
        )
    if job == "inbound-fix" and open_agent_prs >= MAX_OPEN_AGENT_PRS:
        reasons.append(f"open_agent_prs: {open_agent_prs} of {MAX_OPEN_AGENT_PRS}")
    return (not reasons), reasons


def _gh_json(args: list[str]) -> list:
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=60, encoding="utf-8"
        )
        if proc.returncode != 0:
            return []
        return json.loads(proc.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


def runs_today(job: str, repo: str | None, today: str) -> int:
    args = [
        "run",
        "list",
        "--workflow",
        f"{job}.yml",
        "--created",
        f">={today}",
        "--json",
        "databaseId",
        "--limit",
        "200",
    ]
    if repo:
        args += ["-R", repo]
    ceiling = BUDGETS.get(job, {}).get("runs_per_day", 0)
    return count_working_runs(
        other_runs(_gh_json(args), os.environ.get("GITHUB_RUN_ID")),
        lambda r: _run_steps(str(r.get("databaseId")), repo),
        ceiling,
    )


def count_working_runs(runs: list, steps_of, ceiling: int) -> int:
    """Runs that did work, reading each run's steps lazily and stopping at
    the ceiling: the verdict only needs to know whether the ceiling is
    reached, and each read is one `gh run view` (review, finding 2: the
    triage runner fires every 15 minutes and a declined day could cost
    every later gate up to 200 reads)."""
    n = 0
    for r in runs:
        if ceiling and n >= ceiling:
            break
        if run_did_work(steps_of(r)):
            n += 1
    return n


# Steps every job has whether or not its gate let it through. A run in which
# nothing else succeeded declined at its gate and spent no budget.
_PLUMBING_STEP_PREFIXES = (
    "Set up job", "Run actions/", "Post ", "Complete job",
    "kill switch", "switch-reading token", "gate", "audit record",
)  # "Post " covers every action's post step, the token step's included (found on run 33939144859)


def _run_steps(run_id: str, repo: str | None) -> list:
    args = ["run", "view", run_id, "--json", "jobs", "--jq", "[.jobs[].steps[] | {name, conclusion}]"]
    if repo:
        args += ["-R", repo]
    return _gh_json(args)


def run_did_work(steps: list) -> bool:
    """True when any step outside the plumbing set RAN (any conclusion but
    skipped or none: a failed model step spent its budget too; review,
    finding 3). UNKNOWN (no steps readable) counts as work: the budget
    fails closed."""
    if not steps:
        return True
    for s in steps:
        name = str(s.get("name") or "")
        if s.get("conclusion") not in ("skipped", None) and not name.startswith(_PLUMBING_STEP_PREFIXES):
            return True
    return False


def other_runs(runs: list, current_run_id: str | None) -> list:
    """Every run but the one that is asking. The first live sweep
    (2026-09-05, run 33936406280) counted itself and declined with
    "runs_per_day: 1 of 1 used": a job allowed one run a day could never
    run."""
    return [r for r in runs if str(r.get("databaseId")) != str(current_run_id or "")]


def count_other_runs(runs: list, current_run_id: str | None) -> int:
    return len(other_runs(runs, current_run_id))


def open_agent_prs(repo: str | None) -> int:
    args = [
        "pr",
        "list",
        "--state",
        "open",
        "--label",
        "agent-authored",
        "--json",
        "number",
        "--limit",
        "100",
    ]
    if repo:
        args += ["-R", repo]
    return len(_gh_json(args))


def cost_today(ledger_dir: Path | None, today: str) -> float:
    if not ledger_dir:
        return 0.0
    total = 0.0
    month = today[:7]
    f = Path(ledger_dir) / f"{month}.jsonl"
    if not f.exists():
        return 0.0
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (rec.get("recorded_at") or "")[:10] != today:
            continue
        c = rec.get("cost_usd") or 0.0
        job = rec.get("job") or ""
        ceiling = BUDGETS.get(job, {}).get("cost_per_run_usd", 0.0)
        # POLICY section 7: a run over its ceiling counts double against the day.
        total += c * (2.0 if ceiling and c > ceiling else 1.0)
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--job", required=True, choices=sorted(BUDGETS))
    ap.add_argument("--repo", default=None)
    ap.add_argument("--ledger-dir", type=Path, default=None)
    ap.add_argument("--manual", action="store_true")
    ap.add_argument(
        "--today", default=_dt.datetime.now(_dt.timezone.utc).date().isoformat()
    )
    args = ap.parse_args(argv)
    r = runs_today(args.job, args.repo, args.today)
    p = open_agent_prs(args.repo) if args.job == "inbound-fix" else 0
    c = cost_today(args.ledger_dir, args.today)
    ok, reasons = evaluate(args.job, r, p, c, args.manual)
    print(
        json.dumps(
            {
                "job": args.job,
                "runs_today": r,
                "open_agent_prs": p,
                "cost_today_usd": round(c, 2),
                "ok": ok,
                "reasons": reasons,
                "limits": {
                    **BUDGETS[args.job],
                    "daily_cost_usd": DAILY_COST_USD,
                    "max_open_agent_prs": MAX_OPEN_AGENT_PRS,
                },
            }
        )
    )
    return 0 if ok else EXIT_SKIP


if __name__ == "__main__":
    sys.exit(main())
