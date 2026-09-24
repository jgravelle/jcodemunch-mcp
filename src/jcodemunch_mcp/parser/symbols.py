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
    # ⚠⚠ A module-scope MUTABLE binding. TWO producers as of this merge:
    # JS/TS `let` and `var` (#741, #742) and Go's package-level `var` (#731).
    # Each branch added this entry independently believing the other was
    # unmerged, and both comments claimed sole ownership -- the merge is where
    # that gets corrected rather than carried.
    # Deliberately NOT `property`, which #732 added for class state -- a
    # top-level binding belongs to no type, and reusing that kind would mix
    # module bindings into every consumer asking about a class's members. It is
    # the word the grammars themselves use (`variable_declaration`,
    # `variable_declarator`, `var_spec`) and the word LSP uses for the same
    # distinction (Variable, separate from both Property and Constant).
    # ⚠ Appended, never inserted, for the reason above this tuple: each
    # existing position is bytes a client has already cached, and a reorder is
    # a full-rate schema rewrite for every user.
    "variable",   # Module-scope mutable bindings: JS/TS `let`/`var`, Go `var`
)

VALID_KINDS: frozenset[str] = frozenset(KIND_ORDER)

#: The kinds that name DECLARED STATE rather than behaviour or wiring (#760).
#:
#: ⚠⚠ **A consumer keyed on ONE kind string sees one of four.** `file_summarize`
#: counted a class's members with `s.kind == "field"`, so a Java class read as
#: `(2 methods, 5 fields)` and a PHP class with five properties read as
#: `(2 methods)` -- the same channel, different word, and the reader sees a class
#: with no state at all. "Which kinds are declared state" is a property of THIS
#: vocabulary, so it is answered here and imported, never re-derived: a second
#: copy is how the defect returns for the fifth kind.
#:
#: ⚠⚠ **This says what a kind IS, never where it lives.** `constant` and
#: `variable` are reached at module scope AND as class members (a Svelte
#: component's bindings are parented to the component), which is exactly the
#: mixing `KIND_ORDER`'s `variable` comment warns about. Scope is the CALLER's
#: question and the caller answers it with the `parent` field; widening the
#: kinds without keeping that filter is the way to break it.
#:
#: ⚠ Derived from `KIND_ORDER` so the order is the vocabulary's, which makes a
#: rendered summary deterministic and puts a newly added kind where the tuple
#: puts it rather than where a literal was typed.
_STATE_KINDS: frozenset[str] = frozenset({"constant", "field", "property", "variable"})
STATE_KINDS: tuple[str, ...] = tuple(k for k in KIND_ORDER if k in _STATE_KINDS)

#: Which symbol represents a file (#806): lower ranks first; a kind absent
#: from this table ranks after every kind in it.
#:
#: ⚠⚠ **Three tools typed this table by hand, identically** (`get_repo_map`,
#: `get_repo_outline`, `get_symbol_importance`), each written before `field`,
#: `property` and `variable` existed, so a class member that used to arrive as
#: `constant` fell to a default nobody chose once its language learned the
#: real word. Every state kind ranks where `constant` does: where those
#: members ranked before they had their own words. Derived from `STATE_KINDS`,
#: so a fifth state kind is ranked on arrival.
REPRESENTATIVE_KIND_RANK: dict[str, int] = {
    "class": 0,
    "function": 1,
    "method": 2,
    "type": 3,
    **{kind: 4 for kind in STATE_KINDS},
}

#: Plurals that `kind + "s"` gets wrong. Naming a kind in prose forces this:
#: "1 properties" would be worse than the count being absent.
_IRREGULAR_PLURALS: dict[str, str] = {"property": "properties"}


def plural_kind(kind: str, count: int) -> str:
    """`kind` pluralised for `count`, for prose that names the kind."""
    if count == 1:
        return kind
    return _IRREGULAR_PLURALS.get(kind, kind + "s")


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
