# STANDARD — what "best in its niche" means for jCodeMunch-MCP

Authority: this file. Written 2026-09-03 at commit `63a621d` (v1.108.316) from
`DISCOVERY.md` (measurements) and `NICHE.md` (axes and ranking). Every number
here was recomputed in that session or read from a committed artifact named
beside it. **Nothing in this file is to be copied forward by hand**: a future
edit re-runs the Method column and replaces the Current line with the new value,
date and commit.

**Enforcement (2026-09-03, branch `harness/source-of-truth`):** `python -m harness` is the single command that checks every Floor below; the Floors themselves live ONLY in `harness/thresholds.json` (id in brackets after each Floor), and `docs/harness/ARCHAEOLOGY.md` is required reading before touching any test. Tiers: `fast` (pre-commit, 85 files), `full` (PR), `bench` (main + weekly). See `docs/harness/DESIGN.md`.

Posture: **conservative**. Every Floor is a value the tree at `63a621d` clears
with margin. Targets are aspirational and separately labelled. Where a number
could not be established it is written UNKNOWN, with the resolution in `Gap`.
Where the current code does not clear a sensible floor, the criterion is in
§"Not yet enforceable", not in the main list.

The niche, from `NICHE.md`: *an agent calls jCodeMunch-MCP to get the exact
source span that answers a question about a codebase, at symbol granularity,
in one round trip and at a small fraction of the tokens that reading files or
grepping would cost.*

---

## Criteria, in the ranked order of `NICHE.md`

