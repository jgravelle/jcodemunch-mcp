# CI/CD DESIGN — the harness's judgment, made unavoidable (2026-09-03)

Companion to `AUDIT.md` (what exists) and `docs/harness/DESIGN.md` (what the
harness measures). This document says which workflow invokes which harness
tier, when, what a failure blocks, and what stays human. Nothing here defines
a threshold: every Floor is read from `harness/thresholds.json` at run time
through `python -m harness` (principle 1), and every number a workflow writes
is computed in that run (principle 6).

Notation: **BLOCKS** = a required status check on `main`; **INFORMS** = posts
a comment, summary or artifact and cannot block; **OPENS ISSUE** = files an
issue on failure. Runtimes are estimates from the audit's run history and are
re-measured in Phase 4.

## 0. Decisions that shape everything else

| Decision | Choice | Why |
|---|---|---|
| Release trigger | **`workflow_dispatch` with a `version` input; the workflow creates the tag** after the pre-flight passes and Test PyPI succeeds, not before | A tag pushed by hand is an irreversible act typed on a laptop, which is the step this pipeline exists to remove. Dispatch lets every check run BEFORE anything exists that a human would have to clean up. The tag still marks exactly the commit that was published, so consumers see a tag-driven release; it is the tag's AUTHOR that changes. A `push: tags` trigger stays as a refusal path: a hand-pushed `v*` tag runs the pre-flight and fails with "releases are cut by dispatch". |
| Publish credential | **PyPI trusted publishing (OIDC)** on environments `testpypi` and `pypi`; `~/.pypirc` retired after the first successful pipeline publish | No long-lived token anywhere. The 2026-06 quarantine and the v0.2.x leak were both credential-adjacent; OIDC removes the class. PEP 740 attestations come free. |
| Who bumps the version | **A human, in a PR**, exactly as today; CI verifies the seven pin sites agree, CHANGELOG has the heading, and the tag does not exist | Deriving the build number from CI would work (it is a hand-typed monotonic counter) but would move the changelog heading and `whatsnew.json` prose out of the PR that explains the change. The bump stays where the reasoning is. |
| The human step | **Merging the PR** (one click by the owner) and **dispatching a release** (one click, one field) | With a single maintainer, "require one approval" is unsatisfiable on the owner's own PRs (GitHub forbids self-approval) and would push every change through the admin bypass, which is the bypass this design removes. Reviews: 0 required; enforce_admins: on. See §7. |
| Weekly result recording | **Artifacts (90 days) plus an automated PR**, never a push to `main` | A bot push cannot satisfy required checks (AUDIT §7.1). A PR from `harness-bot/results-<date>` goes through the same gate as everything else. |
| Yanking | **Never automated** | A yank is a public statement about a release that a maintainer should make with the failure in front of them; a false positive in the post-publish smoke test would otherwise yank a good release. The pipeline opens a P0 issue instead. |
| MCP registry publish | Automated via `mcp-publisher login github-oidc` in the release workflow, **dry-run only in this session** | The registry step "rots" because it is typed on one machine with one binary. OIDC login exists for Actions. It stays after the PyPI publish because the registry advertises what PyPI serves. |

## 1. Pull request gate — `pr-gate.yml`

Trigger: `pull_request` to `main` (opened, synchronize, reopened,
ready_for_review), plus `merge_group` if a merge queue is ever enabled.
Concurrency: `pr-${{ github.event.pull_request.number }}`,
`cancel-in-progress: true`. Draft PRs run stage 1 only.

Every stage is a separate job. Later stages `needs:` earlier ones, so a lint
failure costs one runner-minute, not ninety. Job names are the required-check
names and are frozen (a rename is a protection change, §7).

