---
description: "Fix a GitHub issue — reproduce it as a failing test before touching source (stop if it cannot be reproduced), check ARCHAEOLOGY for the guard that should have caught it, fix minimally, run the tiers, independent review, changelog, PR referencing the issue, machine-produced checklist."
argument-hint: <issue number>
---

<!--
purpose:  the bug loop that fixes the mechanism, not the reported spelling
invokes:  gh issue view (read only); skills mechanism-not-instance,
          changelog-format, pr-description; docs/harness/ARCHAEOLOGY.md;
          uv run python -m harness fast|full|bench; .claude/hooks/run_full.py,
          .claude/hooks/dod_checklist.py; the reviewer subagent
produces: .claude/state/runs/fix-<n>-<UTC>/ISSUE.md, .claude/state/evidence/*,
          a branch fix/<n>-<slug>, a PR with `Closes #<n>`
refuses:  when the issue cannot be reproduced as a failing test (no fix is
          guessed); a fix touching modules the failing test does not reach
          without a stated reason; any unmet checklist row; a reviewer BLOCK
-->

Issue: #$ARGUMENTS

Steps, in order; a verdict comes only from a file under
`.claude/state/evidence/`. The step list is `docs/workflows/DESIGN.md`
§2.2; the rules are `docs/standard/STANDARD.md` and CLAUDE.md "Issue +
release policy". `REFUSED: <reason>` and stop when a step cannot complete.

1. **Read.** `GITHUB_TOKEN="" gh issue view $ARGUMENTS --comments`. Write
   `ISSUE.md` in `.claude/state/runs/fix-$ARGUMENTS-<UTC>/`: reporter, the
   claimed reproduction, version, platform, the one thing that is wrong
   in the reporter's words. If the issue carries more than one finding,
   say so (policy 1: one issue, one verdict) and fix the first; the split
   is `/triage-issue`'s draft, not this command's action.
2. **Branch.** Not `main`. `fix/$ARGUMENTS-<slug>` from `origin/main`.
   Clear `.claude/state/evidence/`.
3. **Reproduce BEFORE touching `src/`.** Write a test from the report. For
   a destructive defect the target must be one the test owns (Practice 8;
   Standing lesson 08-20). Run it:
   `{ uv run pytest <file> -q; echo "EXIT=$?"; } > .claude/state/evidence/red.txt 2>&1`.
   If the last line is `EXIT=0`, write what you tried into ISSUE.md under
   "Not reproduced" (commands, platform, versions) and
   `REFUSED: not reproduced`. Do not guess a fix.
4. **Archaeology.** Grep `docs/harness/ARCHAEOLOGY.md` for the touched
   module names and the issue's keywords; list the related LOAD-BEARING
   tests in `evidence/archaeology.md`. If the defect is a regression of a
   lesson already encoded there or in CLAUDE.md Standing lessons, say so
   in ISSUE.md and name HOW the guard was bypassed: a second spelling, a
   second call site, a mock that supplied the contract, a guard sampled
   after the work. Load the `mechanism-not-instance` skill.
5. **Fix minimally.** Then answer the skill's two questions in ISSUE.md:
   does the fix belong one layer down (a cache, a shared primitive, the
   authority the call site was reproducing)? What other spellings of the
   same input exist (`find_references` on the fixed symbol and its
   callers)? Add a test per spelling found. Re-run the touched files green:
   `{ uv run pytest <files> -q; echo "EXIT=$?"; } > .claude/state/evidence/green.txt 2>&1`.
   If the fix touches a module the failing test does not import, state
   why in ISSUE.md or refuse.
6. **Tiers.** `uv run python -m harness fast --summary .claude/state/evidence/fast.md`;
   `python .claude/hooks/run_full.py`; and if `benchmarks/`, `harness/` or
   `src/jcodemunch_mcp/server.py` changed,
   `uv run python -m harness bench --offline --summary .claude/state/evidence/bench.md`.
   Read the skip-ceiling rows.
7. **Review.** Spawn the `reviewer` subagent (fresh context) with the diff
   (deletions first), ISSUE.md, `evidence/*`, `evidence/archaeology.md`,
   and the diffs of `harness/thresholds.json`, `harness/retired.json`,
   `docs/*/FINDINGS.md`, `CHANGELOG.md`. Save to `evidence/review.md`.
   `REQUEST CHANGES`: fix, back to step 6. `BLOCK`: refuse with reasons.
8. **Record.** `changelog-format`: the `[Unreleased]` entry cites
   `#$ARGUMENTS` and credits the reporter by login; what was wrong, why,
   what the fix makes impossible; numbers only from evidence.
   `python .claude/hooks/dod_checklist.py --base-ref origin/main`.
   `pr-description`: title `fix: <one line> (#$ARGUMENTS)`, body with
   `Closes #$ARGUMENTS`, the reproduction, the mechanism answer from step
   5, and `checklist.md` and the review verdict verbatim.
9. **Open.** Any `unmet` row: refuse. Else commit, push, `GITHUB_TOKEN=""
   gh pr create`. Print the URL. Never comment on the issue; GitHub closes
   it on merge and the close comment is the next layer's.
