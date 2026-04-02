# Changelog

All notable changes to jcodemunch-mcp are documented here.

## [Unreleased]

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
