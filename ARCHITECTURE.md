# jcodemunch-mcp — Architecture & Parser Design

## 1. Project Directory Structure

```
jcodemunch-mcp/
├── pyproject.toml              # Package config, deps, entry point
├── .gitignore                  # Python gitignore
├── README.md                   # User-facing documentation
├── USER_GUIDE.md               # Detailed usage guide
├── SPEC.md                     # Technical specification (this project)
├── ARCHITECTURE.md             # This file
├── BUILD_ORDER.md              # Implementation guide
├── PROMPT.md                   # Session prompt for builders
│
├── src/
│   └── jcodemunch_mcp/
│       ├── __init__.py                 # Package init, __version__
│       ├── server.py                   # MCP server: tool registration + dispatch
│       │
│       ├── parser/
│       │   ├── __init__.py             # Exports: parse_file, Symbol, LANGUAGE_EXTENSIONS
│       │   ├── symbols.py             # Symbol dataclass definition
│       │   ├── extractor.py           # Generic AST symbol extractor (language-agnostic)
│       │   ├── languages.py           # Language registry: node types, query specs per language
│       │   └── hierarchy.py           # Build symbol tree (file > class > method)
│       │
│       ├── storage/
│       │   ├── __init__.py             # Exports: IndexStore, CodeIndex
│       │   └── index_store.py         # Index save/load, raw file storage, byte-offset reads
│       │
│       ├── summarizer/
│       │   ├── __init__.py             # Exports: BatchSummarizer, summarize_symbols_simple
│       │   └── batch_summarize.py     # Three-tier summarization (docstring > AI > signature)
│       │
│       └── tools/
│           ├── __init__.py             # (empty)
│           ├── index_repo.py          # index_repo tool: fetch, parse, summarize, save
│           ├── list_repos.py          # list_repos tool
│           ├── get_file_tree.py       # get_file_tree tool
│           ├── get_file_outline.py    # get_file_outline tool
│           ├── get_symbol.py          # get_symbol + get_symbols tools
│           └── search_symbols.py      # search_symbols tool
│
└── tests/
    ├── __init__.py
    ├── test_parser.py             # Unit tests for AST extraction
    ├── test_languages.py          # Per-language parsing tests
    ├── test_storage.py            # Index store tests
    ├── test_summarizer.py         # Summarization tests
    ├── test_tools.py              # Integration tests for MCP tools
    └── test_server.py             # End-to-end server tests
```

### File Purposes

| File | Purpose | Approximate lines |
|------|---------|------------------|
| `server.py` | MCP server setup, 7 tool definitions with schemas, dispatch to tool functions | ~250 |
| `parser/symbols.py` | `Symbol` dataclass, `slugify()`, `make_symbol_id()` | ~50 |
| `parser/extractor.py` | `parse_file(content, filename, language)` → `list[Symbol]`. Uses tree-sitter + language spec. | ~150 |
| `parser/languages.py` | `LanguageSpec` dataclass, `LANGUAGE_REGISTRY` dict, node type mappings for all 6 languages | ~250 |
| `parser/hierarchy.py` | `SymbolNode` dataclass, `build_symbol_tree()`, `flatten_tree()` | ~50 |
| `storage/index_store.py` | `CodeIndex` dataclass, `IndexStore` class (save/load/search/delete), byte-offset reads | ~250 |
| `summarizer/batch_summarize.py` | `BatchSummarizer` (AI), `summarize_symbols_simple()` (fallback), docstring extraction | ~180 |
| `tools/index_repo.py` | GitHub API calls, file discovery, orchestrates parse→summarize→save pipeline | ~200 |
| `tools/list_repos.py` | Thin wrapper around `IndexStore.list_repos()` | ~25 |
| `tools/get_file_tree.py` | Builds nested tree from flat file list, path prefix filtering | ~80 |
| `tools/get_file_outline.py` | Filters symbols by file, builds hierarchical outline | ~80 |
| `tools/get_symbol.py` | `get_symbol()` and `get_symbols()` with byte-offset content retrieval | ~100 |
| `tools/search_symbols.py` | Weighted scoring search algorithm with kind/file filters | ~80 |

