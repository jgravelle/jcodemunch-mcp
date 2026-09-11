# Key Files — module map

The descriptive half of `CLAUDE.md`'s **Key Files**: what each module *is*.
Rotated out on 2026-08-29 under Maintenance Practice 5, verbatim.

⚠⚠ **This file is NOT loaded into a session, and that is the point.** It holds
what is DERIVABLE — jcodemunch answers most of it live (`get_file_outline`,
`get_symbol_source`, `get_repo_outline`), which is why it can leave the
always-loaded budget without losing anything.

⚠⚠ **The invariants did NOT move.** Every module whose entry states a
prohibition, a constraint whose violation causes a defect, or a rationale keeps
its full entry in `CLAUDE.md` and is DELIBERATELY ABSENT here. Nothing is
duplicated between the two files: `tests/test_key_files_split.py` fails if an
entry appears in both, or in neither.

⚠ So a module missing from this file is not undocumented — it is documented in
`CLAUDE.md`, because what it needed said was worth a session's context.

```
src/jcodemunch_mcp/
  watcher.py           # WatcherManager class (dynamic folder watching); watch_folders() wrapper
  redact.py            # Response-level secret redaction; regex patterns for AWS/GCP/Azure/JWT/GitHub/Slack/PEM/API keys/private IPs; redact_dict() post-processor
  config.py            # JSONC config: global + per-project layering, env var fallback, language/tool gating
  agent_selector.py    # Complexity scoring + model routing (off/manual/auto); default provider batting orders
  cli/
    init.py            # `jcodemunch-mcp init` — one-command onboarding (client detection, config patching, CLAUDE.md, Cursor rules, Windsurf rules, hooks); --demo flag. v1.105.1: `install <agent>` / `uninstall` / `install-status` verbs. v1.107.0: `--skills` flag on install, skills block in install_status report
    skills.py          # v1.107.0: Claude Agent Skill bundle writer. _build_skill_content() composes YAML frontmatter + tier-filtered tool-usage decision tree. install_claude_skill / uninstall_claude_skill / skill_status. Lives at ~/.claude/skills/jcodemunch/SKILL.md (global) or ./.claude/skills/jcodemunch/SKILL.md (project). Reuses _filter_policy_for_tools from init.py for tier awareness
    hooks/             # Claude Code hook handlers, one module per family: steering.py (PreToolUse Read/Grep/Glob/Bash-search), reindex.py (PostToolUse auto-reindex + Copilot variant), snapshot.py (PreCompact transcript-root registration only — no exit-0 output channel — + SessionStart snapshot restore), landmarks.py, taskcomplete.py (post-task diagnostics, live-journal fed), briefing.py (surface-aware SubagentStart), _common.py; __init__.py re-exports run_* for server.py dispatch
  groq/
    cli.py             # `gcm` CLI entrypoint — codebase Q&A (single question + --chat mode)
    config.py          # GcmConfig dataclass: GROQ_API_KEY, model, token_budget, system prompt
    retriever.py       # Bridge to jCodeMunch: ensure_indexed(), retrieve_context()
    inference.py       # Groq API streaming + batch via OpenAI-compatible client
  parser/
    languages.py       # LANGUAGE_REGISTRY, extension → language map, LanguageSpec
    extractor.py       # parse_file() dispatch; custom parsers for Erlang, Fortran, SQL, Razor
    grammar_pack.py    # (#608) Which GENERATION of tree-sitter-language-pack is installed (bundled 0.x / download 1.x / absent) and what that costs; records a grammar-load failure per language, once, where the extractor used to swallow it as `[]`. A leaf that never calls the pack's network API: the unavailable-language list is what the extractor SAW fail, never a manifest lookup. Read by index_folder (warnings + `grammar_pack` block), evidence/capability.py (`grammar_source`) and install-status; on a bundled pack with no failures `notice()` is None so a 0.x result is byte-identical
    fqn.py             # PHP FQN ↔ symbol_id translation (PSR-4); symbol_to_fqn(), fqn_to_symbol()
  encoding/
    __init__.py          # Dispatcher: encode_response(tool, response, format) — auto/compact/json
    format.py            # MUNCH on-wire primitives: header, legends (@N), scalars, CSV tables
    gate.py              # 15% savings threshold (JCODEMUNCH_ENCODING_THRESHOLD override)
    generic.py           # Shape-sniffer fallback encoder (covers all tools w/o custom encoder)
    decoder.py           # Public decode() — rehydrates MUNCH payloads back to dicts
  investigator/
  storage/
  embeddings/
    local_encoder.py   # Bundled ONNX local encoder (all-MiniLM-L6-v2, 384-dim); WordPiece tokenizer, encode_batch(), download_model()
  enrichment/
    lsp_bridge.py      # LSP bridge — opt-in compiler-grade call graph resolution via pyright/gopls/ts-language-server/rust-analyzer; LSPServer lifecycle, LSPBridge multi-server manager, enrich_call_graph_with_lsp() + enrich_dispatch_edges() (interface/trait dispatch resolution)
  retrieval/
    signal_fusion.py   # Weighted Reciprocal Rank (WRR) fusion: lexical + structural + similarity + identity channels
  summarizer/
    batch_summarize.py # 3-tier: Anthropic > Gemini > OpenAI-compat > signature fallback
  tools/
    index_repo.py      # GitHub indexer (async, httpx)
    get_symbol.py      # get_symbol_source: shape-follows-input (id→flat, ids[]→{symbols,errors}). v1.108.70 bounded-source mode: optional source_start_line/source_end_line/max_source_lines/max_source_bytes/max_total_source_bytes return an explicitly-labeled slice (source_truncated + range/total metadata, source_is_bounded_view); verify stays full-body; context_lines+bound rejected. Pure helpers _utf8_safe_truncate + _bound_source
    search_columns.py  # Column search across dbt/SQLMesh models
    get_context_bundle.py   # Symbol + imports bundle; token_budget/budget_strategy
    get_ranked_context.py   # Query-driven budgeted context (BM25 + PageRank)
    resolve_repo.py    # O(1) path→repo-ID lookup
    find_importers.py  # Files that import a given file (import graph); cross_repo param
    find_references.py # Files that IMPORT or re-export a given identifier (import graph; who imports this). Not the usage-site tool: a call site is invisible to it, so `check_references.py` (imports + every content match) answers "where is this name used" (CF-51, CF-63, 2026-09-10). v1.108.96: _attach_scip_to_response unions SCIP compiler-verified reference edges (compile-time evidence P1)
    check_references.py # Where an identifier is USED: import sites plus every file whose content mentions it, in one call; is_referenced (bool) for a quick dead-code check. The tool the usage question routes to on every steering surface since 2026-09-10
    test_summarizer.py # Diagnostic tool: probe AI summarizer, report status (disabled by default)
    package_registry.py # Cross-repo package registry: manifest parsing, registry building, specifier resolution
    get_cross_repo_map.py # Cross-repo dependency map at the package level
    _call_graph.py       # Shared AST-derived call-graph helpers (callers/callees, BFS)
    get_call_hierarchy.py # get_call_hierarchy: callers+callees for a symbol, N levels deep
    decision_context.py   # (v1.108.59) resolve_decision_context: read-only git-archaeology surfacer. Mines decision-bearing commits (revert/perf/refactor/rename/bugfix) for a set of files, reusing get_symbol_provenance's _run_git/_classify_commit/_extract_intent; dedupes by SHA, ranks by category weight × recency, emits digest + by_category + volatility + summary. Surface-only, nothing persisted. Consumed by get_blast_radius / get_impact_preview via include_decisions
    get_impact_preview.py # get_impact_preview: transitive "what breaks?" analysis. v1.108.59: include_decisions attaches a read-only `decisions` block (decision_context)
    plan_refactoring.py   # plan_refactoring: edit-ready plans for rename/move/extract/signature refactorings
    get_symbol_complexity.py  # get_symbol_complexity: cyclomatic/nesting/param_count for a symbol
    get_churn_rate.py         # get_churn_rate: git commit count for file or symbol over N days
    get_delivery_metrics.py   # (v1.108.69) get_delivery_metrics: durable-change delivery over a window. Classifies each non-merge commit into one bucket (revert_authored/reverted/reworked/durable) via _run_git; commits_durable is the numerator for cost-per-outcome (the `delivery` CLI's --cost divides AI spend by it). Hub files (CHANGELOG/version/monolithic dispatch, co-touched by >=max(4,20%) of commits) excluded from the rework signal (auditable via _meta.hub_files_excluded); commits_provisional flags the trailing tail. Reuses get_symbol_provenance._classify_commit for by_category. Read-only, no new tables
    get_symbol_provenance.py  # get_symbol_provenance: full git archaeology per symbol — authorship lineage, semantic commit classification, evolution narrative. Phase 5: optional stack_frequency block reading runtime_stack_events over a 30-day window — per-severity counts + first/last seen; narrative gains an appended sentence when error count >= 3
    get_architecture_metrics.py # (v1.108.113) get_architecture_metrics: concentration (Gini over per-file symbols/bytes/fan_in/fan_out + top concentrators) + depth (Lakos levelization, longest chain over SCC-condensed DAG) + modularity (WCC clusters + back_edges = DSM hidden coupling). Reuses _build_adjacency (get_dependency_graph) + _find_cycles. One tool vs their 3; NO N×N matrix; does NOT touch radar composite. Read-only analytics. Standard tier
    get_decorator_census.py   # (v1.108.112) get_decorator_census: repo-wide census of decorators/annotations/attributes. Aggregates the index's stored per-symbol `decorators` (cross-language, no parser work); normalized histogram (_normalize_decorator strips @/args/[]; _short_raw flattens+caps raw_forms), per-bucket symbol_kinds + file count; name_filter/scope_path/kind filters, include_sites. Read-only ANALYTICS (no tokens-saved _meta). Standard tier
    get_parity_map.py         # (v1.108.111) get_parity_map: correspondence-aware migration parity between a SOURCE and TARGET symbol tree (two subpaths of one repo, or two repos). Exact + rename matching (reuses find_similar_symbols _signature_tokens/_callee_set/_jaccard/_byte_ratio), status per source symbol (ported/ported_diverged/unported/orphaned/added), dependency-ordered port_plan (adjacency from _callee_set, SCC grouping via get_dependency_cycles._find_cycles, Kahn topo, unblocked/blocking_deps). Read-only/plan-only; parity_axes reserved for P3 suite axes. Standard tier
    get_hotspots.py           # get_hotspots: top-N high-risk symbols by complexity x churn
    get_repo_map.py           # get_repo_map: query-less, token-budgeted, signature-level repo overview ranked by PageRank — cold-start orientation. Reuses cached PageRank, emits signatures only (no bodies), greedy-packs per-file under token_budget
    find_similar_symbols.py   # find_similar_symbols: multi-signal consolidation detection — semantic (embeddings) + structural (signature/size) + behavioral (callee Jaccard); union-find clustering, verdict tier (near_duplicate / similar_logic / parallel_implementation), canonical pick by PageRank, differs_by breakdown. BM25 inverted-index pre-filter for sub-N^2 cost. Skips tests/dunders/generated by default.
    get_group_contracts.py    # get_group_contracts: cross-repo shared-symbol API surface for a group of indexed repos. Resolves named imports through the package registry, classifies each shared symbol into 4 verdict tiers (de_facto_api / leaky_internal / dead_contract / version_skew), attaches stability score (churn-weighted), last_breaking_change (from provenance), and runtime_hits (when traces exist). Pairs with get_cross_repo_map: that gives repo-level edges; this zooms in to the symbol-level surface.
    find_implementations.py   # find_implementations: multi-source concrete-impl discovery for interfaces/abstracts/methods. Four resolution channels with confidence scoring — LSP dispatch (1.0), AST class hierarchy (0.85), duck-typed name match (0.65), decorator handler (0.45). Classifies each impl (subclass_override / interface_impl / duck_typed / decorator_handler / subclass), ranks by PageRank × byte_length, attaches differs_by breakdown, optional cross_repo discovery.
    assemble_task_context.py  # assemble_task_context: task-aware single-call context orchestrator. Auto-classifies the task into one of six intents (explore/debug/refactor/extend/audit/review) via keyword scoring, auto-extracts anchor symbol names from the task, runs the intent-appropriate sub-tool sequence (digest + hotspots + tectonic for explore; anchor + callers + callees + blast + runtime for debug; anchor + rename_safe + delete_safe + implementations + similar for refactor; anchor + implementations + similar + decorators for extend; anchor + risk + blast + dead_code + untested for audit; changed + blast + risk + similar_changed for review), packs results into a single source-attributed capsule under token_budget. Each entry tagged with stage + source_tool. Intent classification is explainable (returns intent_keywords_matched + intent_confidence). Caller can override intent and include to force specific stages.
    get_tectonic_map.py       # get_tectonic_map: logical module topology via 3-signal fusion (structural+behavioral+temporal) + label propagation
    get_signal_chains.py      # get_signal_chains: entry-point-to-leaf pathway discovery; traces how HTTP/CLI/task/event signals propagate through the call graph; discovery + lookup modes. v1.108.58: include_flow_edges param consumes flow_edges.py — string-dispatched handlers become http gateways, rendered templates attach as a per-chain `views` list
    flow_edges.py             # (v1.108.58) Language-agnostic framework flow-edge resolver. resolve_flow_edges(index, store, owner, name, kinds=("route","render")) emits typed edges the AST call graph misses: route→handler (Django path/re_path/url, Express/Fastify/Koa .get(p,h), Flask add_url_rule view_func=, Rails to:"ctrl#action") resolved to symbols via the import graph; render→view (render/render_template/res.render/view string templates) resolved to the template file when indexed. Shape-keyed (one resolver, not per-framework plugins); reuses _ContentCache/_symbol_body/build_symbols_by_file/resolve_specifier. Pure read path, no reindex. Decorator-bound handlers NOT re-emitted (they already surface as gateways)
    render_diagram.py         # render_diagram: universal Mermaid renderer; auto-detects source tool, picks optimal diagram type (flowchart/sequence), encodes metadata as visual signals; 3 themes, smart pruning; optional `open_in_viewer` (config-gated, spawns mmd-viewer)
    mermaid_viewer.py         # mmd-viewer spawn helper for render_diagram; resolve_viewer_path/open_diagram/cleanup_temp_dir; jcm- prefix for safe cleanup; config-gated via render_diagram_viewer_enabled + mermaid_viewer_path
    get_project_intel.py      # get_project_intel: auto-discover+parse non-code knowledge (Dockerfiles, CI configs, compose, K8s, .env templates, Makefiles, scripts); cross-references to code symbols; 6 categories. v1.108.0 adds `scope_path` arg to restrict discovery to a monorepo subpath (use list_workspaces.path values); validates against source_root (traversal/absolute/non-existent all error).
    list_workspaces.py        # (v1.108.0) Enumerate monorepo workspace members. Detects pnpm (pnpm-workspace.yaml), yarn/npm (package.json `workspaces:`), turborepo (turbo.json), lerna (lerna.json), rush (rush.json), Go (go.work `use (...)`, module name from go.mod), Cargo (Cargo.toml `[workspace] members`). Returns `[{path, package_name, manager}, ...]` plus `is_monorepo` + `managers`. Read-only, dependency-free (hand-rolled minimal TOML/YAML readers).
    search_ast.py             # search_ast: cross-language AST pattern matching; 10 preset anti-patterns + custom mini-DSL (call:, string:, comment:, nesting:, loops:, lines:); enriched with symbol context
    winnow_symbols.py         # winnow_symbols: multi-axis constraint-chain query; AND-intersects kind/language/name/file/complexity/decorator/calls/summary/churn in one round trip; ranks by importance/complexity/churn/name
  runtime/
    __init__.py          # Trace ingestion package (Phases 0-5): re-exports redact_trace_record, resolve_to_symbol_id, parse_otel_file, ingest_otel_file, OtelSpan, parse_sql_log_file, ingest_sql_log_file, SqlQueryRecord, parse_stack_log_file, ingest_stack_log_file, StackEvent, StackFrame, VALID_SOURCES = {'otel','sql_log','stack_log','apm'}
    resolve.py           # resolve_to_symbol_id(conn, file, line, name) — best-effort (file, line, function) → symbol_id with suffix-match fallback for absolute trace paths against repo-relative index paths
    otel.py              # Phase 1 OTel JSON parser — handles JSON-Lines, single-document JSON, top-level array, and .gz transparently; extracts code.filepath / code.lineno / code.function / duration into OtelSpan
    ingest.py            # Phase 1 orchestrator ingest_otel_file(db_path, file_path, redact_enabled, max_rows) — parse → redact → resolve → upsert; computes per-batch p50/p95 from span durations; FIFO-evicts runtime_calls + runtime_unmapped down to max_rows when exceeded; persists per-pattern redaction counts to runtime_redaction_log
    sql_log.py           # Phase 4 SQL log parser — pg_stat_statements CSV (header autodetect; total_time/total_exec_time + mean_time/mean_exec_time aliases) + generic JSON-Lines (.jsonl/.json/.log) + top-level array fallback + .gz transparent; extracts table refs (FROM/JOIN/UPDATE/INSERT INTO/DELETE FROM/MERGE INTO; schema-qualified names → trailing ident) and column refs (qualified alias.col + bare idents in SELECT/WHERE/ON/HAVING/GROUP BY/ORDER BY)
    sql_ingest.py        # Phase 4 orchestrator ingest_sql_log_file(db_path, file_path, redact_enabled, max_rows) — parse → redact → resolve → upsert; resolver builds a one-shot read-only metadata snapshot (file-stem map, exact-name map, dbt_columns/sqlmesh_columns set); upserts runtime_calls + runtime_columns + runtime_unmapped + runtime_redaction_log under source='sql_log'; FIFO-evicts all three runtime tables
    stack_log.py         # Phase 5 stack-frame parser — Python tracebacks (`File "...", line N, in <name>` pairs), JVM tracebacks (`at pkg.Class.method(File.java:N)` + flattened `Caused by:` chains), Node.js stacks (named `at funcName (file.js:N:N)` + anonymous `at file.js:N:N` + node:events-style module paths). Plain-text + JSON-Lines structured-log + top-level array + .gz. Severity heuristic: looks 3 lines back for FATAL/CRITICAL/ERROR/WARN[ING]/INFO; default 'info'.
    stack_ingest.py      # Phase 5 orchestrator ingest_stack_log_file(db_path, file_path, redact_enabled, max_rows) — parse → redact (event.message) → resolve each frame → upsert; populates BOTH runtime_calls (severity-agnostic rollup so confidence-stamping fires) AND runtime_stack_events (per-severity counts). FIFO-evicts runtime_calls + runtime_unmapped + runtime_stack_events. Phase 6 adds ingest_stack_log_stream() that takes an in-memory text payload via the shared _ingest_stack_iter() pipeline.
  evidence/
    scip_ingest.py       # (v1.108.96) ingest_scip_file: parse → resolve (definition map scip-symbol→(file,line) from Definition-role occurrences; enclosing symbol via runtime/resolve.resolve_to_symbol_id) → persist scip_edges (kinds: reference, implementation) / scip_unmapped (reasoned) / scip_meta (tool, ingested_at, git_head staleness anchor). Skips counted: Import-role occurrences (import graph covers) + `local N` symbols. _ensure_scip_tables covers pre-v17 DBs; FIFO eviction per JCODEMUNCH_SCIP_MAX_ROWS
  tools/
    get_runtime_coverage.py  # Phase 3: coverage histogram for repo or single file. {total_symbols, confirmed, declared_only, coverage_pct, sources, last_seen, unmapped_runtime[]}.
    find_hot_paths.py        # Phase 3: top-N symbols by runtime hit count, with p50/p95, sources, last_seen. Optional name substring filter. Pairs with get_blast_radius.
    get_redaction_log.py     # Phase 6: forensic accounting of PII redactions — surfaces per-pattern counts from runtime_redaction_log so operators can verify the redaction chokepoint is firing on production traffic. Filters by source + since_days. Read-only / immutable connection.
  retrieval/
    confidence.py        # compute_confidence/attach_confidence: 0-1 retrieval confidence score (geometric mean of gap, strength, identity, freshness sub-signals); attached to _meta.confidence on search_symbols / plan_turn / get_ranked_context
    tuning.py            # WeightTuner + get_semantic_weight: learns per-repo semantic_weight from v1.78.0 ranking_events ledger; ±0.05 step (clamp 0.1-0.8) when mean confidence between semantic_used groups differs by ≥0.05; persists to ~/.code-index/tuning.jsonc; applied at query time when caller leaves semantic_weight at the default (identity_boost learning removed v1.108.102 — audit W6, was never consumed at query time)
    embed_drift.py       # CANARY_STRINGS (16) + capture_canary/check_drift: pins canary embeddings to ~/.code-index/embed_canary.json, re-checks cosine drift via check_embedding_drift MCP tool; catches silent provider model changes (Gemini/OpenAI/bundled-ONNX); default threshold 0.05 cosine distance
```

