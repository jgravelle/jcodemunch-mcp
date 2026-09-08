# Technical Specification

> **Version note:** This document describes the external tool contract, data model, operational behavior, and implementation-aligned semantics of jCodeMunch-MCP at a high level. It is intended for engineers, integrators, evaluators, and technical stakeholders who need a precise understanding of how the system behaves. For broader architectural context, see `ARCHITECTURE.md`. For end-user workflows and setup, see `USER_GUIDE.md`.

---

## Table of Contents

* [Overview](#overview)
* [Core Operating Model](#core-operating-model)
* [Tool Surface](#tool-surface)

  * [Indexing and Repository Management](#indexing-and-repository-management)
  * [Discovery and Repository Inspection](#discovery-and-repository-inspection)
  * [Retrieval](#retrieval)
  * [Search](#search)
  * [Relationship and Impact Analysis](#relationship-and-impact-analysis)
  * [Freshness and Backpressure](#freshness-and-backpressure)
  * [Observability](#observability)
* [Data Models](#data-models)

  * [Symbol](#symbol)
  * [CodeIndex](#codeindex)
* [Repository Acquisition and File Discovery](#repository-acquisition-and-file-discovery)

  * [GitHub Repositories](#github-repositories)
  * [Local Folders](#local-folders)
  * [Filtering Pipeline](#filtering-pipeline)
* [Indexing Semantics](#indexing-semantics)
* [Watcher and Index Freshness](#watcher-and-index-freshness)
* [Search and Ranking Semantics](#search-and-ranking-semantics)
* [Retrieval Semantics](#retrieval-semantics)
* [Response Envelope](#response-envelope)
* [Error Handling](#error-handling)
* [Transport Modes and CLI](#transport-modes-and-cli)
* [Environment Variables](#environment-variables)
* [Security and Safety Controls](#security-and-safety-controls)
* [Performance and Operational Notes](#performance-and-operational-notes)
* [Token Savings Semantics](#token-savings-semantics)
* [Compatibility and Evolution Notes](#compatibility-and-evolution-notes)

---

## Overview

**jCodeMunch-MCP** pre-indexes repository source code using tree-sitter AST parsing and builds a structured catalog of symbols such as functions, classes, methods, constants, and types. Each symbol stores structured metadata including its signature, summary, location, and byte offsets into cached raw file content. Full source is then retrievable on demand through direct byte-offset access rather than repeated full-file reads.

The system is designed to support AI agents and MCP-compatible clients that need repository navigation, symbol lookup, targeted code retrieval, and bounded context assembly while minimizing unnecessary token consumption.

At a high level, the contract is:

1. index a repository or local folder
2. inspect structure through outlines or trees
3. search for relevant symbols or text
4. retrieve only the required code or contextual bundle
5. optionally verify freshness or compute relationships and impact

---

## Core Operating Model

jCodeMunch operates as a **local-first structured retrieval layer**.

Its core behaviors are:

* parse source files into a normalized symbol index
* persist symbol metadata separately from raw file contents
* retrieve precise source segments by byte offset
* expose those capabilities through a stable MCP tool surface
* report operational metadata through `_meta` envelopes

The design assumes that repeated code exploration should be driven by structured navigation and targeted retrieval rather than by repeatedly loading large files into model context.

---

## Tool Surface

The tool surface is best described by capability domain rather than by a fixed historical count.

### Indexing and Repository Management

#### `index_repo` — Index a GitHub repository

```json
{
  "url": "owner/repo",
  "use_ai_summaries": true
}
```

Indexes a GitHub repository by enumerating source files, applying the security and filtering pipeline, parsing with tree-sitter, generating summaries, and persisting both the index and cached raw files.

**Behavioral notes:**

* accepts repository identifiers such as `owner/repo`
* can use AI-generated summaries when configured
* stores repository metadata, language counts, symbol records, file hashes, and cached raw content
* may short-circuit unchanged reindex runs when repository metadata indicates no relevant changes

---

#### `index_folder` — Index a local folder

```json
{
  "path": "/path/to/project",
  "extra_ignore_patterns": ["*.generated.*"],
  "follow_symlinks": false
}
```

Indexes a local project folder using the same parsing and persistence model as remote indexing, with additional local-path safety controls.

**Behavioral notes:**

* performs recursive discovery with path and symlink protections
* respects `.gitignore` and additional ignore patterns
* can auto-detect supported ecosystem tools and apply context-provider enrichment
* may return context-enrichment statistics when providers are active

---

#### `index_file` — Re-index a single file

```json
{
  "path": "/absolute/path/to/file.py",
  "use_ai_summaries": false,
  "context_providers": true
}
```

Re-indexes a single file without touching the rest of the index. Locates the owning index by scanning `source_root` of all indexed repos and selecting the most specific match. Exits early if the file's hash is unchanged.

**Behavioral notes:**

* requires the file's parent folder to already be indexed via `index_folder`
* validates security (path must be within a known `source_root`)
* checks mtime and hash — skips parse/save if file is unchanged
* parses with tree-sitter, runs context providers, and writes a surgical incremental update
* faster than re-running `index_folder` for single-file edits

---

#### `embed_repo` — Precompute symbol embeddings for semantic search

```json
{
  "repo": "owner/repo",
  "batch_size": 50,
  "force": false
}
```

Precomputes and caches all symbol embeddings in one pass. This is an optional warm-up step; `search_symbols(semantic=true)` also computes embeddings lazily on first use.

**Behavioral notes:**

* embeddings are stored in a `symbol_embeddings` SQLite table in the per-repo `.db` file
* serialized as float32 BLOBs via stdlib `array` module; no numpy required
* `force=true` recomputes even when cached embeddings exist
* requires an embedding provider (`JCODEMUNCH_EMBED_MODEL`, `OPENAI_API_KEY + OPENAI_EMBED_MODEL`, or `GOOGLE_API_KEY + GOOGLE_EMBED_MODEL`)

---

#### `invalidate_cache` — Delete index for a repository

```json
{
  "repo": "owner/repo"
}
```

Deletes the persisted index and cached raw content associated with the specified repository identifier.

**Behavioral notes:**

* removes both metadata and cached content
* is typically used when the index is stale, corrupted, or intentionally reset

---

#### `list_repos` — List indexed repositories

No input required.

Returns all indexed repositories known to the local store, together with summary metadata such as symbol counts, file counts, languages, index version, and optional display metadata.

---

#### `resolve_repo` — Resolve a path to a repo identifier

```json
{
  "path": "/absolute/path/to/project"
}
```

O(1) lookup that resolves a filesystem path to its indexed repo identifier. Accepts repo roots, worktrees, subdirectories, or file paths. Computes the deterministic repo ID from the path hash and checks index existence directly — far cheaper than `list_repos` when you only need one repo.

Returns `indexed: true` with full metadata (symbol count, file count, languages, etc.) if found, or `indexed: false` with the computed repo ID and a hint to call `index_folder` if not.

**Behavioral notes:**

* Preferred over `list_repos` when the client knows its working directory
* Tries the input path first, then walks up to the git root for subdirectory lookups
* Returns ~200 tokens vs. potentially thousands from `list_repos`

---

### Discovery and Repository Inspection

#### `get_file_tree` — Get file structure

```json
{
  "repo": "owner/repo",
  "path_prefix": "src/"
}
```

Returns a nested directory tree with file-level annotations such as language and symbol count where available.

**Behavioral notes:**

* useful for structural exploration before retrieving any source
* `path_prefix` can be used to scope the returned subtree

---

#### `get_file_outline` — Get symbols in a file

```json
{
  "repo": "owner/repo",
  "file_path": "src/main.py"
}
```

Returns a hierarchical symbol tree for a file. Parent-child relationships such as class-to-method are preserved where the language and parser support them.

**Behavioral notes:**

* includes signatures and summaries
* does not include full source
* intended as a lightweight inspection tool before `get_symbol_source`

---

#### `get_file_content` — Get cached file content

```json
{
  "repo": "owner/repo",
  "file_path": "src/main.py",
  "start_line": 10,
  "end_line": 30
}
```

Returns raw cached file content from the local store.

**Behavioral notes:**

* optional `start_line` and `end_line` are 1-based inclusive
* line ranges are clamped to file bounds
* intended for line-oriented retrieval when symbol retrieval is not appropriate

---

#### `get_repo_outline` — High-level repository overview

```json
{
  "repo": "owner/repo"
}
```

Returns repository-level summary information such as file counts by directory, language breakdown, symbol-kind distribution, most-imported files, and most central symbols by PageRank.

**Behavioral notes:**

* lighter than `get_file_tree`
* intended as a coarse-grained entry point for unfamiliar repositories
* includes `most_central_symbols` (top 10 by PageRank score) alongside the existing `most_imported_files`
* `_meta.is_stale` reflects whether the git HEAD has moved since index time

---

#### `suggest_queries` — Suggest high-value initial queries

```json
{
  "repo": "owner/repo"
}
```

Returns guidance for exploring unfamiliar repositories, including useful keywords, common entry points, frequently imported files, distributions, and candidate follow-up queries.

**Behavioral notes:**

* intended to reduce cold-start friction
* useful when users or agents do not yet know symbol names or subsystem terminology

---

### Retrieval

#### `get_symbol_source` — Get full source of one or more symbols

Single symbol — returns flat symbol object:

```json
{
  "repo": "owner/repo",
  "symbol_id": "src/main.py::MyClass.login#method",
  "verify": true,
  "context_lines": 3
}
```

Batch — returns `{symbols, errors}`:

```json
{
  "repo": "owner/repo",
  "symbol_ids": ["id1", "id2", "id3"]
}
```

**Behavioral notes:**

* retrieval is based on cached raw file content, not reparsing
* `symbol_id` and `symbol_ids` are mutually exclusive — passing both is an error
* `verify` re-hashes the retrieved source and compares it with the stored `content_hash`; applies to all symbols in batch mode
* the stored `content_hash` itself is returned on a served row only when `verify` or `receipt` is requested (v1.108.208). Neither the boolean `content_verified` nor the receipt's `content_sha256` changed; what stopped shipping is the raw 64-char digest on responses that asked for neither
* `context_lines` optionally adds surrounding lines; applies to all symbols in batch mode
* in batch mode, missing symbols are reported in `errors[]` without causing other lookups to fail

---

#### `get_context_bundle` — Retrieve a bounded contextual package around a symbol or symbol set

```json
{
  "repo": "owner/repo",
  "symbol_id": "src/auth.py::AuthService.login#method",
  "token_budget": 4000,
  "budget_strategy": "most_relevant",
  "include_budget_report": false
}
```

Returns a bounded retrieval package designed to support downstream reasoning tasks without requiring a series of separate calls.

**Behavioral notes:**

* includes the target symbol, related imports, deduplicated dependencies, and optional callers
* supports multi-symbol bundles via `symbol_ids[]`
* `token_budget` (int) — when set, symbols are ranked and trimmed to fit; fully backward-compatible (omit to get existing behavior)
* `budget_strategy`: `"most_relevant"` (default, ranks by import in-degree), `"core_first"` (primary symbol first, imports ranked by centrality), `"compact"` (signatures only, no bodies)
* `include_budget_report=true` adds a `budget_report` field with `budget_tokens`, `used_tokens`, `included_symbols`, `excluded_symbols`, and `strategy`

---

#### `get_ranked_context` — Query-driven token-budgeted context assembler

```json
{
  "repo": "owner/repo",
  "query": "authentication flow",
  "token_budget": 4000,
  "strategy": "combined"
}
```

Returns the best-fit symbols for a task description, ranked by relevance and centrality, greedily packed to fit within the token budget.

**Behavioral notes:**

* `strategy`: `"combined"` (BM25 + PageRank weighted sum, default), `"bm25"` (pure text relevance), `"centrality"` (PageRank only)
* `include_kinds` (list) — restrict candidates to specific symbol kinds
* `scope` (string) — restrict candidates to a subdirectory
* per-item response includes `relevance_score`, `centrality_score`, `combined_score`, `tokens`, and `source`
* token counting uses `len(text) // 4` heuristic; upgrades to `tiktoken` if installed (no hard dep)

---

### Search

#### `search_symbols` — Search across indexed symbols

```json
{
  "repo": "owner/repo",
  "query": "authenticate",
  "kind": "function",
  "language": "python",
  "file_pattern": "src/**/*.py",
  "max_results": 10,
  "fuzzy": false,
  "sort_by": "relevance",
  "semantic": false
}
```

Searches the symbol index using a structured ranking pipeline.

**Behavioral notes:**

* all filters are optional
* supports narrowing by symbol kind, language, and file path pattern
* ranking uses multiple lexical and metadata signals rather than a single naive match rule
* **fuzzy matching** — `fuzzy=true` enables a trigram Jaccard + Levenshtein fallback when BM25 confidence is low (`top score < 0.1`) or when explicitly requested. Fuzzy results carry `match_type="fuzzy"`, `fuzzy_similarity`, and `edit_distance` fields. Zero behavioral change when `fuzzy=false` (default).
* **centrality-aware ranking** — `sort_by`: `"relevance"` (default, BM25), `"centrality"` (filter by query match, rank by PageRank), `"combined"` (BM25 + PageRank weighted sum)
* **semantic search** — `semantic=true` enables hybrid BM25 + embedding ranking. Requires an embedding provider. `semantic_weight` (float, default 0.5) controls the blend. `semantic_only=true` skips BM25 entirely. `semantic=false` (default) has zero performance impact and zero new imports. `semantic=true` with no provider configured returns a structured error (`error: "no_embedding_provider"`).
* intended as the primary entry point for locating code by meaningfully named program elements

---

#### `search_text` — Full-text search across cached file contents

```json
{
  "repo": "owner/repo",
  "query": "TODO",
  "file_pattern": "*.py",
  "max_results": 20,
  "context_lines": 2
}
```

Performs case-insensitive text search across indexed file contents.

**Behavioral notes:**

* intended for comments, strings, configuration values, TODO markers, or other non-symbol content
* returns grouped matches in a file-oriented structure
* can include surrounding lines through `context_lines`
* supports `semantic=true` for embedding-based search (same provider requirements as `search_symbols`)

Representative result shape:

```json
[
  {
    "file": "src/main.py",
    "matches": [
      {
        "line": 42,
        "text": "TODO: refactor this path",
        "before": ["..."],
        "after": ["..."]
      }
    ]
  }
]
```

---

#### `search_columns` — Search column metadata across indexed models

```json
{
  "repo": "owner/repo",
  "query": "customer_id",
  "model_pattern": "stg_*",
  "max_results": 20
}
```

Searches column metadata emitted by context providers (dbt, SQLMesh, database catalogs, etc.). Returns model name, file path, column name, and description.

**Behavioral notes:**

* only returns results when the index was built with an active provider that emits column data
* `model_pattern` narrows results to matching model names
* intended for data-engineering workflows and lineage exploration

---

### Relationship and Impact Analysis

#### `get_related_symbols` — Find structurally or heuristically related symbols

```json
{
  "repo": "owner/repo",
  "symbol_id": "src/auth.py::AuthService.login#method"
}
```

Finds symbols related to a target symbol using heuristics such as same-file co-location, shared importers, or token overlap in names.

---

#### `get_class_hierarchy` — Traverse inheritance structure

```json
{
  "repo": "owner/repo",
  "symbol_id": "src/services.py::UserService#class"
}
```

Returns class hierarchy information above and below the target symbol, including known indexed bases and derived classes where they can be identified.

---

#### `get_blast_radius` — Estimate impacted files or symbols

```json
{
  "repo": "owner/repo",
  "symbol_id": "src/core.py::process_order#function",
  "include_depth_scores": false
}
```

Estimates likely impact by traversing reverse import relationships and inspecting relevant importers.

**Behavioral notes:**

* distinguishes confirmed from potential impact where enough evidence exists
* always returns `overall_risk_score` (0.0–1.0, weighted by hop distance: `1/depth^0.7`) and `direct_dependents_count`
* `include_depth_scores=true` adds `impact_by_depth` — confirmed files grouped by BFS layer, each with a `risk_score`
* flat `confirmed`/`potential` lists are preserved unchanged (backward compatible)
* intended for change-planning and refactoring workflows

---

#### `get_symbol_importance` — Rank symbols by architectural centrality

```json
{
  "repo": "owner/repo",
  "top_n": 20,
  "algorithm": "pagerank"
}
```

Returns the most architecturally important symbols in a repo, ranked by PageRank or in-degree on the import graph.

**Behavioral notes:**

* `algorithm`: `"pagerank"` (damping=0.85, convergence threshold=1e-6, max 100 iterations) or `"degree"` (raw in-degree count, O(1))
* `scope` (string) — restrict to a subdirectory or file glob
* response includes `symbol_id`, `rank`, `score`, `in_degree`, `out_degree`, `kind`, and `iterations_to_converge`
* PageRank scores are cached per `CodeIndex` load and recomputed only on incremental reindex

---

#### `find_dead_code` — Find unreachable symbols and files

```json
{
  "repo": "owner/repo",
  "granularity": "symbol",
  "min_confidence": 0.8,
  "include_tests": false
}
```

Finds symbols and files unreachable from any entry point via BFS over the import graph.

**Behavioral notes:**

* entry points auto-detected: `main.py`, `__main__.py`, `conftest.py`, `manage.py`, `__init__.py` package roots, `if __name__ == "__main__"` guards (Python), and common framework decorators
* `granularity`: `"symbol"` (default) or `"file"` (a file is dead only when all its symbols are dead)
* `min_confidence` (float, default 0.8) — lower values surface more candidates including ambiguous cases
* `entry_point_patterns` (list) — additional glob patterns to treat as live roots
* confidence scoring: `1.0` = zero importers, no framework decoration; `0.9` = zero importers in a test file; `0.7` = all importers are themselves dead (cascading)

---

#### `get_changed_symbols` — Map a git diff to affected symbols

```json
{
  "repo": "owner/repo",
  "since_sha": null,
  "until_sha": "HEAD",
  "include_blast_radius": false
}
```

Maps a git diff to the symbols that were added, modified, removed, or renamed.

**Behavioral notes:**

* `since_sha` defaults to the SHA stored at index time; `until_sha` defaults to `"HEAD"`
* `change_type` values: `"added"`, `"removed"`, `"modified"`, `"renamed"` (body-hash-identical rename heuristic)
* `include_blast_radius=true` appends downstream importers per changed symbol (up to `max_blast_depth` hops)
* requires a locally indexed repo (`index_folder`); GitHub-indexed repos return a structured error
* requires `git` on PATH; graceful error if not available
* filters index-storage files from the diff when the storage dir is inside the repo

---

#### `find_importers` — Find files that import a given file

```json
{
  "repo": "owner/repo",
  "file_path": "src/auth.py"
}
```

Answers "what uses this file?" by walking the stored import graph. Each result indicates whether the importer is itself imported by other files.

**Behavioral notes:**

* accepts `file_path` (single) or `file_paths` (batch)
* returns `has_importers` boolean per result for quick fan-out assessment
* useful for understanding coupling before refactoring

---

#### `find_references` — Find files that reference an identifier

```json
{
  "repo": "owner/repo",
  "identifier": "UserService"
}
```

Answers "where is this used?" by combining import-graph analysis and identifier-name matching. For dbt, also traces `{{ ref() }}` and `{{ source() }}` edges.

**Behavioral notes:**

* accepts `identifier` (single) or `identifiers` (batch)
* returns matches grouped by type: import references, content references, model references

---

#### `check_references` — Dead-code detection combining imports and content search

```json
{
  "repo": "owner/repo",
  "identifier": "deprecated_helper"
}
```

Combines `find_references` and `search_text` into one call. Returns `is_referenced` boolean for quick dead-code checks plus detailed matches when references exist.

**Behavioral notes:**

* accepts `identifier` (single) or `identifiers` (batch)
* `search_content` controls whether to include text-search fallback (default true)
* intended as a single-call replacement for the common "is this symbol used anywhere?" pattern

---

#### `get_dependency_graph` — File-level dependency graph

```json
{
  "repo": "owner/repo",
  "file": "src/core.py",
  "direction": "both",
  "depth": 2
}
```

Traverses import relationships to build a file-level dependency graph.

**Behavioral notes:**

* `direction`: `"imports"` (files this file depends on), `"importers"` (files that depend on this file), or `"both"`
* `depth`: number of hops to traverse (1–3)
* useful for understanding module coupling and change impact

---

#### `get_symbol_diff` — Compare indexed symbol states across snapshots

```json
{
  "repo_a": "owner/repo-branch-a",
  "repo_b": "owner/repo-branch-b"
}
```

Reports added, removed, or changed symbols by comparing two indexed snapshots using `(name, kind)` and `content_hash`. Index the same repo under two names to compare branches.

---

### Observability

#### `get_session_stats` — Token savings statistics

No input required.

Returns per-session and all-time token savings statistics, per-tool breakdown, session duration, cost-avoidance estimates across model price points, and (v1.74.0+) `latency_per_tool` with `{count, p50_ms, p95_ms, max_ms, errors, error_rate}` for every tool exercised this session.

---

#### `analyze_perf` — Per-tool latency + cache telemetry (v1.74.0+)

Inputs:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `window` | `"session" \| "1h" \| "24h" \| "7d" \| "all"` | `"session"` | `"session"` reads the in-memory ring; the others read the persistent `~/.code-index/telemetry.db` SQLite sink (requires `perf_telemetry_enabled`). |
| `top` | int | 20 | Cap on slowest-tool entries returned. |
| `tool` | str | optional | Restrict the analysis to a single tool name. |
| `compare_release` | str | optional | Diffs the current session against `benchmarks/token_baselines/v{X}.json` (saved by `capture_token_baseline.py`). Adds `baseline_diff` per-tool deltas in tokens_saved + latency. Missing baseline reported via `baseline_meta.found=false`. |
| `ledger` | bool | `false` | Include `ranking_ledger` summary aggregating the v1.78.0 `ranking_events` table by repo (events, avg_confidence, identity_hits, semantic_used, stale_events) and by tool. |

Returns the slowest tools by p95 alongside the coldest cache hit-rates.

---

#### `tune_weights` — Online retrieval-weight tuner (v1.79.0+)

Inputs:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `repo` | str | optional | Limit tuning to one repo. Default: every repo present in the ledger. |
| `dry_run` | bool | `false` | Compute proposed deltas without writing `~/.code-index/tuning.jsonc`. |
| `min_events` | int | 50 | Skip repos with fewer ledger events than this (defends against overfitting). |
| `explain` | bool | `false` | Include per-signal correlations (mean confidence with/without the semantic channel) in the response. |

Reads the persisted `ranking_events` ledger, splits events by
`semantic_used`, and proposes a `±0.05` step on `semantic_weight` when
the mean confidence delta between groups exceeds 0.05. Bounded —
`semantic_weight` clamps to `[0.1, 0.8]`. `search_symbols` consults the
resulting override at query time when the caller leaves `semantic_weight`
at the default 0.5.

---

#### `check_embedding_drift` — Embedding canary (v1.80.0+)

Inputs:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `capture` | bool | `false` | Pin a fresh canary instead of running the drift check. No-op when one already exists unless `force=true`. |
| `force` | bool | `false` | Re-pin the canary before checking (use after intentional provider/model upgrades). |
| `threshold` | number | `0.05` | Cosine-distance ceiling above which the alarm fires (per-canary maximum, not mean). |

Pins (or re-checks) a 16-string deterministic canary against the active
embedding provider. Stores `~/.code-index/embed_canary.json` with the
captured vectors, then on each subsequent call re-embeds and compares
cosine similarity. Catches silent provider model changes (Gemini revs,
OpenAI weight updates, bundled-ONNX swaps) that quietly degrade hybrid
retrieval.

---

## Data Models

### Symbol

```python
@dataclass
class Symbol:
    id: str
    file: str
    name: str
    qualified_name: str
    kind: str
    language: str
    signature: str
    content_hash: str = ""
    docstring: str = ""
    summary: str = ""
    decorators: list[str]
    keywords: list[str]
    parent: str | None
    line: int = 0
    end_line: int = 0
    byte_offset: int = 0
    byte_length: int = 0
    ecosystem_context: str = ""
```

### Symbol field semantics

* **`id`**: stable identifier of the form `{file_path}::{qualified_name}#{kind}`
* **`file`**: relative file path within the indexed repository
* **`name`**: local symbol name
* **`qualified_name`**: dotted or container-qualified path including parent context
* **`kind`**: normalized symbol category such as function, class, method, constant, or type
* **`language`**: normalized language label
* **`signature`**: signature line or equivalent declaration text
* **`content_hash`**: SHA-256 hash of the source bytes used for drift detection and diffing
* **`docstring`**: docstring or nearest available inline documentation when extracted
* **`summary`**: condensed human- or model-consumable description
* **`decorators`**: decorators, attributes, annotations, or equivalent modifiers
* **`keywords`**: auxiliary search keywords
* **`parent`**: parent symbol ID where applicable
* **`line` / `end_line`**: 1-indexed line span within the original file
* **`byte_offset` / `byte_length`**: exact byte range in the cached raw file
* **`ecosystem_context`**: provider-derived business or ecosystem metadata

---

### CodeIndex

```python
@dataclass
class CodeIndex:
    repo: str
    owner: str
    name: str
    indexed_at: str
    index_version: int
    source_files: list[str]
    languages: dict[str, int]
    symbols: list[dict]
    file_hashes: dict[str, str]
    git_head: str
    source_root: str
    file_languages: dict[str, str]
    display_name: str
```

### CodeIndex field semantics

* **`repo`**: canonical repository identifier used by the local store
* **`owner` / `name`**: remote repository components where applicable
* **`indexed_at`**: ISO timestamp of index creation
* **`index_version`**: schema version for compatibility control
* **`source_files`**: included files after filtering
* **`languages`**: file-count distribution by language
* **`symbols`**: serialized symbol records, excluding raw source payloads
* **`file_hashes`**: file-level hashes used for incremental indexing
* **`git_head`**: repository revision marker where available
* **`source_root`**: absolute local source root for local indexes
* **`file_languages`**: language per file mapping
* **`display_name`**: friendly display label for local repository identities

---

## Repository Acquisition and File Discovery

### GitHub Repositories

GitHub repositories are typically enumerated through a single recursive tree request:

```text
GET /repos/{owner}/{repo}/git/trees/HEAD?recursive=1
```

File candidates are then filtered through the same safety and eligibility pipeline used for local folders before content is fetched and parsed.

### Local Folders

Local folders are discovered through recursive directory walking with path safety, ignore handling, secret exclusion, and binary detection.

### Filtering Pipeline

Both remote and local acquisition paths flow through the same conceptual filtering pipeline:

1. **Extension filter**
   File extension must map to a supported language or file type.

2. **Skip patterns**
   Excludes directories and files such as `node_modules/`, `vendor/`, `.git/`, build artifacts, lock files, minified assets, and other low-value or generated content.

3. **`.gitignore` handling**
   Ignore semantics are respected through pathspec-based matching where applicable.

4. **Secret detection**
   Files such as `.env`, `*.pem`, `*.key`, `*.p12`, and similar credential-bearing artifacts are excluded.

5. **Binary detection**
   Uses extension-based heuristics together with null-byte or content-based detection.

6. **Size limit**
   Files exceeding configured size bounds are skipped.

7. **File count limit**
   Indexing is capped by a configurable file-count limit, with priority typically given to high-value source directories before lower-priority remainder paths.

---

## Indexing Semantics

The indexing process performs the following conceptual stages:

1. discover candidate files
2. apply security and filtering rules
3. detect language support
4. parse source into AST form
5. extract normalized symbol records
6. post-process overloads and hashes
7. enrich symbols through context providers where available
8. generate summaries
9. persist index metadata and raw file cache

### Incremental indexing

Incremental indexing avoids reprocessing unchanged files by comparing stored file hashes and repository metadata.

Key behaviors:

* **Mtime fast-path**: files whose mtime is unchanged since the last index are skipped without reading content
* **Hash comparison**: files with changed mtimes are hashed; if the hash matches the stored value, parsing is skipped
* **Watcher fast-path**: when the watcher provides a pre-known change set via `changed_paths`, full directory discovery is skipped entirely — only the affected files are processed
* **Memory hash cache**: the watcher supplies `old_hash` from its in-memory cache, allowing `index_folder` to skip loading the stored index from SQLite
* **Git tree SHA short-circuiting**: unchanged remote indexes can be detected via the tree SHA
* **Cross-process locking**: file locks prevent concurrent index corruption
* **Atomic writes**: index updates use atomic write patterns to prevent partial-write corruption

---

## Watcher and Index Freshness

### Watcher architecture

The file watcher monitors local folders for changes and triggers incremental reindexing automatically. It runs as a background task in the same event loop as the MCP server (when `--watcher` is enabled) or as a standalone process via the `watch` subcommand.

**Key components:**

* **Debounce**: filesystem events are debounced (default 2000ms, configurable via `JCODEMUNCH_WATCH_DEBOUNCE_MS`) before triggering a reindex
* **Memory hash cache**: the watcher maintains an in-memory `dict[str, str]` mapping `rel_path → content_hash`. On each debounce tick, the watcher compares incoming file hashes against in-memory hashes rather than loading the full SQLite index (~57ms savings per reindex)
* **WatcherChange**: changed files are communicated to `index_folder` as `WatcherChange(change_type, abs_path, old_hash)` NamedTuples, where `old_hash` is provided from the memory cache
* **Fast path**: when `changed_paths` is provided, `index_folder` skips full directory discovery (~3s on Windows) and only processes affected files

### Deferred summarization

When AI summaries are enabled, the fast path splits the indexing pipeline into two phases:

1. **Immediate** (critical path): tree-sitter parse → `incremental_save` with empty summaries
2. **Deferred** (background daemon thread): AI summarization → second `incremental_save` to update summaries

A monotonic generation counter prevents stale deferred work from overwriting fresher data: the deferred thread captures the current generation before starting and checks it again before saving.

### Per-repo reindex state

Each repo tracked by the watcher has independent reindex state:

* `reindexing`: whether a reindex is actively in progress
* `stale_since`: monotonic timestamp when the index first became stale
* `consecutive_failures`: incremented on failure, reset on success
* `deferred_generation`: monotonic counter for deferred-work cancellation

State is managed through three lifecycle functions:

* `mark_reindex_start(repo)` — sets reindexing flag, clears event, records stale_since
* `mark_reindex_done(repo)` — clears reindexing, sets event, clears stale_since and failures
* `mark_reindex_failed(repo, error)` — clears reindexing, sets event (unblocks waiters), increments failures, preserves stale_since

### Freshness modes

The server supports two freshness modes, controlled by `freshness_mode` config key (or `--freshness-mode` CLI flag):

* **`relaxed`** (default): query tools return immediately regardless of reindex state.
* **`strict`**: all read query tools automatically wait up to 500ms for any in-progress reindex to complete before returning. Write tools (`index_folder`, `index_repo`, `index_file`, `invalidate_cache`) and utility tools (`list_repos`, `get_session_stats`) are excluded from the wait.

The strict-mode wait uses `threading.Event.wait()` dispatched via `asyncio.to_thread` to avoid blocking the event loop.

---

## Search and Ranking Semantics

### Symbol search model

Symbol search combines multiple ranking signals, including:

* exact name matches
* substring matches
* token overlap
* signature terms
* summary terms
* docstring and keyword relevance
* structure-derived signals such as file centrality

The search model is therefore hybrid rather than purely lexical.

### Query-shape exact seeding

In `get_ranked_context`'s default path, source-shaped tokens in the query (qualified names, CamelCase, snake_case, dunders; filename look-alikes excluded) pin exact symbol-name matches ahead of the lexical ranking, bounded at 3 seeds per token and 5 total. Seeded items carry `match_channel: "exact_name"`, and `_meta.query_shape` reports what was recognized and seeded. An exact seed floors the verdict's best-score so a seeded response is never misreported as negative evidence.

### Bounded result handling

When only top-k results are required, the system may use bounded heap strategies rather than full sorting over all candidates.

### Text search model

Text search is file-content oriented and is intended for cases where the desired material is not naturally represented as a symbol. Matching is case-insensitive and may return surrounding context lines.

---

## Retrieval Semantics

### Byte-offset retrieval

`get_symbol_source` and related retrieval tools access cached raw file content by stored byte offsets and lengths. This avoids reparsing or rescanning full files and preserves exact source fidelity.

### Verification

When `verify` is requested, the retrieved content is re-hashed and compared to the stored `content_hash`. Verification status is surfaced through `_meta`.

### Batch retrieval

`get_symbol_source` accepts either `symbol_id` (string, returns flat object) or `symbol_ids` (array, returns `{symbols, errors}`). This reduces overhead for workflows that require several related definitions simultaneously.

### Contextual retrieval

`get_context_bundle` is intended to package useful surrounding material while preserving a bounded payload. This capability is designed to reduce repeated tool orchestration for common reasoning workflows.

---

## Response Envelope

All tool responses return an `_meta` object containing operational metadata.

Representative envelope:

```json
{
  "_meta": {
    "powered_by": "jcodemunch-mcp by jgravelle · https://github.com/jgravelle/jcodemunch-mcp",
    "timing_ms": 42,
    "repo": "owner/repo",
    "symbol_count": 387,
    "truncated": false,
    "content_verified": true,
    "tokens_saved": 2450,
    "total_tokens_saved": 184320,
    "estimate_method": "...",
  }
}
```

### Common `_meta` fields

* **`timing_ms`**: elapsed execution time in milliseconds
* **`repo`**: repository identifier
* **`symbol_count`**: symbol count where relevant to the operation
* **`truncated`**: whether the result was truncated or bounded
* **`content_verified`**: whether verification succeeded when requested
* **`tokens_saved`**: per-call token-savings estimate
* **`total_tokens_saved`**: cumulative saved-token estimate across calls
* **`estimate_method`**: label describing how the savings estimate was computed

The exact `_meta` shape may vary by tool, but the response contract emphasizes explicit operational metadata rather than opaque output.

### Honesty and provenance fields

Retrieval responses additionally carry machine-readable trust signals:

* **`_meta.confidence`**: calibrated 0–1 score (top-1/top-2 gap, top-1 strength, identity-match presence, freshness).
* **`_freshness`** per result entry (`fresh` / `edited_uncommitted` / `stale_index`) plus a `_meta.freshness` summary.
* **`negative_evidence`**: the retrieval verdict on empty or weak results, with `state` ∈ `ok` / `low_confidence` / `absent` / `degraded` and scan counts. `absent` asserts the corpus was scanned and the answer is not present; `degraded` asserts the index is impaired and a missing result may be truncation rather than absence.
* **`_meta.query_shape`** (`get_ranked_context`): which query tokens were recognized as source-shaped identifiers and how many exact-name symbols were seeded ahead of the ranking.
* **`_meta.confidence_provenance`** (`find_implementations`): per-channel basis (`declared` / `measured`), with `measured_ref` (precision/recall on the gold corpus) where available. A prior is never presented as a measurement.
* **`savings_provenance`** (`get_session_stats`, `receipt --export json`): the measured artifacts behind savings/quality claims plus the declared-vs-measured rule.

These contracts are published as JSON Schemas in `schemas/` (`retrieval-verdict.schema.json`, `confidence-provenance.schema.json`, `ranked-context-response.schema.json`); conforming clients may validate responses against them mechanically.

### Tool annotations

Every retrieval tool declares MCP `readOnlyHint: true`. Tools that reach the network or write index state (`index_repo`, `index_folder`, `index_file`, `summarize_repo`, `embed_repo`, and similar) declare `openWorldHint: true`. The annotations are part of the tested contract: hosts may gate approval flows on them.

---

## Error Handling

Errors return a structured object containing a human-readable message and minimal timing metadata.

Representative shape:

```json
{
  "error": "Human-readable message",
  "_meta": {
    "timing_ms": 1
  }
}
```

### Expected error behaviors

| Scenario                    | Behavior                                                           |
| --------------------------- | ------------------------------------------------------------------ |
| Repository not found        | returns an error message                                           |
| GitHub rate limited         | returns an error with reset guidance and recommends `GITHUB_TOKEN` |
| Individual file fetch fails | file is skipped; indexing continues                                |
| Individual file parse fails | file is skipped; indexing continues                                |
| No source files found       | returns an error except for deletion-only incremental refreshes described below |
| Symbol ID not found         | returns an error or per-item error entry                           |
| Repository not indexed      | returns an error suggesting indexing first                         |
| AI summarization fails      | falls back to docstring or signature                               |
| Index version mismatch      | old index is ignored; reindex required                             |

The error model is designed so that partial failures during indexing do not necessarily abort the entire operation.

For `index_folder`, a full incremental scan of an existing index that finds no
eligible source files reconciles deletions, including removal of every indexed
file. Initial indexing and non-incremental indexing of an empty folder still
return an error. Directory traversal failures abort discovery; an empty full
scan with unreadable files or file-limit truncation fails without clearing the
persisted index. Legitimate binary exclusions do not count as read failures.
Explicit-path incremental refreshes also reconcile deleted requested files.

---

## Transport Modes and CLI

### Subcommands

| Subcommand    | Purpose |
| ------------- | ------- |
| `serve`       | Run the MCP server (default when no subcommand given) |
| `watch`       | Watch folders for changes and auto-reindex (standalone) |
| `watch-claude`| Auto-discover and watch Claude Code worktrees |
| `hook-event`  | Record a worktree lifecycle event (used by hooks) |

### Transport modes (`serve`)

The `serve` subcommand supports three transport modes via `--transport` or `JCODEMUNCH_TRANSPORT`:

* **`stdio`** (default): standard input/output, suitable for MCP clients that launch the server as a subprocess
* **`sse`**: HTTP with Server-Sent Events, for persistent connections
* **`streamable-http`**: HTTP with streamable response bodies

HTTP transports bind to `--host` / `--port` (defaults: `127.0.0.1:8901`) and support optional bearer token authentication via `JCODEMUNCH_HTTP_TOKEN`.

### Serve flags

| Flag | Purpose |
| ---- | ------- |
| `--transport {stdio,sse,streamable-http}` | Transport mode |
| `--host HOST` | HTTP bind address |
| `--port PORT` | HTTP listen port |
| `--watcher[=BOOL]` | Enable background file watcher |
| `--watcher-path PATH [PATH...]` | Folders to watch (default: cwd) |
| `--watcher-debounce MS` | Debounce interval in ms |
| `--watcher-idle-timeout MINUTES` | Auto-stop after N idle minutes |
| `--watcher-no-ai-summaries` | Disable AI summaries for watcher |
| `--watcher-log [PATH]` | Log watcher output to file |
| `--freshness-mode {relaxed,strict}` | Freshness mode for query tools |

---

## Environment Variables

| Variable                          | Purpose                                                              | Required |
| --------------------------------- | -------------------------------------------------------------------- | -------- |
| `GITHUB_TOKEN`                    | GitHub API authentication, higher limits, private repository support | No       |
| `ANTHROPIC_API_KEY`               | enables Anthropic-based summaries                                    | No       |
| `ANTHROPIC_MODEL`                 | overrides the Anthropic summary model                                | No       |
| `GOOGLE_API_KEY`                  | enables Gemini-based summaries when Anthropic is not configured      | No       |
| `GOOGLE_MODEL`                    | overrides the Gemini summary model                                   | No       |
| `OPENAI_API_BASE`                 | enables local or remote OpenAI-compatible summary backends           | No       |
| `OPENAI_MODEL`                    | model name for OpenAI-compatible summary backends                    | No       |
| `OPENAI_API_KEY`                  | authentication for OpenAI-compatible summary backends                | No       |
| `OPENAI_CONCURRENCY`              | concurrency control for summary batching                             | No       |
| `OPENAI_BATCH_SIZE`               | batch sizing for OpenAI-compatible summarization                     | No       |
| `OPENAI_MAX_TOKENS`               | max output tokens for compatible summarizers                         | No       |
| `CODE_INDEX_PATH`                 | custom storage path                                                  | No       |
| `JCODEMUNCH_CONTEXT_PROVIDERS`    | enables or disables provider enrichment                              | No       |
| `JCODEMUNCH_MAX_INDEX_FILES`      | overrides the default file-count limit                               | No       |
| `JCODEMUNCH_LOG_FILE`             | directs logging to file instead of stderr in stdio sessions          | No       |
| `JCODEMUNCH_SHARE_SAVINGS`        | enables or disables community savings reporting                      | No       |
| `JCODEMUNCH_PERF_TELEMETRY`       | set `1` to persist per-tool latency + ranking-event rows to `~/.code-index/telemetry.db`. In-memory ring is always tracked; this controls only durable persistence. | No |
| `JCODEMUNCH_PERF_TELEMETRY_MAX_ROWS` | rolling cap on persisted perf rows (default 100000); oldest trimmed in 1k-row batches | No |
| `JCODEMUNCH_PATH_MAP`             | remaps stored path prefixes at retrieval time; format: `orig1=new1,orig2=new2` — allows an index built on one machine (e.g. Linux `/home/user`) to be reused on another (e.g. Windows `C:\Users\user`) without re-indexing. Each pair is split on the **last** `=`, so `=` signs within path components are preserved. Pairs are comma-separated; path components containing commas are not supported. First matching prefix wins. | No       |
| `JCODEMUNCH_REDACT_SOURCE_ROOT`   | redacts absolute path details from output                            | No       |
| `JCODEMUNCH_TRANSPORT`            | transport mode: `stdio`, `sse`, or `streamable-http`                | No       |
| `JCODEMUNCH_HOST`                 | HTTP bind address (default `127.0.0.1`)                             | No       |
| `JCODEMUNCH_PORT`                 | HTTP listen port (default `8901`)                                   | No       |
| `JCODEMUNCH_HTTP_TOKEN`           | bearer token for HTTP transport authentication                      | No       |
| `JCODEMUNCH_FRESHNESS_MODE`       | freshness mode: `relaxed` (default) or `strict`                     | No       |
| `JCODEMUNCH_WATCH_DEBOUNCE_MS`    | watcher debounce interval in ms (default `2000`)                    | No       |
| `JCODEMUNCH_USE_AI_SUMMARIES`     | default for `use_ai_summaries` flag (`true`/`false`)                | No       |
| `JCODEMUNCH_CLAUDE_POLL_INTERVAL` | poll interval in seconds for `watch-claude` git polling             | No       |

---

## Security and Safety Controls

The specification assumes the following security controls are part of compliant operation:

* path traversal prevention
* symlink escape protection
* secret-file exclusion
* binary file exclusion
* safe encoding handling
* `.gitignore` respect where appropriate
* SSRF prevention for configurable API base URLs
* ReDoS protection in text search
* safe temporary-file behavior
* optional HTTP bearer authentication for HTTP transport (via `JCODEMUNCH_HTTP_TOKEN`)
* source-root redaction when configured

These protections apply to repository discovery, file loading, search, retrieval, and optional external-summary integrations.

---

## Performance and Operational Notes

### Local-first persistence

Indexes and raw file caches are stored locally to make repeat search and retrieval fast and to avoid redundant remote fetches.

### Sidecars and metadata shortcuts

Metadata sidecars may be used so repo-listing operations do not require loading full index payloads.

### Cache behavior

The store may use LRU-like caching and mtime invalidation to reduce repeated disk and parse costs.

### File locking

Cross-process locking is used to reduce the risk of index corruption under concurrent access.

### Watch mode

The file watcher monitors directories and triggers incremental reindexing automatically. It can run embedded in the `serve` process (via `--watcher`) or standalone (via the `watch` subcommand).

Key optimizations:

* **Memory hash cache**: avoids loading the full SQLite index on each debounce tick (~57ms savings)
* **Watcher fast path**: when the watcher knows the exact changed files, `index_folder` skips full directory discovery (~3s → ~50ms on Windows)
* **Deferred summarization**: AI summaries are computed in a background thread, so the index is available immediately with empty summaries that are filled in asynchronously
* **Per-repo backpressure**: each watched repo has independent reindex state with `threading.Event`-based signaling; in strict freshness mode, query tools automatically wait for reindex completion

The `watch-claude` variant extends this for Claude Code specifically: it discovers worktrees via hook-driven events (`WorktreeCreate`/`WorktreeRemove` writing to a JSONL manifest) and/or by polling `git worktree list` on specified repositories. Both mechanisms are cross-platform and layout-agnostic.

---

## Token Savings Semantics

jCodeMunch reports token savings as an operational estimate.

### Conceptual basis

Savings are derived from the difference between a larger baseline payload, such as raw file content or broader retrieval, and the smaller actual response returned by the tool.

### Reporting fields

* `tokens_saved` refers to the current call
* `total_tokens_saved` refers to the cumulative persisted total
* `estimate_method` indicates how the figure was calculated

### Conservatism guarantees

Every known error term in the meter points down: the byte-to-token conversion (~4 bytes/token) undercounts denser code, each matched file is credited once per call regardless of matched symbols, and empty or negative results credit zero (the counter clamps at zero). Cache hits record the same savings as the miss that populated them — a repeat query avoids the same raw reads. Savings are stored as tokens, never as currency; monetary valuations are applied at display time at current published input-token rates. The lifetime meter persists in `~/.code-index/_savings.json` (with per-day buckets), and the `receipt` CLI's JSON export carries a `savings_provenance` block chaining the figures to the committed measurement artifacts.

### Important interpretation note

Savings are strongest when clients use structured retrieval instead of brute-force file reading. Installing the system alone does not guarantee savings unless the client actually uses the tool surface for code lookup and navigation.

---

## Compatibility and Evolution Notes

This specification describes the current contract and intended behavior at a high level. The following evolution principles apply:

1. tool capabilities may expand over time
2. ranking internals may change without altering the conceptual contract
3. data-model fields may grow as compatibility permits
4. index-version changes may require reindexing
5. optional integrations and providers may broaden without changing the core retrieval model

The stable foundation of the specification is:

* repository or folder indexing
* structured symbol extraction
* local persistence of metadata and raw source
* search and retrieval through explicit tools
* operational metadata through `_meta`
* bounded, deterministic access to code for AI-assisted workflows
