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
    kind: str                       # "function" | "class" | "method" | "constant" | "type"
    language: str                   # "python" | "javascript" | "typescript" | "go" | "rust" | "java" | "c" | "cpp"
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
    """Compute SHA-256 hash of symbol source bytes.

    Used for drift detection (did the source change since indexing?)
    but not as a primary ID.

    Args:
        source_bytes: Raw bytes of the symbol source code.

    Returns:
        Hex-encoded SHA-256 hash.
    """
    return hashlib.sha256(source_bytes).hexdigest()
