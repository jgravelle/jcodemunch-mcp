# Architecture

## Directory Structure

```
jcodemunch-mcp/
├── pyproject.toml
├── README.md
├── SECURITY.md
├── SYMBOL_SPEC.md
├── CACHE_SPEC.md
├── LANGUAGE_SUPPORT.md
│
├── src/jcodemunch_mcp/
│   ├── __init__.py
│   ├── server.py                    # MCP server: 11 tool definitions + dispatch
│   ├── security.py                  # Path traversal, symlink, secret, binary detection
│   │
│   ├── parser/
│   │   ├── __init__.py
│   │   ├── symbols.py               # Symbol dataclass, ID generation, hashing
│   │   ├── extractor.py             # tree-sitter AST walking + symbol extraction
│   │   ├── languages.py             # LanguageSpec registry
│   │   └── hierarchy.py             # SymbolNode tree building for file outlines
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── index_store.py           # CodeIndex, IndexStore: save/load, incremental indexing
│   │   └── token_tracker.py         # Persistent token savings counter (~/.code-index/_savings.json)
│   │
│   ├── summarizer/
│   │   ├── __init__.py
│   │   └── batch_summarize.py       # Docstring → AI → signature fallback
│   │
│   └── tools/
│       ├── __init__.py
│       ├── index_repo.py            # GitHub repository indexing
│       ├── index_folder.py          # Local folder indexing
│       ├── list_repos.py
│       ├── get_file_tree.py
│       ├── get_file_outline.py
│       ├── get_symbol.py
│       ├── search_symbols.py
│       ├── search_text.py
│       ├── get_repo_outline.py
│       └── invalidate_cache.py
│
├── tests/
│   ├── fixtures/
│   ├── test_parser.py
│   ├── test_languages.py
│   ├── test_storage.py
│   ├── test_summarizer.py
│   ├── test_tools.py
│   ├── test_server.py
│   ├── test_security.py
│   └── test_hardening.py
│
├── benchmarks/
│   └── run_benchmarks.py
│
└── .github/workflows/
    ├── test.yml
    └── benchmark.yml
```

---

## Data Flow

```
Source code (GitHub API or local folder)
    │
    ▼
Security filters (path traversal, symlinks, secrets, binary, size)
    │
    ▼
tree-sitter parsing (language-specific grammars via LanguageSpec)
    │
    ▼
Symbol extraction (functions, classes, methods, constants, types)
    │
    ▼
Post-processing (overload disambiguation, content hashing)
    │
    ▼
Summarization (docstring → AI batch → signature fallback)
    │
    ▼
Storage (JSON index + raw files, atomic writes)
    │
    ▼
MCP tools (discovery, search, retrieval)
```

---

## Parser Design

The parser follows a **language registry pattern**. Each supported language defines a `LanguageSpec` describing how symbols are extracted from its AST.

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

The generic extractor performs two post-processing passes:

1. **Overload disambiguation**
   Duplicate symbol IDs receive numeric suffixes (`~1`, `~2`, etc.)

2. **Content hashing**
   SHA-256 hashes of symbol source content enable change detection.

---

## Symbol ID Scheme

```
{file_path}::{qualified_name}#{kind}
```

Examples:

* `src/main.py::UserService.login#method`
* `src/utils.py::authenticate#function`
* `config.py::MAX_RETRIES#constant`

IDs remain stable across re-indexing as long as the file path, qualified name, and symbol kind remain unchanged.

---

## Storage

Indexes are stored at `~/.code-index/` (configurable via `CODE_INDEX_PATH`):

* `{owner}-{name}.json` — metadata, file hashes, symbol metadata
* `{owner}-{name}/` — cached raw source files

Each symbol records byte offsets, allowing **O(1)** retrieval via `seek()` + `read()` without re-parsing.

Incremental indexing compares stored file hashes with current hashes, reprocessing only changed files. Writes are atomic (temporary file + rename).

---

## Security

All file operations pass through `security.py`:

* Path traversal protection via validated resolved paths
* Symlink target validation
* Secret-file exclusion using predefined patterns
* Binary file detection
* Safe encoding reads using `errors="replace"`

---

## Response Envelope

All tool responses include metadata:

```json
{
  "result": "...",
  "_meta": {
    "timing_ms": 42,
    "repo": "owner/repo",
    "symbol_count": 387,
    "truncated": false,
    "tokens_saved": 2450,
    "total_tokens_saved": 184320
  }
}
```

`tokens_saved` and `total_tokens_saved` are included on all retrieval and search tools. The running total is persisted to `~/.code-index/_savings.json` across sessions.

---

## Search Algorithm

`search_symbols` uses weighted scoring:

| Match type              | Weight                |
| ----------------------- | --------------------- |
| Exact name match        | +20                   |
| Name substring          | +10                   |
| Name word overlap       | +5 per word           |
| Signature match         | +8 (full) / +2 (word) |
| Summary match           | +5 (full) / +1 (word) |
| Docstring/keyword match | +3 / +1 per word      |

Filters (kind, language, file_pattern) are applied before scoring. Results scoring zero are excluded.

---

## Dependencies

| Package                            | Purpose                       |
| ---------------------------------- | ----------------------------- |
| `mcp>=1.0.0`                       | MCP server framework          |
| `httpx>=0.27.0`                    | Async HTTP for GitHub API     |
| `anthropic>=0.40.0`                | AI summarization via Claude Haiku (default) |
| `google-generativeai>=0.8.0`       | AI summarization via Gemini Flash (optional, `pip install jcodemunch-mcp[gemini]`) |
| `tree-sitter-language-pack>=0.7.0` | Precompiled grammars          |
| `pathspec>=0.12.0`                 | `.gitignore` pattern matching |
