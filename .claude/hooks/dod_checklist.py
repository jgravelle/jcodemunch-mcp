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
       python .claude/hooks/dod_checklist.py --stamp red|green   (right after each run, #671)
The DoD text itself is read from docs/standard/STANDARD.md at run time; the
item numbers are the only thing this file knows.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import pathlib
import re
import subprocess
import sys

from _common import EVIDENCE, REPO, UNREADABLE_PREFIX, git, paths_for, tree_id

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


# W-38: the roots a red/green pair is REQUIRED for (this list serves row 1
# only; pre_commit.CODE_ROOTS and _common.TIER_PATHS answer other questions,
# W-43 names the three). Row 1 grades a pair that
# exists whatever the path; the roots decide only whether an absent pair is
# unmet or n.a. Three reviewers in one day graded the row by hand because
# `.claude/hooks/`, `benchmarks/` and `tests/` were not on the old list.
CODE_ROOTS = paths_for("redgreen")  # W-43: projected from _common.PATH_TABLE


def row1_verdict(changed: list[str], red: str | None, green: str | None) -> tuple[str, str]:
    """Row 1: red then green. Returns (verdict, evidence)."""
    if red is None or green is None:
        if not any(c.startswith(CODE_ROOTS) for c in changed):
            return "n.a.", "no change under a code root (" + ", ".join(CODE_ROOTS) + ") and no red/green pair"
        return (
            "unmet",
            "evidence/red.txt (touched tests at the base ref, must fail) and evidence/green.txt (at HEAD, must pass) are required",
        )
    # A red run that dies at collection says `error`, not `failed`, and
    # exits 2 (W-38 review; the remedy named EXIT=1 and EXIT=2 both).
    red_fail = "EXIT=0" not in red.splitlines()[-1:] and (
        "failed" in red.lower() or "error" in red.lower()
    )
    green_ok = "EXIT=0" in green.splitlines()[-1:] or (
        " passed" in green.lower() and "failed" not in green.lower()
    )
    return (
        "met" if red_fail and green_ok else "unmet",
        f"evidence/red.txt fails={red_fail}; evidence/green.txt passes={green_ok}",
    )


# #671: row 1 read red.txt/green.txt by path alone, so a later change on the
# same box inherited them (#669 graded `met` from #666's pair). A stamp binds a
# run to the branch, the tier-path tree it ran on (`_common.tree_id`, the
# identity pre_pr.py already holds the full-tier stamp to) and its own output.


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_stamp(kind: str, text: str, branch: str, tree: str) -> dict:
    return {"kind": kind, "branch": branch, "tree": tree, "sha256": _sha(text)}


def current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD").strip()


def write_stamp(kind: str) -> int:
    """`--stamp red|green`: bind the run just written to this branch and tree."""
    text = evidence(f"{kind}.txt")
    if text is None:
        print(f"dod_checklist: evidence/{kind}.txt is absent; run the tests into it first", file=sys.stderr)
        return 2
    tree = tree_id()
    if tree.startswith(UNREADABLE_PREFIX):
        # UNKNOWN is not a tree: a random id would always "differ" from green's.
        print(f"dod_checklist: the tree could not be read; evidence/{kind}.txt is not stamped", file=sys.stderr)
        return 2
    stamp = make_stamp(kind, text, current_branch(), tree)
    (EVIDENCE / f"{kind}.stamp.json").write_text(json.dumps(stamp, indent=1), encoding="utf-8")
    print(f"stamped evidence/{kind}.txt: branch={stamp['branch']} tree={stamp['tree'][:12]}")
    return 0


def row1_binding(
    red: str, green: str, red_stamp: dict | None, green_stamp: dict | None, *, branch: str, tree: str
) -> str | None:
    """None when the pair belongs to this change; otherwise why it does not."""
    if red_stamp is None or green_stamp is None:
        missing = [k for k, s in (("red", red_stamp), ("green", green_stamp)) if s is None]
        return (
            f"evidence/{'/'.join(missing)} not stamped; run "
            "`python .claude/hooks/dod_checklist.py --stamp red|green` right after each run"
        )
    for kind, text, stamp in (("red", red, red_stamp), ("green", green, green_stamp)):
        if stamp.get("branch") != branch:
            return f"evidence/{kind}.txt was stamped on branch {stamp.get('branch')!r}, not {branch!r}"
        if stamp.get("sha256") != _sha(text):
            return f"evidence/{kind}.txt changed after it was stamped"
    for kind, t in (("red", red_stamp.get("tree")), ("green", green_stamp.get("tree")), ("the current", tree)):
        if not isinstance(t, str) or not t or t.startswith(UNREADABLE_PREFIX):
            return f"{kind} tree could not be read, so the pair cannot be bound (fail closed)"
    if green_stamp.get("tree") != tree:
        return (
            f"evidence/green.txt ran on tree {str(green_stamp.get('tree'))[:12]}, "
            f"the tree now is {tree[:12]}; re-run green and stamp it"
        )
    if red_stamp.get("tree") == green_stamp.get("tree"):
        return "evidence/red.txt ran on the same tree as green.txt, so it did not run on the pre-change tree"
    return None


def row1(
    changed: list[str],
    red: str | None,
    green: str | None,
    red_stamp: dict | None,
    green_stamp: dict | None,
    *,
    branch: str,
    tree: str,
) -> tuple[str, str]:
    """Row 1 with the pair held to this change, then graded by row1_verdict."""
    if red is None or green is None:
        return row1_verdict(changed, red, green)
    why = row1_binding(red, green, red_stamp, green_stamp, branch=branch, tree=tree)
    if why is None:
        return row1_verdict(changed, red, green)
    if not any(c.startswith(CODE_ROOTS) for c in changed):
        return "n.a.", f"no change under a code root; the red/green pair on disk is not this change's ({why})"
    return "unmet", why


def _stamp_json(name: str) -> dict | None:
    text = evidence(name)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def harness_pass(summary: str | None) -> bool | None:
    if summary is None:
        return None
    return "**FAIL**" not in summary and "HARNESS FAIL" not in summary


#: How similar two bodies must be for a removal + addition to read as a RENAME.
#:
#: ⚠⚠ MEASURED on the only two real cases in this repo's history, not chosen:
#: `b9dfcb19`, the one retirement in `harness/retired.json`, scores **0.571**
#: against its nearest replacement in the same file; `4093364f`, #753's rename
#: of `test_every_declared_field_pattern_actually_yields_a_field`, scores
#: **0.851** against its new name. 0.75 sits between them with margin on both
#: sides, and both commits were re-run through the detector to confirm the
#: verdicts come out RETIREMENT and RENAME respectively.
#:
#: ⚠ A two-point calibration is thin, and it is stated here so a future case
#: landing between 0.571 and 0.851 is a decision someone makes rather than a
#: silent misgrade. Widening the gap needs a third real case, not a nudge.
_RENAME_BODY_SIMILARITY = 0.75


def retired_test_functions(diff: str, repo: pathlib.Path) -> list[str]:
    r"""Test FUNCTIONS removed and not redefined, keyed `file::name`.

    ⚠⚠ **A file-granular check could not see a retirement.** DoD 11 and
    `harness/retired.json`'s own note are explicit that the unit is
    `file::test_name`, and this item read `--diff-filter=D`, which lists
    deleted FILES only -- so a PR deleting two test functions from a file
    that survives was graded `n.a.`, and the item was never evaluated by the
    machine at all. Found in review of #748/#749, which deleted exactly two.

    ⚠⚠ **A NAME IS NOT AN IDENTITY, and the first version of this keyed on
    one.** It concatenated every file under `tests/` and asked whether
    `def <name>(` appeared anywhere, so retiring one of the **128 test names
    that are defined in more than one file** (`test_empty` in 8,
    `test_idempotent` in 6) was excluded as a rename and the ledger was
    never demanded -- the exact grade this detector exists to stop. That is
    `tests/test_key_files_split.py`'s own recorded bug, which collapsed
    `runtime/redact.py` with `redact.py` by basename, reproduced inside a
    guard written against a neighbouring miss. Keyed `file::name` now.

    ⚠⚠ **A rename is told from a retirement by the BODY, and a file-level
    rule was measured wrong.** The first version excluded every removal in a
    file that gained any test function, which silenced `b9dfcb19` -- the ONLY
    prior retirement in `harness/retired.json` -- because its replacement was
    added to the same file beside eleven other new tests. The ledger's schema
    makes that the normal shape, not an edge case: entry 0's `path` and
    `replacement` name one file. A rename keeps the body; a replacement
    rewrites it, so the bodies are compared against
    `_RENAME_BODY_SIMILARITY`, which is measured on both real cases in this
    repo's history rather than chosen.

    ⚠ A name added to ANOTHER file in the same diff is a move, not a
    retirement, and is excluded by name.

    ⚠ `REPO / "tests"`, never `Path("tests")`: every other filesystem and
    git read here is anchored because a hook's CWD is not the repo root.
    Unanchored, `rglob` yields nothing, every removed name reads as retired,
    and `pre_pr.py` refuses the PR on an ordinary rename.

    ⚠ A DEFINITION, not a mention. The survival scan matches
    `^\s*(async\s+)?def <name>\b` per file, the pattern
    `tests/test_retirement_ledger.py` already uses. A raw substring scan is
    answered by a test that merely NAMES the retired function -- and this
    very PR ships one, `test_the_retired_gap_tests_are_gone_from_the_short_function_file`,
    which asserts on the literal `"def test_a_macro_is_a_known_separate_gap"`.
    It was saved only by the absent `(`.
    """
    removed: dict[tuple[str, str], tuple[str, ...]] = {}
    added: dict[tuple[str, str], tuple[str, ...]] = {}
    current = ""
    side = ""
    block_name = ""
    block: list[str] = []

    def _flush() -> None:
        if block_name:
            target = removed if side == "-" else added
            target[(current, block_name)] = tuple(block)

    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            _flush()
            side, block_name, block = "", "", []
            current = line[len("+++ b/"):].strip()
            continue
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            _flush()
            side, block_name, block = "", "", []
            continue
        if not current or line[:1] not in ("-", "+"):
            _flush()
            side, block_name, block = "", "", []
            continue
        if line[:1] != side:
            _flush()
            side, block_name, block = line[:1], "", []
        text = line[1:]
        m = re.match(r"\s*(?:async\s+)?def (test_\w+)", text)
        if m:
            _flush()
            block_name, block = m.group(1), []
        elif block_name:
            stripped = text.strip()
            if stripped:
                block.append(stripped)
    _flush()

    if not removed:
        return []

    def _defined_in(path: str, name: str) -> bool:
        file_path = repo / path
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return bool(
            re.search(rf"^\s*(?:async\s+)?def {re.escape(name)}\b", text, re.M)
        )

    def _is_a_rename(path: str, body: tuple[str, ...]) -> bool:
        """Did some function ADDED to this file keep this one's body?

        ⚠⚠ **The body is the discriminator, and a file-level one was measured
        WRONG.** Excluding every removal in a file that gained any test silenced
        `b9dfcb19` -- the only prior retirement in `harness/retired.json` --
        because its replacement went into the SAME file beside eleven other new
        tests. The ledger's own schema makes that the normal shape: entry 0's
        `path` and `replacement` name one file. So the file-level rule missed
        1 of 1 historical retirements, and this PR was caught only because its
        replacements happened to go in a new file.

        ⚠ A rename keeps the body; a replacement rewrites it.
        `_RENAME_BODY_SIMILARITY` carries the calibration and the two
        measurements behind it.
        """
        if not body:
            return False
        for (added_path, _name), added_body in added.items():
            if added_path != path or not added_body:
                continue
            if difflib.SequenceMatcher(
                None, "\n".join(body), "\n".join(added_body)
            ).ratio() >= _RENAME_BODY_SIMILARITY:
                return True
        return False

    moved = {name for _path, name in added}
    return sorted(
        f"{path}::{name}"
        for (path, name), body in removed.items()
        if not _defined_in(path, name)
        and name not in moved
        and not _is_a_rename(path, body)
    )

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-ref", default="origin/main")
    ap.add_argument("--labels", default="")
    ap.add_argument("--contributor", action="store_true")
    ap.add_argument("--stamp", choices=("red", "green"), default=None)
    a = ap.parse_args()
    if a.stamp:
        return write_stamp(a.stamp)
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
    deleted_test_files = [
        f
        for f in git(
            "diff", "--name-only", "--diff-filter=D", f"{base}...HEAD", "--", "tests/"
        ).splitlines()
        if f.strip()
    ]

    retired_functions = retired_test_functions(
        git("diff", "-U0", f"{base}...HEAD", "--", "tests/"), REPO
    )
    tests_deleted = bool(deleted_test_files) or bool(retired_functions)

    rows: list[tuple[int, str, str]] = []

    def row(n: int, verdict: str, ev: str) -> None:
        rows.append((n, verdict, ev))

    # 1 red then green
    red, green = evidence("red.txt"), evidence("green.txt")
    if red is not None and green is not None:
        row(1, *row1(  # W-38, #671
            changed, red, green, _stamp_json("red.stamp.json"), _stamp_json("green.stamp.json"),
            branch=current_branch(), tree=tree_id(),
        ))
    else:
        row(1, *row1_verdict(changed, red, green))  # W-38

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
        # W-38 review: widening this gate is inert, because the authority it
        # routes to (scripts/dod_changelog.py) requires an entry for src/ only;
        # a wider gate here would read `met` with no entry (#508's shape).
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
    needs_bench = touched(*paths_for("bench"))  # W-43: the table's bench column
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
        row(11, "n.a.", "no test file deleted and no test function retired")
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
        subject = ", ".join(deleted_test_files + retired_functions) or "(none)"
        row(
            11,
            "met" if rc == 0 and "harness/retired.json" in changed else "unmet",
            f"retired: {subject}; ledger test rc={rc}; harness/retired.json "
            f"changed={'harness/retired.json' in changed}",
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
