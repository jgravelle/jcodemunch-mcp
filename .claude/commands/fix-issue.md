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
2. **Branch.** Not `main`. `fix/$ARGUMENTS-<slug>` from `origin/main`;
   refuse if the branch has no `.claude/commands/` (the layer is not on
   `main` yet, W-22). Clear `.claude/state/evidence/`. ⚠ The automatic
   hooks follow the session's cwd (W-30); run from the checkout you are
   fixing in.
3. **Reproduce BEFORE touching `src/`.** Write a test from the report. For
   a destructive defect the target must be one the test owns (Practice 8;
   Standing lesson 08-20). Run it:
   `{ uv run pytest <file> -q --continue-on-collection-errors; echo "EXIT=$?"; } > .claude/state/evidence/red.txt 2>&1`
   (`--continue-on-collection-errors` because a pre-existing guard that
   imports a name the defect removed fails at COLLECTION and would abort
   the session before your test runs, W-32; a collection ERROR in red.txt
   is itself evidence and goes in ISSUE.md). If the last line is `EXIT=0`,
   write what you tried into ISSUE.md under
   "Not reproduced" (commands, platform, versions) and
   `REFUSED: not reproduced`. Do not guess a fix.
4. **Archaeology.** Grep `docs/harness/ARCHAEOLOGY.md` for the touched
   module names and the issue's keywords; write `evidence/archaeology.md`
   as one table row per hit (`| file | ARCHAEOLOGY line | why related |`)
   plus the Standing-lesson lines from CLAUDE.md that name the defect
   class. If the defect is a regression of a
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
   why in ISSUE.md or refuse. Load `changelog-format` and write the
   `[Unreleased]` entry NOW (it cites `#$ARGUMENTS` and the reporter;
   what was wrong, why, what is impossible now; numbers only from
   evidence): the reviewer and the checklist read the CHANGELOG diff, and
   `scripts/dod_changelog.py` sees only COMMITTED changes (W-31). Then
   COMMIT the test, the fix and the entry (`git add <files> && git commit
   -m ...` alone; the hook runs the fast tier and writes `evidence/fast.md`).
6. **Tiers.** `python .claude/hooks/run_full.py` on the committed tree
   (the stamp covers the code roots, so the docs edits of step 8 will not
   invalidate it); and if `benchmarks/`, `harness/` or
   `src/jcodemunch_mcp/server.py` changed,
   `uv run python -m harness bench --offline --summary .claude/state/evidence/bench.md`.
   Read the skip-ceiling rows. Then
   `python .claude/hooks/dod_checklist.py --base-ref origin/main` (writes
   `evidence/checklist.md` and `evidence/surface.md`, both reviewer inputs).
7. **Review.** Spawn the `reviewer` subagent (fresh context; if the type
   is absent, `general-purpose` reading `.claude/agents/reviewer.md`, W-12)
   with the committed diff `git diff origin/main...HEAD` (deletions
   first), ISSUE.md, `evidence/*`, and the diffs of
   `harness/thresholds.json`, `harness/retired.json`, `docs/*/FINDINGS.md`,
   `CHANGELOG.md`. Save to `evidence/review.md`. `REQUEST CHANGES`: fix,
   commit, back to step 6. `BLOCK`: refuse with reasons.
8. **Record.** `pr-description`: title `fix: <one line> (#$ARGUMENTS)`,
   body to the SCRATCHPAD with `Closes #$ARGUMENTS`, the reproduction, the
   mechanism answer from step 5, and `checklist.md` and the review
   verdict verbatim.
9. **Open.** Any `unmet` row: refuse. Else push and `GITHUB_TOKEN="" gh pr
   create --body-file <scratchpad path>` as a line of its own. Print the
   URL. Never comment on the issue; GitHub closes it on merge and the
   close comment is the next layer's.
