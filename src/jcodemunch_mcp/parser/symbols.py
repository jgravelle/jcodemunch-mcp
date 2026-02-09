"""Symbol dataclass and utility functions."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Symbol:
    """A code symbol extracted from source via tree-sitter."""
    id: str                         # Unique ID: "file-path::QualifiedName"
    file: str                       # Source file path (e.g., "src/main.py")
    name: str                       # Symbol name (e.g., "login")
    qualified_name: str             # Fully qualified (e.g., "MyClass.login")
    kind: str                       # "function" | "class" | "method" | "constant" | "type"
    language: str                   # "python" | "javascript" | "typescript" | "go" | "rust" | "java"
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


def slugify(text: str) -> str:
    """Convert file path to slug format.
    
    Replace / with - and . with - for use in symbol IDs.
    Example: src/main.py -> src-main-py
    """
    return text.replace("/", "-").replace(".", "-")


def make_symbol_id(file_path: str, qualified_name: str) -> str:
    """Generate unique symbol ID.
    
    Format: {file_slug}::{qualified_name}
    Example: src-main-py::MyClass.login
    """
    return f"{slugify(file_path)}::{qualified_name}"