### 1. Correctness of what is returned
Why it matters: the largest issue theme over 90 days (~42 of 140 closed) is a
wrong span, a fabricated symbol, or a confident absence claim. Each costs the
operator a debugging cycle; the product's value is that it does not.
Metric: (a) extractor fidelity against the language's own parser, four
buckets `extra`, `wrong_span`, `undercount`, `qual_mismatch`; (b) retrieval
quality nDCG@10 / MRR / Recall@10 on the golden replay set; (c)
`find_implementations` per-channel recall on the authored goldset.
Method: (a) `PYTHONPATH=src python -m pytest tests/test_rust_fidelity.py tests/test_rust_fidelity_artifacts.py tests/test_racket_fidelity.py tests/test_racket_reader.py tests/test_racket_fidelity_artifacts.py -q` (frozen oracles, no toolchain); live re-measure per `tests/fixtures/rust/REGENERATE.md` and `benchmarks/racket_fidelity/`. (b) `PYTHONPATH=src python benchmarks/replay/run_replay.py --gate 0.02` (self index; `replay.yml` on every push and PR). (c) `PYTHONPATH=src python benchmarks/goldset/measure.py` and `tests/test_channel_accuracy.py`.
Current: (a) Rust on ripgrep `3fce3b5`: extra 0, wrong_span 0, undercount 0, qual_mismatch 0, coverage 95.8%, missing 156 all `module`+`macro`; Racket 211 files: extra 0, wrong_span 0, source_coverage 89.7%, reader 761,009 nodes with 0 disagreements. (b) nDCG 1.0 / MRR 1.0 / Recall 1.0 on 10 queries, run 2026-09-03, 2.717 s. (c) recall 1.0 on all three channels; precision ast 0.818, duck 0.5, decorator 0.556. All at `63a621d`.
Floor: (a) all four gated buckets at 0 for Rust [`fidelity.rust.*`]; `extra` and `wrong_span` at 0 for Racket [`fidelity.racket.*`]. (b) no replay aggregate more than the `replay.max_relative_drop` fraction below the golden file (0.02 at set time). (c) recall 1.0 on every channel [`goldset.recall_min`].
Target: a fidelity oracle for every language in the top ten of the corpus mix, with the same four buckets at 0; a replay set of at least 100 queries across three languages.
Status: MEASURED for the Floors (fast tier + the bench tier's replay gate in `pr-gate.yml` + `python -m harness check`); PARTIALLY for the axis (two languages have oracles; replay is 10 queries on one repo).
Gap: oracles for Python, TypeScript, Go, Java, C# at minimum; a larger replay golden set; a repo-wide precision number for `search_symbols` that `tests/` can gate.

### 2. Token reduction per task
Why it matters: the headline claim, the product name, and the number every
public surface quotes. A silent drift here is a marketing claim that stopped
being true.
Metric: grand `jmunch_tokens` over 15 task-runs on the three pinned corpora, against the grep-top-3 baseline (`results.md` "Grand Summary"), tokenizer cl100k.
Method: `python benchmarks/harness/run_benchmark.py --out benchmarks/results.md --reference` on the pinned SHAs in `benchmarks/tasks.json` (network clone, 39 s end to end on the CI runner). Sync guards: `tests/test_benchmark_reference.py`, `tests/test_provenance.py`. `benchmark.yml` re-runs weekly and warns on drift.
Current: committed 24,249 jcm tokens vs 664,975 grep-top-3 = 27.4x (captured 2026-08-25 at 1.108.297). CI re-measure 2026-09-03 (run 33713310141): 23,440 jcm tokens, i.e. 28.4x, and the workflow is warning that the committed artifact is stale.
Floor: [`token.grand_ratio_vs_grep`] at least 20x fewer tokens than grep-top-3 on the pinned corpus, and [`token.per_repo_rise_max`] no repo's `jmunch_total_tokens` more than 10% ABOVE its committed value.
Target: the committed artifact never more than one week older than the last weekly run that disagreed with it.
Status: MEASURED and GATED: `run_benchmark.py --floor` fails the weekly `benchmark.yml` on an upward move (2026-09-03); the verdict logic is pinned offline by `tests/test_token_benchmark_floor.py`, which also asserts the committed `results.md` clears the floor.
Gap: none for the gate. The downward drift warning (FINDINGS F-10) still needs the Practice 4 re-sync.

### 3. Index freshness and incremental cost
Why it matters: stale-and-confident is the failure shape this project
documents most (#572, #404, #405, #493, #565). The second axis of freshness is
how much a single edit costs to absorb, because a cache invalidated on every
write is not a cache (#557).
Metric: (a) property: no read path answers `fresh` for a comparison it could not make, and no absence claim is served over incomplete coverage; (b) wall-clock of `index_file` for one edited file on the self corpus; (c) wall-clock of a cold `index_folder` of the self corpus.
Method: (a) `PYTHONPATH=src python -m pytest tests/test_freshness*.py tests/test_v1_108_178.py tests/test_v1_108_179.py tests/test_v1_108_180.py tests/test_v1_108_181.py tests/test_result_cache_isolation.py tests/test_dead_code_corpus_adequacy.py tests/test_absence_wiring_guard.py -q` (file list to be pinned by the enforcement plan). (b)(c) the script in `DISCOVERY.md` §3 against a scratch `CODE_INDEX_PATH`.
Current: (a) green in the 9,161-pass suite of 1.108.316. (b) 738.1 ms for one appended line in `storage/token_tracker.py`; 338.7 ms to establish "unchanged" in a fresh process. (c) 13.88 s for 950 files / 18,046 symbols. All 2026-09-03 on the box in `DISCOVERY.md` §0.
Floor: (a) the property tests pass. (b) [`index.one_file_reindex_ms`] and (c) [`index.cold_self_seconds`]: 2x the median of three consecutive runs of `benchmarks/self_latency/measure.py` on the src-only self corpus (275 files), set 2026-09-03; the runs are committed in `harness/results/self_latency_three_runs.json`. The 13.9 s / 0.74 s figures above were the FULL repo in a warm process and are not comparable.
Target: (b) under 1 s p95 for a single edited file on the self corpus, measured in CI on ubuntu; (c) under 20 s cold on the self corpus in CI.
Status: MEASURED (`python -m harness bench` writes the artifact and compares it to the threshold file).
Gap: the floors are set from one box; a CI runner must produce its own three runs before they tighten (DESIGN s5).

### 4. Tool-surface discipline
Why it matters: `benchmarks/codex_surface/` measured 86% of baseline input as
cached, so the tool block is paid roughly once and a moved prefix is a
full-rate rewrite for every user. A larger or drifting surface is a cost the
operator sees on every request.
Metric: `tools/list` token weight per surface (`benchmarks/schema_baseline.json`), the `core_compact` hard ceiling, the byte-pinned `counter` surface, and the description-smell gate.
Method: `PYTHONPATH=src python -m pytest tests/test_schema_budget.py tests/test_counter_surface_stability.py tests/test_schema_baseline_transcription.py tests/test_description_smells.py tests/test_catalog_moratorium.py -q`; regenerate the baseline with `python benchmarks/harness/capture_schema_baseline.py` only when a change is accepted.
Current: counter 939 tokens vs full_full 22,741 (95.9% avoided); core_compact 3,885 in the baseline, live gate at 3,998 of 4,000 per `CLAUDE.md` (#571); counter surface 6 tools, 4,184 B, byte-pinned; live drift vs baseline at most 4.5% (full_compact) on 2026-09-03; route@1 71.2% on the human corpus against a 60% moratorium bar.
Floor: core_compact at or under the [`schema.core_compact_ceiling`] (4,000 at set time); every profile within [`schema.drift_tolerance`] of the committed baseline; counter surface byte-identical to its pin; counter avoids at least [`counter.saving_min`] of `full_full`; **route@1 on the held-out CONTROL subset at or above its pre-fix baseline [`route.control_at1`, floor 40.0, target 55.0]. CORRECTION 2026-09-03 (FINDINGS F-02): the earlier "route@1 >= 60%" was never a gate, and 55% is the moratorium EXIT bar, a target.**
Target: core_compact back under 3,800 to recover editing headroom (the ceiling is currently 2 tokens away).
Status: MEASURED.
Gap: none for enforcement. The headroom problem is a product decision, not a measurement one.

### 5. Latency
Why it matters: ~13 of 140 issues were hangs or CPU burn (#557, #399, #370,
#375). Every fix was a budget, a heartbeat or a lock report, which is the
right shape, but nothing prevents the next one.
Metric: per-tool p50 / p95 from `analyze_perf`; cold and warm latency of the six core tools on the self corpus.
Method: today, the script in `DISCOVERY.md` §3 (hand-run). `analyze_perf` reports from a session ring (512 calls) or the opt-in `telemetry.db`.
Current (2026-09-03, self corpus, fresh process): `search_symbols` cold 2,605.9 ms / warm 0.8 ms; `search_text` cold 8,780.4 ms / warm 325.9-383.4 ms; `get_symbol_source` warm 7.4 ms. No committed artifact carries any of these.
Floor: [`latency.<tool>_warm_p95_ms`] for `search_symbols`, `search_text`, `get_symbol_source`, `get_file_outline`: 2x the median of three consecutive `benchmarks/self_latency/measure.py` runs on the src-only self corpus (set 2026-09-03; medians 11.2 / 135.3 / 13.1 / 13.9 ms). `analyze_perf` still refuses to diff latency against the TOKEN baselines (v1.108.309), correctly; this artifact is the latency baseline it lacked.
Target: warm p95 under 500 ms for `search_symbols`, `search_text`, `get_symbol_source`, `get_file_outline` on the self corpus in CI; cold first-call under 5 s.
Status: MEASURED (bench tier).
Gap: CI runner noise is unmeasured until `pr-gate.yml`/`main.yml` have produced three bench runs; `search_text` at 10x `search_symbols` warm (FINDINGS F-07) is the first thing to profile.

### 6. Install, configuration and client friction
Why it matters: ~22 of 140 issues. A failed first run ends adoption before any
other axis is visible, and #536 showed the served `serverInfo` and
`instructions` can be wrong while every test is green because tests run from
source.
Metric: (a) the published artifact performs a real stdio handshake reporting its own version and a non-empty `instructions` string; (b) config keys documented vs total; (c) client configs that install without edits.
Method: (a) MANUAL today: `uv venv` + `uv pip install "jcodemunch-mcp==X"` + stdio `initialize` (release skill step; `tests/test_mcp_instructions.py` covers the source side only). (b) `DISCOVERY.md` §7 count; `tests/test_docs_config_parity.py` is one-directional. (c) `CLIENTS.md` lists 13 client configurations; nothing executes them.
Current: (a) verified by hand at 1.108.292 for #536; not re-verified since. (b) 65 of 94 keys documented by name, 13 more via env var, 16 in neither. (c) UNKNOWN.
Floor: (a) `tests/test_mcp_instructions.py` passes (source side). (b) BOTH directions: `tests/test_docs_config_parity.py` (documented -> exists) and `tests/test_config_docs_reverse_parity.py` (exists -> documented, with an INTERNAL_KEYS list that may only shrink; FINDINGS F-03).
Target: (a) an automated post-publish handshake against the PyPI artifact; (b) every `DEFAULTS` key documented or explicitly listed as internal; (c) each `CLIENTS.md` config parsed and validated in a test.
Status: PARTIALLY MEASURED.
Gap: a post-release CI job (`release.yml` post-publish since 2026-09-04) that installs the artifact in a fresh venv and asserts the handshake; a reverse-direction config parity test with an internal-keys allowlist.

### 7. Stability across releases
Why it matters: 246 releases in 90 days. Four consecutive ones shipped on a
red build once. Five version pin sites must agree, and a changed description
rewrites every user's cached prefix.
Metric: pin-site agreement; replay gate across adjacent releases; counter byte-pin; CI green on the release commit.
Method: `PYTHONPATH=src python -m pytest tests/test_server_json_sync.py tests/test_plugin_manifest_sync.py tests/test_lockfile_version_sync.py tests/test_counter_surface_stability.py -q`; `replay.yml`; the release skill's "read CI first" step.
Current: all sync tests green at 1.108.316; last 10 `test.yml` runs 9 of 10 green, the failure fixed in the next push; replay 1.0 on all aggregates.
Floor: pin sites agree; replay within 2%; counter surface unchanged unless the CHANGELOG says so.
Target: the release commit is refused when the previous commit's CI is red or still running.
Status: PARTIALLY MEASURED (branch protection requires only `license/cla`, so a red matrix does not block a merge or a release; `test.yml` now has `timeout-minutes` [`ci.test_job_timeout_minutes`] and a skip ceiling [`ci.skips_*`]).
Gap: make `Tests` and `replay` required status checks on `main`; a release pre-flight script that queries the CI conclusion of `HEAD` and refuses on anything but `success`.

### 8. Security and integrity of what is indexed
Why it matters: the most expensive incident in the project's history (PyPI
quarantine, 2026-06-10) was an undisclosed persistent behaviour, and the
zip-slip and sdist-credential fixes each have a regression pin because they
each shipped once.
Metric: sdist root allowlist in both directions and credential-path canaries; path validation and symlink-escape tests; disclosed-route count in SECURITY.md; release artifacts signed.
Method: `PYTHONPATH=src python -m pytest tests/test_build.py tests/test_sdist_exclusions.py tests/test_security_disclosure.py tests/test_security*.py -q`; `test.yml` sdist tar grep on Linux legs; `sign-release.yml` (sigstore).
Current: all green at 1.108.316; wheels and sdists signed since v1.108.22; dependabot security-only; no CodeQL, pip-audit or bandit; no written threat model; `trusted_folders` whitelist is a no-op with the shipped empty list; SECURITY.md's "500 files" limit disagrees with code (10,000 / 2,000).
Floor: the listed tests pass; the sdist tar grep finds nothing; every new background, persistent or network behaviour has a README disclosure before it ships (the standing rule in the suite `CLAUDE.md`); `tests/test_security_md_limits_parity.py` binds the SECURITY.md limits table to `security.DEFAULT_*` (the file-count row is a strict xfail until the doc is fixed, FINDINGS F-01).
Target: a dependency vulnerability audit in CI; a one-page threat model in SECURITY.md; a parity test between SECURITY.md's limits table and `security.py` defaults.
Status: PARTIALLY MEASURED.
Gap: `pip-audit` (or `uv`'s equivalent) as a CI step with an allowlist file; a test that reads SECURITY.md's limits table against `security.DEFAULT_*`; a decision on whether an empty `trusted_folders` should mean "everything" (documented) or "nothing" (breaking).

### 9. Observability and telemetry honesty
Why it matters: the project's distinguishing habit is that a refusal is not a
zero, UNKNOWN is never False, and every published rate names its basis. It is
what keeps the numbers above believable.
Metric: property tests that pin tri-state answers, `*_basis` fields, refusal-over-zero and disclosed background behaviour.
Method: `PYTHONPATH=src python -m pytest tests/test_v1_108_186.py tests/test_result_cache_isolation.py tests/test_stop_rule.py tests/test_analyze_perf_totals.py tests/test_security_disclosure.py -q` (representative; the enforcement plan pins the full list).
Current: green at 1.108.316. Not a scalar.
Floor: those tests pass; no new tool publishes a rate without a `*_basis` field or a refusal path (review criterion, see Definition of Done).
Target: a single ratchet file enumerating every honesty invariant by name, so a deleted test is noticed.
Status: MEASURED as properties, and ENUMERATED: `tests/test_standard_invariants.py` names each pin and fails if one is deleted; `tests/test_retirement_ledger.py` refuses any deletion of a test listed in ARCHAEOLOGY without a ledgered replacement.
Gap: none.

### 10. Breadth of language support
Why it matters: table stakes past the top ten languages; ~12 of 140 issues were
parser coverage. Fidelity (criterion 1) matters more than the count.
Metric: `len(LANGUAGE_REGISTRY)` and `len(LANGUAGE_EXTENSIONS)`.
Method: `PYTHONPATH=src python -c "from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY, LANGUAGE_EXTENSIONS; print(len(LANGUAGE_REGISTRY), len(LANGUAGE_EXTENSIONS))"`.
Current: 79 languages, 164 extensions (2026-09-03, `63a621d`).
Floor: neither count decreases [`languages.registry_min`, `languages.extensions_min`] without a CHANGELOG entry naming the removal and a tightened-history threshold entry.
Target: fidelity oracles (criterion 1) for the ten languages most represented in indexed repos.
Status: MEASURED (count, `tests/test_standard_invariants.py`); fidelity UNMEASURED beyond Rust and Racket.
Gap: the oracles are criterion 1's gap.

---

## Non-functional criteria that protect autonomy

### N1. Test-suite runtime ceiling
Metric: wall-clock of the `pr-gate.yml` full-tier job per leg, and local `-n auto` runtime.
Method: `gh run list --workflow pr-gate.yml --limit 10 --json createdAt,updatedAt,conclusion`; locally `uv run pytest tests/ -n auto --dist loadfile -q`.
Current: CI 9m44s to 15m59s over the last 10 runs (whole workflow, 9 jobs); local `-n auto` 183 s on 2026-08-16 at 7,859 tests (`pyproject.toml:178-179`), UNKNOWN today at 9,174.
Floor: CI test job under [`ci.test_job_timeout_minutes`] (20, enforced by `timeout-minutes`); a local `-n auto` run under [`suite.full_seconds`] (360 s); the fast tier under [`suite.fast_seconds`] (90 s).
Target: CI under 12 minutes; local under 4.
Status: MEASURED (`python -m harness` times each tier and fails over the ceiling; 2026-09-03 full tier 188.43 s, fast tier 49.35 s at 79 files).
Gap: none.

### N2. Coverage floor
Metric: line coverage of `src/` under the full suite.
Method: `uv run pytest tests/ -n 4 --dist loadfile --cov=src --cov-fail-under=74` (CI).
Current: passes the 74% floor on every green run; the actual percentage is UNKNOWN (not printed in this session and not recorded in any artifact).
Floor: [`coverage.min`] 74% (enforced since v1.108.76; read from the threshold file by the full tier (`pr-gate.yml`) since 2026-09-03).
Target: record the measured percentage per release so the floor can be raised on evidence.
Status: MEASURED (floor), UNKNOWN (value).
Gap: emit `coverage.json` as a CI artifact and pin the number in `whatsnew.json` or a benchmarks artifact.

### N3. Lint and type cleanliness
Metric: `ruff check src/` errors; type-checker errors.
Method: `uv run ruff check src/` (CI `lint` job); no type checker exists.
Current: `src/` clean; `tests/` 292 pre-existing errors (274 unused imports); mypy/pyright not configured, not installed.
Floor: `ruff check src/` clean (fast tier runs it).
Target: `ruff check tests/` clean; a type checker on `src/` at zero errors.
Status: MEASURED for `src/` lint. Types: see Not yet enforceable.
Gap: fix the 282 auto-fixable test-lint errors in one commit, then add `tests/` to the lint job; type checking needs a baseline run first (UNKNOWN error count).

### N4. Deterministic benchmark output
Metric: a committed benchmark artifact re-run on the same tree is byte-identical.
Method: `tests/test_route_recall_artifacts_are_fresh.py` (re-runs both route harnesses), `tests/test_channel_accuracy.py`, `run_benchmark.py --verify-determinism`.
Current: route_recall, goldset and description_smells re-ran byte-identical on 2026-09-03; `cache_stability/results.json` did NOT (every number moved, verdict unchanged) and is pinned by nothing.
Floor: every artifact a test pins is byte-identical on re-run, and every corpus a gated harness reads matches `harness/corpora.json` (verified at the start of every fast tier).
Target: `cache_stability` either pinned to a fixed corpus snapshot or labelled non-deterministic in its README.
Status: MEASURED for the pinned artifacts.
Gap: decide `cache_stability`'s corpus; add a pin or a label.

### N5. No network access during tests
Metric: sockets opened by the test process.
Method: today, a grep (`DISCOVERY.md` §4: 9 files mention httpx/socket/urlopen, none reach the network; `httpx` uses an in-process ASGI transport).
Current: no network reached, by inspection.
Floor: no test opens an outbound socket.
Target: enforced by a session-scoped fixture that raises on `socket.connect` to a non-loopback address.
Status: MEASURED (session-scoped fixture in `tests/conftest.py` raises on any non-loopback `socket.connect`; `@pytest.mark.network` opts out, zero users).
Gap: none.

### N6. Agent-instruction budget
Metric: `CLAUDE.md` size in characters.
Method: `tests/test_claude_md_size.py` (BUDGET 140,000).
Current: 137,442 (2026-09-03), 2,558 headroom.
Floor: at or under [`claude_md.max_chars`] 140,000. That number was a LOOSENING from 130,000 on 2026-08-27 (jjg: the 10k buffer is the last); the threshold entry records it and the loader announces it on every run. It may never rise.
Target: under 120,000 after the next rotation, so a release entry does not need a rotation first.
Status: MEASURED.
Gap: none; rotation is Practice 5.

### N7. CI skip count
Metric: `skipped` on each CI leg.
Method: read the pytest summary line of the `pr-gate.yml` full-tier job.
Current: ubuntu 26, windows 19, local 13 (`CLAUDE.md`); the 2026-08-28 incident was 105 skipped at exit 0.
Floor: ubuntu at or under 30, windows at or under 25.
Target: a step that fails the job when `skipped` exceeds the floor.
Status: MEASURED and enforced (`pr-gate.yml` fails a leg over [`ci.skips_ubuntu`] / [`ci.skips_windows`]; the full tier applies the same ceiling locally).
Gap: reconcile the 19-vs-13 local delta (FINDINGS F-05).

---

## Definition of Regression

A change is a blocking regression, and must not merge, when any of the
following holds on the same job and same corpus as the committed value:

1. Any Rust fidelity gated bucket (`extra`, `wrong_span`, `undercount`, `qual_mismatch`) or Racket `extra` / `wrong_span` becomes non-zero.
2. Any replay aggregate (nDCG@10, MRR, Recall@10) falls more than 2% relative below the golden file.
3. Any `find_implementations` channel recall falls below 1.0 on the goldset.
4. `core_compact` exceeds 4,000 tokens, or any surface moves more than 5% from `schema_baseline.json` without a regenerated baseline and a CHANGELOG entry that states the cache-write cost.
5. The `counter` surface bytes change without a CHANGELOG entry.
6. Any repo's `jmunch_total_tokens` in the token benchmark rises more than 10% above its committed value, or the grand ratio vs grep-top-3 falls below 20x.
7. Route@1 on the human corpus falls below 60%.
8. `ruff check src/` reports any error.
9. Coverage falls below 74%.
10. Any sdist canary or root-allowlist test fails, or the CI tar grep finds a credential path.
11. Any version pin-site sync test fails.
12. CI skip count exceeds the N7 floor on either OS.
13. `CLAUDE.md` exceeds `claude_md.max_chars`.
14. Any `index.*` or `latency.*` value from `benchmarks/self_latency/measure.py` exceeds its threshold (2x the committed median).
15. Any corpus in `harness/corpora.json` changes checksum without the manifest being re-pinned in the same commit.
16. A test file listed in `docs/harness/ARCHAEOLOGY.md` disappears without a `harness/retired.json` entry.
17. A Floor literal appears anywhere but `harness/thresholds.json`.

A change that moves a number in the OTHER direction (fewer tokens, higher
recall) is not a regression, and the committed artifact is re-synced in the
same PR so the published figure never lags the measurement in our own favour
without saying so.

Latency and incremental-cost floors are set from one box (2026-09-03) and
apply in the bench tier; they tighten only after a CI runner has produced its
own three runs.

## Definition of Done for a change

A PR is mergeable when all of the following exist:

1. A test that fails on the pre-change tree and passes on the post-change tree, run on the non-vacuity pass (Practice 9). For a destructive-defect fix, the test executes the defect against a target the test owns.
2. `ruff check src/` clean and the touched test files green locally BEFORE the full suite (Practice 10); the full suite green once as the gate, with the skip count read.
3. For a change under `src/`: a `CHANGELOG.md` `[Unreleased]` entry that states what was wrong, why, and what the fix makes impossible, or the `no-changelog` label with the reason in the PR. A change outside `src/` (the harness, the workflows, the docs, the tests, the benchmarks) records itself in its layer's FINDINGS or ARCHAEOLOGY row and owes no entry; `scripts/dod_changelog.py` is the gate and reads exactly this rule.
4. If a tool was added or its description changed: README tool reference, `CLAUDE.md` Key Files (invariant only) or `KEY-FILES.md` (description), and the schema baseline regenerated with the token delta stated in the CHANGELOG (Practice 1; criterion 4).
5. If a benchmark artifact moved: every mirror re-synced in the same PR (`results.md`, `METHODOLOGY.md`, README, `provenance/measured.json`, `REPRODUCING.md`; Practice 4) and `tests/test_provenance.py` green.
6. If a config key, env var or CLI subcommand was added: a row in `CONFIGURATION.md` or `CLI-AND-ENV.md`, and in `CLAUDE.md` only if it carries an invariant.
7. If a new background, persistent or network behaviour was added: a README disclosure in "Background behavior, fully disclosed" before the release.
8. If a number is published in a response (a rate, a share, a confidence): a `*_basis` field or a refusal path exists, and UNKNOWN is never rendered as zero or False (criterion 9).
9. For a contributor PR: trial-merged onto `main` and run locally before the merge; `license/cla` status present on the head SHA.
10. `python -m harness fast` passes; for a change under `benchmarks/`, `harness/` or `server.py`, `python -m harness bench --offline` passes too.
11. If a test was retired: a `harness/retired.json` entry with the lesson and the replacement assertion, in its own commit.
12. If a threshold moved: tightened with the old value appended to `history`, or loosened with a `loosened` block that names who and why.

## Not yet enforceable

These have no Floor the current tree is known to clear, or no instrument.

| Criterion | Blocker | What would make it enforceable |
|---|---|---|
| 6(a). Published-artifact handshake | manual step in a gitignored skill | post-publish CI job (item 5) |
| 6(c). Client configs install cleanly | nothing parses `CLIENTS.md` | a test that extracts and validates each config block (item 9) |
| N3 types | no checker configured; error count UNKNOWN | baseline run with pyright or mypy, then ratchet the count down (item 10) |
| N3 tests lint | 292 errors today | one auto-fix commit, then add `tests/` to the lint job (item 6) |
| 1(target). Fidelity for more languages | no oracles | per-language oracle harnesses; the Rust one is the template (item 11) |
| 8(target). Dependency audit, threat model | not present | `pip-audit` step; SECURITY.md section (items 8, 12) |
| 7(target). Red CI cannot be released | branch protection requires only `license/cla` | required checks + release pre-flight (item 3) |
