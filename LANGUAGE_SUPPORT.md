# Language Support

## Supported Languages

### Full symbol extraction

| Language          | Extensions                                      | Parser                        | Symbol Types                                                                               | Decorators     | Docstrings                    | Notes / Limitations                                                                         |
| ----------------- | ----------------------------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------ | -------------- | ----------------------------- | ------------------------------------------------------------------------------------------- |
| Python            | `.py`                                           | tree-sitter-python            | function, class, method, constant, type                                                    | `@decorator`   | Triple-quoted strings         | Type aliases require Python 3.12+ syntax for full fidelity                                  |
| JavaScript        | `.js`, `.jsx`                                   | tree-sitter-javascript        | function, class, method, constant                                                          | —              | `//` and `/** */` comments    | Anonymous arrow functions without assigned names are not indexed                            |
| TypeScript        | `.ts`                                           | tree-sitter-typescript        | function, class, method, constant, type                                                    | `@decorator`   | `//` and `/** */` comments    | Decorator extraction depends on Stage-3 decorator syntax                                    |
| TSX               | `.tsx`                                          | tree-sitter-tsx               | function, class, method, type (interface/enum/alias)                                       | `@decorator`   | `//` and `/** */` comments    | JSX-aware TypeScript; separate grammar from `.ts`                                           |
| Go                | `.go`                                           | tree-sitter-go                | function, method, type, constant                                                           | —              | `//` comments                 | No class hierarchy (language limitation)                                                    |
| Rust              | `.rs`                                           | tree-sitter-rust              | function, type (struct/enum/trait), impl, constant                                         | `#[attr]`      | `///` and `//!` comments      | Macro-generated symbols are not visible to the parser                                       |
| Java              | `.java`                                         | tree-sitter-java              | method, class, type (interface/enum), constant                                             | `@Annotation`  | `/** */` Javadoc              | Deep inner-class nesting may be flattened                                                   |
| PHP               | `.php`                                          | tree-sitter-php               | function, class, method, type (interface/trait/enum), constant                             | `#[Attribute]` | `/** */` PHPDoc               | PHP 8+ attributes supported; language-file `<?php` tag required                             |
| Dart              | `.dart`                                         | tree-sitter-dart              | function, class (class/mixin/extension), method, type (enum/typedef)                       | `@annotation`  | `///` doc comments            | Constructors and top-level constants are not indexed                                        |
| C#                | `.cs`                                           | tree-sitter-csharp            | class (class/record), method (method/constructor), type (interface/enum/struct/delegate)   | `[Attribute]`  | `/// <summary>` XML doc       | Properties and `const` fields not indexed                                                   |
| C                 | `.c`                                            | tree-sitter-c                 | function, type (struct/enum/union), constant                                               | —              | `/* */` and `//` comments     | `#define` macros extracted as constants; no class/method hierarchy                          |
| C++               | `.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`, `.hxx`, `.h`* | tree-sitter-cpp           | function, class, method, type (struct/enum/union/alias), constant                         | —              | `/* */` and `//` comments     | Namespace symbols used for qualification but not emitted as standalone                      |
| Swift             | `.swift`                                        | tree-sitter-swift             | function, class (class/struct/enum/extension), method (init/deinit), type (protocol/typealias), constant | — | `///` and `/* */` | Decorators not extracted (live inside modifiers node)                              |
| Elixir            | `.ex`, `.exs`                                   | tree-sitter-elixir            | class (defmodule/defimpl), type (defprotocol/@type/@callback), method (def/defp/defmacro/defguard), function | — | `@doc`/`@moduledoc` strings | Homoiconic grammar; custom walker. `defstruct`, `use`, `import`, `alias` not indexed |
| Ruby              | `.rb`, `.rake`                                  | tree-sitter-ruby              | class, type (module), method (instance + `self.` singleton), function (top-level def)     | —              | `#` preceding comments        | `attr_accessor`, constants, and `include`/`extend` not indexed                              |
| Perl              | `.pl`, `.pm`, `.t`                              | tree-sitter-perl              | function (subroutine), class (package)                                                     | —              | `#` preceding comments        | Parameter extraction not supported                                                          |
| Kotlin            | `.kt`, `.kts`                                   | tree-sitter-kotlin            | function, class (class/interface/enum/data class/object), type (alias)                     | —              | `//` and `/** */` comments    | Annotations live inside modifiers; captured in signature                                    |
| Gleam             | `.gleam`                                        | tree-sitter-gleam             | function, type (definition/alias), constant                                                | —              | `//` preceding comments       | —                                                                                           |
| Bash              | `.sh`, `.bash`                                  | tree-sitter-bash              | function, constant (`readonly`/`declare -r`)                                               | —              | `#` preceding comments        | Only named function definitions indexed                                                     |
| GDScript          | `.gd`                                           | tree-sitter-gdscript          | function, class, type (enum), function (signal)                                            | `@annotation`  | `#` preceding comments        | Godot 4 GDScript                                                                            |
| Scala             | `.scala`, `.sc`                                 | tree-sitter-scala             | function, class (class/object), type (trait/enum), constant (val/var)                     | `@annotation`  | `//` and `/** */` comments    | —                                                                                           |
| Lua               | `.lua`                                          | tree-sitter-lua               | function, method                                                                           | —              | `--` and `--[[` comments      | Handles local, `Module.method` (dot), and `Module:method` (OOP) forms                      |
| Erlang            | `.erl`, `.hrl`                                  | tree-sitter-erlang            | function, type, constant (macro/define), type (record)                                     | —              | `%` preceding comments        | Multi-clause functions deduplicated by (name, arity)                                        |
| Fortran           | `.f90`, `.f95`, `.f03`, `.f08`, `.f`, `.for`, `.fpp` | tree-sitter-fortran      | function (subroutine/function), class (module/program)                                     | —              | `!` preceding comments        | Modern and legacy Fortran dialects                                                          |
| SQL               | `.sql`                                          | tree-sitter-sql               | function (CREATE FUNCTION/CTE), type (CREATE TABLE/VIEW/SCHEMA/INDEX)                      | —              | `--` and `/* */` comments     | Jinja-templated SQL (dbt models) auto-preprocessed; PROCEDURE and TRIGGER not supported    |
| Verse (UEFN)      | `.verse`                                        | regex-based                   | class, method, function, variable, constant                                                | —              | `#` preceding comments        | Optimized for Epic's UEFN API digest files; 99.9% token reduction vs raw file load         |
| Objective-C       | `.m`, `.mm`                                     | tree-sitter-objc              | class (interface/implementation), method                                                   | —              | `/* */` and `//` comments     | Selector-based method naming via custom extractor                                           |
| Protocol Buffers  | `.proto`                                        | tree-sitter-proto             | type (message/enum), function (service/rpc)                                                | —              | `//` and `/* */` comments     | message, service, rpc, and enum definitions extracted                                       |
| HCL / Terraform   | `.tf`, `.hcl`, `.tfvars`                        | tree-sitter-hcl               | type (resource/data/module/variable/output/locals)                                         | —              | `#` and `/* */` comments      | Block types used as symbol kinds; Terraform-aware                                           |
| GraphQL           | `.graphql`, `.gql`                              | tree-sitter-graphql           | type (type/input/interface/union/enum/scalar), function (query/mutation/subscription/fragment) | — | `#` comments              | SDL and query document support                                                              |
| Groovy            | `.groovy`, `.gradle`                            | tree-sitter-groovy            | function, class, method                                                                    | —              | `//` and `/* */` comments     | Custom extractor; Gradle build scripts included                                             |
| Nix               | `.nix`                                          | tree-sitter-nix               | function (let bindings), constant                                                          | —              | `#` preceding comments        | Expression language; binding-based extraction                                               |
| Vue               | `.vue`                                          | custom `<script>` extraction  | function, class, method, type, constant (from `<script>` block)                           | varies         | varies                        | Script block re-parsed as JavaScript or TypeScript (detected from `lang="ts"`)             |
| Blade (Laravel)   | `.blade.php`                                    | regex-based                   | type (section, component, extends, stack, push, slot)                                      | —              | —                             | No tree-sitter grammar; regex scanning of `@directive` syntax                               |
| EJS               | `.ejs`                                          | regex-based                   | function, template                                                                         | —              | —                             | JS extracted from `<% %>` blocks; synthetic template symbol ensures file is always indexed  |
| Assembly          | `.asm`, `.s`, `.S`, `.inc`, `.65816`, `.z80`, `.spc`, `.6502` | regex-based           | function (label/macro/proc), class (section), constant (define/equ), type (struct)         | —              | `;` preceding comments        | Multi-dialect: WLA-DX, NASM, GAS, CA65; local `_`-prefixed labels excluded                 |
| AutoHotkey v2     | `.ahk`, `.ahk2`                                 | regex-based                   | function, class, method (including `static`)                                               | —              | `;` preceding comments        | No tree-sitter grammar available; same-line `{` or `=>` required for declaration detection  |
| XML/XUL           | `.xml`, `.xul`                                   | tree-sitter-xml               | type (root element), constant (id attributes), function (script refs)                      | —              | `<!-- -->` preceding comments | XUL is parsed as XML; root, id-attributed elements, and `<script src>` refs are extracted   |
| AL (Business Central) | `.al` | regex (custom) | class (table/page/codeunit/report/xmlport/query/extensions), type (enum/interface), method (procedure/trigger), constant (field) | `[Attribute]` | `/// <summary>` XML doc comments | No tree-sitter grammar available; regex-based extraction |

