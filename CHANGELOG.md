# Changelog

All notable changes to jcodemunch-mcp are documented here.

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