---

## 2. Parser Architecture

### 2.1 Overview

The parser converts raw source code into `Symbol` objects using tree-sitter's concrete syntax trees. The design uses a **language registry** pattern: each language defines a `LanguageSpec` that tells the generic extractor which AST node types to look for and how to extract signatures, docstrings, and decorators.

```
Raw source code
      │
      ▼
tree-sitter parse (language-specific grammar)
      │
      ▼
Concrete Syntax Tree (CST)
      │
      ▼
Generic Extractor (walks CST using LanguageSpec)
      │
      ▼
list[Symbol]
```

### 2.2 Language Registry

```python
@dataclass
class LanguageSpec:
    """Specification for extracting symbols from a language's AST."""
    # tree-sitter language name (for tree-sitter-language-pack)
    ts_language: str

    # Node types that represent extractable symbols
    # Maps node_type → symbol kind
    symbol_node_types: dict[str, str]
    # e.g., {"function_definition": "function", "class_definition": "class"}

    # How to extract the symbol name from a node
    # Maps node_type → child field name containing the name
    name_fields: dict[str, str]
    # e.g., {"function_definition": "name", "class_definition": "name"}

    # How to extract parameters/signature beyond the name
    # Maps node_type → child field name for parameters
    param_fields: dict[str, str]
    # e.g., {"function_definition": "parameters"}

    # Return type extraction (if language supports it)
    # Maps node_type → child field name for return type
    return_type_fields: dict[str, str]
    # e.g., {"function_definition": "return_type"}

    # Docstring extraction strategy
    # "next_sibling_string" = Python (expression_statement after def)
    # "first_child_comment" = JS/TS (/** */ before function)
    # "preceding_comment" = Go/Rust/Java (// or /* */ before decl)
    docstring_strategy: str

    # Decorator/attribute node type (if any)
    decorator_node_type: str | None
    # e.g., "decorator" for Python, "attribute_item" for Rust

    # Node types that indicate nesting (methods inside classes)
    container_node_types: list[str]
    # e.g., ["class_definition"] for Python - methods inside are "method" kind

    # Additional extraction: constants, type aliases
    constant_patterns: list[str]   # Node types for constants
    type_patterns: list[str]       # Node types for type definitions
```

### 2.3 Language Specifications

#### Python
```python
PYTHON_SPEC = LanguageSpec(
    ts_language="python",
    symbol_node_types={
        "function_definition": "function",
        "class_definition": "class",
    },
    name_fields={
        "function_definition": "name",
        "class_definition": "name",
    },
    param_fields={
        "function_definition": "parameters",
    },
    return_type_fields={
        "function_definition": "return_type",
    },
    docstring_strategy="next_sibling_string",
    decorator_node_type="decorator",
    container_node_types=["class_definition"],
    constant_patterns=["assignment"],       # TOP_LEVEL = value (uppercase names)
    type_patterns=["type_alias_statement"], # type X = Y (Python 3.12+)
)
```

**Python docstring extraction**: After a `function_definition` or `class_definition` node, look for the first child of the body that is an `expression_statement` containing a `string` node. That string is the docstring.

**Python decorator extraction**: Look for `decorator` siblings preceding the definition node.

**Python constant detection**: Top-level `assignment` nodes where the target name is `UPPER_CASE`.

#### JavaScript
```python
JAVASCRIPT_SPEC = LanguageSpec(
    ts_language="javascript",
    symbol_node_types={
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "arrow_function": "function",           # const foo = () => {}
        "generator_function_declaration": "function",
    },
    name_fields={
        "function_declaration": "name",
        "class_declaration": "name",
        "method_definition": "name",
        # arrow_function: name comes from parent variable_declarator
    },
    param_fields={
        "function_declaration": "parameters",
        "method_definition": "parameters",
        "arrow_function": "parameters",
    },
    return_type_fields={},  # JS has no type annotations
    docstring_strategy="preceding_comment",  # JSDoc /** */
    decorator_node_type=None,               # JS has no decorators (yet)
    container_node_types=["class_declaration", "class"],
    constant_patterns=["lexical_declaration"],  # const FOO = ...
    type_patterns=[],
)
```

