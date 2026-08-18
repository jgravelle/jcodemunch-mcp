"""Security utilities for path validation, secret detection, and binary filtering."""

import os
from pathlib import Path
from typing import Optional

from . import config as _config


# --- Package Integrity Check ---

def verify_package_integrity() -> None:
    """Warn at startup if this code is running from an unofficial distribution.

    Detects supply-chain attacks where the package is re-published under a
    different name (e.g. jcodemunch-mcp-fork instead of jcodemunch-mcp).
    Uses packages_distributions() to find which distribution actually owns
    the running code — catches renamed forks that install under a different name.
    """
    import sys

    expected_dist = "jcodemunch-mcp"
    canonical_url = "https://github.com/jgravelle/jcodemunch-mcp"

    try:
        from importlib.metadata import packages_distributions

        distributions = packages_distributions().get("jcodemunch_mcp", [])
        if not distributions:
            # Running from source / editable install without dist metadata — skip.
            return

        actual_dist = distributions[0]
        if actual_dist != expected_dist:
            print(
                f"\nSECURITY WARNING: jcodemunch_mcp is running from distribution "
                f"'{actual_dist}' instead of the official '{expected_dist}'.\n"
                f"This may indicate a supply-chain attack or unofficial fork.\n"
                f"Install only from PyPI: pip install {expected_dist}\n"
                f"Official source: {canonical_url}\n",
                file=sys.stderr,
            )
    except Exception:
        pass  # Never block startup due to integrity check errors


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
#
# The classifier itself lives in `secret_classifier.py` (a pure, structured,
# group-based credential detector). This module owns the boolean public API and
# the translation of the `exclude_secret_patterns` config key into the
# classifier's override knobs.

from .secret_classifier import (  # noqa: E402  (kept beside its callers)
    ALL_GROUPS as _SECRET_GROUPS,
    GROUP_BROAD_BASENAME as _GROUP_BROAD,
    GROUP_SECRET_STORE as _GROUP_STORE,
    classify_secret_file,
)


def _resolve_secret_overrides(excluded: list[str]) -> tuple[set[str], list[str]]:
    """Translate `exclude_secret_patterns` entries into classifier overrides.

    Three accepted forms, for backward compatibility and the new group-aware
    control:

    - A classifier group slug (e.g. ``"key_material_directories"``) disables
      that whole group.
    - The legacy ``"*secret*"`` token disables the broad-basename group AND its
      directory analogue (the secret-store data group), matching the pre-redesign
      opt-out so existing configs behave the same.
    - Any other glob becomes an allow pattern — a file matching it is never
      treated as secret (the per-pattern opt-out, e.g. ``"*.pem"``).
    """
    disabled: set[str] = set()
    allow: list[str] = []
    for entry in excluded:
        if entry in _SECRET_GROUPS:
            disabled.add(entry)
        elif entry == "*secret*":
            disabled.add(_GROUP_BROAD)
            disabled.add(_GROUP_STORE)
        else:
            allow.append(entry)
    return disabled, allow


def is_secret_file(file_path: str, repo: Optional[str] = None) -> bool:
    """Return True if a path is judged to be a secret/credential file.

    Thin boolean wrapper over :func:`secret_classifier.classify_secret_file`,
    applying the project's ``exclude_secret_patterns`` overrides. Use the
    classifier directly when the structured verdict (reason / group / confidence)
    is needed (logging, diagnostics).

    The classifier never reads file contents — it decides from the basename and
    directory shape only. A source module that *handles* secrets
    (``secret_redaction.py``) is code, not credential material, and is not
    flagged; an actual credential file (``.env``, ``*.pem``, ``secrets/db.yaml``,
    ``.aws/credentials``) is.
    """
    # ⚠ `repo=` is what makes the docstring above true. Without it this reads
    # global config only, and the project's documented opt-out never applies
    # (#491). Its sibling `get_respect_cachedir_tag` has threaded it since
    # v1.108.270; these two were left behind by the #301 audit.
    excluded = list(_config.get("exclude_secret_patterns", [], repo=repo) or [])
    disabled, allow = _resolve_secret_overrides(excluded)
    return classify_secret_file(
        file_path,
        disabled_groups=disabled,
        allow_patterns=allow,
    ).is_secret


# --- Binary File Detection ---

