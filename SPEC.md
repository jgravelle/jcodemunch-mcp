# jcodemunch-mcp — Technical Specification

> Token-efficient MCP server for GitHub source code exploration via tree-sitter AST parsing.

## 1. Project Overview

### Problem
When an AI assistant needs to understand a GitHub repository's source code, the naive approach dumps entire files into context. A 10,000-line codebase consumes ~40,000 tokens just to read. Most of those tokens are function bodies the assistant never needs.

### Solution
**jcodemunch-mcp** pre-indexes repository source code using tree-sitter AST parsing, extracting a structured catalog of every symbol (function, class, method, constant, type). Each symbol stores only its **signature + one-line summary**, with the ability to retrieve full source on demand.

### Token Savings
| Scenario | Raw dump | codemunch | Savings |
|----------|----------|-----------|---------|
| Explore 500-file repo structure | ~200,000 tokens | ~2,000 tokens (outline) | 99% |
| Find a specific function | ~40,000 tokens (whole file) | ~200 tokens (search + signature) | 99.5% |
| Read one function body | ~40,000 tokens (whole file) | ~500 tokens (just that symbol) | 98.7% |
| Understand module API | ~15,000 tokens (whole file) | ~800 tokens (outline + summaries) | 94.7% |

**Target: 97%+ average token savings across typical code exploration workflows.**

### Relationship to github-docs-mcp
This is a sibling project. `github-docs-mcp` indexes **documentation** (Markdown files) using section-based parsing. `jcodemunch-mcp` indexes **source code** using AST-based parsing. They share architectural patterns but have completely independent codebases and storage.

| Concept | docs-mcp | codemunch-mcp |
|---------|----------|---------------|
| Unit of content | Section (Markdown heading) | Symbol (function, class, etc.) |
| Parser | Regex-based Markdown splitter | tree-sitter AST extraction |
| Storage | `~/.doc-index/` | `~/.code-index/` |
| Hierarchy | Heading depth (H1-H6) | File > Class > Method |
| Summary source | AI or first line | Docstring > AI > Signature fallback |

---

## 2. MCP Tools

### 2.1 `index_repo` — Index a repository's source code

**Purpose**: Fetch source files from GitHub, parse ASTs, extract symbols, generate summaries, save index.

**Input Schema**:
```json
{
  "type": "object",
  "properties": {
    "url": {
      "type": "string",
      "description": "GitHub repository URL or owner/repo string"
    },
    "use_ai_summaries": {
      "type": "boolean",
      "description": "Use AI to generate symbol summaries (requires ANTHROPIC_API_KEY). When false, uses docstrings or signature fallback.",
      "default": true
    }
  },
  "required": ["url"]
}
```

**Output**:
```json
{
  "success": true,
  "repo": "owner/repo",
  "indexed_at": "2025-01-15T10:30:00",
  "file_count": 42,
  "symbol_count": 387,
  "languages": {"python": 20, "typescript": 15, "go": 7},
  "files": ["src/main.py", "src/utils.py", "..."]
}
```

**Token cost**: ~200 tokens (metadata only, no content returned).

**Behavior**:
1. Parse GitHub URL to extract `owner/repo`.
2. Call `GET /repos/{owner}/{repo}/git/trees/HEAD?recursive=1` (single API call, returns all paths).
3. Filter to supported source files using extension mapping + skip list + .gitignore.
4. Fetch each file's raw content via GitHub raw content API.
5. Parse each file with tree-sitter (language auto-detected from extension).
6. Extract symbols: functions, classes, methods, constants, type definitions.
7. For each symbol, extract signature, docstring, decorators, byte offsets.
8. Generate summaries (three-tier: docstring > AI batch > signature fallback).
9. Save index JSON + raw files to `~/.code-index/{owner}-{repo}/`.

---

### 2.2 `list_repos` — List indexed repositories

**Purpose**: Show what repositories are available for querying.

**Input Schema**:
```json
{
  "type": "object",
  "properties": {}
}
```

