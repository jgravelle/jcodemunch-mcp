# CI/CD AUDIT — what exists today (read-only, 2026-09-03)

Branch `cicd/audit`. Read at `a639909` (main). Every figure here was read from
the workflow files, `gh` (API and run history), PyPI's JSON API, git tags, and
the machine-local release skill. Where something could not be read, it says so.
Run counts and dates expire; the commands that produced them are in §8.

## 1. Workflows under `.github/workflows/`

| Workflow | Trigger | Jobs | Gates on | Runtime (recent) | Last success / last failure | Exercised? |
|---|---|---|---|---|---|---|
| `test.yml` "Tests" | push main, PR to main, dispatch | `test` matrix 2 OS x 4 Python (3.10-3.13), `lint` | pytest with `--cov-fail-under` and a skip ceiling read from `harness threshold`; sdist sensitive-path scan (ubuntu 3.12 only); `ruff check src/` | 10-14 min wall per run (the matrix leg is the long pole, `timeout-minutes: 20`) | success 2026-09-03 22:50; failure 2026-09-03 20:34 (`tomllib` on 3.10, fixed) | Yes, every push and PR. **Required** on main (all 9 jobs) |
| `harness.yml` "Harness" | push main, PR, Mondays 06:30Z, dispatch | `fast` (fast tier), `bench` (bench tier offline, `contents: write`) | `python -m harness fast` / `bench --offline`: every Floor in `harness/thresholds.json` | fast 1-2 min, bench 1-2 min | success 22:50; failure 22:35 (F-19 latency flake on the runner) | Yes since 2026-09-03 (new). `Harness fast tier` is required; bench is not |
| `replay.yml` "Retrieval-quality gate" | push main, PR | one job: index self into a temp store, `run_replay.py --gate $(harness threshold replay.max_relative_drop)` | replay MRR/nDCG vs golden | under 1 min | success 22:50; last failure 2026-08-22 | Yes. **Required** |
| `benchmark.yml` "Token-efficiency benchmark" | Mondays 07:00Z, dispatch (input `reference`), push touching `tasks.json`/`run_benchmark.py`/itself | one job: clone 3 pinned corpora, index, `run_benchmark.py --verify-determinism --floor`, compare to committed reference, upload | `token.grand_ratio_vs_grep`, `token.per_repo_rise_max` (via `--floor`); downward drift is a warning only | 1 min (measured 0-1 min) | success 22:35; last failure 2026-08-06 | Yes. ⚠ **Also fires on every tag push** (`push` has `paths` but no `branches`, so `refs/tags/*` matches): ran on v1.108.309-.316. Harmless, but it is a run nobody asked for |
| `health-radar.yml` "Health Radar" | PR opened/synchronize/reopened | runs the local composite action `.github/actions/health-radar` (base vs head radar) | Nothing: informational, writes an artifact | 0-1 min | success 2026-09-02; last failure 2026-06-05 | Yes on PRs. Not required |
| `health-radar-comment.yml` | `workflow_run` of Health Radar | posts/updates a PR comment from the artifact | Nothing. Has `continue-on-error: true` on the comment step (informational) | 0 min | success 2026-09-02 | Yes |
| `sign-release.yml` "Sign release artifacts" | `release: published`, dispatch (tag) | download release assets, sigstore-sign, upload `.sigstore.json` bundles (`id-token: write`, `contents: write`) | Nothing gates on it; it is forward-only coverage | under 1 min | success on every release since 2026-08-03; failures 05-22, 05-23, 06-09, 08-03 | Yes, every release |
| `handshake.yml` "Handshake" | `release: published`, dispatch (version) | ubuntu + windows: poll PyPI, fresh venv, `pip install jcodemunch-mcp==X`, real stdio `initialize` via `scripts/handshake.py` | `serverInfo.version == X`, non-empty `instructions`, non-empty tool list | 1 min per OS | success 2026-09-03 (dispatch on 1.108.316); no release has fired it yet | New today. Nothing consumes a failure except a red run |

Local composite action: `.github/actions/health-radar` (published to external
consumers under `health-radar-v1` tags; editing it creates a tagging
obligation documented in its header).

**Third-party actions and pinning.** Every `uses:` is SHA-pinned with a
version comment: `actions/checkout` (v5 SHA `93cb6efe`; `sign-release.yml`
still pins the v4 SHA `34e11487`), `actions/setup-python` (v5),
`actions/upload-artifact` / `download-artifact` (v4), `astral-sh/setup-uv`
(v7, uv pinned to 0.9.5 with a documented reason: an older uv silently
re-resolved a `revision = 3` lock), `sigstore/gh-action-sigstore-python`
(v3.3.0). Dependabot covers `github-actions` for security alerts only.

