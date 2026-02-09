# jcodemunch-mcp — Build Order

Step-by-step implementation guide. 6 phases, 20 steps. Each phase has a test checkpoint.

---

## Phase 1: Scaffold + Python Parser (Critical Path)

**Goal**: A working parser that extracts Python symbols from source code. This is the foundation everything else builds on.

### Step 1: Project Scaffold
**Create files**:
- `pyproject.toml` (already exists — verify deps)
- `src/jcodemunch_mcp/__init__.py` — package init with `__version__ = "0.1.0"`
- `src/jcodemunch_mcp/parser/__init__.py`
- `src/jcodemunch_mcp/storage/__init__.py`
- `src/jcodemunch_mcp/summarizer/__init__.py`
- `src/jcodemunch_mcp/tools/__init__.py`
- `tests/__init__.py`

**Verify**: `pip install -e .` succeeds.

### Step 2: Symbol Dataclass
**File**: `src/jcodemunch_mcp/parser/symbols.py`

Implement:
- `Symbol` dataclass (see SPEC.md Section 3.1)
- `slugify(text: str) -> str` — convert file path to slug
- `make_symbol_id(file_path: str, qualified_name: str) -> str` — generate unique ID

**Verify**: Import succeeds, can create Symbol instances.

### Step 3: Language Registry
**File**: `src/jcodemunch_mcp/parser/languages.py`

Implement:
- `LanguageSpec` dataclass (see ARCHITECTURE.md Section 2.2)
- `PYTHON_SPEC` — Python language specification
- `LANGUAGE_REGISTRY = {"python": PYTHON_SPEC}` (other languages added in Phase 5)
- `LANGUAGE_EXTENSIONS` dict mapping file extensions to language names

**Verify**: Can import `LANGUAGE_REGISTRY["python"]` and all fields are populated.

### Step 4: Generic Extractor (Python First)
**File**: `src/jcodemunch_mcp/parser/extractor.py`

Implement:
- `parse_file(content: str, filename: str, language: str) -> list[Symbol]`
- Internal helpers: `_extract_name()`, `_build_signature()`, `_extract_docstring()`, `_extract_decorators()`
- Use `tree_sitter_language_pack.get_parser(spec.ts_language)` for parsing
- Walk CST recursively, match node types from spec

**Dependencies**: Step 2 (Symbol), Step 3 (LanguageSpec)

**Verify**: Parse a Python file and get correct Symbol list.

### Step 5: Symbol Hierarchy
**File**: `src/jcodemunch_mcp/parser/hierarchy.py`

Implement:
- `SymbolNode` dataclass (symbol + children)
- `build_symbol_tree(symbols: list[Symbol]) -> list[SymbolNode]`
- `flatten_tree(nodes) -> list[tuple[Symbol, int]]`

**Dependencies**: Step 2 (Symbol)

**Test Checkpoint 1**: `tests/test_parser.py`
```python
# Test Python parsing end-to-end
PYTHON_SOURCE = '''
class MyClass:
    """A sample class."""
    def method(self, x: int) -> str:
        """Do something."""
        return str(x)

def standalone(a, b):
    """Standalone function."""
    return a + b

MAX_SIZE = 100
'''

def test_parse_python():
    symbols = parse_file(PYTHON_SOURCE, "test.py", "python")
    assert len(symbols) >= 3  # class, method, function, constant
    class_sym = [s for s in symbols if s.kind == "class"][0]
    assert class_sym.name == "MyClass"
    assert "A sample class" in class_sym.docstring
    method_sym = [s for s in symbols if s.kind == "method"][0]
    assert method_sym.name == "method"
    assert method_sym.parent is not None  # should reference MyClass
```

---

## Phase 2: Storage + Summarizer

**Goal**: Can save/load indexes and generate summaries. No GitHub integration yet — uses local data.

### Step 6: CodeIndex and IndexStore
**File**: `src/jcodemunch_mcp/storage/index_store.py`

Implement:
- `CodeIndex` dataclass (see SPEC.md Section 3.2)
- `IndexStore` class:
  - `__init__(base_path=None)` — defaults to `~/.code-index/`
  - `save_index(owner, name, source_files, symbols, raw_files, languages)` → `CodeIndex`
  - `load_index(owner, name)` → `Optional[CodeIndex]`
  - `get_symbol_content(owner, name, symbol_id)` → `Optional[str]` (byte-offset read!)
  - `list_repos()` → `list[dict]`
  - `delete_index(owner, name)` → `bool`
