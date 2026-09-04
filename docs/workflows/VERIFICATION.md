# VERIFICATION — every workflow and hook, dry-run and deliberately failed (2026-09-05)

Branch `workflows/build`. Each row names what was run, on which tree, what
it did, and the evidence path. A row that says "not run" is a gap, not a
pass. Times are measured on this box (Windows 11, Python 3.12 via `uv`).

## 1. Hooks, on synthetic payloads (Phase 3 dry-runs)

Driven by `tests/test_workflow_hooks.py` (in-code payloads, scratch clones)
and the scratchpad runner; the hooks are LIVE in the building session, and
the first attempt to smoke-test H4 through a Bash line that carried the text
`gh pr create` was blocked by H4 itself (FINDINGS W-8). That block is the
first recorded firing of the layer.

| Hook | Input | Result | Evidence |
|---|---|---|---|
| H5 `deny_guard` | `gh release create`, `gh api --method POST`, `uvx --from twine …`, `git tag v9`, `gh workflow run`, `gh pr merge`, `gh issue comment`, `mcp-publisher publish`, `git push --force` | exit 2, reason names the RUNBOOK section or "drafts only" and the cmd.exe hand-over | `tests/test_workflow_hooks.py::test_deny_guard_refuses_exactly_the_forbidden_verbs` (14 cases, 5 negative) |
| H5 | `git tag --list`, `git push origin feat/x`, `gh pr view`, `gh api repos/...` (GET) | exit 0 | same |
| H4 `pre_pr` | `gh pr create` with no stamp / a stamp for another tree / a failed stamp / no checklist / an `unmet` row / on `main` | exit 2 each, distinct reason; exit 0 with a matching stamp and a clean checklist | `::test_pre_pr_refuses_without_a_stamp_and_passes_unrelated_commands` |
| H4 | same command in the building session, live | blocked the Bash call carrying the payload (W-8) | session log 2026-09-05 |
| H2 `test_edit_guard` | `pytestmark = pytest.mark.skip` prepended to `tests/test_result_cache.py` (LOAD-BEARING) with `harness/retired.json` untouched | exit 2; message quotes the ARCHAEOLOGY row and DoD 11 | `::test_test_edit_guard_blocks_a_skip_on_a_load_bearing_test` |
| H2 | same edit with `retired.json` touched | exit 0, WARNING via `additionalContext` naming the skip | same |
| H2 | an edit under `src/` | exit 0, silent | same |
| H2 | first run wrote the ARCHAEOLOGY row to a cp1252 stderr and died with `UnicodeDecodeError` on the reader side, so the BLOCK arrived as a traceback with no reason | fixed: `_common.py` reconfigures stdout/stderr to UTF-8 (the `encoding=` lesson, again) | commit `095a80e` |
| H3 `surface_guard` | an edit to `server.py` with the surface unchanged | exit 0, silent | `::test_surface_guard_is_silent_when_the_surface_did_not_move` |
| H3 | first version warned on every edit: the check looked for the word `added` and `surface_diff.py` prints `added none` | fixed: keys on the script's `no surface change` line | commit `78c2d6e` |
| H1 `pre_commit` | `git status` (not a commit) | exit 0 | scratch runner |
| H1 | `git commit -m x` with nothing staged under a code root | exit 0 (docs commits are free) | scratch runner |
| H1 | `git add -A … && git commit` in ONE Bash line with `tests/` in the add | **passed in 0.2 s without the fast tier**: the hook ran before the add and the index was empty (W-11) | commit `095a80e` took 0.2 s; fixed in `442f494` |
| H1 | the fast-tier and format-check paths against a real failure | see §3 (Phase 4) | — |

## 2. Ratchets