**Output**:
```json
{
  "count": 3,
  "repos": [
    {
      "repo": "owner/repo",
      "indexed_at": "2025-01-15T10:30:00",
      "symbol_count": 387,
      "file_count": 42,
      "languages": {"python": 20, "typescript": 15, "go": 7}
    }
  ]
}
```

**Token cost**: ~100-300 tokens depending on number of indexed repos.

---

### 2.3 `get_file_tree` — Get repository file structure

**Purpose**: Show the file tree of an indexed repository, optionally filtered by path prefix. This replaces `get_toc_tree` from docs-mcp.

**Input Schema**:
```json
{
  "type": "object",
  "properties": {
    "repo": {
      "type": "string",
      "description": "Repository identifier (owner/repo or just repo name)"
    },
    "path_prefix": {
      "type": "string",
      "description": "Optional path prefix to filter (e.g., 'src/utils')",
      "default": ""
    }
  },
  "required": ["repo"]
}
```

**Output**:
```json
{
  "repo": "owner/repo",
  "tree": [
    {
      "path": "src/",
      "type": "dir",
      "children": [
        {
          "path": "src/main.py",
          "type": "file",
          "language": "python",
          "symbol_count": 12,
          "line_count": 245
        }
      ]
    }
  ]
}
```

**Token cost**: ~500-2,000 tokens for a typical repo.

---

### 2.4 `get_file_outline` — Get symbols in a file

**Purpose**: List all symbols (functions, classes, methods) in a specific file with their signatures and one-line summaries. This is the primary navigation tool — use it to understand a file's API before loading full symbol source.

**Input Schema**:
```json
{
  "type": "object",
  "properties": {
    "repo": {
      "type": "string",
      "description": "Repository identifier (owner/repo or just repo name)"
    },
    "file_path": {
      "type": "string",
      "description": "Path to the file within the repository (e.g., 'src/main.py')"
    }
  },
  "required": ["repo", "file_path"]
}
```

**Output**:
```json
{
  "repo": "owner/repo",
  "file": "src/main.py",
  "language": "python",
  "line_count": 245,
  "symbols": [
    {
      "id": "src-main-py::MyClass",
      "kind": "class",
      "name": "MyClass",
      "signature": "class MyClass(BaseClass):",
      "summary": "Handles user authentication and session management.",
      "line": 15,
      "children": [
        {
          "id": "src-main-py::MyClass.login",
          "kind": "method",
          "name": "login",
          "signature": "def login(self, username: str, password: str) -> bool:",
          "summary": "Authenticate a user with username and password.",
          "line": 25
        }
      ]
    },
    {
      "id": "src-main-py::process_data",
      "kind": "function",
      "name": "process_data",
      "signature": "def process_data(input: dict, config: Config) -> Result:",
      "summary": "Transform raw input data according to config rules.",
      "line": 120
    }
  ]
}
```

**Token cost**: ~300-1,500 tokens per file.

---

### 2.5 `get_symbol` — Get full source of a symbol

**Purpose**: Retrieve the complete source code of a specific symbol. Use this after identifying relevant symbols via `get_file_outline` or `search_symbols`.

**Input Schema**:
```json
{
  "type": "object",
  "properties": {
    "repo": {
      "type": "string",
      "description": "Repository identifier (owner/repo or just repo name)"
    },
    "symbol_id": {
      "type": "string",
      "description": "Symbol ID from get_file_outline or search_symbols"
    }
  },
  "required": ["repo", "symbol_id"]
}
```

**Output**:
```json
{
  "id": "src-main-py::MyClass.login",
  "kind": "method",
  "name": "login",
  "file": "src/main.py",
  "line": 25,
  "end_line": 52,
  "signature": "def login(self, username: str, password: str) -> bool:",
  "decorators": ["@require_ssl"],
  "docstring": "Authenticate a user with username and password.\n\nArgs:\n    username: The user's login name\n    password: The user's password\n\nReturns:\n    True if authentication succeeds",
  "source": "    @require_ssl\n    def login(self, username: str, password: str) -> bool:\n        \"\"\"Authenticate a user...\"\"\"\n        ...(full source code)..."
}
```

