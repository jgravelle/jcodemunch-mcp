# Language Support

## Supported Languages

### Full symbol extraction

| Language          | Extensions                                      | Parser                        | Symbol Types                                                                               | Decorators     | Docstrings                    | Notes / Limitations                                                                         |
| ----------------- | ----------------------------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------ | -------------- | ----------------------------- | ------------------------------------------------------------------------------------------- |
| Python            | `.py`                                           | tree-sitter-python            | function, class, method, constant, type, field                                             | `@decorator`   | Triple-quoted strings         | Type aliases require Python 3.12+ syntax; `field` symbols emitted for dataclass / attrs / Pydantic class fields |
| JavaScript        | `.js`, `.mjs`, `.cjs`, `.jsx`                   | tree-sitter-javascript        | function, class, method, constant                                                          | —              | `//` and `/** */` comments    | Anonymous arrow functions without assigned names are not indexed                            |
| TypeScript        | `.ts`                                           | tree-sitter-typescript        | function, class, method, constant, type                                                    | `@decorator`   | `//` and `/** */` comments    | Decorator extraction depends on Stage-3 decorator syntax                                    |
| TSX               | `.tsx`                                          | tree-sitter-tsx               | function, class, method, type (interface/enum/alias)                                       | `@decorator`   | `//` and `/** */` comments    | JSX-aware TypeScript; separate grammar from `.ts`                                           |
| Go                | `.go`                                           | tree-sitter-go                | function, method, type, constant                                                           | —              | `//` comments                 | No class hierarchy (language limitation)                                                    |
| Rust              | `.rs`                                           | tree-sitter-rust              | function, type (struct/enum/trait), impl, constant                                         | `#[attr]`      | `///` and `//!` comments      | Macro-generated symbols are not visible to the parser                                       |
| Java              | `.java`                                         | tree-sitter-java              | method, class, type (interface/enum), constant                                             | `@Annotation`  | `/** */` Javadoc              | Deep inner-class nesting may be flattened                                                   |
| PHP               | `.php`                                          | tree-sitter-php               | function, class, method, type (interface/trait/enum), constant                             | `#[Attribute]` | `/** */` PHPDoc               | PHP 8+ attributes supported; language-file `<?php` tag required                             |
| Dart              | `.dart`                                         | tree-sitter-dart              | function, class (class/mixin/extension), method, type (enum/typedef)                       | `@annotation`  | `///` doc comments            | Constructors and top-level constants are not indexed                                        |
| C#                | `.cs`                                           | tree-sitter-csharp            | class (class/record), method (method/constructor/destructor), type (interface/enum/struct/delegate), constant (property/field/event) | `[Attribute]`  | `/// <summary>` XML doc       | Attributes attached via `decorator_from_children`; auto-properties and event handlers extracted as constants |
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
| Svelte            | `.svelte`                                       | custom `<script>` extraction  | class (synthetic component), function, type, constant (runes/props/reactive labels)       | varies         | `//` and `/* */` comments     | Instance + module `<script>` blocks re-parsed as JS/TS; Svelte 5 runes (`$state`/`$derived`/`$props`), Svelte 4 `export let` props and `$:` reactive labels surfaced |
| Blade (Laravel)   | `.blade.php`                                    | regex-based                   | type (section, component, extends, stack, push, slot)                                      | —              | —                             | No tree-sitter grammar; regex scanning of `@directive` syntax                               |
| EJS               | `.ejs`                                          | regex-based                   | function, template                                                                         | —              | —                             | JS extracted from `<% %>` blocks; synthetic template symbol ensures file is always indexed  |
| Assembly          | `.asm`, `.s`, `.S`, `.inc`, `.65816`, `.z80`, `.spc`, `.6502` | regex-based           | function (label/macro/proc), class (section), constant (define/equ), type (struct)         | —              | `;` preceding comments        | Multi-dialect: WLA-DX, NASM, GAS, CA65; local `_`-prefixed labels excluded                 |
| AutoHotkey v2     | `.ahk`, `.ahk2`                                 | regex-based                   | function, class, method (including `static`)                                               | —              | `;` preceding comments        | No tree-sitter grammar available; same-line `{` or `=>` required for declaration detection  |
| XML/XUL           | `.xml`, `.xul`                                   | tree-sitter-xml               | type (root element), constant (id attributes), function (script refs)                      | —              | `<!-- -->` preceding comments | XUL is parsed as XML; root, id-attributed elements, and `<script src>` refs are extracted   |
| AL (Business Central) | `.al` | regex (custom) | class (table/page/codeunit/report/xmlport/query/extensions), type (enum/interface), method (procedure/trigger), constant (field) | `[Attribute]` | `/// <summary>` XML doc comments | No tree-sitter grammar available; regex-based extraction |
| CSS               | `.css`                                          | tree-sitter-css + custom walker | function (`@keyframes`), class (rule-set selectors), type (`@media`/`@supports`) | — | `/* */` and `//` comments | Selector-based extraction; universal selectors (`*`) skipped |
| SCSS              | `.scss`                                         | tree-sitter-scss + custom walker | function (`@mixin`/`@function`/`@include`), class (selectors/`%placeholder`), type (`@media`/`@supports`), constant (`$variable`) | — | `//` and `/* */` comments | Full SCSS extraction including variables and nested rules |
| SASS              | `.sass`                                         | text search only (no grammar)   | — (files indexed for text search) | — | — | Indented SASS syntax; no tree-sitter-sass grammar in language-pack; falls back to CSS parser which cannot handle indented syntax → no symbols emitted |
| YAML              | `.yaml`, `.yml`                                 | custom dict walker (pyyaml)     | function/type/constant (structural keys and containers extracted by depth/shape) | — | — | Generic YAML; Ansible-specific YAML detected via path heuristics and routed to the Ansible parser instead |
| TOML              | `.toml`                                         | tree-sitter-toml + custom walker | type (table), class (array table), constant (key-value pair) | — | `#` preceding comments | Tables (`[section]`) as types, array tables (`[[section]]`) as classes, key-value pairs as constants; nested paths via dotted qualified names |
| Ansible           | `.yaml`, `.yml` (path-detected)                 | custom dict walker (pyyaml)     | class (play names), function (task/handler/role names), constant (variable keys) | — | — | Detected via path heuristics (tasks/, handlers/, group_vars/, site.yml, etc.); requires pyyaml |
| OpenAPI / Swagger | `.openapi.yaml`, `.openapi.json`, `.swagger.yaml`, `.swagger.json`, `openapi.yaml`, `swagger.json` | custom dict walker (pyyaml + json) | function (path operations: `GET /users`, `POST /orders/{id}`), type (component schemas / v2 definitions) | — | — | Supports OpenAPI 3.x and Swagger 2.0; requires pyyaml for YAML variants |
| JSON              | `.json`                                         | custom json walker (stdlib)     | constant (top-level object keys)                                                           | — | — | Compound extensions (`.openapi.json`, `.swagger.json`) and well-known basenames are routed to the OpenAPI parser first |
| Markdown          | `.md`, `.markdown`                              | tree-sitter-markdown            | heading (ATX `#`-style and setext underlined, parented by level), code_block (fenced ``` / ~~~, named by info-string language) | — | First paragraph of the section | Document outline; heading source ranges cover the whole section (get_symbol_source returns section text); `.mdx` not routed (no JSX-aware grammar); files with no headings/fences stay text-search only |
| Pascal / Delphi   | `.pas`, `.dpr`, `.dpk`, `.lpr`, `.pp`           | tree-sitter-pascal              | function (procedure/function), class, type (record/enum), constant                        | —              | `//` and `{ }` comments       | Object Pascal and Delphi constructs; methods inside class declarations extracted           |
| MATLAB / Octave   | `.mat`, `.mlx`, `.m`*                           | tree-sitter-matlab              | function, class (classdef), method                                                         | —              | `%` comments                  | `.m` disambiguation: MATLAB if path contains `matlab/`, `toolbox/`, `simulink/`; else Objective-C |
| Ada               | `.adb`, `.ads`                                  | tree-sitter-ada                 | function (function/procedure), class (package), type, constant                             | —              | `--` preceding comments       | Package-qualified names with `::` separator                                                 |
| COBOL             | `.cob`, `.cbl`, `.cpy`                          | regex-based                     | class (PROGRAM-ID), function (paragraph/section), constant (01-level data items)           | —              | `*` column 7 comments         | Regex extraction (tree-sitter grammar loses paragraph names)                                |
| Common Lisp       | `.lisp`, `.cl`, `.lsp`, `.asd`                  | tree-sitter-commonlisp          | function (defun/defmacro/defmethod), class (defclass/defstruct), constant (defvar/defconstant/defparameter) | — | `;;` comments | S-expression based; `defgeneric` treated as function                           |
| Racket            | `.rkt`, `.rktl`, `.rktd`                        | Racket reader in Python (`parser/racket_reader.py`) | function (define/define-syntax/define-syntax-rule/define-syntax-parse-rule/define-inline/define-check), constant (value define/define-values), class (struct/define-struct/module+/class), type (define-type/define-signature/gen:name/define-syntax-class), method (define/public etc.) | — | `;;` and `#\| \|#` comments directly above the form | Read by a Racket reader written in Python — not tree-sitter, because a `#lang` line selects a *reader* and a grammar cannot follow it — and measured against `read-syntax` node for node (`benchmarks/racket_fidelity/run_reader_fidelity.py`). **The `#lang` line is read first**: S-expression langs are walked; `at-exp` langs are read with `@` as the command character, as `#lang at-exp` reads them; document langs (`scribble/*`, `pollen`, `punct`, `markdown` …) and any lang not on the built-in lists yield no symbols and stay text-searchable — declare a project's own lang with `racket_langs` in `.jcodemunch.jsonc`. `.scrbl` is deliberately not claimed. A macro's own name is indexed, but **names created by *invoking* a macro are not** (`(define-lgetter second 2)` defines a real exported `second` that cannot be seen statically); a project's own defining macros (`defstep`, `defstudy`) can be declared via `racket_definition_forms`, and a change to either key re-parses the index once. A `provide` rename is not represented, so `(rename-out [greet say-hello])` indexes `greet`. Names the file never spells are synthesised where the expander confirms them: struct accessors, predicates and setters (`(struct posn (x y))` yields `posn`, `posn?`, `posn-x`, `posn-y`), `define-generics` methods and `gen:name`, `define-logger`'s `log-name-<level>`, all pointing at the form that generates them. `require` edges resolve for string paths (relative to the importing file) and for collection paths through the `info.rkt` collection map (`(require foo/bar)` → `foo-lib/bar.rkt`), every module path inside `for-syntax`/`for-meta`/`combine-in` included. A read error marks its form and resumes at the next column-0 form, so a stray paren costs that form, never the rest of the file. Needs no Racket install. Measured against Racket's expander: 89.7% of definitions found, 0 names that do not exist — see `benchmarks/racket_fidelity/` |
| Solidity          | `.sol`                                          | tree-sitter-solidity            | class (contract/library), type (interface/struct/enum/event/error), function (function/modifier), constant (state variable) | — | `//` and `/* */` comments | Contract-scoped qualified names; events and modifiers extracted                 |
| Zig               | `.zig`, `.zon`                                  | tree-sitter-zig                 | function, class (struct), type (enum/union), constant, function (test declarations)        | —              | `//` comments                 | PascalCase AST node names; `test "name"` blocks extracted as functions                      |
| PowerShell        | `.ps1`, `.psm1`, `.psd1`                        | tree-sitter-powershell          | function, class, method (class methods), type (enum)                                       | —              | `#` comments                  | Verb-Noun naming convention preserved (e.g. `Get-UserInfo`)                                 |
| Apex (Salesforce)  | `.cls`, `.trigger`                              | tree-sitter-apex                | class, type (interface/enum), method, function (trigger)                                   | `@annotation`  | `//` and `/* */` comments     | Java-like AST; trigger declarations extracted as top-level functions                        |
| OCaml             | `.ml`, `.mli`                                   | tree-sitter-ocaml               | function (let bindings with params), class (module/class), type, constant (let bindings without params) | — | `(* *)` comments | Module-scoped nested definitions; `let rec` supported                              |
| PL/SQL            | `.pls`, `.plb`, `.pck`, `.pkb`, `.pks`          | (routed to SQL parser)          | (same as SQL)                                                                              | —              | `--` and `/* */` comments     | PL/SQL file extensions routed to the existing SQL parser                                    |
| F#                | `.fs`, `.fsi`, `.fsx`                           | tree-sitter-fsharp              | function (`let` with params), class (module), type (record/union/enum), constant (`let` without params) | — | `//` and `(* *)` comments | Module-scoped nesting; return type annotations preserved in signatures |
| Clojure           | `.clj`, `.cljs`, `.cljc`, `.edn`                | tree-sitter-clojure             | function (defn/defmacro/defmulti), type (defprotocol/defrecord/deftype), constant (def)    | —              | `;;` comments                 | Namespace-qualified names (`ns/symbol`); parameter vectors in signatures                   |
| Emacs Lisp        | `.el`                                           | tree-sitter-elisp               | function (defun/defmacro), constant (defvar/defconst/defcustom)                            | —              | `;;` comments                 | Docstrings extracted from first string after parameter list                                  |
| Nim               | `.nim`, `.nims`, `.nimble`                      | tree-sitter-nim                 | function (proc/func/template/macro/method/iterator), type, constant (var/let/const)        | —              | `#` comments                  | Signature includes keyword (proc/func/template/macro); exported `*` suffix stripped        |
| Tcl               | `.tcl`, `.tk`, `.itcl`                          | tree-sitter-tcl                 | function (proc), class (namespace eval)                                                    | —              | `#` comments                  | Namespace nesting with `::` separator; nested procs inside namespace bodies                |
| D                 | `.d`, `.di`                                     | tree-sitter-d                   | function, class (class/struct/interface), type (enum), function (template)                  | —              | `//` and `/* */` comments     | Nested method extraction inside class/struct bodies; qualified names via scope              |
| Razor / Blazor    | `.cshtml`, `.razor`                             | regex + C#/HTML delegation      | class, function, method, constant                                                          | `[Attribute]`  | `///` and `//` in code blocks | `@functions`/`@code` blocks re-parsed as C#; `@page`/`@inject`/`@model`/`@using` → constants; HTML `id=` attributes captured |
| Astro             | `.astro`                                        | frontmatter + `<script>` delegation | class, function, type, constant                                                       | varies         | varies                        | TS/JS frontmatter (`---` fences) + inline `<script>` re-parsed as TS/JS; template `id=` → constants; frontmatter + PascalCase component-usage import edges |
| Arduino           | `.ino`, `.pde`                                  | tree-sitter-arduino             | function, class, type (struct/enum/union), constant                                        | —              | `/* */` and `//` comments     | C++ superset grammar; `.ino`/`.pde` sketches; `#define` macros as constants                 |
| VHDL              | `.vhd`, `.vhdl`, `.vho`, `.vhs`                 | regex-based                     | class (architecture), type (entity/package), function (process/function/procedure), constant (signal) | — | `--` preceding comments       | No tree-sitter grammar; regex extraction; qualified `entity.architecture` names             |
| Verilog / SystemVerilog | `.v`, `.vh`, `.sv`, `.svh`                | regex-based                     | class (module/class), type (interface), function (function/task), constant                 | —              | `//` and `/* */` comments     | No tree-sitter grammar; regex extraction; module/class/interface/package/function/task constructs |
| Haskell           | `.hs`, `.lhs`                                   | tree-sitter-haskell             | function, type (data/newtype/type synonym/class)                                           | —              | `--` and `{- -}` comments     | Minimal extraction (names via generic walk); full custom parser deferred                    |
| Julia             | `.jl`                                           | regex/custom                    | function (function/macro), class (module), type (struct/abstract)                          | —              | `#` and `"""` docstrings      | Names nested in signature nodes; custom `_parse_julia_symbols`; module-qualified names       |
| Luau              | `.luau`                                         | tree-sitter-luau                | function, type                                                                             | —              | `--` and `--[[` comments      | Roblox Luau (typed Lua variant); `.lua` uses tree-sitter-lua separately                     |

\* `.h` uses C++ parsing first, then falls back to C when no C++ symbols are extracted.
\*\* `.m` defaults to Objective-C unless the file path contains MATLAB indicators (`matlab/`, `toolbox/`, `simulink/`).

### Text search indexing (symbol extraction planned)

These languages are fully indexed and searchable via `search_text`. Symbol extraction is minimal or pending a custom extractor.

| Language | Extensions     | Notes                                                              |
| -------- | -------------- | ------------------------------------------------------------------ |
| R        | `.r`           | Functions are name-bound values (`f <- function(){}`); custom extractor planned |
| LESS     | `.less`        | No tree-sitter-less grammar in language-pack; indexed for text search |
| Stylus   | `.styl`        | No tree-sitter-stylus grammar in language-pack; indexed for text search |

### Templating engines (over an underlying language)

A template file named `name.<underlying-ext>.<engine-ext>` is indexed by masking
the engine's constructs (offset-preserving) and re-parsing the body as its
underlying language — so a Jinja2 template of TypeScript (`foo.ts.j2`) yields the
real TypeScript symbols, with correct line/byte positions. The **underlying
language is inferred from the middle extension**, so any language above works as
the body. A bare template with no underlying extension (`report.j2`) is skipped.

| Engine     | Extensions                          | Notes                                                                 |
| ---------- | ----------------------------------- | --------------------------------------------------------------------- |
| Jinja2     | `.j2`, `.jinja`, `.jinja2`          | `{% macro %}` / `{% block %}` also surfaced as symbols                 |
| Twig       | `.twig`                             | Shares Jinja delimiters; macro/block extraction applies               |

The engine registry (`parser/template_shared.py`) is pluggable. The first cut
ships Jinja2 and Twig — the engines whose `name.<lang>.<engine>` double-extension
convention this feature targets. Single-extension HTML-bodied engines
(Handlebars/Liquid/Mustache — `page.hbs`, `index.liquid`) carry no underlying
extension to resolve and can be added on demand.

Caveat (best-effort, same as dbt SQL): a template hole at a *name* position
(`function {{ name }}()`) erases that symbol's name, and free template text
emitted inside a block body can disrupt the declaration immediately after it.
EJS (`.ejs`) keeps its own dedicated parser.

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
JCODEMUNCH_EXTRA_EXTENSIONS=".cgi:perl,.psgi:perl,.jsm:javascript"
```

- Comma-separated `.ext:lang` pairs
- Overrides built-in mappings on collision
- Unknown languages and malformed entries are skipped with a warning
- Valid language names: `ada`, `al`, `ansible`, `apex`, `arduino`, `asm`, `astro`, `autohotkey`, `bash`, `blade`, `c`, `clojure`, `cobol`, `commonlisp`, `cpp`, `csharp`, `css`, `dart`, `dlang`, `ejs`, `elisp`, `elixir`, `erlang`, `fortran`, `fsharp`, `gdscript`, `gleam`, `go`, `graphql`, `groovy`, `haskell`, `hcl`, `java`, `javascript`, `json`, `julia`, `kotlin`, `less`, `lua`, `luau`, `markdown`, `matlab`, `nim`, `nix`, `objc`, `ocaml`, `openapi`, `pascal`, `perl`, `php`, `powershell`, `proto`, `python`, `r`, `racket`, `razor`, `ruby`, `rust`, `sass`, `scala`, `scss`, `solidity`, `sql`, `styl`, `svelte`, `swift`, `tcl`, `toml`, `tsx`, `typescript`, `verilog`, `verse`, `vhdl`, `vue`, `xml`, `yaml`, `zig`

Set via `.mcp.json` `env` block or any environment mechanism supported by your MCP client.