**JavaScript specifics**:
- Arrow functions (`const foo = () => {}`) get their name from the parent `variable_declarator` node.
- JSDoc comments (`/** ... */`) are `comment` nodes immediately preceding the declaration.
- `const` declarations with UPPER_CASE names are treated as constants.

#### TypeScript
```python
TYPESCRIPT_SPEC = LanguageSpec(
    ts_language="typescript",
    symbol_node_types={
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "arrow_function": "function",
        "interface_declaration": "type",
        "type_alias_declaration": "type",
        "enum_declaration": "type",
    },
    name_fields={
        "function_declaration": "name",
        "class_declaration": "name",
        "method_definition": "name",
        "interface_declaration": "name",
        "type_alias_declaration": "name",
        "enum_declaration": "name",
    },
    param_fields={
        "function_declaration": "parameters",
        "method_definition": "parameters",
        "arrow_function": "parameters",
    },
    return_type_fields={
        "function_declaration": "return_type",
        "method_definition": "return_type",
        "arrow_function": "return_type",
    },
    docstring_strategy="preceding_comment",
    decorator_node_type="decorator",
    container_node_types=["class_declaration", "class"],
    constant_patterns=["lexical_declaration"],
    type_patterns=["interface_declaration", "type_alias_declaration", "enum_declaration"],
)
```

**TypeScript specifics**:
- Extends JavaScript with type annotations, interfaces, type aliases, enums.
- Uses the `typescript` parser from tree-sitter-language-pack (separate from `tsx`).
- TSX files use the `tsx` parser.

#### Go
```python
GO_SPEC = LanguageSpec(
    ts_language="go",
    symbol_node_types={
        "function_declaration": "function",
        "method_declaration": "method",
        "type_declaration": "type",
    },
    name_fields={
        "function_declaration": "name",
        "method_declaration": "name",
        "type_declaration": "name",  # Actually in the type_spec child
    },
    param_fields={
        "function_declaration": "parameters",
        "method_declaration": "parameters",
    },
    return_type_fields={
        "function_declaration": "result",
        "method_declaration": "result",
    },
    docstring_strategy="preceding_comment",
    decorator_node_type=None,
    container_node_types=[],  # Go methods aren't nested
    constant_patterns=["const_declaration"],
    type_patterns=["type_declaration"],
)
```

**Go specifics**:
- Methods have a receiver (e.g., `func (s *Server) Handle()`). The receiver type is extracted for qualified naming.
- Go doc comments are `//` comments immediately preceding the declaration (no blank line).
- `type_declaration` covers structs, interfaces, and type aliases. The `type_spec` child contains the actual name.
- Constants use `const_declaration` (may be `const ( ... )` blocks).

#### Rust
```python
RUST_SPEC = LanguageSpec(
    ts_language="rust",
    symbol_node_types={
        "function_item": "function",
        "struct_item": "type",
        "enum_item": "type",
        "trait_item": "type",
        "impl_item": "class",    # impl blocks group methods
        "type_item": "type",
    },
    name_fields={
        "function_item": "name",
        "struct_item": "name",
        "enum_item": "name",
        "trait_item": "name",
        "type_item": "name",
    },
    param_fields={
        "function_item": "parameters",
    },
    return_type_fields={
        "function_item": "return_type",
    },
    docstring_strategy="preceding_comment",  # /// or //! doc comments
    decorator_node_type="attribute_item",
    container_node_types=["impl_item", "trait_item"],
    constant_patterns=["const_item", "static_item"],
    type_patterns=["struct_item", "enum_item", "trait_item", "type_item"],
)
```