**Token cost**: ~200-2,000 tokens (just the one symbol, not the whole file).

**Content retrieval**: Uses stored `byte_offset` and `byte_length` to read only the relevant bytes from the raw file on disk. No re-parsing needed.

---

### 2.6 `search_symbols` — Search across all symbols

**Purpose**: Search for symbols matching a query across the entire indexed repository. Returns matches with signatures and summaries but NOT full source code.

**Input Schema**:
```json
{
  "type": "object",
  "properties": {
    "repo": {
      "type": "string",
      "description": "Repository identifier (owner/repo or just repo name)"
    },
    "query": {
      "type": "string",
      "description": "Search query (matches symbol names, signatures, summaries, docstrings)"
    },
    "kind": {
      "type": "string",
      "description": "Optional filter by symbol kind",
      "enum": ["function", "class", "method", "constant", "type"]
    },
    "file_pattern": {
      "type": "string",
      "description": "Optional glob pattern to filter files (e.g., 'src/**/*.py')"
    },
    "max_results": {
      "type": "integer",
      "description": "Maximum number of results to return",
      "default": 10
    }
  },
  "required": ["repo", "query"]
}
```

**Output**:
```json
{
  "repo": "owner/repo",
  "query": "authenticate",
  "result_count": 3,
  "results": [
    {
      "id": "src-main-py::MyClass.login",
      "kind": "method",
      "name": "login",
      "file": "src/main.py",
      "line": 25,
      "signature": "def login(self, username: str, password: str) -> bool:",
      "summary": "Authenticate a user with username and password.",
      "score": 15
    }
  ]
}
```

**Token cost**: ~200-500 tokens for search results.

**Search algorithm** (see Section 5 for details):
- Weighted scoring across name, signature, summary, docstring, keywords
- Optional filters by symbol kind and file path pattern

---

### 2.7 `get_symbols` — Batch retrieve multiple symbols

**Purpose**: Retrieve full source code of multiple symbols in one call. Efficient for loading related symbols (e.g., all methods in a class).

**Input Schema**:
```json
{
  "type": "object",
  "properties": {
    "repo": {
      "type": "string",
      "description": "Repository identifier (owner/repo or just repo name)"
    },
    "symbol_ids": {
      "type": "array",
      "items": {"type": "string"},
      "description": "List of symbol IDs to retrieve"
    }
  },
  "required": ["repo", "symbol_ids"]
}
```

**Output**:
```json
{
  "symbols": [
    { "...same as get_symbol output..." }
  ],
  "errors": [
    {"id": "bad-id", "error": "Symbol not found: bad-id"}
  ]
}
```

**Token cost**: Sum of individual symbol sizes, typically 500-5,000 tokens.

---

## 3. Data Models

### 3.1 Symbol Dataclass

The core unit of indexed content. Replaces `Section` from docs-mcp.

```python
@dataclass
class Symbol:
    """A code symbol extracted from source via tree-sitter."""
    id: str                         # Unique ID: "file-path::QualifiedName"
    file: str                       # Source file path (e.g., "src/main.py")
    name: str                       # Symbol name (e.g., "login")
    qualified_name: str             # Fully qualified (e.g., "MyClass.login")
    kind: str                       # "function" | "class" | "method" | "constant" | "type"
    language: str                   # "python" | "javascript" | "typescript" | "go" | "rust" | "java"
    signature: str                  # Full signature line(s)
    docstring: str = ""             # Extracted docstring (language-specific)
    summary: str = ""               # One-line summary
    decorators: list[str] = field(default_factory=list)  # Decorators/attributes
    keywords: list[str] = field(default_factory=list)    # Extracted search keywords
    parent: Optional[str] = None    # Parent symbol ID (for methods -> class)
    line: int = 0                   # Start line number (1-indexed)
    end_line: int = 0               # End line number (1-indexed)
    byte_offset: int = 0           # Start byte in raw file
    byte_length: int = 0           # Byte length of full source
```

