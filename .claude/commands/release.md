---
description: "Prepare a release the way RUNBOOK section 1 requires — confirm main is green, derive the next version and show the derivation, reconcile the changelog against merged PRs, recompute every published figure and refuse on a disagreement, draft the notes, open the labelled release PR, then STOP. Merging and dispatching are the human's acts; nothing here tags, uploads or publishes."
argument-hint: "[--minor | --major] [reason]"
---

<!--
purpose:  the release loop's 35 repair commits were mirrors not regenerated
          (pin sites, whatsnew, uv.lock, CHANGELOG block, CLAUDE.md counts)
          and releases cut on red; this command recomputes and refuses
invokes:  scripts/release_preflight.py; skills version-scheme,
          claude-md-budget, changelog-format; the mirror ratchets
          (tests/test_provenance.py, tests/test_schema_budget.py,
          tests/test_claude_md_size.py, tests/test_claude_md_rotation.py,
          tests/test_tools.py); jcodemunch-mcp surface;
          uv run python -m harness check claude_md.max_chars
produces: the derivation and reconciliation in .claude/state/runs/release-<UTC>/,
          the notes draft in the scratchpad (never the repo), a branch
          release/<version> with the bump commit, the release PR labelled
          `release`
refuses:  pre-flight not PASS; tag and pyproject disagree (a bump already
          pending); empty or unmatched [Unreleased]; any recomputed figure
          disagreeing with README or CLAUDE.md; a MERGEABLE CLEAN contributor
          PR open (policy 3b); CLAUDE.md over budget after the rotation
never:    merge, tag, dispatch release.yml, upload, publish, comment
-->

Arguments: $ARGUMENTS

Before anything: is the release being held for something that is not ours
(a signature, a contributor PR, a reply)? Then ship now (policy 2e). Load
the `release` skill for the PR and CLA halves; the publish half is
`docs/cicd/RUNBOOK.md` §1 and this command stops at its step 2.

1. **Main is green.** `git fetch origin`. The pre-flight reads the
   CHECKED-OUT HEAD, so run it on a worktree of `origin/main`, with this
   checkout's interpreter (a fresh worktree has no venv):
   `git worktree add <scratchpad>/relmain origin/main`, then in it
   `<repo>/.venv/Scripts/python.exe scripts/release_preflight.py --version <next> --ci --no-harness --dry-run`
   (derive `<next>` in step 2 first; `--dry-run` because the pins and the
   heading legitimately lag until step 6). It reads the two `main:`
   witnesses on that HEAD, the tag, PyPI, the 3b PRs and lint. Refuse on
   any FAIL other than `pins` and `changelog`; remove the worktree. After
   step 6 the release PR's own gate and `release.yml` run it for real.
2. **Version.** Load `version-scheme`. Print the derivation:
   `git for-each-ref refs/tags --sort=-v:refname --format='%(refname:short) %(creatordate:iso-strict)' | head -1`
   (never `git tag …`: the deny guard refuses the creating form and the
   read form is this one) and the `version =`
   line of `pyproject.toml`. They must be equal; if pyproject is ahead a
   release is already in flight — refuse. Next = patch + 1. `--minor` or
   `--major` in `$ARGUMENTS` needs a stated reason and bumps that field.
3. **Changelog reconciliation.** `[Unreleased]` must be non-empty. List
   the PRs merged since the last tag's date:
   `GITHUB_TOKEN="" gh pr list --state merged --search "merged:>=<tag date>" --json number,title,mergedAt,files --limit 100`,
   then DROP every row whose `mergedAt` is not after the tag's
   `creatordate` (the search is day-granular; eight of nine hits on the
   first dry-run were inside the previous release). Match every `[Unreleased]` bullet to a PR by `#N`, by branch name, or by
   a file path in the bullet appearing in the PR's files. Write the table
   (bullet → PR, PR → bullet) to the run directory. An unmatched bullet
   describes nothing that merged: refuse. An unmentioned PR that touched
   `src/` is listed as a finding for the human.
4. **Recompute, never copy.** Run the mirror ratchets:
   `uv run pytest tests/test_provenance.py tests/test_schema_budget.py tests/test_claude_md_size.py tests/test_claude_md_rotation.py tests/test_tools.py -q`.
   Recompute the counts `uv run jcodemunch-mcp surface` prints (visible in
   the served profile / catalog) and compare with CLAUDE.md "Tool count";
   every per-repo and grand-total figure in `benchmarks/jcm_reference.json`
   (CI-captured) against the same figure wherever README states it (the
   grand total AND the per-repo averages and ratios); `len(LANGUAGE_REGISTRY)`
   against README's language claim (a floor such as "70+" agrees when the
   count is at or above it). Write every pair (source value, doc value,
   agree?) to the run directory. **Any disagreement: report it and
   refuse.** Never edit the doc to match. (The first dry-run found the
   README per-repo averages disagreeing with the reference: FINDINGS W-16.)
5. **Notes draft.** After step 6 has cut the `## [<version>]` block,
   render it with the same Python `release.yml`'s "release notes" step
   uses, under `PYTHONIOENCODING=utf-8` on this console, to the scratchpad.
   Never write it into the repo (Standing lesson 08-28: a scratch file
   shipped in the sdist). Rendering `[Unreleased]` before the cut yields
   an empty body; the order is 6 then 5.
6. **Release PR.** Load `claude-md-budget`; measure CLAUDE.md sections by
   heading BEFORE editing (Practice 5). Branch `release/<version>` from
   `origin/main`. Enumerate the pin sites by grep for the old version
   (`--include=*.json --include=*.toml --include=*.lock`), `git
   check-ignore` each hit, and bump every tracked one; regenerate
   `whatsnew.json` (current + a new entry); cut `## [<version>] - <date> -
   <thesis>` from `[Unreleased]` (`changelog-format`); rotate Current State
   (two edits: the new entry and the "Older releases" boundary) and append
   the rotated entry to ISSUE-HISTORY.md verbatim. Run step 4's ratchets
   again and `uv run python -m harness check claude_md.max_chars`. Commit
   (the commit hook runs the fast tier), push, and
   `GITHUB_TOKEN="" gh pr create --label release` with the notes draft as
   the body. Run the pre-flight from step 1 again with the bump in place.
7. **Stop.** Print, for the human, RUNBOOK §1 steps 2-4 in cmd.exe form:
   merge when every required check is green, then
   `gh workflow run release.yml -R jgravelle/jcodemunch-mcp -f version=<version> -f dry_run=false`,
   read the run to the end, then Practice 11 (restart the local server).
   You do not merge, tag, dispatch, upload or publish. If the human asks
   you to, hand the line back; the deny list refuses it anyway.
