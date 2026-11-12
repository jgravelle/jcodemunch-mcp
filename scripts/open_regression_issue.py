"""Open (or update) one issue per failing Floor or check on main (DESIGN sections 2 and 4).

`python scripts/open_regression_issue.py --summary FILE --label regression --range BASE..HEAD [--title-prefix ...]`

Reads the harness step summary written by `--summary` and files ONE issue per
failing row, titled `<prefix>: <id> on main` and labeled as given. Two kinds of
row fail: a Floor (`| id | crit | floor | observed | **FAIL** |`) and a check
(`| id | detail | **FAIL** |`: `check.pytest_exit`, `check.ruff_src`, a corpus
checksum). The harness writes both into the same summary, and a failing test
reaches it only as `check.pytest_exit`, so a filer that read Floors alone left
a red nightly test with no issue (nightly run 42754071420). A failed check's
body carries the summary's `**Failed:**` block, which names the failing tests.
An open issue with the same title is commented on, not duplicated. Needs
`GH_TOKEN` with `issues: write`. Never closes an issue: a human does that
with the fix in front of them.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

ROW = re.compile(r"^\| `([^`]+)` \| ([^|]+) \| ([^|]+) \| ([^|]+) \| \*\*FAIL\*\* \|")
CHECK_ROW = re.compile(r"^\| `([^`]+)` \| ([^|]*) \| \*\*FAIL\*\* \|")
FAILED_BLOCK = re.compile(r"\*\*Failed:\*\*\s*```\n(.*?)\n```", re.S)

_FOOTER = "This issue was opened by CI and is closed by a person."


def failing_rows(summary_text: str) -> list[tuple[str, str, str, str]]:
    return [
        m.groups()
        for m in (ROW.match(ln.strip()) for ln in summary_text.splitlines())
        if m
    ]


def failing_checks(summary_text: str) -> list[tuple[str, str]]:
    return [
        m.groups()
        for m in (CHECK_ROW.match(ln.strip()) for ln in summary_text.splitlines())
        if m
    ]


def failed_tests(summary_text: str) -> list[str]:
    m = FAILED_BLOCK.search(summary_text.replace("\r\n", "\n"))
    return [ln for ln in m.group(1).splitlines() if ln.strip()] if m else []


def issues(
    summary_text: str, *, prefix: str, range_: str, run_url: str
) -> list[tuple[str, str]]:
    """(title, body) for every failing Floor and check in a harness summary."""
    where = f"Commit range: `{range_ or 'unknown'}`\nRun: {run_url}\n\n"
    out: list[tuple[str, str]] = []
    for tid, crit, floor, observed in failing_rows(summary_text):
        out.append(
            (
                f"{prefix}: {tid.strip()} on main",
                f"The harness reported a Floor violation on `main`.\n\n"
                f"| threshold | criterion | floor | observed |\n|---|---|---|---|\n"
                f"| `{tid.strip()}` | {crit.strip()} | {floor.strip()} | {observed.strip()} |\n\n"
                + where
                + "Floors live only in `harness/thresholds.json`; a loosening needs a `loosened` block "
                f"(docs/standard/STANDARD.md). {_FOOTER}",
            )
        )
    named = failed_tests(summary_text)
    for cid, detail in failing_checks(summary_text):
        tests = (
            "Failed:\n\n```\n" + "\n".join(named) + "\n```\n\n"
            if named and cid.strip() == "check.pytest_exit"
            else ""
        )
        out.append(
            (
                f"{prefix}: {cid.strip()} on main",
                f"The harness reported a failed check on `main`.\n\n"
                f"| check | detail |\n|---|---|\n| `{cid.strip()}` | {detail.strip()} |\n\n"
                + tests
                + where
                + _FOOTER,
            )
        )
    # The harness appends to `--summary`, so one id can fail twice in one
    # file; one title is one issue.
    seen: set[str] = set()
    return [(t, b) for t, b in out if not (t in seen or seen.add(t))]


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
    found = issues(text, prefix=a.title_prefix, range_=a.range, run_url=run_url)
    if not found:
        print("no FAIL rows in the summary; nothing to file")
        return 0
    for title, body in found:
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
    sys.exit(main())
