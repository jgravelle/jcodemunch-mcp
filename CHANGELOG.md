# Changelog

All notable changes to jcodemunch-mcp are documented here.

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