- `CodeIndex.get_symbol(symbol_id)` → `Optional[dict]`
- `CodeIndex.search(query)` → `list[dict]` (weighted scoring from SPEC.md Section 5)

**Dependencies**: Step 2 (Symbol)

### Step 7: Summarizer
**File**: `src/jcodemunch_mcp/summarizer/batch_summarize.py`

Implement:
- `extract_summary_from_docstring(docstring: str) -> str` — Tier 1
- `BatchSummarizer` class — Tier 2 (same pattern as docs-mcp)
  - `summarize_batch(symbols, batch_size=10)` → `list[Symbol]`
  - Uses `claude-haiku-4-5-20251001`
- `signature_fallback(symbol) -> str` — Tier 3
- `summarize_symbols_simple(symbols) -> list[Symbol]` — Tier 1 + Tier 3 combined
- `summarize_symbols(symbols, use_ai=True) -> list[Symbol]` — orchestrates all three tiers

**Dependencies**: Step 2 (Symbol)

**Test Checkpoint 2**: `tests/test_storage.py` and `tests/test_summarizer.py`
```python
# test_storage.py
def test_save_and_load_index(tmp_path):
    store = IndexStore(base_path=str(tmp_path))
    symbols = [Symbol(...)]
    index = store.save_index("owner", "repo", ["main.py"], symbols, {"main.py": "..."}, {"python": 1})
    loaded = store.load_index("owner", "repo")
    assert loaded is not None
    assert loaded.repo == "owner/repo"

def test_byte_offset_retrieval(tmp_path):
    store = IndexStore(base_path=str(tmp_path))
    # Save a file with known content, verify byte-offset read returns correct slice
    ...

# test_summarizer.py
def test_docstring_extraction():
    assert extract_summary_from_docstring("Do something cool.\n\nDetails...") == "Do something cool."

def test_signature_fallback():
    sym = Symbol(kind="function", name="foo", signature="def foo(x: int) -> str:", ...)
    assert signature_fallback(sym) == "def foo(x: int) -> str:"

def test_simple_summarize():
    symbols = [Symbol(docstring="Does something.", ...)]
    result = summarize_symbols_simple(symbols)
    assert result[0].summary == "Does something."
```

---

## Phase 3: File Discovery + Index Tool

**Goal**: Can index a real GitHub repository end-to-end.

### Step 8: GitHub URL Parsing
**File**: `src/jcodemunch_mcp/tools/index_repo.py`

Implement:
- `parse_github_url(url: str) -> tuple[str, str]` — extract owner/repo (identical to docs-mcp)

### Step 9: File Discovery via Git Trees API
**File**: `src/jcodemunch_mcp/tools/index_repo.py`

Implement:
- `fetch_repo_tree(owner, repo, token) -> list[dict]` — calls `GET /repos/{owner}/{repo}/git/trees/HEAD?recursive=1`
- `discover_source_files(tree_entries, gitignore_content) -> list[str]` — applies filtering pipeline (SPEC.md Section 4.2):
  1. Type filter (blobs only)
  2. Extension filter (LANGUAGE_EXTENSIONS)
  3. Skip list (SKIP_PATTERNS)
  4. Size limit (500KB)
  5. .gitignore matching (using `pathspec`)
  6. File count limit (500)

**Dependencies**: Step 3 (LANGUAGE_EXTENSIONS)

### Step 10: Content Fetching
**File**: `src/jcodemunch_mcp/tools/index_repo.py`

Implement:
- `fetch_file_content(owner, repo, path, token) -> str` — raw content via GitHub API
- `fetch_gitignore(owner, repo, token) -> Optional[str]` — fetch `.gitignore` if it exists
- Concurrency: use `asyncio.Semaphore(10)` to limit concurrent requests

### Step 11: Index Tool Orchestration
**File**: `src/jcodemunch_mcp/tools/index_repo.py`

Implement:
- `index_repo(url, use_ai_summaries, github_token, storage_path) -> dict`
  1. Parse URL
  2. Fetch tree
  3. Fetch .gitignore
  4. Discover source files
  5. Fetch all file contents (concurrent with semaphore)
  6. Parse each file with `parse_file()`
  7. Summarize all symbols
  8. Save index + raw files
  9. Return statistics

