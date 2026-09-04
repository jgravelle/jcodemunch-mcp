---
name: release
description: "Publishing a jMunch release (jcodemunch-mcp, jdocmunch-mcp, jdatamunch-mcp, jragmunch-cli), reviewing/merging/closing PRs, and responding to the community. Load before any version bump, PyPI upload, tag, GitHub release, MCP registry publish, or PR merge."
---

# Release & PR Workflow

> **REVISED 2026-09-04 (docs/workflows/DESIGN.md section 5).** The PUBLISH
> half of this file (steps 4-7 below: local build, `twine upload`, `git tag`,
> `gh release create`, `mcp-publisher`) is SUPERSEDED by `release.yml`,
> dispatched per `docs/cicd/RUNBOOK.md` section 1; the `/release` command
> prepares the release PR and stops. Those steps stay here as the record of
> the retired path and for RUNBOOK section 1a's hand-finish while PyPI
> persists no publisher (C-15). The PR-workflow and CLA halves are current.
> This copy is TRACKED in this repo; the suite copy under `C:\MCPs\.claude`
> serves jdoc and jdata.

⚠⚠ **THIS FILE MIXES TWO AUDIENCES AND THE SPLIT IS LOAD-BEARING.** Almost every
command below is run BY THE AGENT through the Bash tool (Git Bash), so bash
syntax is correct for them: `PYTHONPATH=src python ...`, `GITHUB_TOKEN="" gh ...`,
`BR=$(...)`. **Inline env-var prefixes and `$(...)` do NOT exist in cmd.exe or
PowerShell**, which is what jjg is at when he types.

