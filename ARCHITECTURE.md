# Architecture

> **Version note:** This document describes the current high-level architecture of jCodeMunch-MCP. Exact tool names, optional integrations, and ranking details may evolve over time. For user-facing setup and workflows, see `USER_GUIDE.md`. For protocol semantics, see `SPEC.md`.

---

## Table of Contents

* [System Overview](#system-overview)
* [Design Goals](#design-goals)
* [Directory Structure](#directory-structure)
* [High-Level Data Flow](#high-level-data-flow)
* [Core Architectural Concepts](#core-architectural-concepts)
* [Repository Identity Model](#repository-identity-model)
* [Parsing and Symbol Extraction](#parsing-and-symbol-extraction)
* [Context Provider Framework](#context-provider-framework)
* [Summarization Pipeline](#summarization-pipeline)
* [Storage Model](#storage-model)
* [Indexing Strategies](#indexing-strategies)
* [Retrieval Model](#retrieval-model)
* [Search and Ranking](#search-and-ranking)
* [Import Graph and Relationship Features](#import-graph-and-relationship-features)
* [Tool Surface](#tool-surface)
* [CLI and Watch Mode](#cli-and-watch-mode)
* [Response Envelope and Telemetry](#response-envelope-and-telemetry)
* [Security Model](#security-model)
* [Performance and Scalability Notes](#performance-and-scalability-notes)
* [Benchmarking and Proof](#benchmarking-and-proof)
* [Failure Modes and Tradeoffs](#failure-modes-and-tradeoffs)
* [Appendix: Symbol IDs](#appendix-symbol-ids)
* [Appendix: Key Dependencies](#appendix-key-dependencies)

---

## System Overview

jCodeMunch-MCP is a local-first structured code retrieval system for AI agents.

Its purpose is to let an MCP-compatible client explore a repository by symbol, outline, structure, and tightly scoped context instead of repeatedly opening large files and scanning them linearly. It indexes source code once, extracts symbols with tree-sitter, stores stable symbol metadata plus byte offsets into cached raw files, and serves precise retrieval and search operations through an MCP server.

The central architectural principle is:

> **Retrieval precision is more efficient than brute-force context expansion.**

The system is therefore designed around symbol IDs, file outlines, bounded searches, context bundles, and direct byte-offset retrieval rather than repeated full-file reads.

---

## Design Goals

jCodeMunch is built around the following priorities:

1. **Minimize tokens sent to the model** by retrieving only relevant code.
2. **Preserve source fidelity** by caching original raw files and retrieving exact spans by byte offset.
3. **Stay fast on repeat access** through local storage, incremental indexing, sidecars, and caching.
4. **Remain explainable** through stable IDs, structured metadata, and explicit tool boundaries.
5. **Support real engineering workflows** such as onboarding, code navigation, impact analysis, class hierarchy inspection, and repository exploration.
6. **Favor deterministic structure-aware retrieval** over opaque end-to-end semantic systems when exact code access is required.

---

## Directory Structure

The repository includes core documentation, benchmark material, a lightweight CLI, and the MCP server package. A representative architecture-facing layout is shown below:

```text
jcodemunch-mcp/
├── pyproject.toml
├── uv.lock
├── README.md
├── QUICKSTART.md
├── USER_GUIDE.md
├── ARCHITECTURE.md
├── SPEC.md
├── SECURITY.md
├── LANGUAGE_SUPPORT.md
├── CONTEXT_PROVIDERS.md
├── TOKEN_SAVINGS.md
├── TROUBLESHOOTING.md
├── benchmarks/
│   ├── tasks.json
│   ├── README.md
│   ├── METHODOLOGY.md
│   ├── results.md
│   └── run_benchmarks.py
├── cli/
│   ├── cli.py
│   └── README.md
├── src/jcodemunch_mcp/
│   ├── server.py              # MCP dispatcher, auth/rate-limit, tool routing
│   ├── config.py              # JSONC config: global + per-project layering
│   ├── security.py            # Path validation, skip patterns, file caps
│   ├── agent_selector.py      # Complexity scoring + model routing (off/manual/auto)
│   ├── hook_event.py          # Worktree lifecycle event recording
│   ├── reindex_state.py       # Per-repo reindex generation tracking
│   ├── watcher.py             # File system watcher for auto-reindex
│   ├── cli/
│   │   ├── init.py            # `jcodemunch-mcp init` — one-command onboarding
│   │   └── hooks.py           # PreToolUse, PostToolUse, PreCompact hook handlers
│   ├── parser/
│   │   ├── languages.py       # LANGUAGE_REGISTRY, extension → language map
│   │   ├── extractor.py       # parse_file() dispatch; custom parsers
│   │   ├── imports.py         # Regex import extraction (19+ languages)
│   │   └── fqn.py             # PHP FQN ↔ symbol_id translation (PSR-4)
│   ├── storage/
│   │   ├── index_store.py
│   │   ├── sqlite_store.py
│   │   └── token_tracker.py   # Token savings + per-tool latency rings + opt-in telemetry.db sink (v1.74.0+)
│   ├── retrieval/
│   │   ├── signal_fusion.py   # Weighted Reciprocal Rank fusion (lexical + structural + similarity + identity)
│   │   ├── confidence.py      # 0-1 retrieval confidence score on every result envelope (v1.75.0+)
│   │   ├── freshness.py       # Per-symbol _freshness ∈ {fresh, edited_uncommitted, stale_index} (v1.77.0+)
│   │   ├── tuning.py          # Online weight tuner — learns per-repo semantic_weight from the ranking ledger (v1.79.0+)
│   │   └── embed_drift.py     # 16-string canary + cosine drift detector (v1.80.0+)
│   ├── summarizer/
│   ├── tools/
│   │   ├── _call_graph.py     # AST-derived call-graph helpers
│   │   ├── session_journal.py # Process-lifetime session tracking
│   │   ├── session_state.py   # Session save/restore across restarts
│   │   ├── turn_budget.py     # Cross-call token accumulator
│   │   ├── plan_turn.py       # Opening-move router (BM25 + PageRank)
│   │   ├── analyze_perf.py    # Per-tool latency + cache-hit telemetry; baseline diff + ranking-ledger view (v1.74.0+)
│   │   ├── tune_weights.py    # MCP wrapper around retrieval/tuning.py (v1.79.0+)
│   │   ├── check_embedding_drift.py # MCP wrapper around retrieval/embed_drift.py (v1.80.0+)
│   │   └── ...
│   └── ...
├── tests/
└── .github/workflows/
```

This layout is schematic rather than exhaustive. Its purpose is to reflect the primary architectural domains: server, parser, storage, summarization, tools, CLI, watch mode, documentation, and benchmark harnesses.

---

## High-Level Data Flow

```text
GitHub repo or local folder
    │
    ▼
Discovery / file enumeration
    │
    ▼
Security filtering
(path traversal, symlinks, secrets, binary/unsafe handling, size / path controls)
    │
    ▼
Language detection + tree-sitter parsing
    │
    ▼
Symbol extraction
(functions, classes, methods, constants, types, language-specific constructs)
    │
    ▼
Post-processing
(overload disambiguation, content hashing, import/reference metadata)
    │
    ▼
Context enrichment
(provider-derived metadata such as dbt or Git history)
    │
    ▼
Summarization
(docstring → AI batch → signature fallback)
    │
    ▼
Persistence
(index JSON + sidecars + cached raw files + savings telemetry)
    │
    ▼
MCP / CLI / watch consumers
(search, retrieval, outlines, bundles, hierarchy, blast radius, diff, suggestions)
```

---

## Core Architectural Concepts

### 1. Index once, retrieve many times

The main performance strategy is to pay the parsing and indexing cost once, then make subsequent searches and retrievals cheap and precise. Cached raw files plus symbol offsets make this possible.

### 2. Structured retrieval over full-file reading

Most operations are designed to answer questions such as:

* what symbols exist here?
* where is the implementation?
* what is related to this symbol?
* what code would this change affect?
* what is the surrounding context bundle?

This approach avoids loading entire files unless necessary.

### 3. Local-first storage

Indexes and raw-file caches live under `~/.code-index/` by default, optionally overridden via `CODE_INDEX_PATH`. This keeps repeat access fast and private.

### 4. Stable identities

The system relies on stable symbol IDs based on file path, qualified name, and kind, allowing later retrieval and cross-call workflows without fuzzy lookup.

### 5. Rich metadata envelopes

Every retrieval response carries a `_meta` envelope that, in addition to
token-savings telemetry and timing, exposes:

- `_meta.confidence` (0-1) — calibrated retrieval-quality score from the
  weighted geometric mean of top-1/top-2 score gap, top-1 strength,
  identity-match presence, and freshness. Lets agents gate follow-up
  calls on a single number.
- `_meta.freshness` — `{fresh, edited_uncommitted, stale_index, repo_is_stale}`
  summary. Each result entry also carries a per-symbol `_freshness` field
  derived from index SHA vs `git rev-parse HEAD` and per-file mtime checks.
- `_meta.latency_per_tool` (on `get_session_stats`) — p50/p95/max/error_rate
  per tool in the current session, populated from a per-tool 512-entry
  ring buffer maintained by `storage/token_tracker._State`.

### 6. Telemetry, learning, and quality gates

The retrieval stack carries a built-in telemetry surface, all of it
local-only and additive (no behavior change in default-flag paths):

- A per-tool latency ring is always populated by the `call_tool` dispatcher.
- An opt-in SQLite sink (`~/.code-index/telemetry.db`, gated by
  `perf_telemetry_enabled`) records `tool_calls` (latency rows) and
  `ranking_events` (search-result feature rows for later regression).
- The `tune_weights` tool reads `ranking_events`, learns per-repo
  retrieval weights, and writes `~/.code-index/tuning.jsonc`.
  `search_symbols` consults those overrides at query time when the caller
  uses the default `semantic_weight`.
- `benchmarks/replay/` contains golden retrieval-quality fixtures and a
  CI-friendly regression gate using nDCG/MRR/Recall — every release runs
  against the locked v1.75.0 fixture.
- `check_embedding_drift` pins a 16-string canary at first use and
  re-checks cosine drift on demand to catch silent provider model changes.

Operations return metadata describing timing, repository identity, truncation, token-savings estimates, and related execution details. Recent versions also label savings as estimates and include the estimation method where applicable.

---

## Repository Identity Model

jCodeMunch indexes both:

* **GitHub repositories** via `index_repo`
* **local folders** via `index_folder`

For GitHub repositories, the user-facing identity is typically `owner/repo`. For local folders, the system uses stable internal repository IDs while still exposing friendly names where possible through repo-listing operations.

For GitHub indexing, the system can store the Git tree SHA so unchanged incremental reindex runs can return early without re-downloading repository contents unnecessarily.

---

## Parsing and Symbol Extraction

### Language registry pattern

The parser uses a registry-oriented design where each supported language contributes extraction behavior through a `LanguageSpec`-style definition.

Representative shape:

```python
@dataclass
class LanguageSpec:
    ts_language: str
    symbol_node_types: dict[str, str]
    name_fields: dict[str, str]
    docstring_strategy: str
    decorator_node_type: str | None
    container_node_types: list[str]
    constant_patterns: list[str]
```

Every field is read by the extractor; `tests/test_every_language_spec_field_has_a_reader.py` refuses one that is not (#725).

### Extraction model

The generic extractor walks language-specific ASTs and emits normalized symbol records such as functions, classes, methods, constants, and types, plus language-specific constructs where useful.

### Post-processing passes

Important post-extraction passes include:

1. **Overload disambiguation** via numeric suffixes such as `~1`, `~2`
2. **Content hashing** using SHA-256 for change detection and diff-oriented features

These post-processing steps support stable IDs, change detection, and later comparison across snapshots.

---

## Context Provider Framework

A significant extension point in the system is the context provider framework. Providers enrich indexes with ecosystem-specific metadata during local folder indexing.

### Current provider model

Providers are auto-detected, load metadata from project files or repository state, and inject descriptions, tags, or related properties into symbol summaries, file summaries, and search keywords.

### Known provider examples

* **dbt provider**: detects `dbt_project.yml`, parses docs blocks and schema files, and enriches models and symbols with descriptions, tags, and column metadata
* **Git blame provider**: auto-activates when a `.git` directory is present and enriches files with author and modification metadata derived from repository history

### Architectural role

Context providers sit between raw structural parsing and final summarization and persistence. They improve retrieval quality by supplying domain and repository metadata that tree-sitter alone cannot infer.

This design allows jCodeMunch to remain structure-aware while supporting ecosystem-specific enrichment without tightly coupling every domain into the parser itself.

---

## Summarization Pipeline

The summarization layer generates concise symbol and file descriptions used for retrieval ergonomics and search relevance.

The high-level fallback chain is:

> **docstring → AI batch → signature fallback**

### Available summary backends

The system supports multiple summary backends, including:

* Anthropic-based summaries
* Gemini-based summaries
* local OpenAI-compatible servers via `OPENAI_API_BASE` and `OPENAI_MODEL`

### Architectural role

Summaries assist navigation and ranking, but they are not the architectural source of truth. The retrieval backbone remains symbol extraction, byte offsets, and cached source. Summaries enhance retrieval quality rather than replace deterministic access to code.

---

## Storage Model

Indexes are stored under `~/.code-index/` by default and can be redirected with `CODE_INDEX_PATH`.

### Core persisted artifacts

The primary storage backend is **SQLite in WAL (Write-Ahead Logging) mode**. Each indexed repository produces:

* `{repo_slug}.db` — SQLite database containing metadata, symbols, files, imports, and content blobs
* `{repo_slug}.db-wal` — WAL file for concurrent read/write access
* `{repo_slug}.db-shm` — shared-memory file for WAL coordination
* `{repo_slug}.meta` — lightweight metadata sidecar so repo listing does not require opening the database
* `{repo_slug}.checksum` — SHA-256 checksum sidecar for integrity verification
* `{repo_slug}/...` — cached raw source files for exact retrieval

The SQLite schema includes tables for `meta`, `symbols`, `files`, `imports`, `raw_cache`, and `content_blob` with appropriate indexes. Legacy JSON indexes (`{repo_slug}.json`) are auto-migrated to SQLite on first load.

### WAL mode benefits

WAL mode enables concurrent readers alongside a single writer, which is important for watch-mode scenarios where incremental reindexing runs while MCP tool calls serve retrieval queries. On graceful shutdown, the server checkpoints and compacts WAL files.

### Additional storage features

* LRU index cache with mtime invalidation
* metadata sidecars so repo listing does not require loading full indexes
* schema validation on index load
* SHA-256 checksum sidecars for integrity verification

### Retrieval-critical property

Each symbol stores byte offsets into the cached raw file, enabling exact retrieval via direct byte seeking rather than reparsing or rescanning the file.

---

## Indexing Strategies

### Full indexing

A full index pass performs discovery, security filtering, parsing, enrichment, summarization, and persistence across the entire repository or folder.

### Incremental indexing

Incremental indexing is based on stored file hashes and related repository metadata. Only changed files are reprocessed.

Enhancements to incremental indexing include:

* GitHub tree SHA short-circuiting for no-change reindexes
* mtime fast-path with fallback to hash comparison on mtime mismatch
* watch-triggered incremental reindexing
* SQLite WAL mode for concurrent read/write during incremental updates

### File discovery optimization

File discovery uses pre-resolved path strings and inlined security checks to minimize per-file overhead. Gitignore matching uses string prefix comparison instead of Path operations. These optimizations reduced full-index discovery time by approximately 2x on large repositories.

### Watch mode

The `watch` subcommand continuously monitors one or more directories using `watchfiles` and triggers incremental reindexing on change. This is useful for large repositories and multi-worktree development flows where manual reindexing would be inefficient.

### watch-claude mode

The `watch-claude` subcommand extends watch mode for Claude Code's worktree workflow. Claude Code creates git worktrees when opening parallel sessions, and removes them when sessions end. Rather than requiring users to manually pass paths, `watch-claude` discovers worktrees automatically via two complementary mechanisms:

* **Hook-driven discovery** — Claude Code's `WorktreeCreate` and `WorktreeRemove` hooks call `jcodemunch-mcp hook-event create|remove`, which appends events to a JSONL manifest at `~/.claude/jcodemunch-worktrees.jsonl`. The `watch-claude` process watches this file with `watchfiles` and reacts to new lines instantly — no polling.

* **Git-based discovery** — When `--repos` paths are specified, `watch-claude` periodically runs `git worktree list --porcelain` on each repo and filters for Claude-created worktrees (branches matching `claude/*` or `worktree-*`). This works with any worktree layout without requiring hooks.

Both modes can run concurrently, sharing a single `active` task map to avoid double-watching. On worktree removal, the watcher task is cancelled and `invalidate_cache` cleans up the index. Crashed watcher tasks are automatically restarted by the repos poller.

---

## Retrieval Model

The retrieval layer is centered on bounded, structured access to code.

### Basic retrieval flow

The most direct retrieval path is:

1. `search_symbols`
2. select a `symbol_id`
3. call `get_symbol_source`

### File-oriented discovery

When symbol names are unknown, file and repository outlines support navigation without loading full source:

* `get_repo_outline`
* `get_file_tree`
* `get_file_outline`

### Context bundles

`get_context_bundle` packages a symbol together with closely related material such as imports, neighboring items, or related symbols. Later versions extend this to support multi-symbol bundles, deduplicated imports, optional callers, and Markdown-formatted output.

### Drift verification

`get_symbol_source` supports verification so a client can determine whether retrieved content still matches the indexed version. This helps mitigate stale-index problems in fast-changing repositories.

### Retrieval honesty signals

Retrieval responses carry three independent trust signals (see `retrieval/verdict.py`, `retrieval/confidence.py`, `retrieval/freshness.py`):

* **Verdict** (`negative_evidence`): a four-state contract — `ok`, `low_confidence`, `absent`, `degraded`. `absent` is a positive claim backed by scan counts (the corpus was examined and the answer is not there); `degraded` means the index itself is impaired and silence proves nothing. The distinction prevents agents from hallucinating negatives off empty results.
* **Calibrated confidence** (`_meta.confidence`): a 0–1 score composed from the top-1 vs top-2 gap, absolute top-1 strength, identity-match presence, and freshness.
* **Per-symbol freshness**: each result entry is stamped `fresh`, `edited_uncommitted`, or `stale_index` from the index's recorded git HEAD vs the working tree plus per-file mtimes.

The contracts are published as JSON Schemas in `schemas/` (`retrieval-verdict`, `confidence-provenance`, `ranked-context-response`) so clients and CI pipelines can validate responses mechanically. The narrative treatment is [UNDER_THE_HOOD.md](UNDER_THE_HOOD.md) Chapter 1.

---

## Search and Ranking

### Symbol search

Symbol search began as a weighted scoring system using signals such as:

* exact name match
* substring match
* word overlap
* signature terms
* summary terms
* docstring and keyword matches

This remains a useful conceptual baseline.

### Ranking improvements

The current ranking model incorporates additional mechanisms, including:

* bounded heap search for efficient top-k result handling
* BM25-based symbol ranking
* centrality-aware ranking using a log-scaled bonus for symbols in frequently imported files as a tiebreaker

### Query-shape exact seeding

`retrieval/query_shape.py` classifies *source-shaped tokens* in queries — qualified names (`a.b`, `A::B`), CamelCase, snake_case, and dunder forms, with filename look-alikes (`server.py`) deliberately excluded. In `get_ranked_context`'s default path, exact symbol-name matches for those tokens are pinned ahead of the lexical ranking, capped at 3 seeds per token and 5 total so pinning cannot crowd out ranked results. The response reports recognition and seeding in `_meta.query_shape`. The fusion path needs no separate seeding: its identity channel already covers exact matches.

### Signal-channel fusion

`retrieval/signal_fusion.py` fuses independent channels — lexical (BM25), structural (graph centrality), identity (exact/qualified-name), and similarity (embeddings, when enabled) — rather than betting on a single scoring model. Fusion weights are loadable per repo, and learned overrides from `tune_weights` apply at query time.

### Practical interpretation

Search is best understood as a hybrid structured ranking pipeline that combines lexical and summary relevance with structure-derived signals such as file centrality.

### Text search

`search_text` remains the fallback for non-symbol content such as strings, comments, TODOs, or other literal text that does not map naturally to a symbol record.

### Suggestion mode

`suggest_queries` is designed to help users and agents enter unfamiliar repositories by surfacing useful keywords, frequently imported files, distributions, and ready-to-run query patterns.

---

## Import Graph and Relationship Features

A major architectural expansion in recent versions is support for lightweight relationship and impact analysis.

### `find_importers` and `find_references`

`find_importers` answers "what files import this file?" by resolving import specifiers against indexed source files. `find_references` answers "where is this identifier used?" by matching named imports and specifier stems. Both support batch array parameters (`file_paths` and `identifiers` respectively) for querying multiple targets in a single call with shared index loading.

### `get_dependency_graph`

This operation traverses the file-level dependency graph up to 3 hops in either direction (imports, importers, or both), providing a structural overview of file relationships.

### `check_references`

A composite tool that combines import-level reference checking (same logic as `find_references`) with content-level substring search (same approach as `search_text`) in one call. Returns an `is_referenced` boolean for quick dead-code detection. Skips defining files in content search to avoid false positives. Supports both singular and batch modes.

### `get_related_symbols`

This operation uses multiple heuristics, including:

* same-file co-location
* shared importers
* name-token overlap

### `get_class_hierarchy`

This operation traverses inheritance chains upward and downward, including external bases not present in the index when they can be identified structurally.

### `find_implementations` and evidence imports

`find_implementations` resolves concrete implementations through multiple channels with per-channel confidence: compile-time SCIP evidence and LSP dispatch (1.0), AST class hierarchy (0.85), duck-typed name match (0.65), decorator handler (0.45). The scores are declared ranking priors, and each response's `_meta.confidence_provenance` states every channel's basis (`declared` or `measured`) with gold-corpus measured precision/recall where available (see Benchmarking and Proof below).

Evidence stronger than static analysis is **imported, never generated**: `import-scip` ingests compiler-grade SCIP indexes, `import-trace` ingests runtime signal (feeding runtime-coverage and hot-path views). The server never executes repository code — strong evidence enters through files the operator hands it.

### `get_blast_radius`

This operation traverses the reverse import graph and inspects importers to estimate impacted files, separating confirmed from potential effects where possible.

### `get_symbol_diff`

This operation compares snapshots by `(name, kind)` and uses `content_hash` to report added, removed, and changed symbols.

### Architectural significance

These features mean the system is no longer limited to static symbol lookup. It increasingly supports lightweight code intelligence over the indexed repository while preserving the core retrieval-first design.

---

## Tool Surface

The tool surface is best described by capability domain rather than by a fixed count.

### Indexing and repository management

* `index_repo`
* `index_folder`
* `index_file`
* `list_repos`
* `resolve_repo`
* `invalidate_cache`

### Discovery and outlines

* `get_repo_outline`
* `get_file_tree`
* `get_file_outline` — supports batch via `file_paths` array parameter
* `suggest_queries`

### Retrieval

* `get_file_content`
* `get_symbol_source`
* `get_context_bundle`
* `get_ranked_context` — query-driven token-budgeted context assembly (BM25 + PageRank)

### Search and reference checking

* `search_symbols`
* `search_text`
* `search_columns`
* `check_references` — composite tool combining import + content search for dead-code detection

### Relationship and impact analysis

* `find_importers` — supports batch via `file_paths` array parameter
* `find_references` — supports batch via `identifiers` array parameter
* `get_dependency_graph`
* `get_related_symbols`
* `get_class_hierarchy`
* `get_blast_radius` — includes `include_source`, `source_budget`, `decorator_filter` params
* `get_symbol_diff`
* `get_call_hierarchy` — callers/callees N levels deep (AST-derived on v8+ indexes)
* `get_impact_preview` — transitive downstream impact analysis
* `get_hotspots` — complexity × churn ranking
* `get_coupling_metrics` — afferent/efferent coupling and instability
* `get_dependency_cycles` — circular dependency detection
* `get_extraction_candidates` — refactoring suggestions based on complexity and caller count
* `find_dead_code` — import-graph reachability from entry points
* `get_dead_code_v2` — multi-signal dead code detection with framework awareness
* `get_changed_symbols` — git diff to affected symbols mapping
* `get_symbol_importance` — PageRank centrality ranking
* `get_symbol_complexity` — cyclomatic/nesting/param metrics

### Adaptive tool tiers, schema budget, and annotations

The tool surface is tiered: new installs default to a compact core tier, with `set_tool_tier` escalating on demand; the self-guide tool (`jcodemunch_guide`) is always present so a one-line CLAUDE.md setup works at any tier. The compact core tier's full schema payload is held under a 4,000-token hard ceiling by a CI test that measures it with a real tokenizer — the enforced remedy is trimming a description, never regenerating the baseline. Every retrieval tool ships MCP `readOnlyHint: true`, and the handful of tools that reach the network or write an index carry `openWorldHint`; both annotations are part of the tested contract, so hosts that gate on them (plan modes, approval UIs) can trust the surface.

### Batch query support

Several tools accept array parameters alongside their singular equivalents, enabling multiple queries in a single call. This reduces LLM round-trips and cache re-reads. Singular parameters continue to return the original flat response shape for backward compatibility. Batch mode returns a grouped `results` array. Response-level `tip` fields in `_meta` guide models toward batch usage without requiring external configuration.

### Session and routing

* `plan_turn` — opening-move router (BM25 + PageRank confidence assessment)
* `get_session_context` — session history (reads, searches, edits, tool calls)
* `get_session_snapshot` — compact session state for context injection after compaction
* `register_edit` — post-edit cache invalidation (BM25, search result caches)
* `get_session_stats` — token savings, cost avoided, per-tool breakdown

### Agent config and indexing hygiene

* `audit_agent_config` — scan CLAUDE.md, .cursorrules, etc. for token waste and stale references
* `embed_repo` — precompute all symbol embeddings for semantic search

### Domain-specific retrieval

The system also supports provider-aware or ecosystem-aware retrieval operations where appropriate, such as dbt-related column search in enriched contexts.

---

## CLI and Watch Mode

jCodeMunch is MCP-first, but not MCP-only.

### CLI

A minimal CLI is provided over the shared index store, covering operations such as:

* `list`
* `index`
* `outline`
* `search`
* `get`
* `text`
* `file`
* `invalidate`

### Watch mode

The `watch` subcommand monitors one or more directories and performs incremental reindexing with debounce. The `watch-claude` subcommand builds on this for Claude Code's worktree workflow, using hook-driven events and/or `git worktree list` polling to discover worktrees automatically. Both use the same underlying store as the MCP server, so updates become immediately visible to MCP consumers.

Architecturally, CLI, watch, and watch-claude are all thin interfaces over the same parser, storage, indexing, and retrieval core.

---

## Response Envelope and Telemetry

All tool responses include an `_meta` envelope.

Representative fields include:

* `timing_ms`
* `repo`
* `symbol_count`
* `truncated`
* `tokens_saved`
* `total_tokens_saved`
* `estimate_method`
* `cost_avoided` — estimated cost savings per model (dict with model names as keys)
* `total_cost_avoided` — cumulative cost savings across session
* `powered_by` — attribution string
* `tip` — guidance for models toward more efficient tool usage (e.g., batch params, composite tools)

Representative shape:

```json
{
  "result": "...",
  "_meta": {
    "timing_ms": 42,
    "repo": "owner/repo",
    "truncated": false,
    "tokens_saved": 2450,
    "total_tokens_saved": 184320,
    "estimate_method": "...",
    "cost_avoided": {"claude_opus_4_6": 0.012, "claude_sonnet_4_6": 0.007},
    "total_cost_avoided": {"claude_opus_4_6": 968.32, "claude_sonnet_4_6": 580.99},
    "powered_by": "jcodemunch-mcp by jgravelle",
    "tip": "Tip: use file_paths=[...] to query multiple files in one call."
  }
}
```

Not all fields are present in every response. `tip` fields appear only in single-item responses and are stripped from batch results to keep them clean.

### Savings telemetry

Running totals are persisted in `~/.code-index/_savings.json`. Recent versions improved cross-process accounting so concurrent processes do not overwrite cumulative totals.

### Community meter

If enabled, tool calls can report a token-savings delta together with an anonymous install identifier to a global community counter. This reporting is intended to exclude repository names, paths, and source content.

---

## Security Model

Security is a first-class subsystem.

### File and path protections

The system includes protections for:

* path traversal
* symlink validation
* secret-file exclusion
* binary file detection
* safe encoding reads using replacement handling

### Additional hardening

Recent security-oriented additions include:

* SSRF prevention for configurable API base URLs
* ReDoS protection in `search_text`
* symlink-safe temporary file handling
* HTTP bearer token authentication for HTTP transport
* absolute path redaction via `JCODEMUNCH_REDACT_SOURCE_ROOT`

The security model therefore extends beyond file safety to include network and response-surface hardening.

---

## Performance and Scalability Notes

Recent versions added or documented several performance-oriented mechanisms:

* streaming file indexing to reduce peak memory usage
* bounded heap search for better top-k scaling
* LRU index cache with mtime invalidation
* sidecars for cheaper repo listing
* incremental indexing with mtime fast-path
* watch-based updates
* centrality-aware ranking
* no-change short-circuiting via Git tree SHA
* SQLite WAL mode for concurrent read/write without locking
* optimized file discovery using pre-resolved path strings and inlined security checks (approximately 2x speedup)
* batch array parameters on high-call-count tools to reduce LLM round-trips and cache re-reads

### Practical implications

The system is architected to stay responsive in larger repositories by:

1. minimizing reparsing
2. minimizing reloading
3. minimizing retrieval payload size
4. avoiding full result sorts when only top-k results are needed
5. minimizing LLM round-trips through batch query support

---

## Benchmarking and Proof

The benchmark surface has expanded to include:

* a public benchmark corpus in `benchmarks/tasks.json`
* methodology documentation
* reproducible benchmark materials
* canonical `tiktoken`-measured results
* golden retrieval-quality fixtures and a CI regression gate (`benchmarks/replay/`, nDCG/MRR/Recall)
* an authored gold-labeled implementation-pattern corpus (`benchmarks/goldset/`, Python/TypeScript/Go, seeded with deliberate false-positive traps) whose measurement CI re-runs on every build

This is architecturally important because the system makes concrete efficiency claims. Reproducible benchmarking is therefore part of the product’s proof surface, not just a marketing artifact.

### Confidence provenance

The proof surface is bound to the responses by `retrieval/provenance.py`: every confidence constant carries a basis — `measured` (backed by a committed artifact in `benchmarks/provenance/`, with CI asserting the artifact matches a live re-run, so drift fails the build) or `declared` (an engineering prior, labeled as exactly that). A prior is never presented as a measurement; a `declared` value graduates to `measured` only when the gold corpus backs it, and operating priors change only through deliberate, replay-gated recalibration. Responses surface this via `_meta.confidence_provenance` and the receipt/session-stats `savings_provenance` blocks. Full treatment: [UNDER_THE_HOOD.md](UNDER_THE_HOOD.md) Chapter 3.

---

## Failure Modes and Tradeoffs

No retrieval system is without tradeoffs.

### 1. The system only helps when the client uses it

If the agent continues to open whole files instead of using jCodeMunch tools, the retrieval architecture is present but the efficiency benefits do not materialize.

### 2. Structural retrieval is not identical to deep semantic reasoning

The system is strong at locating, packaging, and relating code. It is not intended to replace all forms of semantic reasoning. Summaries and ranking improve retrieval quality, but the backbone remains structure-aware access.

### 3. Index freshness matters

Because the system serves from a local index and cached raw files, stale indexes can mislead unless verification or reindexing is used.

### 4. Language support is broad but not uniform

Different languages and ecosystem providers expose different kinds of metadata and symbols. The architecture is extensible, but capability depth varies by language and provider maturity.

### 5. Broader capability surfaces increase maintenance demands

As the tool surface expands, protocol clarity, result consistency, and documentation accuracy become more important and more difficult to maintain.

---

## Appendix: Symbol IDs

Symbol IDs follow this stable format:

```text
{file_path}::{qualified_name}#{kind}
```

Examples:

```text
src/main.py::UserService#class
src/main.py::UserService.login#method
src/utils.py::authenticate#function
config.py::MAX_RETRIES#constant
```

IDs remain stable across re-indexing as long as file path, qualified name, and kind remain unchanged. Duplicate overloads can receive numeric suffixes such as `~1`, `~2`.

---

## Appendix: Key Dependencies

Representative architectural dependencies include:

| Package                     | Role                            |
| --------------------------- | ------------------------------- |
| `mcp`                       | MCP server framework            |
| `httpx`                     | Async HTTP and GitHub access    |
| `tree-sitter-language-pack` | Precompiled language grammars   |
| `pathspec`                  | Gitignore pattern matching      |
| `anthropic`                 | Claude-based summarization      |
| `google-generativeai`       | Gemini-based summarization      |
| `pyyaml`                    | dbt schema and provider parsing |
| `watchfiles`                | optional watch mode             |
| `sqlite3` (stdlib)          | WAL-mode index storage backend  |

These dependencies support the core architectural layers: protocol transport, repository acquisition, parsing, storage, summarization, file watching, and safe concurrent access.
