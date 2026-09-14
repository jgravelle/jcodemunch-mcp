# DESIGN — the headless jobs (2026-09-04)

Implements `docs/inbound/POLICY.md`. Each job below names its trigger,
permissions, inputs, the workflow or subagent it invokes, what it may
write, its budgets, its escalation path, and its kill-switch check. Nothing
here grants what the policy withholds; where the two disagree the policy
wins and this file is the defect.

## 0. Decisions that shape every job

**D1. Triage is decoupled from the issue event.** The official action
refuses `issues` events whose actor lacks write access (AUDIT §3.4), which
is every reporter on record, and a `workflow_run` chained from an `issues`
event inherits that actor. So the item is RECORDED on the event by a job
with no model and no secrets, and PROCESSED by a scheduled runner every 15
minutes whose actor is the maintainer who last touched the cron. Latency up
to 15 minutes against a historical median first response of 3.1 h (AUDIT
§1.3). `workflow_dispatch` is the manual form of the same runner.

**D2. One GitHub identity for writes: a custom GitHub App named
`jcodemunch-inbound`, with Contents, Issues, Pull requests (read and write),
Variables (read; added 2026-09-05, POLICY 8 amended: the only token that
can read the kill switch) and Metadata (read), installed on this
repository only.** Not the shared
Claude GitHub App (its token carries Actions and Workflows write, AUDIT
§3.4, which POLICY §4.4 forbids) and not `GITHUB_TOKEN` (commits and PRs
made with it do not trigger the PR gate, so an agent PR would never get
its required checks). The App's short-lived installation token is minted
per job by `actions/create-github-app-token` from two secrets, `INBOUND_APP_ID`
and `INBOUND_APP_PRIVATE_KEY`, and passed to the action's `github_token`
input. A repository ruleset restricts the App to pushing branches matching
`inbound/**` and `inbound-ledger`; `main` is excluded from that ruleset
(FINDINGS IN-19: a ruleset on `main` blocks every human merge too), and so
is `harness-bot/**` since 2026-09-07 (cicd FINDINGS C-17: the ruleset's
`~ALL` include also caught `main.yml`'s weekly results branch, pushed as
`github-actions`, which holds no bypass role). `main`
stays under branch protection, which stops any push there; the App never
merging a PR is by construction, not by a rule. The App's login is added to CLA Assistant's allowlist
so `license/cla` posts on its PRs (AUDIT IN-3; verified in Phase 4 item 1
before anything else is built).

**D3. Model calls and the authoritative test run are different jobs.**
`/fix-issue` runs the fast and full tiers on the model runner because its
hooks refuse a commit without them; that is the agent's own evidence. The
verdict that opens a ready PR comes from the existing PR gate on the pushed
branch, which runs in jobs with no secrets and a read-only token, plus the
reviewer subagent's `APPROVE`. POLICY §4.5 is amended to say this (see §10).

**D4. Two model pins.** `claude-opus-5` for the fix attempt and the reviewer
(the work that changes code); `claude-sonnet-5` for triage, sweep,
dependency evaluation and digest (classification and rendering). Each is
named in the prompt file's front matter and in the workflow's `--model`,
and the audit record carries both so a drift is visible.

**D5. Every prompt is a file, generated from the policy.** Prompt files live
in `.github/inbound/prompts/`, carry `version:` and `model:` front matter,
and begin with POLICY §4.2's preamble and end with POLICY §4.4's never-touch
list, both rendered by `.github/inbound/render_prompts.py` from the policy
text. `tests/test_inbound_prompts.py` fails if a rendered block differs
from the policy by one byte, if a prompt lacks a version, or if a workflow
names a prompt file that does not exist. Editing a prompt is a PR reviewed
like code; the never-touch list includes the directory (§10).

**D6. Helper code lives in `.github/inbound/`, not in `src/` or
`scripts/`.** It is workflow plumbing (kill-switch read, budget count,
pattern scan, ledger append, self-check, digest render), unit-tested under
`tests/test_inbound_*.py`, and excluded from the sdist by the existing
`.github` exclusion. Nothing in layer 4's `.claude/`, the harness, or the
product changes; what they would need to expose goes to
`docs/inbound/FINDINGS.md`.

