---
description: Add a feature the way this repo requires — spec mapped to STANDARD.md, failing tests first, harness tiers, bench delta, independent review, and a machine-produced Definition-of-Done checklist; opens the PR only when nothing is unmet.
argument-hint: <description of the feature>
---

<!--
purpose:  one entry point for "add a feature" that cannot skip a step
invokes:  skills standard-axes, tool-surface-discipline, benchmark-methodology,
          changelog-format, pr-description; scripts/surface_diff.py,
          scripts/pr_bench_comment.py, scripts/dod_changelog.py;
          uv run python -m harness fast|full|bench; .claude/hooks/run_full.py,
          .claude/hooks/dod_checklist.py; the reviewer subagent
produces: .claude/state/runs/feature-<UTC>/SPEC.md, .claude/state/evidence/*,
          a branch feat/<slug>, a PR whose body carries the bench table, the
          surface diff and the checklist verbatim
refuses:  on main; a spec with an acceptance criterion mapped to no
          STANDARD.md criterion; step-3 tests that pass before the change;
          any unmet checklist row; a reviewer BLOCK
-->

Feature request: $ARGUMENTS

Work through these steps in order. Do not skip one, do not reorder them,
and do not mark anything done by hand: every verdict comes from a file
under `.claude/state/evidence/`. Authority for every rule is
`docs/standard/STANDARD.md`; the step list is `docs/workflows/DESIGN.md`
§2.1. If a step cannot complete, print `REFUSED: <reason>` and stop.

1. **Branch.** `git rev-parse --abbrev-ref HEAD` must not be `main`. Create
   `feat/<slug>` from `origin/main` if you are not already on a `feat/`
   branch. Create `.claude/state/runs/feature-<UTC>/` and
   `.claude/state/evidence/`; delete stale files in `evidence/` first.
2. **Spec.** Load the `standard-axes` skill. Write `SPEC.md` in the run
   directory: the request restated in one paragraph, then acceptance
   criteria as a numbered list, each mapped to a STANDARD.md criterion
   number (1-10, N1-N7) and to the Floor ids it could move (run
   `uv run python -m harness thresholds` for the list; cite ids, never
   values). A criterion mapped to nothing is a refusal.
3. **Surface impact.** Load `tool-surface-discipline`. Answer in SPEC.md:
   does any tool get added, removed, renamed, gain or lose an argument, or
   change its description or tier? Decide from the design, then verify
   after implementing with `python scripts/surface_diff.py --base-ref
   origin/main --summary .claude/state/evidence/surface.md` and the
   description dump in step 7. If yes: state in SPEC.md that README,
   CLAUDE.md Key Files or KEY-FILES.md, CHANGELOG and
   `benchmarks/schema_baseline.json` all change in this PR and that stage
   5 (`done: tool surface`) checks it.
4. **Tests first.** Read `docs/harness/ARCHAEOLOGY.md` rows for every test
   file you will touch. Write the failing tests. A new file under `tests/`
   is in the full tier; add it to `harness/tiers.json` `fast` only if it is
   offline and you say so in the PR. Run them and record the red run:
   `{ uv run pytest <files> -q; echo "EXIT=$?"; } > .claude/state/evidence/red.txt 2>&1`
   — the last line must not be `EXIT=0`. If the tests pass before the
   change, the tests are wrong: refuse.
5. **Implement.** Use jcodemunch tools for navigation. The hooks
   `test_edit_guard` and `surface_guard` will speak if you weaken a test or
   move the surface; act on what they say. After editing, run the same
   files green: `{ uv run pytest <files> -q; echo "EXIT=$?"; } > .claude/state/evidence/green.txt 2>&1`.
6. **Fast and full tiers.** `uv run python -m harness fast --summary
   .claude/state/evidence/fast.md` (the commit hook runs it too), then
   `python .claude/hooks/run_full.py` (writes `evidence/full.md` and the
   tree stamp `pre_pr` requires). Read the skip-ceiling verdict rows in
   `full.md`, not only the exit code.
7. **Bench delta.** Load `benchmark-methodology`. If `benchmarks/`,
   `harness/` or `src/jcodemunch_mcp/server.py` changed, run
   `uv run python -m harness bench --offline --summary .claude/state/evidence/bench.md`.
   Then the per-criterion table against main:
   `python scripts/pr_bench_comment.py --base-ref origin/main --results harness/results/latest.json --summary .claude/state/evidence/bench_table.md`
   (never `--post`). Dump the descriptions on both sides for DoD 4 (FINDINGS W-1):
   `uv run python -c "from jcodemunch_mcp.server import _build_tools_list as b; import json; print(json.dumps({t.name: t.description for t in b()}, sort_keys=True))" > .claude/state/evidence/desc_head.json`
   and the same in a worktree of `origin/main` to `desc_base.json`; write
   the diff (or the line `no description changes`) to
   `evidence/surface_descriptions.md`.
8. **Review.** Spawn the `reviewer` subagent (fresh context, NOT a fork)
   with: the diff `git diff origin/main...HEAD` with deletions listed
   first, `SPEC.md`, every file in `evidence/`, the ARCHAEOLOGY rows for
   touched tests, and the diffs of `harness/thresholds.json`,
   `harness/retired.json`, `docs/*/FINDINGS.md`, `CHANGELOG.md`. Save its
   output to `evidence/review.md`. On `REQUEST CHANGES`: fix and return to
   step 6. On `BLOCK`: refuse with its reasons.
9. **Record.** Load `changelog-format` and write the `[Unreleased]` entry:
   what was missing, why it matters, what is now possible or impossible;
   any number pasted from an evidence file, none typed. Run
   `python .claude/hooks/dod_checklist.py --base-ref origin/main` and read
   `evidence/checklist.md`. Load `pr-description` and write the title and
   body; the body carries `bench_table.md`, `surface.md`,
   `surface_descriptions.md` and `checklist.md` verbatim, and the review
   verdict line.
10. **Open.** Any `unmet` row: refuse and say which. Otherwise commit (the
    commit hook runs the fast tier), push the branch, and
    `GITHUB_TOKEN="" gh pr create` with the body from step 9. Print the PR
    URL. Do not merge, do not comment, do not label beyond what
    `gh pr create --label` sets at creation.