**Rust specifics**:
- `impl` blocks serve as containers. Methods inside `impl Foo` get qualified name `Foo.method_name`.
- Doc comments use `///` (outer) or `//!` (inner). These are `line_comment` nodes starting with `///`.
- `#[derive(...)]` and other attributes are `attribute_item` nodes.
- `impl Trait for Type` blocks: the trait implementation type provides the qualifier.

#### Java
```python
JAVA_SPEC = LanguageSpec(
    ts_language="java",
    symbol_node_types={
        "method_declaration": "method",
        "constructor_declaration": "method",
        "class_declaration": "class",
        "interface_declaration": "type",
        "enum_declaration": "type",
    },
    name_fields={
        "method_declaration": "name",
        "constructor_declaration": "name",
        "class_declaration": "name",
        "interface_declaration": "name",
        "enum_declaration": "name",
    },
    param_fields={
        "method_declaration": "parameters",
        "constructor_declaration": "parameters",
    },
    return_type_fields={
        "method_declaration": "type",
    },
    docstring_strategy="preceding_comment",  # Javadoc /** */
    decorator_node_type="marker_annotation",  # @Override, @Deprecated
    container_node_types=["class_declaration", "interface_declaration", "enum_declaration"],
    constant_patterns=["field_declaration"],  # static final UPPER_CASE
    type_patterns=["interface_declaration", "enum_declaration"],
)
```

**Java specifics**:
- All methods live inside classes. The class name provides the qualifier.
- Constructor declarations are separate from method declarations.
- Annotations (`@Override`, `@Deprecated`) are `marker_annotation` or `annotation` nodes.
- Constants are `field_declaration` nodes with `static final` modifiers and UPPER_CASE names.
- Javadoc (`/** ... */`) is a `block_comment` node preceding the declaration.

---

## 3. Generic Extractor

`parser/extractor.py` contains the core extraction logic. It is language-agnostic — it uses the `LanguageSpec` to know what to look for.

### 3.1 parse_file() function

```python
def parse_file(content: str, filename: str, language: str) -> list[Symbol]:
    """
    Parse source code and extract symbols using tree-sitter.

    Args:
        content: Raw source code
        filename: File path (for ID generation)
        language: Language name (must be in LANGUAGE_REGISTRY)

    Returns:
        List of Symbol objects
    """
```

### 3.2 Algorithm

```
1. Look up LanguageSpec from LANGUAGE_REGISTRY[language]
2. Get tree-sitter Language object from tree_sitter_language_pack.get_language(spec.ts_language)
3. Create Parser, set language, parse content bytes
4. Walk the CST root node recursively:
   a. For each node, check if node.type is in spec.symbol_node_types
   b. If yes:
      - Extract name using spec.name_fields[node.type]
      - Extract parameters using spec.param_fields.get(node.type)
      - Extract return type using spec.return_type_fields.get(node.type)
      - Build signature string from these parts
      - Extract docstring using the spec.docstring_strategy
      - Extract decorators using spec.decorator_node_type
      - Determine if inside a container (parent class/impl) for qualified naming
      - Compute byte_offset = node.start_byte, byte_length = node.end_byte - node.start_byte
      - Compute line = node.start_point[0] + 1, end_line = node.end_point[0] + 1
      - Create Symbol object
   c. Check for constant patterns (spec.constant_patterns)
   d. Recurse into children
5. Return list of Symbol objects
```

### 3.3 Signature Construction

Build signature from AST components rather than regex on source text:

```python
def build_signature(node, spec, source_bytes) -> str:
    """Build clean signature from AST node."""
    # Get the text from start of node to end of parameters (or first line)
    # This gives us: "def foo(x: int, y: str) -> bool:" for Python
    # Or: "func (s *Server) Handle(ctx context.Context) error" for Go

    start = node.start_byte
    # Find end of signature (before body)
    body = node.child_by_field_name("body")
    if body:
        end = body.start_byte
    else:
        end = node.end_byte

    sig_bytes = source_bytes[start:end].strip()
    sig_text = sig_bytes.decode("utf-8").strip()

    # Clean up: remove trailing '{', ':', etc.
    sig_text = sig_text.rstrip('{: \n\t')
    return sig_text
```

