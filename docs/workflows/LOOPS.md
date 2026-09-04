# LOOPS — the recurring kinds of work in this repo, and where each has gone wrong

Read-only inventory, 2026-09-04, `main` at `b106657` (v1.108.317). Window:
2026-03-04 to 2026-09-04 (1,751 commits, 645 tags, 308 issues, 257 PRs).
Sources: `git log` bucketed by conventional-commit prefix and touched paths,
`gh issue list`/`gh pr list` with the last four comments of every closed
issue, `docs/cicd/RUNBOOK.md`, `docs/standard/STANDARD.md`, CLAUDE.md, and
the contents of `.claude/`. Nothing was modified. Every count below is a
count over this window and expires with it; recompute, never quote.

Kind classification of issues is heuristic (title + labels; only 33 of 308
issues carry a label), so per-kind issue counts are ±10%. Commit hashes and
issue/PR numbers are exact.

## 0. The ranking

Score = frequency (events in the window) × historical error rate (share of
events followed by a corrective commit, a reopen, or a second issue on the
same defect). Two error columns, because they answer different questions:
"hard" is a correction that had to ship; "DoD-incomplete" is an artifact the
Definition of Done (STANDARD.md) requires that was missing at the time.

| Rank | Loop | Events | Hard error rate | DoD-incomplete rate | Score (hard) | Workflow |
|---|---|---|---|---|---|---|
| 1 | Cut a release | 272 releases | ~13% (35 repair commits; ~20% in the Aug peak) | 43% of closes name no version | 35 | `/release` |
| 2 | Merge a contributor PR | 80 external merges | 50% (40 followed by our own fix within 3 commits) | 88% no changelog line | 40 | `/review` (+ the merge half, §2.4) |
| 3 | Add a feature / tool | 207 `feat:` commits | ~5% (11 corrections) | **94%** (Practice 1's four files in one commit: 12 of 207) | 10 / 195 | `/feature` |
| 4 | Benchmark re-run and mirror resync | 39 runs | 38% (15 corrections; one reference stale 4 months, one 22 days) | — | 15 | `/benchmark-compare` |
| 5 | Fix a reported bug | 221 `fix:` (62 cite an issue) | 6% (12 corrections + 6 reporter-came-back chains) | 35% no changelog in the same commit; 65% no CLAUDE.md | 13 | `/fix-issue` |
| 6 | CI / workflow change | 41 | 29% (12 self-corrections, 7 of them in the Sep series) | — | 12 | none (covered by the gate itself; see §2.7) |
| 7 | Triage an issue | 308 issues | 89% unlabeled; 3 multi-finding reports split by hand | — | 274 (cheap errors) | `/triage-issue` |
| 8 | Review a PR | 257 PRs | 64% merged with zero reviews (164) | — | 164 | `/review` |
| 9 | CLAUDE.md rotation / split | 5 | 60% (3 wrong turns) | — | 3 | a skill, not a command (§2.9) |
| 10 | Retire or skip a test | 3 deletions, 60 skip-mark commits | one incident hid 209 tests | — | — | a hook (§2.10) |

Build order for Phase 3, within the fixed sequence (hooks, review subagent,
commands, skills, CLAUDE.md): `/release`, `/review`, `/feature`,
`/benchmark-compare`, `/fix-issue`, `/triage-issue`. `/fix-issue` ranks
below `/feature` because its same-commit test discipline is already 95%
(59 of 62 issue-citing fixes changed a test); its errors are the
instance-not-property class, which the review subagent catches, not the
command.

## 1. Loops nobody listed, and one that is not a loop

- **Merge a contributor PR** is the highest hard-error loop and the brief's
  required set has no command for it. Half of external merges needed our
  fix within three commits: a merge that silently deleted an adjacent fix
  (`ece87b0` 03-06, `d066d9e`/`f3c925c` 07-10 the LICENSE clause), a
  contributor flipping a default (`f2c1d55`→`7fd8768` #293), review advice
  that dropped a predicate (`1027268`→`51bea3c` #439), and a fix that
  covered one tool where the mechanism was one layer down (`4f8fa65`
  #570→`7e9f490` #572). Phase 2 should give `/review` a merge mode, or add
  `/merge-pr`; either way the missing gate is "what did this PR DELETE"
  plus the trial-merge-and-run that CLAUDE.md already mandates.
- **Per-release CLAUDE.md brief update** was its own commit 42 times
  (`docs: record vX in project brief`) until ~.194, then folded into the
  release commit. It is a release step, not a loop.
- **Dependency bumps** (37, 21 of them dependabot) need no workflow; the
  `deps.vuln_max` Floor and the gate cover them.
- **Parser / language work** (76 commits, Racket and Rust series in Aug)
  is `/feature` with a fidelity oracle attached; not a separate loop.

## 2. The loops

Each: one sentence, frequency, the steps actually performed, where it went
wrong (cited), and which STANDARD.md criteria and DoD items it touches.

### 2.1 Cut a release

**What:** bump seven pin sites, cut the CHANGELOG block, rotate CLAUDE.md
Current State, merge the release PR, dispatch `release.yml`, verify PyPI,
GitHub release and MCP registry, restart the local server.
**Frequency:** 272 in six months; peak 3.4 per day (Jul-Aug).

**Steps performed** (15 release commits sampled; `9e2c638` .259, `de1d063`
.311, `63a621d` .316, `f4be79a` .317):

1. Fix/feat commits merge, since Aug through our own PRs.
2. One `release: vX — <thesis>` commit touching `pyproject.toml`,
   `server.json` (x2), `.claude-plugin/plugin.json`, `uv.lock`,
   `whatsnew.json`, `CHANGELOG.md`, `CLAUDE.md`, often `README.md` and
   `ISSUE-HISTORY.md`. Before 08-05 this was two or three commits
   (`9244b36` bump → `986f419` promote CHANGELOG → `5e61a17` record in
   brief), and **0 of 194 pre-Aug releases touched `server.json`**.
3. Tag (645 tags). Since .317 the tag is `release.yml`'s.
4. PyPI upload, GitHub release, registry publish (invisible in git; the
   registry drifted until `6c22f5f` 08-05 added a guard).
5. Since 2026-09-04: RUNBOOK §1 (release PR → merge → dispatch → read the
   run), with §1a's hand-finish while PyPI persists no publisher (C-15).

**Where it went wrong:**

| Cite | What |
|---|---|
| `978e614`…`7f4e3f9` (.242-.246, 08-05) | five releases missing the `server.json` pin; `6c22f5f` added the drift guard the same day |
| .247-.264 (23 releases, 08-06/07) | `whatsnew.json` not regenerated; the file had existed since `4f35cac` 05-09 |
| `5cd969b` 04-03, `fead9ad` 07-20, `836fb28` 07-22 | `uv.lock` stale or rewritten in the wrong revision format by a newer local uv |
| `431a68e` 06-19 | whatsnew block forgotten, refreshed after |
| `8265e34` 03-27, `f1ba33b` 08-13 | CHANGELOG section lost in a rebase resolution |
| .259-.262 (`9e2c638`…), `477d947` 08-03 | four consecutive releases on a RED lint check; `e1b5f49`/`9661e0b` 08-23: ten modules skipping wholesale hid 209 tests |
| `8c6f85b` 06-07, `5873b9b` 07-05, `7189258` 06-30 | .35, .100, .89 exist only to repair the release before them (CI-only break, latent NameError, revert) |
| `80c91be`, `2734124`, `5d388ed`, `ad67c64`, `60af5f5` (Apr-May) | hand-typed test and tool counts in CLAUDE.md corrected after the fact |
| `7a8e28e` 08-07 | `server.json` advertised a stale savings claim through many releases |
| `da23d93`, `f6bf0b5`, `f9e1019` 08-27→31 | CLAUDE.md budget breached during the rotation step |
| `ca39952` 09-01 | a rate written for a future date, wrong for 69 days |
| #101, #147, #294, #281, #397 | a regression issue the morning after the release (handshake, WAL read path, git-root identity, stdio exit, `init` naming 24 tools that do not exist) |
| C-13, C-14, C-15 (`e2e368e`, `a4e428d`, `6c3e24c`) | the new pre-flight was wrong twice about a main commit; PyPI's publisher form does not persist |
| 57% of closes name a version | 43% of reporters were not told what to install |

**Touches:** criteria 6 (install friction), 7 (stability across releases),
N6 (agent-instruction budget), N7 (skip count); DoD 2, 3, 4, 5, 7, 10;
RUNBOOK §1, §1a, §3; Practices 3, 5, 11.

### 2.2 Merge a contributor PR

**What:** a fork PR arrives; CLA status, trial-merge onto `main`, local
run, merge, our follow-up, changelog credit, CLAUDE.md record, release.
**Frequency:** 182 contributor PRs (131 merged, 51 closed); 80 external
merges by non-dependabot authors.

**Steps performed:** CLA status on the head SHA (policy 3d) → trial-merge →
run → merge → follow-up fix → credit. The review step is mostly absent:
92 of 182 contributor PRs have zero reviews; 13 PRs ever got
CHANGES_REQUESTED, all citing real defects (#218 inverted key, #433
precedence, #443 POSIX-relative members that would fail four legs).

**Where it went wrong:** the table in §1, plus: `2a6880c` "resolve review
findings, perf regression" after `6ccc778` #160; `c9f53ef` #378 needed
`f0eda7b`; the Racket series (`22d871a`, `3735d80`, `9e090cd`, `ae087c3`)
each followed by our fix (CRLF fixtures, watcher hashes, undeclared table
keys); #433's thread where the Health Radar bot posted a false `C -> D`
regression on the contributor's thread and it was retracted publicly
(Practice 6). CLA-caused delays: #443 8 days, #433, #439, #107, #91.
Contributor PR bodies carry a DoD-style checklist 39% of the time; a
changelog line 12%.

**Touches:** criteria 1, 7; DoD 1, 3, 9; policies 3, 3a, 3b, 3d.

### 2.3 Add a feature or tool

**What:** new tool or capability: `server.py` registration, tests,
CHANGELOG, CLAUDE.md Key Files invariant, README tool reference, schema
baseline recapture, tier-table registration.
**Frequency:** 207 `feat:` commits; 98 touch `server.py`.

**Steps performed vs required:** tests 167 of 207, CHANGELOG 70, CLAUDE.md
48, README 42; **all four in one commit: 12** (`d43a397` search_ast,
`4a955df` get_tectonic_map are the shape). Typical: `2fb56d8` (21 files,
none of the three docs), `aca8b7c` 08-06 hooks feature with no CHANGELOG.
The schema baseline was regenerated inside the RELEASE commit rather than
with the feature in `8cfaaf4`, `c472c29` and six releases 04-25→06-22.

**Where it went wrong:**

| Cite | What |
|---|---|
| `d4d7809` 04-22 | tool added but absent from tier bundles, template and tests |
| `60af5f5` 03-23, `84a7131` 04-18 | tool-count literals (test and README) broke on every addition; README said 80+ when it was 60+ |
| `4badd59`, `f63cbd1` 07-28/30 | Counter copy pinned a hand-typed tool count and schema-token figure |
| `b524f7e` 04-12 | revert of `.claude/rules/` discovery |
| `f750718`→`621c639` 08-24 | reuse-audit shipped with four defects on first execution (Standing lesson 08-24: imports clean, tested for nothing) |
| #400→#401→#402 | three defects in one feature, three issues |
| #141→#147 | contributor feature (WAL migration) with a read-path regression the next day |
| `a642f51` 04-18 | tiering regressions |

**Touches:** criteria 2, 4 (tool-surface discipline: `core_compact` is at
3,998 of 4,000 tokens), 5, 10; DoD 1-8, 10; Practice 1; the `surface_diff`
stage-5 check.

### 2.4 Review a PR

**What:** grade a diff against the DoD without the implementer's context.
**Frequency:** 257 PRs; 164 with no review at all; our own 67 merged PRs
have `reviews: []` throughout (#499, #529, #563, #546, #589). Since #589
the harness bench comment is the automated half.

**Where it went wrong:** no review in the window cites a weakened test, a
loosened threshold, or a copied number, while the git record shows all
three happened (`d6ecb4f` 08-14 schema guard asserting a file against
copies of itself; `9661e0b` skips; `4badd59` copied figure). #433 is the
one review that verified a claim by mechanism (`git rev-parse ^{tree}`),
and it is the model. Reviews that requested changes were right every time.

**Touches:** every DoD item; STANDARD "Definition of Regression"; Practice
9 (an old test turning red may be the defect's witness).

### 2.5 Benchmark re-run and mirror resync

**What:** re-measure a published number and re-sync every artifact that
mirrors it.
**Frequency:** 39 runs (127 commits touch `benchmarks/` or `harness/`
including releases); weekly on `benchmark.yml` and `main.yml` since Sep.

**Steps performed:** `run_benchmark.py --reference` → `jcm_reference.json`
+ `results.md` + `METHODOLOGY.md` + README + `provenance/measured.json` +
`REPRODUCING.md` → `tests/test_provenance.py`. Since 09-03 the reference
is captured on CI (`benchmark.yml` dispatch `reference=true`, F-13).

**Where it went wrong:**

| Cite | What |
|---|---|
| `556e928` 08-02 | our side was a 03-28 constant while the other side re-measured every run: four months, ratios drifted in our favour, a published winner flipped |
| `b98a907` 08-25 | reference drifted 22 days |
| `610306d`, `bc5a31e` 08-15 | the Counter's saving was quoted, never measured |
| `d6ecb4f` 08-14 | schema-budget guard vacuous |
| `b680498` 08-10 | codex arm result corrected a published claim (negative result) |
| `16e6605` 08-02, `2985ea2` 04-13 | README headline figures and language counts stale |
| `762cd7d`, `9b2123d`, `2be06cc` 09-03 | box-vs-CI 2.5% gap (F-13); four stale scripts to attic |
| 28% of our merged PRs carry a measured number | the delta is usually not in the PR |

**Touches:** criteria 2, 5, N4 (deterministic output); DoD 5, 10; Practice
4; Floors `token.*`, `latency.*`, `schema.*`, `counter.saving_min`.

### 2.6 Fix a reported bug

**What:** reproduce, failing test, minimal fix, CHANGELOG, CLAUDE.md
invariant, close with the version.
**Frequency:** 221 `fix:` commits, 62 citing an issue; 156 closed bugs,
median time-to-close 0.23 days, p90 1.15 days, 87% within 24 h.

**Steps performed:** 59 of 62 issue-citing fixes changed a test in the
same commit; 45 the CHANGELOG; 22 CLAUDE.md. Since Aug the CLAUDE.md half
is a separate `docs(claude): record #N` commit (seven in two days,
`97a5c3b`…`f700f52` 08-17/18). Repro present in the report body 61%; a
test named in the close comment 10%; a timebox posted 5 times.

**Where it went wrong:**

| Cite | What |
|---|---|
| #169→#170, #390→#391→#416, #550→#566→#567, #570→#572, #68→#74 | the fix hit the reported spelling or call site, not the mechanism; the reporter came back (three rounds on one cap; `da20587` 09-01 "fixed for one spelling") |
| #557 | two wrong diagnoses posted (C1, C2) before the reporter instrumented it and found the cause (C10) |
| #375 | reopened; root cause corrected mid-thread |
| `2ff57a8` 05-11 | #291 fix with 57 files and no CHANGELOG |
| `923c3d6` 08-25 | four defects in one commit (one-issue-one-verdict violated) |
| `3450b41`, `1971d2a`, `3878e04`, `3ca2e6e` Mar-Apr | "restore … accidentally removed": the fix deleted something adjacent |
| `24b6c53` 03-25 | a test wrote the developer's real global config (Practice 8) |
| `636744e` 08-28 | CI-only 3.10 failure invisible on any local run |
| 52 issues (17%) | closed with no maintainer comment at all |

**Touches:** criteria 1, 3, 9; DoD 1, 2, 3, 8; ARCHAEOLOGY (485
LOAD-BEARING files, so most fixes are near a guard that already names the
lesson); policy 1, 2, 3a.

### 2.7 CI / workflow change

**What:** edit `.github/workflows/*`, the composite action, or the gate.
**Frequency:** 41 commits; 22 in the Sep series.
**Where it went wrong:** `52d0791` (health-radar `--depth=1` shortened a
full clone, Practice 6); `9c1d6b0`, `98f6975` (SHA pinning after the
fact); Sep: `5aeefaa`, `ba7dcd6` (format, `encoding=`), CodeQL's invalid
YAML (C-10), `12116fe` (3.10 has no `tomllib`), C-13/C-14, C-15.
**Touches:** criteria 7, 8; `tests/test_workflows_pinned.py` and
`tests/test_ci_env_reproduce_command.py` already gate this loop, so it
gets no command; `docs/cicd/RUNBOOK.md` §8 is its checklist.

### 2.8 Triage an issue

**What:** classify (bug, feature, question, duplicate, security), split a
multi-finding report, label, respond with a reproduction request or a
timebox.
**Frequency:** 308 issues (84 in Mar, 83 in Aug); 57 self-filed.
**Where it went wrong:** 275 of 308 (89%) carry no label; kind lives in
the title convention instead. #444 arrived as a three-finding QA pass and
was split by hand into #447, #448, #449; #557→#558 and #308→#318 likewise.
#574 (the only open issue) is labelled `bug` and is a dependency question.
Three duplicate PyPI-quarantine reports (#308, #309, #312).
**Touches:** policy 1 (one issue, one verdict), 3a (24-hour timebox with a
stated default), 3c (profile the author before a vendor-shaped PR);
`.github/ISSUE_TEMPLATE/`.

### 2.9 CLAUDE.md rotation and split

**What:** keep CLAUDE.md under `claude_md.max_chars` without deleting a
rule.
**Frequency:** 5 (`0fa0e23` 07-28, `da23d93` 08-27, `f6bf0b5` 08-28,
`f9e1019` 08-31, `dcde95c` 09-03). Currently ~139,005 of 140,000.
**Where it went wrong:** proposed rotating the smallest section before
measuring (Practice 5); first split target was gitignored `docs/`; the
rotation is two edits and `test_claude_md_rotation.py` caught .311 at one
failure. It is a knowledge problem (measure sections, split by
derivability), so a skill; the release workflow invokes the measurement.
**Touches:** N6; Practice 5.

### 2.10 Retire or skip a test

**What:** delete, skip-mark or weaken a test.
**Frequency:** 3 deletion commits; 21 `pytest.mark.skip` and 39
`pytest.skip(` commits.
**Where it went wrong:** `9661e0b` 08-23, ten modules skipping wholesale
hid 209 tests across four releases; `e6fb764` 04-15 skipped packing tests
when the native backend was absent; `b82fac2` 07-29 and `c2201a5` 07-31
were the corrections that made CI run what the dev box skipped. Since
09-03 a retirement needs `harness/retired.json` (DoD 11) and the fast and
full tiers have skip ceilings. What remains uncaught is the skip-mark or
weakened assertion that never reaches a ceiling; that is a post-edit hook.
**Touches:** N7; DoD 1, 11, 12; ARCHAEOLOGY.

## 3. What already exists under `.claude/` and in CLAUDE.md

### 3.1 The constraint first: `.claude/` is gitignored and sdist-excluded

`.gitignore:58` ignores `.claude/` wholesale (the v0.2.6 credential-leak
fix: `settings.local.json` carried inline tokens and hatchling bundled it)
and `pyproject.toml` excludes `.claude/` from the sdist, asserted by
`tests/test_sdist_exclusions.py`. In this checkout `.claude/agents` and
`.claude/skills` are **symlinks into `C:\MCPs\.claude\`**, the suite-level
directory, so the release skill is shared by four repos and none of it is
in any repo's git. Consequence for this layer: a command, agent or hook
file written under `.claude/` is machine-local and gone on a fresh
checkout, which is the trap CLAUDE.md already documents for the release
skill. Phase 2 must pick a tracked home. The options are (a) narrow the
ignore to `.claude/settings.local.json` and `.claude/*.bak` and keep the
sdist exclusion of the whole directory (the leak vector was the sdist, and
that exclusion stays), or (b) a tracked `workflows/` directory that an
install step symlinks into `.claude/`. Both are jjg's call; the memory
"`.claude/` is gitignored and must stay that way" was written about the
sdist vector and is satisfied by either.

### 3.2 Inventory and classification

| Item | Where | What it is | Verdict | Why |
|---|---|---|---|---|
| `release` skill (24.5 KB) | `C:\MCPs\.claude\skills\release\SKILL.md` (symlink) | the full release + PR + community checklist, suite-wide | **REVISE** | Its steps 1-8 are now RUNBOOK §1 (dispatch, not `twine upload`); steps 4-7 describe the retired local path and step 7's registry line is superseded by `release.yml`'s registry job. Keep the PR-workflow and CLA halves; point the publish half at the runbook. Untracked, so the revision is machine-local until §3.1 is decided. |
| `observatory` skill (3.2 KB) | same dir | scorecard context; scores pulled live | **KEEP** | Short, points at authority, states when to load. |
| `spokesperson` agent (5.3 KB) | `C:\MCPs\.claude\agents\spokesperson.md` | drafts and reviews outward-bound prose; never posts | **KEEP** | Exactly the drafts-only shape this layer wants for `/triage-issue` responses; the review subagent can hand it the close comment. |
| `settings.local.json` | `.claude/` | 40 allow rules, mostly `Bash(gh:*)`, `Bash(git *)`, `Bash(python:*)` | **REVISE** | Allows everything the brief says a workflow must never do (`gh release`, `git push`, `twine` via `Bash(python:*)`). A deny list for publish, tag, force-push and posting belongs in tracked project settings; the local file stays local. |
| `settings.local.json.bak` | `.claude/` | Aug 12 copy | **RETIRE** | Superseded; it is the file class that leaked in v0.2.6. |
| `settings.local.json` (suite) | `C:\MCPs\.claude\` | ~1,000 allow rules accumulated since Mar, including one-off `gh api` lines with issue-comment ids | KEEP (not this repo's) | Out of scope; noted because it shows what "permit by accretion" produces. |
| User hooks | `C:\Users\j\.claude\settings.json` | `PreToolUse` (Read/Grep/Glob/Bash → `hook-pretooluse` steering), `PostToolUse` (Edit/Write → `hook-posttooluse` reindex; memory sync), `SessionStart`, `PreCompact`, `SubagentStart`, `TaskCompleted` (`hook-taskcomplete` diagnostics), `Notification` | **KEEP** | Product hooks (jcodemunch's own), user-scoped, not repo process. The new hooks are ADDITIONAL project-scoped entries; none of these is replaced. Note `hook-taskcomplete` already runs dead-code and untested-symbol diagnostics at task end, which is adjacent to the pre-PR check. |
| `.claude/commands/`, `.claude/hooks/` | absent | — | — | Nothing exists; the design starts from zero. |
| `scripts/release_preflight.py` | tracked | the release gate (pins, changelog, tag, PyPI, 3b PRs, lint, harness) | KEEP | `/release` invokes it; it already does the recompute. |
| `scripts/surface_diff.py`, `dod_changelog.py`, `pr_bench_comment.py` | tracked | CI stage 4/5 checks | KEEP | The commands call these, never reimplement them. |
| `harness/__main__.py` | tracked | `fast`, `full`, `bench`, `all`, `check <id>`, `threshold`, `thresholds`, `corpora`, `warm`; `--summary`, `--annotate`, `--write-results`, `--offline`, `--stamp` | KEEP | The only thing a workflow runs. It has no machine-readable delta output beyond `--summary` markdown and `harness/results/latest.json`; a FINDINGS candidate for Phase 3. |

### 3.3 CLAUDE.md sections that describe process

| Section | Chars (approx) | Verdict | Where it goes |
|---|---|---|---|
| Current State | ~9k | KEEP | Not process; rotation is the release loop's step. |
| CI/CD: the harness's judgment (2026-09-04) | 1.6k | KEEP | Already short and points at DESIGN/RUNBOOK. |
| The Standard and the Harness (2026-09-03) | 2.4k | REVISE | Its last paragraph lists required checks by name (`lint`, `Retrieval-quality gate`, `Harness fast tier`, 8 `test (os, py)` legs) — **those names are the 2026-09-03 list, superseded on 09-04 by the 21 PR-gate contexts**, a stale copy of the thing it warns about. Replace with the RUNBOOK §8 pointer. |
| Issue + release policy (2026-07-28) | ~12.4k | KEEP, pointer | Rules and commands; the forensics already moved to ISSUE-HISTORY. `/triage-issue` and `/review` reference it; nothing restated. |
| Registry verification reads a NESTED row | ~3k | REVISE | `scripts/registry_verify.py` now exists and `release.yml` runs it; the section can shrink to the cmd.exe line and the pointer. |
| Reproducing CI's environment (release step 2c) | ~2.5k | REVISE | Cites `test.yml` and the release skill's step 2c; the command lives in the harness now (`full` tier). Keep the lesson line, point at the harness. |
| Maintenance Practices 1-11 | ~14k | KEEP | Every command's checklist cites these by number; they are the authority the brief says to point at, and their numbers are an index (Practice 10's own warning). |
| Standing lessons | ~9k | KEEP | Review-subagent input, not process. |
| Key Files / CLI / Env invariants | ~55k | KEEP | Not process. |

Net: three sections carry restated process that has drifted (required-check
names, registry steps, the CI-env command), which is the brief's principle
5 observed in the file. The restructure moves them to pointers; nothing
with a ⚠ rule is deleted.

### 3.4 Claude Code capabilities this design can rely on (2.1.260)

Verified from the settings schema in use: hook events `PreToolUse`,
`PostToolUse`, `SessionStart`, `PreCompact`, `SubagentStart`,
`TaskCompleted`, `Notification` are in use here; `UserPromptSubmit`,
`Stop`, `SubagentStop`, `SessionEnd` exist in this build. Project commands
are `.claude/commands/*.md`, subagents `.claude/agents/*.md`, skills
`.claude/skills/*/SKILL.md`, shared settings `.claude/settings.json` with
`permissions.deny`. There is **no pre-commit or pre-PR event**: the closest
is a `PreToolUse` hook matched on `Bash`/`PowerShell` whose command text
contains `git commit` or `gh pr create`, which can block with a reason.
That is a substitution, documented here so DESIGN.md does not assume an
event that does not exist. The hook runtime budget is the hook's own
timeout; a hook that exceeds it is killed, so "degrade to a warning" must
be implemented inside the hook, not by the harness.

## 4. What this ranking says about the design

- The release loop's 35 repairs are almost all one class: a mirror not
  regenerated (pin site, whatsnew, uv.lock, CHANGELOG block, CLAUDE.md
  counts). `release_preflight.py` and the gate's `done: version pins`
  check now cover the pin sites; what no instrument covers is "was the
  release PR's rotation measured before it was written" and "does the
  close comment name the version".
- The contributor-merge loop's 50% follow-up rate is the strongest case
  for the review subagent receiving the diff's DELETIONS as a first-class
  input, not only its additions.
- The feature loop's 94% DoD-incomplete rate is the cost of Practice 1
  being prose; `surface_diff.py` now fails the gate for a surface change
  without the three docs, but a description edit or a new argument does
  not change the surface and still needs the four files.
- The benchmark loop's errors are all "one artifact re-measured, its
  mirrors not", so `/benchmark-compare` is a diff over every mirror, per
  row, never a total.
- Triage is high-volume and cheap to get wrong, and 89% unlabeled means
  the label recommendation alone would change the record.