`tests/test_workflows_registered.py` (39 assertions): every command named
in CLAUDE.md and DESIGN.md (6 failed before the CLAUDE.md section existed,
the red arm), every command/hook/agent carries the header (caught
`spokesperson.md` on first run), no Floor value restated in a command or
hook (33 Floors × 11 files), every wired hook exists with a timeout, the
deny list covers the nine verbs, H1 reads the format scope from
`pr-gate.yml` rather than copying it, the sdist still excludes `.claude/`.

`uv run pytest tests/test_workflow_hooks.py tests/test_workflows_registered.py`:
56 passed. CLAUDE.md after the restructure: 139,019 of the
`claude_md.max_chars` Floor (`harness check` PASS); the four touched sections
measured before and after (Practice 5) are in the commit message of
`095a80e`.

## 3. Command dry-runs (Phase 3) and deliberate failures (Phase 4)

| Run | Tree / input | What happened | Evidence |
|---|---|---|---|
| `/triage-issue 574` (dry, drafts only) | `442f494`; the only open issue | 120 s. Classified per finding: A feature (tree-sitter 1.x opt-in, follow-on to #382, not a duplicate), B question/dependency (`mcp <2.0.0` never re-derived); the `bug` label wrong; split into two proposed issues with bodies; draft response with a 24 h timebox and its default; apply block in cmd.exe form. Nothing posted. Six ambiguities found and FIXED in the command: the 40-issue window missed the cited predecessor (#382), Standing-lessons/ARCHAEOLOGY paths unnamed, no rule for a wrong existing label, no home for the split bodies, timebox anchor unstated, "existing dependency" not excluded from vendor shape. Also: the agent could not write TRIAGE.md through a Bash heredoc because its apply block carries posting verbs (W-8); the command now says Write tool. Spokesperson step substituted (no nested spawn from an agent). | `.claude/state/runs/triage-574-20260904T173400Z/TRIAGE.md` |
| Reviewer probe A (threshold loosened `coverage.min` 74→64, no `loosened` block, no CHANGELOG, no summaries), run TWICE in fresh contexts | scratch diff `probe_loosen_threshold.diff` | **BLOCK both times.** Same three top reasons in the same order: DoD 12 loosening without `loosened`/`history`; the loader's `if hist:` blind side (harness F-20, a finding NEITHER the author nor the harness had); `guard_patterns`/`set_at.reason` still naming 74. Both graded DoD 2, 3, 10, 12 unmet and the rest n.a.; wording differed, verdict and reason set did not. Run 2 additionally flagged the spec ("tidy") as misdescribing the change. | agent outputs, 2026-09-05; `docs/harness/FINDINGS.md` F-20 |
| Reviewer probe B (`tests/test_result_cache.py` deleted, `retired.json` untouched, spec "covered elsewhere"), run TWICE | scratch diff `probe_delete_load_bearing.diff` | **BLOCK both times.** Both quoted the ARCHAEOLOGY row (line 314, LOAD-BEARING, the #572 `==`-not-`is` witness), both named `tests/test_retirement_ledger.py` as the test that would go red, and both DISPROVED the spec by grepping for the deleted assertions' subjects (`_RESULT_CACHE_MAXSIZE`, `result_cache_stats`, per-repo invalidation) across `tests/`, finding no replacement. Same DoD rows unmet (2, 3, 10, 11). | agent outputs, 2026-09-05 |
| Reviewer agent type | — | `Agent type 'reviewer' not found` in the session that wrote it; probes ran as `general-purpose` told to follow `reviewer.md` (W-12). The type appeared in the list minutes later without a restart; W-12 closed. | session log |
| `run_full.py` (full tier + stamp), first run | `442f494` | `HARNESS PASS`, 221 s, skip verdict rows PASS; stamp `ok=false, tree changed during the run`: pytest-cov's root-level `.coverage.<host>.<pid>` strays and my concurrent doc edits both moved the tree id (W-13). Fixed: the id ignores `.coverage*` and `.claude/state/`; concurrent tracked edits still invalidate, correctly. | `.claude/state/full-tier.json`, `evidence/full.md` |