**Permissions.** Every workflow declares top-level `contents: read` except
`test.yml` (no `permissions:` block at all, so it inherits the repo default;
the default for this repo could not be read from the API and is assumed
read-only for new repos, unverified), `health-radar*.yml`
(`pull-requests: write`), `harness.yml` bench job (`contents: write` for the
weekly commit), `sign-release.yml` (`id-token: write`, `contents: write`).

## 2. The release process as actually performed

Reconstructed from 409 `release: vX.Y.Z` commits, 645 tags, 607 PyPI
versions, the GitHub release list, and the machine-local checklist at
`.claude/skills/release/SKILL.md` (gitignored; the durable copy of its rules
is CLAUDE.md "Issue + release policy" and "Reproducing CI's environment").

Cadence: 1.108.305 through 1.108.316 shipped between 2026-08-28 and
2026-09-03, i.e. **roughly two releases a day**. Every step below is typed by
the maintainer on this Windows box; nothing in `.github/` cuts a release.

1. **Bump five pin sites by hand**: `pyproject.toml`, `server.json` (two
   fields), `plugin/jcodemunch/.claude-plugin/plugin.json`, `uv.lock` (the name-scoped
   `version =` line only, never `uv lock`), `whatsnew.json` (`current` plus a
   new `entries[]` record). `mcpb/manifest.json` is generated and gitignored.
   Guarded by `tests/test_lockfile_version_sync.py`,
   `test_plugin_manifest_sync.py`, `test_server_json_sync.py`,
   `test_whatsnew.py`; `scripts/release_preflight.py` (2026-09-03) reads all
   seven values and refuses if they disagree.
2. Rotate CLAUDE.md Current State (gated by `test_claude_md_rotation.py`).
3. Run the touched tests, `ruff check src/`, then the full suite, then the
   CI-environment reproduce (`uv sync --locked --group dev --extra watch
   --python 3.13` + `uv run --python 3.13 pytest`).
4. Commit `release: vX.Y.Z - <title>`, **push, read CI** (policy: read CI
   before anything irreversible; four releases once shipped on red because
   this was skipped). `scripts/release_preflight.py` now encodes this as a
   check over the required contexts on HEAD.
5. Build and publish **from this box**: `uvx --from build pyproject-build`,
   `uvx --from twine twine check dist/*X.Y.Z*`, `uvx --from twine twine
   upload dist/*X.Y.Z*`.
6. `git tag vX.Y.Z && git push origin vX.Y.Z`.
7. `gh release create vX.Y.Z dist/*X.Y.Z* --title ... --notes ...` (notes
   written by hand, outside the repo, after a scratch `relnotes.md` once
   shipped inside an sdist). This fires `sign-release.yml` and, as of today,
   `handshake.yml`.
8. **MCP registry publish**, by hand, from a cmd.exe prompt:
   `"C:\Users\j\mcp-publisher.exe" login github && ... publish`. Documented
   as "the step that rots". Verification reads the registry's nested row
   shape (CLAUDE.md "Registry verification reads a NESTED row").
9. Reinstall and restart the local server (Practice 11).

**Version scheme.** `MAJOR.MINOR.PATCH` where MINOR has been **108 since
2026-05-12** (`1.108.0`, "explicit-paths indexing"; `1.107.x` lasted one day,
`1.106.0` the same day). Before that MINOR moved per feature. Since 1.108.0
every release, including features, bumps only PATCH, so the third component
behaves as a **monotonic build number typed by hand**: nothing derives it from
git, from a date, or from CI. There is no bump script; the checklist's
enumeration is `grep -rn "<old-version>" --include=*.json --include=*.toml
--include=*.lock .`. `whatsnew.json` is the only place the version is paired
with prose, and `src/jcodemunch_mcp/cli/whatsnew.py` reads it at runtime.

**Tags.** 645 tags: `vX.Y.Z` for releases plus `health-radar-v1`,
`health-radar-v1.0.0`, `health-radar-v1.0.1` for the composite action.
No tag is signed. No tag is created by automation (jdatamunch's
auto-release-on-push is the exception in the suite and is called out in
the skill as a trap for this repo).

**PyPI.** 607 versions, **0 yanked**. Upload times match the release
commit times to the minute, i.e. the upload happens from the box right
after the push. Versions 0.2.0-0.2.5 were yanked in the past for the
`.claude/settings.local.json` credential leak (CLAUDE.md, MEMORY.md); PyPI
quarantine and an account block followed in 2026-06 for an undisclosed
persistent service, lifted 2026-06-10, with the standing rule that every
background/network behaviour is README-disclosed before shipping.

