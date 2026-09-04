# DESIGN — the workflow set: commands, hooks, review subagent, skills, settings

2026-09-04, from `docs/workflows/LOOPS.md` (branch `workflows/loops`,
`afa9c36`). Answers "how does work get done here?" on top of STANDARD.md
(what good means), the harness (measures it) and `docs/cicd/` (enforces it
on every change). Nothing below restates a threshold, a corpus path, a
tool count or a check name; every one is read at run time from
`harness/thresholds.json`, `harness/__main__.py`, `scripts/*`,
`pr-gate.yml` or the docs named.

## 0. Decisions this design rests on

| # | Decision | Why | Reversible? |
|---|---|---|---|
| D1 | **Workflow files live in the repo, tracked, under `.claude/`**: `.claude/commands/*.md`, `.claude/agents/*.md`, `.claude/hooks/*.py`, `.claude/skills/*/SKILL.md`, `.claude/settings.json`. `.gitignore:58` narrows from `.claude/` to `.claude/settings.local.json`, `.claude/*.bak`, `.claude/state/`. The sdist exclusion of the whole `.claude/` directory in `pyproject.toml` is UNCHANGED and `tests/test_sdist_exclusions.py` keeps asserting it. | The v0.2.6 leak vector was the SDIST bundling `settings.local.json`; that exclusion stays. A gitignored workflow is machine-local and gone on a fresh checkout, the trap CLAUDE.md documents for the release skill. | Yes: the alternative is a tracked `workflows/` tree symlinked in by an install step; every file's content is identical either way. **jjg's call; assumed (a) here.** |
| D2 | The existing `.claude/agents` and `.claude/skills` **symlinks into `C:\MCPs\.claude\`** are replaced by real directories in this repo. The suite-level `release` and `observatory` skills and `spokesperson` agent are COPIED in, byte-identical, so nothing this box loads changes; the suite copies stay for jdoc/jdata. | Tracked beats shared-by-symlink; a symlink into a sibling directory cannot be committed. | Yes. |
| D3 | Hooks are **project-scoped** entries in `.claude/settings.json`, added beside (never replacing) the user-scoped product hooks in `~/.claude/settings.json`. | The product hooks (`hook-pretooluse` steering, reindex, snapshot) are jcodemunch features, not repo process. | — |
| D4 | The pre-commit and pre-PR "events" are `PreToolUse` hooks matched on `Bash` and `PowerShell` whose `tool_input.command` contains `git commit` or `gh pr create`. Claude Code 2.1.260 has no commit or PR event (LOOPS §3.4). | Substitution, documented; not a fragile workaround: a `git commit` typed by the human outside the agent is NOT hooked and never was. | — |
| D5 | The "full tier passed recently" check keys on the **git tree hash** (`git rev-parse HEAD^{tree}` plus a hash of the unstaged diff), not on wall-clock minutes. | A full run on the identical tree is valid whatever its age; a time window is a proxy for tree identity and admits a run on a different tree. No N to restate. | — |
| D6 | Every command's completion checklist is written by a SCRIPT (`.claude/hooks/dod_checklist.py`) from evidence files, and pasted into the PR body verbatim. The agent marks nothing by hand. | Principle 4: the checklist is produced, not remembered. | — |
| D7 | Commands refuse by printing `REFUSED: <reason>` and stopping; nothing partial is pushed. Hooks block with exit 2 and a one-line reason; a hook past its budget prints `WARNING: <hook> skipped <what> (budget <s> s)` and exits 0. | Principle 3; the brief's "never silently passes". | — |
| D8 | `.claude/settings.json` `permissions.deny` forbids publish, tag, force-push, merge and every posting verb for the WHOLE session, including when a human asks. The human runs those lines from their own prompt (RUNBOOK §1: merge and dispatch are the human acts). | The brief's settings rule. The 2026-09-04 session dispatched `release.yml` from the agent; under this design that line is handed over in cmd.exe form. | jjg can loosen `deny` in `settings.local.json`, which wins locally and is untracked. |

## 1. Common shape of every command

Each `.claude/commands/<name>.md` starts with a header:

```
<!--
purpose:  one sentence
invokes:  harness tiers / scripts / skills / subagents, by name
produces: artifacts, by path
refuses:  the conditions under which it stops with REFUSED
-->
```

Conventions:

- **State** goes to `.claude/state/` (gitignored): `runs/<name>-<UTC>.md`
  (the run record), `full-tier.json` (the D5 stamp), `evidence/` (harness
  summaries, bench tables, surface diffs for the current branch).
- **Branch**: every command that edits creates or checks out a branch with
  the prefix it names; on `main` it refuses (`enforce_admins` would refuse
  the push anyway; refusing earlier saves the work).
- **Harness calls are verbatim** from `docs/harness/DESIGN.md` §2:
  `uv run python -m harness fast --summary <evidence>/fast.md`,
  `uv run python -m harness full --summary <evidence>/full.md`,
  `uv run python -m harness bench --offline --summary <evidence>/bench.md`,
  `uv run python -m harness check <id>`. Never `pytest` directly except the
  touched files (Practice 10).
- **Numbers**: a command may write a number into a CHANGELOG line, PR body
  or release note only by pasting it from an evidence file produced in the
  same run. The review subagent checks for any other number.
- **Skills** are loaded by name at the step that needs them (§5).
- **Checklist**: `python .claude/hooks/dod_checklist.py --evidence
  .claude/state/evidence --base-ref origin/main` emits the twelve
  STANDARD.md DoD items as `met / unmet / n.a.` with the evidence path per
  row (§1.1). A command that opens a PR refuses on any `unmet`.

### 1.1 What `dod_checklist.py` reads, per DoD item

| DoD | Evidence | n.a. when |
|---|---|---|
| 1 failing-then-passing test | `evidence/red.txt` (touched test files run at the base ref, expected non-zero) and `evidence/green.txt` (same at HEAD, zero) | never for a `fix` or `feat`; docs-only PRs |
| 2 ruff + touched files + full tier + skip count | `evidence/fast.md` (ruff is inside the fast tier), `evidence/touched.txt`, `evidence/full.md`; skip count is the full tier's own `ci.skips_*`/`suite.fast_skips_max` verdict lines | never |
| 3 CHANGELOG line | `scripts/dod_changelog.py --base-ref origin/main` exit code | `no-changelog` label present |
| 4 tool added / description changed | `scripts/surface_diff.py --base-ref origin/main`; plus a description diff over `_build_tools_list()` output (surface_diff reports names only; the description half is FINDINGS W-1 until the script exposes it) | no diff |
| 5 benchmark mirrors | `tests/test_provenance.py`, `tests/test_schema_budget.py` in `evidence/touched.txt` when `benchmarks/` changed | `benchmarks/` untouched |
| 6 config/env/CLI row | `tests/test_cli_env_split.py`, `tests/test_config_docs_reverse_parity.py` run when `config.py` or `cli/` changed | untouched |
| 7 background-behaviour disclosure | README diff contains a change under "Background behavior, fully disclosed" when the diff adds a thread, a socket, a scheduled task or a file outside the store (grep over the diff for `threading.`, `socket.`, `httpx.`, `schedule`) | no such addition |
| 8 `*_basis` beside a published rate | `tests/test_standard_invariants.py` in touched; a grep of the diff for new `_pct`/`_rate`/`_share`/`confidence` keys without a sibling `_basis` | no new rate |
| 9 contributor PR trial-merged, CLA present | `evidence/trial_merge.txt`, `gh api .../commits/<sha>/status` count | our own PR |
| 10 fast tier; bench when `benchmarks/`, `harness/` or `server.py` changed | `evidence/fast.md`, `evidence/bench.md` | bench: untouched |
| 11 retired test has a ledger entry | `tests/test_retirement_ledger.py` in touched when a `tests/` file was deleted | no deletion |
| 12 threshold moved with history/loosened | `harness/thresholds.json` diff parsed: every changed `floor` has `history` appended or a `loosened` block | untouched |

## 2. The commands

### 2.1 `/feature <description>`

Branch prefix `feat/`. Loop 3, LOOPS §2.3; the 94% DoD-incomplete rate.

| Step | Does | Invokes | Produces |
|---|---|---|---|
| 1 spec | Restates the request as `SPEC.md` with acceptance criteria, each mapped to a STANDARD.md criterion (1-10, N1-N7) by number and to the Floor ids it could move (`python -m harness thresholds` is the list) | skill `standard-axes` | `.claude/state/runs/<run>/SPEC.md` |
| 2 surface impact | Asks: does any tool get added, removed, renamed, gain an argument, or change its description? Answers from `python scripts/surface_diff.py --base-ref origin/main` (names) and a `_build_tools_list()` description dump (W-1). If yes: records that README, CLAUDE.md/KEY-FILES, CHANGELOG and the schema baseline all change and that stage 5 (`done: tool surface`) will check; loads `tool-surface-discipline` | skill `tool-surface-discipline`, `scripts/surface_diff.py` | `evidence/surface.md` |
| 3 tests first | Writes the failing tests in the tier the change belongs to: a new file in `tests/` is in the FULL tier automatically; adding it to `harness/tiers.json` `fast` is a judgment stated in the PR (offline, under the ceiling). Runs them: must FAIL. | `uv run pytest <files> -q` → `evidence/red.txt` | red.txt |
| 4 implement | Edits. `hook-posttooluse` reindexes; H2/H3 fire as applicable. | — | — |
| 5 fast tier | Runs on commit via H1; the command also runs it explicitly once before step 6 | `harness fast --summary` | `evidence/fast.md` |
| 6 full tier | Explicit, once | `harness full --summary`; writes the D5 stamp | `evidence/full.md`, `state/full-tier.json` |
| 7 bench delta | `harness bench --offline --summary evidence/bench.md`, then `python scripts/pr_bench_comment.py --base-ref origin/main --results harness/results/latest.json --summary evidence/bench_table.md` (no `--post`) for the per-criterion table: Floor, base, PR, delta, verdict | skill `benchmark-methodology` | `evidence/bench_table.md` |
| 8 review | Spawns the review subagent (§3) with SPEC.md, the diff, `evidence/*`. On REQUEST CHANGES: fixes, re-runs from step 5. On BLOCK: stops, reports. | agent `reviewer` | `evidence/review.md` |
| 9 record | CHANGELOG `[Unreleased]` entry (skill `changelog-format`: what was wrong, why, what the fix makes impossible; numbers pasted from evidence only); PR title + body from skill `pr-description` including the bench table and the surface diff verbatim; checklist from `dod_checklist.py` | skills | PR body |
| 10 open | `gh pr create` (H4 checks the stamp). Refuses if any checklist row is `unmet`. Never merges. | — | the PR |

Refuses: on `main`; a spec with no acceptance criterion mapped to a
criterion; step 3 tests that pass before the change; any `unmet` row;
a review BLOCK.

### 2.2 `/fix-issue <number>`

Branch prefix `fix/<number>-`. Loop 5, LOOPS §2.6; the instance-not-mechanism chains.

| Step | Does | Invokes | Produces |
|---|---|---|---|
| 1 read | `gh issue view N --comments`; extracts the claimed reproduction, version, platform | — | `runs/<run>/ISSUE.md` |
| 2 reproduce | Writes a failing test from the report BEFORE touching `src/`. Runs it. **If it does not fail, stops**: writes what was tried and refuses (`REFUSED: not reproduced`); no fix is guessed. A destructive defect is reproduced against a target the test owns (Practice 8, Standing lesson 08-20). | `uv run pytest <file> -q` | `evidence/red.txt` |
| 3 archaeology | `grep -n <touched module / issue keywords> docs/harness/ARCHAEOLOGY.md`: lists related LOAD-BEARING tests. If the defect is a regression of a lesson already there, the run record says so and names HOW the guard was bypassed (a spelling, a second call site, a mock). Loads `mechanism-not-instance`. | skill | `evidence/archaeology.md` |
| 4 fix | Minimal. Then asks the skill's two questions: does the fix belong one layer down; what other spellings of the same input exist (`find_references` on the fixed symbol). | jcodemunch tools | — |
| 5-8 | fast (hook), full, bench if `server.py`/`benchmarks/`/`harness/` touched, review subagent | as 2.1 | evidence |
| 9 record | CHANGELOG entry citing `#N` and the reporter; PR body `Closes #N` (GitHub closes it on merge; the workflow never comments); checklist | skills | PR |

Refuses: not reproduced; a fix touching more than the modules the failing
test imports without a stated reason; `unmet` rows; BLOCK.

### 2.3 `/release`

No branch until step 6 (`release/<version>`). Loop 1, LOOPS §2.1.

| Step | Does | Invokes |
|---|---|---|
| 1 main green | Reads the last completed `main.yml` run on `origin/main` HEAD: `python scripts/release_preflight.py --version <derived> --ci --no-harness` is the authority (the two `main:` witnesses). Refuses on anything but PASS. | `scripts/release_preflight.py` |
| 2 version | Derives it and prints the derivation: latest `v*` tag by version sort; `pyproject.toml` version; both must be equal (a pending bump means a release is already in flight → refuse). Next = patch + 1 under the `1.108.N` scheme; a minor or major bump needs `--minor`/`--major` and a stated reason. Loads `version-scheme`. | skill |
| 3 changelog | `[Unreleased]` must be non-empty; every bullet is matched to a merged PR since the last tag (`gh pr list --state merged --search "merged:>=<tag date>"`), by `#N`, by branch name or by the bullet's file paths appearing in the PR diff; unmatched bullets and unmentioned PRs are both listed. Refuses on an unmatched bullet (a line describing nothing that merged). | `gh` |
| 4 recompute | Tool counts per profile from `jcodemunch-mcp surface` (the only source, CLAUDE.md "Tool count"); the headline benchmark figures from the CI-captured `benchmarks/jcm_reference.json`; language counts from `LANGUAGE_REGISTRY`. Compares each against what README.md and CLAUDE.md claim (the existing ratchets: `tests/test_provenance.py`, `tests/test_schema_budget.py`, `tests/test_readme_tool_count.py` family, `tests/test_claude_md_size.py`, `tests/test_claude_md_rotation.py`). **Any disagreement is reported and the run refuses**; it never edits the doc to match. | pytest on the named files; `harness check claude_md.max_chars` |
| 5 notes | Release-notes draft rendered from the CHANGELOG block (the same rendering `release.yml` uses, `PYTHONIOENCODING=utf-8`), to the scratchpad, never the repo (Standing lesson 08-28) | — |
| 6 release PR | Measures CLAUDE.md sections BEFORE the rotation (skill `claude-md-budget`); runs the bump over the seven pin sites (enumerated by grep for the old version, `git check-ignore` on each hit), cuts the block, rotates Current State (two edits), appends ISSUE-HISTORY; runs the mirror ratchets again; commits on `release/<version>`; pushes; opens the PR labelled `release` with the notes draft as body. | skill; `harness fast` via H1 |
| 7 stop | Prints RUNBOOK §1 steps 2-4 for the human: merge when green, then dispatch (cmd.exe form). **Does not merge, tag, dispatch, upload or publish.** | — |

Refuses: pre-flight not PASS; tag/pyproject disagree; empty or unmatched
`[Unreleased]`; any recomputed figure disagreeing with a doc; a MERGEABLE
CLEAN contributor PR open (policy 3b, pre-flight reports it); CLAUDE.md
over budget after the rotation.

### 2.4 `/benchmark-compare [ref]`

No branch. Loop 4, LOOPS §2.5.

| Step | Does |
|---|---|
| 1 current | `uv run python -m harness bench --offline --write-results --summary evidence/bench_cur.md` on the working tree, in the deterministic configuration the tier already uses (offline, pinned corpora, `JCODEMUNCH_*` env cleared as `tests/conftest.py` does) |
| 2 ref | `git worktree add <scratch>/ref <ref>` (default `origin/main`), same command there; worktree removed after. Never `git checkout` in the working tree. |
| 3 table | Per threshold id present in either run: Floor (from `thresholds.json` via `harness threshold <id>`), ref value, current value, delta, PASS/FAIL. Rendered by `scripts/pr_bench_comment.py` where the id is one it knows, otherwise from the two `latest.json` files (W-2). Per row, never per total (F-13). |
| 4 record | On `main` with a clean tree: `harness/results/latest.json` was already written by step 1; the command says it is uncommitted and that the weekly `main.yml` results PR is the path that commits it (a bot push to main cannot). Elsewhere: the table and both JSONs go to `.claude/state/evidence/` and the run record names the paths. |

Refuses: a dirty tree when asked to record on `main`; a ref that does not resolve.

### 2.5 `/review [pr-number | ref | (working tree)]`

No branch. Loops 2 and 8. Gathers the inputs and spawns the reviewer
(§3) standalone: for a PR number, `gh pr diff N`, `gh pr view N --json
body,files`, the PR-gate summaries via `gh run view --log` of the head's
runs (or the harness run locally when absent); for a ref or the tree,
`git diff origin/main...`, and the local evidence. **Merge mode** (`/review
N --merge-check`), for the contributor-PR loop: trial-merges onto a
scratch worktree of `origin/main` (never the working tree), runs the fast
tier there, lists the diff's DELETIONS first (removed functions, tests,
LICENSE lines, config defaults flipped), checks `license/cla` on the head
SHA (count, policy 3d), and reports. It never merges, comments, approves
or requests changes on GitHub; the verdict goes to `evidence/review.md`
and the chat. Posting is the next layer's.

### 2.6 `/triage-issue <number>`

No branch, no edits. Loop 7. Reads the issue and the last twenty issues'
titles (duplicates), classifies bug / feature / question / duplicate /
security / dependency, drafts: the label recommendation (from `gh label
list`), a split proposal when the body carries more than one finding
(policy 1), a reproduction request or a timebox WITH its default (policy
3a, skill `pr-description`'s comment half), and for a vendor-shaped
request the three profile queries of policy 3c. Hands the draft to the
`spokesperson` agent for the outward-bound pass. Posts nothing; writes
`runs/<run>/TRIAGE.md`.

## 3. The review subagent (`.claude/agents/reviewer.md`)

**Isolation:** spawned with `subagent_type: reviewer` (fresh context, not
`fork`). Receives ONLY: the diff (deletions listed first, then additions),
`SPEC.md` or `ISSUE.md`, the harness summaries, the bench table, the
surface diff, the ARCHAEOLOGY lines for every touched test file, the
`thresholds.json` and `harness/retired.json` diffs, the FINDINGS diffs
under `docs/`, and the CHANGELOG diff. It does not receive the
implementer's conversation, run record, or reasoning. Tools: Read, Grep,
Glob, Bash (read-only git; the settings deny list applies).

**Prompt requirements, in order:**

1. For each of the twelve DoD items: `met / unmet / n.a.` with the evidence
   line quoted (a path and a line, or the harness verdict line).
2. Weakening scan over the diff: removed `assert` lines, `assert` turned
   into `if`/`print`, added `pytest.mark.skip`/`skipif`/`xfail`/`pytest.skip(`,
   deleted `def test_`, deleted test files, widened `except`, a changed
   `floor` in `thresholds.json` without `history`/`loosened`, a
   `harness/retired.json` entry without a replacement assertion named. Each
   hit is a finding with severity.
3. Seam scan: any change under `src/` in a PR whose spec is a benchmark,
   harness, CI or docs change is a product-code seam and needs a FINDINGS
   entry in the same PR (`docs/harness/FINDINGS.md` "Product-code seams",
   `docs/cicd/FINDINGS.md`, or `docs/workflows/FINDINGS.md`).
4. Copied-number scan: every number in the CHANGELOG diff, PR body, README
   diff, CLAUDE.md diff and code comments must appear in an evidence file
   from this run or be a Floor read from `thresholds.json`; a tool count,
   test total, token figure, latency or percentage that appears nowhere in
   the evidence is a finding (Practice 4; LOOPS §2.1, §2.5).
5. Surface creep: a new tool, a new always-visible tool, a Counter change,
   or a `core` description growth; reads `evidence/surface.md` and the
   `schema.core_compact_ceiling` verdict; cites STANDARD.md criterion 4.
6. Mechanism check for fixes: does the diff fix the reported spelling or
   the property? Names the other spellings it looked for.
7. Verdict: `APPROVE`, `REQUEST CHANGES`, or `BLOCK`, reasons ordered by
   severity. **BLOCK only for**: a Floor violation in the harness output, a
   deleted or skip-marked LOAD-BEARING test without a `retired.json` entry,
   a loosened threshold without a `loosened` block, or a change under
   `.github/workflows/release.yml`, `scripts/release_preflight.py`,
   `scripts/handshake.py` or `scripts/registry_verify.py` without a
   `docs/cicd/RUNBOOK.md` change. Everything else is at most REQUEST CHANGES.
8. Output format is fixed (a table for 1, a list for 2-6, one verdict
   line), so two runs on the same inputs are comparable line by line.
   Phase 4 runs the same diff twice in fresh contexts and diffs the output.

## 4. Hooks (`.claude/hooks/`, wired in `.claude/settings.json`)

All hooks are Python, run as `python .claude/hooks/<name>.py`, read the
hook JSON on stdin, and implement the budget themselves with
`subprocess.run(timeout=)` so that a timeout degrades (D7) instead of the
runner killing the hook silently. `timeout` in settings is set to budget
plus 10 s as the backstop.

| Hook | Event / matcher | Budget | Exact command run | Blocks? |
|---|---|---|---|---|
| H1 `pre_commit.py` | `PreToolUse`, matcher `Bash\|PowerShell`, fires when `tool_input.command` matches `\bgit\s+commit\b` | 150 s | If no staged path is under `src/`, `tests/`, `harness/`, `scripts/`, `benchmarks/harness/` or `.github/`: exit 0 (docs commits are free). Else: `uv run python -m harness fast --summary .claude/state/evidence/fast.md` (ruff check and the offline Floor verdicts are inside it); then the format check with the SAME scope and command as `pr-gate.yml` job `fast: format` (read from the workflow file at run time, never restated; `tests/test_workflow_hooks.py` binds them); then `uv run python -m harness check types.error_max` only if pyright is importable, else WARNING naming it. | Exit 2 on any FAIL with the verdict lines as the reason; WARNING + exit 0 past budget, naming which of the three was skipped. |
| H2 `test_edit_guard.py` | `PostToolUse`, matcher `Edit\|Write`, when `tool_input.file_path` is under `tests/` | 5 s | `git diff -- <file>` (and `git status --porcelain <file>` for a deletion): counts removed `def test_`, removed `assert`, added `pytest.mark.skip`/`skipif`/`xfail`/`pytest.skip(`. If any, and `harness/retired.json` is unchanged in the working tree AND the file is LOAD-BEARING in `docs/harness/ARCHAEOLOGY.md`: prints the ARCHAEOLOGY line and "retirement needs a `retired.json` entry naming the lesson and the replacement assertion (DoD 11) and a commit message stating the lesson". | Exit 2 (the message reaches the agent as feedback; the edit stands). |
| H3 `surface_guard.py` | `PostToolUse`, matcher `Edit\|Write`, when the path is `src/jcodemunch_mcp/server.py`, `counter.py`, `cli/policy.py`, any `tools/*.py`, or `encoding/schemas/*` | 40 s | `python scripts/surface_diff.py --base-ref HEAD` (working tree vs HEAD). On a non-empty diff: "tool surface changed: +a -r; README, CLAUDE.md/KEY-FILES, CHANGELOG and the schema baseline change with it (DoD 4); stage 5 checks". Also runs the description dump when W-1 is closed. | Warning only (exit 2 message, no block). |
| H4 `pre_pr.py` | `PreToolUse`, `Bash\|PowerShell`, command matches `\bgh\s+pr\s+create\b` | 5 s | Reads `.claude/state/full-tier.json` `{tree, ok, date, commit}`; computes the current tree id (`git rev-parse HEAD^{tree}` + sha256 of `git diff` output); requires `ok` and equal tree; requires the branch is not `main`; requires `.claude/state/evidence/checklist.md` exists with no `unmet`. | Exit 2 with the missing item named. |
| H5 `deny_guard.py` | `PreToolUse`, `Bash\|PowerShell` | 1 s | Belt to D8's braces: matches the same verbs as the deny list (`git push --force`, `git tag`, `gh release`, `gh workflow run`, `gh pr merge`, `gh pr comment/review`, `gh issue comment/close/edit`, `twine upload`, `mcp-publisher publish`, `gh api` with `--method POST/PATCH/PUT/DELETE`) and blocks with the RUNBOOK section the human runs. | Exit 2. |

The D5 stamp is written by the command's full-tier step, not by the
harness: `harness full` prints `HARNESS PASS` and writes `latest.json`
only under `--write-results` (a tracked file, so the command must not use
it on a branch). The wrapper `.claude/hooks/run_full.py` runs the tier
with `--summary`, parses the final `HARNESS PASS|FAIL` line, and writes
the stamp. FINDINGS W-3: a `--stamp-file PATH` on the harness would remove
the wrapper.

## 5. Skills (`.claude/skills/<name>/SKILL.md`)

Each under 2 KB, front-matter `description` states when to load, body
points at the authority and holds only what no doc states in list form.

| Skill | Loaded by | Points at | Holds |
|---|---|---|---|
| `benchmark-methodology` | `/feature` 7, `/benchmark-compare`, reviewer | `docs/harness/ARCHAEOLOGY.md` §benchmarks, `benchmarks/METHODOLOGY.md`, Practice 4 | the numbered methodology rules AS A LIST OF POINTERS to their ARCHAEOLOGY lines (never restated); the per-row-never-per-total rule (F-13); the five mirrors |
| `version-scheme` | `/release` 2 | RUNBOOK §1, `release_preflight.py` | the derivation: latest tag = pyproject, next = patch + 1; the seven pin sites are enumerated by grep, never listed |
| `tool-surface-discipline` | `/feature` 2, reviewer | STANDARD.md criterion 4, CLAUDE.md "Tool-description quality", `tests/test_schema_budget.py`, `tests/test_counter_surface_stability.py` | the small-front-door principle in three sentences; what counts as surface change (name, argument, description, tier, Counter) |
| `pr-description` | `/feature` 9, `/fix-issue` 9, `/triage-issue` | CLAUDE.md Output Rules, `spokesperson` | the template: what was wrong / why / what is now impossible / evidence table (bench, surface, checklist pasted) / closes; the comment half: deadline + default only (policy 3a) |
| `changelog-format` | `/feature`, `/fix-issue`, `/release` | `CHANGELOG.md` top block, `scripts/dod_changelog.py` | heading shape `## [X.Y.Z] - date - thesis`; an entry argues (Output Rules carve-out); credit the reporter; numbers only from evidence |
| `standard-axes` | `/feature` 1 | `docs/standard/STANDARD.md` | the criterion NUMBERS and one-line names, and the instruction to map every acceptance criterion to one |
| `mechanism-not-instance` | `/fix-issue` 3-4, reviewer | CLAUDE.md Standing lessons (08-19, 09-01, 09-02), `docs/harness/ARCHAEOLOGY.md` | the two questions (one layer down? other spellings?) and the pointer to the lessons that earned them |
| `claude-md-budget` | `/release` 6 | Practice 5, `tests/test_claude_md_size.py`, `harness check claude_md.max_chars` | measure sections by heading first; rotation is two edits; derivable leaves, invariant stays |
| `release` (existing, copied in) | human, `/release` 7 | RUNBOOK §1 | REVISED: the publish half (steps 4-7) replaced by the RUNBOOK pointer and the cmd.exe dispatch line; the PR and CLA halves kept verbatim |
| `observatory` (existing, copied in) | as today | — | unchanged |

## 6. Settings (`.claude/settings.json`, tracked)

```json
{
  "permissions": {
    "deny": [
      "Bash(git push --force*)", "Bash(git push -f*)", "Bash(git push * --force*)",
      "Bash(git tag*)", "Bash(git push --tags*)", "Bash(git push origin v*)",
      "Bash(gh release*)", "Bash(gh workflow run*)", "Bash(gh pr merge*)",
      "Bash(gh pr comment*)", "Bash(gh pr review*)", "Bash(gh pr close*)",
      "Bash(gh issue comment*)", "Bash(gh issue close*)", "Bash(gh issue edit*)",
      "Bash(gh api --method POST*)", "Bash(gh api --method PATCH*)",
      "Bash(gh api --method PUT*)", "Bash(gh api --method DELETE*)", "Bash(gh api -X *)",
      "Bash(twine*)", "Bash(uvx --from twine*)", "Bash(*mcp-publisher*)",
      "PowerShell(git push --force*)", "PowerShell(git tag*)", "PowerShell(gh release*)",
      "PowerShell(gh workflow run*)", "PowerShell(gh pr merge*)", "PowerShell(*twine*)",
      "PowerShell(*mcp-publisher*)"
    ],
    "allow": [
      "Bash(uv run python -m harness*)", "Bash(uv run pytest*)", "Bash(uv run ruff*)",
      "Bash(python scripts/*)", "Bash(python .claude/hooks/*)",
      "Bash(GITHUB_TOKEN=\"\" gh pr create*)", "Bash(GITHUB_TOKEN=\"\" gh pr diff*)",
      "Bash(GITHUB_TOKEN=\"\" gh pr view*)", "Bash(GITHUB_TOKEN=\"\" gh pr list*)",
      "Bash(GITHUB_TOKEN=\"\" gh issue view*)", "Bash(GITHUB_TOKEN=\"\" gh issue list*)",
      "Bash(GITHUB_TOKEN=\"\" gh run list*)", "Bash(GITHUB_TOKEN=\"\" gh run view*)",
      "Bash(GITHUB_TOKEN=\"\" gh api repos/jgravelle/jcodemunch-mcp/*)",
      "Bash(git worktree*)", "Bash(git diff*)", "Bash(git log*)", "Bash(git status*)"
    ]
  },
  "hooks": { "...": "H1-H5 per section 4" }
}
```

`deny` wins over `allow` and over `settings.local.json`'s allow list.
`Bash(gh api repos/...)` stays allowed for GET; the `--method` and `-X`
forms are denied, which also covers webhook redelivery (policy 3d's one
POST) — that line is handed to the human. `settings.local.json` keeps its
40 accreted allows but loses the effect of `Bash(gh:*)` and `Bash(git *)`
where the deny list overlaps.

## 7. CLAUDE.md restructure

A new first section after `Current State`:

```
## How work is done here (2026-09-05)
Use these; do not improvise the process. Each one runs the harness at the
right moments and produces the Definition-of-Done checklist itself.
/feature <desc> · /fix-issue <n> · /release · /benchmark-compare [ref] ·
/review [pr|ref] · /triage-issue <n>
Authority: docs/standard/STANDARD.md (what good means, Definition of Done),
docs/harness/ARCHAEOLOGY.md (why every test exists), docs/cicd/RUNBOOK.md
(what a human does), docs/workflows/DESIGN.md (what each command does).
Hooks refuse a commit that fails the fast tier and a PR without a full-tier
run on this tree. Nothing in a session publishes, tags, merges or posts.
Adding a workflow: docs/workflows/DESIGN.md §8.
```

Moves (LOOPS §3.3): the required-check names paragraph in "The Standard
and the Harness" becomes one pointer to RUNBOOK §8; "Registry verification
reads a NESTED row" shrinks to the cmd.exe line, the `registry_verify.py`
pointer and the ⚠ nested-row rule (the measurements go to ISSUE-HISTORY);
"Reproducing CI's environment" keeps its ⚠⚠ lesson paragraph and points at
the full tier for the command. No ⚠ rule is deleted; every moved paragraph
lands in ISSUE-HISTORY.md or the doc named, verbatim. Budget: the additions
are ~700 chars, the three shrinks recover ~4,000; measured before and after
(Practice 5), gated by `tests/test_claude_md_size.py`.

## 8. Adding a workflow (the checklist a future session follows)

1. Add the loop to `docs/workflows/LOOPS.md` with its frequency, error
   evidence and criteria touched; re-rank.
2. Spec it in `DESIGN.md` §2 in the same table shape: steps, invokes,
   produces, refuses.
3. Write `.claude/commands/<name>.md` with the header comment; call the
   harness and the scripts; restate no threshold, corpus or number.
4. If it needs knowledge two commands share, a skill (§5), under 2 KB,
   pointing at the authority.
5. If it needs something the harness or a script does not expose, a
   `docs/workflows/FINDINGS.md` entry and graceful degradation, never a
   reimplementation.
6. Dry-run on a throwaway branch; record it in `VERIFICATION.md`.
7. Add its name to CLAUDE.md §"How work is done here" and to the deny/allow
   review: does it need a verb the deny list forbids? Then it is the next
   layer's, not this one's.
8. `tests/test_workflow_hooks.py` and `tests/test_workflows_registered.py`
   (Phase 3) fail if a command exists in `.claude/commands/` and not in
   CLAUDE.md or DESIGN.md, or if a hook command drifts from the workflow
   step it mirrors.

## 9. Known gaps going into Phase 3 (become FINDINGS entries when built)

| ID | Gap | Degradation |
|---|---|---|
| W-1 | `scripts/surface_diff.py` diffs names only; a description or argument change is invisible to it | the command dumps `_build_tools_list()` from both trees to JSON in the scratchpad and diffs descriptions itself; the reviewer treats a missing dump as `unmet` for DoD 4 |
| W-2 | `pr_bench_comment.py` knows the latency and token ids; other Floor ids (fidelity, replay, goldset, route) have no per-row base value outside `latest.json` | `/benchmark-compare` renders those rows from the two `latest.json` files; missing on either side prints `n/a`, never 0 |
| W-3 | the harness has no stamp-file output for "this tier passed on this tree" | `run_full.py` wrapper parses the `HARNESS PASS` line (§4) |
| W-4 | no machine-readable list of LOAD-BEARING files; H2 greps ARCHAEOLOGY's table | acceptable; the table format is stable and `tests/test_retirement_ledger.py` already parses it |
| W-5 | `claude --version 2.1.260` hooks cannot see the commit message, so "commit message states the lesson" (DoD 11) is checked by the reviewer over `git log`, not by H2 | documented |
| W-6 | reviewer determinism is not controllable; the fixed output format makes drift visible, not impossible | Phase 4 measures it |