### 3.4 Docstring Extraction by Strategy

#### `next_sibling_string` (Python)
```python
# Look at first statement in body block
body = node.child_by_field_name("body")
if body and body.child_count > 0:
    first_stmt = body.children[0]
    if first_stmt.type == "expression_statement":
        expr = first_stmt.children[0]
        if expr.type == "string":
            docstring = source_bytes[expr.start_byte:expr.end_byte].decode()
            # Strip quotes
            docstring = docstring.strip('"""').strip("'''").strip('"').strip("'")
```

#### `preceding_comment` (JS, TS, Go, Rust, Java)
```python
# Walk backwards through siblings to find comment(s) immediately preceding
prev = node.prev_named_sibling
comments = []
while prev and prev.type in ("comment", "line_comment", "block_comment"):
    comments.insert(0, source_bytes[prev.start_byte:prev.end_byte].decode())
    prev = prev.prev_named_sibling

if comments:
    docstring = "\n".join(comments)
    # Strip comment markers: //, /*, */, /**, ///, //!
    docstring = clean_comment_markers(docstring)
```

---

## 4. tree-sitter Integration

### 4.1 Dependency

**Package**: `tree-sitter-language-pack`

This package provides:
- Pre-compiled tree-sitter grammars for 165+ languages
- Pre-compiled wheels for Windows, macOS, Linux
- A simple API: `get_language(name)` and `get_parser(name)`

### 4.2 Usage Pattern

```python
from tree_sitter_language_pack import get_language, get_parser

# Get a parser for Python
parser = get_parser("python")

# Parse source code
tree = parser.parse(source_bytes)

# Walk the tree
root = tree.root_node
```

### 4.3 Key tree-sitter Concepts

- **Node**: Every element in the syntax tree. Has `type`, `start_byte`, `end_byte`, `start_point` (row, col), `end_point`, `children`, `child_by_field_name()`.
- **Named nodes**: Significant syntax elements (identifiers, expressions). Access via `named_children`.
- **Field names**: Named child relationships (e.g., a function node has a `name` field and a `body` field).
- **Parser**: Takes source bytes, produces a tree. One parser per language.

### 4.4 Error Handling

tree-sitter is very robust — it produces a tree even for syntactically invalid code. Nodes with errors have `has_error = True`. We skip symbols that contain error nodes.

---

## 5. Storage Schema

### 5.1 Storage Location