**D7. The ledger is an orphan branch.** `inbound-ledger` holds
`ledger/<YYYY-MM>.jsonl` and `drafts/*.md`. Only the App and humans push
to it. The sweep is the single writer of the ledger files; other jobs
upload artifacts and the sweep rolls them in, so a triage job needs no
`contents: write`.

**D8. Nothing is a required check until a human makes it one.** The
self-check (§5) fails its own status on an `agent-authored` PR; making that
status required on `main` is a branch-protection change and therefore a
human step in `docs/cicd/RUNBOOK.md` §8, not something this layer does.

## 1. Intake (record on event)

| | |
|---|---|
| workflow | `.github/workflows/inbound-intake.yml` |
| trigger | `issues: [opened, edited, reopened]`, `issue_comment: [created]` on issues only |
| permissions | `issues: write` (labels only), `contents: read`, `actions: read` (the kill-switch variable is read through the Actions API) |
| secrets | none |
| model | none |
| reads | the event payload; `INBOUND_ENABLED` via API; the labels already on the item (`intake_plan.py`: a held or already-classified item is never re-queued; an edit or comment by anyone but the author re-queues nothing) |
| invokes | `.github/inbound/scan.py`: the POLICY §4.3 plain-text pattern scan and the POLICY §1 rule-1 security keyword scan, both over title, body and the new comment, with HTML comments, `<details>`, zero-width characters and code fences INCLUDED in the scanned text |
| writes | on a security keyword hit: label `inbound:security` + `needs-human`, and NOTHING else in this job; on an injection-pattern hit: `inbound:unknown` + `needs-human` + `inbound:injection-suspected`; otherwise label `inbound:queued`. Uploads the audit artifact. |
| budgets | 10 min; no model; unlimited runs (it is one API call) |
| escalation | the labels above; the sweep's digest names every `needs-human` |
| kill switch | step 1 reads the variable; if not `true`, uploads a `skipped` record and exits 0 with no label |

The maintainer's own issues are labelled like any other and then ignored
by §2 (POLICY §10). An edit to an issue already labelled by §2 re-queues it
only if the edit is by the author and the item is not `needs-human`.

## 2. Triage runner (process the queue)

