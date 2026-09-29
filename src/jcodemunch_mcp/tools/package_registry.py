"""
Cross-repo package registry: extract package names from manifest files
and map them to repo IDs so import graph tools can traverse repo boundaries.
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional

from ..parser.imports import DYNAMIC_IMPORT_UNRESOLVED

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level cache for the package registry
# ---------------------------------------------------------------------------

_registry_cache: Optional[dict[str, list[str]]] = None
_registry_cache_mtime: float = 0.0


# ---------------------------------------------------------------------------
# Package name extraction from manifest files
# ---------------------------------------------------------------------------

def _norm_python_pkg(name: str) -> str:
    """Normalize a Python package name per PEP 503: lowercase + replace _ and - with -."""
    return re.sub(r"[-_]+", "-", name.lower())


def _try_read(path: str) -> Optional[str]:
    """Read a file's content, returning None on any error."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None


def _extract_from_pyproject_toml(content: str) -> Optional[str]:
    """Extract package name from pyproject.toml content."""
    # Try tomllib (stdlib 3.11+) or tomli fallback
    try:
        try:
            import tomllib  # type: ignore[import]
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[import]
            except ImportError:
                tomllib = None  # type: ignore[assignment]

        if tomllib is not None:
            data = tomllib.loads(content)
            name = data.get("project", {}).get("name")
            if name and isinstance(name, str):
                return _norm_python_pkg(name.strip())
    except Exception:
        logger.debug("tomllib parse failed for pyproject.toml, falling back to regex", exc_info=True)

    # Regex fallback for Python 3.10 or parse failures
    # Find [project] section first, then look for name = "..."
    in_project = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped.startswith("[project]") and not stripped.startswith("[project.")
            continue
        if in_project:
            m = re.match(r'^name\s*=\s*["\']([^"\']+)["\']', stripped)
            if m:
                return _norm_python_pkg(m.group(1))
    return None


def _extract_from_setup_cfg(content: str) -> Optional[str]:
    """Extract package name from setup.cfg [metadata].name."""
    in_metadata = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_metadata = stripped.startswith("[metadata]")
            continue
        if in_metadata:
            m = re.match(r'^name\s*=\s*(.+)$', stripped)
            if m:
                return _norm_python_pkg(m.group(1).strip())
    return None


def _extract_from_package_json(content: str) -> Optional[str]:
    """Extract package name from package.json."""
    import json
    try:
        data = json.loads(content)
        name = data.get("name")
        if name and isinstance(name, str):
            return name.strip()
    except Exception:
        # Fallback: regex
        m = re.search(r'"name"\s*:\s*"([^"]+)"', content)
        if m:
            return m.group(1).strip()
    return None


def _extract_from_go_mod(content: str) -> Optional[str]:
    """Extract module path from go.mod."""
    for line in content.splitlines():
        m = re.match(r'^module\s+(\S+)', line.strip())
        if m:
            return m.group(1)
    return None


def _extract_from_cargo_toml(content: str) -> Optional[str]:
    """Extract package name from Cargo.toml."""
    try:
        try:
            import tomllib  # type: ignore[import]
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[import]
            except ImportError:
                tomllib = None  # type: ignore[assignment]

        if tomllib is not None:
            data = tomllib.loads(content)
            name = data.get("package", {}).get("name")
            if name and isinstance(name, str):
                return name.strip()
    except Exception:
        logger.debug("tomllib parse failed for Cargo.toml, falling back to regex", exc_info=True)

    # Regex fallback
    in_package = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_package = stripped == "[package]"
            continue
        if in_package:
            m = re.match(r'^name\s*=\s*["\']([^"\']+)["\']', stripped)
            if m:
                return m.group(1).strip()
    return None


def _extract_from_csproj(content: str) -> Optional[str]:
    """Extract PackageName or AssemblyName from a .csproj file."""
    # Try PackageName first, then AssemblyName
    for tag in ("PackageName", "AssemblyName"):
        m = re.search(r'<' + tag + r'>\s*([^<]+)\s*</' + tag + r'>', content)
        if m:
            return m.group(1).strip()
    return None


