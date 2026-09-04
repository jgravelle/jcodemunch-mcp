---
description: "Grade a PR, a ref, or the working tree against the Definition of Done in an isolated reviewer subagent. `--merge-check` adds the contributor-PR gate — trial merge onto origin/main in a scratch worktree, fast tier there, the diff's DELETIONS first, license/cla count on the head SHA. Verdict to chat and .claude/state/evidence/review.md; never posts."
argument-hint: [pr-number | ref] [--merge-check]
---

<!--
purpose:  the session that writes code does not grade it; and the loop
          where half of contributor merges needed our own fix within three
          commits gets its missing gate (what did this PR DELETE)
invokes:  gh pr diff/view/checks (read only); git worktree; uv run python -m
          harness fast --summary; gh api commits/<sha>/status (read); the
          reviewer subagent; docs/harness/ARCHAEOLOGY.md
produces: .claude/state/evidence/review.md (the verdict), trial_merge.txt and
          cla.txt in merge-check mode, deletions.diff
refuses:  to grade with no diff; to post, approve, request changes, label,
          comment or merge on GitHub
-->

Target: $ARGUMENTS

1. **Gather.** Clear `.claude/state/evidence/review*`, `deletions.diff`.
   - A PR number: `GITHUB_TOKEN="" gh pr diff N > evidence/pr.diff`;
     `GITHUB_TOKEN="" gh pr view N --json title,body,author,headRefOid,files,labels > evidence/pr.json`;
     `GITHUB_TOKEN="" gh pr checks N > evidence/pr_checks.txt` (the PR gate's
     verdicts; a job's summary is `gh run view <id> --log` when a Floor row is needed).
   - A ref: `git diff origin/main...<ref> > evidence/pr.diff`.
   - Nothing: `git diff origin/main > evidence/pr.diff` (the working tree).
   An empty diff: `REFUSED: nothing to review`.
2. **Deletions first.** `grep -n '^-' evidence/pr.diff | grep -v '^[0-9]*:---'`
   into `evidence/deletions.diff`; list every removed `def `, `class `,
   `assert`, test file, LICENSE line, README section and config default
   at the top of the reviewer's input. Half of contributor merges needed a
   follow-up for something the PR removed (LOOPS §1).
3. **Local evidence.** If `evidence/fast.md`, `full.md`, `bench.md`,
   `bench_table.md`, `surface.md`, `checklist.md` exist for this tree, pass
   them; otherwise run
   `python scripts/surface_diff.py --base-ref origin/main --summary .claude/state/evidence/surface.md`
   and `python .claude/hooks/dod_checklist.py --base-ref origin/main`, and
   say which harness summaries are absent (the reviewer counts an absent
   summary as unmet; it does not run the suite).
4. **`--merge-check` (contributor PRs; policy 3b, 3d, DoD 9).**
   `git worktree add <scratchpad>/trial origin/main`; there
   `git merge --no-commit --no-ff <head sha>` (fetch it via
   `git fetch origin pull/N/head:pr-N` first); on a conflict, record it and
   stop the merge-check with `CONFLICTING`. Else
   `{ uv run python -m harness fast --summary <abs>/evidence/trial_merge.md; echo "EXIT=$?"; } > .claude/state/evidence/trial_merge.txt 2>&1`
   in the worktree, then remove it. CLA:
   `GITHUB_TOKEN="" gh api repos/jgravelle/jcodemunch-mcp/commits/<head sha>/status --jq '"state=\(.state) count=\(.statuses|length)"' > .claude/state/evidence/cla.txt`.
   `count=0` means NOT SIGNED or NOT REPORTED; either way do not merge, and
   the redelivery line (CLAUDE.md policy 3d) is the human's to run.
   Profile the author first if the PR adds a vendor, provider or endpoint
   (policy 3c; three `gh api` GET queries).
5. **Reviewer.** Spawn the `reviewer` subagent (fresh context, not a
   fork) with: `deletions.diff`, `pr.diff`, the PR body or the spec, every
   `evidence/*` file, the ARCHAEOLOGY rows for touched or deleted test
   files, and the diffs of `harness/thresholds.json`, `harness/retired.json`,
   `docs/*/FINDINGS.md`, `CHANGELOG.md`. Save its output verbatim to
   `evidence/review.md` and print it.
6. **Stop.** The verdict is a draft. Do not `gh pr review`, comment,
   label, approve or merge; say so in one line and, for a contributor PR,
   print the merge-order rule (policy 3b) with the numbers from step 4.
