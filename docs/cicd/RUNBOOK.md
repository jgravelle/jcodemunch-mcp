# CI/CD RUNBOOK (2026-09-04)

What to do, in order, when the pipeline needs a human. Every command below
is meant to be typed from a cmd.exe prompt on jjg's box unless it says
otherwise; `gh` calls carry `GITHUB_TOKEN=""` in Git Bash form where that
matters and are written for cmd.exe otherwise. Companion: `DESIGN.md` (why),
`VERIFICATION.md` (proof), `FINDINGS.md` (what CI could not do).

## 1. Cut a release

The only human acts are the release PR's merge and one dispatch.

0. In a Claude Code session: `/release`. It confirms `main` is green,
   derives the version and shows the derivation, reconciles `[Unreleased]`
   against the merged PRs, recomputes every published figure and refuses on
   a disagreement, drafts the notes, and does step 1 for you, then stops
   (`docs/workflows/DESIGN.md` §2.3). Steps 2-4 stay yours; the session's
   deny list refuses them.
1. Open the release PR: bump the seven pin sites (`pyproject.toml`,
   `server.json` x2, `.claude-plugin/plugin.json`, `uv.lock` name-scoped
   line, `whatsnew.json` current + entry), write the `## [X.Y.Z]` block in
   `CHANGELOG.md`, rotate CLAUDE.md Current State, label the PR `release`.
   The gate's `done: version pins` check refuses a `release` label without a
   bump and a bump without its heading.
2. Merge it when every required check is green (branch protection will not
   let you otherwise).
3. Dispatch, from the Actions tab or:

   ```
   gh workflow run release.yml -R jgravelle/jcodemunch-mcp -f version=X.Y.Z -f dry_run=false
   ```

   The workflow: pre-flight on `main`'s HEAD (required checks green, pins,
   changelog, tag absent, PyPI absent, no MERGEABLE CLEAN contributor PR),
   build once, Test PyPI, clean-venv smoke on ubuntu and windows, tag, PyPI,
   post-publish smoke from PyPI with the tool count recomputed, GitHub
   release from the CHANGELOG block, MCP registry.
4. Read the run to the end. A P0 issue means §3. Then Practice 11:
   reinstall and restart the local server.

`dry_run=true` (the default until the first real publish) stops before any
upload, tag, release or registry write and smokes the built wheel instead.

⚠ Never push a `v*` tag by hand. The workflow refuses it and says so.
⚠ Never run `twine upload` locally again. After the first pipeline publish,
revoke the `~/.pypirc` token on PyPI (Account settings → API tokens) and
delete the file; §5.

## 1a. Until PyPI persists a trusted publisher (C-15)

`release: pypi` fails with `invalid-publisher` and the run stops after
`release: tag`. Test PyPI, both smokes and the tag are done at that point.
Finish by hand, with the artifact CI built (never a local rebuild):

```
gh run download <run-id> -n dist -D dist-ci
uvx --from twine twine check dist-ci\*
uvx --from twine twine upload dist-ci\jcodemunch_mcp-X.Y.Z*
```

then the post-publish smoke from PyPI in a fresh venv
(`scripts\handshake.py --expect-version X.Y.Z --command <venv>\Scripts\jcodemunch-mcp.exe --fixture testsixtures\pkg_smoke`),
`gh release create vX.Y.Z dist-ci\* --title ... --notes-file ...` with the
notes rendered from the CHANGELOG block, and the registry line from
CLAUDE.md. Re-try the publisher registration on PyPI before each release;
when it finally lists one, this section is deleted and `~/.pypirc` is
revoked (§5).

## 2. Read a failed check

Open the check. The **summary** (not the log) carries one line per verdict:

```
threshold                         criterion   floor      observed   verdict
latency.search_symbols_warm_p95_ms   5        <= 23      54.6       FAIL
```

and the Files tab carries the same as an annotation. `python -m harness
check <id>` reproduces one Floor locally; `python -m harness threshold <id>`
prints the Floor. A pytest failure lists the failing test ids in the
annotation; a lint error lists the ruff line. A Floor never lives in a
workflow: to move one, edit `harness/thresholds.json` with a `loosened`
block and say so in CHANGELOG (STANDARD.md, Definition of Regression).

