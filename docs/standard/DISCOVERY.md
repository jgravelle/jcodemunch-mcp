# DISCOVERY — what jCodeMunch-MCP is, measured on 2026-09-03

Commit `63a621d` (v1.108.316), branch `standard/codify`, working tree clean at
start. Box: Windows 11 10.0.26200, Python 3.12.4, 24 logical CPUs, `.venv` via
uv. Everything below is a recorded command, a file path, or a number that came
out of one of them. Values that could not be established are marked UNKNOWN.

## 0. Assumptions made in this session

1. `docs/*` is gitignored (`.gitignore:83`, deliberately the glob form so a
   negation can work). The prompt requires `docs/standard/`, so a negation
   `!docs/standard/` was added beside the existing `!docs/prd-extraction-fingerprint.md`.
   Verified with `git check-ignore -v docs/standard/STANDARD.md` (exit 1, not
   ignored). This is the only non-doc file touched.
2. The full test suite was NOT run in this session; the prompt caps benchmark
   time and the suite is a documented 10-13 minute gate. Its size and duration
   are taken from CI runs and CLAUDE.md, both dated below.
3. "Reference corpus" for latency and incremental-cost measurements is THIS
   repository indexed into a scratch `CODE_INDEX_PATH`, because the pinned
   benchmark corpora (express/fastapi/gin) need a network clone. Recorded as a
   limitation in STANDARD.md.

## 1. Repository shape and packaging

| Fact | Value | Source |
|---|---|---|
| Version | 1.108.316 | `pyproject.toml:3`, `server.json:6,11`, `.claude-plugin/plugin.json:5` |
| Version scheme | `1.108.N`, N increments per release; five pin sites (`pyproject.toml`, `server.json` x2, `plugin.json`, `uv.lock` name-scoped line) plus regenerated `whatsnew.json` | `tests/test_server_json_sync.py`, `test_plugin_manifest_sync.py`, `test_lockfile_version_sync.py` |
| Python | >=3.10; CI matrix 3.10-3.13 | `pyproject.toml`, `.github/workflows/test.yml` |
| Build | hatchling; wheel packages `src/jcodemunch_mcp` + `munch-bench/munch_bench` | `pyproject.toml:89-98` |
| Entry point | `jcodemunch-mcp = jcodemunch_mcp.cli_entry:cli` | `pyproject.toml:105` |
| Extras | anthropic, gemini, openai, minimax, zhipu, dbt, http, watch, semantic, groq, keyring, bench, local-embed, groq-voice, groq-explain, all | `pyproject.toml:63-81` |
| Dev group | pytest, pytest-asyncio, pytest-cov, pytest-xdist, hypothesis, ruff, tiktoken (PEP 735, no `test` extra) | `pyproject.toml:173-201` |
| sdist excludes | `.claude/`, `.jcodemunch.jsonc`, `.gitattributes`, `index.php`, `vscode-extension/node_modules/`, `vscode-extension/out/`, `*.vsix`, `scripts/repair-munch-installs.ps1` | `pyproject.toml:100-128` |
| Tracked files | 1,082 | `git ls-files \| wc -l` |
| `src/**/*.py` lines | 123,721 | `git ls-files 'src/**/*.py' \| xargs wc -l` |
| Languages / extensions | 79 / 164 | `PYTHONPATH=src python -c "from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY, LANGUAGE_EXTENSIONS; print(len(LANGUAGE_REGISTRY), len(LANGUAGE_EXTENSIONS))"` |
| Agent instruction files | `CLAUDE.md` (137,442 chars vs 140,000 budget in `tests/test_claude_md_size.py:50`), `AGENTS.md`, `AGENT_HINTS.md`, `AGENT_HOOKS.md`, `KEY-FILES.md`, `CLI-AND-ENV.md`, `CONTRIBUTING.md`, suite-level `C:\MCPs\CLAUDE.md` | `wc -c CLAUDE.md` |
| Publish path | build with `uvx --from build pyproject-build`, `twine check`, upload with `uvx --from twine twine upload`; GitHub release triggers `sign-release.yml` (sigstore, OIDC); MCP registry via `mcp-publisher` (manual) | `.claude/skills/release/SKILL.md` (gitignored), CLAUDE.md "Registry verification", `sign-release.yml` |

