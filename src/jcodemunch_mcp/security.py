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


# Doc extensions that are safe from the broad *secret* glob. A file like
# "secrets-handling.md" is documentation about secrets, never a credential file.
# More specific patterns (*.key, *.pem, credentials.json, etc.) still apply to
# all extensions regardless of this set.
_SECRET_GLOB_SAFE_EXTENSIONS: frozenset[str] = frozenset({
    ".md", ".markdown", ".mdx",
    ".rst",
    ".txt",
    ".adoc", ".asciidoc", ".asc",
    ".html", ".htm",
    ".ipynb",
})

# Patterns that should NOT be applied to doc extensions (too broad for prose files).
_SECRET_DOC_EXEMPT_PATTERNS: frozenset[str] = frozenset({"*secret*"})


def is_secret_file(file_path: str) -> bool:
    """Check if a file path matches known secret file patterns.

    Uses filename/extension matching, not content inspection. The broad
    ``*secret*`` glob is not applied to known documentation extensions
    (.md, .rst, .txt, .adoc, .html, .ipynb, etc.) to avoid false positives
    on files like ``docs/secrets-handling.md``.

    Args:
        file_path: Relative file path (forward slashes).

    Returns:
        True if the file matches a secret pattern.
    """
    import fnmatch

    name = os.path.basename(file_path).lower()
    path_lower = file_path.lower()
    _, ext = os.path.splitext(name)

    for pattern in SECRET_PATTERNS:
        if pattern in _SECRET_DOC_EXEMPT_PATTERNS and ext in _SECRET_GLOB_SAFE_EXTENSIONS:
            continue
        if fnmatch.fnmatch(name, pattern):
            return True
        # Also check full path for patterns like .env.*
        if fnmatch.fnmatch(path_lower, pattern):
            return True
    return False


# --- Binary File Detection ---

# Shared skip patterns used by both index_folder and index_repo.
# Keep a single source of truth here.
SKIP_PATTERNS: frozenset[str] = frozenset({
    "node_modules/", "vendor/", "venv/", ".venv/", "__pycache__/",
    "dist/", "build/", ".git/", ".tox/", ".mypy_cache/",
    "target/",
    ".gradle/",
    "test_data/", "testdata/", "fixtures/", "snapshots/",
    "migrations/",
    ".min.js", ".min.ts", ".bundle.js",
    "package-lock.json", "yarn.lock", "go.sum",
    "generated/", "proto/",
    "*.xcodeproj/", "*.xcworkspace/", "DerivedData/", ".build/",
})

SKIP_DIRECTORIES = [
    "node_modules", "vendor", "venv", ".venv", "__pycache__",
    "dist", "build", ".git", ".tox", ".mypy_cache", "target",
    ".gradle", "test_data", "testdata", "fixtures", "snapshots",
    "migrations", "generated", "proto", r"[^/]*\.xcodeproj",
    r"[^/]*\.xcworkspace", "DerivedData", ".build"
]

SKIP_FILES = [
    ".min.js", ".min.ts", ".bundle.js", 
    "package-lock.json", "yarn.lock", "go.sum"
]

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


# --- Extra Ignore Patterns ---

EXTRA_IGNORE_PATTERNS_ENV_VAR = "JCODEMUNCH_EXTRA_IGNORE_PATTERNS"


def get_extra_ignore_patterns(call_patterns: Optional[list] = None) -> list:
    """Return merged extra ignore patterns from env var and per-call list.

    Reads ``JCODEMUNCH_EXTRA_IGNORE_PATTERNS`` (comma-separated string or
    JSON array), then appends any per-call ``extra_ignore_patterns``.

    Args:
        call_patterns: Patterns supplied by the caller (per-call override).

    Returns:
        Combined list of gitignore-style pattern strings. Empty list if none.
    """
    import json as _json

    env_val = os.environ.get(EXTRA_IGNORE_PATTERNS_ENV_VAR, "").strip()
    env_patterns: list = []
    if env_val:
        try:
            parsed = _json.loads(env_val)
            if isinstance(parsed, list):
                env_patterns = [str(p) for p in parsed if p]
        except (_json.JSONDecodeError, ValueError):
            env_patterns = [p.strip() for p in env_val.split(",") if p.strip()]

    combined = env_patterns[:]
    if call_patterns:
        combined.extend(call_patterns)
    return combined


# --- Composite Filters ---

DEFAULT_MAX_FILE_SIZE = 500 * 1024  # 500KB
DEFAULT_MAX_INDEX_FILES = 10_000
MAX_INDEX_FILES_ENV_VAR = "JCODEMUNCH_MAX_INDEX_FILES"

# Local folders are indexed synchronously inside an MCP tool call, so the
# default cap is intentionally lower to stay within client timeouts.
# Users can raise it via JCODEMUNCH_MAX_FOLDER_FILES (or the legacy
# JCODEMUNCH_MAX_INDEX_FILES, which is honoured as a fallback).
DEFAULT_MAX_FOLDER_FILES = 2_000
MAX_FOLDER_FILES_ENV_VAR = "JCODEMUNCH_MAX_FOLDER_FILES"


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


def get_max_folder_files(max_files: Optional[int] = None) -> int:
    """Resolve the maximum indexed file count for local folder indexing.

    Checks JCODEMUNCH_MAX_FOLDER_FILES first, then falls back to
    JCODEMUNCH_MAX_INDEX_FILES for backward compatibility.  The default
    (2,000) is intentionally lower than the GitHub repo default (10,000)
    because local indexing runs synchronously inside an MCP tool call and
    must complete within the client's timeout window.

    Args:
        max_files: Explicit override. Must be a positive integer when provided.

    Returns:
        Positive file-count limit.
    """
    if max_files is not None:
        if max_files <= 0:
            raise ValueError("max_files must be a positive integer")
        return max_files

    # Check folder-specific env var first, then legacy shared var.
    for env_var in (MAX_FOLDER_FILES_ENV_VAR, MAX_INDEX_FILES_ENV_VAR):
        value = os.environ.get(env_var)
        if value is not None:
            try:
                parsed = int(value)
            except ValueError:
                continue
            if parsed > 0:
                return parsed

    return DEFAULT_MAX_FOLDER_FILES


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
