# jcodemunch-mcp — Project Brief

See also: `C:\MCPs\CLAUDE.md` for universal workflow and shell conventions.

## Current State
- **Version:** 1.5.0 (published to PyPI)
- **INDEX_VERSION:** 4
- **Tests:** 599 passed, 4 skipped
- **Python:** >=3.10

## Key Files
```
src/jcodemunch_mcp/
  server.py                    # MCP tool definitions + call_tool dispatcher (async)
  security.py                  # Path validation, skip patterns, get_max_folder_files(), get_max_index_files()
  parser/
    languages.py               # LANGUAGE_REGISTRY, extension → language map, LanguageSpec
    extractor.py               # parse_file() dispatch + custom parsers (_parse_erlang_symbols, _parse_fortran_symbols)
    symbols.py                 # Symbol dataclass
    hierarchy.py               # Parent/child relationship builder
    imports.py                 # NEW v1.3.0 — regex-based import extraction; extract_imports(), resolve_specifier()
  storage/
    index_store.py             # CodeIndex dataclass, IndexStore.save/load/has_index/detect_changes/incremental_save
  summarizer/
    batch_summarize.py         # 3-tier AI summarizer: Anthropic > Gemini > OpenAI-compat > signature fallback
    file_summarize.py          # Heuristic file-level summaries (symbols only, no docstrings)
  tools/
    index_folder.py            # Local folder indexer (sync, run via asyncio.to_thread in server.py)
    index_repo.py              # GitHub repo indexer (async)
    get_file_tree.py
    get_file_outline.py
    get_file_content.py
    get_symbol.py / get_symbols.py
    search_symbols.py
    search_text.py
    search_columns.py            # Search column metadata across dbt/SQLMesh models
    get_repo_outline.py
    get_context_bundle.py        # Symbol source + file imports in one call
    list_repos.py
    invalidate_cache.py
    find_importers.py            # NEW v1.3.0 — find all files that import a given file
    find_references.py           # NEW v1.3.0 — find all files that reference a given identifier
    _utils.py
```

## Architecture Notes
- `index_folder` is **synchronous** — dispatched via `asyncio.to_thread()` in server.py to avoid blocking the event loop (bug fixed in v1.1.4; was root cause of MCP timeouts)
- `index_repo` is **async** (uses httpx for GitHub API)
- `has_index()` distinguishes "no file on disk" from "file exists but version rejected" — used to surface version-mismatch warnings
- `get_max_folder_files()` defaults to 2,000 (separate from `get_max_index_files()` which defaults to 10,000)
- Symbol lookup is O(1) via `__post_init__` id dict in `CodeIndex`

## Languages Supported (25+)
Python, JavaScript, TypeScript, Java, C, C++, C#, Go, Rust, Ruby, PHP, Swift,
Kotlin, Scala, R, Julia, Haskell, Lua, Bash, CSS, SQL, TOML, Erlang, Fortran, ...

SQL has a custom parser (`_parse_sql_symbols`) with a companion `sql_preprocessor.py`
that strips Jinja templating (dbt models) before tree-sitter parsing and extracts
dbt directives (macro/test/snapshot/materialization) as first-class symbols.

Custom parsers (tree-sitter grammar lacks clean named fields):
- **Erlang** (`_parse_erlang_symbols`): multi-clause function merging by (name, arity), arity-qualified names (e.g. `add/2`), type/record/define
- **Fortran** (`_parse_fortran_symbols`): module-as-container, qualified names (`math_utils::multiply`), parameter constants

## Env Vars
| Var | Default | Purpose |
|-----|---------|---------|
| `CODE_INDEX_PATH` | `~/.code-index/` | Index storage location |
| `JCODEMUNCH_MAX_INDEX_FILES` | 10,000 | File cap for repo indexing |
| `JCODEMUNCH_MAX_FOLDER_FILES` | 2,000 | File cap for folder indexing |
| `JCODEMUNCH_USE_AI_SUMMARIES` | true | Set false/0/no/off to disable AI summaries globally |
| `JCODEMUNCH_EXTRA_IGNORE_PATTERNS` | — | Always-on gitignore patterns (comma-sep or JSON array); merged with per-call extra_ignore_patterns |
| `JCODEMUNCH_STALENESS_DAYS` | 7 | Days before get_repo_outline emits a staleness_warning |
| `JCODEMUNCH_MAX_RESULTS` | 500 | Hard cap on search_columns result count |
| `JCODEMUNCH_SHARE_SAVINGS` | 1 | Set 0 to disable anonymous token savings telemetry |
| `ANTHROPIC_API_KEY` | — | Enables Claude Haiku summaries (install `[anthropic]` extra) |
| `ANTHROPIC_MODEL` | claude-haiku-* | Override Anthropic model |
| `GOOGLE_API_KEY` | — | Enables Gemini Flash summaries (install `[gemini]` extra) |
| `GOOGLE_MODEL` | gemini-flash-* | Override Gemini model |
| `OPENAI_API_BASE` | — | Local LLM endpoint (Ollama, LM Studio) |
| `OPENAI_MODEL` | qwen3-coder | Local LLM model name |
| `OPENAI_API_KEY` | local-llm | Local LLM key (placeholder) |
| `OPENAI_TIMEOUT` | 60.0 | Local LLM request timeout |
| `OPENAI_BATCH_SIZE` | 10 | Symbols per summarization request |
| `OPENAI_CONCURRENCY` | 1 | Parallel batch requests to local LLM |
| `OPENAI_MAX_TOKENS` | 500 | Max output tokens per batch |

