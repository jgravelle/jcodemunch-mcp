# jcodemunch-mcp — Project Brief

## Current State
- **Version:** 1.77.0 (published to PyPI)
- **INDEX_VERSION:** 9
- **Tests:** 3645 passed, 7 skipped
- **Python:** >=3.10

## Key Files
```
src/jcodemunch_mcp/
  server.py            # MCP dispatcher (async); CLI subcommand dispatch, auth/rate-limit middleware
  watcher.py           # WatcherManager class (dynamic folder watching); watch_folders() wrapper
  progress.py          # MCP progress notifications; ProgressReporter (thread-safe, monotonic), make_progress_notify() bridge
  security.py          # Path validation, skip patterns, file caps
  redact.py            # Response-level secret redaction; regex patterns for AWS/GCP/Azure/JWT/GitHub/Slack/PEM/API keys/private IPs; redact_dict() post-processor
  config.py            # JSONC config: global + per-project layering, env var fallback, language/tool gating
  agent_selector.py    # Complexity scoring + model routing (off/manual/auto); default provider batting orders
  cli/
    init.py            # `jcodemunch-mcp init` — one-command onboarding (client detection, config patching, CLAUDE.md, Cursor rules, Windsurf rules, hooks); --demo flag
    hooks.py           # PreToolUse (Read interceptor) + PostToolUse (auto-reindex) + PreCompact (session snapshot) + TaskCompleted (post-task diagnostics) + SubagentStart (repo briefing) hook handlers for Claude Code
  groq/
    cli.py             # `gcm` CLI entrypoint — codebase Q&A (single question + --chat mode)
    config.py          # GcmConfig dataclass: GROQ_API_KEY, model, token_budget, system prompt
    retriever.py       # Bridge to jCodeMunch: ensure_indexed(), retrieve_context()
    inference.py       # Groq API streaming + batch via OpenAI-compatible client
  parser/
    languages.py       # LANGUAGE_REGISTRY, extension → language map, LanguageSpec
    extractor.py       # parse_file() dispatch; custom parsers for Erlang, Fortran, SQL, Razor
    imports.py         # Regex import extraction (19 languages); extract_imports(), resolve_specifier(), build_psr4_map()
    fqn.py             # PHP FQN ↔ symbol_id translation (PSR-4); symbol_to_fqn(), fqn_to_symbol()
  encoding/
    __init__.py          # Dispatcher: encode_response(tool, response, format) — auto/compact/json
    format.py            # MUNCH on-wire primitives: header, legends (@N), scalars, CSV tables
    gate.py              # 15% savings threshold (JCODEMUNCH_ENCODING_THRESHOLD override)
    generic.py           # Shape-sniffer fallback encoder (covers all tools w/o custom encoder)
    decoder.py           # Public decode() — rehydrates MUNCH payloads back to dicts
    schemas/             # Per-tool custom encoders (tier-1, phase 2+); auto-discovered registry
  storage/
    sqlite_store.py    # CodeIndex, save/load/incremental_save, WAL-aware LRU cache (_db_mtime_ns); get_source_root()
  embeddings/
    local_encoder.py   # Bundled ONNX local encoder (all-MiniLM-L6-v2, 384-dim); WordPiece tokenizer, encode_batch(), download_model()
  enrichment/
    lsp_bridge.py      # LSP bridge — opt-in compiler-grade call graph resolution via pyright/gopls/ts-language-server/rust-analyzer; LSPServer lifecycle, LSPBridge multi-server manager, enrich_call_graph_with_lsp() + enrich_dispatch_edges() (interface/trait dispatch resolution)
  retrieval/
    signal_fusion.py   # Weighted Reciprocal Rank (WRR) fusion: lexical + structural + similarity + identity channels
  summarizer/
    batch_summarize.py # 3-tier: Anthropic > Gemini > OpenAI-compat > signature fallback
  tools/
    index_folder.py    # Local indexer (sync → asyncio.to_thread in server.py)
    index_repo.py      # GitHub indexer (async, httpx)
    get_symbol.py      # get_symbol_source: shape-follows-input (id→flat, ids[]→{symbols,errors})
    search_columns.py  # Column search across dbt/SQLMesh models
    get_context_bundle.py   # Symbol + imports bundle; token_budget/budget_strategy
    get_ranked_context.py   # Query-driven budgeted context (BM25 + PageRank)
    resolve_repo.py    # O(1) path→repo-ID lookup
    find_importers.py  # Files that import a given file (import graph); cross_repo param
    find_references.py # Files that reference a given identifier
    test_summarizer.py # Diagnostic tool: probe AI summarizer, report status (disabled by default)
    package_registry.py # Cross-repo package registry: manifest parsing, registry building, specifier resolution
    get_cross_repo_map.py # Cross-repo dependency map at the package level
    _call_graph.py       # Shared AST-derived call-graph helpers (callers/callees, BFS)
    get_call_hierarchy.py # get_call_hierarchy: callers+callees for a symbol, N levels deep
    get_impact_preview.py # get_impact_preview: transitive "what breaks?" analysis
    plan_refactoring.py   # plan_refactoring: edit-ready plans for rename/move/extract/signature refactorings
    get_symbol_complexity.py  # get_symbol_complexity: cyclomatic/nesting/param_count for a symbol
    get_churn_rate.py         # get_churn_rate: git commit count for file or symbol over N days
    get_symbol_provenance.py  # get_symbol_provenance: full git archaeology per symbol — authorship lineage, semantic commit classification, evolution narrative
    get_pr_risk_profile.py    # get_pr_risk_profile: unified PR/branch risk assessment — fuses blast radius + complexity + churn + test gaps + volume into composite score
    get_hotspots.py           # get_hotspots: top-N high-risk symbols by complexity x churn
    get_tectonic_map.py       # get_tectonic_map: logical module topology via 3-signal fusion (structural+behavioral+temporal) + label propagation
    get_signal_chains.py      # get_signal_chains: entry-point-to-leaf pathway discovery; traces how HTTP/CLI/task/event signals propagate through the call graph; discovery + lookup modes
    render_diagram.py         # render_diagram: universal Mermaid renderer; auto-detects source tool, picks optimal diagram type (flowchart/sequence), encodes metadata as visual signals; 3 themes, smart pruning; optional `open_in_viewer` (config-gated, spawns mmd-viewer)
    mermaid_viewer.py         # mmd-viewer spawn helper for render_diagram; resolve_viewer_path/open_diagram/cleanup_temp_dir; jcm- prefix for safe cleanup; config-gated via render_diagram_viewer_enabled + mermaid_viewer_path
    get_project_intel.py      # get_project_intel: auto-discover+parse non-code knowledge (Dockerfiles, CI configs, compose, K8s, .env templates, Makefiles, scripts); cross-references to code symbols; 6 categories
    get_repo_health.py        # get_repo_health: one-call triage snapshot (delegate aggregator)
    get_untested_symbols.py   # get_untested_symbols: find functions with no test-file reachability (import graph + name matching)
    search_ast.py             # search_ast: cross-language AST pattern matching; 10 preset anti-patterns + custom mini-DSL (call:, string:, comment:, nesting:, loops:, lines:); enriched with symbol context
    winnow_symbols.py         # winnow_symbols: multi-axis constraint-chain query; AND-intersects kind/language/name/file/complexity/decorator/calls/summary/churn in one round trip; ranks by importance/complexity/churn/name
    audit_agent_config.py    # audit_agent_config: token waste audit for CLAUDE.md, .cursorrules, etc.; cross-refs against index
    analyze_perf.py          # analyze_perf: per-tool latency telemetry (p50/p95/max/error_rate) + cache hit-rate; reads in-memory session ring or persistent telemetry.db (opt-in via perf_telemetry_enabled); compare_release="X" loads benchmarks/token_baselines/vX.json and adds baseline_diff
  retrieval/
    confidence.py        # compute_confidence/attach_confidence: 0-1 retrieval confidence score (geometric mean of gap, strength, identity, freshness sub-signals); attached to _meta.confidence on search_symbols / plan_turn / get_ranked_context
    freshness.py         # FreshnessProbe: per-result _freshness classification (fresh / edited_uncommitted / stale_index); compares index SHA vs git HEAD + per-file mtime vs CodeIndex.file_mtimes; wired into search_symbols / get_symbol_source / get_context_bundle / get_ranked_context
```

