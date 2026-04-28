# Changelog

All notable changes to jcodemunch-mcp are documented here.

## Unreleased

### Added
- Markdown documentation files (`.md`, `.markdown`, `.mdx`) are now indexed
  as text-only files. They are available to `search_text`, `get_file_content`,
  `get_file_tree`, and file summaries without emitting heading symbols or
  affecting code graph analysis.

## [1.80.1] — 2026-04-26 — Scala 3 parser fix (PR #262)

### Fixed
- **Scala 3 symbol extraction** (#262, irreversible-paths). The Scala
  language spec only matched `function_definition` nodes, silently
  dropping abstract defs (`function_declaration`), fields
  (`val_definition` / `var_definition`), and type aliases
  (`type_definition`). It also assumed the identifier field was named
  `name`, but tree-sitter-scala uses `pattern` for val/var nodes.
  `SCALA_SPEC` now declares the additional node types and the
  `pattern` name field. On a real Scala 3 + Spring Boot project the
  indexed symbol count went from 734 → 1,475 (≈2x).
- New `tests/test_scala_parser.py` covers Scala 3 significant-
  indentation syntax: traits, classes, objects, enums, vals, vars,
  type aliases, abstract defs, and nested methods.

## [1.80.0] — 2026-04-25 — closes the 7-release telemetry initiative

### Added — Embedding drift detector
- **`retrieval/embed_drift.py`.** Pins a 16-string deterministic canary
  (`CANARY_STRINGS`, append-only) and embeds it with the active provider.
  Snapshot persists to `~/.code-index/embed_canary.json` as
  `{provider, model, dim, captured_at, strings, vectors}`.
  `check_drift(threshold=0.05)` re-embeds the canaries and compares
  cosine similarity to the snapshot; alarm fires when max cosine
  distance exceeds the threshold.
- **`check_embedding_drift` MCP tool.** First-time use with `capture=True`
  pins the canary; subsequent calls run the drift check. `force=True`
  re-pins (use after intentional provider/model upgrades). Catches
  silent provider model changes (Gemini revs, OpenAI weight updates,
  bundled-ONNX swaps) that quietly degrade hybrid retrieval.
- Reuses `embed_repo._detect_provider` so the canary tracks the live
  encoder (no duplicate provider detection).
- Registered in canonical names, standard tier, default
  `tool_tier_bundles`, init template, CLAUDE.md Utilities snippet,
  EXCLUDED_FROM_STRICT, AUTO_WATCH_EXCLUDED.
- 17 tests in `tests/test_embed_drift.py` covering canary contract
  stability, capture idempotency, force re-pin, no-provider error
  path, zero-drift / large-drift / minor-noise paths, the wrapping
  tool's capture/check flow, cosine helper edge cases, and server
  registration.

### Verified
- v1.75.0 replay benchmark gate passes 1.0/1.0/1.0 — no nDCG/MRR/Recall
  regression.

### Telemetry initiative complete (v1.74.0 → v1.80.0)
1. v1.74.0 — telemetry foundation (per-tool latency + analyze_perf + opt-in SQLite sink)
2. v1.75.0 — retrieval confidence + token-accounting baseline
3. v1.76.0 — replayable benchmark + retrieval-quality harness
4. v1.77.0 — per-symbol freshness markers
5. v1.78.0 — ranking ledger
6. v1.79.0 — online weight tuning
7. v1.80.0 — embedding drift detector (this release)

## [1.79.0] — 2026-04-25

### Added — Online weight tuning (consumes the v1.78.0 ledger)
- **`retrieval/tuning.py` — `WeightTuner`.** Reads the `ranking_events`
  table for a given repo, splits events by signal (`semantic_used`,
  `identity_hit`), and proposes a `±0.05` step on the corresponding
  weight when the mean confidence delta between groups exceeds 0.05.
  Bounded — `semantic_weight` clamps to `[0.1, 0.8]` and
  `identity_boost` clamps to `[0.5, 2.0]`. Defaults to a `min_events=50`
  threshold so small samples can't move weights.
- **`tune_weights` MCP tool.** Runs the tuner over every repo in the
  ledger (or a single `repo`). `dry_run=True` proposes deltas without
  writing the file; `explain=True` includes the per-signal correlations
  used in the proposal. Registered in canonical names, standard tier,
  default `tool_tier_bundles`, init template, CLAUDE.md Utilities
  snippet, EXCLUDED_FROM_STRICT, AUTO_WATCH_EXCLUDED.
- **`~/.code-index/tuning.jsonc`.** Per-repo overrides persist as
  `{repos: {<repo_id>: {semantic_weight?, identity_boost?,
  learned_from_events, captured_at}}}`. Auto-generated header notes
  that `tune_weights` will overwrite per-repo entries on the next run.
- **Query-time application.** `search_symbols` resolves the active
  `semantic_weight` via `tuning.get_semantic_weight(repo)` when the
  caller passed the default `0.5`; explicit non-default values always
  win. The override is only applied when semantic / fusion modes are
  actively engaged — pure-BM25 calls are untouched.
- 17 tests in `tests/test_weight_tuning.py` covering get_semantic_weight
  precedence (explicit / override / default), tuner gating
  (insufficient events, no-signal, semantic-helps, semantic-hurts,
  dry-run), JSONC round-trip, tune_weights tool (no repos, all repos,
  --explain), server registration, and query-time override.

### Verified
- v1.75.0 replay benchmark gate passes 1.0/1.0/1.0 — no nDCG/MRR/Recall
  regression. The tuner is additive: with no learned overrides on disk
  the default behavior is identical.

## [1.78.0] — 2026-04-25

### Added — Ranking ledger (data-collection only; no behavior change)
- **`ranking_events` table in `~/.code-index/telemetry.db`.** Schema:
  `(ts, repo, tool, query_hash, query, returned_ids[json], top1_score,
  top2_score, confidence, semantic_used, identity_hit, repo_is_stale)`.
  Indexed on `repo`, `ts`, `query_hash` for fast aggregation.
- **`record_ranking_event(...)` in `storage/token_tracker.py`.** Append
  helper that no-ops when `perf_telemetry_enabled` is false. Wired into
  every retrieval path: `search_symbols` (BM25, semantic/hybrid, fusion),
  `plan_turn`, both `get_ranked_context` paths. Each call logs the
  query, the top returned IDs (cap 50), the score gap, the confidence
  value, and signals for whether semantic and identity-match channels
  fired.
- **`extract_ledger_features(scored_results)` helper** in
  `retrieval/confidence.py` — uniform `(top1_score, top2_score,
  identity_hit)` extraction so each retrieval tool feeds the ledger
  with the same feature shape.
- **`analyze_perf(ledger=True)` view.** New optional flag pulls
  `ranking_events` and returns `ranking_ledger` summary with
  per-repo (events, avg_confidence, identity_hits, semantic_used,
  stale_events) and per-tool (events) aggregates. Window filter
  (1h/24h/7d/all/session) applies to the ledger query.
- **`ranking_db_query(...)` reader** mirrors `perf_db_query` for the
  ledger table.
- 12 tests in `tests/test_ranking_ledger.py` covering feature
  extraction, persistence on/off, query-hash stability, repo/tool
  filters, ledger summary aggregation, and search_symbols invocation
  capture.

This release stores data; v1.79.0 will train per-repo ranking weights
from it.

## [1.77.0] — 2026-04-25

### Added — Per-symbol freshness markers
- **`_freshness ∈ {fresh, edited_uncommitted, stale_index}`** on every
  symbol returned by `search_symbols` (BM25, semantic, fusion paths),
  `get_symbol_source` (single + batch), `get_context_bundle` (single +
  multi), and both `get_ranked_context` paths. Lets agents decide when
  to trust a result and when to reindex first.
- **`retrieval/freshness.py` — `FreshnessProbe`.** Per-tool-call helper
  that classifies each result's file:
  * `stale_index` — index SHA differs from live `git rev-parse HEAD`
    (whole index is behind, every result inherits the marker).
  * `edited_uncommitted` — repo SHA matches but the file's on-disk mtime
    is newer than the indexed mtime (file was edited since indexing).
    Compares against `CodeIndex.file_mtimes` per-file when available;
    falls back to the index-wide `indexed_at` timestamp.
  * `fresh` — neither condition triggered.
  Probe caches `git HEAD` and per-file mtime stats so classifying many
  symbols in one call stays cheap.
- **Confidence integration.** `attach_confidence` now receives
  `is_stale=probe.repo_is_stale` from each retrieval path, so the
  freshness component of the confidence score finally reflects real
  index drift instead of always reading 1.0.
- **`_meta.freshness` summary** on each result envelope:
  `{fresh, edited_uncommitted, stale_index, repo_is_stale}` counts
  across the returned set — a one-glance health check.
- 11 tests in `tests/test_freshness.py` covering probe construction,
  SHA mismatch, per-file mtime comparison, in-place annotation,
  end-to-end through `search_symbols` (with a forced post-index edit)
  and `get_symbol_source`.

## [1.76.0] — 2026-04-25

### Added — Replayable benchmark + retrieval-quality harness
- **`benchmarks/replay/` module.** Replayable retrieval-quality fixtures
  + scoring metrics. Fixtures live in `benchmarks/replay/fixtures/*.json`
  with shape `{name, repo, repo_sha, queries: [{query, expected_top_k}]}`.
  Each fixture is the contract a release must keep meeting.
- **`metrics.py` — nDCG@k, MRR@k, Recall@k.** Pure-Python, dependency-free
  implementations. Binary-relevance nDCG normalized by ideal DCG;
  Recall = hits in top-k / total relevant; MRR = reciprocal rank of
  first relevant within top-k. `aggregate()` averages per-query metrics
  across a fixture.
- **`run_replay.py` — harness with regression gate.** Runs every query
  in a fixture through `search_symbols`, computes per-query and overall
  metrics, optionally writes `benchmarks/replay/results/{fixture}-v{X}.json`.
  `--baseline X.Y.Z --gate 0.02` exits non-zero if any aggregate metric
  drops by more than 2% (configurable) vs the saved baseline. Missing
  baseline counts as a pass with a "first run" note so new fixtures
  don't break CI.
- **`self_v1_75_0` seed fixture (10 queries) locked at 1.0 nDCG/MRR/Recall.**
  Covers every major surface — `search_symbols`, `compute_confidence`,
  `analyze_perf`, `record_tool_latency`, `_State`, `ndcg_at_k`,
  `build_identity_channel`, `ENTRY_POINT_DECORATOR_RE`, etc. Future
  releases run `run_replay.py --baseline 1.75.0 --gate 0.02` against
  this fixture as a CI regression guard.
- 19 tests in `tests/test_replay_metrics.py` (metric math, harness
  gate logic, fixture-shape contract, baseline lock).

## [1.75.0] — 2026-04-25

### Added — Retrieval confidence + token-accounting baseline
- **`_meta.confidence` (0–1) on retrieval results.** New
  `retrieval/confidence.py` exposes `compute_confidence` /
  `attach_confidence`. The score is a weighted geometric mean of four
  sub-signals: top-1 vs top-2 score gap, top-1 absolute strength, identity
  match presence, and result freshness. Wired into all three
  `search_symbols` paths (BM25, semantic/hybrid, fusion), `plan_turn`, and
  both `get_ranked_context` paths. Agents can read the number to gate
  follow-up retrieval calls — high-confidence results don't need a second
  query, low-confidence results suggest widening or asking the user.
- **Token-accounting baseline harness.**
  `benchmarks/harness/capture_token_baseline.py` snapshots the current
  session's `get_session_stats` + `latency_stats` to
  `benchmarks/token_baselines/v{VERSION}.json`. Schema: per-tool
  `{calls, tokens_saved, p50_ms, p95_ms, max_ms}` plus a session summary.
- **`analyze_perf(compare_release="...")`.** New parameter loads a saved
  baseline and returns `baseline_diff` — per-tool deltas in tokens_saved
  and latency vs the live session. Used to catch regressions in
  compression ratio or per-tool latency drift across releases. Missing
  baseline is reported via `baseline_meta.found=false` rather than an
  error.

## [1.74.0] — 2026-04-25

### Added — Telemetry foundation (kicks off the multi-release telemetry initiative)
- **Per-tool latency tracking.** Every `call_tool` invocation is now timed
  (high-resolution `time.perf_counter`) and recorded into a per-tool ring
  buffer (cap 512 entries) inside `_State`. `get_session_stats` now returns
  a `latency_per_tool` field with `{count, p50_ms, p95_ms, max_ms, errors,
  error_rate}` for every tool that ran during the session.
- **`analyze_perf` MCP tool.** New utility tool surfacing latency + cache
  telemetry. Defaults to the in-memory session ring; pass
  `window=1h|24h|7d|all` to query the persistent perf SQLite db. Returns
  the `top` slowest tools by p95 alongside the coldest cache hit-rates,
  scoped optionally to a single `tool` name. Registered in canonical tool
  names, standard tier bundle, default `tool_tier_bundles`, init template,
  and the CLAUDE.md Utilities snippet category. Excluded from strict
  freshness mode and auto-watch (read-only telemetry).
- **Opt-in perf SQLite sink.** When `perf_telemetry_enabled: true` in
  config (or env `JCODEMUNCH_PERF_TELEMETRY=1`), the latency recorder also
  appends rows to `~/.code-index/telemetry.db` (`tool_calls(ts, tool,
  duration_ms, ok, repo)`, indexed on `tool` and `ts`). Rolling cap via
  `perf_telemetry_max_rows` (default 100k). Disabled by default — every
  session tracks latency in memory but only the explicit opt-in writes a
  durable file.

### Fixed
- (Carried from v1.73.2): `get_repo_health` no longer raises NameError on
  decorated symbols.

## [1.73.2] — 2026-04-25

### Fixed
- **`get_repo_health` raised `NameError: _ENTRY_POINT_DECORATOR_RE is not defined`
  whenever any analysed symbol carried a decorator.** The constant was imported
  correctly as `ENTRY_POINT_DECORATOR_RE` (no leading underscore) from
  `parser/context/_route_utils.py`, but two call sites referenced
  `_ENTRY_POINT_DECORATOR_RE` (typo). Fixed in
  `tools/find_dead_code.py:74` and `tools/get_dead_code_v2.py:214`.
  Existing tests didn't catch the regression because their fixtures used
  bare functions; new test `test_no_nameerror_when_decorators_present`
  exercises the decorator-skip branch with an `@app.route` symbol.

## [1.73.1] — 2026-04-23

### Fixed
- **Register `get_watch_status` in tier bundles, config template, and CLAUDE.md
  snippet.** v1.73.0 added the tool to `_CANONICAL_TOOL_NAMES` and
  `_TOOL_TIER_STANDARD` but missed `DEFAULTS["tool_tier_bundles"]["standard"]`,
  `generate_template()`'s `all_tools` list, and the Utilities category in
  `_generate_claude_md_snippet()`. Effect on 1.73.0 users: `get_watch_status`
  was invisible in the `standard` tier, absent from new config templates, and
  absent from generated CLAUDE.md snippets. CI now guards this via
  `test_all_canonical_tools_accounted_in_tier_bundles`.

## [1.73.0] — 2026-04-23

### Added
- **`watch-all` subcommand + login-service installer.** Auto-discovers every
  locally-indexed repo via the existing `IndexStore.list_repos()` registry and
  keeps all of them fresh with one `WatcherManager`. Rediscovers on a
  configurable interval (default 30s) so repos added to the registry later are
  picked up without a restart. Skips GitHub repos (empty `source_root`) and
  repos whose source_root has been deleted.
- **`watch-install` / `watch-uninstall` / `watch-status` subcommands.** Install
  the watcher as a login service on all three platforms from one command:
  systemd user unit (Linux), launchd agent (macOS), Task Scheduler task
  (Windows). Service shells `sys.executable -m jcodemunch_mcp watch-all`, so it
  inherits the active virtualenv and skips any per-event `uvx` overhead.
- **`get_watch_status` MCP tool.** Surfaces per-repo staleness, in-progress
  reindexes, and service health to agents — callable before acting on anything
  that assumes a fresh index.

## [1.72.0] — 2026-04-21

Correctness + reach release: fixes six tier-1 MUNCH encoders that had shipped
against imagined response shapes (silent data loss in `get_blast_radius`,
`find_importers`, `find_references`, `get_signal_chains`, `get_tectonic_map`,
`get_dependency_cycles`), and adds generic monorepo/`extends`-chain support to
the tsconfig alias loader so path aliases resolve in any workspace layout.

### Fixed
- **MUNCH tier-1 encoder alignment** (#256, credit @MariusAdrian88). Six
  encoders realigned to the actual tool responses. Encoding IDs bumped
  (`br1→br2`, `fi1→fi2`, `fr1→fr2`, `sc1→sc2`, `tm1→tm2`, `dc1→dc2`); old
  payloads were lossy and are retired cleanly.
  - `get_blast_radius`: `symbol` now encoded as a nested dict (was serialized
    as Python repr string); `confirmed`/`potential` tables replace the
    always-empty `affected_symbols`/`importer_files`; correct scalar names
    (`importer_count`, `confirmed_count`, `potential_count`, `overall_risk_score`).
  - `find_importers`: columns `[file, specifier, has_importers]` (was invalid
    `line`/`column`); scalar `file_path` (was `file`); batch mode preserved via
    JSON blob.
  - `find_references`: nested `references[].matches[]` now flattened and
    regrouped round-trip; empty-match groups preserved via `__empty_groups__`.
  - `get_signal_chains`: all three response modes supported (discovery, lookup,
    no-gateway) with mode-specific `_meta` dispatch; correct column set
    including `gateway_name`, `chain_reach`, `depth_from_gateway`.
  - `get_tectonic_map`: correct plate/drifter columns (`plate_id`, `anchor`,
    `cohesion`, `majority_directory`); `isolated_files` moved to JSON (is a
    `list[str]`, not a dict table); optional plate fields pruned on decode.
  - `get_dependency_cycles`: `list[list[str]]` shape now transformed via
    `\x1f` separator (previously silently skipped by dict-only row guard).

### Added
- **`__stypes` type preservation in `schema_driven.py`** (#256). Non-string
  scalar types (int, float, bool) are hinted on encode and coerced on decode,
  reaching parity with `generic.py`. Old payloads without `__stypes` fall back
  to string decoding — backward-compatible.
- **Generic tsconfig discovery walker** (#257, credit @bertPB). Replaces the
  previous root-only lookup with a depth-limited (≤5) walker that finds all
  `tsconfig*.json` / `jsconfig*.json` files and follows each file's `extends`
  chain. Handles TS 5+ array-form `extends`, circular chains (via `seen_cfg`
  dedup), and package refs like `@tsconfig/recommended` (boundary-checked via
  `relative_to(root)`). Nx/Turborepo layouts (`libs/`, `services/`,
  `modules/`) and repos that centralize aliases in `tsconfig.base.json` /
  `tsconfig.paths.json` now resolve correctly. Skip list extended with
  `.turbo` and `.vercel`.

## [1.71.0] — 2026-04-21

One-line CLAUDE.md / AGENT.md via the new `jcodemunch_guide` tool. Resolves
issue #255 (credit @rsubr).

### Added
- **`jcodemunch_guide` tool.** Returns the version-current policy snippet —
  the exact text `jcodemunch-mcp claude-md --generate` emits, plus the running
  `version`. Agents can now keep a one-liner such as
  `"Call jcodemunch_guide and strictly follow its instructions."` in their
  CLAUDE.md / AGENT.md and never hand-edit it again when the tool surface
  changes. Force-included alongside `set_tool_tier` / `announce_model`, so it
  can't be hidden by `disabled_tools` or tier filtering. No repo context
  needed; idempotent.

## [1.70.0] — 2026-04-19

Context-optimization release — default detail level and token-budget bug fix.
Minor bump (not 2.0) because the changes are forward-compatible: explicit
`detail_level` values still honored, `token_budget` semantics unchanged in
signature. Agents that never passed `detail_level` see smaller responses on
broad discovery queries.

A/B benchmark (self-indexed, 16 discovery queries, `max_results=10`):
- **21.3% total token savings** vs prior default (median 18.9%, max 33.7%)
- **100% `token_budget` compliance** in full mode (was overshooting 5-20x)
- **Narrow queries (`max_results<5`) identical** to prior behavior

Results: `benchmarks/results_v1.70.0.md`. Reproduce: `PYTHONPATH=src python benchmarks/harness/ab_v1_70_0.py`.

### Changed
- **`search_symbols(detail_level=)` default is now `"auto"`.** Resolves to
  `"compact"` for broad discovery (no `token_budget`, no `debug`, `max_results
  >= 5`) and to `"standard"` otherwise. Explicit `detail_level` values are
  always honored — never silently overridden. Agents that didn't pass
  `detail_level` now get the cheapest representation by default for discovery
  queries, matching the published guidance. CLI (`cli/cli.py`) and benchmark
  harness pin `detail_level="standard"` explicitly to preserve output shape.
- **`meta_fields` documented default corrected to `[]`.** Runtime default in
  `config.py` has always been `[]` (strip `_meta` entirely). `CONFIGURATION.md`
  previously showed `null`, which caused agents to emit `_meta` fields users
  weren't opting into. Code is canonical; docs now match.
- **`use_ai_summaries` documented default corrected to `"auto"`** with the
  actual type (`bool or str`). Runtime accepts `"auto"`/`true`/`false`; docs
  previously reported a stale `true` default.

### Fixed
- **`search_symbols` full-mode respects `token_budget`.** The packer previously
  saw each result's pre-materialization `byte_length` (signature + summary
  only), then full-mode appended `source`, `docstring`, and `end_line` AFTER
  packing, overshooting declared budgets by 5-20x on real symbols. Full
  payload is now materialized before packing via a shared
  `_materialize_full_entry` helper; `byte_length` reflects the actual payload
  the caller will see. Applied uniformly to the main BM25 path, the semantic
  (embedding) path, and the fusion (WRR) path — all three had the same bug.
  Fuzzy-path entries materialize inline instead of via a trailing pass.

### Tests
- `tests/test_search_symbols_defaults.py` (11 new tests) — auto-resolution
  decision matrix, explicit-override honoring, full-mode budget adherence
  across BM25/fusion paths, edge cases (empty query, zero results, cache warm).
- `tests/test_docs_config_parity.py` — documented defaults in
  `CONFIGURATION.md` now parity-checked against the `DEFAULTS` dict in
  `config.py`. CI will fail if a default drifts between code and docs.
- `tests/test_schema_budget.py` — schema-token count tracked per
  `(tool_profile, compact_schemas)` against `benchmarks/schema_baseline.json`
  with a 5% drift tolerance; `core + compact_schemas=True` asserted under
  4000 tokens (v2-success-criterion guardrail).

## [1.63.1] — 2026-04-19

Hotfix — `get_hotspots` (and therefore `get_repo_health`, which aggregates
it) crashed with `UnboundLocalError: local variable 'top' referenced before
assignment` on any repo where `source_root` wasn't a git worktree — most
notably `index_repo` extracts living in the cache dir, where this is the
common case, not the exception.

### Fixed
- **`get_hotspots` no-git path de-indented (closes crash in `get_repo_health`).** The candidate-building block was nested inside the `if rc_check == 0:` branch, so when `git rev-parse --git-dir` failed (no `.git` reachable from `source_root`), the block never ran and `top` was never bound. The existing test suite masked this because `tmp_path` on the maintainer's machine nests inside the project's own git worktree, so `rev-parse` accidentally walked up and succeeded. Block is now de-indented to function-body level; a regression test monkeypatches `_run_git` to force the no-git path regardless of where tmp_path lives.

## [1.63.0] — 2026-04-19

Python import resolution — fixes under-resolution on layouts where the
effective source root is injected at runtime (conftest.py sys.path shims,
`PYTHONPATH`, setuptools `package_dir` with nested roots). Minor bump
because resolution behavior changes on affected repos; no wire-format or
session-state changes.

### Fixed
- **`resolve_specifier` handles runtime-injected Python source roots (closes #252).** On layouts where a nested package dir (e.g. `src/agent_platform/`) is simultaneously a real package AND a sys.path entry, `_python_source_roots` saw only the outer parent as a root, so specifiers like `shared.core.runtime` never resolved — causing `find_importers`, `find_references`, `get_blast_radius`, `get_dependency_graph`, and `find_dead_code` to under-report on affected files. The resolver now falls back to a cached `package_basename → parent_dirs` index and retries against the parents of any package whose name matches the specifier's first dotted segment. Scoped by concrete first-segment evidence in the import itself, not a broad suffix sweep — zero false-positive risk, memory bounded by distinct package-name count. Credit to @vaionetalex (skleung.uk@gmail.com) for the diagnosis and repro.

## [1.62.0] — 2026-04-19

Audit remediation — wire-format and session-state changes. Minor bump
because the MUNCH generic encoder gains new escape sequences; old
payloads still decode but new payloads are forward-only.

### Fixed
- **MUNCH generic encoder escapes newlines in quoted scalars (F1).** `assemble` joins sections with `\n\n` and `split_sections` splits on the same delimiter. Scalars containing a blank line (docstrings, stacktraces, multi-paragraph summaries routed through the generic fallback) truncated mid-record on decode; blocks after the truncation were misclassified as tables. `\\`, `\n`, `\r` are now escaped on write and reversed on read. Old un-escaped payloads still decode cleanly — the decoder only consumes known escape sequences.
- **MUNCH schema embed escapes separator characters (F11).** The `__tables` embed used `:` and `|` as separators with no escape for keys or column names containing those characters. Keys like `"stats:by_file"` and columns like `"col|weird"` are now percent-encoded (`%3A`, `%7C`, `%2C`, `%25`) on write and reversed on read.
- **Nested-table decode no longer silently drops data (F5).** When a parent key carried both a flattened table and a heterogeneous sibling that fell back to `__json.<parent>`, the decoder restored the parent as the JSON scalar first and the nested-table pass then silently skipped the assignment behind an `isinstance(..., dict)` guard. The scalar is now wrapped in `{"_scalar": ...}` so the nested table survives.
- **Per-session tier tracking switches to WeakKeyDictionary (F2 + F3).** `id()` can be reused after GC, so a freed session's tier override could be inherited by a newly-allocated replacement at the same address; separately, the LRU cap of 256 silently reset a live session's tier to config default under session-churn load. Both addressed by keying on a per-session UUID tracked in a WeakKeyDictionary: entries disappear exactly when the session is collected, no cap, no eviction, no id() reuse. Constant `_SESSION_TIER_CAP` removed.
- **`mermaid_viewer_path` config accepts bare command names (F6).** Configuring a bare command (`"mmd-viewer"`) was rejected by the strict executable-file-on-disk check and never reached `shutil.which`. Bare names (no path separator) now defer to `shutil.which`; absolute and relative paths still go through the strict check so a typo in a configured path isn't silently replaced by something on `PATH`.

### Changed
- Removed `_SESSION_TIER_CAP` constant and LRU-cap eviction from `server.py`. Test helpers and docs updated accordingly.

## [1.61.1] — 2026-04-19

Audit remediation — low-risk fixes. No wire-format or session-state
changes; all installs can upgrade without migration.

### Fixed
- **`model_tier_map` substring match normalizes both sides (F4, closes #249).** Layer-3 substring matching compared raw config keys against the normalized incoming model id, so provider-prefixed entries like `"anthropic/claude-haiku"` silently failed to match `"claude-haiku-4-5"`. Keys are now normalized the same way incoming ids are before the substring check; longest-match semantics preserved.
- **Bearer-token redaction requires the `Authorization` anchor (F9).** The previous pattern treated the Authorization prefix as optional, so prose containing the word `Bearer` followed by 20+ word-characters (docstrings, CLI help, example text) tripped redaction. We lose the naked-token-in-prose detection path in exchange for no false positives on ordinary documentation.
- **Redaction depth cap collapses all scalars past depth 20 (F10).** Past the cap, strings under 16 characters were returned raw on the theory that short strings cannot hold a secret. Recognizable credential prefixes (AWS access-key, GitHub-token prefixes) fit under that limit, so the carve-out leaked them. Any scalar or container at the cap now becomes the `[REDACTED:depth_exceeded]` sentinel.
- **`render_diagram` edge resolution picks the correct direction (F7).** On mutually-recursive pairs a symbol appears in both `callers` and `callees` with different `resolution` tiers. The edge-style lookup scanned the two lists concatenated and picked whichever id matched first, which surfaced the caller's resolution on every edge touching that symbol. Caller vs callee edges now consult their own list.
- **`render_diagram` honors `max_nodes` in the `impact_by_depth` branch (F8).** The depth-bucketed rendering ignored `max_nodes` entirely and emitted every file at every depth. Budget is now tracked per bucket; excess accumulates into `pruned_count` instead of blowing past the caller's cap.

## [1.61.0] — 2026-04-18

### Added
- **Per-session tier state makes `adaptive_tiering: true` safe under HTTP (#253).** The process-global `_session_tier_override` is now a session-keyed `OrderedDict` of overrides (LRU-capped at 256 entries). The key is the MCP session's `session_id` when present, otherwise `id(request_context.session)`; stdio and tests fall through to a `"__default__"` sentinel so their behavior is byte-for-byte unchanged. One HTTP client's `plan_turn(model=...)` or `set_tool_tier(...)` call no longer leaks into every other concurrent client on the same process. `set_tool_tier(None)` now evicts the entry rather than storing `None`. New `_reset_session_tiers()` test helper; five new tests cover cross-session isolation, `session_id` preference over identity, LRU eviction, and `None`-evicts semantics.

### Changed
- **HTTP + `adaptive_tiering: true` is supported again; v1.60.1's `HttpAdaptiveTieringError` refuse-to-start removed (#253).** The guard existed only because the underlying state was process-global; with session-keyed state the misconfiguration no longer exists. The startup hook is now `_note_adaptive_tiering_transport(transport)` — an INFO log when adaptive_tiering runs under `sse` / `streamable-http`, purely for observability. Operators who intentionally rely on the hard-fail should pin v1.60.1.

## [1.60.1] — 2026-04-18

### Changed
- **`adaptive_tiering: true` + HTTP transport now refuses to start (#248).** Previously emitted a startup WARNING. Process-global tier state leaks across concurrent HTTP clients — one client's `plan_turn(model=...)` flip silently changes the tool surface for every other concurrent client on the same server. That's a misconfiguration, not a heads-up condition. The server now logs an ERROR and aborts via `HttpAdaptiveTieringError` (a `SystemExit` subclass) on both `sse` and `streamable-http` transports. Stdio is unaffected. Existing installs running `adaptive_tiering: false` (the default) see zero change. A per-session tier-state fix that would make HTTP + adaptive_tiering actually safe remains tracked for a future release.

### Fixed
- **`model_tier_map` substring match now picks the longest-matching key (#249).** Previously depended on dict iteration order — a config with both `"claude": "standard"` and `"claude-haiku": "core"` could resolve `claude-haiku-4-5` to either tier depending on config write order. Longest-match-wins makes specific entries beat broader ones regardless of order. Three regression tests cover both insertion orders and the broader-key-still-matches-non-haiku case.

## [1.60.0] — 2026-04-18

### Added
- **Tiered tool surface with runtime model-driven switching (#246, @MariusAdrian88)** — jcodemunch's 60+ tools now narrow per model at runtime, so request-capped plans stretch further when a small model is driving. Three tiers (`core` / `standard` / `full`); tier bundle contents are user-editable in `config.jsonc` (moved from hardcoded constants in `server.py`) with baked-in defaults as a fallback. New `model_tier_map` config maps model identifiers to tiers via layered matching: normalize (lowercase, strip provider prefix, strip bracket/date suffixes) → exact → glob → substring → `*` wildcard → `full` fallback. Opt-in `adaptive_tiering: true` enables runtime switching; defaults preserve prior behavior. New tools: `set_tool_tier(tier=...)` for explicit override, `announce_model(model=...)` for non-plan_turn flows; `plan_turn(model=...)` piggybacks tier flips on the opening-move call (zero extra MCP requests). Both tier-control tools are force-included at list-time and call-time so users can never strand themselves via `disabled_tools`. Bundle ∩ `disabled_tools` conflicts emit startup WARNING + `config --check` diagnostic. HTTP transport + `adaptive_tiering: true` emits startup WARNING about process-global tier state leaking across clients (hard refusal tracked in #248).
- **Server error responses include `summary` field** — unexpected exceptions now add a short, sanitized `RuntimeError: ...` summary alongside the generic top-level `error`. Full traceback still lands in the server log.

### Fixed
- **MUNCH `search_text` round-trip restored (#246)** — the on-wire schema declared flat columns `file|line|line_content`, but the real tool response is nested `{results:[{file, matches:[...]}]}`. Two matches in the same file collapsed to one null row; the matched line `text` was read as `line_content` (wrong column); `before`/`after` context arrays were dropped entirely. Rewritten with flatten/regroup around the real nested shape; columns now `file|line|text|before|after` with context lines riding as JSON strings inside CSV cells (adversarial-tested for embedded commas, quotes, newlines). Encoding ID bumped `st1` → `st2`; legacy `st1` payloads still decode via a new `LEGACY_ENCODING_IDS` discovery hook in the encoder registry. `schema_driven.decode()` gains an opt-in `scalar_types` parameter so typed scalars (ints, floats, bools) round-trip correctly instead of stringifying — `search_text` declares its numeric/boolean fields; every other schema's behavior is unchanged.
- **`tools/list_changed` notifications actually emit now (#246)** — `_emit_tools_list_changed` was a `pass` placeholder, so the entire runtime-tier feature was silently never notifying clients. New `_get_mcp_session` helper does concrete `srv.request_context.session` lookup with narrowed `LookupError` / `AttributeError` handling; warns at WARNING level when the session lacks `send_tool_list_changed` so SDK version mismatches are visible. Integration test exercises the real `FakeServer.request_context.session` chain instead of mocking the helper.
- **`plan_turn` tie-safe heap (#246)** — the bounded heap stored `(score, entry)` tuples, so equal scores forced Python to compare `dict` values and raised `TypeError: '<' not supported between instances of 'dict' and 'dict'`. Heap now stores `(score, symbol_id, entry)` triples; regression test covers the equal-score case with two indexed `helper()` symbols.
- **`plan_turn` tier flip atomicity (#246)** — when `adaptive_tiering: true`, the tier switch now runs after the handler returns successfully. A handler failure can no longer leave a half-applied session tier.
- **`render_diagram` integration test portability (#250)** — test now skips cleanly when `MMD_VIEWER_PATH` is unset, instead of falling back to a hardcoded path only one contributor's machine had. Set `MMD_VIEWER_PATH` to opt in locally.

### Migration
- No config changes required. Existing installs see zero behavior change; `adaptive_tiering` defaults to `false`.
- Operators wanting runtime tier switching: set `adaptive_tiering: true` in `config.jsonc` and optionally customize `tool_tier_bundles` and `model_tier_map`. Run `jcodemunch-mcp config --upgrade` to pull the new keys into an existing config file without clobbering user values.
- The MUNCH `search_text` encoder bumped from `st1` to `st2`. Existing cached `st1` payloads continue to decode via `LEGACY_ENCODING_IDS`.

## [1.59.1] — 2026-04-18

### Fixed
- **Redaction no longer mangles source code (F1)** — the `bearer_token` pattern was over-broad: `(?i)(?:bearer|token)\s+...` matched ordinary identifiers like `def refresh_token session_identifier_name`. Pattern is now header-anchored (`Authorization:` context or capital `Bearer `). The `generic_api_key` pattern now requires the captured value to include all three character classes (upper+lower+digit) AND meet a Shannon entropy threshold (≥3.5 bits/char), so SCREAMING_CASE and snake_case identifiers pass through unchanged.
- **Redaction depth cap no longer leaks secrets (F2)** — past the 20-level recursion guard, `redact_dict` previously returned the raw subtree. Deeply nested payloads now collapse to `[REDACTED:depth_exceeded]` instead.
- **`_meta` string fields now scanned for secrets (F6)** — the blanket `_meta` bypass in `redact_dict` skipped strings inside `_meta.hint` / `_meta.error`, letting secrets echoed into metadata leak unredacted. Only scalar numeric/bool fields bypass scanning now; strings and nested containers are redacted.
- **`mermaid_viewer.open_diagram` contains filesystem errors (F4)** — `mkdir` and `write_text` failures previously propagated as exceptions, contradicting the docstring's "non-fatal on failure" contract. They now return `{opened: False, error: "write_failed: ..."}`.
- **Per-call temp-file purge (F3)** — `open_diagram` now prunes `jcm-*.mmd` files older than one hour on every invocation, preventing unbounded growth under repeated use.

### Security
- **Viewer executable gate (F5)** — `resolve_viewer_path` now checks that the configured path looks executable (exec suffix on Windows; execute bit on POSIX). Stale config pointing at a non-executable file now returns `None` instead of attempting to spawn it.

## [1.59.0] — 2026-04-17

### Added
- **`render_diagram` optional `open_in_viewer` parameter (#245, @MariusAdrian88)** — opt-in integration with the companion [mmd-viewer](https://github.com/MariusAdrian88/mmd-viewer) binary for instant visual preview of rendered Mermaid diagrams. Fully gated behind two new config keys: `render_diagram_viewer_enabled` (default `false`) controls whether the `open_in_viewer` parameter is exposed in the tool schema at all, so LLM clients see no change unless the feature is enabled locally; `mermaid_viewer_path` points at the executable (empty = `$PATH` lookup). When enabled and invoked, the rendered Mermaid is written to a `jcm-`-prefixed `.mmd` file under `<index_storage>/temp/mermaid/` and piped to the viewer on stdin. Non-fatal by design: viewer-missing or spawn-failure adds a `viewer_error` field to the response but always returns the Mermaid markup. Cleanup is selective — only `jcm-`-prefixed files are removed, on both startup (stale files from prior sessions) and shutdown (only if the viewer was invoked this session). Windows file-lock aware with 500 ms retry on unlink.

## [1.58.0] — 2026-04-17

### Added
- **MUNCH TypeScript decoder + agent hints (phase 5)** — reference TS decoder at `clients/ts/decoder.ts` (zero dependencies, ~200 lines) decodes both tier-1 and generic-fallback MUNCH payloads to plain JS objects; falls through to `JSON.parse` for non-MUNCH input. New `AGENT_HINTS.md` ships a drop-in prompt snippet so agents can read MUNCH payloads directly without a client-side decoder, plus a worked example walking through legend handles, scalar quoting, and table rehydration. Closes phase 5 of the MUNCH rollout — clients that cannot or will not decode MUNCH can still request `format="json"` to opt out per call or set `JCODEMUNCH_DEFAULT_FORMAT=json` to disable globally.

## [1.57.0] — 2026-04-17

### Added
- **MUNCH spec + benchmark harness (phase 4)** — full on-wire format spec shipped as [SPEC_MUNCH.md](SPEC_MUNCH.md) so third-party clients and alternate MCP servers can decode MUNCH payloads without depending on the Python reference decoder. New A/B encoding benchmark at `munch-bench/munch_bench/encoding_bench.py` (`python -m munch_bench.encoding_bench`) runs representative fixtures through the dispatcher and reports JSON bytes, compact bytes, savings %, and token-saved estimate. Current numbers: **median 45.5% / max 55.4%** across six tools covering both tier-1 and generic-fallback paths — exceeds the PRD's ≥30% median / ≥50% graph-tool targets.
- **README compact-output section** and updated `TOKEN_SAVINGS.md` with the dual-axis framing (retrieval + encoding compose independently).

## [1.56.0] — 2026-04-17

### Changed
- **MUNCH generic fallback hardened (phase 3)** — the shape-sniffer that covers every tool without a hand-tuned encoder now produces fully round-trippable output. Original table keys, column order, and per-column types (int / float / bool / str) are preserved via an embedded schema line instead of being emitted under synthesized `table_<tag>` keys. Top-level scalars now round-trip with their original types via a compact companion `__stypes` map. Nested `dict` values containing list-of-dicts children are flattened with a dotted key so downstream tools can still read them. Path-prefix legend promotion widened and byte-threshold tightened so interning only fires when it actually saves bytes. Table-tag alphabet widened from 7 to 26 and the tag-vs-scalar classifier rewritten against the `<char>,` CSV leading-byte signal so scalar keys can freely start with any letter. Malformed or pathological shapes (mixed arrays, oversized table counts, short lists) fall through to JSON-blob passthrough instead of crashing; the savings gate still discards compact output whenever it isn't a net win, so the fallback remains safe to fail-open across all 65+ tools without custom encoders.

## [1.55.0] — 2026-04-17

### Added
- **MUNCH tier-1 custom encoders (phase 2)** — 15 hand-tuned per-tool compact encoders now ship, replacing the generic fallback for the highest-payoff tools: `get_dependency_graph` (dg1), `get_call_hierarchy` (ch1), `find_references` (fr1), `find_importers` (fi1), `get_blast_radius` (br1), `get_impact_preview` (ip1), `get_signal_chains` (sc1), `get_dependency_cycles` (dc1), `get_tectonic_map` (tm1), `search_symbols` (ss1), `search_text` (st1), `search_ast` (sa1), `get_file_outline` (fo1), `get_repo_outline` (ro1), `get_ranked_context` (rc1). Each encoder declares a schema with column order, type hints, and intern columns; dispatcher picks the custom encoder when available and falls back to the generic shape-sniffer otherwise. Round-trip tested — decoded payload preserves all table contents and scalar fields. Sample `get_dependency_graph` response (8 edges, 6 files) drops from 721 bytes JSON to 490 bytes compact (32% savings); larger responses climb toward 50-70% as legend interning amortizes.
- **Schema-driven encoder helper** (`encoding/schema_driven.py`) lets future per-tool encoders be written in ~30 lines of declarative config: `TableSpec` + scalar/nested-dict/meta/json-blob declarations, then two one-line `encode`/`decode` wrappers.

## [1.54.0] — 2026-04-17

### Added
- **Compact response encoding (MUNCH, phase 1)** — opt-in second-axis token savings independent from retrieval-side optimization. Every tool response can now be emitted in a purpose-built compact format (path/symbol interning, tabular row packing, quoted-CSV data sections) instead of verbose JSON. New `format` argument on every tool accepts `"auto"` (default; falls back to JSON if savings <15%), `"compact"` (force), or `"json"` (never encode). Server-wide default overridable via `JCODEMUNCH_DEFAULT_FORMAT`. Phase 1 ships a schema-agnostic generic encoder that covers all 80+ tools; hand-tuned per-tool encoders land in subsequent releases. `_meta` now surfaces `encoding`, `encoding_tokens_saved`, and a new persisted `total_encoding_tokens_saved` counter so encoding savings are reported separately from retrieval savings. Decoder shipped at `jcodemunch_mcp.encoding.decoder.decode()` for clients that need to rehydrate payloads back to dicts.

## [1.53.0] — 2026-04-17

### Added
- **Constraint-chain retrieval** — new `winnow_symbols` tool runs a multi-axis query against the index in a single round trip. Accepts an ordered list of `{axis, op, value}` criteria (AND semantics) that intersect signals no existing tool composes: `kind`, `language`, `name` (regex), `file` (glob), `complexity`, `decorator`, `calls` (direct call references), `summary/docstring` text, and git `churn` (with configurable `window_days`). Survivors are ranked by PageRank-based `importance` (default), `complexity`, `churn`, or `name`. Replaces the common 4–5-call pattern of intersecting `search_symbols` + `get_hotspots` + `get_untested_symbols` + `find_references` client-side — e.g. "complex functions that call `db.Exec` and churned recently" resolves in one call. Reports `matched`, `total_scanned`, and `supported_axes` alongside ranked results.

## [1.52.1] — 2026-04-16

### Changed
- **`plan_refactoring` full language coverage** — import and definition patterns, import rewrite logic, and import formatting extended to ~40 previously-uncovered languages (erlang, solidity, zig, clojure, powershell, ocaml, fsharp, nim, tcl, dlang, pascal, ada, cobol, matlab, apex, css/scss/sass/less/styl, razor, blade, al, nix, ejs, verse, asm, vue, and others). Refactorings in these languages now emit correct edits instead of falling through to a generic default. New `TestLanguageCoverage` suite enforces parity between `LANGUAGE_REGISTRY` and the refactoring patterns so future language additions can't silently drift.
- **Config tool registry** — `config.py` `all_tools` list updated to include 25+ tools added since last refresh (`audit_agent_config`, `check_rename_safe`, `get_call_hierarchy`, `get_churn_rate`, `get_hotspots`, `get_impact_preview`, `get_pr_risk_profile`, `get_symbol_provenance`, `plan_refactoring`, `render_diagram`, `search_ast`, and more); alphabetically sorted.
- **Test parametrization** — 4 test files consolidated from 330 individual functions to 78 parametrized ones (322 pytest cases, all assertions preserved). Net −1,508 lines of boilerplate across `test_plan_refactoring.py`, `test_config.py`, `test_find_importers.py`, `test_hardening.py`, and `test_render_diagram.py`.

### Fixed
- **Test index contamination** — `test_project_intel.py` was orphaning 22 indexes in `~/.code-index/` on each run; now isolated to `tmp_path`.

Thanks to **@MariusAdrian88** for this contribution (#244).

## [1.52.0] — 2026-04-16

### Added
- **AST Pattern Matching** — new `search_ast` tool provides cross-language structural code pattern matching across all 70+ indexed languages. Write one query, match everywhere — no need to know language-specific tree-sitter node types. Two modes: **(1) Preset anti-patterns** — 10 curated detectors that auto-translate across languages: `empty_catch` (silently swallowed errors), `bare_except` (catch-all without specific type), `deeply_nested` (5+ control-flow levels), `nested_loops` (O(n³)+ triple loops), `god_function` (100+ line functions), `eval_exec` (dynamic code execution — injection risk), `hardcoded_secret` (credential patterns in string literals), `todo_fixme` (unfinished work markers), `magic_number` (unexplained numeric constants), `reassigned_param` (overwritten function parameters). **(2) Custom mini-DSL** — ad-hoc structural queries: `call:*.unwrap` (call-site glob matching), `string:/password/i` (regex over string literals), `comment:/TODO/i` (regex over comments), `nesting:5+` / `loops:3+` / `lines:80+` (threshold queries). Run by category (`security`, `error_handling`, `complexity`, `performance`, `maintenance`) or `category=all` for a full sweep. Every match is attributed to its enclosing indexed symbol with complexity metadata. Universal node-type mapping covers 15 language families (Python, JS/TS, Go, Rust, Java, C#, Ruby, PHP, C/C++, Kotlin, Swift, Dart). Results sorted by severity (error → warning → info).
- Updated **assess** and **triage** MCP prompt templates to recommend `search_ast` for security and anti-pattern sweeps.

## [1.51.0] — 2026-04-16

### Added
- **Symbol Provenance** — new `get_symbol_provenance` tool traces the complete authorship lineage and evolution narrative of any symbol through git history. Uses `git log -L` line-range tracking (with file-level fallback) to find every commit that touched a symbol, classifies each into semantic categories (creation, bugfix, refactor, feature, perf, rename, revert, etc.), extracts motivating intent from commit bodies, and generates a human-readable narrative summarising who created it, why, and how it evolved. Returns ranked author list, evolution summary with lifespan/frequency metrics, and dominant change pattern. Use before refactoring unfamiliar code to understand the "why" behind it.
- **PR Risk Profile** — new `get_pr_risk_profile` tool produces a unified risk assessment for all changes between two git refs. Fuses five orthogonal signals — blast radius (30%), complexity (25%), test gaps (20%), churn (15%), and change volume (10%) — into a single composite `risk_score` (0.0–1.0) with `risk_level` (low/medium/high/critical). Returns per-signal breakdowns, top-5 riskiest changed symbols, untested symbol list, and actionable recommendations. Designed for CI gating and the `/review` workflow. One call replaces manual orchestration of `get_changed_symbols` + `get_blast_radius` + `get_hotspots` + `get_untested_symbols`.
- **Response Secret Redaction** — all tool responses are now scanned for leaked credentials before reaching the LLM context window. Detects AWS access keys (AKIA...), AWS secret keys, GCP service account emails, Azure storage/client keys, JWT tokens, GitHub PATs (ghp_/gho_/...), Slack tokens, PEM private key headers, generic API keys (32+ char high-entropy values), and private IPv4 addresses (10.x, 172.16-31.x, 192.168.x). Matched values are replaced with `[REDACTED:<type>]` placeholders. Controlled by `redact_response_secrets` config key (default: true) or `JCODEMUNCH_REDACT_RESPONSE_SECRETS` env var. The `_meta` field reports `secrets_redacted` count when any redactions occur.
- Updated the **assess** MCP prompt template to recommend `get_pr_risk_profile` as the quick-path and `get_symbol_provenance` for deep-path analysis.

## [1.50.1] — 2026-04-16

### Fixed
- **Devcontainer/Docker support** — `index_folder` no longer rejects shallow paths like `/workspace` or `/app` when running inside a container. Auto-detects Docker (`/.dockerenv`), Podman (`/run/.containerenv`), VS Code devcontainers (`REMOTE_CONTAINERS`), GitHub Codespaces (`CODESPACES`), and generic container orchestrators (`container` env var). Minimum path depth is relaxed from 3 to 2 components; bare `/` is still rejected. `trusted_folders` remains available as a manual override. Fixes #243.

## [1.50.0] — 2026-04-15

### Added
- **Branch-Aware Delta Indexing** — jcodemunch-mcp now maintains per-branch delta layers instead of re-indexing from scratch when you switch git branches. One base index (typically `main`/`master`) stores the full index; non-base branches save only what changed relative to the base (O(delta) storage). At query time, the delta is composed onto the base to produce the branch-specific view. All 55+ tools auto-detect the current branch via `git rev-parse --abbrev-ref HEAD` — no new parameters required. Supports detached HEAD (uses commit SHA), non-git folders (graceful no-op), and stale delta detection (warns when base was re-indexed after the delta was created). New storage: `branch_deltas` and `branch_meta` tables in the existing SQLite DB. `list_repos` now shows indexed branches. INDEX_VERSION bumped to 9 (auto-migration from v8).

## [1.49.0] — 2026-04-15

### Added
- **Project Intelligence** — new `get_project_intel` tool auto-discovers and structurally parses non-code knowledge files (Dockerfiles, docker-compose, GitHub Actions, GitLab CI, CircleCI, K8s manifests, .env templates, Makefiles, package.json scripts, pyproject.toml scripts) and cross-references them to indexed code symbols. Returns structured intelligence grouped into 6 categories: `infra` (Docker stages/services/ports, K8s resources, Terraform from index), `ci` (pipeline jobs/triggers/run commands), `config` (env vars with defaults and comments), `deps` (scripts/targets/entry points), `api` (OpenAPI endpoints, GraphQL types, Protobuf services from index), `data` (dbt/SQLMesh models, column counts, migration files from index). Cross-references link Dockerfile entrypoints to source files, compose build contexts to directories, env var names to code that reads them, CI run commands to test files, and script targets to referenced paths. Every YAML parser has a regex fallback — works with zero optional dependencies. Single `os.walk` pass with 200-file cap, 256KB size guard, and 50-item output caps per category.

## [1.48.0] — 2026-04-15

### Added
- **Universal Mermaid Renderer** — new `render_diagram` tool transforms any graph-producing tool's output into rich, annotated Mermaid markup. Auto-detects the source tool from the dict's key signature and picks the optimal diagram type: `flowchart TD` for call hierarchies and blast radius, `flowchart BT` for impact previews, `flowchart LR` for tectonic plates / dependency graphs / cycles, and `sequenceDiagram` for signal chains. Encodes metadata as visual signals: edge colors for resolution confidence (green=LSP, blue=AST, orange=inferred, red=heuristic), node shapes by symbol kind, subgraph grouping by file/plate/depth, risk heat coloring, drifter/nexus callouts. Three themes: `flow` (blue/purple depth gradient), `risk` (red/yellow/green heat), `minimal` (monochrome). Smart pruning preserves topology under `max_nodes` budget (leaf removal → low-degree removal). Returns `mermaid` markup, `legend`, `node_count`, `edge_count`, `pruned_count`. Supports all 7 graph tools: `get_call_hierarchy`, `get_signal_chains`, `get_tectonic_map`, `get_dependency_cycles`, `get_impact_preview`, `get_blast_radius`, `get_dependency_graph`.

## [1.47.0] — 2026-04-15

### Added
- **Signal Chain Discovery** — new `get_signal_chains` tool traces how external signals (HTTP requests, CLI commands, scheduled tasks, events) propagate through the codebase via the call graph. Each chain starts at a **gateway** (route handler, CLI command, task decorator, event listener, main entry point) and follows BFS callees to leaf symbols. Two modes: **discovery** (omit `symbol` — maps all chains, reports orphan symbols not on any chain) and **lookup** (pass a `symbol` — returns which user-facing chains it participates in, e.g. "validate_email sits on POST /api/users and cli:import-users"). Detects gateways from Flask/FastAPI/Spring/NestJS/ASP.NET route decorators, @click/@app.command CLI, @celery/@dramatiq task queues, event handlers, and standard entry points. Filter by `kind` (http/cli/event/task/main/test). Reuses existing AST-resolved call graph infrastructure for 70+ language support.

## [1.46.0] — 2026-04-15

### Added
- **Tectonic Analysis** — new `get_tectonic_map` tool discovers the logical module topology of a codebase by fusing three independent coupling signals: structural (import edges), behavioral (shared symbol references), and temporal (git co-churn). Returns auto-detected file clusters ("plates"), each with an anchor file, cohesion score, inter-plate coupling map, drifter detection (files whose directory doesn't match their logical module), and nexus alerts (god-module risk). Plate count emerges from the topology — no k parameter. Pure Python label propagation, no external dependencies.

## [1.45.1] — 2026-04-15

### Documentation
- **Hermes Agent integration** — added "Works with" section to README with Hermes Agent config example; submitted optional skill PR to [NousResearch/hermes-agent#10413](https://github.com/NousResearch/hermes-agent/pull/10413)

## [1.45.0] — 2026-04-15

### Added
- **Enhanced BM25 tokenizer** — Porter-style suffix stemming ("searching" → "search", "running" → "run") and bidirectional abbreviation expansion (40 entries: "db" ↔ "database", "config" ↔ "configuration", etc.). Significantly improves recall for natural-language queries against code symbols.
- **Diversity-aware budget packing** — `get_ranked_context` now spreads results across files (per-file cap of 3, decay penalty for same-file repeats) instead of greedy same-file stacking. Produces more useful context bundles.

### Fixed
- **Content hash consistency** — drift detection always uses SHA-256, preventing false-positive staleness on existing indexes.

## [1.44.1] — 2026-04-14

### Fixed
- `claude-md` (and Cursor/Windsurf rule generators) now respects `tool_profile` and `disabled_tools` — only emits tools the model can actually call (#242).

## [1.44.0] — 2026-04-14

### Added
- **Tool profiles** — new `tool_profile` config key with three tiers to control context budget (#242):
  - `"core"` — 16 essential tools (indexing, search, retrieval, relationships). ~5-6k tokens saved vs full.
  - `"standard"` — core + analytics, architecture, quality, impact tools (~40 tools).
  - `"full"` — all tools (default, backwards-compatible).
- **Compact schemas** — new `compact_schemas` config key strips rarely-used advanced parameters (debug, fusion, semantic_*, fuzzy_*, etc.) from tool schemas. The server still accepts them — they're just hidden from the LLM. Saves ~1-2k tokens on top of any profile.
- `config` command now shows `tool_profile` and `compact_schemas` in the Tool Profile section.
- 6 new tests for profile filtering and compact schema stripping.

## [1.43.0] — 2026-04-13

### Added
- **6 new languages** — F# (`.fs`, `.fsi`, `.fsx`), Clojure (`.clj`, `.cljs`, `.cljc`, `.edn`), Emacs Lisp (`.el`), Nim (`.nim`, `.nims`, `.nimble`), Tcl (`.tcl`, `.tk`, `.itcl`), D (`.d`, `.di`)
- Custom tree-sitter parsers for all 6 languages with full symbol extraction: F# modules/functions/types/values, Clojure namespace-qualified defn/def/defprotocol/defrecord, Emacs Lisp defun/defvar/defconst/defmacro with docstrings, Nim proc/func/template/macro/type/var/let/const, Tcl proc with namespace nesting (`::`-qualified names), D functions/classes/structs/interfaces/enums/templates with nested method extraction

### Documentation
- Updated `server.json` version (1.8.6 → 1.43.0) and language count (25+ → 70+)
- Updated `benchmarks/whitepaper.md` language counts from "25+" to "70+"
- Added CONFIGURATION.md and GROQ.md to README.md documentation table
- Updated `LANGUAGE_SUPPORT.md` valid language names list with all 73 registered languages

## [1.42.0] — 2026-04-13

### Added
- **11 new languages** — Pascal/Delphi (`.pas`, `.dpr`, `.dpk`, `.lpr`, `.pp`), MATLAB (`.mat`, `.mlx`, + `.m` path-heuristic disambiguation vs Objective-C), Ada (`.adb`, `.ads`), COBOL (`.cob`, `.cbl`, `.cpy`), Common Lisp (`.lisp`, `.cl`, `.lsp`, `.asd`), Solidity (`.sol`), Zig (`.zig`, `.zon`), PowerShell (`.ps1`, `.psm1`, `.psd1`), Apex/Salesforce (`.cls`, `.trigger`), OCaml (`.ml`, `.mli`), PL/SQL (`.pls`, `.plb`, `.pck`, `.pkb`, `.pks` → existing SQL parser)
- Custom tree-sitter parsers for all 10 new grammar-backed languages with full symbol extraction: functions, classes, types, constants, methods, and language-specific constructs (COBOL paragraphs/sections, Solidity contracts/events/modifiers, Zig test declarations, Apex triggers, OCaml modules)
- MATLAB vs Objective-C `.m` file disambiguation via path heuristics (directories named `matlab/`, `toolbox/`, `simulink/` → MATLAB; `ios/`, `xcode/`, `cocoa/` → Objective-C)

## [1.41.0] — 2026-04-13

### Added
- **munch-bench** — Retrieval + Inference benchmark consolidated into the mothership (Phase 5 of Groq Integration). 110 questions across 11 repos, evaluation harness with Groq/OpenAI/Anthropic providers, static HTML leaderboard with Chart.js. Install with `pip install jcodemunch-mcp[bench]`, run with `munch-bench run --provider groq`. First results: Sonnet 0.81, Haiku 0.68, Groq Llama 0.69 judge scores.
- New optional dependency group `[bench]` (openai, anthropic, pyyaml, rich, jinja2)
- `munch-bench` CLI entrypoint: `run`, `compare`, `corpus-stats` subcommands

## [1.40.1] — 2026-04-13

### Fixed
- Fix `jcodemunch-mcp index <owner/repo>` CLI crash — was passing `repo=` instead of `url=` to `index_repo()`, causing `TypeError: got an unexpected keyword argument 'repo'`

## [1.40.0] — 2026-04-13

### Added
- **Voice-to-Codebase (`gcm --voice`)** — speak a question about your codebase, hear the answer spoken back. Full audio pipeline: Groq Whisper STT → jCodeMunch retrieval → Groq LLM → Orpheus TTS playback. Push-to-talk via Enter key, with text fallback. Install with `pip install jcodemunch-mcp[groq-voice]`. Supports multi-turn voice conversation, configurable model, and verbose timing.
- **Auto Repo Explainer (`gcm explain`)** — generate a narrated explainer video for any codebase in a single command. Pipeline: gather repo structure + key symbols → Groq LLM generates narration script → Orpheus TTS renders audio → Pillow renders 1920x1080 dark-theme slides → FFmpeg composites into MP4. Install with `pip install jcodemunch-mcp[groq-explain]` (requires FFmpeg on PATH). Produces 45-90 second videos with file tree and code snippet slides.
- New optional dependency groups: `[groq-voice]` (sounddevice, numpy), `[groq-explain]` (Pillow)
- 18 new tests for voice and explainer modules (`test_groq_voice.py`, `test_groq_explainer.py`)

## [1.39.1] — 2026-04-13

### Fixed
- **gcm: fix GitHub repo detection on Linux** — `_is_github_repo` now correctly identifies `owner/name` patterns on all platforms (was failing on Linux where `/` is `os.path.sep`)

## [1.39.0] — 2026-04-13

### Added
- **Codebase Q&A CLI (`gcm`)** — ask any question about any codebase, get an answer in under 3 seconds. Powered by jCodeMunch retrieval + Groq inference. Install with `pip install jcodemunch-mcp[groq]`. Supports GitHub repos (`--repo owner/name`), local directories, streaming output, interactive `--chat` mode, `--fast` flag for 8B model, and configurable token budget. Auto-indexes on first use.

## [1.38.0] — 2026-04-13

### Added
- **speedreview GitHub Action** (`speedreview/`) — AI code review in under 5 seconds. Composite action uses jCodeMunch locally for symbol-level diff analysis (`get_changed_symbols` + `get_blast_radius` + `get_ranked_context`) and Groq for sub-2s inference. Posts structured review as PR comment. Usage: `uses: jgravelle/jcodemunch-mcp/speedreview@main`.

## [1.37.0] — 2026-04-13

### Added
- **Groq Remote MCP integration** — full tutorial (`GROQ.md`), Docker deployment (`Dockerfile`, `docker-compose.yml`, `Caddyfile`), validation script (`examples/groq_validate.py`), and README section. Deploy jCodeMunch as an HTTPS SSE endpoint and connect via Groq's Responses API in a single API call. Includes allowed-tools presets (explore, deep, review, full) and model recommendations.

## [1.36.0] — 2026-04-12

### Added
- **Arduino language support** ([#239](https://github.com/jgravelle/jcodemunch-mcp/pull/239)): `.ino`/`.pde` files parsed via tree-sitter-arduino grammar (C++ superset). Classes, structs, enums, functions, constants extracted. Import extraction reuses `#include` path
- **VHDL language support** ([#239](https://github.com/jgravelle/jcodemunch-mcp/pull/239)): `.vhd`/`.vhdl`/`.vho`/`.vhs` files parsed via regex. Extracts entity, architecture, package, process, function, procedure, component, signal, constant, type/subtype. Import extraction for `library`/`use` clauses (`work` library excluded)
- **Verilog/SystemVerilog language support** ([#239](https://github.com/jgravelle/jcodemunch-mcp/pull/239)): `.v`/`.vh`/`.sv`/`.svh` files parsed via regex. Extracts module, interface, class, function, task, package, typedef, parameter/localparam, `` `define ``. Import extraction for `` `include `` directives

## [1.35.1] — 2026-04-12

### Fixed
- **invalidate_cache + index_folder reliability** ([#238](https://github.com/jgravelle/jcodemunch-mcp/pull/238)): `invalidate_cache` followed by `index_folder` (incremental) no longer returns "No changes detected". Fixes Windows WAL file-locking race, legacy JSON resurrection, and adds `_force_full_reindex` coordination flag
- **meta_fields config applied to batch results** ([#238](https://github.com/jgravelle/jcodemunch-mcp/pull/238)): `meta_fields` filter now strips/filters nested `_meta` in batch tool responses (e.g. `get_file_outline` with `file_paths=[...]`)
- **WatcherManager self-restarts on crash** ([#238](https://github.com/jgravelle/jcodemunch-mcp/pull/238)): monitoring loop auto-restarts with 100ms backoff, up to 5 consecutive attempts before clean exit
- **Orphan index cleanup on startup** ([#238](https://github.com/jgravelle/jcodemunch-mcp/pull/238)): indexes whose `source_root` no longer exists on disk are deleted at server startup

## [1.35.0] — 2026-04-12

### Added
- **`plan_refactoring` tool** ([#236](https://github.com/jgravelle/jcodemunch-mcp/pull/236)): generate edit-ready `{old_text, new_text}` refactoring plans in a single call. Supports rename, move, extract, and signature change operations across all affected files. Handles import rewrites for 20+ languages, collision detection, inter-symbol dependency warnings, path alias detection, non-code file scanning, and multi-line signature capture. 325 new tests

### Fixed
- Python 3.10 compatibility in `plan_refactoring` — removed Python 3.12+ f-string syntax ([#236](https://github.com/jgravelle/jcodemunch-mcp/pull/236))
- False call sites no longer reported for multi-line signature continuation lines ([#236](https://github.com/jgravelle/jcodemunch-mcp/pull/236))
- `_plan_extract` no longer unconditionally adds source import when no staying symbol references extracted symbols ([#236](https://github.com/jgravelle/jcodemunch-mcp/pull/236))
- `_split_python_import` preserves indentation for imports inside `try:` blocks ([#236](https://github.com/jgravelle/jcodemunch-mcp/pull/236))

### Changed
- Extracted `_capture_multiline_sig()` helper and hoisted `_file_to_module()` to module level — net -142 lines of duplication ([#237](https://github.com/jgravelle/jcodemunch-mcp/pull/237))

## [1.34.0] — 2026-04-11

### Added
- **MCP progress notifications** ([#232](https://github.com/jgravelle/jcodemunch-mcp/issues/232)): `index_folder`, `index_repo`, `index_file`, and `embed_repo` now emit `notifications/progress` when the client provides a `progressToken`. Zero token cost — notifications go to the host (e.g. VS Code MCP widget), never the model. Shows label, ASCII bar, percent, count, and current item name
- **`ProgressReporter`** (`progress.py`): thread-safe, monotonic progress helper. No pulse threads, no fake drift — progress reflects real completed work
- **`make_progress_notify()`** (`progress.py`): bridge function that creates a thread-safe callback from the MCP request context, using `asyncio.run_coroutine_threadsafe` to safely send notifications from worker threads
- 16 new tests in `tests/test_progress.py` covering reporter lifecycle, monotonicity, thread safety, format, no-op behavior, and tool signature wiring

## [1.33.0] — 2026-04-11

### Added
- **Auto-watch on demand** ([#233](https://github.com/jgravelle/jcodemunch-mcp/pull/233)): when `watch: true` is set in config (or `JCODEMUNCH_WATCH=1`), the server automatically reindexes and starts watching any unwatched repo before a tool executes. Eliminates silent-stale-data that causes LLMs to abandon jcodemunch tools for the session. Race-safe via `asyncio.Condition` — concurrent tool calls to the same unwatched repo trigger only one reindex
- **`WatcherManager` class** (`watcher.py`): manages dynamic folder watching with `add_folder()`, `remove_folder()`, `is_watched()` (O(1)), `list_folders()`, `ensure_indexed()` (race-safe), and `run()` (crash recovery). Replaces direct task manipulation in `watch_folders()`
- **`get_source_root()`** (`sqlite_store.py`): lightweight metadata-only SQLite query to resolve repo ID to folder path without loading full `CodeIndex`
- **`watch` config key**: opt-in via `watch: true` in config.jsonc or `JCODEMUNCH_WATCH=1` env var (default: `false`)
- 15 new tests in `tests/test_watcher_dynamic.py` covering manager lifecycle, race guard, and auto-watch integration

### Fixed
- Restarted watch tasks (crash recovery in `WatcherManager.run()`) now receive the `on_reindex` callback — previously dropped, causing idle-timeout to fire prematurely after a task restart
- `_pending_results` dict in `WatcherManager.ensure_indexed()` no longer leaks — entries are popped after concurrent waiters consume them
- Individual `_watch_single` tasks are now explicitly cancelled and awaited during `watch_folders` shutdown (previously only manager/watchdog tasks were cancelled)

## [1.32.1] — 2026-04-10

### Fixed
- **`embed_repo` preflight performance** (#231): cache-discovery no longer loads and decodes every stored embedding blob just to get symbol IDs. New `EmbeddingStore.get_all_ids()` queries only the `symbol_id` column. Eliminates unnecessary CPU, memory, and latency on repos with existing embeddings

## [1.32.0] — 2026-04-10

### Added
- **`jcodemunch-mcp index` CLI command** ([#230](https://github.com/jgravelle/jcodemunch-mcp/issues/230)): Index a local folder or GitHub repo directly from the terminal. Defaults to the current directory when no target is given — no `init` required. Supports `--no-ai-summaries`, `--follow-symlinks`, and `--extra-ignore` flags

### Changed
- **Version renumbered from 2.1.0 → 1.32.0.** The 2.0.0 bump was premature — every change from 1.24.4 through 2.1.0 was purely additive (new tools, new opt-in config, new CLI subcommands). Nothing was removed, renamed, or made incompatible. INDEX_VERSION stayed at 8, all config defaults preserved existing behavior, and LSP/dispatch features are off by default. Per semver, additive features are minor bumps. The full renumbering: 1.24.4→1.25.0, 1.24.5→1.26.0, 1.25.0→1.27.0, 1.26.0→1.28.0, 1.27.0→1.29.0, 1.28.0→1.30.0, 2.0.0→1.31.0, 2.1.0→1.32.0. PyPI releases under the old numbers remain installable but are logically equivalent to their renumbered counterparts

## [1.31.0] — 2026-04-10

### Added
- **Interface & trait dispatch resolution** (Phase 5 / Gap 2C): resolves interface/trait method calls to their concrete implementations via LSP `textDocument/implementation`. Supports Go interfaces, Rust traits, TypeScript/Java/C#/PHP interfaces and abstract classes. Adds `dispatches_to` edges with `lsp_dispatch` resolution tier
- **`_detect_interface_keywords()`** in `parser/extractor.py`: tags interface/trait/abstract symbols in `keywords` during tree-sitter parsing — zero-cost, no INDEX_VERSION bump required. Covers Go (`interface_type`), Rust (`trait_item`), TypeScript (`interface_declaration`), Java/C# (interface + abstract class), PHP (interface + trait)
- **`goto_implementation()` on `LSPServer`**: new method parallel to `goto_definition()`, sends `textDocument/implementation` request. Updated `_initialize()` capabilities to advertise implementation support
- **`DispatchEdge` dataclass**: represents interface method → concrete implementation mapping with `lsp_dispatch` resolution
- **`resolve_implementations()` on `LSPBridge`**: resolves interface method positions to concrete implementations across multiple language servers. Caps at 50 implementations per interface method
- **`enrich_dispatch_edges()` entry point**: high-level function that scans parsed symbols for interface keywords, collects method positions, resolves implementations via LSP, and returns serializable edge dicts
- **`dispatch_edges` in `context_metadata`**: stored alongside existing `lsp_edges` in both full and incremental `index_folder` paths
- **`_dispatch_callers()` / `_dispatch_callees()`** in `_call_graph.py`: query dispatch edges to find concrete implementations (callees) or callers through interface dispatch. Integrated at highest priority in `find_direct_callers/callees`
- **`dispatches` section in `get_call_hierarchy`**: new response field showing interface dispatch relationships grouped by interface/method, with concrete implementation details
- **`lsp_dispatch_enriched` methodology**: when dispatch edges are present, `_meta.methodology` is `lsp_dispatch_enriched` and `confidence_level` is `high`
- 31 new tests in `tests/test_dispatch_resolution.py` covering interface keyword detection (15 languages), `goto_implementation` unit tests, `DispatchEdge` dataclass, `_dispatch_callers/_dispatch_callees` with mock indexes, `get_call_hierarchy` dispatches section, graceful degradation, and TS interface keyword propagation through `index_folder`

## [1.30.0] — 2026-04-10

### Added
- **LSP Bridge enrichment layer** (Gap 2B): new `enrichment/lsp_bridge.py` module — optional, opt-in integration with language servers for compiler-grade call graph resolution. Manages LSP server lifecycles for pyright (Python), typescript-language-server (TS/JS), gopls (Go), and rust-analyzer (Rust). Strictly additive: if a language server isn't installed, falls back to pure tree-sitter + heuristic with zero behaviour change
- **`lsp_resolved` resolution tier**: new highest-confidence tier in call graph edges. `get_call_hierarchy` now reports four tiers: `lsp_resolved` (compiler-grade via LSP), `ast_resolved` (direct tree-sitter match), `ast_inferred` (resolved via import graph), `text_matched` (heuristic). When LSP data is present, `_meta.methodology` is `lsp_enriched` and `confidence_level` is `high`
- **LSP enrichment in `index_folder`**: when `enrichment.lsp_enabled` is set to `true` in config.jsonc, the indexing pipeline calls LSP servers to resolve unqualified call sites after tree-sitter parsing. Resolved edges are stored in `context_metadata.lsp_edges` and consumed by the call graph at query time
- **`enrichment` config block**: new configuration section in config.jsonc — `enrichment.lsp_enabled` (default `false`), `enrichment.lsp_servers` (per-language server map), `enrichment.lsp_timeout_seconds` (default 30). Supports both global and per-project config
- 40 new tests in `tests/test_lsp_bridge.py` covering JSON-RPC helpers, server lifecycle, graceful degradation, call graph integration, config helpers, and index_folder integration

## [1.29.0] — 2026-04-10

### Added
- **Bundled ONNX local encoder** (Gap 1): new `embeddings/local_encoder.py` module ships a zero-config embedding provider using `all-MiniLM-L6-v2` (Apache 2.0, 384-dim, ~23 MB). Install via `pip install 'jcodemunch-mcp[local-embed]'` — no API keys, no internet after first download, no configuration. Includes a minimal WordPiece tokenizer (no `transformers` dependency) and L2-normalised mean-pooled output
- **`local_onnx` provider (priority 0)**: when `onnxruntime` is installed and the model is present, `embed_repo` and `search_symbols(semantic=true)` automatically use the bundled encoder — zero friction. Falls through to sentence-transformers/Gemini/OpenAI if unavailable
- **`download-model` CLI subcommand**: `jcodemunch-mcp download-model` fetches the ONNX model + vocab from HuggingFace to `~/.code-index/models/all-MiniLM-L6-v2/`. Auto-downloads on first `embed_repo` call if model is missing. Override path via `JCODEMUNCH_LOCAL_EMBED_MODEL` env var or `--target-dir` flag
- **`[local-embed]` install extra**: `pip install 'jcodemunch-mcp[local-embed]'` adds `onnxruntime>=1.16.0` dependency

## [1.28.0] — 2026-04-10

### Added
- **Unified signal fusion pipeline** (Gap 3 full): new `retrieval/signal_fusion.py` module implements Weighted Reciprocal Rank (WRR) fusion across four channels — lexical (BM25), structural (PageRank), similarity (embeddings), and identity (exact/prefix/segment match). Configurable per-channel weights via `config.jsonc` under `retrieval.fusion_weights`. Eliminates linear score addition in favour of proper rank fusion
- **`search_symbols(fusion=true)`**: new parameter activates multi-signal fusion ranking. Debug mode (`debug=true`) reports `fusion_score`, `channel_contributions`, and `channel_ranks` per result. `_meta` includes active channels, weights, and smoothing constant
- **`get_ranked_context(fusion=true)`**: fusion-based context assembly with per-item channel contribution breakdown in results
- **Post-task diagnostics hook** (Gap 4B): new `hook-taskcomplete` CLI subcommand — on task completion, runs three diagnostics scoped to session-modified files: `find_dead_code` (newly-orphaned symbols), `get_untested_symbols` (untested new code), `check_references` (unreferenced symbols). Injects a compact housekeeping nudge via `systemMessage`
- **Subagent briefing hook** (Gap 4C): new `hook-subagent-start` CLI subcommand — injects a condensed repo orientation (file/symbol/language stats, top-15 PageRank central symbols, full 40+ tool catalog) for spawned agents. Ensures subagents start with structural context
- Both new hooks are auto-registered in `~/.claude/settings.json` by `jcodemunch-mcp init` and `config --check` verifies their presence

## [1.27.0] — 2026-04-10

### Added
- **PreCompact structural landmarks** (Gap 4A): `run_precompact()` now enriches the session snapshot with PageRank-ranked top-20 central symbols and recently-changed symbols from the session journal. Gives the LLM a structural "table of contents" that survives context compaction
- **Per-edge resolution tiers** (Gap 2A): every edge in `get_call_hierarchy` callers/callees now carries a `resolution` field — `ast_resolved` (direct tree-sitter match), `ast_inferred` (resolved via import graph), or `text_matched` (heuristic word-boundary fallback). `_meta.resolution_tiers` summarises the tier distribution
- **Identity channel in search** (Gap 3 partial): `search_symbols` replaces the old `50.0` exact-name hack with a proper identity scoring channel — exact match (50), prefix match (30), qualified-ID segment match (20). Debug mode (`debug=true`) now reports `identity` score and `identity_type` in the per-field breakdown

## [1.26.0] — 2026-04-10

### Added
- **Guided workflow prompts**: 4 new MCP prompt templates alongside the existing `workflow` prompt — `explore` (onboard to an unfamiliar repo), `assess` (pre-merge impact analysis), `triage` (diagnose code quality), `trace` (investigate a bug through the call graph). Each composes existing jcodemunch tools into a step-by-step workflow. Accessible via the MCP prompt protocol (`list_prompts` / `get_prompt`)

## [1.25.0] — 2026-04-10

### Added
- **`get_untested_symbols`**: new tool — find functions and methods with no evidence of test-file reachability. Uses import-graph analysis + name matching (AST call_references when available, word-boundary text heuristic as fallback). Classifies symbols as "unreached" (no test imports the source file) or "imported_not_called" (test imports the module but no test references this specific function). Supports `file_pattern` glob filter, `min_confidence` threshold, and `max_results` cap
- **`get_blast_radius` enrichment**: every confirmed entry now includes a `has_test_reach: bool` field indicating whether any test file imports that file AND references the affected symbol by name
- **`_is_test_file()` expanded**: now recognizes JS/TS test patterns (`.spec.ts`, `.spec.js`, `.test.ts`, `.test.js`, `__tests__/`) in addition to existing Python patterns. Benefits both `find_dead_code` and `get_untested_symbols`

## [1.24.3] — 2026-04-10

### Added
- **`watch --once`**: one-shot index sync — indexes all paths incrementally and exits immediately. No watchfiles dependency required. Supports multiple paths. Exit code 1 if any path fails (#227, thanks @kecsap!)

## [1.24.2] — 2026-04-08

### Added
- **Starter Packs** (`install-pack` subcommand): download pre-built indexes for popular frameworks. `--list` shows the catalog, `--license KEY` for premium packs, `--force` to re-download. Free packs require no license
- **Per-call pulse signal** (`_pulse.json`): opt-in activity file for downstream dashboards and monitors. Set `JCODEMUNCH_EVENT_LOG=1` to enable. Writes tool name, timestamp, call count, and tokens saved on every tool call (#225)

### Fixed
- `test_summarizer`: "misconfigured" error now names the missing package and includes the exact `pip install` command instead of a generic message (#224)
- `config` output: `allow_remote_summarizer` moved from Privacy section to AI Summarizer section with clarification that it only affects custom base URLs, not standard API endpoints (#224)

## [1.24.1] — 2026-04-08

### Changed
- **Comprehensive doc audit** — reviewed all CHANGELOG entries from 1.21.13–1.24.0 and updated 6 user-facing docs:
  - **USER_GUIDE.md**: Added 12 missing tools (`plan_turn`, `get_session_context`, `register_edit`, `get_session_snapshot`, `get_call_hierarchy`, `get_hotspots`, `get_coupling_metrics`, `get_dependency_cycles`, `get_extraction_candidates`, `get_impact_preview`, `get_dead_code_v2`), `decorator` filter on `search_symbols`, `include_source`/`source_budget`/`decorator_filter` on `get_blast_radius`, negative evidence, and 9 new workflow patterns
  - **CONFIGURATION.md**: Added 15 missing config keys (`agent_selector`, `exclude_skip_directories`, `exclude_secret_patterns`, `languages_adaptive`, `session_journal`, `turn_budget_tokens`, `turn_gap_seconds`, `negative_evidence_threshold`, `search_result_cache_max`, `plan_turn_*_threshold`, `session_resume`, `session_max_age_minutes`, `session_max_queries`, `discovery_hint`, `strict_timeout_ms`)
  - **AGENT_HOOKS.md**: Added Python CLI hooks section (`hook-pretooluse`, `hook-posttooluse`, `hook-precompact`) with `init --hooks` as recommended install method; added call hierarchy, hotspots, decorator search, session tools to both prompt policy blocks
  - **ARCHITECTURE.md**: Added 16 missing tools to Tool Surface; updated directory structure with `agent_selector.py`, `cli/`, `parser/` details, `_call_graph.py`, `session_journal.py`, `session_state.py`, `turn_budget.py`, `plan_turn.py`
  - **README.md**: Added call hierarchy, hotspots, coupling metrics, dependency cycles to structural queries section; added session-aware routing, enforcement hooks, agent selector to feature list; updated `init` docs for `--hooks`
  - **QUICKSTART.md**: Added enforcement hooks to `init` feature list; documented `--demo` flag

## [1.24.0] — 2026-04-08

### Added
- **Agent Selector**: opt-in complexity-based model routing system that assesses request complexity using pre-processing signals and recommends (manual mode) or automatically selects (auto mode) the appropriate model tier (low/medium/high). Off by default — zero behavioral change for existing users
  - `ComplexityScorer`: weighted linear scoring using retrieval set size, symbol count, cross-file references, cross-project flag, language complexity, and token estimate
  - `ModelRouter`: three modes — `off` (default), `manual` (advisory prompts on step-up; `verbosePrompts` for step-down), `auto` (automatic routing with metadata annotation)
  - Default batting orders for Anthropic, OpenAI, and Google providers; fully customizable via `agentSelector` config block
  - Session-level init param overrides (`agentSelector.mode`, `agentSelector.activeProvider`, `agentSelector.verbosePrompts`)
  - Tier resolution edge cases: missing tier fallback, single-model provider passthrough, unknown provider graceful degradation
  - 39 new tests covering scorer, router, config, tier resolution, and language classification

## [1.23.5] — 2026-04-08

### Changed
- CI: bump `actions/checkout` v4→v5 and `astral-sh/setup-uv` v3→v6 for Node.js 24 compatibility (GitHub enforces June 2nd 2026)

## [1.23.4] — 2026-04-08

### Fixed
- Python import resolution: `resolve_specifier` now handles module-style absolute imports (`app.notifications.mentions`) by converting dots to slashes and trying each auto-detected source root (`backend/`, `src/`, etc.) as a prefix. Previously `posixpath.splitext` treated the last dotted component as a file extension, breaking all non-flat Python layouts (#223, @kallevaravas)
- Python import extraction: `_PY_FROM` and `_PY_IMPORT` regexes now allow optional leading whitespace, capturing function-local and class-body imports that were previously silently dropped (#223, @kallevaravas)

## [1.23.3] — 2026-04-07

### Fixed
- Tests: 6 `index_folder()` calls in `test_negative_evidence.py` were leaking index files into `~/.code-index/` instead of pytest's `tmp_path` (#222, @MariusAdrian88)

## [1.23.2] — 2026-04-07

### Added
- `get_blast_radius`: new `include_source` flag returns `source_snippets` (lines referencing the symbol) and `symbols_in_file` (nearby symbol signatures) on each confirmed entry — enables fix-ready context in one call without extra `get_symbol_source`/`get_file_content` round-trips. Optional `source_budget` (default 8000 tokens) caps output size; files prioritised by reference count (#221, @MariusAdrian88)

### Fixed
- `get_blast_radius`: `decorator_filter` was missing from session cache key, which could return stale filtered results

## [1.23.1] — 2026-04-07

### Changed
- Switch MCP tool responses from pretty-printed JSON (`indent=2`) to compact JSON (`separators=(',',':')`) — saves 30-40% tokens per response with zero information loss (fixes #219)

## [1.23.0] - 2026-04-07

### Added
- **AST-based call graph** — extract `call_expression` nodes during tree-sitter parsing and store as `call_references` per symbol. 13 languages supported including constructor calls (`new Foo()`). INDEX_VERSION bumped from 7 to 8 with full v7 backward compatibility (graceful degradation to text heuristic). Confidence upgraded from "low" to "medium" for AST-derived results.
- **Decorator awareness** — `search_symbols(decorator=...)` filter (case-insensitive substring match), `get_blast_radius(decorator_filter=...)`, and decorator surfacing in `get_file_outline` results. Enables cross-cutting concern discovery (e.g. "which endpoints lack CSRF protection?").
- **Negative evidence + enforcement signals** — structured `negative_evidence` and top-level `⚠ warning` strings in `get_ranked_context` and `search_symbols` when queries return empty/low-confidence results. `plan_turn` emits `action: "STOP_AND_REPORT_GAP"` on low/none confidence. Reduces LLM hallucination about missing features.
- **18 new framework route/middleware providers** — Flask, FastAPI, Express, Fastify, Hono, Koa, Gin, Chi, Echo, Fiber, Django (+ DRF), Spring Boot, NestJS, ASP.NET, Rails. Consolidated entry-point decorator regex into `_route_utils.py`. 8 new `FrameworkProfile` definitions.

### Changed
- **Performance optimizations** — single-pass AST walk for symbols + call sites, lazy `_callers_by_name` index (0ms load when unused), pre-computed `enrich_symbols` file context cache (~60-80% fewer provider calls), fuzzy search early-exit cap at 5× max_results, merged disambiguate + complexity pass, O(1) PHP detection via `languages` set.
- **`get_dead_code_v2` Signal 2** — uses AST `call_references` lookup (O(1)) on v8 indexes instead of O(N×M) file I/O.
- `budget_warning` promoted to top-level alongside `_meta` for visibility.

### Fixed
- Semantic search negative evidence used fragile nested ternary — replaced with named `best_score` variable.
- Empty query terms guard added to `search_symbols`, `get_ranked_context`, and `plan_turn`.

### Contributors
- @MariusAdrian88

## [1.22.6] - 2026-04-06

### Fixed
- **`_merge_hooks()` idempotency** — per-rule dedup instead of per-event. Previously, once any jcodemunch PreToolUse hook was installed, no additional PreToolUse rules could be added by `init --hooks`. Now each rule's command is checked individually, allowing incremental hook installation. Cherry-picked from @DrHayt's PR #214.
- **Worktree hook-event derivation** — Claude Code sends `{cwd, name}` in WorktreeCreate/WorktreeRemove payloads, not `worktreePath`. Derive path as `{cwd}/.claude/worktrees/{name}`. Legacy fields still accepted. Also outputs resolved path on stdout as Claude Code expects. Cherry-picked from @DrHayt's PR #214.
- **`config --check` hook validation** — now verifies Python hooks in `~/.claude/settings.json` instead of scanning for shell scripts in `~/.claude/hooks/`. Warns about legacy shell scripts if found. Cherry-picked from @DrHayt's PR #214.

## [1.22.5] - 2026-04-06

### Added
- **`TWEAKCC.md`** — guide for system prompt routing via [tweakcc](https://github.com/Piebald-AI/tweakcc) as an alternative to hook-based enforcement. Includes 8 prompt rewrites that embed jCodemunch preferences into Claude's core tool descriptions. Cross-referenced from AGENT_HOOKS.md. Credit: [@vadash](https://github.com/vadash). Closes #173.

### Fixed
- **PreToolUse hook no longer blocks Read** — changed from hard `deny` to a stderr warning. The deny broke the Edit workflow because Claude Code requires Read before Edit, forcing workarounds or env var overrides. Targeted reads (with `offset` or `limit`) are now silently allowed; full-file reads on large code files produce a stderr hint nudging toward `get_file_outline` + `get_symbol_source`. Aligns the Python CLI hook with the documented shell hook design in AGENT_HOOKS.md which explicitly notes "Read is intentionally NOT blocked".

## [1.22.4] - 2026-04-06

### Added
- **`get_session_snapshot` MCP tool** — compact ~200 token markdown summary of session state (focus files by read count, edited files, key searches, negative evidence). Designed for context injection after compaction to restore session orientation. Contributed by @MariusAdrian88. Closes #211.
- **PreCompact CLI hook** (`jcodemunch-mcp hook-precompact`) — automatically generates and injects a session snapshot before Claude Code context compaction via the `systemMessage` hook output field. Registered by `jcodemunch-mcp init`.
- **`sort_by` parameter for `get_context()`** — session journal now supports `sort_by="frequency"` (by read/edit/query count) in addition to the default `sort_by="timestamp"`.
- **`max_edits` parameter for `get_context()`** — limits the number of edited files returned, consistent with `max_files` and `max_queries`.

## [1.22.3] - 2026-04-06

### Added
- **`exclude_skip_directories` config** — remove entries from the built-in skip directory list at runtime. Mirrors the existing `exclude_secret_patterns` pattern. Example: set `["proto"]` to index protobuf directories that are skipped by default. Contributed by @DrHayt. Closes #209.

## [1.22.2] - 2026-04-05

### Fixed
- **CLI init indexing broken** — `run_index()` passed `folder_path=` to `index_folder()` which expects `path=`, causing `unexpected keyword argument` error on `jcodemunch-mcp init`. Closes #208.

## [1.22.1] - 2026-04-05

### Fixed
- **streamable-http session persistence** — `run_streamable_http_server` previously created a new `StreamableHTTPServerTransport` (and a new `server.run()` coroutine) for every incoming HTTP request, leaving follow-up calls like `tools/list` hitting an uninitialised session and failing with `-32602 INVALID_PARAMS`. The handler now maintains a session map keyed by `mcp-session-id`: on the first request a background `asyncio.Task` runs `transport.connect()` + `server.run()` for the lifetime of the session, and all subsequent requests from the same client are routed to the existing transport. Terminated sessions (e.g. after DELETE) are cleaned up automatically. Includes a 10-second setup timeout with graceful error response. Closes #204.
- 9 new tests in `test_streamable_http_sessions.py`.

## [1.22.0] - 2026-04-05

### Added
- **`plan_turn` tool** — opening-move router for any task. Runs BM25 + PageRank against the query, returns confidence level (high/medium/low/none), recommended symbols/files, insertion point suggestions for missing features, prior negative evidence detection, and a budget advisor when turn budget exceeds 60%.
- **`get_session_context` tool** — returns session history: files read, searches run, edits made, tool call counts. Use to avoid re-reading the same files.
- **`register_edit` tool** — post-edit cache invalidation. Clears BM25 token cache and search result cache for edited files; optionally reindexes.
- **Session journal** (`session_journal.py`) — process-lifetime singleton tracking reads, searches, edits, and negative evidence. Bounded at 5000 entries per category with LRU eviction. Thread-safe.
- **Turn budget** (`turn_budget.py`) — cross-call token accumulator. Injects `budget_warning` + `auto_compacted` into `_meta` when budget runs low. Configurable via `turn_budget_tokens` and `turn_gap_seconds`.
- **Session state persistence** (`session_state.py`) — save/restore session across restarts. Writes only on clean shutdown via `atexit`. Staleness validated against `indexed_at` on restore. Opt-in via `session_resume: true`.
- **Negative evidence in `search_symbols`** — when results are empty or below threshold, response includes structured `negative_evidence` with `verdict` (no_implementation_found / low_confidence_matches), `scanned_symbols`, `scanned_files`, `related_existing`.
- **LRU result cache in `search_symbols`** — 128-entry default, cache key includes `indexed_at` for automatic invalidation on reindex.
- **10 new config keys**: `negative_evidence_threshold`, `search_result_cache_max`, `session_journal`, `plan_turn_high_threshold`, `plan_turn_medium_threshold`, `turn_budget_tokens`, `turn_gap_seconds`, `session_resume`, `session_max_age_minutes`, `session_max_queries`.
- **CLAUDE.md policy updates** — routing rules for `plan_turn`, negative evidence handling, budget warning response, and a Read exception note (harness requires Read before Edit/Write).
- 75 new tests across 10 test files (2191 total, 0 regressions).

## [1.21.27] - 2026-04-04

### Added
- **PreToolUse enforcement hook** (`hook-pretooluse` subcommand) — intercepts `Read` calls on large code files (>=4KB, configurable via `JCODEMUNCH_HOOK_MIN_SIZE`) and returns a `deny` decision directing Claude to use `get_file_outline` + `get_symbol_source` instead. Non-code files and small files pass through silently. Addresses the "0% jcodemunch efficiency" problem where CLAUDE.md rules are ignored under cognitive load.
- **PostToolUse auto-reindex hook** (`hook-posttooluse` subcommand) — fires after `Edit` or `Write` on code files and spawns `jcodemunch-mcp index-file` in the background to keep the index fresh. Eliminates "index staleness anxiety" that caused users to bypass jcodemunch and fall back to `Read`.
- **Enforcement hooks in `init`** — `jcodemunch-mcp init` now offers to install both hooks into `~/.claude/settings.json` (PreToolUse matcher: `Read`, PostToolUse matcher: `Edit|Write`). Enabled by `--hooks` flag or interactive prompt. Idempotent, backup-aware, and respects `--dry-run`/`--demo`.
- New `_merge_hooks()` helper in `cli/init.py` — shared logic for merging hook definitions into settings.json, used by both worktree and enforcement hook installers.
- 25 new tests in `test_hooks.py` covering PreToolUse deny/allow logic, PostToolUse indexing, idempotent install, and edge cases (missing files, invalid JSON, Windows creation flags).

## [1.21.26] - 2026-04-04

### Added
- **Cursor rules injection in `init`** — when Cursor is detected, `jcodemunch-mcp init` now offers to write `.cursor/rules/jcodemunch.mdc` with `alwaysApply: true`. This ensures the code-exploration policy is in context for every Cursor agent turn, including subagents, fixing the unreliable tool-fallback behaviour reported by Cursor users.
- **Windsurf rules injection in `init`** — when Windsurf is detected, `init` now offers to append the code-exploration policy to `.windsurfrules`. Both files are idempotent, backup-aware, and respect `--dry-run`.
- **`--demo` flag for `init`** — `jcodemunch-mcp init --demo` walks through the full setup process without making any changes, then prints "Had this NOT been a demo, I would have:" followed by each action and its benefit. 10 new tests in `test_init.py`.

## [1.21.25] - 2026-04-03

### Added
- **`audit_agent_config` tool** — scans agent config files (CLAUDE.md, .cursorrules, copilot-instructions.md, .windsurfrules, settings.json, etc.) for token waste. Reports per-file token cost, stale symbol references (cross-referenced against the jcodemunch index), dead file paths, redundancy between global and project configs, bloat patterns, and scope leaks. 34 new tests.

## [1.21.24] - 2026-04-03

### Added
- **`jcodemunch-mcp init` subcommand** — one-command onboarding that auto-detects installed MCP clients (Claude Code, Claude Desktop, Cursor, Windsurf, Continue), writes their config entries, injects the Code Exploration Policy into CLAUDE.md, installs worktree lifecycle hooks, and optionally indexes the current directory. Supports `--dry-run`, `--yes` (non-interactive), `--client`, `--claude-md`, `--hooks`, `--index`, and `--no-backup` flags. 27 new tests in `test_init.py`.
- Updated QUICKSTART.md, README.md, and AGENT_HOOKS.md to lead with `init` as the recommended setup path.
- **`audit_agent_config` tool** — scans agent config files (CLAUDE.md, .cursorrules, copilot-instructions.md, .windsurfrules, settings.json, etc.) for token waste. Reports per-file token cost, stale symbol references (cross-referenced against the jcodemunch index), dead file paths, redundancy between global and project configs, bloat patterns, and scope leaks. 34 new tests in `test_audit_agent_config.py`.

## [1.21.23] - 2026-04-02

### Fixed
- **`@includeFirst` Blade directive now parsed** (`laravel.py`) — `_BLADE_INCLUDE_FIRST` regex captures the first (highest-priority) candidate from `@includeFirst(['primary.view', 'fallback.view'])` array arguments and injects it as an import edge. Previously the directive was silently dropped. The fallback candidates are intentionally omitted — only the preferred view is tracked. Closes #203.

## [1.21.22] - 2026-04-02

### Added
- **Stage 4: Cross-language dependency graph (Laravel)** — `laravel.py` now injects extra import edges via the new `get_extra_imports()` provider hook: Blade `@extends`/`@include`/`@includeWhen`/`@includeUnless`/`@component`/`view()` templates, `<x-*>` components, Eloquent relationship edges (`hasMany`/`belongsTo`/etc.), 40 built-in Laravel facades mapped to underlying `Illuminate\*` classes, route→controller file edges, and Inertia.js `Inertia::render`/`inertia()` → Vue/React page component resolution. Frontend `fetch`/`axios`/`useFetch` API calls are matched to Laravel route→controller files via wildcard URI matching.
- **Stage 5: Nuxt/Next.js context providers** — `parser/context/nuxt.py` (`NuxtContextProvider`) parses `pages/` for file-based routing, `server/api/` for API handlers with HTTP method extraction, and scans `composables/`/`utils/` for auto-import edges. `parser/context/nextjs.py` (`NextjsContextProvider`) handles App Router `page`/`layout`/`loading`/`error`/`route` files, route group `(auth)` segment collapsing, middleware detection, and HTTP method extraction from route handlers.
- **Stage 6: FQN ↔ symbol_id translation** — new `parser/fqn.py` with `symbol_to_fqn()` and `fqn_to_symbol()` for bidirectional PSR-4 translation. Optional `fqn` parameter added to `get_symbol_source`, `get_blast_radius`, `search_symbols`, and `get_context_bundle`. `_utils.py` gains `resolve_fqn()` helper. Detailed error messages for missing PSR-4 config, unindexed files, or namespace mismatch.
- **`collect_extra_imports()` merger in `context/base.py`** — called in all 4 indexing pipeline paths; deduplicates by specifier, swallows per-provider failures with a warning.
- **125 new tests** across `test_laravel_provider.py`, `test_nuxt_provider.py`, `test_nextjs_provider.py`, `test_fqn.py`, and `test_find_importers.py`; 2008 total passing.

## [1.21.21] - 2026-04-02

### Changed
- **`files_to_remove` kept as `set` in `incremental_save` (T8)** — `sqlite_store.py` no longer converts the union of `deleted_files` and `changed_files` to a list. The set is preserved through the function and passed to `_patch_index_from_delta`, making membership tests in the hot path (`in files_to_remove`) O(1) instead of O(n). sqlite3 calls receive `tuple(files_to_remove)`.
- **Defer `stat()` until after LRU key check in `load_index` (T9)** — `stat()` is now only called when the cache key is already present; cold-start loads skip the pre-load `stat()` syscall entirely. `_CACHE_MAX_SIZE` raised from 16 → 32.
- **Cap `_REPO_PATH_CACHE` at 512 entries (T23)** — `config.py` trims the oldest entries after each `update()` so the cache cannot grow unbounded in long-running server sessions.
- **`expanduser()` on startup storage path log (T24)** — all three transport startup log lines (`stdio`, `sse`, `streamable-http`) now call `os.path.expanduser()` on the `CODE_INDEX_PATH` value so the logged path shows the real expanded path on Windows instead of `~/.code-index/`.

## [1.21.20] - 2026-04-02

### Added
- **Dart import extractor (T19)** — `imports.py` now includes `_extract_dart_imports` (regex on `import`/`export` statements) registered as `"dart"` in `_LANGUAGE_EXTRACTORS`. Dart files no longer appear in `missing_extractors` after indexing. 9 new tests in `tests/test_dart_imports.py`; `test_parse_warnings.py` updated to use Elixir as the canonical missing-extractor example.
- **LANGUAGE_SUPPORT.md expanded (T20)** — added full extraction rows for CSS, SCSS, SASS, YAML, Ansible, OpenAPI, and JSON; fixed C# entry to list `constant (property/field/event)` symbol types (were incorrectly documented as "not indexed"); corrected CSS row previously listed only under "text search indexing"; SASS entry now documents the CSS-parser fallback.
- **Hypothesis property-based tests (T22)** — `tests/test_property_based.py` with 4 tests across 3 invariant classes: **ID uniqueness** (`TestIdUniqueness` — all symbol IDs in a freshly indexed folder are unique); **Incremental idempotency** (`TestIncrementalIdempotency` — indexing the same files twice yields the same symbol IDs and counts); **No self-imports** (`TestNoSelfImports` — no file in the import graph lists itself as an importer). `hypothesis>=6.0.0` added to dev dependency group. 4 new tests, 90 Hypothesis examples per run.

### Changed
- **`JCODEMUNCH_EXTRA_EXTENSIONS` valid language names** (T21) — added `scss`, `sass`, `less`, `styl`, `yaml`, `ansible`, `json`, `openapi`, `luau` to the documented list in LANGUAGE_SUPPORT.md.

## [1.21.19] - 2026-04-02

### Added
- **Methodology disclosure on all 6 analytical tools (T15)** — every analytical tool response now includes `_meta.methodology` and `_meta.confidence_level`. Values: `get_call_hierarchy` + `get_impact_preview` → `methodology: "text_heuristic"`, `confidence_level: "low"`; `get_symbol_complexity` → `methodology: "stored_metrics"`, `confidence_level: "medium"`; `get_churn_rate` → `methodology: "git_log"`, `confidence_level: "high"`; `get_hotspots` → `methodology: "complexity_x_churn"`, `confidence_level: "medium"`; `get_repo_health` → `methodology: "aggregate"`, `confidence_level: "medium"`; `get_dead_code_v2` → `methodology: "multi_signal"`, `confidence_level: "medium"`. 18 new tests in `tests/test_meta_disclosure.py`.
- **Import-gap signal in `index_folder` (T17)** — `index_folder` now reports `missing_extractors` (sorted list of languages that have symbol extraction but no import extractor) and `parse_warnings` when import graph coverage is incomplete. Example: indexing a folder with `.dart` files yields `missing_extractors: ["dart"]` and a human-readable `parse_warnings` entry. 4 new tests in `tests/test_parse_warnings.py`.
- **`framework_warning` in `get_dead_code_v2` (T18)** — when BFS finds zero standard entry points (`main.py`, `app.py`, etc.), all files are unreachable from entry points and Signal 1 fires for every symbol, inflating dead code counts. `get_dead_code_v2` now includes `framework_warning` in that case, advising callers to pass `entry_point_patterns`. 5 new tests in `tests/test_parse_warnings.py`.

### Fixed
- **Parameter count off-by-one for C-style zero-param functions (T16)** — `_count_params` in `parser/complexity.py` treated `void foo(void)` as a one-parameter function because `"void"` was a non-empty `params_str` with no commas, yielding `commas + 1 = 1`. Added a special case: `params_str == "void"` → return 0, matching the C/C++ convention that `(void)` declares zero parameters. `void*` and multi-param signatures containing `void` are unaffected. 3 new tests in `tests/test_complexity.py`.

## [1.21.18] - 2026-04-02

### Added
- **Correctness fixture library (T12)** — `tests/conftest.py` now exports three shared pytest fixtures (`small_index`, `medium_index`, `hierarchy_index`) that build deterministic synthetic Python repos with documented ground-truth expected outputs. Used across multiple test modules as the canonical in-process test corpus.
- **Tests for `get_class_hierarchy` (T13)** — 22 new tests in `tests/test_class_hierarchy.py` covering: `_parse_bases` unit tests (Python single/multi base, Java extends/implements, combined, lowercase filter, empty); hierarchy BFS error cases (repo not indexed, class not found); ancestor direction (no ancestors for root, direct parent, transitive chain, BFS nearest-first order); descendant direction (all descendants of root, direct children, leaf has none); meta fields (case-insensitive lookup, timing, class info, external base recorded as `"(external)"`).
- **Tests for `get_related_symbols` (T13)** — 14 new tests in `tests/test_related_symbols.py` covering: `_tokenize_name` unit tests (snake_case, camelCase, single word, short-token filter, lowercase); error cases (repo not indexed, symbol not found); same-file grouping (co-located symbols are related, scores positive); name-token overlap scoring; `max_results` cap; meta fields (timing, target symbol in response, required entry fields).
- **Tests for `get_symbol_diff` (T13)** — 15 new tests in `tests/test_symbol_diff.py` covering: error cases (repo A not indexed, repo B not indexed); added symbols (detected, count matches list); removed symbols (detected, count matches list); unchanged symbols (not in added/removed, identical repo → all unchanged); changed symbols (signature change detected, both signatures present); meta fields (timing, symbol counts, repo identifiers).
- **Tests for `suggest_queries` (T13)** — 11 new tests in `tests/test_suggest_queries.py` covering: error cases (repo not indexed, empty index); small repo stats (symbol count, file count, kind distribution, language distribution, example queries non-empty, required query fields); medium repo stats (file count, most_imported file structure, class+function kinds, repo field, timing meta).
- **Tests for rate-limit middleware (T13)** — 10 new tests in `tests/test_rate_limit.py` covering: factory returns `None` when `JCODEMUNCH_RATE_LIMIT` is 0, unset, invalid, or negative; returns non-`None` `Middleware` when limit is positive; sliding-window bucket logic: under-limit all allowed, over-limit rejected, expired entries evicted, limit=1 allows first denies second.
- **In-process perf benchmarks with latency budgets (T14)** — `tests/test_search_perf.py` rewritten from an external-index-dependent skip-if-not-indexed pattern to a fully self-contained suite. Builds a 5-file, 20+ symbol synthetic repo at module scope. New latency assertions: cold search < 2000 ms, warm search < 500 ms (BM25 cache benefit). Correctness assertions: result order stable across two consecutive calls, scores stable with `debug=True`, relevant symbol appears in top-5 for known query, all queries return non-empty results. Zero `pytest.skip` in the file.

### Changed
- **`tests/test_search_perf.py`** — removed `_require_index()` / `pytest.skip` pattern that caused CI-skip when `jcodemunch-mcp` was not indexed locally. Tests now run unconditionally against the synthetic in-process index.

## [1.21.17] - 2026-04-02

### Fixed
- **BM25 `avgdl` inflation corrected (T10)** — `_sym_tokens` computed `_dl` (document length) as `len(tokens)` where `tokens` is the weighted repeated bag (field-repetition multipliers make the name appear 3× in the bag, signature 2×, etc.). This inflated `_dl` and therefore `avgdl`, distorting the BM25 length-normalisation term `K`. Fixed by using `len(set(tokens))` — the unique-token count — consistent with how document-frequency (`df`) is already computed via `for t in set(toks)`. Symbols with overlap across name/signature/summary fields (the common case) were previously penalised as "long documents" when they are not.
- **BM25 rebuild canonical `_dl` enforcement (T11)** — `_compute_bm25` now overwrites `sym["_dl"]` with `len(unique_toks)` on every corpus rebuild. Previously the function used the cached `_dl` from `_sym_tokens`, meaning retained symbols carrying a pre-T10 `_dl` value (the inflated bag length) would make `avgdl` inconsistent with the new formula. The forced rewrite ensures the corpus and all scoring are internally consistent even when the BM25 cache is rebuilt over a mix of freshly computed and carried-forward symbols (e.g., after deferred AI summarisation). 11 new correctness tests added (`tests/test_bm25_correctness.py`).

## [1.21.16] - 2026-04-02

### Fixed
- **Watcher hash-cache double-read race eliminated (T6)** — after each incremental reindex the watcher previously re-read each changed file to compute the new content hash for its in-memory cache. If the file changed again between `index_folder`'s internal read and the watcher's post-reindex re-read, the cache recorded the wrong (newer) hash while the index held the older content. The *next* watchfiles event would then deliver `old_hash=<newer>`, `index_folder` would hash the file, see no difference, and silently skip re-parsing a stale index entry. Fixed by replacing per-file re-reads with a single `_build_hash_cache()` call that reads hashes from the store `index_folder` just wrote — the single authoritative source of truth. Removed the now-dead `_update_hash_cache` / `_remove_from_hash_cache` helpers and the unused `_file_hash` import.

## [1.21.15] - 2026-04-02

### Fixed
- **Deferred-summarize write-lock race eliminated (T7)** — a narrow but real race existed between the deferred summarization thread's generation check ("check 2") and its `incremental_save` call. A concurrent `mark_reindex_start` could bump `deferred_generation` and write a fresh index between those two points; the deferred thread would then overwrite it with stale AI summaries from the previous parse generation. Fixed by introducing a per-repo `threading.Lock` (`_repo_deferred_save_locks` in `reindex_state.py`). The deferred thread holds this lock across check 2 + save; `mark_reindex_start` holds it while bumping `deferred_generation`. This makes check-and-save atomic with respect to generation bumps: either the deferred thread saves before the new generation is written, or it sees the new generation and self-aborts. Added `gen=N` to deferred-summarize log messages so abandoned and completed saves are distinguishable in debug output (pre-T7 instrumentation).

## [1.21.14] - 2026-04-02

### Fixed
- **Threading locks added to all in-process caches (T5)** — four module-level caches were missing `threading.Lock` guards, leaving them vulnerable to data races under concurrent MCP requests (HTTP transport, multi-client stdio). Now protected:
  - `_bare_name_cache` (`tools/_utils.py`) — new `_BARE_NAME_LOCK`; check and write are each under the lock; expensive `list_repos()` I/O happens between the two lock acquisitions so the lock is never held during I/O.
  - `_REPO_PATH_CACHE` (`config.py`) — now protected by the existing `_CONFIG_LOCK`; reads (check) and bulk writes (`update`) are each atomic under the lock; store I/O happens outside.
  - `_alias_map_cache` (`parser/imports.py`) — new `_ALIAS_MAP_LOCK`; same check-then-build-then-write pattern.
  - `_sql_stem_cache` (`parser/imports.py`) — new `_SQL_STEM_LOCK`; same pattern.
- **`invalidate_cache` now clears all 5 in-process caches under their locks (T4.5)** — previously `_sql_stem_cache` was not cleared on `invalidate_cache`, leaving stale SQL stem mappings across re-indexes. Also, `_REPO_PATH_CACHE.clear()` and `_PROJECT_CONFIGS.pop()` were called outside `_CONFIG_LOCK`. All five caches (`_REPO_PATH_CACHE`, `_PROJECT_CONFIGS`, `_PROJECT_CONFIG_HASHES`, `_bare_name_cache`, `_sql_stem_cache`, `_alias_map_cache`) are now cleared under their respective locks.

## [1.21.13] - 2026-04-02

### Fixed
- **`truncated` flag now correct when `token_budget` packing drops results** — in the BM25 search path, the flag was computed using `candidates_scored > len(scored_results)` after the fuzzy augmentation pass, meaning fuzzy results appended after budget packing could mask dropped BM25 results and produce `truncated=False` incorrectly. Now tracked as a separate `budget_truncated` boolean computed immediately after packing; the final flag is `candidates_scored > heap_count or budget_truncated`. The semantic search path was already correct.
- **Call graph `"source"` label corrected** — `get_call_hierarchy` and `get_impact_preview` both returned `"source": "ast"` in `_meta`, implying type-resolved AST analysis. The implementation is word-token regex matching on raw file text. Label changed to `"source": "text_heuristic"` with updated tip text to accurately describe the approach and its limitations (false positives for common names, no dynamic dispatch).

## [1.21.12] - 2026-04-02

### Added
- **PSR-4 namespace resolution for PHP projects (Stage 1 of jgravelle/jcodemunch-mcp#201)** — `find_importers`, `get_blast_radius`, `get_dependency_graph`, `find_dead_code`, and all other import-graph tools now correctly resolve PHP `use App\Models\User` statements to `app/Models/User.php` via `composer.json` PSR-4 autoload mappings. Previously these tools returned zero results for PHP projects using Composer autoloading (effectively every modern PHP project). `build_psr4_map()` and `resolve_php_namespace()` are new public helpers; `CodeIndex` auto-loads the PSR-4 map at load time when PHP files are present and `source_root` is set. 61 new tests added.
- **PHP `property_declaration` symbol indexing** — PHP class properties (`protected $fillable`, `public string $name`, etc.) are now indexed as `property`-kind symbols, fixing a gap in PHP symbol coverage.
- **Laravel context provider** — new `LaravelContextProvider` detects Laravel projects (via `artisan` + `laravel/framework` in `composer.json`) and enriches symbols with: routes parsed from `routes/*.php`, Eloquent relationship/fillable/scope metadata from `app/Models/*.php`, controller-to-route mapping, and event→listener mappings from `EventServiceProvider`. Migration column definitions (from `database/migrations/*.php`) are exposed via `search_columns` under the `laravel_columns` key.
- **Framework profile auto-detection** — `detect_framework()` checks for Laravel, Nuxt, Next.js, Vue SPA, and React SPA at index time and applies framework-specific `ignore_patterns` (e.g. `vendor/`, `.nuxt/`, `.next/`) automatically. Profile `entry_point_patterns` and `layer_definitions` are stored in `context_metadata` for downstream use by `find_dead_code` and `get_layer_violations`. The `index_folder` result now includes `framework_profile` when a profile is active. Zero overhead for non-matching projects.

## [1.21.11] - 2026-04-02

### Added
- **`config --check` now detects CLAUDE.md and hook-script drift (issue #200)** — the existing check command gains two new sections. *CLAUDE.md check* reads `~/.claude/CLAUDE.md` and reports any canonical tool names absent from the file, pointing to `jcodemunch-mcp claude-md --generate` to fix them. *Hook scripts check* scans `~/.claude/hooks/jcodemunch_read_guard.*` and lists any tool names missing from the guard's feedback message.
- **`jcodemunch-mcp claude-md --generate`** — new subcommand that prints a ready-to-paste CLAUDE.md prompt-policy snippet listing all 45 tools in logical categories. `--format=append` outputs only the tools not yet mentioned in the existing `~/.claude/CLAUDE.md`, making it easy to diff-and-merge without rewriting the whole file.
- **`_CANONICAL_TOOL_NAMES` module-level tuple** — authoritative ordered list of every registered tool name, used by both the drift-detection checks and the snippet generator. Validated by a test that asserts no tool produced by `_build_tools_list()` is absent from the tuple.

## [1.21.10] - 2026-04-02

### Fixed
- **`index_folder` full re-index no longer crashes with `'dict' object has no attribute 'summary'` when an existing index is present (issue #198)** — `CodeIndex.symbols` is `list[dict]` (serialized symbol dicts), but the summary-preservation dict comprehension at the top of the full-index path used dot notation (`s.file`, `s.name`, `s.kind`, `s.summary`) instead of bracket notation (`s["file"]`, etc.). Any second full index (or first index when an in-memory stale cache remained after `invalidate_cache` on pre-1.21.8) would immediately fail with this `AttributeError`. Fixed by using `s["key"]` / `s.get("key")` throughout that comprehension. Regression test added.

## [1.21.9] - 2026-04-02

### Added
- **`workflow` MCP prompt** — Claude Code surfaces this as `/mcp__jcodemunch-mcp__workflow`, a slash command that injects step-by-step usage guidance (list_repos → search_symbols → get_symbol_source) directly into context. Provides reliable workflow instructions even when CLAUDE.md is absent or not loaded by the model.
- **`discovery_hint` config flag** (default `true`) — when enabled, the `list_repos` tool description includes a short note reminding Claude to prefer jcodemunch tools over native Grep/Read and to call `ToolSearch` if schemas appear deferred. Set `"discovery_hint": false` in `config.jsonc` to suppress this. Addresses jgravelle/jcodemunch-mcp#199.

## [1.21.8] - 2026-04-02

### Fixed
- **`invalidate_cache` now clears all four in-process caches (X1 / C4-B)** — previously `_REPO_PATH_CACHE`, `_PROJECT_CONFIGS`/`_PROJECT_CONFIG_HASHES`, `_alias_map_cache`, and `_bare_name_cache` were never evicted on `invalidate_cache`, leaving stale import graphs, wrong project config, and unresolvable repo names for the process lifetime. `invalidate_cache` now resolves `source_root` before deletion and clears all four caches in addition to the SQLite/JSON index.
- **`_alias_map_cache` evicted at the start of every `index_folder` run (C6-A)** — tsconfig/jsconfig path alias edits were permanently invisible to re-indexing because `_load_tsconfig_aliases` cached by `source_root` with no invalidation hook. `index_folder` now pops the stale entry before parsing begins, so alias-dependent import edges (`find_importers`, `find_references`, `get_dependency_graph`) are always computed against the current tsconfig.
- **`_sql_stem_cache` keyed by frozenset instead of `id()` (C7-A)** — the single-entry tuple cache used `id(source_files)` as its key. After the previous `source_files` set was GC'd, a new set allocated at the same address received the same `id`, causing a false cache hit and returning SQL stem mappings for the wrong file set. Replaced with a bounded frozenset-keyed dict (max 4 entries) for correct content-based identity.

## [1.21.7] - 2026-04-02

### Fixed
- **`search_symbols` no longer returns centrality-only results for out-of-corpus queries (C3)** — `_bm25_score` now guards the centrality bonus with `score > 0`, so it is only applied when at least one query term contributed BM25 relevance (or an exact name match fired). Previously, queries whose terms appeared in no indexed symbol produced BM25 score 0 for every symbol, but the unconditional `centrality` add-on gave structurally popular files scores > 0, causing them to pass the `score <= 0` filter and surface as apparent results with no indication they were purely import-graph artifacts.

## [1.21.6] - 2026-04-02

### Fixed
- **`_REPO_PATH_CACHE` negative entries no longer permanently suppress project config (C2)** — `_resolve_repo_key` previously wrote `None` into `_REPO_PATH_CACHE` for any identifier that couldn't be resolved at call time (e.g. during watcher startup before the first index completes). That entry was never invalidated, so all subsequent calls — including those after successful indexing — silently fell through to the global config, ignoring `.jcodemunch.jsonc` for the process lifetime. Removed the negative cache write; unknown identifiers now re-scan `list_repos()` on each call (cheap read) so project configs are picked up as soon as the repo is indexed.

## [1.21.5] - 2026-04-02

### Fixed
- **Deferred summarization no longer doubles `CodeIndex.symbols` in memory (C1)** — `_patch_index_from_delta` now builds a set of symbol IDs present in `new_sym_dicts` and skips any retained symbol whose ID is already being replaced. Previously, when `_run_deferred_summarize` called `incremental_save` with `changed_files=[]` and `deleted_files=[]`, every symbol was retained *and* appended again as a summarized copy, doubling the in-memory symbol list. This caused BM25 scores to be computed over a 2× corpus (wrong IDF, wrong `avgdl`) and `search_symbols` to return duplicate hits for the same symbol ID until the next cold cache load.

## [1.21.4] - 2026-04-02

### Added
- **JSON indexing and symbol extraction** — `.json` files are now indexed and text-searchable. Top-level object keys are extracted as `constant` symbols (e.g. `name`, `dependencies`, `scripts` in `package.json`; compiler options keys in `tsconfig.json`). Compound extensions `.openapi.json` / `.swagger.json` and well-known basenames (`openapi.json`, `swagger.json`) continue to resolve to `openapi` as before. Closes reported gap in issue #197 follow-up comment (nikolai-vysotskyi).
- **15 new tests** covering extension detection, compound-extension precedence, top-level key extraction, symbol kind/metadata, array-at-root edge case, and `parse_file()` dispatch.

## [1.21.3] - 2026-04-02

### Added
- **CSS preprocessor support (SCSS, SASS, Less, Stylus)** — `.scss`, `.sass`, `.less`, and `.styl` files are now indexed and text-searchable. SCSS additionally gets full symbol extraction: `$variables` → `constant`, `@mixin` → `function`, `@function` → `function`, rule-set selectors (including `%placeholders`) → `class`, `@media`/`@supports` → `type`. SASS, Less, and Stylus have no tree-sitter grammar in the pack so they index for text search only — `search_text` with `file_pattern: "**/*.scss"` now returns results. Closes issue #197 (reported by nikolai-vysotskyi).
- **24 new tests** covering SCSS extension detection, variable/mixin/function/selector/at-rule extraction, symbol ID uniqueness, byte metadata, `parse_file()` dispatch, empty-file edge case, and text-only confirmation for Less/SASS/Stylus.

## [1.21.2] - 2026-04-02

### Added
- **Summary preservation during full reindex** — when a full reindex runs over a repo that already has an index (e.g. after a schema bump or explicit `incremental=False` call), symbols whose file content hash is unchanged now reuse their existing AI-generated summaries instead of triggering new AI calls. Symbols in changed or new files are summarized normally. This is automatic and requires no parameter changes — the optimization fires whenever a prior index is present and has stored file hashes. Addresses issue #192 (reported by rknighton).

## [1.21.1] - 2026-04-02

### Fixed
- **`summarizer_concurrency` now respected by OpenAI-compatible provider** — `OpenAIBatchSummarizer` was reading concurrency from `OPENAI_CONCURRENCY` env var with a hardcoded default of 1, ignoring the `summarizer_concurrency` config key entirely. The default is now `_config.get("summarizer_concurrency", 4)`, so the config file (and `JCODEMUNCH_SUMMARIZER_CONCURRENCY` env var) correctly controls concurrency for all providers. `OPENAI_CONCURRENCY` env var still overrides when set. The `config` diagnostic display now shows the effective fallback value from config rather than the stale hardcoded 1. Reported by nikolai-vysotskyi (issue #194).

## [1.21.0] - 2026-04-02

### Added
- **CSS symbol extraction** — CSS files now produce real symbols: rule-set selectors (`.container`, `#header`, `body`, `:root`, compound selectors like `.navbar .item`) are extracted as `kind: class`; `@keyframes` as `kind: function`; `@media` and `@supports` blocks as `kind: type`. Previously CSS was indexed (text-searchable) but `get_file_outline` always returned 0 symbols. Fixes reported issue where users believed CSS was not supported at all.
- **17 new tests** (1641 total, 7 skipped): full coverage of CSS selector extraction, @-rule extraction, edge cases (empty file, comment-only file), symbol ID uniqueness, and `parse_file()` dispatch.

## [1.20.0] - 2026-04-02

### Changed
- **Lazy tool imports** — all 45 tool module imports in `server.py` are now deferred to the first `call_tool()` dispatch for each tool. Previously, importing `server.py` loaded every tool module (and their transitive dependencies: tree-sitter, httpx, pathspec, subprocess wrappers) regardless of which tools the session actually uses. Now only 7 tool modules load at startup (via the watcher's `index_folder` chain). Tools not called in a session are never imported. This reduces cold-start overhead for query-only sessions that never trigger indexing.
- **`_build_tools_list()` helper** — `list_tools()` now delegates to a named `_build_tools_list()` function, making the tool list construction easier to test and reason about independently of the MCP decorator.
- **Test patch targets updated** — tests that previously patched `jcodemunch_mcp.server.xxx` (where `xxx` is a tool function) now correctly patch `jcodemunch_mcp.tools.xxx_module.xxx_func`, which is where the name is looked up during dispatch. This follows Python's `unittest.mock.patch` best practice: patch where the name is looked up, not where it is defined.
- **No API or output schema changes.** Zero new tools, zero removed tools, zero field changes.

## [1.19.0] - 2026-04-01

### Added
- **`assessment` field on `get_hotspots` entries** — each hotspot now includes `assessment: "low" | "medium" | "high"` based on `hotspot_score` thresholds (low ≤ 3, medium ≤ 10, high > 10). Allows an LLM to relay findings directly without interpreting the raw score.
- **`architecture.layers` documented in README** — the `.jcodemunch.jsonc` reference now includes the full `architecture` block schema with a worked example for a typical layered Python project (api → service → repo → db). Used by `get_layer_violations`.
- **2 new tests** (1624 total, 7 skipped): `test_assessment_field_present`, `test_high_complexity_no_churn_is_low`.

## [1.18.0] - 2026-04-01

### Added
- **Session-level LRU result cache** — `get_blast_radius` and `find_references` (single-identifier mode) now cache their results for the duration of the MCP session. Repeated calls with the same arguments return instantly from the in-process cache with `_meta.cache_hit: true` instead of re-running the expensive BFS traversal and file-content scans. Cache is a 256-entry LRU (OrderedDict); oldest entries are evicted first. Thread-safe via the existing `_State` lock.
- **Automatic cache invalidation** — the result cache is cleared after any `index_repo`, `index_folder`, `index_file`, or `invalidate_cache` call so stale results are never served after re-indexing.
- **`get_session_stats` — `result_cache` field** — the existing `get_session_stats` tool now includes a `result_cache` section: `{total_hits, total_misses, hit_rate, cached_entries}`. Useful for tuning and for verifying that the cache is working in real sessions.
- **18 new tests** (1622 total, 7 skipped): `test_result_cache.py` covers get/put, hit/miss counters, by-tool breakdown, invalidation (all-repos and repo-specific), LRU eviction at maxsize, and the `result_cache` field in `get_session_stats`.

## [1.17.0] - 2026-04-01

### Added
- **`get_symbol_complexity(symbol_id)`** — returns cyclomatic complexity, max nesting depth, parameter count, line count, and a human-readable `assessment` ("low" / "medium" / "high") for any indexed function or method. Data is read directly from the index (no re-parsing); requires INDEX_VERSION 7 (jcodemunch-mcp >= 1.16).
- **`get_churn_rate(target, days=90)`** — returns git commit count, unique authors, first-seen date, last-modified date, and `churn_per_week` for a file or symbol over a configurable look-back window. `assessment` field: "stable" (≤1/week), "active" (≤3/week), "volatile" (>3/week). Accepts a relative file path or a symbol ID. Requires a locally indexed repo.
- **`get_hotspots(top_n=20, days=90, min_complexity=2)`** — ranks functions and methods by `hotspot_score = cyclomatic × log(1 + commits_last_N_days)`. Surfaces code that is both complex and frequently changed — the highest bug-introduction risk in the repo. Identical methodology to Adam Tornhill's CodeScene hotspot analysis. Falls back gracefully when git is unavailable (complexity-only scoring).
- **`get_repo_health(days=90)`** — one-call triage snapshot: total files/symbols, dead-code %, average cyclomatic complexity, top-5 hotspots, dependency cycle count, and unstable module count. Produces a `summary` string suitable for immediate relay. Designed to be the first tool called in any new session. Thin aggregator — delegates to individual tools, no duplicated logic.
- **Bug fix: complexity data now correctly persisted through `save_index`** — the symbol serialization dict in `save_index` was missing `cyclomatic`, `max_nesting`, and `param_count` fields (they were computed by the parser but silently dropped before DB write). Fixed by including these fields in the serialized dict. All tools depending on complexity data (`get_extraction_candidates`, `get_symbol_complexity`, `get_hotspots`) now return accurate values after a fresh `index_folder`.
- **36 new tests** (1604 total, 7 skipped): `test_symbol_complexity.py`, `test_churn_rate.py`, `test_hotspots.py`, `test_repo_health.py`.

## [1.16.0] - 2026-04-01

### Added
- **`check_rename_safe(symbol_id, new_name)`** — new tool that detects name collisions before renaming a symbol. Scans the symbol's defining file and every file that imports it, checking for an existing symbol already using the proposed new name. Returns `{safe, conflicts, checked_files}`. Use before any rename/refactor to avoid silent breakage.
- **`get_dead_code_v2()`** — enhanced dead-code detection with three independent evidence signals per function/method: (1) the symbol's file is not reachable from any entry point via the import graph, (2) no indexed symbol calls this symbol in the call graph, (3) the symbol name is not re-exported from any `__init__` or barrel file. Each result includes a `confidence` score (0.33 = 1 signal, 0.67 = 2 signals, 1.0 = all 3). More reliable than single-signal detection. Accepts `min_confidence` (default 0.5) and `include_tests` parameters.
- **`get_extraction_candidates(file_path, min_complexity, min_callers)`** — new tool that identifies functions worth extracting to a shared module. A candidate must have high cyclomatic complexity (doing a lot) AND be called from multiple other files (already implicitly shared). Results ranked by `score = cyclomatic × caller_file_count`.
- **Complexity metrics stored at index time** — `INDEX_VERSION` bumped from 6 to 7. Three new fields per symbol (functions and methods only): `cyclomatic` (McCabe complexity), `max_nesting` (bracket-nesting depth), `param_count`. Computed from symbol body text at index time via `parser/complexity.py`. Existing indexes are automatically migrated (columns added as NULL; re-index to populate). Consumed by `get_extraction_candidates`.
- **37 new tests** (1568 total, 7 skipped): `test_complexity.py`, `test_check_rename_safe.py`, `test_dead_code_v2.py`, `test_extraction_candidates.py`.

### Changed
- `INDEX_VERSION` is now 7 (was 6). Re-index required to populate complexity fields; existing indexes load and operate correctly with complexity = 0.

## [1.15.3] - 2026-04-01

### Added
- **`config --upgrade`** — new CLI flag that adds missing keys from the current version's template into an existing `config.jsonc`, preserving all user-set values. Useful after upgrading jcodemunch-mcp to a newer version that introduces new config keys. Updates the `"version"` field automatically and reports which keys were injected. Addresses the gap implied by the `"version"` field / "additive migrations" comment in `config.jsonc`. Requested by nikolai-vysotskyi in issue #191.

## [1.15.2] - 2026-04-01

### Added
- **`summarize_repo(repo, force)`** — new MCP tool that re-runs AI summarization on all symbols in an existing index. Useful when `index_folder` completed without AI summaries (deferred background thread was interrupted, AI was disabled at index time, or the provider wasn't configured). With `force=true`, clears all existing summaries and re-runs the full 3-tier pipeline (docstring → AI → signature fallback). Returns `{success, symbol_count, updated, skipped, duration_seconds}`. Reported by nikolai-vysotskyi in issue #190.
- **AI summarization progress logging** — `summarize_batch` (both `BaseSummarizer` and `OpenAIBatchSummarizer`) now logs progress at INFO level every ~10% of batches: `"AI summarization: N/M symbols (P%)"`. Start and completion are also logged. Previously there was zero feedback during 10–30 minute summarization runs on large codebases.
- **`summarization_deferred` field in `index_folder` response** — when the watcher-driven fast path fires a background summarization thread, the response now includes `"summarization_deferred": true` and a note suggesting `summarize_repo` as a synchronous fallback.

### Changed
- **Deferred summarization thread logging promoted to INFO** — thread start (`"Deferred AI summarization started for owner/repo (N symbols)"`) and completion (`"Deferred AI summarization saved N symbols for owner/repo"`) are now logged at INFO instead of DEBUG, making them visible in default logging configurations.

## [1.15.1] - 2026-04-01

### Fixed
- **Empty-array false positive in singular/batch mode detection** — `get_symbol_source`, `find_references`, `check_references`, `find_importers`, and `get_file_outline` each support a singular param (e.g. `symbol_id`) and a batch param (e.g. `symbol_ids`). Some MCP clients (observed with OpenCode + GPT codex) pass the batch param as an empty array `[]` even when invoking singular mode. Since `[] is not None` is `True`, the mutual-exclusivity guard fired and returned `"Provide symbol_id or symbol_ids, not both."` / `"Internal error processing find_references"`. Fixed by normalizing empty lists to `None` before the guard check in all five tools. Reported by razorree in issue #189.

## [1.15.0] - 2026-04-01

### Added
- **`get_dependency_cycles()`** — new tool detecting circular import chains in the repository. Uses Kosaraju's algorithm (iterative, no recursion limit) on the file-level import graph. Returns each strongly-connected component (set of files mutually reachable via imports) as a cycle. Useful for finding architectural problems and test-isolation blockers.
- **`get_coupling_metrics(module_path)`** — new tool returning afferent coupling (Ca, how many files import this module), efferent coupling (Ce, how many files this module imports), instability score I = Ce/(Ca+Ce), and a human-readable `assessment` ("stable" | "neutral" | "unstable" | "isolated"). Identifies fragile modules and guides refactoring priorities.
- **`get_layer_violations(rules?)`** — new tool validating inter-module imports against declared architectural layer boundaries. Reports every import that crosses a forbidden boundary. Rules can be passed directly or defined in `.jcodemunch.jsonc` under `architecture.layers`. Output includes `file`, `file_layer`, `import_target`, `target_layer`, `rule_violated` per violation.
- **`architecture` config key** — new `.jcodemunch.jsonc` / global config key (type: dict) for per-project layer definitions. Structure: `{"layers": [{"name": str, "paths": [str], "may_not_import": [str]}]}`. Consumed by `get_layer_violations` when no inline `rules` are provided.
- **36 new tests** (1527 total, 9 skipped) in `tests/test_architecture_tools.py`.

## [1.14.0] - 2026-04-01

### Added
- **`get_call_hierarchy(symbol_id, direction, depth)`** — new tool returning incoming callers and outgoing callees for any indexed symbol, N levels deep (default 3). Uses AST-derived detection: callers = symbols in importing files whose bodies mention the name; callees = imported symbols mentioned in the symbol's source body. No LSP required. Results include `{id, name, kind, file, line, depth}` per entry and `source: "ast"` in `_meta`.
- **`get_impact_preview(symbol_id)`** — new tool answering "what breaks if I delete or rename this?". DFS over the call graph transitively, returns all affected symbols grouped by file (`affected_by_file`) with call-chain paths (`call_chains`) showing how each symbol is reached from the target.
- **`_call_graph.py`** — shared internal module with `find_direct_callers`, `find_direct_callees`, `bfs_callers`, `bfs_callees` used by all call-graph tools.

### Changed
- **`get_blast_radius`** — new optional `call_depth` param (default 0, disabled). When `call_depth > 0`, adds `callers` list of symbols that actually call the target symbol (call-level analysis) alongside the existing import-level `confirmed`/`potential` lists. All existing fields unchanged; fully backwards-compatible.
- **`find_references`** — new optional `include_call_chain` param (default false, singular mode only). When true, each reference entry gains `calling_symbols`: symbols in that file whose source bodies mention the identifier. Batch mode ignores this flag.

## [1.13.2] - 2026-03-31

### Fixed
- **Per-project language config ignored during parsing** — `parse_file()` was calling `is_language_enabled(language)` without forwarding the `repo` path, so it always consulted the global config and never the per-project `.jcodemunch.jsonc`. Projects that declared their own `"languages"` list got `symbol_count: 0` when the global config had `"languages": []` (the recommended default). Fixed by threading `repo` from every `parse_file` call site (`index_folder`, `index_file`, `get_changed_symbols`, and all three pipeline functions in `_indexing_pipeline`) down to the language-gate check. `index_repo` is unaffected (remote repos have no local project config). Reported and root-caused by AmaralVini in issue #187.

## [1.13.1] - 2026-03-30

### Changed
- **`get_repo_outline` 2-level directory grouping for large repos** — when a repository has more than 500 indexed files, `directories` now groups by two path components (e.g., `src/api/`, `src/models/`) instead of only the top-level directory. Results are capped at 40 entries (highest file-count dirs first). Small repos (≤ 500 files) retain the existing 1-level behavior. Agents navigating large monorepos get actionable directory hints rather than a single coarse bucket.

## [1.13.0] - 2026-03-30

### Added
- **Cross-repository dependency tracking** — import graph tools (`find_importers`, `get_blast_radius`, `get_dependency_graph`, `get_changed_symbols`) now accept an opt-in `cross_repo: bool` parameter (default `false`). When enabled, the tools traverse repo boundaries using a package registry built from manifest files (`pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`, `*.csproj`). Cross-repo results are annotated with `"cross_repo": true` and `"source_repo"`. Zero behavior change when `cross_repo` is omitted.
- **`get_cross_repo_map` tool** — new tool that returns the full cross-repository dependency map at the package level, or filtered to a single repo. Shows `depends_on` and `depended_on_by` for each indexed repo, plus a flat `cross_repo_edges` list.
- **`package_names` field on `CodeIndex`** — package names are extracted from manifest files at index time (both `index_folder` and `index_repo`) and stored in the SQLite meta table. Old indexes load cleanly with `package_names = []`.
- **`package_registry.py`** — new module providing `extract_package_names()` (5 ecosystems: Python, JS/TS, Go, Rust, C#), `extract_root_package_from_specifier()` (language-aware root extraction), `build_package_registry()` (in-memory registry with mtime-based cache), and `resolve_cross_repo_file()`.
- **`cross_repo_default` config key** — boolean default for the `cross_repo` parameter across all import graph tools. Env var: `JCODEMUNCH_CROSS_REPO_DEFAULT`. Default: `false`.
- **53 new tests** (1431 total, 9 skipped).

## [1.12.9] — docs patch 2026-03-30

### Changed
- **QUICKSTART.md Step 3** — upgraded AGENT_HOOKS.md footnote to an `[!IMPORTANT]` callout naming the "pressure bypass" failure mode (agent sees CLAUDE.md rule, ignores it under load) and explaining why hooks are needed for hard enforcement.
- **QUICKSTART.md Troubleshooting** — added entry for "Claude uses jCodeMunch in simple tasks but falls back to Read/Grep in complex ones" pointing to AGENT_HOOKS.md.
- **AGENT_HOOKS.md intro** — sharpened to explicitly name the failure mode: the agent sees the rule and skips it anyway because native tools feel faster under pressure or in long sessions.

## [1.12.9] - 2026-03-29

### Added
- **Tri-state `use_ai_summaries`** (PR #186 — contributed by MariusAdrian88) — Config key and `JCODEMUNCH_USE_AI_SUMMARIES` env var now accept three values: `"auto"` (new default; auto-detect provider from API keys, identical to previous `true`), `true` (use explicit `summarizer_provider` + `summarizer_model` from config), `false` (disable AI summarization entirely). Existing boolean `true`/`false` configs are fully backward-compatible.
- **`summarizer_model` config key** — Override the default model for any provider via config or `JCODEMUNCH_SUMMARIZER_MODEL` env var. Priority: config key > provider-specific env var (`ANTHROPIC_MODEL`, `GOOGLE_MODEL`, etc.) > hardcoded default. Applies to all providers.
- **`summarizer_max_failures` config key** — Circuit breaker threshold (default 3). After this many consecutive batch failures the summarizer stops calling the API and falls back to signature summaries for all remaining symbols. A successful batch resets the counter. Set 0 to disable. Thread-safe (`threading.Lock`). Configurable via `JCODEMUNCH_SUMMARIZER_MAX_FAILURES`.
- **OpenRouter provider** — New provider via `OPENROUTER_API_KEY` using the OpenAI-compatible API at `openrouter.ai/api/v1`. Default model: `meta-llama/llama-3.3-70b-instruct:free` (zero cost). Auto-detect priority: last in chain (after GLM-5). Explicit selection: `summarizer_provider: "openrouter"` or `JCODEMUNCH_SUMMARIZER_PROVIDER=openrouter`. `jcodemunch-mcp config` now shows active OpenRouter section.
- **`test_summarizer` diagnostic tool** — Sends a probe request to the configured AI summarizer and reports status: `ok`, `disabled`, `no_provider`, `misconfigured`, `fallback`, `timeout`, or `error`. Disabled by default (remove from `disabled_tools` in config to enable). Optional `timeout_ms` parameter (default 15000).
- **`strict_timeout_ms` config key** — Configures the maximum milliseconds to block in `freshness_mode: strict` before proceeding with a stale index (previously hardcoded at 500ms). Default: 500.
- **`embed_model` config key** — Promotes `JCODEMUNCH_EMBED_MODEL` env var to a config file setting. Configures the sentence-transformers model for local semantic embeddings. Config key takes priority over env var.
- **`summarizer_provider` config key** — Promotes `JCODEMUNCH_SUMMARIZER_PROVIDER` env var to a config file setting. Takes priority over env var.
- **60+ new tests** (1397 total, 7 skipped).

### Fixed
- **`languages_adaptive` config key** (PR #185 — contributed by MariusAdrian88) — New boolean config key that enables automatic language detection based on files actually found in the indexed folder, overriding the `languages` allowlist for that run. Useful when indexing polyglot repos without maintaining an explicit language list.
- **`meta_fields` default changed to `[]`** (PR #185) — Previously defaulted to `null` (all meta fields included); now defaults to `[]` (no `_meta` block) for token-efficient responses. Set to `null` in config to restore all meta fields.

## [1.12.7] - 2026-03-29

### Added
- **MiniMax and GLM-5 summarizer providers** (PR #184 — contributed by SkaldeStefan) — `MINIMAX_API_KEY` auto-detects MiniMax M2.7 (`api.minimax.io/v1`) and `ZHIPUAI_API_KEY` auto-detects GLM-5 (`api.z.ai`), both via the existing OpenAI-compatible summarizer path. `JCODEMUNCH_SUMMARIZER_PROVIDER` env var added for explicit selection (`anthropic`, `gemini`, `openai`, `minimax`, `glm`, `none`). Auto-detect priority: Anthropic → Gemini → OpenAI-compatible → MiniMax → GLM-5. Remote providers (including MiniMax/GLM) still require `allow_remote_summarizer: true` in `config.jsonc`. `get_provider_name()` exported from `jcodemunch_mcp.summarizer`. `jcodemunch-mcp config` now shows active provider and new MiniMax/GLM sections. 10 new tests (1332 total).

### Fixed
- **`test_get_provider_name_unknown_falls_back_to_auto` test isolation** — test did not clear higher-priority env vars before auto-detecting MiniMax, causing false `anthropic` result in environments where `ANTHROPIC_API_KEY` is set.

## [1.12.6] - 2026-03-29

### Fixed
- **Gemini `CODE_RETRIEVAL_QUERY` KeyError on legacy SDK** (follow-up to #181) — The legacy `google-generativeai` package does not include `CODE_RETRIEVAL_QUERY` in its `TaskType` proto enum (it was introduced in the newer `google-genai` SDK). Passing that string to `genai.embed_content` caused a `KeyError` during semantic search. A new `_normalise_gemini_task_type` helper probes the installed SDK's `TaskType` enum at runtime and falls back `CODE_RETRIEVAL_QUERY` → `RETRIEVAL_QUERY` on legacy installs, producing equivalent retrieval quality. New SDK installs with `CODE_RETRIEVAL_QUERY` are unaffected. 5 new tests (1322 total).

## [1.12.5] - 2026-03-29

### Added
- **YAML and Ansible parser support** (PR #183 — contributed by SkaldeStefan) — `.yaml` and `.yml` files are now indexed as first-class symbols. A path-heuristic layer (`_looks_like_ansible_path`) automatically promotes Ansible-structured files (playbooks, roles, group_vars, host_vars, tasks, handlers, defaults) to the `ansible` language so they receive Ansible-aware symbol extraction: plays as `class`, tasks as `function`, roles and handlers as `type`, and variables as `constant`. Generic YAML falls back to a structural walker that emits container keys as `type` and scalar keys as `constant`. Multi-document YAML (multiple `---` sections) is handled correctly. pyyaml is already a base dependency — no extra install step. 8 new tests (1317 total).

## [1.12.4] - 2026-03-29

### Added
- **Task-aware embedding for Gemini** (closes #181) — When `GOOGLE_EMBED_MODEL` is configured, `embed_repo` now passes `task_type="RETRIEVAL_DOCUMENT"` to `genai.embed_content` for document indexing, and `search_symbols` passes `task_type="CODE_RETRIEVAL_QUERY"` when embedding the search query. Models that support task types (e.g. `text-embedding-004`, Gemini Embedding 2) produce measurably better code retrieval results; models that do not simply ignore the parameter. Other providers (sentence-transformers, OpenAI) are unaffected.
- **`GEMINI_EMBED_TASK_AWARE` env var** — Set to `0` / `false` / `no` / `off` to opt out of task-type routing (default: on). Useful if your Gemini model predates task-type support.
- **`embed_task_type` stored in meta** — The task type used when building the embedding index is now persisted. If you toggle `GEMINI_EMBED_TASK_AWARE`, `embed_repo` detects the mismatch and automatically forces a re-embed so query and document embeddings always come from the same task-type space.
- **`task_type` field in `embed_repo` response** — Present when a task type was applied; absent for providers that do not use one.
- 7 new tests (1309 total): `_gemini_task_aware` default/opt-out, Gemini document task type in `embed_repo`, `CODE_RETRIEVAL_QUERY` routing in `search_symbols`, opt-out disables task types, task-type change triggers re-embed, `EmbeddingStore` task type round-trip.

## [1.12.3] - 2026-03-29

### Fixed
- **Cross-process LRU cache invalidation** — SQLite WAL mode does not always update the `.db` file's mtime on commit. The watcher (a separate process) was writing new index data that the MCP server's in-memory cache never detected, causing agents to see stale results. New `_db_mtime_ns()` helper checks `max(db_mtime, db-wal_mtime)` so WAL writes are detected without an explicit cache eviction call. `os.utime()` added after `save_index()` and `incremental_save()` as a belt-and-suspenders measure; `os.utime()` runs *before* `_cache_put()` so the cached mtime matches what cross-process readers compute.
- **`get_file_tree` silently ignored `max_files`** — the parameter was present in the MCP schema but was never passed through `call_tool` dispatch.
- **Config template stale entries** — `wait_for_fresh` (removed v1.12.0) was still listed in `disabled_tools` template; staleness `_meta` fields (`index_stale`, `reindex_in_progress`, `stale_since_ms`) were still listed in `meta_fields` template.

### Added
- **`file_tree_max_files` config key** — configures the `get_file_tree` result cap via `config.jsonc` or `JCODEMUNCH_FILE_TREE_MAX_FILES` env var (default 500). Per-call `max_files` param still overrides.
- **`gitignore_warn_threshold` config key** — configures the missing-`.gitignore` warning threshold in `index_folder` via `config.jsonc` or `JCODEMUNCH_GITIGNORE_WARN_THRESHOLD` env var (default 500). Set `0` to disable entirely.
- **Config template overhaul** — all keys now have inline documentation; tools and meta_fields lists sorted alphabetically; all missing keys added (`trusted_folders_whitelist_mode`, `exclude_secret_patterns`, `path_map`, watcher params, transport docs); `version` field added for future migration tooling. Note: the template now defaults to `"meta_fields": []` (no `_meta` in responses) rather than `null` (all fields) — better for token efficiency; users who want `_meta` should uncomment the desired fields.
- 5 new tests covering `_db_mtime_ns` (no-WAL, WAL-newer, WAL-older) and the full cross-process cache invalidation scenario (1302 total). Contributed by MariusAdrian88 (PR #180).

## [1.12.2] - 2026-03-29

### Added
- **`.razor` (Blazor component) file support** — `.razor` extension now mapped to the `razor` language spec alongside `.cshtml`. `_parse_razor_symbols` extended to emit `@page` route directives and `@inject` dependency injection bindings as constant symbols, making Blazor component routes and injected services first-class navigable symbols. Includes `Counter.razor` test fixture and 8 new tests (1298 total). Contributed by drax1222 (PR #182).

## [1.12.1] - 2026-03-28

### Fixed
- **`get_file_tree` token overflow on large indexes** (closes #178) — results are now capped at `max_files` (default 500). When truncated, the response includes `truncated: true`, `total_file_count`, and a `hint` suggesting `path_prefix` to scope the query. `max_files` is exposed as a tool parameter so callers can raise it explicitly if needed.
- **`index_folder` silent over-inclusion** (closes #178) — when no `.gitignore` is present in the repo root and ≥ 500 files are indexed, a warning is now included in the result advising the user to add a `.gitignore` and re-index.
- 10 new tests (1288 total).

## [1.12.0] - 2026-03-28

### Removed
- **`check_freshness` and `wait_for_fresh` MCP tools** — no client ever consumed these; removing them saves ~400 schema tokens per call. Server-side freshness management via `freshness_mode` config key (`relaxed`/`strict`) remains fully functional.
- **Staleness `_meta` fields** (`index_stale`, `reindex_in_progress`, `stale_since_ms`) — ~30-50 tokens of annotated noise per response. The watcher still manages freshness internally; strict mode blocks silently in `call_tool` before returning clean results.
- `powered_by` removed from `_meta` common fields.

### Fixed
- **Watcher config layering** — `_get_watcher_enabled()` previously bypassed `config_module.get()` and read `JCODEMUNCH_WATCH` env var directly, silently ignoring the `"watch"` key in `config.jsonc`. Precedence is now: CLI flag > config file (with env var as fallback only when key absent).
- **Hash-cache miss reindex skip** — when the watcher's in-memory hash cache missed, the fallback read the file from disk. By the time `watchfiles` delivers the event the file already has new content, making `old_hash == new_hash` and silently skipping the change. Fixed with a `"__cache_miss__"` sentinel that guarantees re-parse on any cache miss.
- **Flaky Windows tests from SQLite WAL cache contamination** — tests that modified the DB directly didn't invalidate the in-memory LRU cache; WAL mode on Windows doesn't always update file mtime on write, so the cache key matched stale data. Fixed via `tests/conftest.py` autouse fixtures for cache clear and config reset, plus targeted `_cache_evict()` calls after direct DB writes.
- `test_openai_summarizer_timeout_config` now correctly flows `allow_remote_summarizer` through `load_config()` instead of reading from `config.get()` directly.

### Added
- **Config-driven watcher parameters** — all watcher options are now configurable via `config.jsonc` (CLI flags remain as overrides). New keys:
  - `watch_debounce_ms` (int, default 2000) — was wired in config.py but not forwarded to watcher kwargs
  - `watch_paths` (list, default `[]` → CWD) — folders to watch
  - `watch_extra_ignore` (list, default `[]`) — additional gitignore-style patterns
  - `watch_follow_symlinks` (bool, default `false`)
  - `watch_idle_timeout` (int or null, default `null`) — auto-stop after N minutes idle
  - `watch_log` (str or null, default `null`) — log watcher output to file; `"auto"` = temp file
- 25 new tests (1285 total).

## [1.11.17] - 2026-03-27

### Added
- **Optional semantic / embedding search (Feature 8)** — hybrid BM25 + vector search, opt-in only, zero mandatory new dependencies.
  - `search_symbols` gains three new params: `semantic` (bool, default `false`), `semantic_weight` (float 0–1, default 0.5), `semantic_only` (bool, default `false`). When `semantic=false` (default) there is zero performance impact and zero new imports.
  - **New `embed_repo` tool** — precomputes and caches all symbol embeddings in one pass (`batch_size`, `force` params). Optional warm-up; `search_symbols` lazily embeds missing symbols on first semantic query.
  - **New `EmbeddingStore`** — thin SQLite CRUD layer (`symbol_embeddings` table) in the existing per-repo `.db` file. Embeddings serialised as float32 BLOBs via stdlib `array` module. Persists across restarts; invalidatable per-symbol for incremental reindex.
  - **Three embedding providers** (priority order): local `sentence-transformers` (`JCODEMUNCH_EMBED_MODEL` env var), Gemini (`GOOGLE_API_KEY` + `GOOGLE_EMBED_MODEL`), OpenAI (`OPENAI_API_KEY` + `OPENAI_EMBED_MODEL`). `OPENAI_API_KEY` alone does **not** activate embeddings (prevents conflation with local-LLM summariser use).
  - **Hybrid ranking**: `combined = (1−w) × bm25_normalised + w × cosine_similarity`. BM25 normalised by max score over the candidate set. `semantic_weight=0.0` produces identical results to pure BM25.
  - **Pure Python cosine similarity** — `math.sqrt` + `sum()`, no numpy required.
  - `semantic=true` with no provider configured returns `{"error": "no_embedding_provider", "message": "..."}` (structured error, not a crash).
  - New optional dep: `pip install jcodemunch-mcp[semantic]` installs `sentence-transformers>=2.2.0`.
  - 22 new tests.

## [1.11.16] - 2026-03-27

### Added
- **Token-budgeted context assembly (Feature 5)** — two new capabilities:
  - `get_context_bundle` gains `token_budget`, `budget_strategy`, and `include_budget_report` params. When `token_budget` is set, symbols are ranked and trimmed to fit. `budget_strategy` controls how: `most_relevant` (default) ranks by file import in-degree, `core_first` keeps the primary symbol first then ranks the rest by centrality, `compact` strips all source bodies and returns signatures only. `include_budget_report=true` adds a `budget_report` field showing `budget_tokens`, `used_tokens`, `included_symbols`, `excluded_symbols`, and `strategy`. Fully backward-compatible: all new params default to existing behavior.
  - **New `get_ranked_context` tool** — standalone token-budgeted context assembler. Takes a `query` + `token_budget` (default 4000) and returns the best-fit symbols with their full source, greedy-packed by combined score. `strategy` controls ranking: `combined` (BM25 + PageRank weighted sum, default), `bm25` (pure text relevance), `centrality` (PageRank only). Optional `include_kinds` and `scope` params restrict the candidate set. Response includes per-item `relevance_score`, `centrality_score`, `combined_score`, `tokens`, and `source`. Token counting uses `len(text) // 4` heuristic with optional `tiktoken` upgrade (no hard dep). No new dependencies. 19 new tests.

## [1.11.15] - 2026-03-27

### Added
- **`get_changed_symbols` tool** — maps a git diff to affected symbols. Given two commits (`since_sha` / `until_sha`, defaulting to index-time SHA vs HEAD), returns `added_symbols`, `removed_symbols`, and `changed_symbols` (with `change_type`: "added", "removed", "modified", or "renamed"). `renamed` detection fires when body hash is identical but name differs. Set `include_blast_radius=true` to also return downstream importers (with `max_blast_depth` hop limit). Requires a locally indexed repo (`index_folder`); GitHub-indexed repos return a clear error. Requires `git` on PATH; graceful error if not available. Filters index-storage files (e.g. `.index/`) from the diff when the storage dir is inside the repo. No new dependencies. 12 new tests.

## [1.11.14] - 2026-03-27

### Added
- **`find_dead_code` tool** — finds files and symbols unreachable from any entry point using the import graph. Entry points auto-detected by filename (`main.py`, `__main__.py`, `conftest.py`, `manage.py`, etc.), `__init__.py` package roots, and `if __name__ == "__main__"` guards (Python only). Returns `dead_files` and `dead_symbols` with confidence scores: `1.0` = zero importers, no framework decoration; `0.9` = zero importers in a test file; `0.7` = all importers are themselves dead (cascading). Parameters: `granularity` ("symbol"/"file"), `min_confidence` (default 0.8), `include_tests` (bool), `entry_point_patterns` (additional glob roots). No new dependencies. 13 new tests.

## [1.11.13] - 2026-03-27

### Fixed
- **Manifest watcher reliability** — replaced `watchfiles.awatch()` in `_manifest_watcher` with a simple 0.5s polling loop. `watchfiles` was unreliable on Windows (especially in temp directories used by tests and agent hooks), causing the manifest watcher to silently miss create/remove events. Polling the manifest file's size every 500ms is sufficient for this append-only JSONL file and works reliably on all platforms.

## [1.11.12] - 2026-03-27

### Added
- **PageRank / centrality ranking** — new `get_symbol_importance` tool returns the most architecturally important symbols in a repo, ranked by full PageRank or simple in-degree on the import graph. Parameters: `top_n` (default 20), `algorithm` ("pagerank" or "degree"), `scope` (subdirectory filter). Response includes `symbol_id`, `rank`, `score`, `in_degree`, `out_degree`, `kind`, `iterations_to_converge`. New `sort_by` parameter on `search_symbols` ("relevance" | "centrality" | "combined") — "centrality" filters by BM25 query match but ranks by PageRank; "combined" adds PageRank as weighted boost to BM25 score; "relevance" (default) is unchanged (backward compatible). `get_repo_outline` now includes `most_central_symbols` (top 10 symbols by PageRank score, one representative per file, alongside the existing `most_imported_files`). PageRank implementation: damping=0.85, convergence threshold=1e-6, max 100 iterations, dangling-node correction, cached in `_bm25_cache` per `CodeIndex` load. 23 new tests.

## [1.11.11] - 2026-03-27

### Added
- **Fuzzy symbol search** — `search_symbols` gains three new parameters: `fuzzy` (bool, default `false`), `fuzzy_threshold` (float, default `0.4`), and `max_edit_distance` (int, default `2`). When enabled, a trigram Jaccard + Levenshtein pass runs as fallback when BM25 confidence is low (top score < 0.1) or when explicitly requested. Fuzzy results carry `match_type="fuzzy"`, `fuzzy_similarity`, and `edit_distance` fields; BM25 results carry `match_type="exact"`. Zero behavioral change when `fuzzy=false` (default). No new dependencies — pure stdlib (`frozenset` trigrams + Wagner-Fischer edit distance). 21 new tests.

## [1.11.10] - 2026-03-27

### Added
- **Blast radius depth scoring** — `get_blast_radius` now always returns `direct_dependents_count` (depth-1 count) and `overall_risk_score` (0.0–1.0, weighted by hop distance using `1/depth^0.7`). New `include_depth_scores=true` parameter adds `impact_by_depth` (files grouped by BFS layer, each with a `risk_score`). Flat `confirmed`/`potential` lists are preserved unchanged (backward compatible). 14 new tests.

## [1.11.9] - 2026-03-27

### Fixed
- **Windows CI: trusted_folders tests** — `_platform_path_str` was using `str(Path(...))` which on Windows returns backslash paths (`C:\work`). When embedded raw into f-string JSON literals in tests, the backslash produced invalid `\escape` sequences, causing `config.jsonc` parse failures across all 4 Windows matrix legs (6 tests failing). Fixed by switching to `.as_posix()`, which returns forward-slash paths (`C:/work`) that are valid in both JSON and Windows pathlib.

## [1.11.8] - 2026-03-27

### Added
- **`trusted_folders` allowlist for `index_folder`** (PR #175, credit: @tmeckel) — new `trusted_folders` config key (plus `trusted_folders_whitelist_mode`) restricts or blocks indexing by path. Whitelist mode (default) allows only explicitly named roots; blacklist mode blocks specific paths while trusting all others. Path-aware matching (not string-prefix). Project config supports `.`, `./subdir`, and bare relative paths. Escape-attempt paths are rejected. Empty list preserves existing behavior (backward compatible). Env var fallback via `JCODEMUNCH_TRUSTED_FOLDERS`.

## [1.11.7] - 2026-03-27

### Added
- **`check_freshness` tool** — compares the git HEAD SHA recorded at index time against the current HEAD for locally indexed repos. Returns `fresh` (bool), `indexed_sha`, `current_sha`, and `commits_behind`. GitHub repos return `is_local: false` with an explanatory message. `get_repo_outline` staleness check upgraded to SHA-based comparison (accurate) with time-based fallback for GitHub/no-git repos; `is_stale` added to `_meta`. 8 new tests.

## [1.11.6] - 2026-03-27

### Added
- **Structured file-cap warnings** — `index_folder` and `index_repo` now surface `files_discovered`, `files_indexed`, and `files_skipped_cap` fields plus a human-readable `warning` when the file cap is hit. Previously a silent "note".
- **`_meta` hint on single-symbol responses** — `search_symbols` and `get_symbol_source` single-symbol responses now include a `_meta` hint pointing to `get_context_bundle`.

### Changed
- **Benchmark docs** — `METHODOLOGY.md` expanded with a "Common Misreadings" section; reproducible results table added to README.

## [1.11.5] - 2026-03-26

### Fixed
- **`tsconfig.json`/`jsconfig.json` parsed as JSONC** — previously `json.loads()` silently failed on commented tsconfigs (TypeScript projects commonly use `//` comments in tsconfig.json), leaving `alias_map` empty and causing `find_importers`/`get_blast_radius` to return 0 alias-based results. Now parsed with the same JSONC stripper used for `config.jsonc`. Also adds a test for nested layouts with specific `@/lib/*` overrides. Closes #170. 5 new tests.

## [1.11.4] - 2026-03-25

### Fixed
- **TypeScript/SvelteKit path alias resolution** — `find_importers`, `get_blast_radius`, `get_dependency_graph`, and 5 other import-graph tools now resolve `@/*`, `$lib/*`, and other configured aliases by reading `compilerOptions.paths` from `tsconfig.json`/`jsconfig.json` at the project root. Also resolves TypeScript's ESM `.js`→`.ts` extension convention. `alias_map` is auto-loaded from `source_root` and cached at module level. Closes #169. 10 new tests.

## [1.11.3] - 2026-03-25

### Added
- **Debug logging for silent skip paths** — all three skip paths (`skip_dir`, `skip_file`, `secret`) now emit debug-level log lines. `skip_dir` and `skip_file` counters added to the discovery summary. `exclude_secret_patterns` config option suppresses specific `SECRET_PATTERNS` entries (workaround for `*secret*` glob false-positives on full relative paths in Go monorepos). (PR #168, credit: @DrHayt) 6 new tests.

## [1.11.2] - 2026-03-25

### Fixed
- **`resolve_repo` hang on Windows** — added `stdin=subprocess.DEVNULL` to the git subprocess call in `_git_toplevel()`. Without it, the git child process inherits the MCP stdio pipe and blocks indefinitely. Same pattern fixed in v1.1.7 for `index_folder`. Closes #166.
- **`parse_git_worktrees` hang on Windows** (watcher) — same missing `stdin=subprocess.DEVNULL` fix, preventative.

## [1.8.3] - 2026-03-18

### Added
- **`find_importers`: `has_importers` flag** — each result now includes `has_importers: bool`. When `false`, the importer itself has no importers, revealing transitive dead code chains without requiring recursive calls. Implemented as one additional O(n) pass over the import graph; no re-indexing required. Closes #132. Identified via 50-iteration dead code A/B test (#130).

## [1.8.2] - 2026-03-18

### Changed
- **`get_file_outline` tool description** — now explicitly states "full signatures (including parameter names)" and adds "Use signatures to review naming at parameter granularity without reading the full file." Parameter names were always present in the `signature` field; the description now makes this discoverable. Closes #131.

## [1.8.1] - 2026-03-18

### Fixed
- **Dynamic `import()` detection in JS/TS/Vue** — `find_importers` now detects Vue Router lazy routes and other code-splitting patterns using `import('specifier')` call syntax. Previously these files appeared to have zero importers and were misclassified as dead. Identified via 50-iteration dead code A/B test (#130, @Mharbulous); 4 Vue view files affected.

## [1.8.0] - 2026-03-18

### Security
- **Supply-chain integrity check** — `verify_package_integrity()` added to `security.py` and called at startup. Uses `importlib.metadata.packages_distributions()` to identify the distribution that actually owns the running code. If it differs from the canonical `jcodemunch-mcp`, a `SECURITY WARNING` is printed to stderr. Catches the fork-republishing attack class described at https://news.ycombinator.com/item?id=47428217. Silent for source/editable installs.

### Added
- **`authors` and `[project.urls]`** in `pyproject.toml` — PyPI pages now display official provenance metadata (author, homepage, issue tracker).

## [1.7.9] - 2026-03-18

### Added
- **JS/TS const extraction** — top-level `const` and `export const` declarations in JavaScript, TypeScript, and TSX are now indexed as `constant` symbols. Arrow functions and function expressions assigned to consts are correctly skipped (handled by existing function extraction). Accepts all identifier naming conventions for JS/TS.
- **`index_file` tool** (PR #126, credit: @thellMa) — re-index a single file instantly after editing. Locates the correct index by scanning `source_root` of all indexed repos (picks most specific match), validates security, computes hash + mtime, and exits early if the file is unchanged. Parses with tree-sitter, runs context providers, and calls `incremental_save()` for a surgical single-file update. Registered as a new MCP tool with `path`, `use_ai_summaries`, and `context_providers` parameters.
- **mtime optimization** (PR #126, credit: @thellMa) — `index_folder` and `index_repo` now check file modification time (`st_mtime_ns`) before reading or hashing. Files with unchanged mtimes are skipped entirely; hashes are computed lazily only for files whose mtime changed. Indexes store a `file_mtimes` dict; old indexes without mtime data fall back to hash-all for backward compatibility.
- **`watch-claude` CLI subcommand** — auto-discover and watch Claude Code worktrees via two complementary modes:
  - **Hook-driven mode** (recommended): install `WorktreeCreate`/`WorktreeRemove` hooks that call `jcodemunch-mcp hook-event create|remove`. Events are written to `~/.claude/jcodemunch-worktrees.jsonl` and `watch-claude` reacts instantly via filesystem watch.
  - **`--repos` mode**: `jcodemunch-mcp watch-claude --repos ~/project1 ~/project2` polls `git worktree list --porcelain` and filters for Claude-created worktrees (branches matching `claude/*` or `worktree-*`).
  - Both modes can run simultaneously. When a worktree is removed, the watcher stops and the index is invalidated.
- **`hook-event` CLI subcommand** — `jcodemunch-mcp hook-event create|remove` reads Claude Code's hook JSON from stdin and appends to the JSONL manifest. Designed to be called from Claude Code's `WorktreeCreate`/`WorktreeRemove` hooks.

### Changed
- **Shared indexing pipeline** (PR #126, credit: @thellMa) — new `_indexing_pipeline.py` consolidates logic previously duplicated across `index_folder`, `index_repo`, and the new `index_file`: `file_languages_for_paths()`, `language_counts()`, `complete_file_summaries()`, `parse_and_prepare_incremental()`, and `parse_and_prepare_full()`. All three tools now call the shared pipeline functions.
- `main()` subcommand set expanded to include `hook-event` and `watch-claude`.

## [1.7.2] - 2026-03-17

### Fixed
- **Stale `context_metadata` on incremental save** — `{}` from active providers was treated as falsy, silently preserving old metadata instead of clearing it. Changed to `is not None` check.
- **`_resolve_description` discarding surrounding text** — `"Prefix {{ doc('name') }} suffix"` now preserves both prefix and suffix instead of returning only the doc block content.
- **dbt tags only extracted from `config.tags`** — top-level `model.tags` (valid in dbt schema.yml) are now merged with `config.tags`, deduplicated.
- **Redundant `posixpath.sep` check** in `resolve_specifier` — removed duplicate of adjacent `"/" not in` check.
- **Inaccurate docstring** on `_detect_dbt_project` — said "max 2 levels deep" but only checks root + immediate children.

### Changed
- **Concurrent AI summarization** — `BaseSummarizer.summarize_batch()` now uses `ThreadPoolExecutor` (default 4 workers) for Anthropic and Gemini providers. Configurable via `JCODEMUNCH_SUMMARIZER_CONCURRENCY` env var. Matches the pattern already used by `OpenAIBatchSummarizer`. ~4x faster on large projects.
- **O(1) stem resolution** — `resolve_specifier` stem-matching fallback now uses a cached dict lookup instead of O(n) linear scan. Significant perf improvement for dbt projects with thousands of files, called in tight loops across 7 tools.
- **`collect_metadata` collision warning** — logs a warning when two providers emit the same metadata key, instead of silently overwriting via `dict.update()`.
- **`find_importers`/`find_references` tool descriptions** — now note that `{{ source() }}` edges are extracted but not resolvable since sources are external.
- **`search_columns` cleanup** — moved `import fnmatch` to top-level; documented empty-query + `model_pattern` behavior (acts as "list all columns for matching models").

## [1.7.0] - 2026-03-17

### Added
- **Centrality ranking** — `search_symbols` BM25 scores now include a log-scaled bonus for symbols in frequently-imported files, surfacing core utilities as tiebreakers when relevance scores are otherwise equal.
- **`get_symbol_diff`** — diff two indexed snapshots by `(name, kind)`. Reports added, removed, and changed symbols using `content_hash` for change detection. Index the same repo under two names to compare branches.
- **`get_class_hierarchy`** — traverse inheritance chains upward (ancestors via `extends`/`implements`/Python parentheses) and downward (subclasses/implementors) from any class. Handles external bases not in the index.
- **`get_related_symbols`** — find symbols related to a given one via three heuristics: same-file co-location (weight 3.0), shared importers (1.5), name-token overlap (0.5/token).
- **Git blame context provider** — `GitBlameProvider` auto-activates during `index_folder` when a `.git` directory is present. Runs a single `git log` at index time and attaches `last_author` + `last_modified` to every file via the existing context provider plugin system.
- **`suggest_queries`** — scan the index and get top keywords, most-imported files, kind/language distribution, and ready-to-run example queries. Ideal first call when exploring an unfamiliar repository.
- **Markdown export** — `get_context_bundle` now accepts `output_format="markdown"`, returning a paste-ready document with import blocks, docstrings, and fenced source code.

## [1.6.1] - 2026-03-17

### Added
- **`watch` CLI subcommand** (PR #113, credit: @DrHayt) — `jcodemunch-mcp watch <path>...` monitors one or more directories for filesystem changes and triggers incremental re-indexing automatically. Uses `watchfiles` (Rust-based, async) for OS-native notifications with configurable debounce. Install with `pip install jcodemunch-mcp[watch]`.
- `watchfiles>=1.0.0` optional dependency under `[watch]` and `[all]` extras.

### Changed
- `main()` refactored to use argparse subcommands (`serve`, `watch`). Full backwards compatibility preserved — bare `jcodemunch-mcp` and legacy flags like `--transport` continue to work unchanged.

## [1.6.0] - 2026-03-17

### Added
- **`get_context_bundle` multi-symbol bundles** — new `symbol_ids` (list) parameter fetches multiple symbols in one call. Import statements are deduplicated when symbols share a file. New `include_callers=true` flag appends the list of files that directly import each symbol's defining file.

### Changed
- Single `symbol_id` (string) remains fully backward-compatible.

## [1.5.9] - 2026-03-17

### Added
- **`get_blast_radius` tool** — find every file affected by changing a symbol. Given a symbol name or ID, traverses the reverse import graph (up to 3 hops) and text-scans each importing file. Returns `confirmed` (imports the file + references the symbol name) and `potential` (imports the file only — wildcard/namespace imports). Handles ambiguous names by listing all candidate IDs.

## [1.5.8] - 2026-03-17

### Changed
- **BM25 search** — replaced hand-tuned substring scoring in `search_symbols` with proper BM25 + IDF. IDF is computed over all indexed symbols at query time (no re-indexing required). CamelCase/snake_case tokenization splits `getUserById` into `get`, `user`, `by`, `id` for natural language queries. Per-field repetition weights: name 3×, keywords 2×, signature 2×, summary 1×, docstring 1×. Exact name match retains a +50 bonus. `debug=true` now returns per-field BM25 score breakdowns.

## [1.5.7] - 2026-03-17

### Added
- **`get_dependency_graph` tool** — file-level import graph with BFS traversal up to 3 hops. `direction` parameter: `imports` (what this file depends on), `importers` (what depends on this file), or `both`. Returns nodes, edges, and per-node neighbor map. Built from existing index data — no re-indexing required.

## [1.5.6] - 2026-03-17

### Added
- **`get_session_stats` tool** — process-lifetime token savings dashboard. Reports tokens saved and cost avoided (current session + all-time cumulative), per-tool breakdown, session duration, and call counts.

## [1.5.5] - 2026-03-17

### Added
- **Tiered loading** (`detail_level` on `search_symbols`) — `compact` returns id/name/kind/file/line only (~15 tokens/result, ideal for discovery); `standard` is unchanged (default); `full` inlines source, docstring, and end_line.
- `byte_length` field added to all `search_symbols` result entries regardless of detail level.

## [1.5.4] - 2026-03-17

### Added
- **Token budget search** (`token_budget=N` on `search_symbols`) — greedily packs results by byte length until the budget is exhausted. Overrides `max_results`. Reports `tokens_used` and `tokens_remaining` in `_meta`.

## [1.5.3] - 2026-03-17

### Added
- **Microsoft Dynamics 365 Business Central AL language support** (PR #110, credit: @DrHayt) — `.al` files are now indexed. Extracts procedures, triggers, codeunits, tables, pages, reports, and XML ports.

## [1.5.2] - 2026-03-17

### Fixed
- `tokens_saved` always reporting 0 in `get_file_outline` and `get_repo_outline`.

## [1.5.1] - 2026-03-16

### Added
- **Benchmark reproducibility** — `benchmarks/METHODOLOGY.md` with full reproduction details.
- **HTTP bearer token auth** — `JCODEMUNCH_HTTP_TOKEN` env var secures HTTP transport endpoints.
- **`JCODEMUNCH_REDACT_SOURCE_ROOT`** env var redacts absolute local paths from responses.
- **Schema validation on index load** — rejects indexes missing required fields.
- **SHA-256 checksum sidecars** — index integrity verification on load.
- **GitHub rate limit retry** — exponential backoff in `fetch_repo_tree`.
- **`TROUBLESHOOTING.md`** with 11 common failure scenarios and solutions.
- CI matrix extended to Windows and Python 3.13.

### Changed
- Token savings labeled as estimates; `estimate_method` field added to all `_meta` envelopes.
- `search_text` raw byte count now only includes files with actual matches.
- `VALID_KINDS` moved to a `frozenset` in `symbols.py`; server-side validation rejects unknown kinds.

## [1.5.0] - 2026-03-16

### Added
- **Cross-process file locking** via `filelock` — prevents index corruption under concurrent access.
- **LRU index cache with mtime invalidation** — re-reads index JSON only when the file changes on disk.
- **Metadata sidecars** — `list_repos` reads lightweight sidecar files instead of loading full index JSON.
- **Streaming file indexing** — peak memory reduced from ~1 GB to ~500 KB during large repo indexing.
- **Bounded heap search** — `O(n log k)` instead of `O(n log n)` for bounded result sets.
- **`BaseSummarizer` base class** — deduplicates `_build_prompt`/`_parse_response` across AI summarizers.
- +13 new tests covering `search_columns`, `get_context_bundle`, and ReDoS hardening.

### Fixed
- **ReDoS protection** in `search_text` — pathological regex patterns are rejected before execution.
- **Symlink-safe temp files** — atomic index writes use `tempfile` rather than direct overwrite.
- **SSRF prevention** — API base URL validation rejects non-HTTP(S) schemes.

## [1.4.4] - 2026-03-16

### Added
- **Assembly language support** (PR #105, credit: @astrobleem) — WLA-DX, NASM, GAS, and CA65 dialects. `.asm`, `.s`, `.wla` files indexed. Extracts labels, macros, sections, and directives as symbols.
- `"asm"` added to `search_symbols` language filter enum.

## [1.4.3] - 2026-03-15

### Fixed
- Cross-process token savings loss — `token_tracker` now uses additive flush so savings accumulated in one process are not overwritten by a concurrent flush from another.

## [1.4.2] - 2026-03-15

### Added
- XML `name` and `key` attribute extraction — elements with `name=` or `key=` attributes are now indexed as `constant` symbols (closes #102).

## [1.4.1] - 2026-03-14

### Added
- **Minimal CLI** (`cli/cli.py`) — 47-line command-line interface over the shared `~/.code-index/` store covering all jMRI ops: `list`, `index`, `outline`, `search`, `get`, `text`, `file`, `invalidate`.
- `cli/README.md` — explains MCP as the preferred interface and documents CLI usage.

### Changed
- README onboarding improved: added "Step 3: Tell Claude to actually use it" with copy-pasteable `CLAUDE.md` snippets.

## [1.4.0] - 2026-03-13

### Added
- **AutoHotkey hotkey indexing** — all three hotkey syntax forms are now extracted as `kind: "constant"` symbols: bare triggers (`F1::`), modifier combos (`#n::`), and single-line actions (`#n::Run "notepad"`). Only indexed at top level (not inside class bodies).
- **`#HotIf` directive indexing** — both opening expressions (`#HotIf WinActive(...)`) and bare reset (`#HotIf`) are indexed, searchable by window name or expression string.
- **Public benchmark corpus** — `benchmarks/tasks.json` defines the 5-task × 3-repo canonical task set in a tool-agnostic format. Any code retrieval tool can be evaluated against the same queries and repos.
- **`benchmarks/README.md`** — full methodology documentation: baseline definition, jMunch workflow, how to reproduce, how to benchmark other tools.
- **`benchmarks/results.md`** — canonical tiktoken-measured results (95.0% avg reduction, 20.2x ratio, 15 task-runs). Replaces the obsolete v0.2.22 proxy-based benchmark files.
- Benchmark harness now loads tasks from `tasks.json` when present, falling back to hardcoded values.

## [1.3.9] - 2026-03-13

### Added
- **OpenAPI / Swagger support** — `.openapi.yaml`, `.openapi.yml`, `.openapi.json`, `.swagger.yaml`, `.swagger.yml`, `.swagger.json` files are now indexed. Well-known basenames (`openapi.yaml`, `swagger.json`, etc.) are auto-detected regardless of directory. Extracts: API info block, paths as `function` symbols, schema definitions as `class` symbols, and reusable component schemas.
- `get_language_for_path` now checks well-known OpenAPI basenames before compound-extension matching.
- `"openapi"` added to `search_symbols` language filter enum.

## [1.3.8] - 2026-03-13

### Added
- **`get_context_bundle` tool** — returns a self-contained context bundle for a symbol: its definition source, all direct imports, and optionally its callers/implementers. Replaces the common `get_symbol` + `find_importers` + `find_references` round-trip with a single call. Scoped to definition + imports in this release.

## [1.3.7] - 2026-03-13

### Added
- **C# properties, events, and destructors** (PR #100) — `get { set {` property accessors, `event EventHandler Name`, and `~ClassName()` destructors are now extracted as symbols alongside existing C# method/class support.

## [1.3.6] - 2026-03-13

### Added
- **XML / XUL language support** (PR #99) — `.xml` and `.xul` files are now indexed. Extracts: document root element as a `type` symbol, elements with `id` attributes as `constant` symbols, and `<script src="...">` references as `function` symbols. Preceding `<!-- -->` comments captured as docstrings.

## [1.3.5] - 2026-03-13

### Added
- **GitHub blob SHA incremental indexing** — `index_repo` now stores per-file blob SHAs from the GitHub tree response and diffs them on re-index. Only files whose SHA changed are re-downloaded and re-parsed. Previously, every incremental run downloaded all file contents before discovering what changed.
- **Tokenizer-true benchmark harness** — `benchmarks/harness/run_benchmark.py` measures real tiktoken `cl100k_base` token counts for the jMunch retrieval workflow vs an "open every file" baseline on identical tasks. Produces per-task markdown tables and a grand summary.

## [1.3.4] - 2026-03-13

### Added
- **Search debug mode** — `search_symbols` now accepts `debug=True` to return per-result field match breakdown (name score, signature score, docstring score, keyword score). Makes ranking decisions inspectable.

## [1.3.3] - 2026-03-12

### Added
- **`search_columns` tool** — structured column metadata search across indexed models. Framework-agnostic: auto-discovers any provider that emits a `*_columns` key in `context_metadata` (dbt, SQLMesh, database catalogs, etc.). Returns model name, file path, column name, and description. Supports `model_pattern` glob filtering and source attribution when multiple providers contribute. 77% fewer tokens than grep for column discovery.
- **dbt import graph** — `find_importers` and `find_references` now work for dbt SQL models. Extracts `{{ ref('model') }}` and `{{ source('source', 'table') }}` calls as import edges, enabling model-level lineage and impact analysis out of the box.
- **Stem-matching resolution** — `resolve_specifier()` now resolves bare dbt model names (e.g., `dim_client`) to their `.sql` files via case-insensitive stem matching. No path prefix needed.
- **`get_metadata()` on ContextProvider** — new optional method for providers to persist structured metadata at index time. `collect_metadata()` pipeline function aggregates metadata from all active providers with error isolation.
- **`context_metadata` on CodeIndex** — new field for persisting provider metadata (e.g., column info) in the index JSON. Survives incremental re-indexes.
- Updated `CONTEXT_PROVIDERS.md` with column metadata convention (`*_columns` key pattern), `get_metadata()` API docs, architecture data flow, and provider ideas table

### Changed
- `search_columns` tool description updated to reflect framework-agnostic design
- `_LANGUAGE_EXTRACTORS` now includes `"sql"` mapping to `_extract_sql_dbt_imports()`

## [1.2.11] - 2026-03-10

### Added
- **Context provider framework** (PR #89, credit: @paperlinguist) — extensible plugin system for enriching indexes with business metadata from ecosystem tools. Providers auto-detect their tool during `index_folder`, load metadata from project config files, and inject descriptions, tags, and properties into AI summaries, file summaries, and search keywords. Zero configuration required.
- **dbt context provider** — the first built-in provider. Auto-detects `dbt_project.yml`, parses `{% docs %}` blocks and `schema.yml` files, and enriches symbols with model descriptions, tags, and column metadata. Install with `pip install jcodemunch-mcp[dbt]`.
- `JCODEMUNCH_CONTEXT_PROVIDERS=0` env var and `context_providers=False` parameter to disable provider discovery entirely
- `context_enrichment` key in `index_folder` response reports stats from all active providers
- `CONTEXT_PROVIDERS.md` — architecture docs, dbt provider details, and community authoring guide for new providers

## [1.2.9] - 2026-03-10

### Fixed
- **Eliminated redundant file downloads on incremental GitHub re-index** (fixes #86) — `index_repo` now stores the GitHub tree SHA after every successful index and compares it on subsequent calls before downloading any files. If the tree SHA is unchanged, the tool returns immediately ("No changes detected") without a single file download. Previously, every incremental run fetched all file contents from GitHub before discovering nothing had changed, causing 25–30 minute re-index sessions. The fast-path adds only one API call (the tree fetch, which was already required) and exits in milliseconds when the repo hasn't changed.
- **`list_repos` now exposes `git_head`** — so AI agents can reason about index freshness without triggering any download. When `git_head` is absent or doesn't match the current tree SHA, the agent knows a re-index is warranted.

## [1.2.8] - 2026-03-09

### Fixed
- **Massive folder indexing speedup** (PR #80, credit: @briepace) — directory pruning now happens at the `os.walk` level by mutating `dirnames[:]` before descent. Previously, skipped directories (node_modules, venv, .git, dist, etc.) were fully walked and their files discarded one by one. Now the walker never enters them at all. Real-world result: 12.5 min → 30 sec on a vite+react project.
  - Fixed `SKIP_FILES_REGEX` to use `.search()` instead of `.match()` so suffix patterns like `.min.js` and `.bundle.js` are correctly matched against the end of filenames
  - Fixed regex escaping on `SKIP_FILES` entries (`re.escape`) and the xcodeproj/xcworkspace patterns in `SKIP_DIRECTORIES`

## [1.2.7] - 2026-03-09

### Fixed
- **Performance: eliminated per-call disk I/O in token savings tracker** — `record_savings()` previously did a disk read + write on every single tool call. Now uses an in-memory accumulator that flushes to disk every 10 calls and at process exit via `atexit`. Telemetry is also batched at flush time instead of spawning a new thread per call. Fixes noticeable latency on rapid tool use sequences (get_file_outline, search_symbols, etc.).

## [1.2.6] - 2026-03-09

### Added
- **SQL language support** — `.sql` files are now indexed via `tree-sitter-sql` (derekstride grammar)
  - CREATE TABLE, VIEW, FUNCTION, INDEX, SCHEMA extracted as symbols
  - CTE names (`WITH name AS (...)`) extracted as function symbols
  - dbt Jinja preprocessing: `{{ }}`, `{% %}`, `{# #}` stripped before parsing
  - dbt directives extracted as symbols: `{% macro %}`, `{% test %}`, `{% snapshot %}`, `{% materialization %}`
  - Docstrings from preceding `--` comments and `{# #}` Jinja block comments
  - 27 new tests covering DDL, CTEs, Jinja preprocessing, and all dbt directive types
- **Context provider framework** — extensible plugin system for enriching indexes with business metadata from ecosystem tools. Providers auto-detect their tool during `index_folder`, load metadata from project config files, and inject descriptions, tags, and properties into AI summaries, file summaries, and search keywords. Zero configuration required.
- **dbt context provider** — the first built-in provider. Auto-detects `dbt_project.yml`, parses `{% docs %}` blocks and `schema.yml` files, and enriches symbols with model descriptions, tags, and column metadata.
- `context_enrichment` key in `index_folder` response reports stats from all active providers
- New optional dependency: `pip install jcodemunch-mcp[dbt]` for schema.yml parsing (pyyaml)
- `CONTEXT_PROVIDERS.md` documentation covering architecture, dbt provider details, and guide for writing new providers
- 58 new tests covering the context provider framework, dbt provider, and file summary integration

### Fixed
- `test_respects_env_file_limit` now uses `JCODEMUNCH_MAX_FOLDER_FILES` (the correct higher-priority env var) instead of the legacy `JCODEMUNCH_MAX_INDEX_FILES`

## [1.2.5] - 2026-03-08

### Added
- `staleness_warning` field in `get_repo_outline` response when the index is 7+ days old — configurable via `JCODEMUNCH_STALENESS_DAYS` env var

## [1.2.4] - 2026-03-08

### Added
- `duration_seconds` field in all `index_folder` and `index_repo` result dicts (full, incremental, and no-changes paths) — total wall-clock time rounded to 2 decimal places
- `JCODEMUNCH_USE_AI_SUMMARIES` env var now mentioned in `index_folder` and `index_repo` MCP tool descriptions for discoverability
- Integration test verifying `index_folder` is dispatched via `asyncio.to_thread` (guards against event-loop blocking regressions)

## [1.0.0] - 2026-03-07

First stable release. The MCP tool interface, index schema (v3), and symbol
data model are now considered stable.

### Languages supported (25)
Python, JavaScript, TypeScript, TSX, Go, Rust, Java, C, C++, C#, Ruby, PHP,
Swift, Kotlin, Dart, Elixir, Gleam, Bash, Nix, Vue SFC, EJS, Verse (UEFN),
Laravel Blade, HTML, and plain text.

### Highlights from the v0.x series
- Tree-sitter AST parsing for structural, not lexical, symbol extraction
- Byte-offset content retrieval — `get_symbol` reads only the bytes for that
  symbol, never the whole file
- Incremental indexing — re-index only changed files on subsequent runs
- Atomic index saves (write-to-tmp, then rename)
- `.gitignore` awareness and configurable ignore patterns
- Security hardening: path traversal prevention, symlink escape detection,
  secret file filtering, binary file detection
- Token savings tracking with cumulative cost-avoided reporting
- AI-powered symbol summaries (optional, requires `anthropic` extra)
- `get_symbols` batch retrieval
- `context_lines` support on `get_symbol`
- `verify` flag for content hash drift detection

### Performance (added in v0.2.31)
- `get_symbol` / `get_symbols`: O(1) symbol lookup via in-memory dict (was O(n))
- Eliminated redundant JSON index reads on every symbol retrieval
- `SKIP_PATTERNS` consolidated to a single source of truth in `security.py`

### Breaking changes from v0.x
- `slugify()` removed from the public `parser` package export (was unused)
- Index schema v3 is incompatible with v1 indexes — existing indexes will be
  automatically re-built on first use
