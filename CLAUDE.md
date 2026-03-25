# jcodemunch-mcp — Project Brief

## Current State
- **Version:** 1.10.20 (published to PyPI)
- **INDEX_VERSION:** 4
- **Tests:** 1101 passed, 7 skipped
- **Python:** >=3.10

## Key Files
```
src/jcodemunch_mcp/
  server.py                    # MCP tool definitions + call_tool dispatcher (async); also hosts _run_config(), _make_auth_middleware(), _make_rate_limit_middleware(), and CLI subcommand dispatch
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
    get_symbol.py                # get_symbol_source — symbol_id→flat object or symbol_ids[]→{symbols,errors}
    search_symbols.py
    search_text.py
    search_columns.py            # Search column metadata across dbt/SQLMesh models
    get_repo_outline.py
    get_context_bundle.py        # Symbol source + file imports in one call
    list_repos.py
    resolve_repo.py              # O(1) path-to-repo-ID lookup; avoids full list_repos scan
    invalidate_cache.py
  config.py                      # Centralized JSONC config: global + per-project layering, env var fallback, language/tool gating
    find_importers.py            # NEW v1.3.0 — find all files that import a given file
    find_references.py           # NEW v1.3.0 — find all files that reference a given identifier
    _utils.py
```

## CLI Subcommands
| Subcommand | Purpose |
|------------|---------|
| `serve` (default) | Run the MCP server (`stdio`, `sse`, or `streamable-http`) |
| `watch <paths>` | File watcher — auto-reindex on change |
| `watch-claude` | Auto-discover and watch Claude Code worktrees |
| `hook-event create\|remove` | Record a worktree lifecycle event (called by Claude Code hooks) |
| `index-file <path>` | Re-index a single file within an existing indexed folder (used by PostToolUse hooks) |
| `config` | Print effective configuration grouped by concern |
| `config --check` | Also validate prerequisites (storage writable, AI pkg installed, HTTP pkgs present) |

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
| `JCODEMUNCH_PATH_MAP` | — | Remap stored path prefixes at retrieval time; format: `orig1=new1,orig2=new2`. Allows an index built on one machine (e.g. Linux `/home/user`) to be used on another (e.g. Windows `D:\Users\user`) without re-indexing |
| `JCODEMUNCH_STALENESS_DAYS` | 7 | Days before get_repo_outline emits a staleness_warning |
| `JCODEMUNCH_MAX_RESULTS` | 500 | Hard cap on search_columns result count |
| `JCODEMUNCH_SHARE_SAVINGS` | 1 | Set 0 to disable anonymous token savings telemetry |
| `JCODEMUNCH_STATS_FILE_INTERVAL` | 3 | Calls between session_stats.json writes; set 0 to disable (reduces NVMe writes) |
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
| `JCODEMUNCH_HTTP_TOKEN` | — | Bearer token for HTTP transport auth (opt-in) |
| `JCODEMUNCH_RATE_LIMIT` | 0 | Max requests per minute per client IP in HTTP transport (0 = disabled) |
| `JCODEMUNCH_REDACT_SOURCE_ROOT` | 0 | Set 1 to replace source_root with display_name in responses |

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
| #109 | DrHayt | Fix SKIP_DIRS_REGEX missing `$` anchor — prevented prefix-match pruning (e.g. `proto` eating `protoc-gen-*`) → merged |
| #158 | iEdgir01 | JCODEMUNCH_PATH_MAP — cross-platform path prefix remapping; merged v1.10.18 |
| #160 | DrHayt | resolve_repo tool — O(1) path-to-repo-ID lookup; merged v1.10.19 |
| #162 | MariusAdrian88 | Centralized JSONC config: language filtering, tool gating, meta control, per-project overrides; merged v1.10.20 |


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
| 1.3.2 | search_text: is_regex=true for full regex (alternation, patterns); improved context_lines description (grep -C analogy); get_file_outline accepts 'file' alias for 'file_path' |
| 1.4.2 | XML: extract name/key identity attributes as symbols (alongside id); qualified_name encodes tag::value (e.g. block::foundationConcrete) — closes #102 |
| 1.4.3 | Fix cross-process savings loss: token_tracker _flush_locked now writes additive delta instead of overwriting with in-process total — reported in PR #103 |
| 1.4.4 | Assembly language support (WLA-DX, NASM, GAS, CA65): labels, sections, macros, constants, structs, enums, .proc, imports — contributed by astrobleem (PR #105) |
| 1.5.0 | Hardening release: ReDoS protection, symlink-safe temp files, cross-process file locking, bounded heap search, metadata sidecars, LRU index cache, SSRF prevention, streaming file indexing, consolidated skip patterns, BaseSummarizer dedup, exception logging, search_columns + get_context_bundle tests |
| 1.9.1 | (see git log) |
| 1.9.2 | session_stats.json: token_tracker now writes ~/.code-index/session_stats.json (with last_updated timestamp) on every flush and on explicit get_session_stats calls — enables VS Code status bar extensions and other external stat consumers (closes #140) |
| 1.10.0 | Restore in-memory LRU cache (regression from WAL PR #141) + schema v5: promote 6 JSON data fields to real columns (eliminates json.loads per row on cold reads); mmap_size=256MB + temp_store=MEMORY pragmas; v4→v5 auto-migration; warm reads now 0ms (cache hit), cold reads -26% — contributed by MariusAdrian88 (PR #148) |
| 1.10.1 | JCODEMUNCH_STATS_FILE_INTERVAL env var: configurable session_stats.json write frequency (default 3, 0 = disable) — reduces NVMe writes for users who don't use the VS Code extension (closes #140 follow-up from MariusAdrian88) |
| 1.10.2 | Razor (.cshtml) language support: dedicated CshtmlExtractor handling @functions/@code blocks, @model/@using/@inject directives, HTML ID extraction, and embedded script/style blocks — contributed by outoftheblue9 (PR #150) |
| 1.10.3 | Perf: search_symbols (filtered) up to 25x faster via inverted index; find_references 14x faster via lazy import-name inverted index; watcher reindex 30x faster (3s → 96ms) via changed_paths fast path; safe_name cache key bug fix (permanent cache misses for repos with special chars); BM25 internal keys no longer leak into API responses; carry forward token bags across incremental reindex — contributed by MariusAdrian88 (PR #152) |
| 1.10.4 | Security hardening: timing-safe bearer token comparison (hmac.compare_digest); GitHub URL hostname validation blocking SSRF + token leakage; sanitized call_tool exception responses (full trace to server log, generic message to client); 500-char query length limit on search_symbols and search_text |
| 1.10.5 | Perf + security: folder_path redacted in all index_folder responses when JCODEMUNCH_REDACT_SOURCE_ROOT=1 (S6); bounded heap in token_budget search mode O(N log K) instead of O(N log N) (P1); incremental_save in-memory patch path — O(delta) instead of O(total rows) on cache hit, eliminates SELECT * FROM symbols on every watcher cycle (P6) |
| 1.10.6 | Perf + security: search_text uses readlines() instead of f.read()+split (P4, avoids large intermediate string on big files); _is_gitignored_fast uses os.path.normcase for case-insensitive prefix matching on Windows (S7); 3 new T5 tests for normcase behaviour |
| 1.10.7 | Perf: bare-name repo lookup cache in resolve_repo (P5) — mtime-gated module-level dict avoids O(N) list_repos() scan on every tool call; cost when warm is one stat() per call |
| 1.10.8 | Perf: token_tracker telemetry now uses a single daemon worker thread + queue.Queue (P11) — eliminates per-flush thread creation; _share_savings enqueues work instead of spawning Thread |
| 1.10.9 | Perf: discover_local_files merges two os.walk passes into one (P8) — .gitignore specs loaded incrementally during file enumeration, eliminating redundant full-tree walk |
| 1.10.10 | Security: optional per-IP rate-limiting middleware for HTTP transport (S9) — set JCODEMUNCH_RATE_LIMIT=N to cap N requests/minute per client IP; disabled by default |
| 1.10.11 | UX: `jcodemunch-mcp config` subcommand — prints effective configuration grouped by concern; `--check` validates storage path, AI provider package, and HTTP transport packages |
| 1.10.12 | UX: `get_repo_outline` now includes `most_imported_files` (top 10 by import in-degree, requires import graph); `get_symbol_diff` MCP description now includes step-by-step branch diff workflow |
| 1.10.13 | Perf: remove 4-model cost table from per-tool _meta (cost_avoided now returns {}); trim 6 verbose tool descriptions — reduces schema tokens ~249/turn and _meta overhead ~71 tokens/call (×9 tools, compounding) |
| 1.10.14 | Watcher backpressure + index freshness: memory hash cache (~57ms savings/tick), watcher fast path (~3s → ~50ms on Windows), deferred AI summarization (index queryable immediately), per-repo reindex state with threading.Event signaling, _meta staleness fields on all query responses, wait_for_fresh tool, --freshness-mode=strict CLI flag; 78 regression tests — contributed by MariusAdrian88 (PR #154) |
| 1.10.15 | Perf: suppress_meta param on all 25 tools — add suppress_meta=true to strip _meta envelope (~100-200 tokens/call savings); injected at list_tools() time via single property dict, stripped in call_tool dispatcher; 3 new tests — requested by Mharbulous (#142) |
| 1.10.16 | Docs: expand tool reference in USER_GUIDE.md, QUICKSTART.md, and README.md with new tools; add AGENT_HOOKS.md cross-links (PR #155 by gokhanozdemir) |
| 1.10.17 | CLI: `index-file <path>` subcommand — re-index a single file from the shell; enables PostToolUse hooks for automatic index freshness after agent edits (PR #156 by gokhanozdemir) |
| 1.10.18 | Feat: JCODEMUNCH_PATH_MAP env var — cross-platform path prefix remapping so an index built on one machine (e.g. Linux /home/user) can be reused on another (e.g. Windows C:\Users\user) without re-indexing; directory-boundary-aware matching, mixed-separator support, =signs in paths; 22 tests — contributed by iEdgir01 (PR #158); fix: remap() returns path unchanged on no-match (prevented Windows separator regression) |
| 1.10.19 | Feat: resolve_repo tool — O(1) path-to-repo-ID lookup; accepts repo root, worktree, subdirectory, or file path; computes deterministic hash ID and checks index existence directly; returns full metadata when indexed or hint to call index_folder when not; ~200 tokens vs potentially thousands from list_repos; 8 tests — contributed by DrHayt (PR #160) |
| 1.10.20 | Feat: centralized JSONC config system — global ~/.code-index/config.jsonc + per-project .jcodemunch.jsonc; language filtering (skip parsers for unlisted languages); tool gating (disabled_tools removes from schema); meta_fields allowlist replaces suppress_meta in schema; description overrides; env vars deprecated as fallback-only; hash-based project config cache; config --init CLI; 110 new tests (1101 total) — contributed by MariusAdrian88 (PR #162) |
| 1.10.21 | Perf: token efficiency pass — remove `powered_by` string from default `_meta` (~20 tokens/call), remove `repo`/`query`/`is_regex`/`context_lines` echo from search responses (~15 tokens/call), move BM25 `score` field to debug-only mode (~30 tokens/search); combined ~65 tokens saved per search call |
| 1.10.22 | Perf: filesystem overhead reduction — `_VERIFIED_PATHS` cache skips redundant mkdir syscalls on every tool call; `list_repos` fast-exits before legacy JSON glob passes when no `.json` files exist (~5-20ms saved); `_hash_file` caches read content for reuse in parse step (eliminates double-read of changed files during incremental index); `_safe_content_path` caches `content_dir.resolve()` result (eliminates repeated resolve() syscalls in search_text/search_symbols) |
| 1.10.23 | Perf: INDEX_VERSION 6 — add `size_bytes` column to `files` table; eliminates `os.path.getsize()` stat calls in search_symbols, search_text, and get_file_content (replaced with `index.file_sizes.get()`); auto-migration from v5; `file_sizes` dict propagated through `_patch_index_from_delta` for O(delta) incremental updates |
| 1.10.24 | Perf: pipeline optimization — cache `get_language_for_path()` result in parse loop and reuse for import extraction (eliminates 2× call per file across all 3 pipeline functions); add `source_bytes` param to `parse_file` and pass pre-encoded bytes from full-index loop (eliminates redundant `content.encode('utf-8')` on every indexed file); add `_file_hash_bytes` helper for bytes-based hashing |

## Maintenance Practices

1. **Document every tool before shipping.** Any PR adding a new tool to `server.py`
   must simultaneously update: README.md (tool reference), CLAUDE.md (Key Files),
   version history, and at least one test.
2. **Log every silent exception.** Every `except Exception:` block must emit at
   minimum `logger.debug("...", exc_info=True)`. For user-facing fallbacks (AI
   summarizer, index load), use `logger.warning(...)`.
3. **Version history goes at the bottom** in ascending order (oldest first, newest last).