## Summarizer Priority
1. `ANTHROPIC_API_KEY` → Claude Haiku (`pip install jcodemunch-mcp[anthropic]`)
2. `GOOGLE_API_KEY` → Gemini Flash (`pip install jcodemunch-mcp[gemini]`)
3. `OPENAI_API_BASE` → local LLM via OpenAI-compatible endpoint
4. Signature fallback (always available, no deps)

## PR / Issue History

### Merged / Closed
| # | Author | What |
|---|--------|------|
| #7 | eresende | Recommend uvx — incorporated into README manually |
| #8 | eresende | Local LLM summarization via OpenAI-compatible endpoints |
| #12 | josh-stephens | resolve_repo() deduplication, input validation, CI coverage |
| #13 | josh-stephens | anthropic as optional dep — applied manually |
| #15 | josh-stephens | Incremental indexing (`incremental=` param) |
| #61 | snafu4 | Token-stats CLI — reviewed, deferred (out of scope for MCP server) |
| #69 | (community) | Erlang support request → implemented in v1.1.3, issue closed |
| #70 | (community) | Fortran support request → implemented in v1.1.3, issue closed |
| #71 | zrk02 | Concurrent batch summarization + local LLM tuning docs → merged |
| #75 | Clubbers | JCODEMUNCH_EXTRA_IGNORE_PATTERNS env var → shipped v1.2.2 |
| #76 | oderwat | Secret filter false positives on doc files → fixed v1.2.3 |
| #80 | briepace | Folder indexing speedup: prune dirnames[:] at os.walk level → merged v1.2.8 |
| #82 | paperlinguist | SQL language support with dbt Jinja preprocessing → merged v1.2.6 |

### Open PRs / Issues
None — queue is clear as of 2026-03-12.

### Action Required
- **Yank jcodemunch-cli 1.0.0 from PyPI** — package is discontinued, broken, and should not be installable. Log in to pypi.org → jcodemunch-cli → Manage → Release 1.0.0 → "Yank release". Issue #84 closed.

### Recently Closed Issues
| # | What | Resolution |
|---|------|-----------|
| #68 | index_folder timeouts (all versions, even 2 files) | Fixed in v1.1.4 — sync call was blocking asyncio event loop; wrapped in asyncio.to_thread() |
| #69 | Erlang language support | Implemented v1.1.3 |
| #70 | Fortran language support | Implemented v1.1.3 |
| #74 | index_folder still stuck/hanging on Windows in v1.1.5 | Fixed in v1.1.7 — two causes: (1) subprocess.run without stdin=DEVNULL inherited MCP stdio pipe causing protocol corruption when event loop was live; (2) rglob() followed NTFS junctions causing infinite walk — replaced with os.walk(followlinks=False) |
| #75 | JCODEMUNCH_EXTRA_IGNORE_PATTERNS env var request | Shipped v1.2.2 |
| #76 | Secret filter false positives on doc files | Fixed v1.2.3 |

## Roadmap / Backlog
| Priority | Item |
|----------|------|
| ~~P0~~ | ~~Add find_importers + find_references tools~~ — done v1.3.0 |
| ~~P0~~ | ~~Wrap all sync read tools in asyncio.to_thread()~~ — done v1.1.8 |
| ~~P0~~ | ~~Move `import functools` out of call_tool hot path to module top~~ — done v1.1.5 |
| ~~P1~~ | ~~Merge PR #62 (Swift parsing + Xcode ignores)~~ — done v1.1.9 |
| ~~P1~~ | ~~Close PR #61 (token-stats CLI)~~ — closed, suggested jcodemunch-cli as separate package |
| ~~P2~~ | ~~Add structured logging (server startup, index events, version mismatches)~~ — done v1.2.0 |
| ~~P2~~ | ~~Promote SKIP_PATTERNS to named frozenset at module top in security.py~~ — done v1.2.1 |
| ~~P2~~ | ~~Fix search_symbols language enum in MCP schema~~ — done v1.1.5 |
| ~~P3~~ | ~~Add duration_seconds to index result dicts for user visibility~~ — done (unpublished) |
| ~~P3~~ | ~~Mention JCODEMUNCH_USE_AI_SUMMARIES in index_folder/index_repo tool descriptions~~ — done (unpublished) |
| ~~P3~~ | ~~Integration test for asyncio.to_thread dispatch in call_tool~~ — done (unpublished) |
| ~~P4~~ | ~~Docker image~~ — dropped; pip/uvx install story is already frictionless, Docker adds perception risk with no meaningful gain |
| ~~P4~~ | ~~Index staleness warning in get_repo_outline if index is N days old~~ — done (unpublished) |