**Dependencies**: Steps 4, 6, 7, 8, 9, 10

**Test Checkpoint 3**: `tests/test_tools.py` (mock GitHub API)
```python
# Mock the GitHub API responses
def test_index_repo(tmp_path, mock_github):
    result = await index_repo(
        "owner/repo", use_ai_summaries=False, storage_path=str(tmp_path)
    )
    assert result["success"] is True
    assert result["symbol_count"] > 0

def test_file_discovery():
    tree_entries = [
        {"path": "src/main.py", "type": "blob", "size": 1000},
        {"path": "node_modules/foo.js", "type": "blob", "size": 500},
        {"path": "README.md", "type": "blob", "size": 200},
    ]
    files = discover_source_files(tree_entries, gitignore_content=None)
    assert "src/main.py" in files
    assert "node_modules/foo.js" not in files
    assert "README.md" not in files  # .md not a source file
```

---

## Phase 4: Query Tools

**Goal**: All 6 remaining tools work against a saved index.

### Step 12: list_repos Tool
**File**: `src/jcodemunch_mcp/tools/list_repos.py`

Implement `list_repos(storage_path)` → dict. Thin wrapper around `IndexStore.list_repos()`.

### Step 13: get_file_tree Tool
**File**: `src/jcodemunch_mcp/tools/get_file_tree.py`

Implement `get_file_tree(repo, path_prefix, storage_path)` → dict.
- Load index
- Build nested directory tree from flat `source_files` list
- Annotate each file with language, symbol_count, line_count
- Filter by path_prefix if provided

### Step 14: get_file_outline Tool
**File**: `src/jcodemunch_mcp/tools/get_file_outline.py`

**Dependencies**: Step 5 (hierarchy), Step 6 (storage)

Implement `get_file_outline(repo, file_path, storage_path)` → dict.
- Load index
- Filter symbols to those in the requested file
- Build hierarchical symbol tree using `build_symbol_tree()` from `parser/hierarchy.py`
- Return signatures + summaries (no source code)

### Step 15: get_symbol and get_symbols Tools
**File**: `src/jcodemunch_mcp/tools/get_symbol.py`

Implement:
- `get_symbol(repo, symbol_id, storage_path)` → dict — loads full source via byte-offset read
- `get_symbols(repo, symbol_ids, storage_path)` → dict — batch retrieval

### Step 16: search_symbols Tool
**File**: `src/jcodemunch_mcp/tools/search_symbols.py`

Implement `search_symbols(repo, query, kind, file_pattern, max_results, storage_path)` → dict.
- Load index
- Apply kind filter and file_pattern filter
- Score each candidate symbol (SPEC.md Section 5)
- Return top results with signatures + summaries

**Test Checkpoint 4**: `tests/test_tools.py` (extend with query tool tests)
```python
# Pre-save an index, then test all query tools
def test_get_file_outline(tmp_path):
    # Save index with known symbols
    result = get_file_outline("owner/repo", "src/main.py", storage_path=str(tmp_path))
    assert len(result["symbols"]) > 0

def test_search_symbols(tmp_path):
    result = search_symbols("owner/repo", "authenticate", storage_path=str(tmp_path))
    assert result["result_count"] > 0

def test_get_symbol_source(tmp_path):
    result = get_symbol("owner/repo", "src-main-py::MyClass.login", storage_path=str(tmp_path))
    assert "source" in result
    assert "def login" in result["source"]
```

---

## Phase 5: Server + Remaining Languages

**Goal**: MCP server runs and handles all 7 tools. All 6 languages are supported.

### Step 17: MCP Server
**File**: `src/jcodemunch_mcp/server.py`

Implement:
- Create `Server("jcodemunch-mcp")`
- `@server.list_tools()` — return all 7 Tool definitions with input schemas (from SPEC.md Section 2)
- `@server.call_tool()` — dispatch to tool functions
- `run_server()` async function
- `main()` entry point

**Pattern**: Follow docs-mcp `server.py` exactly. Same structure, different tool names and schemas.

### Step 18: Add Remaining Language Specs
**File**: `src/jcodemunch_mcp/parser/languages.py`

Add to `LANGUAGE_REGISTRY`:
- `JAVASCRIPT_SPEC` (see ARCHITECTURE.md Section 2.3)
- `TYPESCRIPT_SPEC`
- `GO_SPEC`
- `RUST_SPEC`
- `JAVA_SPEC`