The definition-of-done checks say what to add: a CHANGELOG line (or the
`no-changelog` label with a reason in the PR), a pin site, or README +
CLAUDE.md + a CHANGELOG line naming the tool.

## 3. Post-publish check failed (P0 issue opened)

PyPI has the version and cannot be re-uploaded. Do not yank from a script.

1. Read the P0 issue and the run. The failure is one of: install failed,
   handshake failed (version, instructions, tool list), fixture index or
   tool call failed, tool count moved.
2. Reproduce by hand from a clean venv, pinned, never through bare `uvx`:

   ```
   uv venv %TEMP%\pp --python 3.12
   uv pip install --python %TEMP%\pp\Scripts\python.exe jcodemunch-mcp==X.Y.Z
   %TEMP%\pp\Scripts\python.exe scripts\handshake.py --expect-version X.Y.Z --command %TEMP%\pp\Scripts\jcodemunch-mcp.exe --fixture tests\fixtures\pkg_smoke --expect-languages python,typescript,javascript,go
   ```

3. Decide: if the artifact is unusable, yank on PyPI by hand with a reason,
   and ship X.Y.Z+1 through the pipeline (policy 2: nothing waits). If the
   check was wrong, fix the check in a PR and close the issue with the run
   link. Either way the issue records the decision.

## 4. PyPI quarantine or index trouble

- **Quarantine / account block** (it happened 2026-06): the pipeline's
  `release: pypi` job fails at upload. Nothing else is affected. Fix the
  cause (an undisclosed persistent or network behaviour was the last one;
  README "Background behavior, fully disclosed" is the compliance surface),
  reply to PyPI, and re-dispatch when lifted. Do not tag by hand meanwhile.
- **Propagation lag**: `release: post-publish` polls up to 10 minutes. If
  it times out, re-run only that job from the Actions UI once the version
  shows on `https://pypi.org/pypi/jcodemunch-mcp/X.Y.Z/json`.
- **Test PyPI down**: `release: test pypi` fails; re-dispatch later. There is
  no skip switch by design.

## 5. Trusted publishing: first setup and rotation

1. On PyPI and on Test PyPI: project `jcodemunch-mcp` → Publishing → add a
   GitHub publisher: owner `jgravelle`, repository `jcodemunch-mcp`, workflow
   `release.yml`, environment `pypi` (and `testpypi` on Test PyPI).
2. On GitHub: Settings → Environments → create `testpypi` and `pypi`,
   deployment branches: `main` only; on `pypi` set a 5-minute wait timer.
3. Dispatch a dry run, then a real one.
4. Revoke the token in `~/.pypirc`, delete the file.

Rotation is a repeat of step 1 with the old publisher removed: there is no
secret to rotate, which is the point. If the workflow file is renamed, the
publisher must be edited to match or the OIDC exchange fails with a message
naming the mismatch.

## 6. Bypassing a gate in a real emergency

There is no bypass in the mechanism. If a broken gate is blocking a fix
users need (policy 2), and the gate cannot be repaired in the same PR:

1. Open an issue labeled `bypass` FIRST, naming the gate, the PR, and why.
2. Temporarily lift admin enforcement:

   ```
   gh api -X DELETE repos/jgravelle/jcodemunch-mcp/branches/main/protection/enforce_admins
   ```

3. Merge with the admin override. Restore within the hour:

   ```
   gh api -X POST repos/jgravelle/jcodemunch-mcp/branches/main/protection/enforce_admins
   ```

4. Link the merge and the restore in the issue; close it only when the gate
   is repaired. `main.yml` will open a `regression` issue for anything the
   bypassed gate would have caught; do not close that one without a fix.

## 7. Weekly results PR and regression issues

- Mondays, `main.yml` opens `harness: weekly bench result (<date>)`. Merge
  it when green; it is labeled `no-changelog` on purpose.
- A `regression` issue names one threshold on `main`. Fix or, with a
  measured reason, loosen with a `loosened` block; close with the PR link.