# --- Skip Rules (single source of truth) ---
#
# All three exported collections (SKIP_PATTERNS, SKIP_DIRECTORIES, SKIP_FILES)
# are derived from these canonical lists. Add new entries here — never edit
# the derived exports directly.

_SKIP_DIRECTORY_NAMES: list[str] = [
    "node_modules", "vendor", "venv", ".venv", "__pycache__",
    "dist", "build", ".git", ".tox", ".mypy_cache", "target",
    ".gradle", "test_data", "testdata", "fixtures", "snapshots",
    "migrations", "generated", "proto", "DerivedData", ".build",
    # v1.108.234: duplicate source trees. A `backup/`, `old/` or `archive/`
    # directory holding real source files indexes the SAME symbols twice, and
    # the copies then compete with the originals in ranking. Reported by a user
    # who indexed a project root (1,824 files, ~40% sources) and got diluted
    # results plus some empty queries; scoping to the crate fixed it.
    #
    # ⚠ These are ordinary English words and CAN name a real package. That is
    # why `exclude_skip_directories` exists — a project that ships an
    # `archive/` module removes it there per-project. Every skip is also
    # counted in `discovery_skip_counts`, so a surprised user can see which
    # rule dropped what rather than guessing.
    "backup", "old", "archive",
]

# Glob-style patterns — matched by regex in index_folder, by suffix in index_repo.
_SKIP_DIRECTORY_GLOBS: list[str] = [
    "*.xcodeproj", "*.xcworkspace",
]

_SKIP_FILE_PATTERNS: list[str] = [
    ".min.js", ".min.ts", ".bundle.js",
    "package-lock.json", "yarn.lock", "go.sum",
]

# Derived exports — index_repo uses SKIP_PATTERNS (path substring matching),
# index_folder uses SKIP_DIRECTORIES + SKIP_FILES (regex matching on os.walk names).

SKIP_PATTERNS: frozenset[str] = frozenset(
    [d + "/" for d in _SKIP_DIRECTORY_NAMES]
    + [g + "/" for g in _SKIP_DIRECTORY_GLOBS]
    + _SKIP_FILE_PATTERNS
)

SKIP_DIRECTORIES: list[str] = _SKIP_DIRECTORY_NAMES + [
    r"[^/]*\." + g.split("*.")[-1] for g in _SKIP_DIRECTORY_GLOBS
]

SKIP_FILES: list[str] = list(_SKIP_FILE_PATTERNS)


# ── Cache Directory Tagging Specification (https://bford.info/cachedir/) ──────
#
# A directory declares ITSELF a cache by containing a `CACHEDIR.TAG` whose first
# 43 bytes are exactly the signature below. This is the one exclusion rule in
# this file that is not ours: the writer of the cache declares it, and any
# reader that knows the standard honours it without knowing who wrote it.
#
# ⚠⚠ The signature check is the whole point and MUST NOT be reduced to "a file
# with that name exists". A name-only check is an assertion about one instance
# of the property rather than the property, which is exactly the mistake that
# produced the defect this rule was written to answer (#429 and its neighbours:
# jdoc pinned a `.txt` suffix, we pinned a node type, both read as coverage).
# The spec says first 43 bytes; anything looser silently excludes a directory
# that merely has a file by that name.
CACHEDIR_TAG_FILENAME = "CACHEDIR.TAG"
CACHEDIR_TAG_SIGNATURE = b"Signature: 8a477f597d28d172789f06886806bc55"


def is_cache_directory(path) -> bool:
    """True when ``path`` declares itself a cache directory per the spec.

    Reads at most 43 bytes and never raises: an unreadable or absent tag means
    "not a cache", so a permission error can never silently empty a corpus.

    ⚠ Deliberately NOT a withheld exclusion. A tagged directory holds derived,
    regenerable data BY THE WRITER'S OWN DECLARATION, which puts it in the same
    class as `gitignore` and `wrong_extension` — the corpus being defined, not
    a file we refused. Absence claims over the remaining corpus stay citable.
    ``respect_cachedir_tag: false`` is the opt-out for anyone who tags a
    directory they nonetheless want indexed.
    """
    try:
        with open(os.path.join(str(path), CACHEDIR_TAG_FILENAME), "rb") as fh:
            return fh.read(len(CACHEDIR_TAG_SIGNATURE)) == CACHEDIR_TAG_SIGNATURE
    except (OSError, ValueError):
        return False