**Symbol ID format**: `{file_slug}::{qualified_name}`
- File slug: file path with `/` replaced by `-` and `.` replaced by `-` (e.g., `src-main-py`)
- Qualified name: dot-separated for nesting (e.g., `MyClass.login`)
- Examples: `src-main-py::process_data`, `src-auth-py::AuthHandler.validate`

### 3.2 CodeIndex Dataclass

The top-level index for a repository. Replaces `RepoIndex` from docs-mcp.

```python
@dataclass
class CodeIndex:
    """Index for a repository's source code."""
    repo: str                    # "owner/repo"
    owner: str
    name: str
    indexed_at: str              # ISO timestamp
    source_files: list[str]      # All indexed file paths
    languages: dict[str, int]    # Language -> file count
    symbols: list[dict]          # Serialized Symbol dicts (without source content)
```

### 3.3 Index JSON Schema

Stored at `~/.code-index/{owner}-{repo}.json`:

```json
{
  "repo": "owner/repo",
  "owner": "owner",
  "name": "repo",
  "indexed_at": "2025-01-15T10:30:00",
  "source_files": ["src/main.py", "src/utils.py"],
  "languages": {"python": 2},
  "symbols": [
    {
      "id": "src-main-py::process_data",
      "file": "src/main.py",
      "name": "process_data",
      "qualified_name": "process_data",
      "kind": "function",
      "language": "python",
      "signature": "def process_data(input: dict, config: Config) -> Result:",
      "docstring": "Transform raw input data according to config rules.",
      "summary": "Transform raw input data according to config rules.",
      "decorators": [],
      "keywords": ["process", "data", "transform", "config"],
      "parent": null,
      "line": 120,
      "end_line": 155,
      "byte_offset": 3842,
      "byte_length": 1205
    }
  ]
}
```

**Note**: `source` content is NOT stored in the index JSON. It is read on demand from raw files using `byte_offset` and `byte_length`. This keeps the index small and fast to load.

---

## 4. File Discovery Algorithm

### 4.1 Single-Call Tree Fetch

**Improvement over docs-mcp**: Instead of recursive directory-by-directory API calls, use the Git Trees API:

```
GET /repos/{owner}/{repo}/git/trees/HEAD?recursive=1
```

This returns ALL paths in the repository in a single API call. The response includes:
```json
{
  "tree": [
    {"path": "src/main.py", "type": "blob", "size": 4523},
    {"path": "src/utils/", "type": "tree"},
    ...
  ]
}
```

### 4.2 File Filtering Pipeline

Apply filters in order (each step reduces the set):

1. **Type filter**: Only `"type": "blob"` entries (skip trees/directories).
2. **Extension filter**: Must match supported language extensions:
   ```python
   LANGUAGE_EXTENSIONS = {
       ".py": "python",
       ".js": "javascript",
       ".jsx": "javascript",
       ".ts": "typescript",
       ".tsx": "typescript",
       ".go": "go",
       ".rs": "rust",
       ".java": "java",
   }
   ```
3. **Skip list**: Exclude known non-source paths:
   ```python
   SKIP_PATTERNS = [
       "node_modules/", "vendor/", "venv/", ".venv/", "__pycache__/",
       "dist/", "build/", ".git/", ".tox/", ".mypy_cache/",
       "target/",           # Rust build output
       ".gradle/",          # Java build output
       "test_data/", "testdata/", "fixtures/", "snapshots/",
       "migrations/",       # Database migrations (usually generated)
       ".min.js", ".min.ts", ".bundle.js",  # Minified files
       "package-lock.json", "yarn.lock", "go.sum",  # Lock files
       "generated/", "proto/",  # Generated code
   ]
   ```
