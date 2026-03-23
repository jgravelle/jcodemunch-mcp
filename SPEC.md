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
* intended as a lightweight inspection tool before `get_symbol` or `get_symbols`

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

Returns repository-level summary information such as file counts by directory, language breakdown, and symbol-kind distribution.

**Behavioral notes:**

* lighter than `get_file_tree`
* intended as a coarse-grained entry point for unfamiliar repositories

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

#### `get_symbol` — Get full source of a symbol

```json
{
  "repo": "owner/repo",
  "symbol_id": "src/main.py::MyClass.login#method",
  "verify": true,
  "context_lines": 3
}
```

Retrieves the source for a single symbol using its stored byte offset and byte length.

**Behavioral notes:**

* retrieval is based on cached raw file content, not reparsing
* `verify` re-hashes the retrieved source and compares it with the stored `content_hash`
* `context_lines` optionally adds surrounding lines for limited context
* returns verification state in `_meta` when verification is requested

---

#### `get_symbols` — Batch retrieve multiple symbols

```json
{
  "repo": "owner/repo",
  "symbol_ids": ["id1", "id2", "id3"]
}
```

Returns a list of resolved symbol payloads together with per-ID errors where applicable.

**Behavioral notes:**

* intended to reduce multi-call overhead when several related symbols are needed
* missing symbols are reported without causing unrelated successful lookups to fail

---

#### `get_context_bundle` — Retrieve a bounded contextual package around a symbol or symbol set

```json
{
  "repo": "owner/repo",
  "symbol_id": "src/auth.py::AuthService.login#method"
}
```

Returns a bounded retrieval package designed to support downstream reasoning tasks without requiring a series of separate calls.

**Behavioral notes:**

* may include the target symbol, related imports, neighboring items, or related symbols
* later variants may support multi-symbol bundles, deduplicated imports, optional callers, and alternative output formatting
* intended to reduce tool-call thrash while preserving bounded context

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
  "max_results": 10
}
```

Searches the symbol index using a structured ranking pipeline.

**Behavioral notes:**

* all filters are optional
* supports narrowing by symbol kind, language, and file path pattern
* ranking uses multiple lexical and metadata signals rather than a single naive match rule
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
  "symbol_id": "src/core.py::process_order#function"
}
```

Estimates likely impact by traversing reverse import relationships and inspecting relevant importers.

**Behavioral notes:**

* may distinguish confirmed from potential impact where enough evidence exists
* intended for change-planning and refactoring workflows

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

### Freshness and Backpressure

#### `wait_for_fresh` — Wait for watcher reindex to complete

```json
{
  "repo": "local/project-abc123",
  "timeout_ms": 500
}
```

Blocks until the watcher's in-progress reindex completes for the specified repo, or until timeout.

**Return shapes:**

* Already fresh: `{"fresh": true, "waited_ms": 0}`
* Waited and got fresh: `{"fresh": true, "waited_ms": 123}`
* Timeout: `{"fresh": false, "waited_ms": 500, "reason": "timeout"}`
* Persistent failure (2nd+ consecutive): `{"fresh": false, "waited_ms": 0, "reason": "reindex_failed", "reindex_error": "...", "reindex_failures": 3}`

**Behavioral notes:**

* intended for multi-agent workflows where an agent writes files and needs to read fresh symbols immediately
* uses `threading.Event.wait()` internally — dispatched via `asyncio.to_thread` in `call_tool`
* in strict freshness mode, all read query tools automatically wait before returning (this tool is not needed in that mode)

---

### Observability

#### `get_session_stats` — Token savings statistics

No input required.

Returns per-session and all-time token savings statistics, per-tool breakdown, session duration, and cost-avoidance estimates across model price points.

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

### Staleness signaling via `_meta`

Every query response includes staleness fields in `_meta`:

```json
{
  "_meta": {
    "index_stale": true,
    "reindex_in_progress": true,
    "stale_since_ms": 47
  }
}
```

