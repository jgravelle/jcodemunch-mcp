"""Shared utilities for route/middleware context providers.

This module provides common functionality used by multiple framework-specific
route providers: Flask, FastAPI, Express, Go routers, Django, Rails, etc.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

from .base import FileContext

# Try to use tomllib for proper TOML parsing (Python 3.11+)
if sys.version_info >= (3, 11):
    import tomllib
else:
    tomllib = None  # type: ignore


# ---------------------------------------------------------------------------
# Expanded entry-point decorator regex (canonical, shared by all consumers)
# ---------------------------------------------------------------------------

ENTRY_POINT_DECORATOR_RE = re.compile(
    # Python: Flask/FastAPI/Sanic + Celery/task queues + pytest + Django DRF
    r"@(?:app|router|blueprint|api|bp|flask_app)\."
    r"(?:route|get|post|put|delete|patch|head|options|websocket|before_request|after_request)"
    r"|@pytest\.fixture"
    r"|@(?:cli|app)\.command"
    r"|@(?:celery|huey|dramatiq|rq)\."
    r"|@task\b"
    r"|@event_handler\b"
    r"|@on_event\b"
    # Java: Spring Boot annotations
    r"|@(?:Get|Post|Put|Delete|Patch|Request)Mapping\b"
    r"|@(?:Rest)?Controller\b"
    # TypeScript: NestJS decorators
    r"|@(?:Get|Post|Put|Patch|Delete|Head|Options|All)\s*\("
    r"|@Controller\s*\("
    # C#: ASP.NET attributes
    r"|\[Http(?:Get|Post|Put|Patch|Delete|Head|Options)(?:\s*\(?|$)"
    r"|\[Route\s*(?:\(|$)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Dependency detection
# ---------------------------------------------------------------------------

_MANIFEST_PARSERS: dict[str, callable] = {}


def _parse_requirements_txt(content: str) -> set[str]:
    """Extract package names from a requirements.txt file."""
    packages: set[str] = set()
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # Handle options like -r, -e, --index-url, etc.
        if line.startswith("-"):
            continue
        # Handle versions: package==1.0.0, package>=1.0.0, package~=1.0.0
        pkg = re.split(r"[<>=!~]", line)[0].strip()
        if pkg:
            packages.add(pkg)
    return packages


def _parse_pyproject_toml_deps(content: str) -> set[str]:
    """Extract package names from pyproject.toml [project.dependencies] or [project.optional-dependencies]."""
    packages: set[str] = set()

    # Try to use tomllib for proper TOML parsing
    if tomllib is not None:
        try:
            data = tomllib.loads(content)
            project = data.get("project", {})
            for dep_list in [project.get("dependencies", []), project.get("optional-dependencies", {})]:
                if isinstance(dep_list, list):
                    for dep in dep_list:
                        if isinstance(dep, str):
                            pkg = re.split(r"[<>=!~]", dep)[0].strip()
                            if pkg:
                                packages.add(pkg)
                        elif isinstance(dep, dict):
                            # Inline table format: {package = "version"}
                            for pkg in dep.keys():
                                packages.add(pkg)
                elif isinstance(dep_list, dict):
                    # Optional deps dict: {dev: ["pkg1", "pkg2"]}
                    for dep in dep_list.values():
                        if isinstance(dep, list):
                            for d in dep:
                                if isinstance(d, str):
                                    pkg = re.split(r"[<>=!~]", d)[0].strip()
                                    if pkg:
                                        packages.add(pkg)
            return packages
        except Exception:
            pass

    # Fallback: regex-based extraction for Python < 3.11 (no tomllib)
    # 1) Match dependencies = ["pkg1", "pkg2>=1.0", "pkg3[extra]"]
    for m in re.finditer(r'dependencies\s*=\s*\[([^\]]*)\]', content, re.DOTALL):
        for dep_m in re.finditer(r'["\']([^"\']+)["\']', m.group(1)):
            dep = dep_m.group(1)
            pkg = re.split(r'[\[<>=!~;]', dep)[0].strip()
            if pkg:
                packages.add(pkg)
    # 2) Match optional-dependencies groups: dev = ["pytest", "flask[async]"]
    #    These appear under [project.optional-dependencies] as key = [...]
    opt_section = re.search(
        r'\[project\.optional-dependencies\]\s*\n(.*?)(?:\n\[|\Z)',
        content, re.DOTALL,
    )
    if opt_section:
        for m in re.finditer(r'\w+\s*=\s*\[([^\]]*)\]', opt_section.group(1), re.DOTALL):
            for dep_m in re.finditer(r'["\']([^"\']+)["\']', m.group(1)):
                dep = dep_m.group(1)
                pkg = re.split(r'[\[<>=!~;]', dep)[0].strip()
                if pkg:
                    packages.add(pkg)
    return packages


def _parse_package_json_deps(content: dict) -> set[str]:
    """Extract package names from package.json dependencies/devDependencies."""
    packages: set[str] = set()
    for key in ("dependencies", "devDependencies", "optionalDependencies"):
        if key in content:
            packages.update(content[key].keys())
    return packages


def _parse_build_gradle(content: str) -> set[str]:
    """Extract dependency strings from build.gradle."""
    packages: set[str] = set()
    for line in content.splitlines():
        line = line.strip()
        # Match implementation 'group:artifact:version' or compile 'group:artifact'
        m = re.search(r"['\"]([^'\"]+)['\"]", line)
        if m:
            packages.add(m.group(1))
    return packages


def _parse_go_mod(content: str) -> set[str]:
    """Extract module paths from go.mod require block."""
    packages: set[str] = set()
    in_require = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("require ("):
            in_require = True
            continue
        if in_require and stripped == ")":
            in_require = False
            continue
        if in_require:
            # Each line should be a module path with optional version
            parts = stripped.split()
            if parts:
                pkg = parts[0].strip()
                if pkg and not pkg.startswith("//"):
                    packages.add(pkg)
    return packages


def _parse_gemfile(content: str) -> set[str]:
    """Extract gem names from Gemfile."""
    packages: set[str] = set()
    for line in content.splitlines():
        line = line.strip()
        m = re.match(r"gem\s+['\"]([^'\"]+)['\"]", line)
        if m:
            packages.add(m.group(1))
    return packages


def _parse_mix_exs(content: str) -> set[str]:
    """Extract dependency names from mix.exs."""
    packages: set[str] = set()
    in_deps = False
    for line in content.splitlines():
        stripped = line.strip()
        if "def deps do" in stripped:
            in_deps = True
            continue
        if in_deps and stripped.startswith("end"):
            in_deps = False
            continue
        if in_deps and ":" in stripped:
            # Match {:phoenix, "~> 1.7.0"}
            m = re.search(r":(\w+)", stripped)
            if m:
                packages.add(m.group(1))
    return packages


_MANIFEST_PARSERS = {
    "requirements.txt": _parse_requirements_txt,
    "pyproject.toml": _parse_pyproject_toml_deps,
    "package.json": lambda content: _parse_package_json_deps(json.loads(content) if isinstance(content, str) else content),
    "build.gradle": _parse_build_gradle,
    "go.mod": _parse_go_mod,
    "Gemfile": _parse_gemfile,
    "mix.exs": _parse_mix_exs,
}


def has_dependency(folder: Path, name: str, manifests: list[str]) -> bool:
    """Check if dependency exists in any manifest file.

    Args:
        folder: Project root path.
        name: Dependency name to search for (substring match).
        manifests: List of manifest filenames to check.

    Returns:
        True if the dependency is found in any manifest.
    """
    name_lower = name.lower()
    for manifest in manifests:
        manifest_path = folder / manifest
        if not manifest_path.exists():
            continue
        try:
            content = manifest_path.read_text("utf-8", errors="replace")
        except Exception:
            continue

        parser = _MANIFEST_PARSERS.get(manifest)
        if parser is None:
            continue

        try:
            if manifest == "package.json":
                packages = parser(content)
            else:
                packages = parser(content)
        except Exception:
            continue

        for pkg in packages:
            if name_lower in pkg.lower():
                return True
    return False


def read_package_json(folder: Path) -> dict:
    """Safe package.json reader returning {} on failure."""
    pkg_path = folder / "package.json"
    if not pkg_path.exists():
        return {}
    try:
        return json.loads(pkg_path.read_text("utf-8", errors="replace"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# FileContext factory for routes
# ---------------------------------------------------------------------------

def make_route_file_context(
    framework: str,
    routes: list[dict],
    kind: str = "route",
) -> FileContext:
    """Build a standardized FileContext for route files.

    Args:
        framework: Framework name (e.g., "fastapi", "flask", "express").
        routes: List of route dicts, each with at least "verb" and "path".
        kind: Kind of context ("route" or "middleware").

    Returns:
        FileContext with standardized tags and properties.
    """
    tags = [f"{framework}-{kind}", "endpoint", "http"]

    if not routes:
        return FileContext(
            description="",
            tags=tags,
            properties={"framework": framework},
        )

    # Build description from routes
    route_summaries = []
    http_methods: set[str] = set()
    for route in routes:
        verb = route.get("verb", "GET").upper()
        path = route.get("path", "/")
        http_methods.add(verb)
        route_summaries.append(f"{verb} {path}")

    # Truncate if too many routes
    if len(route_summaries) > 20:
        description = f"{framework.capitalize()} {kind}: {', '.join(route_summaries[:20])} ... (+{len(route_summaries) - 20} more)"
    else:
        description = f"{framework.capitalize()} {kind}: {', '.join(route_summaries)}"

    return FileContext(
        description=description,
        tags=tags,
        properties={
            "routes": "; ".join(route_summaries),
            "http_methods": ", ".join(sorted(http_methods)),
            "framework": framework,
        },
    )