4. **Size limit**: Skip files > 500KB (likely generated or vendored).
5. **.gitignore matching**: If a `.gitignore` file exists in the repo, parse it with `pathspec` and exclude matched paths.
6. **File count limit**: Maximum 500 files per repository. If more files pass filtering, prioritize by:
   - Files in `src/`, `lib/`, `pkg/`, `cmd/`, `internal/` directories
   - Files with fewer path segments (shallower = more important)
   - Alphabetical as tiebreaker

### 4.3 Content Fetching

For each file that passes filtering, fetch raw content:
```
GET /repos/{owner}/{repo}/contents/{path}
Accept: application/vnd.github.v3.raw
```

Use `httpx.AsyncClient` with concurrency limit (10 concurrent requests) to avoid rate limiting.

---

## 5. Search Algorithm

### 5.1 Weighted Scoring

When `search_symbols` is called, each symbol is scored against the query:

```python
def score_symbol(symbol: dict, query: str) -> int:
    query_lower = query.lower()
    query_words = set(query_lower.split())
    score = 0

    # 1. Exact name match (highest weight)
    name_lower = symbol["name"].lower()
    if query_lower == name_lower:
        score += 20
    elif query_lower in name_lower:
        score += 10

    # 2. Name word overlap
    for word in query_words:
        if word in name_lower:
            score += 5

    # 3. Signature match
    sig_lower = symbol["signature"].lower()
    if query_lower in sig_lower:
        score += 8
    for word in query_words:
        if word in sig_lower:
            score += 2

    # 4. Summary match
    summary_lower = symbol.get("summary", "").lower()
    if query_lower in summary_lower:
        score += 5
    for word in query_words:
        if word in summary_lower:
            score += 1

    # 5. Keyword match
    keywords = set(symbol.get("keywords", []))
    matching_keywords = query_words & keywords
    score += len(matching_keywords) * 3

    # 6. Docstring match (lower weight, but catches detail)
    doc_lower = symbol.get("docstring", "").lower()
    for word in query_words:
        if word in doc_lower:
            score += 1

    return score
```

### 5.2 Filters

Before scoring, optionally filter the candidate set:

- **`kind` filter**: If provided, only score symbols matching the kind (e.g., `"function"`).
- **`file_pattern` filter**: If provided, use `fnmatch` to match symbol file paths against the glob pattern.

### 5.3 Results

Sort by score descending, return top `max_results`. Symbols with score 0 are excluded.

---

## 6. Summarization Strategy

Three tiers, tried in order. The goal is to produce a useful one-line summary for every symbol.

### Tier 1: Docstring Extraction (Free)
If the symbol has a docstring, use its **first sentence** (up to first period or 100 characters). This costs zero tokens and is already language-specific.

```python
def extract_summary_from_docstring(docstring: str) -> str:
    """Extract first sentence from docstring."""
    if not docstring:
        return ""
    # Take first line, strip whitespace
    first_line = docstring.strip().split('\n')[0].strip()
    # Truncate at first period if present
    if '.' in first_line:
        first_line = first_line[:first_line.index('.') + 1]
    return first_line[:120]
```

### Tier 2: AI Batch Summarization (Haiku)
If no docstring and `use_ai_summaries=True`, batch symbols into groups of 10 and use Claude Haiku:

```
Model: claude-haiku-4-5-20251001
Max tokens per batch: 500
Prompt: "Summarize each code symbol in ONE short sentence (max 15 words).
         Focus on what it does, not how."
```

Batch format matches docs-mcp pattern (numbered input/output for reliable parsing).

### Tier 3: Signature Fallback
If no docstring and AI is disabled/fails, derive a summary from the signature:

```python
def signature_fallback(symbol: dict) -> str:
    """Generate summary from signature when all else fails."""
    kind = symbol["kind"]
    name = symbol["name"]
    sig = symbol["signature"]

    if kind == "class":
        return f"Class {name}"
    elif kind == "constant":
        return f"Constant {name}"
    elif kind == "type":
        return f"Type definition {name}"
    else:
        # For functions/methods, include parameter hint
        return sig[:120]
```

