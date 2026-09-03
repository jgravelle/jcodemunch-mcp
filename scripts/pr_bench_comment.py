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


def rows(
    base_results: dict | None,
    pr_results: dict,
    base_ref_json: dict | None,
    bench: list | None,
) -> list[list[str]]:
    out = []
    base_lat = (base_results or {}).get("artifacts", {}).get("self_latency", {}) or {}
    pr_lat = pr_results.get("artifacts", {}).get("self_latency", {}) or {}
    for tid, e in T.load(announce=False).items():
        if tid in pr_lat:
            pv, bv = pr_lat.get(tid), base_lat.get(tid)
            out.append(
                [
                    f"`{tid}`",
                    str(e["criterion"]),
                    f"{e['comparator']} {e['floor']}",
                    _fmt(bv),
                    _fmt(pv),
                    _delta(bv, pv),
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


def render(table: list[list[str]], head_sha: str) -> str:
    md = [
        MARKER,
        f"### Harness bench on `{head_sha[:7]}` (base = committed artifacts)",
        "",
        "| threshold | crit | floor | base | PR | delta | verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    md += ["| " + " | ".join(r) + " |" for r in table]
    md.append("")
    md.append(
        "_Floors from `harness/thresholds.json`; base from `harness/results/latest.json` and `benchmarks/jcm_reference.json` on the base ref. `latency.*` verdicts are informational until F-19 closes with three CI runs._"
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
    table = rows(
        _git_show(a.base_ref, "harness/results/latest.json"),
        pr_results,
        _git_show(a.base_ref, "benchmarks/jcm_reference.json"),
        bench,
    )
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).strip()
    body = render(table, head)
    print(body)
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as fh:
            fh.write(body.replace(MARKER + "\n", ""))
    if a.post and a.pr:
        post(a.pr, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
