"""H2: a test edited, deleted or skip-marked without a retirement entry (DESIGN section 4).

purpose:  a LOAD-BEARING test cannot be weakened by accident; the weakening
          is named the moment it happens, with the lesson the test encodes
invokes:  `git diff -- <file>`, `git status --porcelain <file>`,
          docs/harness/ARCHAEOLOGY.md (the row for the file), the working
          tree's harness/retired.json diff
produces: nothing; feedback only
refuses:  nothing (PostToolUse cannot undo an edit); exit 2 puts the
          finding in front of the agent, and the reviewer subagent and
          tests/test_retirement_ledger.py refuse later
budget:   5 s
"""

from __future__ import annotations

import re

from _common import (
    REPO,
    Budget,
    block,
    budget_warning,
    git,
    ok,
    read_hook_input,
    tool_path,
    under,
    warn,
)

BUDGET_SECONDS = 5
SKIP_RE = re.compile(r"pytest\.(?:mark\.(?:skip|skipif|xfail)|skip\(|importorskip\()")
TEST_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+test_\w+")
ASSERT_RE = re.compile(r"^\s*assert\b")


def archaeology_row(rel: str) -> str | None:
    text = (REPO / "docs" / "harness" / "ARCHAEOLOGY.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith(f"| {rel} |") or line.startswith(f"| `{rel}` |"):
            return line
    return None


def main() -> None:
    payload = read_hook_input()
    path = tool_path(payload)
    if not under(path, "tests"):
        ok()
    budget = Budget(BUDGET_SECONDS)
    rel = path.relative_to(REPO).as_posix()

    status = git("status", "--porcelain", "--", rel).strip()
    deleted = status.startswith(" D") or status.startswith("D ")
    diff = git("diff", "HEAD", "--", rel)
    removed_tests = removed_asserts = added_skips = 0
    for line in diff.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-"):
            body = line[1:]
            if TEST_DEF_RE.match(body):
                removed_tests += 1
            elif ASSERT_RE.match(body):
                removed_asserts += 1
        elif line.startswith("+") and SKIP_RE.search(line[1:]):
            added_skips += 1
    if budget.left() <= 0:
        warn(
            "PostToolUse",
            budget_warning("test_edit_guard", "the weakening scan", budget),
        )

    if not (deleted or removed_tests or removed_asserts or added_skips):
        ok()

    ledger_touched = bool(
        git("status", "--porcelain", "--", "harness/retired.json").strip()
    )
    row = archaeology_row(rel)
    load_bearing = bool(row and "LOAD-BEARING" in row)
    what = []
    if deleted:
        what.append("the file is DELETED")
    if removed_tests:
        what.append(f"{removed_tests} `def test_` removed")
    if removed_asserts:
        what.append(f"{removed_asserts} `assert` line(s) removed")
    if added_skips:
        what.append(f"{added_skips} skip/xfail mark(s) added")
    msg = [f"test_edit_guard: {rel}: " + "; ".join(what) + "."]
    if row:
        msg.append("ARCHAEOLOGY: " + row[:400])
    else:
        msg.append(
            "ARCHAEOLOGY: no row for this file (a new test, or one the survey missed)."
        )
    if (
        (deleted or removed_tests or added_skips)
        and load_bearing
        and not ledger_touched
    ):
        msg.append(
            "A LOAD-BEARING test is being retired or skipped with NO harness/retired.json change in the tree. "
            "DoD 11: add the ledger entry naming the lesson and the replacement assertion, in its own commit, "
            "and state the lesson in the commit message; tests/test_retirement_ledger.py fails otherwise."
        )
        block("\n".join(msg))
    if removed_asserts and not (deleted or removed_tests):
        msg.append(
            "An assertion was removed. If the old one encoded the defect (Practice 9) say so in the commit; the reviewer checks."
        )
    warn("PostToolUse", "\n".join(msg))


if __name__ == "__main__":
    main()