- A `drift` issue is the nightly's: a dependency, runner image or grammar
  moved without a commit. Same rule.
- A `latency.*` FAIL on the bench tier is informational until harness F-19
  closes (three CI runs); the summary says so.

## 8. Adding or renaming a check

A required check is matched by NAME. Renaming a job in `pr-gate.yml`
without updating branch protection makes `main` unmergeable, and removing a
required name silently stops gating. The list lives in one place:

```
gh api repos/jgravelle/jcodemunch-mcp/branches/main/protection --jq .required_status_checks.contexts
```

`scripts/release_preflight.py` reads that list and fails on a missing run,
so a drift shows up at the next release at the latest.

## 9. The inbound layer (headless issue and PR work)

`docs/inbound/POLICY.md` is the contract; `docs/inbound/DESIGN.md` is
each job. This section is the human's part.

**Turn it on or off.** The repository variable `INBOUND_ENABLED` is the
one switch. Only the exact string `true` is on.

```
gh variable set INBOUND_ENABLED --body true
gh variable set INBOUND_ENABLED --body false
```

Every job reads it at its first step and again before its first write, so
a flip stops the layer within one step. Deleting `ANTHROPIC_API_KEY` is
the coarse stop and is not reversible without the key.

**Approve a drafted reply.** Triage and dependency evaluation never post
prose. A draft is a file under `drafts/` on the `inbound-ledger` branch
with `approved: false` in its front matter. To post it, edit the file on
that branch and set `approved: true` in a commit of your own; the next
sweep (daily, 06:30 UTC) posts it as the App and moves the file to
`drafts/posted/`. An App-authored approval never posts. Editing the body
before approving is fine; it posts the edited text and resets that
category's graduation streak (POLICY 9).

**Ask for a fix.** Apply `agent-fix` to an issue. The pre-flight declines
unless a human applied the label (`INBOUND_AUTOFIX` stays absent), the
issue carries none of `agent:reverted`, `agent:in-progress`,
`inbound:security`, and no merged revert names it since your label. The
result is a DRAFT PR on `inbound/fix-<n>-*` labelled `agent-authored`, or
`needs-human` on the issue with the reason in the run's audit record. The
draft becomes ready only when the PR gate, the `selfcheck` check and the
reviewer verdict are all green; otherwise it carries `agent:incomplete`.
Merging is yours.

**Read the digest.** One issue per ISO week, `inbound digest <week>`,
labelled `inbound:digest`, Mondays 06:45 UTC. Every number in it is
computed by `.github/inbound/digest.py` from the ledger branch; the model
wrote at most the opening paragraph. `needs-human` items older than 7 days
are listed there; each is a decision only you can make.

**Something looks wrong.** Flip the switch off, then read the run's audit
record (`inbound-audit-<run id>` artifact, or `ledger/<YYYY-MM>.jsonl` on
`inbound-ledger` after the next sweep). A record with `outcome: failed`
names the step. A security item is named by number only, everywhere.

**Before the first run (once).** FINDINGS IN-3, IN-4, IN-6, IN-8: create
the App `jcodemunch-inbound` (repository permissions: Contents, Issues,
Pull requests read and write; Variables read; Metadata read; no webhook;
installed on this repository only), store `INBOUND_APP_ID`,
`INBOUND_APP_PRIVATE_KEY` and `ANTHROPIC_API_KEY` as repository secrets,
add the App to the CLA allowlist, enable private vulnerability reporting,
and add the ruleset that confines the App to `inbound/**` and
`inbound-ledger` (target `branch`, include `~ALL`, exclude
`refs/heads/inbound/**`, `refs/heads/inbound-ledger` AND
`refs/heads/main`; rules creation, update, deletion; bypass actors the
Write, Maintain and Admin repository roles, mode `always`). ⚠ Leaving
`main` inside it makes every human merge need `--admin` and stops
auto-merge (FINDINGS IN-19); `main` is protected by branch protection
already. `docs/inbound/VERIFICATION.md` row 1.10 tracks it.