## CLI Subcommands
| Subcommand | Purpose |
|------------|---------|
| `serve` (default) | Run the MCP server (`stdio`, `sse`, or `streamable-http`) |
| `init` | One-command onboarding: detect MCP clients, write config, install CLAUDE.md policy, hooks, index |
| `watch <paths>` | File watcher — auto-reindex on change |
| `watch-claude` | Auto-discover and watch Claude Code worktrees |
| `watch-all` | Auto-discover **every** locally-indexed repo and keep it fresh; rediscovers on interval |
| `watch-install` | Install `watch-all` as a login service (systemd / launchd / Task Scheduler) |
| `watch-uninstall` | Remove the installed `watch-all` login service |
| `watch-status` | Print service state + per-repo reindex status (also exposed as MCP tool `get_watch_status`) |
| `hook-event create\|remove` | Record a worktree lifecycle event (called by Claude Code hooks) |
| `index [target]` | Index a local folder (default: `.`) or GitHub repo (`owner/repo`). One command, no init required |
| `index-file <path>` | Re-index a single file within an existing indexed folder (used by PostToolUse hooks) |
| `config` | Print effective configuration grouped by concern |
| `config --check` | Also validate prerequisites (storage writable, AI pkg installed, HTTP pkgs present) |
| `config --upgrade` | Add missing keys from current template to existing config.jsonc, preserving user values |
| `download-model` | Download bundled ONNX embedding model (all-MiniLM-L6-v2) for zero-config semantic search; `--target-dir` override |
| `install-pack [id]` | Download and install a Starter Pack pre-built index; `--list` for catalog, `--license KEY` for premium |
| `hook-pretooluse` | PreToolUse hook: intercept Read on large code files, suggest jCodemunch (reads JSON stdin) |
| `hook-posttooluse` | PostToolUse hook: auto-reindex files after Edit/Write (reads JSON stdin) |
| `hook-precompact` | PreCompact hook: generate session snapshot before context compaction (reads JSON stdin) |
| `hook-taskcomplete` | TaskCompleted hook: post-task diagnostics — dead code, untested symbols, dangling refs (reads JSON stdin) |
| `hook-subagent-start` | SubagentStart hook: inject condensed repo orientation for spawned agents (reads JSON stdin) |

