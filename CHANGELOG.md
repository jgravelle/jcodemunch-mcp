# Changelog

All notable changes to jcodemunch-mcp are documented here.

## [Unreleased]

### Added
- **SQL language support** — `.sql` files are now indexed via `tree-sitter-sql` (derekstride grammar)
  - CREATE TABLE, VIEW, FUNCTION, INDEX, SCHEMA extracted as symbols
  - CTE names (`WITH name AS (...)`) extracted as function symbols
  - dbt Jinja preprocessing: `{{ }}`, `{% %}`, `{# #}` stripped before parsing
  - dbt directives extracted as symbols: `{% macro %}`, `{% test %}`, `{% snapshot %}`, `{% materialization %}`
  - Docstrings from preceding `--` comments and `{# #}` Jinja block comments
  - 27 new tests covering DDL, CTEs, Jinja preprocessing, and all dbt directive types

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