## 10. The competitive loop (the tier that measures us against the field)

`docs/competitive/DESIGN.md` is the loop; `POLICY.md` section 4.4 and
section 7 (the inbound contract) govern its three jobs. This section is the
human's part. Nothing in it can run before the steps below.

**Turn it on.** The loop inherits `INBOUND_ENABLED` (section 9) for all
three jobs, and the post job also needs a second variable. Only the exact
string `true` is on; absent is off.

```
gh variable set COMPETITIVE_POST_ENABLED --body true
gh variable set COMPETITIVE_POST_ENABLED --body false
```

**Create the labels (once).** The post job applies exactly one of these
plus `needs-human`; they do not exist and only a human creates them
(POLICY 4.4 lists labels as never-touch; the post job is the one exception,
for these four).

```
gh label create competitive-gap --description "a competitor ahead on a comparable axis, meaningful, from a recorded run"
gh label create competitive-watch --description "we lead and the gap narrowed on two consecutive runs"
gh label create competitive-idea --description "a set member's release names a capability (title quoted as data)"
gh label create standard-proposal --description "a competitor past a STANDARD Target on two runs; a human edits the standard or declines"
```

**Run it.** Monthly on the first Sunday 03:00 UTC (`competitive-run.yml`),
or by hand with a reason and an optional tool:

```
gh workflow run competitive-run.yml -f reason="first run on a runner (CF-53)"
gh workflow run competitive-run.yml -f reason="serena release" -f tool=serena
```

The first dispatched run is the measurement FINDINGS CF-53 is waiting for
(the full set did not fit the 240-minute budget on a workstation) and the
three-run Linux baseline CF-8 and CF-14 need before `latency_call_ms` and
`index_cold_seconds` are read at all. Read its summary from the
`competitive-result-<run id>` artifact or `competitive/results/latest.md`
on `inbound-ledger`; which runner it ran on is in the result JSON's
header (`runner.os`, `runner.python`, `runner.ci`), beside `latest.md` in
the same directory, not in the summary.

**Approve a draft.** Drafts are files under `competitive/drafts/` on
`inbound-ledger`, one per fingerprint (`competitive-id:` line), with
`approved: false` in the head. Edit the file on that branch and set
`approved: true` in a commit of your own; the next post run (daily 07:00
UTC) opens the issue, applies the label, and writes `posted: #<n>` back.
The post script checks only that the line reads `approved: true`; the
guarantee that no headless job approves rests on the App never writing
that line (`findings.py` and `ledger_merge.py` write `approved: false`
and keep an existing head), so a human commit is the only thing that can
flip it. A `standard-proposal` draft's body opens with the sentence that
the standard is edited only by a human; approving it opens the issue,
nothing more.

**Read a finding.** A gap draft names the axis, corpus, category, both
medians and spreads, the band, the competitor's pinned release and image
digest, the run file, and one hypothesis from the fixed list. Two rows
travel with caveats the draft must carry: a token row where the tool
returns hits and the body is read elsewhere (CF-24), and a token row where
the harness chose the pattern (CF-29). A `tool_not_called` hypothesis
names the adapter before the tool; check the adapter's call plan first.
Ours was the first case (CF-51), and that one is both: a harness mapping
defect and a real loss, because a user who reaches for the same tool for
that question gets the same answer.

**Weekly feed.** Sundays 04:00 UTC (`competitive-feed.yml`) reads each set
member's latest release through registries on a read-only token, drafts a
`competitive-idea` when a title carries a capability word, and dispatches
one re-run when a release names a measured axis. It reads the ledger first
so one release is re-run once.

**Something looks wrong.** Flip `INBOUND_ENABLED` off: each job re-reads
it before its next write, so a running job finishes its current step
and writes nothing after (the run job's container step is up to 240
minutes long and holds no write; the flip stops the ledger push after
it). Read the run's audit record: the `competitive-audit-<run id>`
artifact, or `competitive-audit-<run id>-gate` when the gate refused. A container that outlived its run is named
`jcm-compete-<hex>`; `docker ps` shows it and `docker kill` ends it.