## 3. Publish mechanism

- **Token-based, from the maintainer's machine.** `~/.pypirc` exists on this
  box with a single `[pypi]` section (3 lines; contents not read). Upload is
  `uvx --from twine twine upload`. No workflow uploads to PyPI.
- **No trusted publishing.** No workflow has a `pypi` environment or calls
  `pypa/gh-action-pypi-publish`; the repo has two environments,
  `copilot` and `github-pages`, neither release-related. Whether a trusted
  publisher is configured on the PyPI project side cannot be read from
  here (PyPI exposes it only to project owners in the web UI); the presence
  of the token file and the absence of any publishing workflow make
  token-based the operative mechanism.
- **No Test PyPI step** anywhere: not in the skill, not in a workflow, not in
  the release commits.
- **Repository secrets: none.** `gh secret list` returns nothing. Every
  workflow that needs credentials uses the ambient `GITHUB_TOKEN` or OIDC
  (`sign-release.yml`).
- **Signing** happens after the fact: sigstore bundles are attached to the
  GitHub release by `sign-release.yml`; PyPI receives no attestation.
- **MCP registry** publish uses a local `mcp-publisher.exe` binary with a
  GitHub login; not automated, not in CI.

## 4. Branch protection and required checks (read via `gh api`, 2026-09-03)

`main`:
- `required_status_checks.contexts` (12): `license/cla`, `lint`,
  `Retrieval-quality gate`, `Harness fast tier`, `test (ubuntu-latest,
  3.10|3.11|3.12|3.13)`, `test (windows-latest, 3.10|3.11|3.12|3.13)`.
  `strict: false` (branches need not be up to date). Set today; before
  2026-09-03 only `license/cla` was required.
- `enforce_admins: false` — **the owner can push to main directly and merge
  a red PR**. Deliberate per CLAUDE.md policy 3d (needed to land merges on
  contributor forks); it is also exactly what principle 5 of this brief
  wants closed.
- `required_pull_request_reviews`: **none**. No review is required; a PR with
  green checks can be merged by anyone with write (in practice only the
  owner has write).
- `allow_force_pushes: false`, `allow_deletions: false`,
  `required_linear_history: false`, `required_conversation_resolution: false`.
- Repo settings: `allow_auto_merge: false`, `delete_branch_on_merge: false`,
  squash/merge/rebase all allowed.
- ⚠ `license/cla` is a **legacy commit status** posted by the cla-assistant
  webhook to PR heads; a main commit never carries it. Any pipeline that reads
  "all required checks green on HEAD" must exclude it (the pre-flight does).

Contributor PRs today: `Tests`, `Retrieval-quality gate`, `Harness fast tier`
and `Health Radar` run on `pull_request`; the two release workflows and the
benchmark do not. The observed merge path is: contributor PR, CLA status,
green checks, owner merges from the CLI (`gh pr merge`), often after a local
trial merge (CLAUDE.md "A CONTRIBUTOR PR IS TRIAL-MERGED ONTO main").

## 5. Security automation

| Item | State |
|---|---|
| Dependabot | `.github/dependabot.yml`: `uv` and `github-actions` ecosystems, weekly, **security updates only** (`open-pull-requests-limit: 0` suppresses version updates), grouped into one PR per ecosystem. Repo setting `dependabot_security_updates: enabled`. One such PR seen 2026-09-01 (`dependabot/uv/security-updates-...`). |
| CodeQL | **Not configured** (`code-scanning/default-setup` → `state: not-configured`; languages detected: actions, go, javascript, python, rust, typescript). No `codeql.yml`. |
| Secret scanning | Enabled with **push protection enabled**; validity checks and non-provider patterns disabled. |
| Dependency vulnerability scan in CI | **None.** No `pip-audit`, `uv audit`, or equivalent step. ENFORCEMENT-PLAN item 8. |
| SBOM / attestations | Sigstore bundles on GitHub release assets only. No PyPI attestations (PEP 740) — those require trusted publishing. |
| SECURITY.md | Present, 360+ lines, describes path traversal, symlink, secret exclusion, redaction, limits, background behaviour, artifact signing. **It has no vulnerability-reporting contact or disclosure policy section**: no email, no "Report a vulnerability" heading, no reference to GitHub private advisories. |
| sdist scan | `test.yml` builds the sdist on ubuntu/3.12 and greps it for sensitive paths (the v0.2.6 credential-leak guard), plus `tests/test_sdist_exclusions.py` and `tests/test_build.py`. |

## 6. Secrets referenced by workflows