## 2. Tool surface (executed, not grepped)

Command (run from repo root, 2026-09-03):

```
PYTHONPATH=src python -c "
from jcodemunch_mcp import server as s
for p in ('core','standard','full'):
    print(p, len(s._build_tools_list(profile_override=p)))
t = s._build_tools_list(surface_override='counter'); print('counter', len(t), [x.name for x in t])
print('catalog', len(s._raw_catalog_tools()))"
```

| Surface | Tools |
|---|---|
| `core` profile | 20 |
| `standard` profile | 82 |
| `full` profile | 91 |
| `counter` surface | 6 (`set_tool_tier`, `announce_model`, `jcodemunch_guide`, `order`, `menu`, `route`) |
| raw catalog | 94 (three front-door tools hidden under `full`) |

Schema token weights from `benchmarks/schema_baseline.json` (cl100k, written by
`benchmarks/harness/capture_schema_baseline.py`, guarded by `tests/test_schema_budget.py`):
core_compact 3,885 / core_full 5,913 / standard_compact 19,083 / standard_full
21,431 / full_compact 20,393 / full_full 22,741 / counter 939. Derived: counter
avoids 95.9% of `full_full`, core 74.0%, standard 5.8%. CLAUDE.md records the
LIVE core_compact figure at 3,998 of a 4,000 hard ceiling (#571).

## 3. Measurements run this session (this repo as corpus)

Scratch index at `%TMP%\jcm-std-idx`, `JCODEMUNCH_TRUSTED_FOLDERS` set to the
repo root, `use_ai_summaries=False`, process-cold unless stated. Script in the
session transcript; each line is one `time.perf_counter()` pair.

| Measurement | Value | Notes |
|---|---|---|
| `index_folder` of this repo, cold | 13.88 s; 950 files, 18,046 symbols | repo id `jgravelle/jcodemunch-mcp` |
| `search_symbols("cache_put")`, first call in a fresh process | 2,605.9 ms | includes index hydration |
| same query, second call | 0.8 ms | result cache hit |
| `search_text("cache_put")`, first call in a fresh process | 8,780.4 ms | includes hydration; see below |
| `search_text`, warm, 3 runs | 383.4 / 327.7 / 325.9 ms | |
| `get_symbol_source` (one method), warm | 7.4 ms | |
| `index_file` on an unchanged file, fresh process | 338.7 ms | "File unchanged" (content hash, mtime touch does not trigger reparse) |
| `index_file` on a file with one appended line | 738.1 ms | real re-parse + incremental save; file restored afterwards, `git status` clean |

Observation: the cold `search_text` at 8.8 s against a 2.6 s cold
`search_symbols` in the same index is UNKNOWN in cause (not profiled here); it
is logged, not fixed.

## 4. Tests

| Fact | Value | Source |
|---|---|---|
| Test files | 491 | `find tests -name 'test_*.py' \| wc -l` |
| Test functions (source) | 7,421 | grep `def test_` |
| Collected | 9,174 in 34.93 s | `PYTHONPATH=src python -m pytest tests/ --collect-only -q` |
| Last full local run | 9,161 passed, 13 skipped, 0 failed (v1.108.316) | `CLAUDE.md` "Tests:" line |
| Local duration, documented | 10:18 to 12:55 serial-ish; 183 s with `-n auto --dist loadfile` on this 24-core box (2026-08-16, 7,859 tests then) | `CLAUDE.md` Practice 10, `ISSUE-HISTORY.md:55`, `pyproject.toml:178-179` |
| CI duration, last 10 `test.yml` runs | 9m44s to 15m59s, 9 of 10 green | `gh run list --workflow test.yml --limit 10` |
| Coverage | CI enforces `--cov=src --cov-fail-under=74` since v1.108.76 | `test.yml`; local number NOT produced this session |
| pytest config | `testpaths`, `asyncio_mode=auto` only; no addopts, no timeout plugin | `pyproject.toml:130-132` |
| Skips on this box | 13 (numpy, PIL, onnxruntime absent; platform-conditional); CI ubuntu 26, windows 19 | `CLAUDE.md`, grep of skip reasons |
| Network in tests | none reaches the network; `httpx` uses in-process ASGI transport | grep of `httpx`/`socket`/`urlopen` |
| Repo-invariant tests (import no package module) | 46 files (CLAUDE.md size/rotation, key-files split, sdist allowlist, version sync, CI command binding, schema baseline transcription, fidelity artifacts) | grep of imports |
| Ruff | `src/` clean; `tests/` 292 pre-existing errors (274 F401), not gated | `uv run ruff check src/`, `uv run ruff check tests/` |
| Type checking | none configured, none installed | `pyproject.toml`, `.github/`, `.venv` |

Tests by covered area (a file counts in every area it imports): tools 253,
storage 143, parser 109, server 86, config 84, retrieval 49, cli 39, security
17, evidence 11, encoding 10, runtime 10, summarizer 7, counter 7,
investigator 4, progress 4, embeddings 3, groq 3.

## 5. CI/CD

| Workflow | Trigger | What it does | Gates merge? |
|---|---|---|---|
| `test.yml` | push main, PR, dispatch | 2 OS x 4 Python = 8 legs, `uv sync --locked --group dev --extra watch`, `pytest -n 4 --dist loadfile --cov-fail-under=74`; `lint` job `ruff check src/`; sdist tar grep for credential paths on Linux legs | NO (advisory) |
| `replay.yml` | push main, PR | indexes self, `benchmarks/replay/run_replay.py --gate 0.02` (nDCG/MRR/Recall@k, 2% relative drop) | NO |
| `health-radar.yml` + `-comment.yml` | PR | sticky health-radar comment via in-repo action pinned to jcodemunch 1.88.0 | informational |
| `benchmark.yml` | Mon 07:00 UTC, dispatch, push on 3 paths | clones pinned corpora, re-runs token benchmark, `::warning` on drift; says it does not gate | NO |
| `sign-release.yml` | release published | sigstore-python signs wheel + sdist, uploads bundles | n/a |

Branch protection on `main`: `required_status_checks.contexts == ["license/cla"]`,
`strict: false`, `enforce_admins: false`. **The 8-leg test matrix, lint and
replay are not required checks.** All actions SHA-pinned. Dependabot: uv +
github-actions, weekly, `open-pull-requests-limit: 0` (security-only). No
CodeQL, pip-audit, bandit, mypy or pyright anywhere.

## 6. Security posture

- `SECURITY.md`: 398 lines, 19 sections; "Background behavior, fully disclosed"
  is the compliance surface after the 2026-06-10 PyPI quarantine. **No section
  is a threat model** (grep for "threat model" across SECURITY.md, README.md,
  docs/: none).
- Untrusted repo content during indexing (`security.py`): `validate_path`
  (resolve + commonpath), `is_symlink_escape` (unresolvable = escape, symlinks
  not followed by default), secret-file classifier by name/shape only,
  verified `CACHEDIR.TAG` signature, binary sniff (extension + NUL in first
  8 KB), `safe_decode(errors="replace")`, caps `max_file_size` 512,000 B,
  `max_index_files` 10,000, `max_folder_files` 2,000, `response_max_bytes` 1 MiB.
- Trusted folders: `trusted_folders: []` with `trusted_folders_whitelist_mode: True`;
  `index_folder._is_trusted` returns True when the list is EMPTY, so the
  whitelist is a no-op until configured (`tools/index_folder.py:367`).
- Response redaction: 15 compiled secret patterns + entropy floor 3.5 in
  `redact.py`, applied in the dispatcher; `get_file_content`, `get_symbol_source`,
  `get_context_bundle` are exempt and SECURITY.md says so.
- Trace ingestion redaction chokepoint `runtime/redact.py`, on by default.
- sdist leak guard: `tests/test_build.py` (3), `tests/test_sdist_exclusions.py`
  (5, root allowlist asserted in both directions), CI tar grep.
- `verify_package_integrity()` on every CLI invocation (5 ms fast path).
- HTTP: bearer token optional for the MCP transport (warning if unset on a
  non-loopback host); write routes return 503 without it and each has a second
  config gate defaulting off; per-IP rate limit off by default.
- Release signing: sigstore, forward-only from v1.108.22.
- Doc/code discrepancy found: SECURITY.md's limits table says "File count limit
  500 files"; code defaults are 10,000 / 2,000. Logged, not fixed.

## 7. Configuration surface

- `config.py` `DEFAULTS` (lines 339-527): **94 keys**. About half carry an
  inline rationale comment; the rest are bare `key: value`.
- Documented by key name: 65 in `CONFIGURATION.md` or `CLI-AND-ENV.md`; 13
  more reachable via a documented env var; **16 in neither file nor CLAUDE.md**
  (`trusted_folders_whitelist_mode`, `skill_advisor_mode`, `server_output`,
  `server_output_threshold`, `worktree_base_path`, `git_root_identity`,
  `identity_mode`, `git_blame_enabled`, `summarizer_max_failures`, `cache_mode`,
  `summarize_from_docstrings`, `enrichment`, `render_diagram_viewer_enabled`,
  `mermaid_viewer_path`, `racket_definition_forms`, `racket_langs`; the last
  two are in README).
- `tests/test_docs_config_parity.py` checks documented -> exists, not
  exists -> documented. One-directional.
- `tool_surface` DEFAULTS `'full'`; `_fresh_config_content` writes `counter`
  for new installs and `upgrade_config` never back-injects it (the freeze in
  `surface_offer.py`).
- Defaults documented as deliberate: yes for the env-var invariants kept in
  CLAUDE.md "Env Vars" (27 rows with a stated prohibition or rationale); no
  for the 16 undocumented keys.

## 8. Git history, 2026-06-05 to 2026-09-03

| Fact | Value |
|---|---|
| Commits | 766 |
| By prefix | release 246, docs 139, fix 107, test 36, feat 34, chore 26, bench 17, refactor 7, ci 6, perf 3 |
| Tags created | 288 (v1.108.28 -> v1.108.316) |
| True reverts | 1 (`7189258`, v1.108.89 reverting the Antigravity install target) |
| Net diff | 769 files, +188,218 / -7,705 |
| Contributors | maintainer 682 commits (89%); 16 other humans; dependabot 10 |
| Most-changed files | CHANGELOG.md 418, CLAUDE.md 375, pyproject.toml 296, README.md 150, uv.lock 139, server.py 118 |
| Shipped regressions named in CHANGELOG (most recent five) | 1.108.309 `analyze_perf` latency delta vs a baseline that never measured it; 1.108.305 `diff_radar` `.get("composite", 0.0)`; 1.108.304 hydration witness; 1.108.302 Racket fabrication (#554); 1.108.301 `search_ast` empty tables for months (#553) |

Fixes outnumber features 3:1; releases are 2.7 per day; docs commits
outnumber fixes. The dominant change type is a correctness fix with a
regression pin.

## 9. Issues and PRs

- Open issues: **0**. Open PRs: **0** (queried 2026-09-03).
- Closed issues in window: 140; median time-to-close 0.39 days, max 22.3;
  103 from 36 external authors.
- Themes (hand-categorised titles): wrong results / false positives / absence
  claims ~42; install / config / hooks ~22; stale index / freshness / cache
  ~14; feature asks ~14; performance / hangs ~13; language / parser coverage
  ~12; docs / questions ~12; security / redaction ~6; Windows / WSL ~5.
- External PRs: 47 (27 merged, 20 closed unmerged; 8 of the merged and 5 of
  the closed are dependabot).
- Five most expensive incidents: PyPI quarantine 2026-06-10 (undisclosed
  persistence); #375 leaked stdio servers ~17 GB; #443 eight-day CLA stall on
  a security fix; CLAUDE.md at 200,543 chars refusing to load (2026-08-21);
  CI-env reproduce silently skipping 105 tests at exit 0 (2026-08-28).

## 10. Benchmark suite

Inventory of `benchmarks/` with the committed headline, what it needs, and
whether a test pins it. Harnesses marked RAN were executed this session on this
box (Windows, `PYTHONPATH=src`); any rewritten committed artifact was restored
with `git checkout --` and `benchmarks/` is clean.

| Harness | Measures | Committed headline | Needs | Pinned by | RAN |
|---|---|---|---|---|---|
| `harness/run_benchmark.py` + `tasks.json` -> `results.md`, `jcm_reference.json`, `provenance/measured.json` | tokens per task: `search_symbols`+`get_symbol_source`x3 vs read-all and grep-top-3, 5 queries x 3 repos | grand 24,249 jcm vs 664,975 grep-top-3 (96.4%, 27.4x) vs 5,658,685 read-all (233.4x); captured 2026-08-25 at 1.108.297; corpora pinned express `1faf228`, fastapi `a64dfbb`, gin `75ccf94` | network clones, tiktoken; no API keys | `tests/test_benchmark_reference.py` (30), `test_provenance.py` | no (network) |
| `harness/run_rag_baseline.py`, `run_odysseus_compare.py` | same corpus vs LangChain RAG / Odysseus | RAG best chunk 3.2x / 1.3x / 2.5x; Odysseus 1.2x / 0.2x / 0.9x (bold in METHODOLOGY = comparator cheaper) | langchain, faiss, MiniLM download; Odysseus server | `test_benchmark_reference.py` (no hardcoded constants, estimator absent) | no |
| `harness/capture_schema_baseline.py` -> `schema_baseline.json` | `tools/list` tokens per surface x profile | see §2 | tiktoken | `test_schema_budget.py` (6), `test_schema_baseline_transcription.py`, `test_counter_surface_stability.py` | yes, 1.140 s; live core_compact 3,972 / full_full 23,675 / counter 945, all inside the 5% tolerance; restored |
| `replay/` | nDCG / MRR / Recall@10 on 10 golden queries, self index | all 1.0 at 1.108.99 (`results/self_v1_75_0-golden.json`) | nothing | `test_replay_metrics.py`, `test_provenance.py`; CI `replay.yml --gate 0.02` | yes, 2.717 s, all 1.0, gate passed |
| `route_recall/` | Counter front door proposes the labelled action | human corpus 59 queries: route@1 71.2, @3 86.4, menu@10 78.0; holdout 44: route@1 65.9; emitted (rknighton corpus, 40): strict@1 30.0, cannot discriminate | nothing | `test_route_recall_artifacts_are_fresh.py` (re-runs), `test_catalog_moratorium.py` (route@1 >= 60 bar) | yes, 1.0-1.4 s each, digit-identical to committed |
| `route_binary_pilot/` | H3 vocabulary probe | 53.3% (chance), REFUTED, frozen | 3 clones | `test_route_binary_pilot_is_frozen.py` | no |
| `tier_switch/results.json` | cache break-even of a tier switch | full->standard 173.6 requests (864.2 at 100k history); full->core 4.2 | nothing | `test_tier_switch_cost.py` (15, properties not bytes) | yes, 0.923 s; live 173 (21 tokens lower per tier) |
| `codex_surface/` | net token effect on Codex CLI | NEGATIVE; kept finding: 86% of baseline input cached | codex CLI + auth | referenced in a docstring only | no |
| `rust_fidelity/results.json` | Rust extractor vs `syn` on ripgrep `3fce3b5` | 110 files, extra 0 / wrong_span 0 / undercount 0 / qual_mismatch 0, missing 156 (module 126, macro 30), coverage 95.8% | cargo + clone | frozen: `test_rust_fidelity.py`, `test_rust_fidelity_artifacts.py` | frozen tests only |
| `racket_fidelity/` | Racket extractor vs expander; reader vs `read-syntax` | 211 files, extra 0 / wrong_span 0, source_coverage 89.7%; reader 761,009 nodes, 0 disagreements | `racket` (absent on this box) | frozen: `test_racket_fidelity.py`, `test_racket_reader.py`, `test_racket_fidelity_artifacts.py` | frozen tests only |
| `deadcode_eval/results-2026-08-03.json` | `get_dead_code_v2` definite FPs vs coverage oracle | cutoff 1.0: flagged 766, FP 482, fp_rate 0.6292 (lower bound) | full suite under `--cov` (~13 min) | `test_deadcode_eval_harness.py` (logic only) | no |
| `goldset/` -> `provenance/channel_accuracy.json` | `find_implementations` per-channel P/R | ast 0.818/1.0, duck 0.5/1.0, decorator 0.556/1.0 | nothing | `test_channel_accuracy.py` (re-runs in CI) | yes, 0.741 s, byte-identical |
| `cache_stability/results.json` | reshuffled share of `get_ranked_context` output | reshuffled_share 0.139, verdict `hold` (kill at 0.30) | nothing (indexes `src/` snapshot) | **none** | yes, 16.472 s; every number moved (reshuffled 0.196), verdict still `hold`; restored |
| `description_smells/scores.json` | tool descriptions vs arXiv:2602.14878 | 194 tools, 26.8% with >=1 smell (schema frame), 57.7% (paper frame) | nothing | `test_description_smells.py` (Purpose + Length only) | yes, 0.131 s, byte-identical |
| `token_baselines/v1.108.163.json` | per-release tokens-saved snapshot | one file, 2026-07-23; no latency keys | live session | `test_analyze_perf_totals.py` | no |
| `swebench/` | protocol + power table | PARKED; n=200 detects +12 pt 86% of the time; needs 120 GB | Claude Code runs, rented box | none | power table only, 1.579 s |
| `anchor/`, `offload/`, `calibration/` | telemetry cue redelivery; offload label rate; planted-query calibration | anchor: nothing committed; offload self-run marked non-reproducible; calibration 2 pos / 2 neg | telemetry.db / nothing / nothing | `test_offload*.py`, `test_verdict_coverage_calibration.py` | no |

Twenty pinning test files (364 tests) were run together: 364 passed, 0 failed,
18.19 s wall.

**Drift found:** `benchmark.yml` runs 33368105928 (2026-08-31) and 33713310141
(2026-09-03) both printed `::warning::published benchmark numbers have moved`:
jcm tokens express 5,009 -> 5,024, fastapi 11,353 -> 10,771, gin 7,887 -> 7,645
(CI grand 23,440 vs committed 24,249). Baselines unchanged. The workflow warns
and never fails, so nothing is red. Re-sync is
`python benchmarks/harness/run_benchmark.py --out benchmarks/results.md --reference`
plus the hand-mirrored README/METHODOLOGY rows (`test_provenance.py` catches a
miss). Logged, not fixed.

**Full-run cost:** the token benchmark is 39 s end to end on the CI runner
(clone 7 s, index 13 s, run 7 s); every no-network harness above sums to about
28 s plus 18 s of pin tests. Rust fidelity (cargo build + clone), Racket
fidelity (211 + 725 files through `racket`), RAG/Odysseus and codex_surface are
UNKNOWN in duration; deadcode_eval is bounded below by the ~13-minute coverage
run. The 20-minute cap in the brief was therefore met by running the
no-network subset; the full run including oracles and comparators is UNKNOWN.

## 11. Things found that contradict or qualify the repo's own docs

1. `README.md` and `results.md` carry a 27.4x figure the weekly benchmark has
   been warning is stale since 2026-08-31 (jcm tokens moved on all three repos,
   two of them DOWN, i.e. in our favour). The warning-only workflow means the
   README and the artifact can disagree indefinitely.
2. `SECURITY.md` limits table says "File count limit 500 files"; code defaults
   are `max_index_files` 10,000 and `max_folder_files` 2,000.
3. `CLAUDE.md` "Tests:" line says CI is the gate that four releases skipped;
   branch protection shows only `license/cla` is REQUIRED. Tests, lint and
   replay can be red and the merge button still works.
4. "Whitelist mode by default" (`CLAUDE.md` Env Vars, `trusted_folders`) is
   inert with the shipped empty list.
5. `cache_stability/results.json` is quoted in a `hold` verdict and pinned by
   nothing; a re-run moved every number.
6. `tier_switch/results.json` says 173.6 and README rounds to 174; the live run
   says 173. Within noise, and no test pins the artifact bytes.
7. `CLAUDE.md` quotes live core_compact at 3,998 of 4,000; the capture script's
   `--breakdown` count says 3,972. The two count differently; the test is the
   authority, and the capture output must not be quoted as the gate value.
8. The cold `search_text` latency (8.8 s) is 3.4x the cold `search_symbols`
   (2.6 s) on the same index in the same session. Not documented anywhere.
9. (Post-report, user-supplied) `https://jcodemunch.com/versus.php` describes
   jCodeMunch as v1.104.1 with 4,228 tests; the tree is 1.108.316 with 9,174
   collected. Marketing copy, not touched; the page is the source for
   `NICHE.md` §2a. The companion ChatGPT digest could not be read by fetch
   (title only: "24 live competitors fully reconciled").
