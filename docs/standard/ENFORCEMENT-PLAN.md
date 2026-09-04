# ENFORCEMENT PLAN — moving each criterion to MEASURED and gated

**Status 2026-09-04 (workflows layer, `docs/workflows/`):** the process side is a set of Claude Code commands, hooks and a reviewer subagent that invoke items 1-4, 7, 8, 10, 17 at the right moments (`/feature`, `/fix-issue`, `/release`, `/benchmark-compare`, `/review`, `/triage-issue`; `docs/workflows/DESIGN.md`). No item below changes state by it; item 13 (coverage as an artifact) and W-1/W-2/W-3 in `docs/workflows/FINDINGS.md` are what the workflows still lack from the harness.

**Status 2026-09-03 (branch `harness/source-of-truth`):** items 1, 2, 4, 6-partial, 7, 12-partial, 13-partial, 16, 17, 18 are DONE by the harness build; the table below keeps the original order and marks each.

| # | Done? |
|---|---|
| 1 | DONE: `run_benchmark.py --floor`, `benchmark.yml` fails on an upward move; `tests/test_token_benchmark_floor.py` |
| 2 | DONE: `benchmarks/self_latency/measure.py`, six thresholds at 2x the median of three runs, bench tier |
| 3 | DONE 2026-09-04 (`cicd/*` series): required checks are the PR gate's job names, `enforce_admins` and `strict` on, `scripts/release_preflight.py` reads the list; earlier: required status checks on `main` set 2026-09-03 (12 contexts: `license/cla`, `lint`, `Retrieval-quality gate`, `Harness fast tier`, the 8 `test (os, py)` legs; `strict` and `enforce_admins` stay false). The bench tier is NOT required: it runs on push to main only and would block every PR. `scripts/release_preflight.py` written 2026-09-03: reads the required contexts and HEAD's check-runs, and refuses on a missing or unfinished run, a lagging pin site, an existing tag or PyPI version, or a MERGEABLE CLEAN contributor PR; `tests/test_release_preflight.py` covers each refusal |
| 4 | DONE: `tests/test_standard_invariants.py` (+ `tests/test_retirement_ledger.py`, `tests/test_thresholds_are_the_only_copy.py`) |
| 5 | DONE 2026-09-03, folded into `release.yml` post-publish 2026-09-04: `.github/workflows/handshake.yml` (on `release: published` or dispatch with a version; ubuntu + windows; polls PyPI up to 10 min; fresh `uv venv` + `uv pip install jcodemunch-mcp==X`) runs `scripts/handshake.py`, a real stdio `initialize` that fails unless `serverInfo.version == X`, `instructions` is non-empty and `list_tools` is non-empty. Verified locally against the published 1.108.316 (PASS) and against a wrong expectation (FAIL) |
| 6 | PARTIAL: `fast: format` gates `harness/` and `scripts/` (docs/cicd/FINDINGS.md C-1: `src/` is 254 files unformatted, jjg's call); `ruff check tests/` still 292; not touched (tests are the fossil record and a mass auto-fix would rewrite 274 files in one commit) |
| 7 | DONE: `tests/conftest.py::_no_network` |
| 8 | DONE 2026-09-04: `fast: dependency audit` = `harness check deps.vuln_max` (pip-audit over the runtime set, Floor 0, dated allowlist); nightly too |
| 9 | NOT DONE: CLIENTS.md block parser |
| 10 | DONE 2026-09-04: `fast: types` = `harness check types.error_max` (pyright ratchet, 369 at first measurement, may only fall) |
| 11 | NOT DONE: fidelity oracles for Python/TypeScript/Go |
| 12 | PARTIAL: `tests/test_security_md_limits_parity.py` (strict xfail on the 500-files row, FINDINGS F-01); threat model page not written |
| 13 | PARTIAL: the full tier prints coverage as a threshold verdict on every leg's summary; `coverage.json` is still not uploaded as an artifact |
| 14 | NOT DONE: larger replay set |
| 15 | PARTIAL: `cache_stability` is EXCLUDED from every tier with the reason (FINDINGS F-06); corpus not pinned |
| 16 | DONE: `tests/test_config_docs_reverse_parity.py` (12 keys in INTERNAL_KEYS, FINDINGS F-03) |
| 17 | DONE: skip ceiling in the full tier (`pr-gate.yml` since 2026-09-04; `test.yml` before) |
| 18 | DONE: `timeout-minutes: 20` on the test job |


Written 2026-09-03 at `63a621d`. Nothing here is implemented. Ordered by
leverage: the items that let the most future work run unattended come first.
Sizes: S = under half a day, M = one to two days, L = more than two days.
Criterion numbers refer to `STANDARD.md`.

| # | Build | Size | Unblocks | Prerequisites | Why it is here in the order |
|---|---|---|---|---|---|
| 1 | **Make the weekly token benchmark FAIL on an upward move.** Add a `--floor` mode to `benchmarks/harness/run_benchmark.py` (or a post-step in `benchmark.yml`) that exits non-zero when any repo's `jmunch_total_tokens` exceeds the committed value by more than 10% or the grand ratio vs grep-top-3 drops below 20x; keep the downward case a warning that names the re-sync command. | S | Criterion 2 -> gated; Regression rule 6 | none; the workflow and artifact already exist and the CI run is 39 s | The headline number is the one claim every public surface repeats, and today it can drift for weeks with only a warning nobody reads (it has been warning since 2026-08-31). Smallest change with the largest reputational cover. |
| 2 | **A latency + incremental-cost harness on the self corpus.** `benchmarks/self_latency/measure.py`: cold `index_folder` of `src/`, one-file edit + `index_file`, then cold and warm p50/p95 for the six core tools over N=20 calls; writes `results.json` with box metadata. Run in `test.yml` on one ubuntu leg with a floor of 2x the committed p95 (runner noise is UNMEASURED, so start loose and tighten on evidence). | M | Criteria 3(b)(c) and 5 -> MEASURED; the "Not yet enforceable" latency row | none | Two ranked axes have no artifact at all; the first committed run also explains the cold `search_text` 8.8 s vs `search_symbols` 2.6 s gap found in discovery. Every later perf fix gets a before/after for free. |
| 3 | **Required status checks on `main`: `Tests` (all 8 legs), `lint`, `replay`.** Plus a release pre-flight script that reads the CI conclusion for `HEAD` and refuses to bump when it is not `success`. | S | Criterion 7 target; the "four releases on a red build" class | none (admin setting + one script; `enforce_admins` stays false so fork merges still work) | The test matrix exists and is green 9 of 10 runs, yet it gates nothing. This turns the largest existing investment into an actual gate. |
| 4 | **Honesty-invariant enumeration ratchet.** `tests/test_standard_invariants.py` listing by name every test file the standard's Method columns cite (criteria 1, 3, 4, 7, 8, 9) and failing if one is missing or collects zero tests. | S | Criterion 9 target; makes every other criterion's Method column self-checking | none | A deleted or renamed pin test is invisible today. This is the ratchet that keeps the standard's own citations from rotting. |
| 5 | **Post-publish handshake job.** On `release: published`, install `jcodemunch-mcp==<tag>` from PyPI into a fresh venv on ubuntu and windows, run a real stdio `initialize`, assert `serverInfo.version == tag` and `instructions` non-empty. | M | Criterion 6(a) -> MEASURED; closes the #536 class | item 3 is not required but pairs with it; needs PyPI propagation wait (poll up to 10 min) | The only step of the release that is still manual and machine-local (the skill file is gitignored). It ran once by hand and never since. |
| 6 | **Lint `tests/`.** One `ruff check tests/ --fix` commit (282 auto-fixable), fix the remaining 10 by hand, add `tests/` to the `lint` job. | S | N3 tests half; Regression rule 8 widens | none | Cheap, and it removes 292 findings that make the lint output unreadable for the errors that matter. |
| 7 | **Network-blocking test fixture.** Session-scoped autouse fixture that patches `socket.socket.connect` to raise on non-loopback addresses, with a `@pytest.mark.network` opt-out (zero users today). | S | N5 -> MEASURED by instrument | none | Turns an inspection into a guarantee; a future contributor's test that reaches PyPI or GitHub fails locally instead of flaking in CI. |
| 8 | **Dependency audit step.** `uv run pip-audit` (or `uvx pip-audit -r` over the exported lock) in `test.yml`, with an allowlist file for accepted advisories. | S | Criterion 8 target | none | Dependabot is security-only and PR-limit 0; nothing today tells us a shipped dependency has a CVE until someone files an issue. |
| 9 | **Client-config validation test.** Parse every fenced JSON/TOML block in `CLIENTS.md`, assert it parses and names the `jcodemunch-mcp` command or `uvx` form. | S | Criterion 6(c) | none | 13 client configs, none machine-checked; an install-friction issue is the second-largest theme. |
| 10 | **Type-check baseline.** Run pyright (or mypy) on `src/` once, commit the error count as a ratchet that may only decrease. | M | N3 types half | none | Unknown error count today; the ratchet form means it never blocks a release while still preventing growth. |
| 11 | **Fidelity oracles for Python, TypeScript, Go.** Clone the Rust harness shape (`benchmarks/rust_fidelity/`): language-native parser as oracle (`ast`, TypeScript compiler API, `go/parser`), four gated buckets, frozen fixtures for CI. One language per item. | L each | Criterion 1 target; criterion 10 target | item 4 so the new pins are enumerated | The top-ranked axis has oracles for two of 79 languages, neither in the top five by usage. The Rust harness documents four oracle traps that each new one will hit; the template exists. |
| 12 | **SECURITY.md limits parity test + threat-model page.** A test reading the limits table against `security.DEFAULT_*`; a one-page threat model (what is trusted, what is not, what a malicious repo can and cannot do to the indexing host). | S + M | Criterion 8 target; fixes the "500 files" discrepancy | none | Parity is one test; the threat model is the document a security reviewer asks for first and it does not exist. |
| 13 | **Coverage number as an artifact.** Upload `coverage.json` from the ubuntu 3.12 leg and record `totals.percent_covered` in `whatsnew.json` per release. | S | N2 value -> known; lets the 74% floor be raised on evidence | none | The floor has been 74 since v1.108.76 and nobody knows the actual figure. |
| 14 | **Larger replay golden set.** 100 queries across this repo plus two pinned external corpora, regenerated with the existing `run_replay.py`. | M | Criterion 1(b) target | item 1's corpora are already pinned in `tasks.json` | 10 queries on one repo all scoring 1.0 is a ceiling, not a measurement. |
| 15 | **`cache_stability` corpus decision.** Either pin the measured corpus to a fixed snapshot committed under `benchmarks/cache_stability/fixtures/` or label the artifact non-deterministic in its README and stop quoting the `hold` verdict. | S | N4 | none | The one benchmark artifact that moved on re-run and is pinned by nothing. |
| 16 | **Reverse config-parity test.** Every `DEFAULTS` key appears in `CONFIGURATION.md` or `CLI-AND-ENV.md` or an `INTERNAL_KEYS` allowlist in the test. | S | Criterion 6(b) | none | 16 keys are documented nowhere; the current parity test cannot see them by construction. |
| 17 | **Skip-count assertion in CI.** Parse the pytest summary line in `test.yml` and fail when `skipped` exceeds 30 (ubuntu) or 25 (windows). | S | N7 | none | The 2026-08-28 incident was 105 skips at exit 0; the same shape in CI would be invisible today. |
| 18 | **`timeout-minutes: 20` on the test job.** | S | N1 floor enforced | none | Turns the observed ceiling into an enforced one; a hung suite costs a runner-hour today. |

## What the first three buy

Items 1-3 together take the two most-quoted numbers (token ratio, CI green)
and the one most-expensive release failure class (shipping on red) from
"observed" to "cannot merge or release otherwise", for roughly one day of
work and no new harness. Item 2 is the first new instrument and it is the one
that makes two ranked axes measurable at all.

## Explicitly out of scope

- Competitor head-to-head benchmarking: gated on a VM by `ROADMAP.md:308` and
  not reopened here.
- SWE-bench: parked by decision (`benchmarks/swebench/PROTOCOL.md`), 120 GB.
- Raising `CLAUDE.md`'s 140,000 budget: the gate says its buffer is the last one.
