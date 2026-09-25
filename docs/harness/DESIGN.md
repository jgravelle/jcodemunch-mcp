# DESIGN: the harness as the source of truth

2026-09-03, branch `harness/source-of-truth` at `457a758`. Inputs:
`docs/standard/STANDARD.md` (criteria, Floors), `ARCHAEOLOGY.md` (491 test
files, 62 methodology rules R1-R62, 17 threshold movements), `COVERAGE-MAP.md`
(what is gated, what is a number). Nothing here is built yet.

## 1. One entry point

```
python -m harness            # == python -m harness all
python -m harness fast       # pre-commit tier
python -m harness full       # PR tier
python -m harness bench      # benchmark tier
python -m harness check <id> # evaluate ONE threshold id and print floor/observed/verdict
python -m harness thresholds # print the threshold table
```

`harness/` is a top-level package at the repo root (beside `tests/`,
`benchmarks/`), NOT inside `src/jcodemunch_mcp/` and NOT in the wheel: it is
dev tooling and the sdist root allowlist (`tests/test_sdist_exclusions.py`)
gets a `harness/` entry in BOTH directions. Exit code is non-zero on any Floor
violation, any test failure, or any tier over its runtime ceiling. Every run
prints one line per threshold: `id  criterion  floor  observed  PASS|FAIL`.

Why a Python module and not a Makefile: jjg's prompt is cmd.exe
(`CLAUDE.md`, "publish line"), CI is ubuntu and windows, and `uv run python
-m harness` is the same spelling on all three.

## 2. Tiers, with measured runtimes

| Tier | Contents | Measured 2026-09-03 (this box, `-n auto`) | Ceiling | Runs on |
|---|---|---|---|---|
| **fast** | the 79 files in `harness/tiers.json["fast"]`: the 69 repo-invariant files that import no package module (size budgets, sync pins, sdist allowlist, CI-command binding, artifact transcription guards) plus the 10 surface/standard pins (`test_schema_budget`, `test_counter_surface_stability`, `test_description_smells`, `test_catalog_moratorium`, `test_route_recall_artifacts_are_fresh`, `test_tier_switch_cost`, `test_channel_accuracy`, `test_mcp_instructions`, `test_stop_rule`, `test_replay_metrics`), then `ruff check src/`, then every OFFLINE threshold check | **1,120 passed, 7 skipped, 49.35 s** | 90 s | pre-commit hook (opt-in via `init --hooks`), every `harness` invocation |
| **full** | `tests/` entire, `-n auto --dist loadfile`, coverage, then the fast tier's threshold checks again over the fresh artifacts | **9,155 passed, 19 skipped, 188.43 s** (`suite_before.log`) | 360 s local; CI `timeout-minutes: 20` | every PR, every push to main |
| **bench** | replay gate; route-recall x3 + explain_misses; schema-baseline capture (print mode, diff vs committed); tier-switch pricing; goldset measure; description-smells score; NEW `self_latency` (cold index, one-file reindex, six core tools warm p95); the token benchmark on the three pinned corpora when network is available (`--offline` skips it and says so) | offline subset ~28 s + self_latency UNKNOWN until built (expect ~60 s: two cold indexes of `src/` at 13.9 s each plus 20 warm calls x 6 tools); token benchmark 39 s on CI | 300 s offline | merge to main and Mondays 07:00 UTC (`benchmark.yml` already exists at that cadence); PRs run it only when `benchmarks/**`, `harness/**` or `src/jcodemunch_mcp/server.py` changed |

The split is justified by the numbers: the fast tier is 12% of the files and
covers every criterion that has an OFFLINE artifact (2 sync, 4, 7, 8 sdist,
N4, N6), so a pre-commit hook can refuse a prefix-moving description edit or
a broken pin in under a minute. The full tier is the release gate and already
fits a 4-minute local run. The bench tier is kept off the PR path because its
one network step is the token benchmark and its one slow step is the
self-latency harness, and neither changes on an ordinary PR.

Rust and Racket LIVE oracles, deadcode_eval, RAG/Odysseus, codex_surface and
SWE-bench are NOT in any tier; they stay manual with their frozen artifacts
pinned by the full tier (`test_*_fidelity_artifacts.py`), which is what the
tree does today. Recorded as a limitation, not a plan to change.

## 3. Threshold file

`harness/thresholds.json`, the ONLY place a Floor or Target lives. Every
entry:

```json
{
  "id": "schema.core_compact_ceiling",
  "criterion": "4",
  "metric": "tools/list tokens, core profile, compact schemas, cl100k",
  "comparator": "<=",
  "floor": 4000,
  "target": 3800,
  "unit": "tokens",
  "set_at": {"commit": "457a758", "date": "2026-09-03", "by": "harness/source-of-truth", "reason": "STANDARD §4; ceiling introduced 2026-08-16 (ARCHAEOLOGY §C)"},
  "measured": {"value": 3998, "commit": "63a621d", "date": "2026-09-03", "source": "tests/test_schema_budget.py"},
  "history": [],
  "enforced_by": ["tests/test_schema_budget.py::test_core_compact_hard_ceiling"]
}
```