**Step 7 is the ONE command a HUMAN types interactively**, and on 2026-08-13 it
failed mid-release because it had silently inherited bash path syntax from its
neighbours (`~\mcp-publisher.exe` -> "The system cannot find the path
specified"). **The dev platform is Windows.** Any line added here that a human
will type must be given in cmd.exe AND PowerShell form, with no `~`, no inline
`VAR=x cmd` prefix, and no `$(...)`. Mark the shell explicitly.

## Publishing a release

⚠⚠ **BEFORE ANY OF THIS: are you holding the release for something that is not
ours?** A signature, a contributor PR, a reply, a re-run, "so it can go out
together" — if the thing being waited for needs someone else to act, **ship
now** and let them ride the next one. Policy 2 is not broken by anyone
overruling it; it is broken by a batching argument that never mentions it
(2026-08-18: five merged green fixes held two days to avoid three cheap conflict
resolutions). **"Reduce our churn" is not a release criterion** — conflict
resolution and re-merges are our costs to absorb, and the moment avoiding them
shapes *when users get fixes*, we are spending their latency for our
convenience. See CLAUDE.md policy 2e.

```bash
# 1. Bump EVERY pin site, not just pyproject.toml. jcm now has FOUR:
#      pyproject.toml, server.json (version + packages[].version),
#      .claude-plugin/plugin.json, uv.lock, whatsnew.json (FIFTH site,
#      found 2026-08-09: both `current` AND a new `entries[]` record)
#    `grep -rn "<old-version>" --include=*.json --include=*.toml --include=*.lock .`
#    is the only reliable enumeration — the list GROWS (plugin.json arrived
#    after this checklist was written and the checklist said "pyproject AND
#    server.json" straight through it).
#    ⚠ That grep also hits GENERATED files. `mcpb/manifest.json` is gitignored
#    and written by mcpb/build.py from pyproject — editing it does nothing.
#    Check `git check-ignore` before adding a hit to this list.
#    ⚠ uv.lock: edit ONLY the `name = "<pkg>"` version line. Do NOT `uv lock` to
#    bump a version — a local uv newer than the CI pin rewrites unrelated
#    platform markers (0.12.1 stripped `sys_platform` conjuncts off 53 nvidia
#    deps on a patch bump). The workflow note documents the older-uv hazard;
#    this is the third direction.
# 2. Run tests (the *_sync.py gates fail if step 1 missed a site; CLAUDE.md's
#    Current State rotation is gated too, so bumping means rotating)
PYTHONPATH=src python -m pytest tests/ -q
# 2b. ⚠⚠ LINT. `uv run ruff check src/` — CI runs it and the local pytest run
#    does NOT. A green suite is not a green build.
#    2026-08-07: `refresh --json` shipped with `_json` unbound (ruff F821) and
#    stayed broken for FOUR releases (.259-.262) because nobody ran this or read
#    the check afterwards.
uv run ruff check src/
# 2c. ⚠⚠ REPRODUCE CI's ENVIRONMENT. A dev box has packages CI does not.
#    `openai` (optional extra) and `numpy` (dev-only in jdoc) are both present
#    here and absent there, so tests that import them pass locally and fail on
#    all 8 matrix jobs. 2026-08-09: jdoc 1.128.0 went RED this way.
#    ⚠⚠ SYNC FIRST, WITH THE SAME FLAGS CI USES. This step read
#    `uv run --python 3.13 python -m pytest tests/ -q` until 2026-08-28 and
#    NEVER built CI's environment -- no `--extra watch`, no dev-group sync. It
#    only looked correct because `.venv` happened to carry the extras from an
#    earlier sync, so the command was inheriting a state it did not create.
#    ⚠⚠ Caught mid-release: the run came back **exit 0 with the totals
#    reconciling exactly**, and 105 tests had silently not executed
#    (8721 passed / 19 skipped -> 8634 passed / 124 skipped, same total).
#    `watchfiles` -- the `[watch]` extra CI installs BY NAME -- was absent.
#    ⚠ READ THE SKIP COUNT, not just the exit code and the total. Expect the
#    documented 19-26 range; a jump means the environment, not the code.
#    ⚠ The flags must match `.github/workflows/test.yml`'s install step
#    verbatim; `tests/test_ci_env_reproduce_command.py` fails if they drift.
uv sync --locked --group dev --extra watch --python 3.13
uv run --python 3.13 pytest tests/ -q
# 3. Commit, then PUSH and READ CI *BEFORE* the irreversible steps below.
#    ⚠⚠ Steps 4-7 cannot be taken back: PyPI cannot be re-uploaded, the registry
#    advertises whatever it was given, and a "your bug is fixed" reply to a
#    contributor cannot be unsent. 2026-08-09: jdoc 1.127.0 did all of them and
#    THEN read CI, which came back red. It was a flake; that is luck, not
#    process. The very next release (1.128.0) read CI first and caught a real
#    red on all 8 jobs with nothing shipped.
#      git push origin <branch>   # then jump to step 8, and only then continue
#      uv run python scripts/release_preflight.py --version <new>   # 2026-09-03: reads the
#      required checks on HEAD, pins, tag, PyPI, 3b PRs, lint, fast tier. PREFLIGHT PASS or stop.
git commit -m "release: vX.Y.Z — <summary>"
# 4. Build, CHECK, then publish. ⚠⚠ NOT `python -m twine` — it is broken on this
#    box and upgrading it is the WRONG fix. See the note below before "helpfully"
#    simplifying these three lines back into one.
# ⚠ 2026-08-18: `python -m build` is NOT installed in the project venv and
#   `uv run python -m build` dies with `No module named build`. Use uvx, for the
#   same reason twine moved there — a throwaway env that cannot rot.
uvx --from build pyproject-build
uvx --from twine twine check  dist/*X.Y.Z*   # gate: fails BEFORE anything ships
uvx --from twine twine upload dist/*X.Y.Z*
# 4b. ⚠⚠ jdatamunch AUTO-RELEASES on push to master: a `Release` workflow
#    creates the tag AND the GitHub release with CI-built artifacts. So for
#    jdata, `git tag`/`git push --tags` is a no-op and `gh release create`
#    fails with "a release with the same tag name already exists" -- that is
#    the automation working, not a failure. Check what it produced (`gh release
#    view <tag> --json assets,body`) and `gh release edit` the body if needed.
#    PyPI is still MANUAL there (local credentials, by design). jcm and jdoc
#    have no such workflow: steps 5-6 below are theirs.
# 5. Tag + push. ⚠⚠ RESOLVE the default branch, never type it: jcm is `main`,
#    jdoc is `master`. 2026-08-12, jdoc 1.132.0: a stale local `main` existed, so
#    `git checkout main` succeeded silently and `git push origin main` CREATED a
#    remote branch instead of erroring. Only step 8's "read CI before shipping"
#    caught it. A wrong-branch push is recoverable; a wrong-branch PyPI upload
#    is not.
BR=$(GITHUB_TOKEN="" gh repo view jgravelle/<repo> --json defaultBranchRef -q .defaultBranchRef.name)
git rev-parse --abbrev-ref HEAD    # must equal $BR before going further
git tag vX.Y.Z
GITHUB_TOKEN="" git push origin "$BR" && GITHUB_TOKEN="" git push origin vX.Y.Z
# 6. GitHub release
GITHUB_TOKEN="" gh release create vX.Y.Z dist/*X.Y.Z* --repo jgravelle/<repo> --title "..." --notes "..."
# 7. MCP registry (NOT optional - this is what mcp.so/MCPFind/mcprepository/PulseMCP display)
#    ⚠⚠ The registry JWT lives FIVE MINUTES. login and publish MUST be one
#    command, run from the repo root (publish reads server.json from the CWD).
#    The device flow blocks on a browser and its prompt does not surface from an
#    agent shell, so jjg runs this in-session with the `!` prefix.
#    ⚠⚠ THE DEV PLATFORM IS WINDOWS. `~` IS NOT A PATH HERE. cmd.exe treats it as
#    a literal directory name and fails with "The system cannot find the path
#    specified" -- 2026-08-13, mid-release, because this line used to read
#    `~\mcp-publisher.exe`. The binary lives at C:\Users\j\mcp-publisher.exe.
#    ⚠⚠ GIVE THE LITERAL PATH. NO `~`, NO `%USERPROFILE%`, NO `$env:USERPROFILE`.
#    jjg has asked for full paths TWICE -- 2026-08-13 (`~`) and 2026-08-31
#    (`$env:USERPROFILE`), each time mid-release, each time after the
#    expanding form failed at his prompt. An env var buys portability this
#    line does not need: there is ONE dev box and the binary is at
#    C:\Users\j\mcp-publisher.exe. Pick the line matching the prompt you are
#    actually at:
#    ⚠⚠ **GIVE THE cmd.exe FORM. ONE FORM. NO MENU.** jjg is NEVER at a Bash
#    prompt -- stated flatly 2026-09-02 (*"We've danced this dance a thousand
#    times"*) after this block's `/c/...` "default route" was handed over and
#    died on "The system cannot find the path specified".
#    ⚠⚠ **THE `!` PREFIX RUNS GIT BASH, AND THAT IS NOT THE RULE.** The
#    2026-09-01 revision reasoned from the mechanism -- `!` is bash, so write
#    bash -- and inverted the practice, because the premise it needed was which
#    prompt jjg ACTUALLY USES, and nobody had asked. **A mechanism is not a
#    habit.** If a line must genuinely run through `!`, that is a tool call to
#    make, not a paste to hand over.
#    ⚠ Offering three labelled forms is not thoroughness; it is the indecision
#    that picked the wrong one. Delete the alternatives.
#      THE LINE (cmd.exe; `/d` because he will be in another repo's directory):
#        cd /d C:\MCPs\<repo> && "C:\Users\j\mcp-publisher.exe" login github && "C:\Users\j\mcp-publisher.exe" publish
#    ⚠ `login` alone is NOT a valid invocation -- the auth method is a required
#    argument, so it must be `login github`.
#    ⚠ Then VERIFY against the live API - the CLI checkmark is not proof.
#    ⚠⚠ EACH ROW IS {server:{...}, _meta:{...}} - name and version live UNDER
#    `server`, NOT at top level (schema 2025-12-11). A flat read of `.name`
#    returns ZERO rows on a perfectly good publish, which is a SECOND way to
#    reach the same wrong answer as the paging trap below, and it SURVIVES
#    &limit=100. Measured 2026-08-27 on a confirmed-good 1.108.301 publish.
#    Do NOT re-publish on a zero-row read; fix the parse first.
#      curl -s 'https://registry.modelcontextprotocol.io/v0/servers?search=<pkg>&limit=100' -o reg.json
#      python -c "import json;d=json.load(open('reg.json'));r=[x for x in d['servers'] if x['server']['name']=='io.github.jgravelle/<pkg>'];print(len(r),'rows');print([ (x['server']['version'], x['_meta']['io.modelcontextprotocol.registry/official'].get('publishedAt')) for x in r if x['_meta']['io.modelcontextprotocol.registry/official'].get('isLatest')])"
#    and confirm the new version carries isLatest: true AND that
#    server.packages[].version advanced too - an entry can move one, not both.
#    ⚠⚠ `&limit=100` IS LOAD-BEARING. The default response is a PAGE, not the
#    set. 2026-08-18: a bare `?search=` returned 28 entries with 1.108.284 still
#    marked isLatest and 1.108.285 ABSENT, minutes after a successful publish;
#    with the limit it returns 29 and isLatest is 1.108.285. **A truncated answer
#    read as a complete one** - so a missing version here is NOT evidence the
#    publish failed, and re-publishing on that reading is the wrong move.
# 8. ⚠⚠ READ THE CI RUN for the pushed SHA. Pushing is not checking.
GITHUB_TOKEN="" gh run list --repo jgravelle/<repo> --limit 3 \
  --json headSha,conclusion,displayTitle
```

⚠⚠ **Step 4 uses `uvx`, and the global `python -m twine` must NOT be restored.**
2026-08-12, jdoc 1.132.0, mid-release: `InvalidDistribution: '2.5' is not a valid
metadata version`. `python -m build` fetches an **always-latest hatchling** into
an isolated env, which now emits `Metadata-Version: 2.5`; the global twine 6.2.0
validates with `packaging` **24.2**, which tops out at 2.4. Build and upload were
reading two different toolchains and only one of them was moving.

⚠⚠ **The obvious fix — `pip install -U packaging` — is the wrong one.** That
interpreter is a kitchen sink, and three installed packages cap it:
`langchain-core` (`<25`), `streamlit` (`<25`), `inference-gpu` (`~=24.0`).
Upgrading breaks three working packages to satisfy a release tool. `uvx` resolves
twine and its `packaging` in a throwaway env and leaves the global alone, so it
also cannot rot when metadata 2.6 lands.

⚠ **`twine check` is the load-bearing half, not the upload.** The failure is
otherwise discovered *during* `upload`, i.e. after the wheel may already be on
PyPI and the sdist not — a half-published version that cannot be re-uploaded. The
check is a step 4 gate for the same reason step 8 exists.

⚠ **Not a jdoc quirk — it is ecosystem-wide.** jcm's already-built
`1.108.272-py3-none-any.whl` carries `Metadata-Version: 2.5` and fails global
`twine check` identically; jdata uses hatchling too, so it is next. ⚠ jragmunch
is the exception and the reason to run the check rather than reason about it: it
builds with **setuptools**, and its last wheel (0.4.8) is `Metadata-Version: 2.4`
— under the old ceiling, so it would have uploaded fine and taught you the wrong
lesson about which repos are affected.

⚠⚠ **Step 8 is not ceremony.** Lint was RED on jcm .259/.260/.261/.262 — four
consecutive releases published, tagged, announced and PyPI-uploaded on a failing
build, because the local suite was green and nobody looked at the check. The test
matrix (8 jobs) passed the whole time, so the failure was invisible from every
signal that was actually being read.

**Step 7 is the step that rots.** Both registry entries sat frozen at their
2026-03-21 publish for five months while PyPI went to 1.108.x, advertising
`1.8.6` to every downstream aggregator. The registry version is a liveness
signal to anyone comparing servers; a stale one reads as abandoned regardless
of what ships.

⚠⚠ **"jdatamunch was never published at all" WAS TRUE AND IS NOT — corrected
2026-08-25.** It has been in the registry since 2026-08-06 and now carries nine
versions. The stale line was quoted from this file four times in one session
before anyone ran the query, and it was quoted to justify calling a routine
refresh a "first publish". **A publication-state claim about any of the three
servers expires the moment someone publishes; it belongs in the API, never
here.** Same rule this repo already applies to open-issue counts and timebox
dates — run it, do not quote it:

⚠⚠ **Rows are NESTED: `{server: {...}, _meta: {...}}`.** `name` and `version`
sit under `server`; `isLatest` sits under `_meta`. A flat `.name` read returns
zero rows on a good publish — a false negative independent of the paging trap,
and one that survives `&limit=100`.

```bash
curl -s 'https://registry.modelcontextprotocol.io/v0/servers?search=<pkg>&limit=100' -o reg.json
python - reg.json <<'PY'
import json, sys
rows = json.load(open(sys.argv[1], encoding="utf-8"))["servers"]
ours = [r for r in rows if r["server"]["name"].endswith("/<pkg>")]
print(f"{len(ours)} rows")
for r in ours:
    off = r["_meta"]["io.modelcontextprotocol.registry/official"]
    if off.get("isLatest"):
        print("isLatest:", r["server"]["version"],
              "| packages:", [p.get("version") for p in r["server"].get("packages", [])])
PY
```

⚠⚠ **Why it rots is now measured, not guessed.** The registry JWT is issued with
a **five minute** lifetime (2026-08-08: `iat` 13:14:40, `exp` 13:19:40). Any flow
that logs in, hands off, and publishes later loses the window, and the failure
arrives as a bare `401 ... token is expired`, which reads as "the login did not
work" and sends you back to re-authenticate instead of re-sequencing. Run the two
as one command. If a publish 401s, check the token mtime before re-authenticating:
nothing newer than the last publish means the login ran in a different directory.

## PR workflow

⚠⚠ **A FORK PR SHOWING ONLY `license/cla` HAS NOT BEEN TESTED — IT HAS BEEN
SILENTLY HELD.** `gh pr checks` lists only checks that RAN, so a held run is
invisible from the place you would look. Diagnosed 2026-08-13, after #459 was
merged having never run the matrix once.
```bash
# Is a run being held for approval? (conclusion=action_required, status=completed)
GITHUB_TOKEN="" gh api "repos/jgravelle/<repo>/actions/runs?status=action_required&per_page=30" \
  --jq '.workflow_runs[] | "\(.id)|\(.name)|\(.head_branch)|\(.head_sha[0:7])"'
GITHUB_TOKEN="" gh api --method POST "repos/jgravelle/<repo>/actions/runs/<id>/approve"
# Does a merge ref exist? NO MERGE REF = NO pull_request RUN, EVER.
git ls-remote origin "refs/pull/<n>/merge"
```
⚠ **Two independent causes that present identically**, and the second is not a
settings problem: (1) `actions/permissions/fork-pr-contributor-approval` was
`first_time_contributors`, so a first-time fork contributor's runs were created
with `conclusion=action_required` and never executed — relaxed to
`first_time_contributors_new_to_github` on 2026-08-13; (2) **a CONFLICTING PR has
no `refs/pull/N/merge`**, and `pull_request` workflows run against the MERGE ref,
so a conflicting fork PR gets no run at all regardless of policy. It must be
rebased first. #451 was cause 2; #443 was cause 1.
⚠ **`gh pr close` + `gh pr reopen` does NOT re-provoke the run** — measured on
both PRs, no run appeared. The reliable re-trigger is a CONTRIBUTOR PUSH
(`synchronize`). Do not assume a policy fix worked until a real push proves it.

### ⚠⚠ OUR OWN PUSH TO A FORK BRANCH ERASES THE `license/cla` STATUS

Measured twice on #443. Pushing a conflict resolution moves the head, and
**commit STATUSES (legacy API) do not follow a new SHA the way Actions CHECK RUNS
do.** The matrix reappears, `license/cla` does not, and the PR can read
`mergeStateStatus: CLEAN` **with the CLA unsigned** — the blocker is not passing,
it is absent. `gh pr checks` shows only checks that RAN, so it looks clean there
too.
```bash
# The only reliable read. count=0 means NOT SIGNED, never "cleared".
GITHUB_TOKEN="" gh api "repos/jgravelle/<repo>/commits/<head-sha>/status" \
  --jq '"state=\(.state) count=\(.statuses|length)"'
```
⚠⚠ **A MISSING CLA check counts as NOT SIGNED.** Never merge on its absence.
⚠ **Close + immediately reopen is NOT a dependable remedy — corrected
2026-08-15.** It restored the status once (head `6aec667`) and **failed to
restore it on the very next occurrence** (head `d4760a1`), so the earlier note
claiming it works was written from a single success. There is no maintainer-side
fix: the status returns when the CONTRIBUTOR acts (signing posts a fresh status
against the current head). Say so on the thread so the contributor is not left
reading eleven green checks as done.
⚠⚠ **CLA Assistant can fail to fire on PR OPEN, and then there is no status to
erase — measured 2026-08-15 on #479.** Zero statuses on the head SHA and no bot
comment, on a repo where the same app posted both on #473 and #443. **It reads
identically to "we pushed and wiped it" and has the opposite cause.** Check the
comments as well as the statuses before concluding anything about a signature:
```bash
GITHUB_TOKEN="" gh api repos/jgravelle/<repo>/issues/<n>/comments --jq '.[].user.login'
```
⚠ **OUR OWN push woke it.** Pushing a fix onto their branch is a `synchronize`,
and the badge plus a `pending license/cla` appeared within seconds. So the same
push that ERASES an existing status is what PROVOKES a missing one — the two
notes are not in conflict, they are about statuses that exist and statuses that
never did. Either way the verdict is unchanged: **absent means NOT SIGNED**.
⚠⚠ **Until a fork PR shows a green matrix, a local TRIAL MERGE onto current main
is the substitute, not the branch's own result** — branch-green is not
merged-green. Merge base drifts every release.

### An ORG-OWNED fork cannot be pushed to, and the PR says otherwise

⚠⚠ **`maintainerCanModify: true` is DISPLAYED AND WRONG when the fork belongs to
an organization.** GitHub's "Allow edits by maintainers" only grants push access
for forks under a personal account. Against an org fork the push returns
**403 `Permission to <Org>/<repo>.git denied`** with FULL `repo` scope — it is not
a token problem and there is no flag to flip. Measured 2026-08-13 on #451
(`Nexusmill`, an Organization; the PR reported `maintainerCanModify: true`).
```bash
GITHUB_TOKEN="" gh pr view <n> --json headRepositoryOwner --jq .headRepositoryOwner.login
GITHUB_TOKEN="" gh api users/<that-login> --jq .type   # "Organization" => cannot push, do not try
```
⚠ **CHECK THIS BEFORE PROMISING A CONTRIBUTOR YOU WILL FIX THEIR BRANCH.** The
whole point of resolving it yourself is to stop sending them back; discovering
mid-way that you cannot is worse than never offering.

**When we cannot push and the conflict is OURS, land it from our side:**
```bash
GITHUB_TOKEN="" gh pr checkout <n> --force        # their head, authorship intact
git merge origin/main                             # resolve; verify; run the suite
GITHUB_TOKEN="" git push origin HEAD:refs/heads/contrib/<n>-merged
GITHUB_TOKEN="" gh pr create --base main --head contrib/<n>-merged   # CI gates the real merge
```
⚠ Their head commit becomes an ancestor of `main`, so GitHub flips their PR to
**MERGED, not closed** — the contribution stays on their record. Verify it:
`git merge-base --is-ancestor <their-head> origin/main`.
⚠ **Say in the PR comment that the conflict was ours and why the normal route was
unavailable.** A contributor whose branch is bypassed with no explanation
reasonably reads it as their work being taken over.
⚠⚠ **A MERGEABLE CONTRIBUTOR PR MERGES FIRST. CHECK BEFORE EVERY MERGE OF OURS
THAT TOUCHES `CHANGELOG.md`** (jjg, 2026-08-14), including a release commit:

```bash
GITHUB_TOKEN="" gh pr list --state open --json number,author,mergeable,mergeStateStatus \
  --jq '.[] | select(.author.login != "jgravelle") | "#\(.number) \(.author.login) \(.mergeable) \(.mergeStateStatus)"'
```

Any row reading `MERGEABLE CLEAN` goes in before ours. The reason is mechanical:
our `[Unreleased]` edits land in the same block a contributor's entry occupies,
so each of our merges conflicts their branch, and **a conflicting fork PR has no
`refs/pull/N/merge` and therefore gets NO CI AT ALL** — their branch goes dark
for a reason unrelated to their change.

⚠⚠ **Measured 2026-08-14: #443 conflicted FIVE TIMES IN ONE DAY** (two PR merges,
two releases, one docs change), every one resolved by us pushing to their fork.
**That is one wrong merge order repeated, not five incidents.**

⚠ **The boundary, or the rule fails on its first real case.** A BLOCKED PR
cannot go first — #443 was unsigned-CLA throughout, so "contributor first" was
never available. Blocked means we ship anyway (a release is never blocked on an
open issue) and **we own the resolution**: push the merge to their branch,
resolve it, and say on the thread that the conflict was ours. **The rule governs
ORDER when we have a choice; it never holds our work behind someone else's
form.**

- Review diff carefully before approving
- Approve: `GITHUB_TOKEN="" gh pr review <n> --repo jgravelle/<repo> --approve --body "..."`
- Merge: `GITHUB_TOKEN="" gh pr merge <n> --repo jgravelle/<repo> --merge`
- Pull after merge: `GITHUB_TOKEN="" git pull origin main`
- Close without merging: `GITHUB_TOKEN="" gh pr close <n> --repo jgravelle/<repo> --comment "..."`

## Community responses
- Invite paying customers to: https://jcodemunch.com/#pricing
- Benchmarks live in `jcodemunch-mcp/benchmarks/` (Express, FastAPI, Gin — see results.md)
