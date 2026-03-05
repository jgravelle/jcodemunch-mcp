"""Security utilities for path validation, secret detection, and binary filtering."""

import os
from pathlib import Path
from typing import Optional


# --- Path Traversal & Symlink Protection ---

def validate_path(root: Path, target: Path) -> bool:
    """Check that target path resolves within root directory.

    Prevents path traversal attacks (e.g., ../../etc/passwd) and
    symlink escapes. Both paths are resolved to absolute form before
    comparison.

    Args:
        root: The trusted root directory (must already be resolved).
        target: The path to validate.

    Returns:
        True if target is inside root, False otherwise.
    """
    try:
        resolved = target.resolve()
        resolved_root = root.resolve()
        # Use os.path for reliable prefix check (handles trailing sep)
        return os.path.commonpath([resolved_root, resolved]) == str(resolved_root)
    except (OSError, ValueError):
        return False


def is_symlink_escape(root: Path, path: Path) -> bool:
    """Check if a symlink points outside the root directory.

    Args:
        root: The trusted root directory (resolved).
        path: The path to check.

    Returns:
        True if the path is a symlink that escapes root, False otherwise.
    """
    try:
        if path.is_symlink():
            resolved = path.resolve()
            resolved_root = root.resolve()
            return os.path.commonpath([resolved_root, resolved]) != str(resolved_root)
    except (OSError, ValueError):
        return True  # If we can't resolve, treat as escape
    return False


# --- Secret File Detection ---

SECRET_PATTERNS = [
    "*.env",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.credentials",
    "*.keystore",
    "*.jks",
    "*.token",
    "*secret*",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "id_dsa",
    "id_ecdsa",
    ".htpasswd",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "service-account*.json",
    "*.secrets",
]


def is_secret_file(file_path: str) -> bool:
    """Check if a file path matches known secret file patterns.

    Uses filename/extension matching, not content inspection.

    Args:
        file_path: Relative file path (forward slashes).

    Returns:
        True if the file matches a secret pattern.
    """
    import fnmatch

    name = os.path.basename(file_path).lower()
    path_lower = file_path.lower()

    for pattern in SECRET_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return True
        # Also check full path for patterns like .env.*
        if fnmatch.fnmatch(path_lower, pattern):
            return True
    return False


# --- Binary File Detection ---

BINARY_EXTENSIONS = frozenset([
    # Executables
    ".exe", ".dll", ".so", ".dylib", ".bin", ".out",
    # Object files
    ".o", ".obj", ".a", ".lib",
    # Archives
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".webp", ".tiff", ".tif",
    # Media
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac",
    ".ogg", ".webm",
    # Documents
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    # Compiled / bytecode
    ".pyc", ".pyo", ".class", ".wasm",
    # Database
    ".db", ".sqlite", ".sqlite3",
    # Fonts
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    # Other
    ".jar", ".war", ".ear",
    ".min.js.map", ".min.css.map",
])


def is_binary_extension(file_path: str) -> bool:
    """Check if a file has a known binary extension.

    Args:
        file_path: File path or name.

    Returns:
        True if the extension indicates a binary file.
    """
    _, ext = os.path.splitext(file_path)
    return ext.lower() in BINARY_EXTENSIONS


def is_binary_content(data: bytes, check_size: int = 8192) -> bool:
    """Detect binary content by checking for null bytes.

    Reads up to check_size bytes and looks for null bytes,
    which strongly indicate binary content.

    Args:
        data: Raw bytes to check.
        check_size: How many bytes to inspect (default 8KB).

    Returns:
        True if the data appears to be binary.
    """
    sample = data[:check_size]
    return b"\x00" in sample


def is_binary_file(file_path: Path, check_size: int = 8192) -> bool:
    """Check if a file is binary using extension check + content sniffing.

    Args:
        file_path: Path to the file.
        check_size: Bytes to read for content check.

    Returns:
        True if the file appears to be binary.
    """
    # Fast path: extension check
    if is_binary_extension(str(file_path)):
        return True

    # Content sniff: read first N bytes
    try:
        with open(file_path, "rb") as f:
            data = f.read(check_size)
        return is_binary_content(data, check_size)
    except OSError:
        return True  # Can't read -> skip


# --- Encoding Safety ---

def safe_decode(data: bytes, encoding: str = "utf-8") -> str:
    """Decode bytes to string with replacement for invalid sequences.

    Args:
        data: Raw bytes.
        encoding: Target encoding.

    Returns:
        Decoded string with replacement characters for invalid bytes.
    """
    return data.decode(encoding, errors="replace")


# --- Composite Filters ---

DEFAULT_MAX_FILE_SIZE = 500 * 1024  # 500KB
DEFAULT_MAX_INDEX_FILES = 500
MAX_INDEX_FILES_ENV_VAR = "JCODEMUNCH_MAX_INDEX_FILES"


def get_max_index_files(max_files: Optional[int] = None) -> int:
    """Resolve the maximum indexed file count from arg or environment.

    Args:
        max_files: Explicit override. Must be a positive integer when provided.

    Returns:
        Positive file-count limit. Falls back to the default if the environment
        variable is unset or invalid.
    """
    if max_files is not None:
        if max_files <= 0:
            raise ValueError("max_files must be a positive integer")
        return max_files

    value = os.environ.get(MAX_INDEX_FILES_ENV_VAR)
    if value is None:
        return DEFAULT_MAX_INDEX_FILES

    try:
        parsed = int(value)
    except ValueError:
        return DEFAULT_MAX_INDEX_FILES

    if parsed <= 0:
        return DEFAULT_MAX_INDEX_FILES

    return parsed


def should_exclude_file(
    file_path: Path,
    root: Path,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    check_secrets: bool = True,
    check_binary: bool = True,
    check_symlinks: bool = True,
) -> Optional[str]:
    """Run all security checks on a file. Returns reason string if excluded, None if ok.

    Args:
        file_path: Absolute path to the file.
        root: Repository root directory (resolved).
        max_file_size: Maximum file size in bytes.
        check_secrets: Whether to check secret patterns.
        check_binary: Whether to check for binary files.
        check_symlinks: Whether to check for symlink escapes.

    Returns:
        A reason string if excluded, None if the file passes all checks.
    """
    # Symlink escape
    if check_symlinks and is_symlink_escape(root, file_path):
        return "symlink_escape"

    # Path traversal
    if not validate_path(root, file_path):
        return "path_traversal"

    # Get relative path for pattern matching
    try:
        rel_path = file_path.relative_to(root).as_posix()
    except ValueError:
        return "outside_root"

    # Secret detection
    if check_secrets and is_secret_file(rel_path):
        return "secret_file"

    # File size
    try:
        size = file_path.stat().st_size
        if size > max_file_size:
            return "file_too_large"
    except OSError:
        return "unreadable"

    # Binary detection (extension first, then content)
    if check_binary and is_binary_extension(rel_path):
        return "binary_extension"

    return None
