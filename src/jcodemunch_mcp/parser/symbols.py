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
VALID_KINDS: frozenset[str] = frozenset({
    "function",   # Standalone functions, procedures, subroutines
    "class",      # Classes, structs, modules-as-containers
    "method",     # Methods belonging to a class/module
    "constant",   # Constants, named values, defines
    "type",       # Type aliases, interfaces, enums, traits, protocols
    "template",   # C++ templates
    "import",     # Import directives (C++ #include, etc.)
    "field",      # Dataclass / attrs / Pydantic class fields (v1.108.80)
    "heading",    # Markdown ATX/setext headings (document outline)
    "code_block", # Markdown fenced code blocks (``` / ~~~)
})


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