## Version History
| Version | What |
|---------|------|
| 0.2.x | Pre-stable iterations |
| 1.0.0 | Stable release |
| 1.0.1 | Lua language support |
| 1.1.0 | Repo identity, full-file indexing, get_file_content, search_text context |
| 1.1.1 | Minor fixes |
| 1.1.2 | Version mismatch detection (has_index()), lower folder file cap (2,000), version mismatch warnings |
| 1.1.3 | Erlang + Fortran language support |
| 1.1.4 | Fix asyncio blocking bug in index_folder; add JCODEMUNCH_USE_AI_SUMMARIES env var |
| 1.1.5 | Nested .gitignore support; complete search_symbols language enum; housekeeping |
| 1.1.6 | Full Vue SFC support: Composition API (ref/computed/defineProps/etc.) + Options API (methods/computed/props) |
| 1.1.7 | Fix index_folder hang on Windows: stdin=DEVNULL for git subprocess; os.walk(followlinks=False) replaces rglob |
| 1.1.8 | Wrap all sync read tools in asyncio.to_thread() — prevents event loop blocking on every query call |
| 1.1.9 | Improved Swift parsing (typealias, deinit, property_declaration); Xcode project ignore rules; 2 new Swift tests |
| 1.2.0 | Structured logging: startup, per-call, index lifecycle, version mismatch warnings |
| 1.2.1 | SKIP_PATTERNS promoted to named frozenset at module top in security.py |
| 1.2.2 | JCODEMUNCH_EXTRA_IGNORE_PATTERNS env var — closes #75 |
| 1.2.3 | Fix *secret* false positives on doc files (.md, .rst, etc.) — closes #76 |
| 1.2.4 | duration_seconds in index results; JCODEMUNCH_USE_AI_SUMMARIES in tool descriptions; asyncio.to_thread integration test |
| 1.2.5 | staleness_warning in get_repo_outline when index >= JCODEMUNCH_STALENESS_DAYS old (default 7) |
| 1.2.6 | SQL language support: DDL symbols, CTEs, dbt Jinja preprocessing, dbt directives (macro/test/snapshot/materialization) |
| 1.2.7 | Perf fix: token tracker in-memory accumulator; eliminated per-call disk read/write + per-call thread spawn |
| 1.2.8 | Folder indexing speedup: prune dirnames[:] before os.walk descent; SKIP_FILES_REGEX .search() fix; re.escape on file patterns |
| 1.2.9–1.2.12 | Various fixes (see git log) |
| 1.3.0 | find_importers + find_references tools: regex import extraction for 19 languages, import graph persisted in index, resolve_specifier for relative path resolution |
| 1.3.1 | HTTP transport modes: --transport sse/streamable-http, --host, --port; also JCODEMUNCH_TRANSPORT/HOST/PORT env vars; default 127.0.0.1:8901 |
| 1.5.0 | Hardening release: ReDoS protection, symlink-safe temp files, cross-process file locking, bounded heap search, metadata sidecars, LRU index cache, SSRF prevention, streaming file indexing, consolidated skip patterns, BaseSummarizer dedup, exception logging, search_columns + get_context_bundle tests |
| 1.4.4 | Assembly language support (WLA-DX, NASM, GAS, CA65): labels, sections, macros, constants, structs, enums, .proc, imports — contributed by astrobleem (PR #105) |
| 1.4.3 | Fix cross-process savings loss: token_tracker _flush_locked now writes additive delta instead of overwriting with in-process total — reported in PR #103 |
| 1.4.2 | XML: extract name/key identity attributes as symbols (alongside id); qualified_name encodes tag::value (e.g. block::foundationConcrete) — closes #102 |
| 1.3.2 | search_text: is_regex=true for full regex (alternation, patterns); improved context_lines description (grep -C analogy); get_file_outline accepts 'file' alias for 'file_path' |