\* `.h` uses C++ parsing first, then falls back to C when no C++ symbols are extracted.

### Text search indexing (symbol extraction planned)

These languages are fully indexed and searchable via `search_text`. Symbol extraction is minimal or pending a custom extractor.

| Language | Extensions     | Notes                                                              |
| -------- | -------------- | ------------------------------------------------------------------ |
| Haskell  | `.hs`, `.lhs`  | Basic node types recognized; full extraction requires custom walker |
| Julia    | `.jl`          | Function nodes detected; signature extraction in progress          |
| R        | `.r`           | Functions-as-values pattern not yet handled by generic extractor   |
| CSS      | `.css`         | Rule sets indexed; selector-as-symbol extractor planned            |
| TOML     | `.toml`        | Tables indexed; key-as-symbol extractor planned                    |

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


## Configuration

### `JCODEMUNCH_EXTRA_EXTENSIONS`

Map additional file extensions to languages at startup without modifying source:

```
JCODEMUNCH_EXTRA_EXTENSIONS=".cgi:perl,.psgi:perl,.mjs:javascript"
```

- Comma-separated `.ext:lang` pairs
- Overrides built-in mappings on collision
- Unknown languages and malformed entries are skipped with a warning
- Valid language names: `python`, `javascript`, `typescript`, `tsx`, `go`, `rust`, `java`, `php`, `dart`, `csharp`, `c`, `cpp`, `swift`, `elixir`, `ruby`, `perl`, `gdscript`, `blade`, `kotlin`, `gleam`, `bash`, `nix`, `vue`, `ejs`, `verse`, `lua`, `erlang`, `fortran`, `sql`, `scala`, `haskell`, `julia`, `r`, `css`, `toml`, `groovy`, `objc`, `proto`, `hcl`, `graphql`, `autohotkey`, `asm`, `xml`, `al`

Set via `.mcp.json` `env` block or any environment mechanism supported by your MCP client.
