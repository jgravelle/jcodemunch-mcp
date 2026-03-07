"""Language registry with LanguageSpec definitions for all supported languages."""

import logging
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class LanguageSpec:
    """Specification for extracting symbols from a language's AST."""
    # tree-sitter language name (for tree-sitter-language-pack)
    ts_language: str

    # Node types that represent extractable symbols
    # Maps node_type -> symbol kind
    symbol_node_types: dict[str, str]

    # How to extract the symbol name from a node
    # Maps node_type -> child field name containing the name
    name_fields: dict[str, str]

    # How to extract parameters/signature beyond the name
    # Maps node_type -> child field name for parameters
    param_fields: dict[str, str]

    # Return type extraction (if language supports it)
    # Maps node_type -> child field name for return type
    return_type_fields: dict[str, str]

    # Docstring extraction strategy
    # "next_sibling_string" = Python (expression_statement after def)
    # "first_child_comment" = JS/TS (/** */ before function)
    # "preceding_comment" = Go/Rust/Java (// or /* */ before decl)
    docstring_strategy: str

    # Decorator/attribute node type (if any)
    decorator_node_type: Optional[str]

    # Node types that indicate nesting (methods inside classes)
    container_node_types: list[str]

    # Additional extraction: constants, type aliases
    constant_patterns: list[str]   # Node types for constants
    type_patterns: list[str]       # Node types for type definitions

    # If True, decorators are direct children of the declaration node (e.g. C#)
    # If False (default), decorators are preceding siblings (e.g. Python, Java)
    decorator_from_children: bool = False


# File extension to language mapping
LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".php": "php",
    ".dart": "dart",
    ".cs": "csharp",
    ".c": "c",
    ".h": "cpp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".swift": "swift",
    ".ex": "elixir",
    ".exs": "elixir",
    ".rb": "ruby",
    ".rake": "ruby",
    ".pl": "perl",
    ".pm": "perl",
    ".t": "perl",
    ".gd": "gdscript",
    ".blade.php": "blade",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".gleam": "gleam",
    ".sh": "bash",
    ".bash": "bash",
    ".nix": "nix",
    ".vue": "vue",
    ".ejs": "ejs",
    ".verse": "verse",
}


# Python specification
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
    constant_patterns=["assignment"],
    type_patterns=["type_alias_statement"],
)


# JavaScript specification
JAVASCRIPT_SPEC = LanguageSpec(
    ts_language="javascript",
    symbol_node_types={
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "generator_function_declaration": "function",
    },
    name_fields={
        "function_declaration": "name",
        "class_declaration": "name",
        "method_definition": "name",
    },
    param_fields={
        "function_declaration": "parameters",
        "method_definition": "parameters",
        "arrow_function": "parameters",
    },
    return_type_fields={},
    docstring_strategy="preceding_comment",
    decorator_node_type=None,
    container_node_types=["class_declaration", "class"],
    constant_patterns=["lexical_declaration"],
    type_patterns=[],
)


