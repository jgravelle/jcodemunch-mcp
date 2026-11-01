"""Open (or update) one issue per failing Floor on main (DESIGN sections 2 and 4).

`python scripts/open_regression_issue.py --summary FILE --label regression --range BASE..HEAD [--title-prefix ...]`

Reads the harness step summary written by `--summary`, takes every row whose
verdict is FAIL, and files ONE issue per threshold id titled
`<prefix>: <id> on main`, labeled as given, body = the verdict row, the
commit range and the run URL. An open issue with the same title is commented
on, not duplicated. Needs `GH_TOKEN` with `issues: write`. Never closes an
issue: a human does that with the fix in front of them.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

ROW = re.compile(r"^\| `([^`]+)` \| ([^|]+) \| ([^|]+) \| ([^|]+) \| \*\*FAIL\*\* \|")


def failing_rows(summary_text: str) -> list[tuple[str, str, str, str]]:
    return [
        m.groups()
        for m in (ROW.match(ln.strip()) for ln in summary_text.splitlines())
        if m
    ]


def _gh(*args: str, input_: str | None = None) -> str:
    p = subprocess.run(
        ["gh", *args], text=True, capture_output=True, encoding="utf-8", input=input_
    )
    if p.returncode != 0:
        raise SystemExit(f"gh {' '.join(args[:3])} failed: {p.stderr[-500:]}")
    return p.stdout


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", required=True)
    ap.add_argument("--label", default="regression")
    ap.add_argument("--range", default="")
    ap.add_argument("--title-prefix", default="regression")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    repo = os.environ.get("GITHUB_REPOSITORY", "jgravelle/jcodemunch-mcp")
    run_url = (
        f"https://github.com/{repo}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    )
    text = open(a.summary, encoding="utf-8").read()
    rows = failing_rows(text)
    if not rows:
        print("no FAIL rows in the summary; nothing to file")
        return 0
    for tid, crit, floor, observed in rows:
        title = f"{a.title_prefix}: {tid.strip()} on main"
        body = (
            f"The harness reported a Floor violation on `main`.\n\n"
            f"| threshold | criterion | floor | observed |\n|---|---|---|---|\n"
            f"| `{tid.strip()}` | {crit.strip()} | {floor.strip()} | {observed.strip()} |\n\n"
            f"Commit range: `{a.range or 'unknown'}`\nRun: {run_url}\n\n"
            f"Floors live only in `harness/thresholds.json`; a loosening needs a `loosened` block "
            f"(docs/standard/STANDARD.md). This issue was opened by CI and is closed by a person."
        )
        print(f"{'would file' if a.dry_run else 'filing'}: {title}")
        if a.dry_run:
            continue
        existing = _gh(
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--search",
            f'"{title}" in:title',
            "--json",
            "number,title",
        )
        match = [x for x in json.loads(existing) if x["title"] == title]
        if match:
            _gh(
                "issue",
                "comment",
                str(match[0]["number"]),
                "--repo",
                repo,
                "--body",
                f"Still failing.\n\n{body}",
            )
            print(f"  commented on #{match[0]['number']}")
        else:
            out = _gh(
                "issue",
                "create",
                "--repo",
                repo,
                "--title",
                title,
                "--label",
                a.label,
                "--body",
                body,
            )
            print(f"  opened {out.strip()}")
    return 0


if __name__ == "__main__":
    # A Windows console or redirect is cp1252; repo text is not (L-145).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