May need to adjust the generic extractor slightly if any language's AST structure doesn't fit the current model. Handle edge cases:
- JavaScript arrow functions (name from parent `variable_declarator`)
- Go method receivers (extract receiver type for qualified name)
- Rust `impl` blocks (extract type for qualified name)
- Java constructors (same name as class)

**Test Checkpoint 5**: `tests/test_languages.py`
```python
# Test each language with a representative source snippet
JAVASCRIPT_SOURCE = '''
/** Greet a user. */
function greet(name) {
    return `Hello, ${name}!`;
}

class Calculator {
    /** Add two numbers. */
    add(a, b) {
        return a + b;
    }
}

const MAX_RETRY = 5;
'''

def test_parse_javascript():
    symbols = parse_file(JAVASCRIPT_SOURCE, "app.js", "javascript")
    func = [s for s in symbols if s.name == "greet"][0]
    assert func.kind == "function"
    assert "Greet a user" in func.docstring

# Similar tests for TypeScript, Go, Rust, Java
# Each test validates: functions, classes/types, methods, constants, docstrings

def test_parse_typescript():
    ...

def test_parse_go():
    ...

def test_parse_rust():
    ...

def test_parse_java():
    ...
```

### Step 19: End-to-end Server Test
**File**: `tests/test_server.py`

Test the server can:
- List tools (returns 7)
- Handle index_repo (mocked)
- Handle list_repos
- Handle get_file_tree, get_file_outline, get_symbol, get_symbols, search_symbols

```python
def test_server_lists_seven_tools():
    tools = await server.list_tools()
    assert len(tools) == 7
    names = {t.name for t in tools}
    assert names == {
        "index_repo", "list_repos", "get_file_tree",
        "get_file_outline", "get_symbol", "get_symbols", "search_symbols"
    }
```

---

## Phase 6: Documentation + Comprehensive Tests

**Goal**: Project is complete, documented, and thoroughly tested.

### Step 20: README + User Guide + Final Tests

**README.md**: Project overview, quick start, MCP config, example workflows.

**USER_GUIDE.md**: Detailed guide with:
- Installation
- Configuration (Claude Desktop, VS Code)
- All 7 tools with example usage
- Workflow examples (explore repo, find function, understand class)
- Troubleshooting

**Comprehensive tests**: Expand test suite to cover:
- Edge cases (empty files, files with no functions, malformed code)
- All error paths (repo not found, symbol not found, etc.)
- Large file handling (500+ symbols)
- Unicode in source code
- Each language's edge cases

---

## Dependency Graph

```
Step 1 (scaffold)
  ├── Step 2 (Symbol) ──────┬── Step 4 (extractor) ──┐
  ├── Step 3 (languages) ───┘                         │
  │                          ├── Step 5 (hierarchy)    │
  │                          │                         │
  │   Step 6 (storage) ─────┤                         │
  │   Step 7 (summarizer) ──┤                         │
  │                          │                         │
  │   Step 8 (URL parse) ───┤                         │
  │   Step 9 (discovery) ───┤                         │
  │   Step 10 (fetch) ──────┤                         │
  │                          │                         │
  │   Step 11 (index tool) ◄┘─── needs 4,6,7,8,9,10  │
  │                                                    │
  │   Step 12-16 (query tools) ◄── needs 5,6           │
  │                                                    │
  │   Step 17 (server) ◄── needs 11, 12-16            │
  │   Step 18 (languages) ◄── needs 4                 │
  │                                                    │
  │   Step 19 (e2e test) ◄── needs 17, 18             │
  │   Step 20 (docs+tests) ◄── needs everything       │
```

## Phase Summary

| Phase | Steps | Deliverable | Test Checkpoint |
|-------|-------|-------------|----------------|
| 1 | 1-5 | Python parser works | test_parser.py passes |
| 2 | 6-7 | Storage + summarizer work | test_storage.py, test_summarizer.py pass |
| 3 | 8-11 | Can index a real GitHub repo | test_tools.py (index) passes |
| 4 | 12-16 | All query tools work | test_tools.py (queries) passes |
| 5 | 17-19 | MCP server runs, all languages | test_languages.py, test_server.py pass |
| 6 | 20 | Docs complete, all tests pass | Full test suite passes |

