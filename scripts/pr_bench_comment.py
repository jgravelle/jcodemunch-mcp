"""One PR comment: criterion, Floor, base value, PR value, delta, verdict (DESIGN stage 4).

`python scripts/pr_bench_comment.py --base-ref origin/main --results harness/results/latest.json
    --benchmark BENCH.json --pr N [--post] [--summary FILE]`

Base values come from the artifacts COMMITTED on the base ref
(`harness/results/latest.json` for the self-latency Floors,
`benchmarks/jcm_reference.json` for the token totals); PR values from this
run. Floors and criteria come from `harness/thresholds.json` through the
loader; nothing here is typed. The table is also appended to the step
summary, so a fork PR (whose token cannot comment) still shows it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from harness import thresholds as T  # noqa: E402

MARKER = "<!-- harness-bench-comment -->"


def _git_show(ref: str, path: str) -> dict | None:
    p = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=REPO,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return None


def _fmt(v):
    if v is None:
        return "—"
    return f"{v:.4g}" if isinstance(v, float) else str(v)


def _delta(base, pr):
    if isinstance(base, (int, float)) and isinstance(pr, (int, float)):
        d = pr - base
        return f"{d:+.4g}" if isinstance(d, float) else f"{d:+d}"
    return "—"


def is_run_of(results: dict, head_sha: str) -> bool:
    """True when `results` was measured on the commit `head_sha` names.

    The latency rows are a measurement of ONE tree. A result file that was not
    produced on HEAD is some other tree's run: the artifact committed on the
    base ref (a bench run without `--write-results` leaves the tracked file
    alone, and the token job never runs the bench at all), or one from an
    older weekly result. Rendered as this PR's column it read base = PR, delta
    `+0`, on every PR (workflow FINDINGS W-52). An absent or unreadable stamp
    is not a run of this tree.
    """
    commit = str((results.get("env") or {}).get("commit") or "")
    return len(commit) >= 7 and commit != "unknown" and head_sha.startswith(commit)


def _box(results: dict | None) -> tuple | None:
    env = (results or {}).get("env") or {}
    box = (env.get("runner"), str(env.get("os") or "").split("-")[0], env.get("cpus"))
    return box if all(box) else None


def comparable(base_results: dict | None, pr_results: dict) -> bool:
    """A timing delta means something only between runs on the same kind of
    box (runner, OS family, CPU count); an unknown side is not comparable."""
    base, pr = _box(base_results), _box(pr_results)
    return base is not None and base == pr


def _latency(results: dict | None) -> dict:
    """The self-latency ids of a result. A FAILED step keeps its numbers under
    `measurement` (LEDGER L-72), and those are the rows a reader most needs."""
    lat = (results or {}).get("artifacts", {}).get("self_latency", {}) or {}
    measured = lat.get("measurement")
    return measured if isinstance(measured, dict) else lat


def _box_text(results: dict | None) -> str:
    box = _box(results)
    if box is None:
        return "an unrecorded box"
    return f"{box[0]} {box[1]}, {box[2]} cpus"


def rows(
    base_results: dict | None,
    pr_results: dict,
    base_ref_json: dict | None,
    bench: list | None,
    *,
    latency: bool = True,
    deltas: bool = True,
) -> list[list[str]]:
    """`latency=False` leaves the self-latency rows out (the results are not a
    run of this tree); `deltas=False` prints `n/c` in their delta cell (the
    two runs are not on comparable boxes). Token rows are never affected."""
    out = []
    base_lat = _latency(base_results)
    pr_lat = _latency(pr_results)
    for tid, e in T.load(announce=False).items():
        if latency and tid in pr_lat:
            pv, bv = pr_lat.get(tid), base_lat.get(tid)
            out.append(
                [
                    f"`{tid}`",
                    str(e["criterion"]),
                    f"{e['comparator']} {e['floor']}",
                    _fmt(bv),
                    _fmt(pv),
                    _delta(bv, pv) if deltas else "n/c",
                    "PASS" if T.passes(tid, pv) else "**FAIL**",
                ]
            )
    if bench:
        pr_total = sum(
            sum(t.get("jmunch_tokens", 0) for t in r.get("tasks", []))
            for r in bench
            if "error" not in r
        )
        base_total = (base_ref_json or {}).get("grand", {}).get("jmunch_tokens")
        grep_total = sum(
            sum(t.get("grep_baseline_tokens", 0) for t in r.get("tasks", []))
            for r in bench
            if "error" not in r
        )
        ratio = round(grep_total / pr_total, 2) if pr_total else None
        e = T.get("token.grand_ratio_vs_grep")
        out.append(
            [
                "`token.grand_ratio_vs_grep`",
                str(e["criterion"]),
                f"{e['comparator']} {e['floor']}",
                "—",
                _fmt(ratio),
                "—",
                "PASS"
                if ratio is not None and T.passes("token.grand_ratio_vs_grep", ratio)
                else "**FAIL**",
            ]
        )
        out.append(
            [
                "jcm tokens, 15 tasks",
                "2",
                "—",
                _fmt(base_total),
                _fmt(pr_total),
                _delta(base_total, pr_total),
                "info",
            ]
        )
        e = T.get("token.per_repo_rise_max")
        for r in bench:
            if "error" in r:
                continue
            pv = sum(t.get("jmunch_tokens", 0) for t in r.get("tasks", []))
            bv = ((base_ref_json or {}).get("repos", {}).get(r["repo"]) or {}).get(
                "jmunch_total_tokens"
            )
            rise = round((pv - bv) / bv, 4) if bv else None
            out.append(
                [
                    f"`token.per_repo_rise_max` {r['repo']}",
                    str(e["criterion"]),
                    f"{e['comparator']} {e['floor']}",
                    _fmt(bv),
                    _fmt(pv),
                    _fmt(rise),
                    "PASS"
                    if rise is not None and T.passes("token.per_repo_rise_max", rise)
                    else ("—" if rise is None else "**FAIL**"),
                ]
            )
    return out


def render(
    table: list[list[str]], head_sha: str, notes: list[str] | None = None
) -> str:
    md = [
        MARKER,
        f"### Harness bench on `{head_sha[:7]}` (base = committed artifacts)",
        "",
        "| threshold | crit | floor | base | PR | delta | verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    md += ["| " + " | ".join(r) + " |" for r in table]
    for note in notes or []:
        md.append("")
        md.append(note)
    md.append("")
    md.append(
        "_Floors from `harness/thresholds.json`; base from `harness/results/latest.json` and `benchmarks/jcm_reference.json` on the base ref. A failing `latency.*` p95 is re-sampled once before its verdict (harness F-19); the first p95 is in `self_latency.json` as `_warm_p95_first_ms`._"
    )
    return "\n".join(md) + "\n"


def post(pr: int, body: str) -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", "jgravelle/jcodemunch-mcp")
    existing = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/issues/{pr}/comments",
            "--paginate",
            "--jq",
            f'.[] | select(.body | startswith("{MARKER}")) | .id',
        ],
        text=True,
        capture_output=True,
        encoding="utf-8",
    ).stdout.split()
    payload = json.dumps({"body": body})
    if existing:
        subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "PATCH",
                f"repos/{repo}/issues/comments/{existing[0]}",
                "--input",
                "-",
            ],
            input=payload,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    else:
        subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{repo}/issues/{pr}/comments",
                "--input",
                "-",
            ],
            input=payload,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-ref", default="origin/main")
    ap.add_argument("--results", default="harness/results/latest.json")
    ap.add_argument("--benchmark")
    ap.add_argument("--pr", type=int)
    ap.add_argument("--post", action="store_true")
    ap.add_argument("--summary")
    a = ap.parse_args(argv)
    pr_results = json.loads(Path(a.results).read_text(encoding="utf-8"))
    bench = (
        json.loads(Path(a.benchmark).read_text(encoding="utf-8"))
        if a.benchmark and Path(a.benchmark).exists()
        else None
    )
    base_results = _git_show(a.base_ref, "harness/results/latest.json")
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).strip()
    this_run = is_run_of(pr_results, head)
    same_box = comparable(base_results, pr_results)
    table = rows(
        base_results,
        pr_results,
        _git_show(a.base_ref, "benchmarks/jcm_reference.json"),
        bench,
        latency=this_run,
        deltas=same_box,
    )
    stamp = (pr_results.get("env") or {}).get("commit") or "absent"
    if not table:
        if this_run:
            why = (
                f"{a.results} is a run of this tree (env.commit {stamp}) but holds no "
                "self-latency measurement: the step did not run, or wrote none"
            )
        else:
            why = (
                f"{a.results} is not a run of this tree (its env.commit is {stamp}, "
                f"HEAD is {head[:8]}); run `python -m harness bench --offline "
                "--results-out <path>` on this commit and pass that path as --results"
            )
        if a.benchmark:
            why += f"; and the --benchmark file {a.benchmark} does not exist"
        else:
            why += "; and no --benchmark was given"
        print(f"REFUSED: nothing measured to table. {why}.", file=sys.stderr)
        return 2
    notes = []
    if not this_run:
        notes.append(
            f"_Latency rows omitted: `{a.results}` is not a run of this tree (its "
            f"`env.commit` is `{stamp}`, HEAD is `{head[:8]}`). This tree's latency "
            "verdicts are in its own bench tier summary._"
        )
    elif not same_box:
        notes.append(
            f"_Latency deltas read `n/c` (not comparable): the base was measured on "
            f"{_box_text(base_results)}, this run on {_box_text(pr_results)}. Each "
            "verdict is this run against its Floor._"
        )
    body = render(table, head, notes)
    print(body)
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as fh:
            fh.write(body.replace(MARKER + "\n", ""))
    if a.post and a.pr:
        post(a.pr, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