## Architecture Notes
- `index_folder` is **synchronous** — dispatched via `asyncio.to_thread()` in server.py to avoid blocking the event loop
- `index_repo` is **async** (uses httpx for GitHub API)
- `has_index()` distinguishes "no file on disk" from "file exists but version rejected"
- Symbol lookup is O(1) via `__post_init__` id dict in `CodeIndex`

## Custom Parsers
Tree-sitter grammar lacks clean named fields for these — custom regex extractors:
- **Erlang**: multi-clause function merging by (name, arity); arity-qualified names (e.g. `add/2`)
- **Fortran**: module-as-container, qualified names (`math_utils::multiply`), parameter constants
- **SQL**: `_parse_sql_symbols` + `sql_preprocessor.py` strips Jinja (dbt); macro/test/snapshot/materialization as symbols
- **Razor/Blazor** (.cshtml/.razor): `@functions/@code` → C#, `@page`/`@inject` → constants, HTML ids

## Env Vars
| Var | Default | Purpose |
|-----|---------|---------|
| `CODE_INDEX_PATH` | `~/.code-index/` | Index storage location |
| `JCODEMUNCH_MAX_INDEX_FILES` | 10,000 | File cap for repo indexing |
| `JCODEMUNCH_MAX_FOLDER_FILES` | 2,000 | File cap for folder indexing |
| `JCODEMUNCH_FILE_TREE_MAX_FILES` | 500 | Cap for get_file_tree results |
| `JCODEMUNCH_GITIGNORE_WARN_THRESHOLD` | 500 | Missing-.gitignore warning threshold (0 = disable) |
| `JCODEMUNCH_USE_AI_SUMMARIES` | auto | AI summarization mode: `auto` (detect provider), `true` (use explicit config), `false`/`0`/`no`/`off` (disable) |
| `JCODEMUNCH_SUMMARIZER_PROVIDER` | — | Explicit summarizer provider: `anthropic`, `gemini`, `openai`, `minimax`, `glm`, `openrouter`, `none` |
| `JCODEMUNCH_SUMMARIZER_MODEL` | — | Model name override for the selected summarizer provider |
| `JCODEMUNCH_TRUSTED_FOLDERS` | — | Roots trusted for index_folder; whitelist mode by default |
| `JCODEMUNCH_EXTRA_IGNORE_PATTERNS` | — | Always-on gitignore patterns (comma-sep or JSON array) |
| `JCODEMUNCH_PATH_MAP` | — | Cross-platform path remapping; format: `orig1=new1,orig2=new2` |
| `JCODEMUNCH_STALENESS_DAYS` | 7 | Days before get_repo_outline emits a staleness_warning |
| `JCODEMUNCH_MAX_RESULTS` | 500 | Hard cap on search_columns result count |
| `JCODEMUNCH_HTTP_TOKEN` | — | Bearer token for HTTP transport auth (opt-in) |
| `JCODEMUNCH_RATE_LIMIT` | 0 | Max requests/minute per client IP in HTTP transport (0 = disabled) |
| `JCODEMUNCH_REDACT_SOURCE_ROOT` | 0 | Set 1 to replace source_root with display_name in responses |
| `JCODEMUNCH_SHARE_SAVINGS` | 1 | Set 0 to disable anonymous token savings telemetry |
| `JCODEMUNCH_REDACT_RESPONSE_SECRETS` | 1 | Set 0 to disable response-level secret redaction (AWS/GCP/Azure/JWT/etc.) |
| `JCODEMUNCH_STATS_FILE_INTERVAL` | 3 | Calls between session_stats.json writes; 0 = disable |
| `JCODEMUNCH_PERF_TELEMETRY` | 0 | Set 1 to enable persistent perf SQLite sink at ~/.code-index/telemetry.db (per-tool latency + ok flag + repo). In-memory ring is always tracked; the env var only controls durable persistence. |
| `JCODEMUNCH_PERF_TELEMETRY_MAX_ROWS` | 100000 | Rolling cap on persisted perf rows; oldest rows trimmed in 1k-row batches once exceeded. |
| `ANTHROPIC_API_KEY` | — | Enables Claude Haiku summaries (`pip install jcodemunch-mcp[anthropic]`) |
| `GOOGLE_API_KEY` | — | Enables Gemini Flash summaries (`pip install jcodemunch-mcp[gemini]`) |
| `OPENAI_API_BASE` | — | Local LLM endpoint (Ollama, LM Studio) |
| `OPENAI_WIRE_API` | — | Set `responses` to use OpenAI Responses API instead of chat/completions |
| `OPENROUTER_API_KEY` | — | Enables OpenRouter summaries (default model: `meta-llama/llama-3.3-70b-instruct:free`) |
| `JCODEMUNCH_LOCAL_EMBED_MODEL` | — | Override path to bundled ONNX model directory (default: `~/.code-index/models/all-MiniLM-L6-v2/`) |
| `GEMINI_EMBED_TASK_AWARE` | 1 | Set `0`/`false`/`no`/`off` to disable task-type hints (`RETRIEVAL_DOCUMENT` / `CODE_RETRIEVAL_QUERY`) when using Gemini embeddings |
| `JCODEMUNCH_CROSS_REPO_DEFAULT` | 0 | Set 1 to enable cross-repo traversal by default in find_importers, get_blast_radius, get_dependency_graph |
| `JCODEMUNCH_EVENT_LOG` | — | Set `1` to write `_pulse.json` on every tool call (per-call activity signal for dashboards) |

## PR / Issue History
See `git log` and CHANGELOG.md. Active contributors: MariusAdrian88, DrHayt, tmeckel, drax1222.

## Maintenance Practices

1. **Document every tool before shipping.** Any PR adding a new tool to `server.py`
   must simultaneously update: README.md (tool reference), CLAUDE.md (Key Files),
   CHANGELOG.md, and at least one test.
2. **Log every silent exception.** Every `except Exception:` block must emit at
   minimum `logger.debug("...", exc_info=True)`. For user-facing fallbacks (AI
   summarizer, index load), use `logger.warning(...)`.
3. **CHANGELOG.md** is the authoritative version history — update it with every release.