`~/.code-index/` (separate from docs-mcp's `~/.doc-index/`)

### 5.2 Directory Layout

```
~/.code-index/
├── owner-repo.json              # Index JSON for owner/repo
├── owner-repo/                  # Raw source files directory
│   ├── src/
│   │   ├── main.py
│   │   └── utils.py
│   └── lib/
│       └── helper.go
├── another-owner-another-repo.json
└── another-owner-another-repo/
    └── ...
```

### 5.3 Index JSON

See SPEC.md Section 3.3 for the full schema. Key points:
- Symbols stored as dicts **without source content** (content is read from raw files)
- Each symbol has `byte_offset` and `byte_length` for efficient content retrieval
- File is typically 50-200KB for a large repo

### 5.4 Content Retrieval via Byte Offsets

**Improvement over docs-mcp**: Instead of re-parsing the file to find a section, directly read the relevant bytes:

```python
def get_symbol_content(self, owner: str, name: str, symbol_id: str) -> Optional[str]:
    """Read symbol source using stored byte offsets."""
    index = self.load_index(owner, name)
    symbol = index.get_symbol(symbol_id)
    if not symbol:
        return None

    file_path = self._content_dir(owner, name) / symbol["file"]
    with open(file_path, "rb") as f:
        f.seek(symbol["byte_offset"])
        source_bytes = f.read(symbol["byte_length"])
    return source_bytes.decode("utf-8")
```

This is O(1) — no re-parsing, no scanning, just a seek + read.

---

## 6. Comparison: docs-mcp → codemunch-mcp

This table maps every component from github-docs-mcp to its counterpart in jcodemunch-mcp. Use this to understand the architectural parallels.

| docs-mcp | codemunch-mcp | Notes |
|----------|---------------|-------|
| `Section` dataclass | `Symbol` dataclass | Section has title/content/depth; Symbol has name/signature/kind/docstring |
| `RepoIndex` dataclass | `CodeIndex` dataclass | Same pattern, different fields |
| `parser/markdown.py` | `parser/extractor.py` + `parser/languages.py` | Regex → tree-sitter. Language specs are new. |
| `parser/hierarchy.py` | `parser/hierarchy.py` | Same pattern: `SectionNode` → `SymbolNode` |
| `storage/index_store.py` | `storage/index_store.py` | Same pattern but byte-offset reads instead of re-parse |
| `summarizer/batch_summarize.py` | `summarizer/batch_summarize.py` | Same AI batching, adds docstring tier |
| `tools/index_repo.py` | `tools/index_repo.py` | git/trees API instead of recursive contents API |
| `tools/list_repos.py` | `tools/list_repos.py` | Nearly identical |
| `tools/get_toc.py` → `get_toc` | `tools/get_file_outline.py` | Sections → Symbols per file |
| `tools/get_toc.py` → `get_toc_tree` | `tools/get_file_tree.py` | Section tree → File tree |
| `tools/get_section.py` → `get_section` | `tools/get_symbol.py` → `get_symbol` | Section content → Symbol source |
| `tools/get_section.py` → `get_sections` | `tools/get_symbol.py` → `get_symbols` | Batch retrieval |
| `tools/search_sections.py` | `tools/search_symbols.py` | Same scoring pattern, adds kind/file filters |
| `server.py` | `server.py` | Same pattern: 7 tools, dispatch |
| `~/.doc-index/` | `~/.code-index/` | Separate storage |
| `parse_github_url()` | `parse_github_url()` | Identical |
| `discover_doc_files()` | `discover_source_files()` | Recursive walk → git/trees single call |
| `extract_keywords()` | `extract_keywords()` | Adapted for code identifiers |
| `slugify()` | `slugify()` | Identical |

---

## 7. Key Design Decisions

### 7.1 Why `tree-sitter-language-pack` (not individual grammars)?
- One `pip install` instead of 6 separate grammar packages
- Pre-compiled wheels (no build step, no C compiler needed)
- 165+ languages available for future expansion
- Consistent API across all languages

### 7.2 Why `pathspec` for .gitignore matching?
- Full `.gitignore` spec compliance (negation, directory-only, etc.)
- Battle-tested (used by major Python tools)
- Simple API: `pathspec.PathSpec.from_lines("gitwildmatch", lines)`

### 7.3 Why `git/trees?recursive=1` instead of recursive `contents/` calls?
- **1 API call** instead of potentially dozens (one per directory)
- Drastically reduces GitHub API rate limit consumption
- Returns ALL paths including sizes, enabling client-side filtering
- docs-mcp's recursive approach was its biggest performance bottleneck

### 7.4 Why byte-offset content retrieval instead of re-parsing?
- docs-mcp re-parses the entire file to extract one section (O(n))
- codemunch-mcp seeks to the byte offset and reads exactly the needed bytes (O(1))
- For large files with many symbols, this is significantly faster

### 7.5 Why three-tier summarization?
- **Docstring (free)**: Most well-written code already has docstrings. Using them costs nothing.
- **AI (Haiku)**: For code without docstrings, AI generates better summaries than pure heuristics. Haiku is cheap and fast.
- **Signature fallback**: Always have a summary, even without API keys. The signature itself tells you what a function does.

### 7.6 Why separate storage from docs-mcp?
- Independent operation — either server can be installed alone
- Different index schemas (symbols vs sections)
- Avoids naming collisions if same repo is indexed by both
- Clean separation of concerns

