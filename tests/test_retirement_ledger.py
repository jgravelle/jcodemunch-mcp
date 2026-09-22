"""A test may be deleted only through the ledger.

`docs/harness/ARCHAEOLOGY.md` section 1 lists every test file that existed
on 2026-09-03 with the reason it exists. From now on a listed file may
disappear only if `harness/retired.json` names it, the lesson it encoded,
and the replacement assertion (`file::test_name`) that now carries the
lesson, and that assertion must exist and collect. Otherwise this fails.

The ledger starts empty: the archaeology found nothing to retire.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from harness import thresholds as T

REPO = T.REPO_ROOT
LEDGER = REPO / "harness" / "retired.json"
ARCH = REPO / "docs" / "harness" / "ARCHAEOLOGY.md"


def _archaeology_paths() -> list[str]:
    text = ARCH.read_text(encoding="utf-8", errors="replace")
    # Section 1 only: section D lists tests deleted BEFORE the ledger existed.
    start = text.index("## 1. Test files")
    end = text.index("## 2. ", start)
    return sorted(set(re.findall(r"^\| (tests/[^ |]+\.py) \|", text[start:end], re.M)))


def _ledger() -> list[dict]:
    return json.loads(LEDGER.read_text(encoding="utf-8"))["retired"]


def test_ledger_is_well_formed():
    for r in _ledger():
        for k in ("path", "lesson", "replacement", "commit", "date"):
            assert r.get(k), f"retired entry {r} lacks {k!r}"
        assert "::" in r["replacement"], f"{r['path']}: replacement must be file::test_name"


def test_no_archaeology_row_is_split_across_lines():
    """A row broken in two is a row the next reader cannot read (#817).

    ⚠⚠ **This PR broke one and everything stayed green**, which is the whole
    argument for the assertion. A raw newline written into a notes cell -- a
    quoted source snippet whose escapes were expanded before they reached the
    file -- ends the row mid-sentence and starts a one-cell row after it. GFM
    renders both as garbage and a line-based reader sees a malformed row.

    ⚠ `_archaeology_paths`' own floor cannot see it: the path regex matches
    the FIRST half, so the count is unchanged and `>= 480` holds over a broken
    table. A guard whose numerator and denominator move together is the
    cache-hit-rate shape (08-27) -- it had to be a different question, not a
    tighter floor.

    ⚠ Asserted over the whole file rather than section 1, because a split row
    is corruption wherever it lands.

    ⚠⚠ **The predicate is PIPE BALANCE and the property is one logical row per
    line; they are not the same thing, and this says so rather than letting a
    reader assume otherwise.** A split whose second half happened to both open
    and close with `|` would pass. No cell in the file has that shape and it
    takes a contrived one to build, so the proxy is kept and named instead of
    replaced by a parser. The other direction fails LOUDLY and deliberately: a
    fenced block whose lines start with `|` would break this, the file carries
    no fences today, and a future one is worth a decision rather than silence.
    """
    offenders = []
    for number, line in enumerate(ARCH.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
        stripped = line.rstrip()
        if not stripped:
            continue
        opens = stripped.startswith("|")
        closes = stripped.endswith("|")
        if opens != closes:
            offenders.append(f"{number}: {stripped[:60]!r}")
    assert not offenders, (
        "ARCHAEOLOGY.md row(s) split across lines -- a cell holds a literal "
        f"newline: {offenders}"
    )


def test_every_ledger_entry_names_a_test_that_actually_left_this_branch():
    """The direction neither guard asked, and a typo slipped through it (#821).

    ⚠⚠ **Two guards over one ledger, and the gap was between them.**
    `test_ledger_is_well_formed` asks whether an entry has its fields;
    `test_every_ledgered_replacement_exists_and_collects` asks whether the
    REPLACEMENT is real; `.claude/hooks/dod_checklist.py` asks whether every
    removed test has an entry. None asked whether an entry names a test that
    actually left. A row reading `..._here_needs_the_ordinal_stripped` for a
    test called `..._here_that_needs_the_ordinal_stripped` therefore passed
    this file, failed the checklist, and sat wrong for two commits.

    ⚠⚠ **Against the MERGE BASE, never the working diff.** A test added AND
    retired inside one branch never existed on `main` and owes no row, so a
    working-diff version of this check would demand one; `origin/main...HEAD`
    asks what left the tree as `main` knows it.

    ⚠ Scoped to entries whose `commit` is on this branch. An older row names a
    commit whose diff is not in this range, and re-deriving history here would
    make the test a git archaeologist rather than a guard on what is arriving.

    ⚠⚠ **AN UNESTABLISHABLE BASE FAILS; it must not return green.** The first
    draft returned on a missing `origin/main`, and `actions/checkout` fetches
    ONE ref at depth 1 -- so in the two jobs that collect this file the ref was
    absent, `merge-base` exited 128, and the guard passed having asked nothing.
    A green ratchet and an absent ratchet look identical, and this repo has
    paid for that twice (Practice 6's `--depth=1`). `fast-harness` and `full`
    carry `fetch-depth: 0` for this test; if the base cannot be found, the
    remedy is in the message rather than in a silence.

    ⚠ Two lenient-looking details are deliberate and both fail LOUD rather than
    quiet: an `--abbrev=8` collision prints nine characters and stops matching
    an eight-character ledger value, and `-def test_` misses an indented `def`
    (a test method inside a class). Neither can produce a false pass, so do not
    "fix" either into a substring match.
    """
    import subprocess

    def _git(*args: str) -> tuple[int, str]:
        p = subprocess.run(["git", *args], capture_output=True, text=True, cwd=REPO)
        return p.returncode, p.stdout.strip()

    # ⚠⚠ **NOT A WORK TREE is a different answer from NO BASE, and conflating
    # them breaks the suite for every sdist consumer.** This project ships
    # `tests/` in its artifact -- 573 of the 1,138 entries in
    # `dist/jcodemunch_mcp-1.108.317.tar.gz` -- and an extracted sdist has no
    # `.git`, so a hard failure there tells a reader to set `fetch-depth: 0`
    # in a workflow they are not running. `tests/test_claude_md_size.py`
    # already carries this convention and this reuses its wording.
    #
    # ⚠ The skip costs nothing against `ci.skips_windows` (24 of 25): every CI
    # leg IS a work tree, so this branch cannot fire there. A shallow clone
    # answers `true` here and falls through to the assert below, which is the
    # case that must stay loud.
    if _git("rev-parse", "--is-inside-work-tree")[1] != "true":
        pytest.skip("no usable git here (sdist checkout or git absent)")

    rc, base = _git("merge-base", "origin/main", "HEAD")
    if rc != 0 or not base:
        base_ref = os.environ.get("GITHUB_BASE_REF", "")
        if base_ref:
            rc, base = _git("merge-base", f"origin/{base_ref}", "HEAD")
    assert base, (
        "cannot establish the merge base with origin/main, so this guard would "
        "check nothing. A shallow checkout is the usual cause: the jobs that "
        "collect this file need `fetch-depth: 0` (.github/workflows/pr-gate.yml). "
        "Failing rather than passing, because an absent check and a green one "
        "are indistinguishable from the outside."
    )
    # ⚠ `rc` is checked: an empty `rev-list` after a successful `merge-base`
    # means HEAD IS the base (nothing to grade, return), but a FAILED one means
    # the question went unasked, and those two must not share an exit. Not
    # reachable through any path constructed in review; asserted so the next
    # reader does not have to re-derive that.
    rc, out = _git("rev-list", f"{base}..HEAD", "--abbrev-commit", "--abbrev=8")
    assert rc == 0, f"git rev-list {base[:8]}..HEAD failed; the guard cannot run"
    branch_commits = set(out.split())
    if not branch_commits:
        return
    _rc, diff = _git("diff", f"{base}...HEAD", "--", "tests/")
    removed = {
        ln[len("-def "):].split("(")[0]
        for ln in diff.splitlines()
        if ln.startswith("-def test_")
    }
    missing, unresolvable = [], []
    for r in _ledger():
        commit = r.get("commit", "")
        if commit not in branch_commits:
            # ⚠ A REBASE rewrites shas, so a row written on this branch can
            # stop being in the range -- and the first draft skipped it in
            # silence, which is how the typo this guard exists for would have
            # survived a rebase. A sha that resolves is history; one that does
            # not is a row nothing can check, and an un-checkable row is loud.
            if commit and _git("cat-file", "-e", f"{commit}^{{commit}}")[0] != 0:
                unresolvable.append(f"{r['path']} (commit {commit})")
            continue
        name = r["path"].partition("::")[2]
        if not name:
            # A whole-FILE retirement, which `harness/retired.json`'s own
            # schema line allows: the file must actually be gone.
            if (REPO / r["path"]).exists():
                missing.append(r["path"] + " (file still present)")
            continue
        if name not in removed:
            missing.append(r["path"])
    assert not unresolvable, (
        f"{unresolvable} name a commit this repository does not have. A rebase "
        f"rewrites shas; re-point the row at the commit that now carries the "
        f"retirement rather than leaving a row nothing can verify."
    )
    assert not missing, (
        f"{missing} are ledgered against a commit on this branch, and no `def` "
        f"of that name was removed between {base[:8]} and HEAD. Either the name "
        f"is mistyped or the retirement did not happen; paste it from "
        f"`git diff` rather than retyping it."
    )


def test_every_archaeology_test_still_exists_or_is_in_the_ledger():
    paths = _archaeology_paths()
    assert len(paths) >= 480, f"ARCHAEOLOGY.md section 1 parsed to only {len(paths)} rows"
    ledgered = {r["path"] for r in _ledger()}
    gone = [p for p in paths if not (REPO / p).exists() and p not in ledgered]
    assert not gone, (
        "test file(s) deleted without a harness/retired.json entry naming the lesson "
        f"and the replacement assertion: {gone}"
    )


def test_every_ledgered_replacement_exists_and_collects():
    bad = []
    for r in _ledger():
        f, _, name = r["replacement"].partition("::")
        p = REPO / f
        if not p.exists():
            bad.append(f"{r['path']}: replacement file {f} missing")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if not re.search(rf"^\s*(async\s+)?def {re.escape(name)}\b", text, re.M):
            bad.append(f"{r['path']}: replacement {r['replacement']} not found")
        # A ledgered `file::test_name` retires one function from a file that
        # stays; its absence is the DEF, not the file (#670's entry).
        rfile, sep, rname = r["path"].partition("::")
        if sep:
            rp = REPO / rfile
            if rp.exists() and re.search(
                rf"^\s*(async\s+)?def {re.escape(rname)}\b",
                rp.read_text(encoding="utf-8", errors="replace"),
                re.M,
            ):
                bad.append(f"{r['path']}: listed as retired but still defined")
        elif (REPO / r["path"]).exists():
            bad.append(f"{r['path']}: listed as retired but still present")
    assert not bad, "\n".join(bad)
