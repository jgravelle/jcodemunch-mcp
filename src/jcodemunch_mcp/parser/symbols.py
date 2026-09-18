"""Symbol dataclass and utility functions."""

import hashlib
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Symbol:
    """A code symbol extracted from source via tree-sitter."""
    id: str                         # Unique ID: "file_path::QualifiedName#kind"
    file: str                       # Source file path (e.g., "src/main.py")
    name: str                       # Symbol name (e.g., "login")
    qualified_name: str             # Fully qualified (e.g., "MyClass.login")
    kind: str                       # One of VALID_KINDS below
    language: str                   # "python" | "javascript" | "typescript" | "go" | "rust" | "java" | "c" | "cpp" | "xml"
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
    content_hash: str = ""         # SHA-256 of symbol source bytes (for drift detection)
    ecosystem_context: str = ""    # Optional context from ecosystem (e.g., dbt model metadata)
    cyclomatic: int = 0            # McCabe cyclomatic complexity (branch count + 1)
    max_nesting: int = 0           # Max bracket-nesting depth relative to opening brace
    param_count: int = 0           # Number of parameters in the signature
    call_references: list[str] = field(default_factory=list)  # Called names from AST call_expression nodes



# Single source of truth for all symbol kinds emitted by parsers.
#
# ⚠⚠ **ORDERED, because it is PUBLISHED.** The `kind` enum in `search_symbols`'
# `inputSchema` derives from this tuple, and that schema sits in the CACHED
# PREFIX of every request. A frozenset cannot be published directly: `str`
# hashing is randomised per process, so set iteration order differs between
# runs and the served schema would change for the same build — invalidating
# the prefix at every server start, for every user, forever.
#
# ⚠ Append, never reorder. Each existing position is bytes a client has
# already cached; a new kind at the end leaves them untouched.
KIND_ORDER: tuple[str, ...] = (
    "function",   # Standalone functions, procedures, subroutines
    "class",      # Classes, structs, modules-as-containers
    "method",     # Methods belonging to a class/module
    "constant",   # Constants, named values, defines
    "type",       # Type aliases, interfaces, enums, traits, protocols
    "template",   # C++ templates
    "import",     # Import directives (C++ #include, etc.)
    # ⚠⚠ (@devtomnl, #571) Emitted by the Python parser since the
    # dataclass-fields change and absent from this set for its whole life —
    # 399 of them in this repo's own index. BOTH gates rejected it: the
    # runtime check at `server.py` (`kind_filter not in VALID_KINDS`) and the
    # hardcoded wire enum, which had drifted from this set because it was a
    # SECOND COPY. Deriving the enum is what stops the next kind repeating it.
    "field",      # Struct/dataclass/record fields
    # ⚠⚠ (#732) #571 ALMOST REPEATED, one entry down. `PHP_SPEC` had mapped
    # `property_declaration` to this kind since before #571 and the entry never
    # fired, so the kind was declared-but-dead and both gates would have
    # rejected it the moment anything emitted one. Kotlin is the first live
    # emitter. The comment above says deriving the enum is what stops the next
    # kind repeating it -- deriving the enum was not enough, because nothing
    # checked that a kind a SPEC can emit is a kind this tuple contains.
    # `tests/test_kind_enum_is_derived.py::test_every_spec_kind_is_a_valid_kind`
    # is that check, and it fails on a spec kind absent from here.
    "property",   # Kotlin/PHP properties: named, mutable-or-not class state
    # ⚠⚠ (#731) A module-scope MUTABLE binding: Go's package-level `var` today,
    # and the kind #741/#742 will use for JS/TS `let` and `var` when that branch
    # merges -- it is not in the tree yet, so Go is the only producer here.
    # Deliberately NOT `property`, which
    # #732 added for class state -- a top-level `let` belongs to no type, and
    # reusing that kind would mix module bindings into every consumer asking
    # about a class's members. It is the word the grammar itself uses
    # (`variable_declaration`, `var_spec`) and the word LSP uses for the same
    # distinction (Variable, separate from both Property and Constant).
    # ⚠ Appended, never inserted, for the reason above this tuple: each
    # existing position is bytes a client has already cached, and a reorder is
    # a full-rate schema rewrite for every user.
    "variable",   # Module-scope mutable bindings: JS/TS `let`/`var`, Go `var`
)

VALID_KINDS: frozenset[str] = frozenset(KIND_ORDER)


def make_symbol_id(file_path: str, qualified_name: str, kind: str = "") -> str:
    """Generate unique symbol ID.

    Format: {file_path}::{qualified_name}#{kind}
    Example: src/main.py::MyClass.login#method

    The file_path is kept as-is (no slugification) to maintain readability
    and ensure IDs are stable across re-indexing when the file path,
    qualified name, and kind are unchanged.

    Args:
        file_path: Relative file path within the repo.
        qualified_name: Fully qualified symbol name.
        kind: Symbol kind (function, class, method, constant, type).

    Returns:
        A human-readable symbol ID.
    """
    if kind:
        return f"{file_path}::{qualified_name}#{kind}"
    return f"{file_path}::{qualified_name}"


def compute_content_hash(source_bytes: bytes) -> str:
    """Compute SHA-256 content hash for drift detection.

    Args:
        source_bytes: Raw bytes of the symbol source code.

    Returns:
        64-char hex-encoded SHA-256 digest.
    """
    return hashlib.sha256(source_bytes).hexdigest()
