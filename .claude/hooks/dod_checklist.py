"""The Definition-of-Done checklist, produced from evidence (DESIGN D6, section 1.1).

purpose:  every workflow ends in a checklist the AGENT did not fill in; each of
          STANDARD.md's twelve DoD items is met / unmet / n.a. with the evidence
          path, and pre_pr.py refuses a PR with an `unmet` row
invokes:  git diff against --base-ref, scripts/dod_changelog.py,
          scripts/surface_diff.py, the evidence files under
          .claude/state/evidence/, harness/thresholds.json (diff only)
produces: .claude/state/evidence/checklist.md (also printed)
refuses:  nothing; it reports

Usage: python .claude/hooks/dod_checklist.py [--base-ref origin/main] [--labels a,b] [--contributor]
The DoD text itself is read from docs/standard/STANDARD.md at run time; the
item numbers are the only thing this file knows.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

from _common import EVIDENCE, REPO, git

RATE_KEY_RE = re.compile(
    r'^\+.*["\'](\w+_(?:pct|rate|share)|confidence)["\']\s*:', re.M
)
BACKGROUND_RE = re.compile(
    r"^\+.*(?:threading\.Thread|socket\.socket|httpx\.|asyncio\.create_task|schedule)",
    re.M,
)


def sh(*cmd: str) -> tuple[int, str]:
    r = subprocess.run(
        cmd,
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def dod_items() -> dict[int, str]:
    text = (REPO / "docs" / "standard" / "STANDARD.md").read_text(encoding="utf-8")
    sec = text.split("## Definition of Done for a change", 1)[1].split("\n## ", 1)[0]
    items = {}
    for m in re.finditer(r"^(\d+)\.\s+(.*?)(?=^\d+\.\s|\Z)", sec, re.M | re.S):
        items[int(m.group(1))] = " ".join(m.group(2).split())
    return items


def evidence(name: str) -> str | None:
    p = EVIDENCE / name
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else None


def harness_pass(summary: str | None) -> bool | None:
    if summary is None:
        return None
    return "**FAIL**" not in summary and "HARNESS FAIL" not in summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-ref", default="origin/main")
    ap.add_argument("--labels", default="")
    ap.add_argument("--contributor", action="store_true")
    a = ap.parse_args()
    labels = {s.strip() for s in a.labels.split(",") if s.strip()}
    base = a.base_ref
    changed = (
        git("diff", "--name-only", f"{base}...HEAD").split()
        + git("diff", "--name-only").split()
    )
    changed = sorted(set(changed))
    # DoD 7 and 8 are about PRODUCT behaviour: scan additions under src/ only,
    # or a doc that MENTIONS httpx or a schedule trips them (first run on the
    # workflows layer itself).
    src_diff = git("diff", f"{base}...HEAD", "--", "src/") + git("diff", "--", "src/")
    touched = lambda *pre: any(c.startswith(pre) for c in changed)  # noqa: E731
    src_changed = touched("src/")
    tests_deleted = bool(
        git(
            "diff", "--name-only", "--diff-filter=D", f"{base}...HEAD", "--", "tests/"
        ).strip()
    )

    rows: list[tuple[int, str, str]] = []

    def row(n: int, verdict: str, ev: str) -> None:
        rows.append((n, verdict, ev))

    # 1 red then green
    red, green = evidence("red.txt"), evidence("green.txt")
    if not src_changed and not touched("harness/", "scripts/"):
        row(1, "n.a.", "no change under src/, harness/ or scripts/")
    elif red is None or green is None:
        row(
            1,
            "unmet",
            "evidence/red.txt (touched tests at the base ref, must fail) and evidence/green.txt (at HEAD, must pass) are required",
        )
    else:
        red_fail = "EXIT=0" not in red.splitlines()[-1:] and "failed" in red.lower()
        green_ok = (
            "EXIT=0" in green.splitlines()[-1:]
            or " passed" in green.lower()
            and "failed" not in green.lower()
        )
        row(
            1,
            "met" if red_fail and green_ok else "unmet",
            f"evidence/red.txt fails={red_fail}; evidence/green.txt passes={green_ok}",
        )

    # 2 fast tier (ruff inside), touched files, full tier with skip verdicts
    fast, full = evidence("fast.md"), evidence("full.md")
    fp, fu = harness_pass(fast), harness_pass(full)
    if fp is None or fu is None:
        row(
            2,
            "unmet",
            "evidence/fast.md and evidence/full.md (harness --summary) are required",
        )
    else:
        row(
            2,
            "met" if fp and fu else "unmet",
            f"fast tier pass={fp}; full tier pass={fu} (skip ceilings are verdict rows inside)",
        )

    # 3 changelog
    if "no-changelog" in labels:
        row(3, "n.a.", "label no-changelog")
    elif not src_changed:
        row(3, "n.a.", "no change under src/")
    elif (
        git("diff", "--name-only", "--", "src/").strip()
        or git("diff", "--cached", "--name-only", "--", "src/").strip()
    ):
        # scripts/dod_changelog.py diffs base...HEAD; an UNCOMMITTED src/ edit is
        # invisible to it and the row would read met with no entry (W-31).
        row(
            3,
            "unmet",
            "src/ has uncommitted changes; commit them, then re-run (dod_changelog reads the committed diff)",
        )
    else:
        rc, out = sh(
            sys.executable,
            "scripts/dod_changelog.py",
            "--base-ref",
            base,
            "--labels",
            a.labels,
        )
        row(
            3,
            "met" if rc == 0 else "unmet",
            "scripts/dod_changelog.py: "
            + (out.strip().splitlines() or ["?"])[-1][:160],
        )

    # 4 tool surface
    has_desc = "--descriptions" in (REPO / "scripts" / "surface_diff.py").read_text(
        encoding="utf-8"
    )
    rc, out = sh(
        "uv",
        "run",
        "python",
        "scripts/surface_diff.py",
        *(["--descriptions"] if has_desc else []),
        "--base-ref",
        base,
    )
    desc_changed = "description changed:" in out
    if rc == 0 and "no surface change" in out and not desc_changed:
        row(
            4,
            "n.a.",
            "surface unchanged (names"
            + (
                " and descriptions"
                if has_desc
                else "; descriptions not diffed, FINDINGS W-1"
            )
            + " via scripts/surface_diff.py)",
        )
    else:
        docs = all(
            any(c == f for c in changed) for f in ("README.md", "CHANGELOG.md")
        ) and any(c in ("CLAUDE.md", "KEY-FILES.md") for c in changed)
        base_touched = "benchmarks/schema_baseline.json" in changed
        row(
            4,
            "met" if rc == 0 and docs and base_touched else "unmet",
            f"surface_diff rc={rc} desc_changed={desc_changed}; README+CHANGELOG+CLAUDE/KEY-FILES changed={docs}; schema_baseline changed={base_touched}",
        )

    # 5 benchmark mirrors
    if not touched("benchmarks/"):
        row(5, "n.a.", "benchmarks/ untouched")
    else:
        rc, out = sh(
            "uv",
            "run",
            "pytest",
            "tests/test_provenance.py",
            "tests/test_schema_budget.py",
            "-q",
            "-p",
            "no:cacheprovider",
        )
        row(
            5,
            "met" if rc == 0 else "unmet",
            "tests/test_provenance.py + test_schema_budget.py: "
            + (out.strip().splitlines() or ["?"])[-1][:120],
        )

    # 6 config/env/CLI rows
    if not touched("src/jcodemunch_mcp/config.py", "src/jcodemunch_mcp/cli/"):
        row(6, "n.a.", "config.py and cli/ untouched")
    else:
        rc, out = sh(
            "uv",
            "run",
            "pytest",
            "tests/test_cli_env_split.py",
            "tests/test_config_docs_reverse_parity.py",
            "-q",
            "-p",
            "no:cacheprovider",
        )
        row(
            6,
            "met" if rc == 0 else "unmet",
            "tests/test_cli_env_split.py + test_config_docs_reverse_parity.py: "
            + (out.strip().splitlines() or ["?"])[-1][:120],
        )

    # 7 background behaviour disclosure
    if not BACKGROUND_RE.search(src_diff):
        row(7, "n.a.", "no thread/socket/http/scheduled addition in the diff")
    else:
        readme_diff = git("diff", f"{base}...HEAD", "--", "README.md")
        row(
            7,
            "met" if "Background behavior" in readme_diff else "unmet",
            "diff adds a thread/socket/http/schedule; README 'Background behavior, fully disclosed' must change",
        )

    # 8 *_basis beside a published rate
    rates = RATE_KEY_RE.findall(src_diff)
    if not rates:
        row(8, "n.a.", "no new rate/share/confidence key in the diff")
    else:
        basis = "_basis" in src_diff or "refus" in src_diff
        row(
            8,
            "met" if basis else "unmet",
            f"new keys {sorted(set(rates))[:5]}; a *_basis sibling or a refusal path must appear in the diff",
        )

    # 9 contributor PR
    if not a.contributor:
        row(9, "n.a.", "our own PR")
    else:
        tm = evidence("trial_merge.txt")
        cla = evidence("cla.txt")
        row(
            9,
            "met"
            if tm and "EXIT=0" in tm and cla and "count=0" not in cla
            else "unmet",
            "evidence/trial_merge.txt (fast tier on the trial merge) and evidence/cla.txt (status count on the head SHA)",
        )

    # 10 fast; bench when benchmarks/, harness/ or server.py changed
    needs_bench = touched("benchmarks/", "harness/", "src/jcodemunch_mcp/server.py")
    bench = evidence("bench.md")
    bp = harness_pass(bench)
    if fp is None:
        row(10, "unmet", "evidence/fast.md required")
    elif needs_bench and bp is None:
        row(
            10,
            "unmet",
            "benchmarks/, harness/ or server.py changed: evidence/bench.md (harness bench --offline --summary) required",
        )
    else:
        row(
            10,
            "met" if fp and (bp is not False) else "unmet",
            f"fast pass={fp}; bench pass={bp if needs_bench else 'not required'}",
        )

    # 11 retired test ledger
    if not tests_deleted:
        row(11, "n.a.", "no test file deleted")
    else:
        rc, out = sh(
            "uv",
            "run",
            "pytest",
            "tests/test_retirement_ledger.py",
            "-q",
            "-p",
            "no:cacheprovider",
        )
        row(
            11,
            "met" if rc == 0 and "harness/retired.json" in changed else "unmet",
            f"retirement ledger test rc={rc}; harness/retired.json changed={'harness/retired.json' in changed}",
        )

    # 12 threshold moved
    if "harness/thresholds.json" not in changed:
        row(12, "n.a.", "harness/thresholds.json untouched")
    else:
        try:
            old = json.loads(git("show", f"{base}:harness/thresholds.json"))[
                "thresholds"
            ]
            new = json.loads(
                (REPO / "harness" / "thresholds.json").read_text(encoding="utf-8")
            )["thresholds"]
        except (json.JSONDecodeError, KeyError):
            old, new = [], []
        oldmap = {e["id"]: e for e in old}
        bad = []
        for e in new:
            o = oldmap.get(e["id"])
            if o and o.get("floor") != e.get("floor"):
                hist_ok = len(e.get("history", [])) > len(o.get("history", []))
                if not (hist_ok or e.get("loosened")):
                    bad.append(e["id"])
        row(
            12,
            "met" if not bad else "unmet",
            "moved floors without history/loosened: " + (", ".join(bad) or "none"),
        )

    items = dod_items()
    out_lines = ["| DoD | item | verdict | evidence |", "|---|---|---|---|"]
    for n, verdict, ev in rows:
        out_lines.append(f"| {n} | {items.get(n, '?')[:90]} | {verdict} | {ev} |")
    unmet = [n for n, v, _ in rows if v == "unmet"]
    out_lines.append("")
    out_lines.append(
        f"Checklist: {len(rows) - len(unmet)}/{len(rows)} met or n.a.; unmet: {unmet or 'none'}. Generated by .claude/hooks/dod_checklist.py against {base}."
    )
    text = "\n".join(out_lines) + "\n"
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "checklist.md").write_text(text, encoding="utf-8")
    print(text)
    return 1 if unmet else 0


if __name__ == "__main__":
    sys.exit(main())
