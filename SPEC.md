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
* [Data Models](#data-models)

  * [Symbol](#symbol)
  * [CodeIndex](#codeindex)
* [Repository Acquisition and File Discovery](#repository-acquisition-and-file-discovery)

  * [GitHub Repositories](#github-repositories)
  * [Local Folders](#local-folders)
  * [Filtering Pipeline](#filtering-pipeline)
* [Indexing Semantics](#indexing-semantics)
* [Search and Ranking Semantics](#search-and-ranking-semantics)
* [Retrieval Semantics](#retrieval-semantics)
* [Response Envelope](#response-envelope)
* [Error Handling](#error-handling)
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

#### `get_symbol_diff` — Compare indexed symbol states across snapshots

```json
{
  "repo": "owner/repo",
  "symbol_id": "src/core.py::process_order#function"
}
```

Reports added, removed, or changed symbols by comparing indexed snapshots using `(name, kind)` and `content_hash`.

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

Enhancements may include:

* Git tree SHA short-circuiting for unchanged remote indexes
* watch-triggered incremental runs
* atomic writes
* cross-process locking
* integrity verification sidecars

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
    "timing_ms": 42,
    "repo": "owner/repo",
    "symbol_count": 387,
    "truncated": false,
    "content_verified": true,
    "tokens_saved": 2450,
    "total_tokens_saved": 184320,
    "estimate_method": "..."
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

## Environment Variables

| Variable                        | Purpose                                                              | Required |
| ------------------------------- | -------------------------------------------------------------------- | -------- |
| `GITHUB_TOKEN`                  | GitHub API authentication, higher limits, private repository support | No       |
| `ANTHROPIC_API_KEY`             | enables Anthropic-based summaries                                    | No       |
| `ANTHROPIC_MODEL`               | overrides the Anthropic summary model                                | No       |
| `GOOGLE_API_KEY`                | enables Gemini-based summaries when Anthropic is not configured      | No       |
| `GOOGLE_MODEL`                  | overrides the Gemini summary model                                   | No       |
| `OPENAI_API_BASE`               | enables local or remote OpenAI-compatible summary backends           | No       |
| `OPENAI_MODEL`                  | model name for OpenAI-compatible summary backends                    | No       |
| `OPENAI_API_KEY`                | authentication for OpenAI-compatible summary backends                | No       |
| `OPENAI_CONCURRENCY`            | concurrency control for summary batching                             | No       |
| `OPENAI_BATCH_SIZE`             | batch sizing for OpenAI-compatible summarization                     | No       |
| `OPENAI_MAX_TOKENS`             | max output tokens for compatible summarizers                         | No       |
| `CODE_INDEX_PATH`               | custom storage path                                                  | No       |
| `JCODEMUNCH_CONTEXT_PROVIDERS`  | enables or disables provider enrichment                              | No       |
| `JCODEMUNCH_MAX_INDEX_FILES`    | overrides the default file-count limit                               | No       |
| `JCODEMUNCH_LOG_FILE`           | directs logging to file instead of stderr in stdio sessions          | No       |
| `JCODEMUNCH_SHARE_SAVINGS`      | enables or disables community savings reporting                      | No       |
| `JCODEMUNCH_REDACT_SOURCE_ROOT` | redacts absolute path details from output                            | No       |

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
* optional HTTP bearer authentication for HTTP transport
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

A watch-oriented interface may monitor directories and trigger incremental reindexing automatically.

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