None beyond `${{ secrets.GITHUB_TOKEN }}` / `github.token`. `sign-release.yml`
uses OIDC (`id-token: write`). The PyPI token lives only in `~/.pypirc` on the
maintainer's machine. The MCP registry credential is a GitHub login held by
`mcp-publisher.exe` locally.

## 7. Silently broken, always-green, or fragile

1. **The weekly harness commit cannot push.** `harness.yml`'s bench job
   commits `harness/results/latest.json` on `schedule` and runs `git push`
   to `main`. As of today `main` requires 12 status checks; a push from the
   Actions token does not carry them and is not an admin bypass, so the
   first Monday run (2026-09-08 06:30Z) will fail at the push step. Nothing
   has exercised this path yet (no schedule run has happened since the
   workflow was added).
2. **`benchmark.yml` runs on every tag push** (see §1). Eight extra runs in
   six days; each clones three corpora. Not wrong, just unasked-for.
3. **`continue-on-error: true`** appears once, on the PR-comment step of
   `health-radar-comment.yml`. That step is informational (a comment), so the
   flag is appropriate; the job name does not say so.
4. **`test.yml` has no `permissions:` block.** It inherits whatever the repo
   default is, which could not be read. Every other workflow is explicit.
5. **Latency Floors are one-box Floors.** `latency.*_warm_p95_ms` were set
   at 2x the median of three runs on this machine; the bench job failed once
   today at 72.7 ms against a 27 ms floor and passed on the next run (harness
   F-19). Until CI has three runs of its own the bench tier will flake on
   runner noise; it is not a required check, so it cannot block a merge, but
   it will paint main red on Mondays.
6. **The benchmark's published number embeds the measuring box's HOME
   ledger** (harness F-17): `_meta.total_tokens_saved` is read from
   `~/.code-index/_savings.json`, so a local `--reference` run is never
   byte-identical to CI's. The reference is captured on CI now; the
   underlying basis question is open.
7. **`Health Radar` ran 12 times on PRs in the last two weeks and gates
   nothing.** Its verdict is a comment. That is by design (it grades, it
   does not block), but the brief's principle 2 wants every job that posts a
   verdict to say whether it can block.
8. **No workflow reads `CHANGELOG.md`.** A release with no changelog entry is
   caught only by `scripts/release_preflight.py`, run by hand.
9. **No workflow verifies the version string** on a tag or release. The
   `serverInfo.version == tag` check exists (handshake) but runs after
   publish, against PyPI; nothing checks the five pin sites against the tag
   before upload except the local pre-flight.
10. **The MCP registry step is not observable from CI at all.** It succeeds
    or rots on the maintainer's box; the only guard is a manual nested-row
    read documented in CLAUDE.md.
11. **`sign-release.yml` pins `actions/checkout` at a v4 SHA** while every
    other workflow pins v5. Not broken; inconsistent.
12. **`Tests` failed 5-8 times a day in early August** (08-02 x5, 08-03 x8,
    08-07 x6) and 0-2 times a day since 08-13. The August spike coincides
    with the red-build releases .259-.262 the checklist warns about. Today's
    two failures were both mine (F-14 on 4109cbf, `tomllib` on 5daf6fe).

## 8. Facts with an expiry date — recompute, never quote

```bash
GITHUB_TOKEN="" gh run list -R jgravelle/jcodemunch-mcp --workflow <file> --limit 12 --json conclusion,createdAt,updatedAt,event
GITHUB_TOKEN="" gh api repos/jgravelle/jcodemunch-mcp/branches/main/protection
GITHUB_TOKEN="" gh api repos/jgravelle/jcodemunch-mcp/code-scanning/default-setup
GITHUB_TOKEN="" gh secret list -R jgravelle/jcodemunch-mcp
GITHUB_TOKEN="" gh api repos/jgravelle/jcodemunch-mcp/environments --jq '.environments[].name'
curl -fsS https://pypi.org/pypi/jcodemunch-mcp/json | python -c "import json,sys;d=json.load(sys.stdin);print(len(d['releases']))"
git tag | wc -l ; git log --format=%s | grep -cE '^release: v'
```

## 9. What this audit could not read

- The repository's default `GITHUB_TOKEN` permission setting (Settings →
  Actions → Workflow permissions). Not exposed by the endpoints tried.
- Whether a PyPI trusted publisher is registered for the project (PyPI web
  UI only).
- The content of `~/.pypirc` (deliberately not read; its existence and
  section name are enough for the finding).
- The Actions minutes budget consumed per month (billing endpoint needs an
  org/user scope this token does not have). Runtimes above are per-run wall
  clocks from the run list.