* `index_stale`: true when the repo is actively reindexing or the previous reindex failed (stale_since is set)
* `reindex_in_progress`: true only while actively reindexing
* `stale_since_ms`: milliseconds since the index first became stale, or null

**Failure escalation:** on the 1st consecutive failure, only `index_stale: true` is set (transient tolerance). On the 2nd+ consecutive failure, `reindex_error` and `reindex_failures` are added to `_meta`.

### Freshness modes

The server supports two freshness modes, controlled by `--freshness-mode` or `JCODEMUNCH_FRESHNESS_MODE`:

* **`relaxed`** (default): query tools return immediately regardless of reindex state. Agents can inspect `_meta` staleness fields or call `wait_for_fresh` explicitly.
* **`strict`**: all read query tools automatically wait up to 500ms for any in-progress reindex to complete before returning. Write tools (`index_folder`, `index_repo`, `index_file`, `invalidate_cache`) and utility tools (`list_repos`, `get_session_stats`, `wait_for_fresh`) are excluded from the wait.

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

### Bounded result handling

When only top-k results are required, the system may use bounded heap strategies rather than full sorting over all candidates.

### Text search model

Text search is file-content oriented and is intended for cases where the desired material is not naturally represented as a symbol. Matching is case-insensitive and may return surrounding context lines.

---

## Retrieval Semantics

### Byte-offset retrieval

`get_symbol` and related retrieval tools access cached raw file content by stored byte offsets and lengths. This avoids reparsing or rescanning full files and preserves exact source fidelity.

### Verification

When `verify` is requested, the retrieved content is re-hashed and compared to the stored `content_hash`. Verification status is surfaced through `_meta`.

### Batch retrieval

`get_symbols` allows multiple known symbol IDs to be retrieved in one call. This reduces overhead for workflows that require several related definitions simultaneously.

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
    "index_stale": false,
    "reindex_in_progress": false,
    "stale_since_ms": null
  }
}
```

### Common `_meta` fields

* **`powered_by`**: attribution string, always present
* **`timing_ms`**: elapsed execution time in milliseconds
* **`repo`**: repository identifier
* **`symbol_count`**: symbol count where relevant to the operation
* **`truncated`**: whether the result was truncated or bounded
* **`content_verified`**: whether verification succeeded when requested
* **`tokens_saved`**: per-call token-savings estimate
* **`total_tokens_saved`**: cumulative saved-token estimate across calls
* **`estimate_method`**: label describing how the savings estimate was computed

### Staleness `_meta` fields

Present on all query responses when a watcher is active:

* **`index_stale`**: whether the repo's index is stale (reindexing or failed)
* **`reindex_in_progress`**: whether the repo is actively reindexing right now
* **`stale_since_ms`**: milliseconds since the index first became stale, or null
* **`reindex_error`**: error message (only on 2nd+ consecutive failure)
* **`reindex_failures`**: failure count (only on 2nd+ consecutive failure)

For tools without a `repo` parameter (except excluded tools like `list_repos` and `get_session_stats`), global staleness is reported: `index_stale` and `reindex_in_progress` reflect whether *any* repo is currently reindexing.

The exact `_meta` shape may vary by tool, but the response contract emphasizes explicit operational metadata rather than opaque output.

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
| No source files found       | returns an error                                                   |
| Symbol ID not found         | returns an error or per-item error entry                           |
| Repository not indexed      | returns an error suggesting indexing first                         |
| AI summarization fails      | falls back to docstring or signature                               |
| Index version mismatch      | old index is ignored; reindex required                             |

The error model is designed so that partial failures during indexing do not necessarily abort the entire operation.

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
* **Per-repo backpressure**: each watched repo has independent reindex state with `threading.Event`-based signaling, enabling agents to wait for freshness via `wait_for_fresh` or automatic strict-mode waits

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
