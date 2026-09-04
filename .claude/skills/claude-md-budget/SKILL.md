---
name: claude-md-budget
description: "How to keep CLAUDE.md under its character Floor without deleting a rule: measure sections first, rotate by derivability, two edits per rotation. Load in /release step 6 and before any CLAUDE.md rotation or split."
---
# CLAUDE.md budget

Authority: CLAUDE.md Maintenance Practice 5 (the whole forensic record),
`tests/test_claude_md_size.py`, `tests/test_claude_md_rotation.py`,
`tests/test_key_files_split.py`, `tests/test_cli_env_split.py`;
the Floor is `claude_md.max_chars` (`uv run python -m harness check
claude_md.max_chars`).

1. **Measure sections by heading and sort by size BEFORE choosing** what
   to rotate; twice the answer was a section nobody suspected.
2. **A rotation is TWO edits**: the release moves out of Current State AND
   the "Older releases (X and earlier)" boundary moves; the rotation test
   fails naming both numbers.
3. **What is derivable leaves, what is not stays**: a description
   jcodemunch can answer live goes to `KEY-FILES.md` / `CLI-AND-ENV.md`;
   a prohibition, a constraint whose violation causes a defect, or a
   rationale stays. Adding a name to the split tests' rationale lists to
   buy budget is the thing the split exists to stop.
4. **Rotated text goes to `ISSUE-HISTORY.md` verbatim**; ask what lesson
   it earned and put that one line in Standing lessons.
5. **The target must be tracked**: `git check-ignore` the path before
   writing (`docs/*` is ignored; `docs/standard/`, `docs/harness/`,
   `docs/cicd/`, `docs/workflows/` are re-included).
6. Never raise the Floor to make room; never delete a warning-marked rule.