---

## 7. Error Handling & Graceful Degradation

| Scenario | Behavior | User-visible result |
|----------|----------|-------------------|
| GitHub API 404 | Return `{"success": false, "error": "Repository not found"}` | Clear error message |
| GitHub API 403 (rate limit) | Return error with rate limit reset time | Suggest setting GITHUB_TOKEN |
| GitHub API 401 | Return error suggesting token check | Clear auth error |
| File fetch fails (single file) | Skip file, continue indexing | File omitted, warning in result |
| tree-sitter parse fails (single file) | Skip file, log warning | File omitted silently |
| tree-sitter language not supported | Skip file | File omitted silently |
| No source files found | Return `{"success": false, "error": "No source files found"}` | Clear error |
| No symbols extracted | Return `{"success": false, "error": "No symbols extracted"}` | Clear error |
| AI summarization fails (batch) | Fall back to Tier 3 (signature) | Summaries still present, lower quality |
| AI summarization fails (no API key) | Fall back to Tier 1 + Tier 3 | Docstring or signature summaries |
| Anthropic package not installed | Fall back to Tier 1 + Tier 3 | Same as above |
| Index file corrupted/missing | Return `{"error": "Repository not indexed"}` | Clear error, suggest re-index |
| Symbol ID not found | Return `{"error": "Symbol not found: {id}"}` | Clear error |
| Repository not indexed | Return `{"error": "Repository not indexed: owner/repo"}` | Suggest running index_repo |
| Raw file missing on disk | Re-fetch from stored content or return error | Graceful error |
| .gitignore parse error | Ignore .gitignore, index all matching files | Warning logged, indexing continues |
| File exceeds size limit | Skip file | File omitted silently |
| Repo exceeds file count limit | Index first 500 files (priority-sorted) | Warning in result with total file count |

### Error Response Format

All errors follow consistent JSON structure:
```json
{
  "error": "Human-readable error message"
}
```

For partial failures during indexing:
```json
{
  "success": true,
  "repo": "owner/repo",
  "warnings": ["Skipped 3 files due to parse errors", "AI summaries unavailable, using fallback"]
}
```

---

## 8. Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `GITHUB_TOKEN` | GitHub API authentication (for private repos, higher rate limits) | No (public repos work without) |
| `ANTHROPIC_API_KEY` | AI summarization via Claude Haiku | No (falls back to docstring/signature) |

---

## 9. Supported Languages (v1)

| Language | Extensions | tree-sitter grammar |
|----------|-----------|-------------------|
| Python | `.py` | `tree-sitter-python` (via language-pack) |
| JavaScript | `.js`, `.jsx` | `tree-sitter-javascript` (via language-pack) |
| TypeScript | `.ts`, `.tsx` | `tree-sitter-typescript` (via language-pack) |
| Go | `.go` | `tree-sitter-go` (via language-pack) |
| Rust | `.rs` | `tree-sitter-rust` (via language-pack) |
| Java | `.java` | `tree-sitter-java` (via language-pack) |

All grammars come from `tree-sitter-language-pack` — one dependency, pre-compiled wheels, 165+ languages available for future expansion.

---

## 10. Token Budget Guidelines

For tool responses, target these token budgets:

| Tool | Target tokens | Strategy |
|------|--------------|----------|
| `index_repo` | <300 | Metadata only, no content |
| `list_repos` | <300 | Compact summary per repo |
| `get_file_tree` | <2,000 | Hierarchical paths, no content |
| `get_file_outline` | <1,500 | Signatures + summaries, no source |
| `get_symbol` | <2,000 | Single symbol source, typically small |
| `search_symbols` | <500 | IDs + signatures + summaries |
| `get_symbols` | <5,000 | Multiple symbols, batched |

These represent typical cases. Actual sizes vary with repo/file/symbol size.