| | |
|---|---|
| workflow | `.github/workflows/inbound-triage.yml` |
| trigger | `schedule: */15 * * * *`; `workflow_dispatch` with an optional issue number |
| permissions | Two jobs per item. `classify` (the model): `contents: read`, `issues: read`, `id-token: write` (the action requires it), `github_token` = the read-only `GITHUB_TOKEN`; it holds no write scope at all, so the allow-list is not the only thing between the model and a post. `apply` (no model): `issues: write`, `contents: read`, `actions: read`, the App token from D2, and only `apply_triage.py`. The kill-switch reads use a `switchtok` App token minted first in every no-model job (POLICY 8 as amended 2026-09-05; `GITHUB_TOKEN` cannot read a variable); `classify` has no read of its own and starts from `queue`'s. |
| secrets | `ANTHROPIC_API_KEY` in `classify`; App id and key in `apply` only |
| model | `claude-sonnet-5`, `--max-turns 12` |
| reads | up to 5 issues labelled `inbound:queued` (oldest first); for each, the issue via `gh issue view --comments` (the only Bash tools allowed: `Bash(gh issue view:*)` and `Bash(gh search issues:*)` for duplicate candidates; `gh api *` is disallowed by name because its `-f` form is a POST); the tree at `main` (Read, Grep, Glob); `docs/inbound/POLICY.md` §1 and §2 |
| invokes | prompt `prompts/triage.md`, which is `/triage-issue <n>` bounded by the policy: classify and write the JSON `{issue, category, confidence, evidence[], duplicate_of?, draft?, escalate_reason?}` to a path-restricted result file (the one `Write` rule); `apply_triage.py` validates it in the `apply` job and a malformed or missing file escalates (FINDINGS IN-13) |
| writes | per item: remove `inbound:queued`; apply `inbound:<category>` (+ the matching human label per POLICY §1); if `duplicate` with `high`: post the link comment (the one day-one unattended comment); if a draftable category: write `drafts/<n>-<run>.md` to the artifact for the sweep; if `medium`/`low`/`unknown`: `needs-human`. Never comments otherwise. |
| budgets | 10 min; 12 turns; 5 USD per run; 2 concurrent; 20 runs a day (pre-flight counts today's runs of this workflow name via `gh run list`) |
| escalation | `needs-human` + audit record with `evidence[]` quoted; security is never reached here because intake labelled it first, but the prompt still carries rule 1 and applies it if intake's scan missed a phrasing |
| kill switch | read at step 1 and again in the step immediately before labelling; the `queue` gate also reads `INBOUND_MODEL_ENABLED` and `go` needs both (2026-09-14) |

Reproducibility (POLICY rule 7) is NOT decided here: triage classifies a
bug as `inbound:bug-candidate`, and only the fix job's reproduction step
turns it into `bug-reproducible` or `bug-unreproducible`. Until
`INBOUND_AUTOFIX` is `true`, a `bug-candidate` waits for a maintainer's
`agent-fix` label.

## 3. Fix attempt

| | |
|---|---|
| workflow | `.github/workflows/inbound-fix.yml` |
| trigger | `issues: [labeled]` where the label is `agent-fix` (actor is a maintainer with write, so the action's check passes); later, when `INBOUND_AUTOFIX` is `true`, the triage runner may also apply `agent-fix` and the same trigger fires with the App as actor (`allowed_bots` names the App login only) |
| permissions | `GITHUB_TOKEN` read-only in every job (`id-token: write` in the model job for the action's handshake); the branch push, the draft PR and every label are the App token's, in the two jobs with no model (amended 2026-09-04, as built) |
| secrets | `ANTHROPIC_API_KEY`; App id and key. No PyPI, no registry, no environment. |
| model | `claude-opus-5`, `--max-turns 60` |
| pre-flight (no model) | kill switch; `INBOUND_AUTOFIX` unless the labeler is a human; budget counts (3 fix runs today, 1 concurrent via `concurrency: inbound-fix`, at most 3 open `agent-authored` PRs); the issue carries none of `agent:reverted`, `agent:in-progress`, `inbound:security`; the author's account is at least 90 days old and has one prior comment, issue or PR here, OR the `agent-fix` labeler is a human; the issue was not the subject of a merged `Revert "..."` PR since the last human `agent-fix` (timeline API, D-rollback §9). Any failure: `skipped` record naming the reason, label untouched, exit 0. |
| reads | a fresh checkout of `main` at the workspace root (never the issue's content as code); the issue via `gh issue view`; the hooks and commands of layer 4 from that checkout (non-`--bare`: POLICY D5 depends on `.claude/` loading) |
| invokes | prompt `prompts/fix.md` = the preamble + `/fix-issue <n>` + the never-touch list. The command's own steps run: ISSUE.md, branch `inbound/fix-<n>-<slug>`, the reproduction (POLICY §3: the failing test is committed alone, first), archaeology, the fix, CHANGELOG, fast tier via the commit hook, `run_full.py`, checklist, reviewer subagent, PR body to the scratchpad |
| writes | label `agent:in-progress` at start and its removal at end; branch `inbound/fix-<n>-*` (pushed by the publish job from the model's bundle, after `fix_publish.py` passes); a PR (`--body-file`, template §7, `Closes #<n>`, label `agent-authored`): opened as DRAFT always. Uploads the hand-over (bundle, body, `evidence/*`, the review verdict, the head SHA) as one artifact. |
| budgets | 60 min; 60 turns; 25 USD (a run over the ceiling is `failed`); the reproduction step alone is bounded by `/fix-issue`'s own `--continue-on-collection-errors` run and a 15-minute step timeout |
| escalation | `REFUSED: not reproduced` from the command: label `inbound:bug-unreproducible`, draft the request for information, `needs-human`, no branch pushed. `BLOCK` from the reviewer: delete the local branch, push nothing, `needs-human`, record the reasons. Any other failure, and any refusal by the publish gate: `needs-human`, nothing pushed (the hand-over artifact holds the reproduction for 90 days; a partial branch is not pushed, as built). |
| kill switch | pre-flight, before the push, and before `gh pr create`; the pre-flight also reads `INBOUND_MODEL_ENABLED` and `go` needs both (2026-09-14) |

**Promotion from draft to ready is a separate, model-free job**:
`.github/workflows/inbound-fix-promote.yml` on `workflow_run` of `PR gate`
completed, filtered to head branches `inbound/fix-*` opened by the App.
Its actor check is the upstream run's actor, the App, named in
`allowed_bots`; it has `pull-requests: write` only, reads the gate
conclusion, the self-check status (§5) and the review verdict artifact from
the fix run, and calls `gh pr ready` only when all three are green
(gate success, self-check success, verdict `APPROVE`). Otherwise it labels
`agent:incomplete` and leaves the draft. It never edits the PR body and
never re-runs anything.

**As built (2026-09-04, item 6).** Three jobs, so the model never holds
a token that can write. `preflight` (no model): the kill switch, the
budget, `fix_preflight.py` (the labeler, `INBOUND_AUTOFIX`, the blocking
labels, the author's account age and prior activity, a merged revert
newer than the last human `agent-fix`), then the App token for exactly
one write, `agent:in-progress`. `fix` (the model; `GITHUB_TOKEN`
read-only; `id-token: write` for the action's own handshake; the push URL
of `origin` is set to `no_push`): runs `/fix-issue` in a non-bare
checkout of `main`, commits on a local `inbound/fix-<n>-<slug>` branch,
writes the PR body to `$RUNNER_TEMP/pr-body.md`, and stops after the
command's step 8; the hand-over (a `git bundle` of `origin/main..HEAD`,
the body, the evidence directory, the model outcome) is an artifact.
`publish` (no model, no API key): `fix_publish.py` verifies the bundle
sits on top of `origin/main`, the branch name, every commit's paths
against the never-touch list and the version pin, the test-before-src
order, the template headings and `Closes #<n>`; on exit 0 only, the App
pushes the branch and opens the DRAFT with `--label agent-authored`; on
anything else the issue gets `needs-human`. `agent:in-progress` is
removed by the publish job's `always()` step on every path but one: if
the kill switch flips off between the pre-flight and the publish job's
re-read, no App token is minted, so the label stays until a human looks
(the run is red and the record says `skipped`); that is the switch doing
its job. The prompt is version 2 (no push, no PR from the model).
`inbound-fix-promote.yml` writes with the App token too, so every write
in the layer is the App's. After review round 1: the gate refuses any
commit with other than one parent and checks the base-to-head range as
well as the per-commit lists (a merge or root commit lists no files);
the promote job matches the App login exactly and accepts a verdict only
when the hand-over's head SHA is the PR head being promoted.

## 4. Dependency PR evaluation

| | |
|---|---|
| workflow | `.github/workflows/inbound-depeval.yml` |
| trigger | `workflow_run` of `PR gate` completed, when the upstream head branch starts with `dependabot/` and the upstream event is `pull_request` |
| permissions | `pull-requests: write`, `contents: read`, `id-token: write` |
| secrets | `ANTHROPIC_API_KEY`; App id and key. `allowed_bots: dependabot[bot]` on the action, because the upstream actor is Dependabot (AUDIT §3.4: allowed bots are not permission-checked, which is acceptable here because the job checks out `main`, not the PR head, and can only label and comment) |
| model | `claude-sonnet-5`, `--max-turns 30` |
| reads | `main` at the workspace root; the PR diff via `gh pr diff` (as text, never checked out); the gate's artifacts (`fast.md`, `full.md`, `bench.md`, the Floor table); `uv.lock` before and after via `gh api` |
| classification (no model) | `.github/inbound/depkind.py`: `grammar-or-parser` if any bumped package name starts `tree-sitter`; `major` if any bump crosses a major; else `patch-or-minor`; `unknown` if the diff touches any file outside `uv.lock`, the dependency tables of `pyproject.toml`, or `uses:` lines |
| invokes | for `patch-or-minor`: the reviewer subagent with the diff, the summaries and the Floor table (prompt `prompts/depeval.md`); for `major` and `grammar-or-parser`: the reviewer plus a drafted assessment; for `grammar-or-parser` additionally dispatches `inbound-bench-full.yml` (below) and waits for nothing: it labels `agent:bench-pending` and the bench job replaces that label |
| writes | the stage-4-format delta comment (one sticky comment, edited on re-run, like `pr-gate.yml`'s); labels: `agent:ready-to-merge` (patch-or-minor, all Floors hold, gate green, `APPROVE`), `agent:evaluation-failed`, or `agent:needs-human-review` (major, grammar, unknown, any non-APPROVE) |
| budgets | 45 min; 30 turns; 10 USD; 4 runs a day; 2 concurrent |
| escalation | `agent:needs-human-review`; the digest lists every dependency PR by label |
| kill switch | step 1 and before the comment; the `gate` job also reads `INBOUND_MODEL_ENABLED` and `go` needs both (2026-09-14) |

`inbound-bench-full.yml`: `workflow_dispatch` with the PR number, run by
the depeval job through the App token; `contents: read`,
`pull-requests: write`; no model; checks out `main`, runs
`/benchmark-compare`'s two fresh runs (`harness bench`, every pinned
corpus, NOT `--offline`) against the PR's merge ref in a worktree; 90
minutes; posts the per-row table under the sticky comment and swaps
`agent:bench-pending` for `agent:needs-human-review`. Its runtime ceiling
is the one budget the policy exempts, because a grammar update has never
been measured (AUDIT §2.3) and a truncated measurement is worse than none.

**As built (2026-09-04, item 4; amended after review round 1).** The
evaluation is three jobs since 2026-09-05 (a `gate` job with no model reads the switch and the budget first, POLICY 8 as amended); the shape item 2's review settled on: `classify`
(model; `GITHUB_TOKEN` read-only, `contents: read`, `pull-requests: read`,
`actions: read`, `id-token: write`; no App token; the result JSON is its
only write, kept as an artifact) and `apply` (no model, no API key; the
App token; `apply_depeval.py` reads the result, the no-model kind from
`depkind.py`, the gate conclusion and the Floor verdicts, and applies
exactly one outcome label and the one sticky comment). The workflow-level
permission block is therefore `pull-requests: read`, not `write`: the App
writes.

**The Floor table comes from the gate's job log.** The PR gate writes its
summaries to `$GITHUB_STEP_SUMMARY` and uploads only the dist and the
bench results (FINDINGS IN-14), so both jobs fetch `gh run view --log` for
the gate run and `apply_depeval.floors_hold` parses the harness's verdict
lines from it; `floors_hold` in the model's JSON is informational and
never read for the label.

**`depkind.py` inspects the diff, not the file names.** A `pyproject.toml`
change is admitted only when every added line sits in a dependency table
of the head text and every removed line sat in one of the base text (the
`[project]` `dependencies` array, `[project.optional-dependencies*]`,
`[dependency-groups]`, `[build-system]`); a workflow change only when
every changed line is a 40-hex `uses:` pin. A removed package is `major`
(POLICY rule 2 does not name removals; a human reads them).

**The assessment is a draft.** For `major` and `grammar-or-parser` the
model's paragraph is written in the triage draft format to a `draft-*`
artifact for the sweep; the sticky comment carries our numbers, the
reviewer's reasons and a line saying a draft awaits approval. Nothing the
model wrote is posted.

**The full-corpus bench is started by a label, and is three jobs** (a `gate` job with no model and no PR code reads the switch and the budget first, since 2026-09-05). `apply`
labels the PR `agent:bench-pending`; `inbound-bench-full.yml` triggers on
that `pull_request: labeled` event (same-repo, `dependabot/` head, the
label name, all in the job `if:`). Its `bench` job is read-only and holds
no App token, because it executes the merge ref's build hooks and harness
in a worktree under the runner temp; its `apply` job, which runs nothing
from the PR, mints the App token for the appended table and the label
swap. `gh api` rejects `-R`, so `apply_depeval._gh` omits it for API
calls. `tests/test_inbound_workflows.py` asserts each of these guards
(VERIFICATION 4.7), not only the checkout ones.

## 5. Agent PR self-check

| | |
|---|---|
| workflow | `.github/workflows/inbound-selfcheck.yml` |
| trigger | `pull_request: [opened, synchronize, labeled, ready_for_review, reopened]` on same-repo PRs carrying `agent-authored` |
| permissions | `contents: read`, `pull-requests: read`, `issues: read`; no `checks: write`, the job's own conclusion is the check run |
| secrets | none |
| model | none |
| invokes | `.github/inbound/selfcheck.py` on the PR's commit list and file list via the API and `refs/inbound/pr-head` (no checkout of the head at root; the test commit is cherry-picked into a worktree under the runner temp) |
| checks | (a) no touched path matches the never-touch list rendered from POLICY §4.4; (b) the first commit that touches `tests/` precedes every commit that touches `src/`, that first commit's tests fail on `main` (it cherry-picks the test commit onto a `main` worktree and runs the touched test files, expecting exit 1 and nothing else), that test commit touches nothing outside `tests/` (a root `pyproject.toml` or `conftest.py` in it would reconfigure the run that proves the red), and every path it touched, fixtures included, is byte-identical at the PR head (a rewritten reproduction is named); (c) the PR body carries every §7 template heading in order; (d) `Closes #<n>` names an issue that carries `agent-fix` or `inbound:bug-candidate`; (e) the head branch matches `inbound/fix-*`; (f) the PR author is the App |
| writes | one check run, pass or fail, with each failed clause named |
| budgets | 15 min |
| escalation | a failed check; the promote job (§3) reads it and keeps the PR a draft |
| kill switch | none: this job only ever fails a PR, so it runs even when the switch is off |

**As built (2026-09-04, item 5).** No `checks: write`: the job's own
conclusion is the check run named `selfcheck`, which the promote job
reads. The PR's commits are fetched into `refs/inbound/pr-head` and read
with `git diff-tree`; the test commit is cherry-picked into a worktree of
`main` under `$RUNNER_TEMP` and its `tests/*.py` run from the MAIN
checkout's environment (`uv run --no-sync`), because the package under
test is `main` either way and a second `uv sync` per worktree is a second
environment to keep honest. Only pytest exit 1 counts as red: 2, 4 and 5
(interrupted, usage, nothing collected) are not a reproduction. The PR
author check accepts the App's login with or without `[bot]` and the
`app/` prefix `gh` renders. Clause (b) also proves every path the test commit touched, fixtures
included, is the one the PR head carries (`test_files_rewritten_after`),
because `assert False` committed first and the real test written in the
fix commit would otherwise satisfy it (item-5 review, finding 1), and a
test that reads a fixture is rewritten by rewriting the fixture (round 2,
finding 2). The pytest command is `_pytest_cmd()`, replaced by the test
suite with its own interpreter so the red run is exercised end to end. The
read-only `GITHUB_TOKEN` is scoped to the one step that reads the PR, not
the job, so the step that executes the PR's test files holds no token.

## 6. Scheduled sweep and weekly digest

**Sweep**, `.github/workflows/inbound-sweep.yml`, daily 06:30 UTC and
`workflow_dispatch`; `GITHUB_TOKEN` is `contents: read`, `actions: read`
only; every write (the ledger push, the approved-draft comment) uses the App
token, which the ruleset confines to `inbound-ledger`; no model. (Amended
2026-09-04, item-3 review: the first draft gave `GITHUB_TOKEN` write scope
the job never used.) The artifact collection admits only artifacts whose
producing run is this repository's, on `main`, from a schedule, dispatch,
issue, or `workflow_run` event: a fork PR's own workflow file can upload an
artifact under any name, and POLICY 4.1 applies to the ledger as much as to
a comment.

1. Kill switch.
2. Roll every `inbound-audit-*` artifact since the last ledger line into
   `ledger/<YYYY-MM>.jsonl`; roll every `draft-*` artifact into `drafts/`.
3. Post approved drafts: a draft file whose front matter reads
   `approved: true` and whose approver commit is by a human (not the App)
   is posted verbatim as a comment on its item, the file moved to
   `drafts/posted/`, and the category's streak in the ledger incremented
   if `edited: false` (the sweep compares the posted body to the
   agent-written body stored in the file's `original:` block; any
   difference sets `edited: true` and resets the streak).
4. Re-queue nothing. For every `needs-human` older than 7 days, add the
   item to the digest's re-notify list.
5. Any `inbound:queued` older than 2 hours (the triage runner failed or
   the budget declined it) is named in the digest.

**Digest**, `.github/workflows/inbound-digest.yml`, Mondays 06:45 UTC after
the sweep; `GITHUB_TOKEN` read-only, the issue written by the App (as built; the first draft said `issues: write`); `claude-sonnet-5`,
`--max-turns 8`, only to render prose from the ledger rows the job hands
it; the numbers in the digest are computed by `.github/inbound/digest.py`
and pasted, never asked of the model. It opens or updates ONE issue titled
`inbound digest <ISO week>` labelled `inbound:digest` with: items handled
by category and outcome; items escalated and why; drafts awaiting approval
with links to the ledger files; budgets consumed per day and every declined
run; job failures with run links; kill-switch flips (actor, time); the
streak table from POLICY §9. This is the maintainer's dashboard and the
only place the agent summarises its own work.

**As built (2026-09-04, item 7).** Three jobs, the sweep's shape: `numbers`
(no model; `digest.py` over a read-only checkout of `inbound-ledger`; the
last sweep's `sweep-summary.json` fetched from that run's own audit
artifact for step 4's needs-human list, rendered "not recorded" when none
is readable), `prose` (the model, `GITHUB_TOKEN` read-only, `Read` and
`Write` of one file under `$RUNNER_TEMP`; one paragraph at most), and
`apply` (no model; the App token opens or edits the one issue). No
`issues: write` on `GITHUB_TOKEN`: every write is the App's, as for the
sweep. The paragraph is admitted only when every digit-run in it appears
in the JSON the model was given, and when it is under 1,200 characters;
otherwise the issue carries the computed sections alone and the audit
record says why. A model failure still posts the digest without the
paragraph. A run with no cost figure is counted, never priced at 0; the
step-5 `inbound:queued` list is the sweep's (`stale_needs_human` covers
step 4; step 5 is FINDINGS IN-15).

## 7. The agent-authored PR description

Rendered by `/fix-issue` step 8 through the `pr-description` skill with
these headings, in this order, all present even when a section says
"none":

```
## What was asked
<issue number, title, reporter; the one sentence from the report that states the defect>
## What was found
<the mechanism, one paragraph; the layer the fix lives in and why (mechanism-not-instance)>
## The failing test
<path::name; the assertion; commit sha of the test-only commit; the red run's last line>
## The fix
<files touched; what is now impossible>
## Evidence
<fast tier line; full tier line with the skip count; bench table if run; surface diff line; checklist "12/12" line>
## Review verdict
<APPROVE | REQUEST CHANGES | BLOCK, verbatim from evidence/review.md, with its reasons>
## What to look at first
<the one file and the one decision the reviewer should question>
## What the agent was unsure about
<every place the agent chose between readings; "none" is a claim the self-check will read back to you>
## Audit
<job, prompt version, model, run link, cost>
```

## 8. Prompt versioning and the audit record

Prompt front matter: `version: <int>`, `model: <id>`, `job: <name>`,
`policy_sha256: <sha of POLICY.md at render>`. The workflow computes the
prompt file's sha256 at run time and writes it, with the version, the
model actually passed in `claude_args`, the action's commit SHA, the Claude
Code version from the `system/init` event, and `total_cost_usd` from the
result, into the audit record (POLICY §6.1). A prompt edit without a
version bump fails `tests/test_inbound_prompts.py` (it stores the last
rendered sha per version in `.github/inbound/prompts/VERSIONS.json`).

## 9. Fork safety, isolation, and rollback

**Forks.** Every job with a write permission triggers on `issues`,
`schedule`, `workflow_dispatch`, or `workflow_run` with the upstream actor
restricted to the App or Dependabot, or on `pull_request` from a same-repo
branch (`github.event.pull_request.head.repo.full_name ==
github.repository` in the job's `if:`). No job uses `pull_request_target`.
No job checks out a PR head at the workspace root; diffs are read as text
through the API or checked out into a `--add-dir` subdirectory with no
execution. `tests/test_inbound_workflows.py` asserts each of those
properties over every `inbound-*.yml`, alongside the existing SHA-pin and
`continue-on-error` rules of `tests/test_workflows_pinned.py`.

**Dependabot versus humans.** `github.actor == 'dependabot[bot]'` on the
upstream run AND the head branch prefix `dependabot/` AND the
`dependencies` label; all three, or the PR is a human PR and no inbound job
touches it.

**Isolation of the fix job.** GitHub-hosted `ubuntu-latest`, fresh
checkout of `main`, `uv sync --locked --group dev --extra watch` from the
pinned lock (the harness's no-network fixture governs tests). Environment
carries exactly `ANTHROPIC_API_KEY` and the App installation token, both
required to do the job; no environment is granted, so the publish secrets
are unreachable. Hosted runners do not restrict egress: the model call
needs the Anthropic API, and the design does not pretend a network policy
exists that does not. The compensating controls are the tool allow-list
(`--allowedTools` limited to Read, Edit, Write, Grep, Glob, `Bash(uv run *)`,
`Bash(git *)` minus the deny list, `Bash(gh issue view:*)`, `Bash(gh pr
create:*)`), `--permission-mode dontAsk`, `--permission-prompts none`, and
the layer-4 deny guard, which runs because the checkout is non-`--bare`.
`WebFetch` and `WebSearch` are denied by name in every headless prompt's
settings.

**Rollback.** A maintainer reverts an agent-authored merge with `git
revert` through a normal PR whose title keeps the `Revert "...(#<pr>)"`
form, and applies `agent:reverted` to the issue. The fix job's pre-flight
refuses an issue that carries `agent:reverted`, and independently refuses
any issue that a merged revert PR names in its body unless an `agent-fix`
label event on the issue is NEWER than that merge (timeline API). Removing
`agent:reverted` and re-applying `agent-fix` is the only way back, and both
are human label events the audit record names.

**Human and agent on the same issue.** `agent:in-progress` is applied at
the start of a fix run and removed at its end (also on failure, by an
`always()` step). CLAUDE.md gains the rule that an interactive session
checks for that label before `/fix-issue` (Phase 6).

## 10. Amendments to POLICY.md made by this design

(2026-09-14, owner) §8 gains a second switch, `INBOUND_MODEL_ENABLED`, read by the gate in front of every model job: `inbound-triage`'s `queue`, `inbound-fix`'s `preflight`, `inbound-depeval`'s `gate`, and `inbound-digest`'s `numbers`, which emits `model_go` for the `prose` job and keeps `go` for `apply`, so the digest posts its numbers without a paragraph.

Both applied in the same commit as this file, each with a dated note in
the policy:

1. §4.4 never-touch list gains `.github/inbound/**` (the prompts and helper
   code) and `.github/ISSUE_TEMPLATE/**`.
2. §4.5 reads: "The model runner executes the agent's own tests as
   `/fix-issue` requires; the AUTHORITATIVE run for a verdict is the PR
   gate on the pushed branch, whose jobs hold no secrets and a read-only
   token." The previous sentence promised a separation the workflow layer's
   commit hook makes impossible without weakening it.

## 11. Build order and what each PR must show

Per the brief, one PR each, in this order; every PR carries
`tests/test_inbound_*` for what it adds and a `VERIFICATION.md` row.

1. Plumbing: the App (D2), the two secrets and `ANTHROPIC_API_KEY`, the
   `INBOUND_ENABLED` variable (absent = off), the ledger branch and its
   ruleset, the labels, `.github/inbound/{killswitch,budget,ledger}.py`,
   `render_prompts.py` and the prompt test, `scan.py` and its tests, the
   CLA allowlist entry (IN-3), the private-reporting check (IN-4), the
   `qa` label (IN-1). Nothing acts yet.
2. Intake + triage runner, draft-only: labels applied, every draftable
   comment held; the duplicate link is the only comment.
3. Sweep with the approved-draft path; digest skeleton.
4. Dependency evaluation and the full-corpus bench dispatch.
5. Self-check.
6. Fix attempt with promotion, `agent-fix` label only; `INBOUND_AUTOFIX`
   absent.
7. Weekly digest complete.

## 12. Substitutions from the brief, recorded

- "Triage on issue open" is intake-on-event plus a 15-minute scheduled
  runner (D1), because the official action cannot act on an issue event
  from a non-write actor.
- "Runs with read and label permissions only" holds for intake and the
  runner; the runner additionally needs `id-token: write`, which the action
  requires for its own auth handshake and which grants nothing in the
  repository.
- "No network beyond package installation" is not enforceable on hosted
  runners; §9 states the compensating controls instead of claiming a
  sandbox that does not exist.
- The Claude GitHub App is not installed; a custom App with three
  permissions replaces it (D2), per the action's own documentation for
  narrow permission sets.