def extract_package_names(source_root: str) -> list[str]:
    """Read manifest files in source_root to find package names published by this repo.

    Supports Python (pyproject.toml, setup.cfg), JavaScript/TypeScript (package.json),
    Go (go.mod), Rust (Cargo.toml), and C#/.NET (*.csproj).

    Returns:
        List of package names (normalized). Empty list if no manifest found or on error.
    """
    names: list[str] = []
    root = source_root

    try:
        # Python: pyproject.toml (preferred) → setup.cfg fallback
        pyproject = os.path.join(root, "pyproject.toml")
        setup_cfg = os.path.join(root, "setup.cfg")
        if os.path.isfile(pyproject):
            content = _try_read(pyproject)
            if content:
                name = _extract_from_pyproject_toml(content)
                if name:
                    names.append(name)

        if not names and os.path.isfile(setup_cfg):
            content = _try_read(setup_cfg)
            if content:
                name = _extract_from_setup_cfg(content)
                if name:
                    names.append(name)

        # JavaScript/TypeScript: package.json
        package_json = os.path.join(root, "package.json")
        if os.path.isfile(package_json):
            content = _try_read(package_json)
            if content:
                name = _extract_from_package_json(content)
                if name:
                    names.append(name)

        # Go: go.mod
        go_mod = os.path.join(root, "go.mod")
        if os.path.isfile(go_mod):
            content = _try_read(go_mod)
            if content:
                name = _extract_from_go_mod(content)
                if name:
                    names.append(name)

        # Rust: Cargo.toml
        cargo_toml = os.path.join(root, "Cargo.toml")
        if os.path.isfile(cargo_toml):
            content = _try_read(cargo_toml)
            if content:
                name = _extract_from_cargo_toml(content)
                if name:
                    names.append(name)

        # C#/.NET: *.csproj
        try:
            for entry in os.scandir(root):
                if entry.is_file() and entry.name.endswith(".csproj"):
                    content = _try_read(entry.path)
                    if content:
                        name = _extract_from_csproj(content)
                        if name:
                            names.append(name)
        except OSError:
            pass

    except Exception:
        logger.debug("extract_package_names failed for %s", source_root, exc_info=True)

    return names


# ---------------------------------------------------------------------------
# Root package extraction from import specifiers
# ---------------------------------------------------------------------------

def extract_root_package_from_specifier(specifier: str, language: str) -> str:
    """Extract the root package name from an import specifier.

    Args:
        specifier: Raw import specifier (e.g. "flask.blueprints", "@org/pkg/utils").
        language: Language name (e.g. "python", "javascript", "go", "rust").

    Returns:
        Root package name string, or "" for relative imports and for the
        (#876) dynamic-import marker, which names no package.
    """
    if not specifier or specifier == DYNAMIC_IMPORT_UNRESOLVED:
        return ""

    lang = language.lower()

    if lang in ("python",):
        # Relative imports: strip leading dots
        stripped = specifier.lstrip(".")
        if not stripped:
            return ""  # pure relative (e.g. "...")
        if specifier.startswith("."):
            return ""  # relative import — not cross-repo
        parts = stripped.split(".")
        return parts[0]

    elif lang in ("javascript", "typescript", "tsx", "jsx", "vue", "astro", "svelte"):
        # Relative imports
        if specifier.startswith(".") or specifier.startswith(".."):
            return ""
        # Scoped packages: @org/package[/...]
        if specifier.startswith("@"):
            parts = specifier.split("/")
            if len(parts) >= 2:
                return f"{parts[0]}/{parts[1]}"
            return specifier
        # Unscoped: first segment before /
        return specifier.split("/")[0]

    elif lang == "go":
        # Go: domain/org/repo convention → take first 3 segments
        parts = specifier.split("/")
        return "/".join(parts[:3]) if len(parts) >= 3 else specifier

    elif lang == "rust":
        # Rust: crate::module::... → first segment before ::
        parts = specifier.split("::")
        return parts[0]

    else:
        # Others: first segment before . or /
        first = re.split(r"[./]", specifier)[0]
        return first


# ---------------------------------------------------------------------------
# Registry building
# ---------------------------------------------------------------------------

def _get_newest_index_mtime(all_repos: list[dict]) -> float:
    """Return the maximum mtime of all index .db files for cache invalidation."""
    storage = os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index"))
    newest: float = 0.0
    for repo in all_repos:
        repo_id = repo.get("repo", "")
        if not repo_id or "/" not in repo_id:
            continue
        parts = repo_id.split("/", 1)
        owner, name = parts[0], parts[1]
        # Sanitize name for slug
        safe_owner = re.sub(r"[^A-Za-z0-9._-]", "-", owner).strip("-") or "local"
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "-", name).strip("-") or "repo"
        safe_name = re.sub(r"-+", "-", safe_name)
        safe_owner = re.sub(r"-+", "-", safe_owner)
        db_path = os.path.join(storage, f"{safe_owner}-{safe_name}.db")
        try:
            mtime = os.stat(db_path).st_mtime
            if mtime > newest:
                newest = mtime
        except OSError:
            pass
    return newest