| Stage | Job name (= check name) | What it runs | Harness tier / criterion | Runtime budget | Blocks? |
|---|---|---|---|---|---|
| 1 | `fast: harness fast tier` | `uv run python -m harness fast --summary $GITHUB_STEP_SUMMARY` on ubuntu/3.12 | fast tier: 85 offline files, `ruff check src/`, every offline Floor, `suite.fast_seconds`, `suite.fast_skips_max` | 90 s ceiling from `suite.fast_seconds` + 60 s setup | BLOCKS |
| 1 | `fast: format` | `ruff format --check src/ harness/ scripts/` | N3 (tests half is deferred, see FINDINGS) | 30 s | BLOCKS |
| 1 | `fast: types` | `uv run python -m harness check types.error_max` — a new harness measurer running pyright over `src/` and comparing the error count to a ratchet Floor | N3 types half; ENFORCEMENT-PLAN item 10. The Floor is the count on the day it is added and may only decrease | 2 min | BLOCKS once the Floor exists; INFORMS until then (Phase 3 sets it) |
| 1 | `fast: dependency audit` | `uv run python -m harness check deps.vuln_high_max` — `pip-audit` over `uv export --no-dev`, counting HIGH/CRITICAL advisories not in `harness/audit-allowlist.json` | Criterion 8; ENFORCEMENT-PLAN item 8; Floor 0 | 1 min | BLOCKS |
| 1 | `fast: secret scan` | gitleaks (SHA-pinned) over the PR's commits, with the repo's existing redaction fixtures allow-listed by path | Criterion 8; the v0.2.x leak class | 30 s | BLOCKS |
| 2 | `full: test (<os>, <py>)` x 8 | `uv run python -m harness full --summary ...` on ubuntu + windows, Python 3.10-3.13 (the versions `pyproject.toml` declares) | full tier: all of `tests/`, `coverage.min`, `ci.skips_<os>`, `suite.full_seconds`; sdist sensitive-path scan on one leg (moved from `test.yml`) | 360 s ceiling from `suite.full_seconds` + ~4 min setup per leg; 12 min wall | BLOCKS (each leg) |
| 3 | `package: install and handshake (<os>)` x 2 | `uv build`; fresh venv; `uv pip install dist/*.whl`; `jcodemunch-mcp surface` (tool listing); `scripts/handshake.py --expect-version <pyproject> --fixture tests/fixtures/pkg_smoke/` which indexes the fixture and calls `search_symbols` + `get_symbol_source` over stdio; repeat for the sdist on ubuntu | Criterion 6(a); the #536 class; broken entry points and missing package data | 3 min per OS | BLOCKS |
| 4 | `bench: harness bench tier` | `uv run python -m harness bench --offline --summary ...` (replay, route recall, schema capture, self-latency) | every bench-tier Floor | 2 min | BLOCKS, **except** `latency.*` until F-19 is closed with three CI runs; those print as INFORMS lines in the summary and the job says so in its name suffix |
| 4 | `bench: token benchmark` | `benchmarks/harness/run_benchmark.py --verify-determinism --floor --json` on the cached pinned corpora, then `scripts/pr_bench_comment.py` posts one comment: criterion, Floor, base value (committed `jcm_reference.json`), PR value, delta | `token.grand_ratio_vs_grep`, `token.per_repo_rise_max` | 2 min (corpora cached) | BLOCKS on a Floor; the comment INFORMS |
| 5 | `done: changelog` | Diff `CHANGELOG.md` against base. If `src/` changed, the `[Unreleased]` block (or the new version's block) must have grown, unless the PR carries the `no-changelog` label with a comment saying why | Definition of Done 3 | 10 s | BLOCKS |
| 5 | `done: version pins` | `scripts/release_preflight.py --pins-only`: seven pin sites agree; if they moved relative to base, `CHANGELOG.md` has that heading and `whatsnew.json` has the entry; if `src/` changed and the PR carries the `release` label, they MUST have moved | Definition of Done 1, 2 | 10 s | BLOCKS |
| 5 | `done: tool surface documented` | `jcodemunch-mcp surface --json` on base and head (installed from each in stage 3's artifacts); a non-empty diff requires `README.md` AND `CLAUDE.md` in the PR diff and a CHANGELOG line naming the tool | Practice 1; Definition of Done 5 | 1 min | BLOCKS |
| — | `Health Radar` (existing) | unchanged | grades, does not block | 1 min | INFORMS (renamed `Health Radar (informational)`) |

Stage 2 needs every stage-1 job. Stages 3, 4 and 5 need stage 2's ubuntu/3.12
leg only (not the whole matrix), so packaging and benchmark start while
windows/3.10 is still running; the check on `main` still requires all of
them. Total wall per PR: ~15 min; runner-minutes: ~95 (matrix 8 x ~9,
packaging 2 x 3, the rest ~15).

`test.yml`, `harness.yml` (fast job) and `replay.yml` are retired into this
file: replay's index-then-gate is the bench tier's `replay_gate` step with
`self_index` (already so since 2026-09-03). The retired check names
(`test (ubuntu-latest, 3.10)` etc.) are removed from protection in the same
change that adds the new names, or `main` becomes unmergeable.

## 2. Merge to main — `main.yml`

Trigger: `push` to `main`. Concurrency: `main`, no cancellation (every merge
gets its full run; two merges queue).

| Job | Runs | Outcome |
|---|---|---|
| `main: harness full (ubuntu, 3.12)` | full tier once, as a tripwire that merged-green equals branch-green (`strict: true` in §7 is the real guarantee; this is the witness) | OPENS ISSUE `regression: <id> on main` labeled `regression`, body = verdict lines + `<base>..<head>` from the merge commit, deduplicated by title |
| `main: harness bench (online)` | `python -m harness bench --write-results` WITHOUT `--offline`, i.e. including the token benchmark on cached corpora | uploads `harness/results/latest.json` + `self_latency.json` as `harness-main-<sha>` (90 days); OPENS ISSUE on any Floor |
| `main: results PR (weekly)` | Mondays only: checks out `harness-bot/results-<date>`, commits the two result files, opens a PR labeled `harness-results`, `no-changelog` | The PR runs the gate like any other; the owner merges it. Replaces the doomed bot push |

Runtime: ~12 min per merge, ~20 runner-minutes.

## 3. Release — `release.yml`

Trigger: `workflow_dispatch` with `version` (X.Y.Z) and `dry_run` (boolean,
default true until the first real publish). Also `push: tags: v*` which runs
ONLY the pre-flight job and fails it with a message: releases are cut by
dispatch, delete the tag (this catches the old habit without breaking it
silently). Concurrency: `release`, no cancellation.

Jobs, strictly sequential:

1. **`release: pre-flight`** (ubuntu). `scripts/release_preflight.py --version X --ci` on `main`'s HEAD: on main, clean, every required check green on HEAD, seven pins == X, `CHANGELOG.md` has `## [X]`, `whatsnew.json` entry present, tag `vX` absent locally and on origin, `X` absent on PyPI and on Test PyPI, no MERGEABLE CLEAN contributor PR (policy 3b). Recomputes the tool count from `jcodemunch-mcp surface` and stores it as a job output for step 7. Fails on anything it cannot establish.
2. **`release: build`**. `uv build`; `twine check dist/*`; uploads `dist/` as the single artifact every later job installs from (never rebuilt).
3. **`release: test pypi`** (environment `testpypi`, `id-token: write`). `pypa/gh-action-pypi-publish` (SHA-pinned) with `repository-url: https://test.pypi.org/legacy/`, `attestations: true`. Skipped when `dry_run`, replaced by `twine upload --repository testpypi --skip-existing` in `--dry-run`-equivalent mode: the action's `verbose` + `print-hash`, no upload.
4. **`release: smoke from test pypi (<os>)`** x 2. Fresh venv, `pip install --index-url test.pypi.org --extra-index-url pypi.org jcodemunch-mcp==X` (deps from PyPI, package from Test PyPI), `scripts/handshake.py --expect-version X --fixture ...`. In `dry_run`, installs the wheel from the build artifact instead and says so in the summary.
5. **`release: tag`**. `git tag -a vX -m "<CHANGELOG heading>"` on the pre-flight's SHA, pushed by the workflow token with `contents: write`. Refuses if the SHA is no longer `main`'s HEAD (something merged mid-release; re-dispatch).
6. **`release: pypi`** (environment `pypi`, `id-token: write`). Trusted publishing with attestations. Skipped entirely under `dry_run`.
7. **`release: post-publish (<os>)`** x 2 (`handshake.yml`'s job, moved here). Poll PyPI up to 10 min, fresh venv, install `==X`, handshake, **and** assert the tool count from `surface` equals the pre-flight's count (both computed this run; never a literal). OPENS ISSUE `P0: post-publish check failed for vX` labeled `release`, `P0` on failure. No yank.
8. **`release: github release`**. Notes rendered by `scripts/release_notes.py` from `CHANGELOG.md`'s `## [X]` block: verbatim when it fits GitHub's 125,000-character release body, and otherwise the block's lead paragraph, every entry heading and a link to the full block at the tag (LEDGER L-92; the prose is human-written in the PR; nothing is composed here), tool count and Python range inserted from the pre-flight outputs, `dist/` and the sigstore bundles attached (fold `sign-release.yml` in: sign the artifact set from step 2 before upload). Marked as latest.
9. **`release: mcp registry`**. `mcp-publisher login github-oidc && mcp-publisher publish`, then the nested-row verification from CLAUDE.md as a script. `dry_run`: login + `--dry-run` publish only. OPENS ISSUE on failure; PyPI is already live, so this is a follow-up, not a rollback.

Runtime: ~15 min real, ~8 min dry-run. Steps 3, 6 and 9 are the only ones with
credentials, and each has only `id-token: write` plus what it uploads.

**Migration from the token.** Register the trusted publisher on PyPI and Test
PyPI (`jgravelle/jcodemunch-mcp`, workflow `release.yml`, environments
`testpypi`/`pypi`) — owner action, web UI. Until then steps 3 and 6 fail at
the OIDC exchange with a message naming that page. After the first pipeline
publish succeeds, revoke the `~/.pypirc` token on PyPI and delete the file;
the runbook has the order.

## 4. Scheduled — `nightly.yml`, Dependabot

- `nightly.yml`: `schedule` 04:00Z daily and dispatch. The PR gate's stages
  1-4 on `main` across the full matrix, plus the token benchmark WITHOUT the
  corpora cache (fresh clones, so a cache cannot hide upstream drift of a
  pinned SHA). OPENS ISSUE labeled `drift` with the first failing verdict
  line; deduplicated by title so a persistent drift is one issue, not seven.
  Runtime ~20 min, ~110 runner-minutes nightly (~13 h/week).
- Dependabot: keep the security-only, grouped configuration and ADD monthly
  version updates in two groups, `python-minor-patch` (uv, semver minor and
  patch only, `open-pull-requests-limit: 1`) and `actions` (github-actions,
  limit 1). Majors stay manual. Each PR runs the full gate; that is the
  point.

## 5. Security — `security.yml`

- CodeQL (SHA-pinned `github/codeql-action`) for `python` and `actions` on
  PRs, on push to main, and weekly. Query set `security-extended` (the
  quality queries are lint, which is ruff's gate; C-11).
  INFORMS on PRs for the first two weeks (baseline), then BLOCKS on `error`
  severity; the switch is a one-line change recorded in FINDINGS.
- Dependency vulnerabilities: stage 1's `fast: dependency audit` job is the
  gate (Floor `deps.vuln_high_max = 0` over the runtime set, allowlist file
  with an expiry date per entry). The nightly runs it too, because a new
  advisory arrives without a commit.
- `tests/test_security_md_policy.py`: `SECURITY.md` has a "Reporting a
  vulnerability" section naming the private-advisory URL and a response
  window. AUDIT §5 found none. Part of the fast tier (it is an offline file
  read), so it BLOCKS through stage 1.
- Secret scanning push protection stays on; gitleaks in stage 1 covers what
  push protection does not (non-provider patterns are disabled at the repo
  level).

## 6. Concurrency, caching, budget

**Concurrency.** PR: one group per PR number, cancel superseded runs.
`main`, `release`, `nightly`: one group each, never cancel.

**Caching.**
- uv: `astral-sh/setup-uv` cache keyed on `hashFiles('uv.lock')` and the
  runner OS/Python; a lock change invalidates it.
- tiktoken asset: `~/.cache/tiktoken` (or `TIKTOKEN_CACHE_DIR`) keyed on the
  tiktoken version from `uv.lock`; `python -m harness warm` fills it. F-14.
- Benchmark corpora: `${RUNNER_TEMP}/bench-corpus` keyed on
  `hashFiles('benchmarks/tasks.json')` **and** the three pinned SHAs
  concatenated. A pinned SHA names an immutable tree, so a hit can never be
  stale; a pin change is a new key. The nightly bypasses the cache (§4).
- Benchmark indexes are NOT cached: `INDEX_VERSION`/`PARSER_GENERATION`
  changes must re-index, and indexing the three corpora is 40 s.
- Self-index for the replay gate: never cached (fresh store per run is what
  makes it deterministic, F-15).

**Runner budget** (public repo, hosted runners are free; the budget is
wall-clock and queue pressure):

| Event | Wall | Runner-minutes |
|---|---|---|
| PR (all stages) | ~15 min | ~95 |
| PR, stage 1 failure | ~2 min | ~3 |
| Merge to main | ~12 min | ~20 |
| Release (real) | ~15 min | ~25 |
| Nightly | ~20 min | ~110 |
| Weekly total at ~10 PRs/week + 14 merges + nightly | — | ~2,100 |

## 7. Branch protection on `main` (applied in Phase 3 step 6, after go-ahead)

- Required checks, by name: `license/cla`, `fast: harness fast tier`,
  `fast: format`, `fast: types`, `fast: dependency audit`, `fast: secret
  scan`, the 8 `full: test (<os>, <py>)`, `package: install and handshake
  (ubuntu-latest)`, `package: install and handshake (windows-latest)`,
  `bench: harness bench tier`, `bench: token benchmark`, `done: changelog`,
  `done: version pins`, `done: tool surface documented`. The 12 current names
  are removed in the same call.
- `strict: true` (require up to date). Reverses the 2026-08-17 setting.
  Reason: it is the mechanical form of "a contributor PR is trial-merged
  onto main before the merge", which today costs a local trial merge per PR.
  Cost: an "Update branch" click after each release; auto-merge is enabled
  so the click is the last human act.
- `enforce_admins: true`. Reverses the 2026-08-17 setting. Landing a merge on
  a contributor's fork pushes to THEIR branch, not to `main`, so 3d's
  justification does not need the bypass. What it does remove is the owner's
  ability to push to `main` or merge red, which is principle 5.
- `required_pull_request_reviews`: **not set** (see §0). The human step is
  the merge click. If a second maintainer ever has write, set 1 review with
  `dismiss_stale_reviews: true` and CODEOWNERS on `src/`.
- `required_conversation_resolution: true`, `allow_force_pushes: false`,
  `allow_deletions: false`, `required_linear_history: false` (merge commits
  keep contributor authorship visible).
- Repo settings: `allow_auto_merge: true`, `delete_branch_on_merge: true`.
- Environments `testpypi` and `pypi`: deployment branch `main` only; no
  required reviewers (the dispatch is the approval). Optionally a 5-minute
  wait timer on `pypi` as a cancel window; recommended.

Emergency bypass: none in the mechanism. The runbook's procedure is to
temporarily set `enforce_admins: false` via the API, do the thing, restore
it, and open an issue labeled `bypass` in the same hour naming what and why.

## 8. Failure legibility

Every gating job writes the harness verdict lines for what it ran into
`$GITHUB_STEP_SUMMARY`, so the Checks tab shows:

```
latency.search_symbols_warm_p95_ms   crit 5   floor <= 23   observed 54.6   FAIL
```

and emits a workflow annotation `::error title=<threshold id>::floor <= 23,
observed 54.6 (criterion 5, STANDARD.md)` so the PR's Files tab carries it
too. This needs one harness change: `python -m harness ... --summary FILE`
writes the same lines as Markdown and `--annotate` emits the `::error`
form. That is output formatting, not judgment; thresholds, corpora and
assertions do not move (Phase 3 rule). Non-harness jobs (format, secret
scan, DoD) print one line in the same shape with the criterion named.

The PR benchmark comment is one table: criterion, Floor, base, PR, delta,
verdict; updated in place on each push (one comment per PR).

## 9. Deliberately not automated

- **Major/minor version decisions.** MINOR has been 108 for four months by
  choice; the pipeline verifies the number, it never picks it.
- **Changelog and release-note prose.** Both are written by a human in the
  PR; the release copies the block (verbatim when it fits GitHub's limit,
  its lead and headings when not) and fills in only computed figures.
- **Whether a change is user-facing.** A label (`release`, `no-changelog`)
  set by the human who knows; the gate checks consistency with the label,
  not the judgement behind it.
- **Yanking, and closing a P0 issue.**
- **The first real publish** through the pipeline, and revoking the token.
- **Merging Dependabot majors.**
- **Setting branch protection**: applied by the owner after this design is
  reviewed (constraint), then never touched by a workflow.
- **The MCP registry credential rotation**: OIDC removes it; if the registry
  ever rejects OIDC, the fallback is the documented cmd.exe line, by hand.

## 10. What Phase 3 must add outside `.github/`

- `harness/__main__.py`: `--summary FILE`, `--annotate`, and two measurers
  (`types` via pyright, `deps` via pip-audit) with Floors `types.error_max`
  (set from the first measurement, ratchet) and `deps.vuln_high_max` (0).
  Both are new Floors, not loosened ones.
- `harness/audit-allowlist.json` (empty), `tests/fixtures/pkg_smoke/` (a
  ten-file repo for the packaging smoke test).
- `scripts/handshake.py --fixture`, `scripts/release_preflight.py --ci
  --pins-only`, `scripts/pr_bench_comment.py`, `scripts/surface_diff.py`,
  `scripts/open_regression_issue.py`.
- `tests/test_security_md_policy.py`, and a SECURITY.md "Reporting a
  vulnerability" section for it to pass (content is the owner's; a draft is
  proposed in the PR).
- `tests/test_workflows_pinned.py`: every `uses:` in `.github/` is a 40-hex
  SHA; no `continue-on-error` outside a job whose name ends in
  `(informational)`; no numeric literal that matches a threshold Floor (the
  existing `test_thresholds_are_the_only_copy.py` guard extended to
  `.github/`).

## 11. Open questions for the review (answers change Phase 3)

1. `strict: true` costs an "Update branch" click per contributor PR after
   each release. Accept, or keep `strict: false` and rely on the `main.yml`
   tripwire plus the local trial merge?
2. `enforce_admins: true` removes the owner's direct push. Accept?
3. Environment `pypi` with a 5-minute wait timer: yes or no?
4. Nightly full matrix (~13 runner-hours a week) versus weekly: the brief
   asks for nightly or weekly; nightly is proposed because runner-image and
   grammar drift has bitten within a week before (2026-08-28's UTC-only
   failure).
5. Should the MCP registry job exist in the pipeline at all, given the
   binary is currently a hand-typed cmd.exe line and OIDC login is
   unverified here? Proposed: yes, dry-run, with the finding recorded if
   OIDC does not work.