# TSX specification (TypeScript with JSX — requires separate grammar)
TSX_SPEC = LanguageSpec(
    ts_language="tsx",
    symbol_node_types={
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
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


# TypeScript specification
TYPESCRIPT_SPEC = LanguageSpec(
    ts_language="typescript",
    symbol_node_types={
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
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


# Go specification
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
        "type_declaration": "name",
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
    container_node_types=[],
    constant_patterns=["const_declaration"],
    type_patterns=["type_declaration"],
)


# Rust specification
RUST_SPEC = LanguageSpec(
    ts_language="rust",
    symbol_node_types={
        "function_item": "function",
        "struct_item": "type",
        "enum_item": "type",
        "trait_item": "type",
        "impl_item": "class",
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
    docstring_strategy="preceding_comment",
    decorator_node_type="attribute_item",
    container_node_types=["impl_item", "trait_item"],
    constant_patterns=["const_item", "static_item"],
    type_patterns=["struct_item", "enum_item", "trait_item", "type_item"],
)


# Java specification
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
    docstring_strategy="preceding_comment",
    decorator_node_type="marker_annotation",
    container_node_types=["class_declaration", "interface_declaration", "enum_declaration"],
    constant_patterns=["field_declaration"],
    type_patterns=["interface_declaration", "enum_declaration"],
)


# PHP specification
PHP_SPEC = LanguageSpec(
    ts_language="php",
    symbol_node_types={
        "function_definition": "function",
        "class_declaration": "class",
        "method_declaration": "method",
        "interface_declaration": "type",
        "trait_declaration": "type",
        "enum_declaration": "type",
    },
    name_fields={
        "function_definition": "name",
        "class_declaration": "name",
        "method_declaration": "name",
        "interface_declaration": "name",
        "trait_declaration": "name",
        "enum_declaration": "name",
    },
    param_fields={
        "function_definition": "parameters",
        "method_declaration": "parameters",
    },
    return_type_fields={
        "function_definition": "return_type",
        "method_declaration": "return_type",
    },
    docstring_strategy="preceding_comment",
    decorator_node_type="attribute",  # PHP 8 #[Attribute] syntax
    container_node_types=["class_declaration", "trait_declaration", "interface_declaration"],
    constant_patterns=["const_declaration"],
    type_patterns=["interface_declaration", "trait_declaration", "enum_declaration"],
)


# Dart specification
DART_SPEC = LanguageSpec(
    ts_language="dart",
    symbol_node_types={
        "function_signature": "function",
        "class_definition": "class",
        "mixin_declaration": "class",
        "enum_declaration": "type",
        "extension_declaration": "class",
        "method_signature": "method",
        "type_alias": "type",
    },
    name_fields={
        "function_signature": "name",
        "class_definition": "name",
        "enum_declaration": "name",
        "extension_declaration": "name",
        # mixin_declaration, method_signature, type_alias: special-cased in extractor
    },
    param_fields={
        "function_signature": "parameters",
    },
    return_type_fields={},
    docstring_strategy="preceding_comment",
    decorator_node_type="annotation",
    container_node_types=["class_definition", "mixin_declaration", "extension_declaration"],
    constant_patterns=[],
    type_patterns=["type_alias", "enum_declaration"],
)


# C# specification
CSHARP_SPEC = LanguageSpec(
    ts_language="csharp",
    symbol_node_types={
        "class_declaration": "class",
        "record_declaration": "class",
        "interface_declaration": "type",
        "enum_declaration": "type",
        "struct_declaration": "type",
        "delegate_declaration": "type",
        "method_declaration": "method",
        "constructor_declaration": "method",
    },
    name_fields={
        "class_declaration": "name",
        "record_declaration": "name",
        "interface_declaration": "name",
        "enum_declaration": "name",
        "struct_declaration": "name",
        "delegate_declaration": "name",
        "method_declaration": "name",
        "constructor_declaration": "name",
    },
    param_fields={
        "method_declaration": "parameters",
        "constructor_declaration": "parameters",
        "delegate_declaration": "parameters",
    },
    return_type_fields={
        "method_declaration": "returns",
    },
    docstring_strategy="preceding_comment",
    decorator_node_type="attribute_list",
    decorator_from_children=True,
    container_node_types=["class_declaration", "struct_declaration", "record_declaration", "interface_declaration"],
    constant_patterns=[],
    type_patterns=["interface_declaration", "enum_declaration", "struct_declaration", "delegate_declaration", "record_declaration"],
)


# C specification
C_SPEC = LanguageSpec(
    ts_language="c",
    symbol_node_types={
        "function_definition": "function",
        "struct_specifier": "type",
        "enum_specifier": "type",
        "union_specifier": "type",
        "type_definition": "type",
    },
    name_fields={
        "function_definition": "declarator",
        "struct_specifier": "name",
        "enum_specifier": "name",
        "union_specifier": "name",
        "type_definition": "declarator",
    },
    param_fields={
        "function_definition": "declarator",
    },
    return_type_fields={
        "function_definition": "type",
    },
    docstring_strategy="preceding_comment",
    decorator_node_type=None,
    container_node_types=[],
    constant_patterns=["preproc_def"],
    type_patterns=["type_definition", "enum_specifier", "struct_specifier", "union_specifier"],
)


# Swift specification
# Note: tree-sitter-swift uses class_declaration for class/struct/enum/extension;
# the declaration_kind child field ("class"/"struct"/"enum"/"extension") disambiguates
# at the source level but all map to "class" here for uniform treatment.
# Attributes (@discardableResult etc.) live inside a modifiers child node rather
# than as preceding siblings, so decorator extraction is not supported in this spec.
SWIFT_SPEC = LanguageSpec(
    ts_language="swift",
    symbol_node_types={
        "function_declaration": "function",
        "class_declaration": "class",    # covers class, struct, enum, extension
        "protocol_declaration": "type",
        "typealias_declaration": "type",
        "init_declaration": "method",
        "deinit_declaration": "method",
        "property_declaration": "constant",
    },
    name_fields={
        "function_declaration": "name",  # simple_identifier child
        "class_declaration": "name",     # type_identifier child
        "protocol_declaration": "name",  # type_identifier child
        "typealias_declaration": "name", # user_type child
        "init_declaration": "name",      # "init" keyword token
        "deinit_declaration": "name",    # "deinit" keyword token
        "property_declaration": "name",  # pattern child
    },
    param_fields={},  # Swift params are unnamed children; signature captured via source range
    return_type_fields={},  # return type shares field "name" with function identifier
    docstring_strategy="preceding_comment",  # /// and /* */ doc comments
    decorator_node_type=None,
    container_node_types=["class_declaration", "protocol_declaration"],
    constant_patterns=[],  # property_declaration handled via symbol_node_types
    type_patterns=["protocol_declaration", "typealias_declaration"],
)


# C++ specification
CPP_SPEC = LanguageSpec(
    ts_language="cpp",
    symbol_node_types={
        "class_specifier": "class",
        "struct_specifier": "type",
        "union_specifier": "type",
        "enum_specifier": "type",
        "type_definition": "type",
        "alias_declaration": "type",
        "function_definition": "function",
        "declaration": "function",
        "field_declaration": "function",
    },
    name_fields={
        "class_specifier": "name",
        "struct_specifier": "name",
        "union_specifier": "name",
        "enum_specifier": "name",
        "type_definition": "declarator",
        "alias_declaration": "name",
        "function_definition": "declarator",
        "declaration": "declarator",
        "field_declaration": "declarator",
    },
    param_fields={
        "function_definition": "declarator",
        "declaration": "declarator",
        "field_declaration": "declarator",
    },
    return_type_fields={
        "function_definition": "type",
        "declaration": "type",
        "field_declaration": "type",
    },
    docstring_strategy="preceding_comment",
    decorator_node_type=None,
    container_node_types=["class_specifier", "struct_specifier", "union_specifier"],
    constant_patterns=["preproc_def"],
    type_patterns=["class_specifier", "struct_specifier", "union_specifier", "enum_specifier", "type_definition", "alias_declaration"],
)


# Elixir specification
# NOTE: Elixir's tree-sitter grammar is homoiconic — all constructs (defmodule,
# def, defp, defmacro, @doc, @type, etc.) are represented as generic `call` or
# `unary_operator` nodes. Custom extraction is performed in extractor.py via
# _parse_elixir_symbols(); the fields below are intentionally empty.
ELIXIR_SPEC = LanguageSpec(
    ts_language="elixir",
    symbol_node_types={},
    name_fields={},
    param_fields={},
    return_type_fields={},
    docstring_strategy="elixir",
    decorator_node_type=None,
    container_node_types=[],
    constant_patterns=[],
    type_patterns=[],
)


# Perl specification
PERL_SPEC = LanguageSpec(
    ts_language="perl",
    symbol_node_types={
        "subroutine_declaration_statement": "function",
        "package_statement": "class",
    },
    name_fields={
        "subroutine_declaration_statement": "name",
        "package_statement": "name",
    },
    param_fields={},
    return_type_fields={},
    docstring_strategy="preceding_comment",
    decorator_node_type=None,
    container_node_types=[],
    constant_patterns=["use_statement"],
    type_patterns=[],
)


# Ruby specification
RUBY_SPEC = LanguageSpec(
    ts_language="ruby",
    symbol_node_types={
        "method": "function",           # top-level → function; inside class/module → method
        "singleton_method": "function", # def self.foo → always has class parent → method
        "class": "class",
        "module": "type",
    },
    name_fields={
        "method": "name",
        "singleton_method": "name",
        "class": "name",
        "module": "name",
    },
    param_fields={
        "method": "parameters",
        "singleton_method": "parameters",
    },
    return_type_fields={},
    docstring_strategy="preceding_comment",
    decorator_node_type=None,
    container_node_types=["class", "module"],
    constant_patterns=[],
    type_patterns=["module"],
)


# GDScript specification (Godot 4)
# GDScript is Python-like; tree-sitter-gdscript exposes named fields
# for name, parameters, and return_type on function_definition nodes.
# Annotations (@export, @onready, @tool) appear as preceding `annotation`
# siblings before the declaration they decorate.
GDSCRIPT_SPEC = LanguageSpec(
    ts_language="gdscript",
    symbol_node_types={
        "function_definition": "function",
        "class_definition": "class",
        "signal_statement": "function",
        "enum_definition": "type",
    },
    name_fields={
        "function_definition": "name",
        "class_definition": "name",
        "signal_statement": "name",
        "enum_definition": "name",
    },
    param_fields={
        "function_definition": "parameters",
        "signal_statement": "parameters",
    },
    return_type_fields={
        "function_definition": "return_type",
    },
    docstring_strategy="preceding_comment",
    decorator_node_type="annotation",
    container_node_types=["class_definition"],
    constant_patterns=["const_statement"],
    type_patterns=["enum_definition"],
)


# Blade (Laravel) specification
# NOTE: No tree-sitter grammar is available for Blade templates.
# Symbol extraction is performed by _parse_blade_symbols() in extractor.py
# via regex scanning for Blade directives (@section, @component, @extends, etc.).
# The fields below are intentionally empty.
BLADE_SPEC = LanguageSpec(
    ts_language="blade",
    symbol_node_types={},
    name_fields={},
    param_fields={},
    return_type_fields={},
    docstring_strategy="preceding_comment",
    decorator_node_type=None,
    container_node_types=[],
    constant_patterns=[],
    type_patterns=[],
)


# Kotlin specification
# NOTE: Kotlin's tree-sitter grammar exposes no named field accessors for names,
# parameters, or bodies. All extraction is handled via special-cases in extractor.py
# that walk children by node type (simple_identifier / type_identifier / function_body).
KOTLIN_SPEC = LanguageSpec(
    ts_language="kotlin",
    symbol_node_types={
        "class_declaration": "class",     # class, interface, enum class, data class
        "object_declaration": "class",    # object declarations (singletons)
        "function_declaration": "function",
        "type_alias": "type",
    },
    name_fields={},     # Names extracted via special-case in extractor.py
    param_fields={},    # Parameters captured via source range in _build_signature
    return_type_fields={},
    docstring_strategy="preceding_comment",
    decorator_node_type=None,  # Annotations live inside modifiers node; captured in signature
    container_node_types=["class_declaration", "object_declaration"],
    constant_patterns=["property_declaration"],
    type_patterns=["type_alias", "class_declaration"],
)


# Gleam specification
GLEAM_SPEC = LanguageSpec(
    ts_language="gleam",
    symbol_node_types={
        "function": "function",
        "type_definition": "type",
        "type_alias": "type",
        "constant": "constant",
    },
    name_fields={
        "function": "name",    # identifier field
        "constant": "name",    # identifier field
        # type_definition and type_alias: name via type_name child, special-cased in extractor.py
    },
    param_fields={
        "function": "parameters",
    },
    return_type_fields={
        "function": "return_type",
    },
    docstring_strategy="preceding_comment",
    decorator_node_type=None,
    container_node_types=[],
    constant_patterns=["constant"],
    type_patterns=["type_definition", "type_alias"],
)


# Bash specification
BASH_SPEC = LanguageSpec(
    ts_language="bash",
    symbol_node_types={
        "function_definition": "function",
    },
    name_fields={
        "function_definition": "name",
    },
    param_fields={},
    return_type_fields={},
    docstring_strategy="preceding_comment",
    decorator_node_type=None,
    container_node_types=[],
    constant_patterns=["declaration_command"],  # readonly / declare -r
    type_patterns=[],
)


# Nix specification
# NOTE: Nix is an expression language; all constructs are `binding` nodes inside
# binding_set children of let_expression or attrset_expression. Custom extraction
# is performed in extractor.py via _parse_nix_symbols(). Fields below are empty.
NIX_SPEC = LanguageSpec(
    ts_language="nix",
    symbol_node_types={},
    name_fields={},
    param_fields={},
    return_type_fields={},
    docstring_strategy="preceding_comment",
    decorator_node_type=None,
    container_node_types=[],
    constant_patterns=[],
    type_patterns=[],
)


# EJS (Embedded JavaScript Templates) specification
# NOTE: No tree-sitter grammar exists for EJS. Extraction is handled by
# _parse_ejs_symbols() in extractor.py via regex, which pulls JS function
# definitions from <% %> scriptlet blocks and emits a synthetic "template"
# symbol per file to ensure the file is always stored for text search.
EJS_SPEC = LanguageSpec(
    ts_language="ejs",
    symbol_node_types={},
    name_fields={},
    param_fields={},
    return_type_fields={},
    docstring_strategy="preceding_comment",
    decorator_node_type=None,
    container_node_types=[],
    constant_patterns=[],
    type_patterns=[],
)


# Vue SFC specification
# NOTE: Vue Single-File Components are parsed by _parse_vue_symbols() in
# extractor.py, which extracts the <script>/<script setup> block and re-parses
# it as JavaScript or TypeScript (detected from the lang="ts" attribute).
VUE_SPEC = LanguageSpec(
    ts_language="vue",
    symbol_node_types={},
    name_fields={},
    param_fields={},
    return_type_fields={},
    docstring_strategy="preceding_comment",
    decorator_node_type=None,
    container_node_types=[],
    constant_patterns=[],
    type_patterns=[],
)


# Verse (UEFN) specification
# NOTE: No tree-sitter grammar exists for Epic's Verse language.
# Symbol extraction is performed by _parse_verse_symbols() in extractor.py
# using regex-based parsing (same approach as Blade).
#
# Primary use case: token-efficient lookup of UEFN API digest files.
# The three standard Verse digest files total ~800KB / ~200k tokens:
#   Fortnite.digest.verse    587KB  3,608 symbols  ~147k tokens
#   Verse.digest.verse       125KB    622 symbols   ~31k tokens
#   UnrealEngine.digest.verse 91KB    326 symbols   ~23k tokens
#
# With jcodemunch indexing, a single symbol lookup costs ~94 tokens
# instead of loading the full file (~147k tokens) — a 99.9% reduction.
# A search returning 10 signature matches costs ~130 tokens.
#
# The LanguageSpec fields below are intentionally empty — all extraction
# logic lives in _parse_verse_symbols() which handles containers, methods,
# extension methods, variables, constants, docstrings, and decorators.
VERSE_SPEC = LanguageSpec(
    ts_language="verse",
    symbol_node_types={},
    name_fields={},
    param_fields={},
    return_type_fields={},
    docstring_strategy="preceding_comment",
    decorator_node_type=None,
    container_node_types=[],
    constant_patterns=[],
    type_patterns=[],
)


# Language registry
LANGUAGE_REGISTRY = {
    "python": PYTHON_SPEC,
    "javascript": JAVASCRIPT_SPEC,
    "typescript": TYPESCRIPT_SPEC,
    "tsx": TSX_SPEC,
    "go": GO_SPEC,
    "rust": RUST_SPEC,
    "java": JAVA_SPEC,
    "php": PHP_SPEC,
    "dart": DART_SPEC,
    "csharp": CSHARP_SPEC,
    "c": C_SPEC,
    "swift": SWIFT_SPEC,
    "cpp": CPP_SPEC,
    "elixir": ELIXIR_SPEC,
    "ruby": RUBY_SPEC,
    "perl": PERL_SPEC,
    "gdscript": GDSCRIPT_SPEC,
    "blade": BLADE_SPEC,
    "kotlin": KOTLIN_SPEC,
    "gleam": GLEAM_SPEC,
    "bash": BASH_SPEC,
    "nix": NIX_SPEC,
    "vue": VUE_SPEC,
    "ejs": EJS_SPEC,
    "verse": VERSE_SPEC,
}

logger = logging.getLogger(__name__)


def _apply_extra_extensions() -> None:
    """Apply JCODEMUNCH_EXTRA_EXTENSIONS env var to LANGUAGE_EXTENSIONS at import time."""
    raw = os.environ.get("JCODEMUNCH_EXTRA_EXTENSIONS", "").strip()
    if not raw:
        return
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" not in token:
            logger.warning("JCODEMUNCH_EXTRA_EXTENSIONS: malformed entry %r (expected .ext:lang) — skipped", token)
            continue
        ext, _, lang = token.partition(":")
        ext = ext.strip()
        lang = lang.strip()
        if not ext or not lang:
            logger.warning("JCODEMUNCH_EXTRA_EXTENSIONS: malformed entry %r (empty ext or lang) — skipped", token)
            continue
        if lang not in LANGUAGE_REGISTRY:
            logger.warning("JCODEMUNCH_EXTRA_EXTENSIONS: unknown language %r in entry %r — skipped", lang, token)
            continue
        LANGUAGE_EXTENSIONS[ext] = lang


_apply_extra_extensions()


def get_language_for_path(path: str) -> "Optional[str]":
    """Return the language name for a file path, handling compound extensions.

    Checks compound suffixes (e.g. ``.blade.php``) before falling back to the
    last extension (e.g. ``.php``).  This ensures Blade templates are not
    mis-classified as plain PHP.
    """
    import os as _os
    lower = path.lower()
    base = _os.path.basename(lower)
    first_dot = base.find(".")
    if first_dot != -1:
        compound = base[first_dot:]  # e.g. ".blade.php"
        if compound in LANGUAGE_EXTENSIONS:
            return LANGUAGE_EXTENSIONS[compound]
    _, ext = _os.path.splitext(lower)
    return LANGUAGE_EXTENSIONS.get(ext)