def get_respect_cachedir_tag(repo: Optional[str] = None) -> bool:
    """Whether the walk honours `CACHEDIR.TAG`. Default True.

    Only an explicit false disables it, so a typo or a garbage value keeps the
    standard honoured rather than silently re-admitting cache trees.
    """
    value = _config.get("respect_cachedir_tag", True, repo=repo)
    return False if value is False else True


def _excluded_skip_directories(repo: Optional[str] = None) -> set[str]:
    """Return the set of directory names the user wants to un-skip.

    ⚠ ``repo`` is not optional in practice, only in signature. The skip list
    holds ordinary English words that CAN name a real package, which is the
    whole reason ``exclude_skip_directories`` exists — and a project that ships
    a ``fixtures/`` or ``archive/`` module declares that in its own
    ``.jcodemunch.jsonc``, not in global config. Reading without ``repo`` skips
    the project overlay entirely, so the documented per-project opt-out silently
    did nothing (#491).
    """
    raw = _config.get("exclude_skip_directories", [], repo=repo)
    return set(raw) if isinstance(raw, list) else set()


def get_skip_directories(repo: Optional[str] = None) -> list[str]:
    """Return SKIP_DIRECTORIES with user-excluded entries removed.

    Pass ``repo`` (a filesystem path) to honour the project's
    ``exclude_skip_directories``. Omitting it resolves global config only.
    """
    excluded = _excluded_skip_directories(repo=repo)
    if not excluded:
        return SKIP_DIRECTORIES
    return [d for d in SKIP_DIRECTORIES if d not in excluded]


def get_skip_patterns(repo: Optional[str] = None) -> frozenset[str]:
    """Return SKIP_PATTERNS with user-excluded directory entries removed.

    Pass ``repo`` (a filesystem path) to honour the project's
    ``exclude_skip_directories``. Omitting it resolves global config only.
    """
    excluded = _excluded_skip_directories(repo=repo)
    if not excluded:
        return SKIP_PATTERNS
    excluded_with_slash = {d + "/" for d in excluded}
    return frozenset(p for p in SKIP_PATTERNS if p not in excluded_with_slash)

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


def get_extra_ignore_patterns(
    call_patterns: Optional[list] = None,
    repo: Optional[str] = None,
) -> list:
    """Return merged extra ignore patterns from config and per-call list.

    Args:
        call_patterns: Patterns supplied by the caller (per-call override).
        repo: Repo identifier (absolute path or display name). When supplied,
            the merged project config (`.jcodemunch.jsonc`) is consulted first;
            falls back to global config when no project entry exists. Without
            `repo`, only the global config is read — which silently ignores
            project-level overrides (issue #300, reported by @domis86).

    Returns:
        Combined list of gitignore-style pattern strings. Empty list if none.
    """
    config_patterns = _config.get("extra_ignore_patterns", [], repo=repo)
    if isinstance(config_patterns, list):
        combined = config_patterns[:]
    else:
        combined = []
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

# The size cap was the only one of these three with no way to move it
# (reported by @dkiaulakis, v1.108.193). Its two neighbours each had a
# resolver reading config; this one was passed as a hardcoded constant from
# index_folder.py and appeared in none of the 79 JCODEMUNCH_* variables.
#
# The default is NOT raised. 500KB stays, because it protects the common case
# from a parse that costs more than the file is worth. What changes is that a
# caller who knows their own corpus can move it, the same way they can already
# move both file-count limits three lines up.
MAX_FILE_SIZE_ENV_VAR = "JCODEMUNCH_MAX_FILE_SIZE"

# A RESPONSE limit, deliberately not an indexing one (#425). Until this existed,
# the largest single MCP reply the server could emit was bounded by
# `max_file_size` -- an indexing cap, living in another subsystem, doing the job
# by coincidence with no test pinning the relationship. Two things followed:
# the protection could be removed by an unrelated change to how bodies are
# cached or sliced, and raising the indexing cap to cover a large generated file
# silently raised the maximum reply size, which the indexing key's documentation
# never claimed to do.
#
# 1 MiB is roughly double the largest reply measured crossing stdio intact
# (516,376 bytes from `get_file_content` on a file at the 512KB indexing cap).
# It is a stated bound, not a discovered one.
DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024  # 1 MiB
MAX_RESPONSE_BYTES_ENV_VAR = "JCODEMUNCH_RESPONSE_MAX_BYTES"