def build_package_registry(all_repos: list[dict]) -> dict[str, list[str]]:
    """Build a mapping of {package_name: [repo_id, ...]} from all indexed repos.

    Uses module-level cache invalidated by newest index file mtime.

    Args:
        all_repos: List of repo dicts from list_repos() with 'repo' and 'source_root' keys.

    Returns:
        Dict mapping package names to lists of repo IDs that publish them.
    """
    global _registry_cache, _registry_cache_mtime

    newest_mtime = _get_newest_index_mtime(all_repos)
    if _registry_cache is not None and newest_mtime == _registry_cache_mtime:
        return _registry_cache

    registry: dict[str, list[str]] = {}

    for repo in all_repos:
        repo_id = repo.get("repo", "")
        if not repo_id:
            continue

        # Get package_names from index if available (stored in CodeIndex)
        pkg_names: list[str] = []

        # First check if the index has package_names (loaded in-memory)
        idx_pkg_names = repo.get("package_names")
        if idx_pkg_names and isinstance(idx_pkg_names, list):
            pkg_names = idx_pkg_names
        else:
            # Fall back to extracting from source_root at registry build time
            source_root = repo.get("source_root", "")
            if source_root and os.path.isdir(source_root):
                try:
                    pkg_names = extract_package_names(source_root)
                except Exception:
                    logger.debug("Failed to extract package names for %s", repo_id, exc_info=True)

        for pkg in pkg_names:
            if pkg:
                registry.setdefault(pkg, []).append(repo_id)

    _registry_cache = registry
    _registry_cache_mtime = newest_mtime
    return registry


def invalidate_registry_cache() -> None:
    """Clear the module-level package registry cache."""
    global _registry_cache, _registry_cache_mtime
    _registry_cache = None
    _registry_cache_mtime = 0.0


# ---------------------------------------------------------------------------
# Cross-repo resolution helpers
# ---------------------------------------------------------------------------

def find_repos_for_package(package_name: str, all_repos: list[dict]) -> list[str]:
    """Return repo IDs that publish the given package name.

    Args:
        package_name: Normalized package name.
        all_repos: List of repo dicts from list_repos().

    Returns:
        List of repo IDs.
    """
    registry = build_package_registry(all_repos)
    return list(registry.get(package_name, []))


def resolve_cross_repo_file(
    specifier: str,
    language: str,
    importing_repo_id: str,
    all_repos: list[dict],
    storage_path: Optional[str] = None,
) -> list[dict]:
    """Given an unresolved import specifier, find files in other indexed repos.

    Args:
        specifier: Raw import specifier string.
        language: Language of the importing file.
        importing_repo_id: Repo ID of the importing repo (to exclude self).
        all_repos: List of repo dicts from list_repos().
        storage_path: Custom storage path.

    Returns:
        List of dicts: [{repo_id, file, package_name}], or [] if no match.
    """
    root_pkg = extract_root_package_from_specifier(specifier, language)
    if not root_pkg:
        return []

    candidate_repos = find_repos_for_package(root_pkg, all_repos)
    # Exclude the importing repo itself
    candidate_repos = [r for r in candidate_repos if r != importing_repo_id]

    if not candidate_repos:
        return []

    results: list[dict] = []
    from ..storage import IndexStore
    store = IndexStore(base_path=storage_path)

    for repo_id in candidate_repos:
        if "/" not in repo_id:
            continue
        owner, name = repo_id.split("/", 1)
        index = store.load_index(owner, name)
        if not index:
            continue

        # Find the best entry-point file for this repo
        entry_file = _find_entry_point(index.source_files, language)
        results.append({
            "repo_id": repo_id,
            "file": entry_file or "",
            "package_name": root_pkg,
        })

    return results


def _find_entry_point(source_files: list[str], language: str) -> Optional[str]:
    """Find the most likely entry-point file in a list of source files.

    Priority: __init__.py, index.js/ts, main.go, lib.rs, etc.
    """
    lang = language.lower() if language else ""

    # Ordered candidate patterns (most to least specific)
    patterns: list[str] = []

    if lang in ("python",):
        patterns = ["__init__.py", "main.py", "app.py"]
    elif lang in ("javascript", "typescript", "tsx", "jsx", "vue", "astro", "svelte"):
        patterns = [
            "index.js", "index.ts", "index.tsx", "index.jsx",
            "main.js", "main.ts", "src/index.js", "src/index.ts",
        ]
    elif lang == "go":
        patterns = ["main.go", "cmd/main.go"]
    elif lang == "rust":
        patterns = ["src/lib.rs", "src/main.rs", "lib.rs", "main.rs"]
    else:
        patterns = ["index.js", "main.py", "main.go", "__init__.py"]

    # Try basename-only matches first (common case)
    for pattern in patterns:
        for sf in source_files:
            if sf == pattern or sf.endswith("/" + pattern):
                return sf

    # Fall back to first file
    return source_files[0] if source_files else None
