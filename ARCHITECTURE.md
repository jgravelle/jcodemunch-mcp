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
│   ├── server.py
│   ├── security.py
│   ├── parser/
│   ├── storage/
│   ├── summarizer/
│   ├── tools/
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
    param_fields: dict[str, str]
    return_type_fields: dict[str, str]
    docstring_strategy: str
    decorator_node_type: str | None
    container_node_types: list[str]
    constant_patterns: list[str]
    type_patterns: list[str]
```

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

The main storage artifacts are:

* `{repo}.json` for metadata, hashes, and symbol records
* `{repo}/...` cached raw source files for exact retrieval

### Additional storage features

Recent storage-oriented improvements include:

* cross-process file locking to prevent concurrent corruption
* LRU index cache with mtime invalidation
* metadata sidecars so repo listing does not require loading full index JSON
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
* watch-triggered incremental reindexing
* atomic writes using temporary files
* locking and integrity checks for concurrent or interrupted runs

### Watch mode

The watch subcommand continuously monitors one or more directories using `watchfiles` and triggers incremental reindexing on change. This is useful for large repositories and multi-worktree development flows where manual reindexing would be inefficient.

---

## Retrieval Model

The retrieval layer is centered on bounded, structured access to code.

### Basic retrieval flow

The most direct retrieval path is:

1. `search_symbols`
2. select a `symbol_id`
3. call `get_symbol` or `get_symbols`

### File-oriented discovery

When symbol names are unknown, file and repository outlines support navigation without loading full source:

* `get_repo_outline`
* `get_file_tree`
* `get_file_outline`

### Context bundles

`get_context_bundle` packages a symbol together with closely related material such as imports, neighboring items, or related symbols. Later versions extend this to support multi-symbol bundles, deduplicated imports, optional callers, and Markdown-formatted output.

### Drift verification

`get_symbol` supports verification so a client can determine whether retrieved content still matches the indexed version. This helps mitigate stale-index problems in fast-changing repositories.

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

### Practical interpretation

Search is best understood as a hybrid structured ranking pipeline that combines lexical and summary relevance with structure-derived signals such as file centrality.

### Text search

`search_text` remains the fallback for non-symbol content such as strings, comments, TODOs, or other literal text that does not map naturally to a symbol record.

### Suggestion mode

`suggest_queries` is designed to help users and agents enter unfamiliar repositories by surfacing useful keywords, frequently imported files, distributions, and ready-to-run query patterns.

---

## Import Graph and Relationship Features

A major architectural expansion in recent versions is support for lightweight relationship and impact analysis.

### `get_related_symbols`

This operation uses multiple heuristics, including:

* same-file co-location
* shared importers
* name-token overlap

### `get_class_hierarchy`

This operation traverses inheritance chains upward and downward, including external bases not present in the index when they can be identified structurally.

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
* `list_repos`
* `invalidate_cache`

### Discovery and outlines

* `get_repo_outline`
* `get_file_tree`
* `get_file_outline`
* `suggest_queries`

### Retrieval

* `get_file_content`
* `get_symbol`
* `get_symbols`
* `get_context_bundle`

### Search

* `search_symbols`
* `search_text`

### Relationship and impact analysis

* `get_related_symbols`
* `get_class_hierarchy`
* `get_blast_radius`
* `get_symbol_diff`

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

The `watch` subcommand monitors one or more directories and performs incremental reindexing with debounce. It uses the same underlying store as the MCP server, so updates become immediately visible to MCP consumers.

Architecturally, both CLI and watch mode are thin interfaces over the same parser, storage, indexing, and retrieval core.

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
    "estimate_method": "..."
  }
}
```

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
* incremental indexing
* watch-based updates
* centrality-aware ranking
* no-change short-circuiting via Git tree SHA

### Practical implications

The system is architected to stay responsive in larger repositories by:

1. minimizing reparsing
2. minimizing reloading
3. minimizing retrieval payload size
4. avoiding full result sorts when only top-k results are needed

---

## Benchmarking and Proof

The benchmark surface has expanded to include:

* a public benchmark corpus in `benchmarks/tasks.json`
* methodology documentation
* reproducible benchmark materials
* canonical `tiktoken`-measured results

This is architecturally important because the system makes concrete efficiency claims. Reproducible benchmarking is therefore part of the product’s proof surface, not just a marketing artifact.

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
| `anthropic`                 | Claude-based summarization      |
| `google-generativeai`       | Gemini-based summarization      |
| `pyyaml`                    | dbt schema and provider parsing |
| `watchfiles`                | optional watch mode             |
| `filelock`                  | cross-process index protection  |

These dependencies support the core architectural layers: protocol transport, repository acquisition, parsing, summarization, file watching, and safe concurrent storage.