def get_max_response_bytes(
    max_bytes: "Optional[int]" = None,
    repo: "Optional[str]" = None,
) -> int:
    """Resolve the single-response byte ceiling from arg or config.

    Same shape as ``get_max_file_size`` and its two siblings, because the point
    of #425 is that "how large can one reply be" should be one auditable number
    with a documented default, not an emergent consequence of an unrelated cap.

    Returns:
        Positive byte limit, falling back to the default when config is unset or
        invalid. A non-positive configured value means "no ceiling" and is
        returned as 0, which the caller treats as disabled -- an explicit opt-out
        is different from a typo, and a typo must not silently uncap the server.
    """
    if max_bytes is not None:
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        return max_bytes

    value = _config.get("response_max_bytes", DEFAULT_MAX_RESPONSE_BYTES, repo=repo)
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_MAX_RESPONSE_BYTES
    if value == 0:
        return 0  # explicit opt-out
    if value < 0:
        return DEFAULT_MAX_RESPONSE_BYTES
    return value


def get_max_file_size(
    max_size: Optional[int] = None,
    repo: Optional[str] = None,
) -> int:
    """Resolve the per-file byte cap from arg or config.

    Parity with ``get_max_index_files`` / ``get_max_folder_files``, which is the
    whole point: the size cap was the one limit of the three that could not be
    moved by any route (v1.108.193).

    Args:
        max_size: Explicit override. Must be a positive integer when provided.
        repo: Repo identifier (absolute path or display name). When supplied,
            the merged project config (``.jcodemunch.jsonc``) is consulted
            before global config — see the note above ``get_max_index_files``.

    Returns:
        Positive byte limit. Falls back to the default if config is unset or
        invalid, matching the siblings rather than failing the index.
    """
    if max_size is not None:
        if max_size <= 0:
            raise ValueError("max_size must be a positive integer")
        return max_size

    value = _config.get("max_file_size", DEFAULT_MAX_FILE_SIZE, repo=repo)
    if isinstance(value, int) and value > 0:
        return value
    return DEFAULT_MAX_FILE_SIZE


# All three limit resolvers below take `repo`. A limit documented in the config
# template is a limit a user will set in `.jcodemunch.jsonc`, and a per-project
# corpus is exactly the case that justifies moving one — the monorepo with the
# 800KB generated client, not the whole machine. Reading global config only made
# that setting land in a file the resolver never opened, so it failed silently:
# no warning, no unknown-key error, just the default (#390 @lazy-geeek reported
# the global-config half, fixed in .194; #391 @amarakramali carried the repro
# that exposed this project-config half).
def get_max_index_files(
    max_files: Optional[int] = None,
    repo: Optional[str] = None,
) -> int:
    """Resolve the maximum indexed file count from arg or config.

    Args:
        max_files: Explicit override. Must be a positive integer when provided.
        repo: Repo identifier. When supplied, the merged project config
            (``.jcodemunch.jsonc``) is consulted before global config.

    Returns:
        Positive file-count limit. Falls back to the default if config
        is unset or invalid.
    """
    if max_files is not None:
        if max_files <= 0:
            raise ValueError("max_files must be a positive integer")
        return max_files

    value = _config.get("max_index_files", DEFAULT_MAX_INDEX_FILES, repo=repo)
    if isinstance(value, int) and value > 0:
        return value
    return DEFAULT_MAX_INDEX_FILES


def get_max_folder_files(
    max_files: Optional[int] = None,
    repo: Optional[str] = None,
) -> int:
    """Resolve the maximum indexed file count for local folder indexing.

    The default (2,000) is intentionally lower than the GitHub repo default (10,000)
    because local indexing runs synchronously inside an MCP tool call and
    must complete within the client's timeout window.

    Args:
        max_files: Explicit override. Must be a positive integer when provided.
        repo: Repo identifier. When supplied, the merged project config
            (``.jcodemunch.jsonc``) is consulted before global config.

    Returns:
        Positive file-count limit.
    """
    if max_files is not None:
        if max_files <= 0:
            raise ValueError("max_files must be a positive integer")
        return max_files

    value = _config.get("max_folder_files", repo=repo)
    if isinstance(value, int) and value > 0:
        return value
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
    if check_secrets and is_secret_file(rel_path, repo=str(root)):
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
