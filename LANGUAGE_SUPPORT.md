# Language Support

## Supported Languages

| Language   | Extensions    | Parser                 | Symbol Types                                       | Decorators    | Docstrings                 | Notes / Limitations                                              |
| ---------- | ------------- | ---------------------- | -------------------------------------------------- | ------------- | -------------------------- | ---------------------------------------------------------------- |
| Python     | `.py`         | tree-sitter-python     | function, class, method, constant, type            | `@decorator`  | Triple-quoted strings      | Type aliases require Python 3.12+ syntax for full fidelity       |
| JavaScript | `.js`, `.jsx` | tree-sitter-javascript | function, class, method, constant                  | —             | `//` and `/** */` comments | Anonymous arrow functions without assigned names are not indexed |
| TypeScript | `.ts`, `.tsx` | tree-sitter-typescript | function, class, method, constant, type            | `@decorator`  | `//` and `/** */` comments | Decorator extraction depends on Stage-3 decorator syntax         |
| Go         | `.go`         | tree-sitter-go         | function, method, type, constant                   | —             | `//` comments              | No class hierarchy (language limitation)                         |
| Rust       | `.rs`         | tree-sitter-rust       | function, type (struct/enum/trait), impl, constant | `#[attr]`     | `///` and `//!` comments   | Macro-generated symbols are not visible to the parser            |
| Java       | `.java`       | tree-sitter-java       | method, class, type (interface/enum), constant     | `@Annotation` | `/** */` Javadoc           | Deep inner-class nesting may be flattened                        |

---

## Parser Engine

All language parsing is powered by **tree-sitter** via the `tree-sitter-language-pack` Python package, providing:

* Incremental, error-tolerant parsing
* Uniform AST representation across languages
* Pre-compiled grammars for supported languages

**Dependency:** `tree-sitter-language-pack>=0.7.0` (pinned in `pyproject.toml`)

---

## Adding a New Language

1. **Define a `LanguageSpec`** in `src/jcodemunch_mcp/parser/languages.py`:

```python
NEW_LANG_SPEC = LanguageSpec(
    ts_language="new_language",
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
    return_type_fields={},
    docstring_strategy="preceding_comment",
    decorator_node_type=None,
    container_node_types=["class_definition"],
    constant_patterns=[],
    type_patterns=[],
)
```

2. **Register the language**:

```python
LANGUAGE_REGISTRY["new_language"] = NEW_LANG_SPEC
```

3. **Map file extensions**:

```python
LANGUAGE_EXTENSIONS[".ext"] = "new_language"
```

4. **Verify parser availability**:

```python
from tree_sitter_language_pack import get_parser
get_parser("new_language")  # Must not raise
```

5. **Add parser tests**:

```python
def test_parse_new_language():
    source = "..."
    symbols = parse_file(source, "test.ext", "new_language")
    assert len(symbols) >= 2
```

---

## Inspecting AST Node Types

To inspect the node types produced by tree-sitter for a source file:

```python
from tree_sitter_language_pack import get_parser

parser = get_parser("python")
tree = parser.parse(b"def foo(): pass")

def print_tree(node, indent=0):
    print(" " * indent + f"{node.type} [{node.start_point}-{node.end_point}]")
    for child in node.children:
        print_tree(child, indent + 2)

print_tree(tree.root_node)
```

This inspection process helps identify the correct `symbol_node_types`, `name_fields`, and extraction rules when adding support for a new language.