Rules:
- `floor` is the Floor from STANDARD.md, never the best observed value (principle 5). A tightening appends the old entry to `history`; a LOOSENING is refused by `harness.thresholds.load()` unless the entry carries `"loosened": {"reason": ..., "by": ...}`, and the loader prints it on every run so it cannot be quiet (the 130k -> 140k CLAUDE.md case is recorded that way).
- `tests/test_thresholds_are_the_only_copy.py` (new, fast tier) scans `tests/`, `benchmarks/`, `.github/workflows/` for every literal that appears as a `floor` in the file and fails if one is found outside the loader's call sites. It is the `test_schema_baseline_transcription.py` idea applied to thresholds, and it runs against the reintroduced defect on its non-vacuity pass (Standing lesson 08-22).
- Workflows read thresholds through `python -m harness threshold <id>` (prints the value) so `replay.yml`'s `--gate` and `test.yml`'s `--cov-fail-under` stop being hand-typed.
- Initial entries (all Floors the tree clears today; values recomputed in Phase 4, not copied from this doc): `fidelity.rust.{extra,wrong_span,undercount,qual_mismatch}=0`, `fidelity.racket.{extra,wrong_span}=0`, `replay.relative_drop<=0.02`, `goldset.recall_min>=1.0`, `token.grand_ratio_vs_grep>=20`, `token.per_repo_rise_max<=0.10`, `schema.core_compact_ceiling<=4000`, `schema.drift_tolerance<=0.05`, `counter.surface_bytes==pinned`, `counter.saving_min>=0.95`, `route.control_at1>=40` (target 55; corrected from the standard's 60 and from this line's own `>=55`, #715; COVERAGE-MAP §4.1), `languages.registry_min>=79`, `languages.extensions_min>=164`, `coverage.min>=74`, `claude_md.max_chars<=140000 (loosened)`, `suite.fast_seconds<=90`, `suite.full_seconds<=360`, `ci.skips_ubuntu<=30`, `ci.skips_windows<=25`, `latency.*` and `index.*` (self corpus; floor = 2x first committed measurement, per §5).

## 4. Benchmark result format and where it lives

`harness/results/latest.json` is COMMITTED on main by the bench tier's
scheduled run (the existing Monday `benchmark.yml` slot, extended), one file,
overwritten, so `git log -p harness/results/latest.json` is the trend and a
regression is locatable to the commit range between two Monday runs. PR runs
upload the same JSON as a CI artifact (90-day retention) and never commit,
because main sees 2.7 pushes a day and a per-push commit would double the
release commit count. Shape:

```json
{"schema": "jcm-harness-result/v1", "commit": "...", "date": "...",
 "env": {"os": "...", "python": "...", "cpus": 24, "runner": "local|github"},
 "tiers": {"fast": {"seconds": 49.35, "passed": 1120, "skipped": 7, "failed": 0}, ...},
 "thresholds": [{"id": "...", "floor": ..., "observed": ..., "verdict": "PASS"}],
 "artifacts": {"schema_baseline": {...}, "route_recall": {...}, "self_latency": {...}}}
```

Every number in `artifacts` is the harness's own output; nothing is
transcribed from a README (R-rules "never hand-typed", ARCHAEOLOGY §A).

## 5. Determinism plan

- **Corpora pinned with checksums.** `harness/corpora.json` lists every corpus a gated harness reads: the three `tasks.json` SHAs (already pinned, R1-R6), `tests/fixtures/rust/` + `rust_oracle.json`, Racket fixtures, `benchmarks/replay/fixtures/`, `benchmarks/goldset/` corpus (sha256 already recorded), `benchmarks/route_recall/*.json` corpora, and the self corpus for latency (pinned by `git rev-parse HEAD` + a sha256 over `git ls-files src/`). The fast tier verifies every checksum before any harness runs.
- **Seeds.** The only randomised harness is route_recall's emitted-task corpus (seed 421, R-rule); the seed moves into the threshold file's `params` and the harness passes it explicitly.
- **No wall-clock assertions in the test tiers.** Runtime ceilings are checked by the harness runner around pytest, not inside a test; latency floors live in the bench tier only.
- **No network.** Session-scoped autouse fixture in `tests/conftest.py` patches `socket.socket.connect` to raise on non-loopback targets; `@pytest.mark.network` opts out (zero users today). The bench tier's token benchmark declares `network: true` in `tiers.json` and is skipped under `--offline`.
- **Tolerance bands.** Rule: a band is set only from THREE consecutive runs on one machine at one commit; band = max(5%, 3 x the observed max-min spread), recorded in the threshold entry's `set_at.reason` with the three values. Timing floors are additionally 2x the committed median until a CI runner has produced its own three runs. A band is never widened to make a run pass; a run outside the band is a FINDING.
- **`cache_stability`** does not enter any tier until its corpus is pinned (COVERAGE-MAP N4); it is listed in `tiers.json["excluded"]` with the reason.

## 6. Migration plan for existing tests

**No test file moves and nothing is retired.** ARCHAEOLOGY found 0 REDUNDANT
and 0 STALE test files under the rule that a covering file must be named and
cover fully; moving 491 files would break `git log --follow` for every scar
and gain nothing the tier list does not. The restructure is layered:

| Class | Count | Action |
|---|---|---|
| STRUCTURAL | 3 | stay; listed in `tiers.json["structural"]` |
| LOAD-BEARING | 484 | stay; the 79 offline ones join the fast tier by name; the four hardcoded thresholds they carry (`BUDGET` 140,000 in `test_claude_md_size.py`, the 4,000 ceiling and 0.05 tolerance in `test_schema_budget.py`, 55.0 in `test_catalog_moratorium.py`, the four zero buckets in `test_rust_fidelity_artifacts.py`) are read from the threshold file, each in its own commit, with the test's assertion text unchanged |
| REDUNDANT | 0 | none |
| STALE (tests) | 0 | none |
| STALE (benchmarks) | 4 scripts (`profile_backpressure.py`, `ab_v1_70_0.py`, two FlatCAM profilers) | moved to `benchmarks/attic/` with a README naming the commit that retired their subject; not deleted, no assertion depends on them |
| UNCLEAR | 3 tests + `cache_stability` | stay byte-identical; listed in `tiers.json["unclear"]` with the review question from ARCHAEOLOGY §5; the fast tier prints them as `REVIEW` lines on every run so they cannot be forgotten |

Retirement rule going forward, enforced by `tests/test_retirement_ledger.py`:
a test file may be deleted only if `harness/retired.json` names it, the lesson
it encoded, and the replacement assertion (`file::test`), and that assertion
collects. The ledger starts empty.

Workflow edits (`replay.yml --gate`, `test.yml --cov-fail-under`,
`timeout-minutes`, the bench job) are each one commit, after the tests.

## 7. Product-code seams anticipated

None are required by this design. `index_folder`, `index_file` and the six
core tools already take `storage_path`; `capture_schema_baseline.py` and the
route harnesses already have print modes. If the self-latency harness needs
an injectable clock or a way to disable the AI summarizer beyond
`use_ai_summaries=False`, that is one isolated commit listed in
`FINDINGS.md`.

## 8. Handling of the four proposed sub-criteria

6a, 3a, 9a, 8a (COVERAGE-MAP §3) are NOT added to the standard in this
session. Their pins are enumerated in `tests/test_standard_invariants.py`
under a `proposed` key so they are counted and cannot be deleted silently,
which is the enforcement they lack today.

## 9. How a future agent adds a criterion

1. Write the block in `docs/standard/STANDARD.md` with Metric, Method, Current, Floor, Target, Status, Gap. Current comes from a run in that session with commit and date.
2. Add a threshold entry to `harness/thresholds.json` with `set_at` and `measured` filled from that run. Floor = STANDARD Floor, never the observed value.
3. Write the assertion that reads the entry through `harness.thresholds.get(id)` and name it in the entry's `enforced_by`.
4. Add the test file to `tiers.json["fast"]` if it needs no network and runs under 5 s; otherwise it is in the full tier by default.
5. Run `python -m harness check <id>` and paste its line into the CHANGELOG entry.
6. Run `python -m harness fast`: `test_standard_invariants.py` fails until the entry, the test and the STANDARD block all name each other; `test_thresholds_are_the_only_copy.py` fails if the floor literal appears anywhere else.
7. Add a row to `ARCHAEOLOGY.md` §1 for the new test (path, asserts, why, criterion, class).

Removing or loosening one follows §3 (loosening flag, printed on every run)
and §6 (retirement ledger).

## 10. Build order for Phase 4 (from the brief, with the file names above)

1. `harness/__init__.py`, `harness/thresholds.py`, `harness/thresholds.json`, `tests/test_thresholds_are_the_only_copy.py`.
2. `harness/corpora.json` + checksum verifier; decide `cache_stability` (excluded with reason).
3. `harness/__main__.py` runner with `fast|full|bench|all|check|thresholds`, `harness/tiers.json`, runtime ceilings, result JSON writer.
4. Migrations, one commit each: STRUCTURAL (list only), LOAD-BEARING threshold reads (5 files), benchmark attic move, UNCLEAR listing. Full suite after every step; compare to `suite_before.log`.
5. New gates: `test_standard_invariants.py`, `test_retirement_ledger.py`, network fixture, language-count pin, reverse config parity with allowlist, SECURITY.md limits parity, `benchmarks/self_latency/`, token-benchmark floor mode, skip-count assertion, `timeout-minutes`.
6. `harness/results/latest.json` writer + `benchmark.yml` extension.
