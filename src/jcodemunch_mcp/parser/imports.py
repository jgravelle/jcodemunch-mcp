"""Extract import statements from source files using language-specific regex patterns."""

import json
import posixpath
import re
import threading
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Per-language regex patterns
# ---------------------------------------------------------------------------

# JS/TS: import { A, B } from 'specifier'
_JS_IMPORT_FROM = re.compile(
    r"""(?:^|\n)\s*(?:import|export)\s+(?:type\s+)?"""
    r"""(?:\*\s+as\s+\w+|\{([^}]*)\}|(\w+)(?:\s*,\s*\{([^}]*)\})?)\s+from\s+['"]([^'"]+)['"]""",
    re.MULTILINE,
)
# JS/TS: import 'specifier' (side-effect)
_JS_SIDE_EFFECT = re.compile(r"""(?:^|\n)\s*import\s+['"]([^'"]+)['"]""", re.MULTILINE)
# JS/TS: require('specifier')
_JS_REQUIRE = re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""", re.MULTILINE)
# JS/TS: export { A } from 'specifier'  (re-export without full import parse)
_JS_REEXPORT = re.compile(r"""(?:^|\n)\s*export\s+\{[^}]*\}\s+from\s+['"]([^'"]+)['"]""", re.MULTILINE)
# JS/TS: import('specifier') — dynamic import (Vue Router lazy routes, code splitting)
_JS_DYNAMIC_IMPORT = re.compile(r"""import\s*\(\s*['"]([^'"]+)['"]\s*\)""", re.MULTILINE)

# Python: from .module import A, B  /  import os
# Allow optional leading whitespace so function-local imports inside def/class
# bodies are also captured (common pattern for breaking circular imports).
_PY_FROM = re.compile(
    r"""^[ \t]*from\s+(\.{0,4}[\w.]*)\s+import\s+(.+)$""", re.MULTILINE
)
_PY_IMPORT = re.compile(r"""^[ \t]*import\s+([\w.,][^\n]*)$""", re.MULTILINE)

# Go: import "pkg"  or import ( ... )
_GO_IMPORT_BLOCK = re.compile(r"""import\s*\((.*?)\)""", re.DOTALL)
_GO_IMPORT_LINE = re.compile(r"""import\s+(?:\w+\s+)?["']([^"']+)["']""")
_GO_IMPORT_ENTRY = re.compile(r"""(?:\w+\s+)?["']([^"']+)["']""")

# Java/Kotlin: import com.example.Foo
_JAVA_IMPORT = re.compile(r"""^import\s+(?:static\s+)?([\w.]+)\s*;?$""", re.MULTILINE)

# Rust: use crate::foo::{Bar, Baz}
_RUST_USE = re.compile(r"""^use\s+([\w::{},\s*]+)\s*;""", re.MULTILINE)

# C/C++/ObjC: #include <foo>  or  #include "foo"
_C_INCLUDE = re.compile(r"""^#include\s+[<"]([^>"]+)[>"]""", re.MULTILINE)

# Assembly: .include "foo" / .incbin "foo" / %include "foo"
_ASM_INCLUDE = re.compile(r"""^\s*[.%]include\s+["']([^"']+)["']""", re.MULTILINE | re.IGNORECASE)

# VHDL: library ieee; / use ieee.std_logic_1164.all;
_VHDL_LIBRARY = re.compile(r"""^\s*library\s+(\w+)\s*;""", re.MULTILINE | re.IGNORECASE)
_VHDL_USE = re.compile(r"""^\s*use\s+([\w.]+)\s*;""", re.MULTILINE | re.IGNORECASE)

# Verilog/SystemVerilog: `include "foo.vh"
_VERILOG_INCLUDE = re.compile(r"""^\s*`include\s+["']([^"']+)["']""", re.MULTILINE)

# Ruby: require 'foo' / require_relative 'bar'
_RUBY_REQUIRE = re.compile(r"""(?:require|require_relative)\s+['"]([^'"]+)['"]""", re.MULTILINE)

# C#: using System.Foo;
_CSHARP_USING = re.compile(r"""^using\s+(?:static\s+)?(?:(\w+)\s*=\s*)?([\w.]+)\s*;""", re.MULTILINE)

# PHP: use App\Foo\Bar;  /  require/include
_PHP_USE = re.compile(r"""^use\s+([\w\\]+)(?:\s+as\s+\w+)?\s*;""", re.MULTILINE)
_PHP_REQUIRE = re.compile(r"""(?:require|include)(?:_once)?\s+['"]([^'"]+)['"]""", re.MULTILINE)

# Swift: import Foundation
_SWIFT_IMPORT = re.compile(r"""^import\s+(\w+)""", re.MULTILINE)

# Scala: import scala.collection.mutable
_SCALA_IMPORT = re.compile(r"""^import\s+([\w.{}]+)""", re.MULTILINE)

# Haskell: import Data.Map (fromList)
_HASKELL_IMPORT = re.compile(r"""^import\s+(?:qualified\s+)?(\S+)""", re.MULTILINE)


def _clean_names(raw: str) -> list[str]:
    """Parse comma-separated names from an import clause, stripping aliases/whitespace."""
    names = []
    for part in raw.split(","):
        # Handle 'Foo as Bar' or 'type Foo' — take the original name
        part = part.strip()
        if not part:
            continue
        # Remove 'type' keyword prefix (TS)
        part = re.sub(r"^type\s+", "", part)
        # Take first token before 'as'
        names.append(part.split()[0])
    return [n for n in names if n]


def _extract_js_imports(content: str) -> list[dict]:
    edges = []
    seen: set[str] = set()

    def add(specifier: str, names: list[str]) -> None:
        if specifier not in seen:
            seen.add(specifier)
            edges.append({"specifier": specifier, "names": names})
        else:
            # Merge names into existing entry
            for e in edges:
                if e["specifier"] == specifier:
                    e["names"] = sorted(set(e["names"]) | set(names))
                    break

    for m in _JS_IMPORT_FROM.finditer(content):
        named_group, default_group, extra_named, specifier = m.group(1), m.group(2), m.group(3), m.group(4)
        names: list[str] = []
        if named_group:
            names.extend(_clean_names(named_group))
        if default_group:
            names.append(default_group)
        if extra_named:
            names.extend(_clean_names(extra_named))
        add(specifier, names)

    for m in _JS_SIDE_EFFECT.finditer(content):
        add(m.group(1), [])

    for m in _JS_REQUIRE.finditer(content):
        add(m.group(1), [])

    for m in _JS_REEXPORT.finditer(content):
        # Only add if not already captured by _JS_IMPORT_FROM
        if m.group(1) not in seen:
            add(m.group(1), [])

    for m in _JS_DYNAMIC_IMPORT.finditer(content):
        add(m.group(1), [])

    return edges


def _extract_python_imports(content: str) -> list[dict]:
    edges = []
    seen: set[str] = set()

    for m in _PY_FROM.finditer(content):
        module, names_str = m.group(1), m.group(2)
        # Skip 'from __future__ import ...'
        if module.strip() == "__future__":
            continue
        specifier = module.strip()
        names = _clean_names(names_str)
        # Handle 'from foo import (A, B)' — strip parens
        names = [n.strip("()") for n in names]
        names = [n for n in names if n and n != "*"]
        if specifier not in seen:
            seen.add(specifier)
            edges.append({"specifier": specifier, "names": names})

    for m in _PY_IMPORT.finditer(content):
        for mod in m.group(1).split(","):
            mod = mod.strip().split()[0]  # handle 'import os as operating_system'
            if mod and mod not in seen:
                seen.add(mod)
                edges.append({"specifier": mod, "names": []})

    return edges


def _extract_go_imports(content: str) -> list[dict]:
    edges = []
    seen: set[str] = set()

    # Block imports
    for block_m in _GO_IMPORT_BLOCK.finditer(content):
        for entry_m in _GO_IMPORT_ENTRY.finditer(block_m.group(1)):
            spec = entry_m.group(1)
            if spec not in seen:
                seen.add(spec)
                edges.append({"specifier": spec, "names": []})

    # Single-line imports
    for m in _GO_IMPORT_LINE.finditer(content):
        spec = m.group(1)
        if spec not in seen:
            seen.add(spec)
            edges.append({"specifier": spec, "names": []})

    return edges


def _extract_java_imports(content: str, language: str) -> list[dict]:
    edges = []
    for m in _JAVA_IMPORT.finditer(content):
        qualified = m.group(1)
        # Last component is the type name
        parts = qualified.rsplit(".", 1)
        names = [parts[-1]] if len(parts) > 1 else []
        edges.append({"specifier": qualified, "names": names})
    return edges


def _extract_rust_imports(content: str) -> list[dict]:
    edges = []
    seen: set[str] = set()
    for m in _RUST_USE.finditer(content):
        raw = m.group(1).strip()
        # Simplify: use the first path segment as specifier
        base = raw.split("::")[0].strip()
        if base not in seen:
            seen.add(base)
            # Extract names from braces if present
            names = []
            brace_m = re.search(r"\{([^}]+)\}", raw)
            if brace_m:
                names = _clean_names(brace_m.group(1))
            edges.append({"specifier": raw.split("{")[0].rstrip(":").strip(), "names": names})
    return edges


def _extract_c_imports(content: str) -> list[dict]:
    return [{"specifier": m.group(1), "names": []} for m in _C_INCLUDE.finditer(content)]


def _extract_asm_imports(content: str) -> list[dict]:
    return [{"specifier": m.group(1), "names": []} for m in _ASM_INCLUDE.finditer(content)]


def _extract_vhdl_imports(content: str) -> list[dict]:
    edges = []
    seen: set[str] = set()
    for m in _VHDL_LIBRARY.finditer(content):
        lib = m.group(1).lower()
        if lib != "work" and lib not in seen:
            seen.add(lib)
            edges.append({"specifier": lib, "names": []})
    for m in _VHDL_USE.finditer(content):
        spec = m.group(1)
        if spec not in seen:
            seen.add(spec)
            edges.append({"specifier": spec, "names": []})
    return edges


def _extract_verilog_imports(content: str) -> list[dict]:
    return [{"specifier": m.group(1), "names": []} for m in _VERILOG_INCLUDE.finditer(content)]


def _extract_ruby_imports(content: str) -> list[dict]:
    return [{"specifier": m.group(1), "names": []} for m in _RUBY_REQUIRE.finditer(content)]


def _extract_csharp_imports(content: str) -> list[dict]:
    edges = []
    for m in _CSHARP_USING.finditer(content):
        qualified = m.group(2)
        parts = qualified.rsplit(".", 1)
        names = [parts[-1]] if len(parts) > 1 else []
        edges.append({"specifier": qualified, "names": names})
    return edges


def _extract_php_imports(content: str) -> list[dict]:
    edges = []
    for m in _PHP_USE.finditer(content):
        qualified = m.group(1)
        parts = qualified.rsplit("\\", 1)
        names = [parts[-1]] if len(parts) > 1 else []
        edges.append({"specifier": qualified, "names": names})
    for m in _PHP_REQUIRE.finditer(content):
        edges.append({"specifier": m.group(1), "names": []})
    return edges


def _extract_swift_imports(content: str) -> list[dict]:
    return [{"specifier": m.group(1), "names": []} for m in _SWIFT_IMPORT.finditer(content)]


def _extract_scala_imports(content: str) -> list[dict]:
    edges = []
    for m in _SCALA_IMPORT.finditer(content):
        raw = m.group(1)
        brace_m = re.search(r"\{([^}]+)\}", raw)
        names = _clean_names(brace_m.group(1)) if brace_m else []
        edges.append({"specifier": raw.split("{")[0].rstrip(".").strip(), "names": names})
    return edges


def _extract_haskell_imports(content: str) -> list[dict]:
    return [{"specifier": m.group(1), "names": []} for m in _HASKELL_IMPORT.finditer(content)]


# Dart: import 'package:flutter/material.dart' / import 'dart:async' / import './foo.dart'
_DART_IMPORT = re.compile(
    r"""^\s*(?:import|export)\s+['"]([^'"]+)['"]""", re.MULTILINE
)


def _extract_dart_imports(content: str) -> list[dict]:
    return [{"specifier": m.group(1), "names": []} for m in _DART_IMPORT.finditer(content)]


# SQL/dbt: {{ ref('model_name') }} and {{ source('source', 'table') }}
_DBT_REF = re.compile(
    r"""\{\{[\s-]*ref\s*\(\s*['"]([^'"]+)['"]\s*(?:,\s*v\s*=\s*\d+\s*)?\)\s*[\s-]*\}\}"""
)
_DBT_SOURCE = re.compile(
    r"""\{\{[\s-]*source\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)\s*[\s-]*\}\}"""
)


def _extract_sql_dbt_imports(content: str) -> list[dict]:
    """Extract dbt ref() and source() calls as import edges."""
    edges = []
    seen: set[str] = set()

    for m in _DBT_REF.finditer(content):
        model_name = m.group(1)
        if model_name not in seen:
            seen.add(model_name)
            edges.append({"specifier": model_name, "names": []})

    for m in _DBT_SOURCE.finditer(content):
        source_name = m.group(1)
        table_name = m.group(2)
        specifier = f"source:{source_name}.{table_name}"
        if specifier not in seen:
            seen.add(specifier)
            edges.append({"specifier": specifier, "names": []})

    return edges


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Vue <template> component extraction
# ---------------------------------------------------------------------------

_VUE_TEMPLATE_BLOCK = re.compile(r"<template\b[^>]*>(.*)</template>", re.DOTALL)

_VUE_TEMPLATE_COMPONENT = re.compile(
    r"""<(?P<tag>[A-Z][\w]*|[a-z]+-[\w-]+)[\s/>]""",
    re.MULTILINE,
)

_HTML_STANDARD_ELEMENTS = frozenset({
    # HTML5 elements
    "a", "abbr", "address", "area", "article", "aside", "audio",
    "b", "base", "bdi", "bdo", "blockquote", "body", "br", "button",
    "canvas", "caption", "cite", "code", "col", "colgroup",
    "data", "datalist", "dd", "del", "details", "dfn", "dialog", "div", "dl", "dt",
    "em", "embed",
    "fieldset", "figcaption", "figure", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hgroup", "hr", "html",
    "i", "iframe", "img", "input", "ins",
    "kbd",
    "label", "legend", "li", "link",
    "main", "map", "mark", "menu", "meta", "meter",
    "nav", "noscript",
    "object", "ol", "optgroup", "option", "output",
    "p", "param", "picture", "pre", "progress",
    "q",
    "rp", "rt", "ruby",
    "s", "samp", "script", "search", "section", "select", "slot", "small", "source", "span",
    "strong", "style", "sub", "summary", "sup",
    "table", "tbody", "td", "template", "textarea", "tfoot", "th", "thead", "time", "title", "tr", "track",
    "u", "ul",
    "var", "video",
    "wbr",
    # SVG elements
    "svg", "path", "circle", "rect", "line", "g", "defs", "use", "text",
    "polygon", "polyline", "ellipse", "image", "mask", "pattern",
    # Vue built-in elements
    "transition", "transition-group", "keep-alive", "teleport", "suspense", "component",
})


def _kebab_to_pascal(name: str) -> str:
    """Convert kebab-case to PascalCase: 'user-table' → 'UserTable'."""
    return "".join(part.capitalize() for part in name.split("-"))


def _extract_vue_template_components(content: str) -> list[str]:
    """Extract component names used in Vue <template> blocks."""
    m = _VUE_TEMPLATE_BLOCK.search(content)
    if not m:
        return []
    template = m.group(1)

    components: set[str] = set()
    for cm in _VUE_TEMPLATE_COMPONENT.finditer(template):
        tag = cm.group("tag")
        # Normalize to lowercase for HTML check
        if tag.lower() not in _HTML_STANDARD_ELEMENTS:
            components.add(tag)
    return sorted(components)


def _extract_vue_imports(content: str) -> list[dict]:
    """Extract imports from Vue SFC: script imports + template component usage."""
    edges = _extract_js_imports(content)

    template_components = _extract_vue_template_components(content)
    if not template_components:
        return edges

    # Collect already-imported names from <script> for dedup
    imported_names: set[str] = set()
    for edge in edges:
        imported_names.update(edge["names"])

    for component in template_components:
        # Check if already imported (PascalCase or kebab→PascalCase)
        pascal = _kebab_to_pascal(component) if "-" in component else component
        if component in imported_names or pascal in imported_names:
            continue
        # Synthetic import edge for template-only component usage
        edges.append({"specifier": pascal, "names": [pascal]})

    return edges


_LANGUAGE_EXTRACTORS = {
    "javascript": _extract_js_imports,
    "typescript": _extract_js_imports,
    "tsx": _extract_js_imports,
    "jsx": _extract_js_imports,
    "vue": _extract_vue_imports,
    "python": _extract_python_imports,
    "go": _extract_go_imports,
    "java": lambda c: _extract_java_imports(c, "java"),
    "kotlin": lambda c: _extract_java_imports(c, "kotlin"),
    "rust": _extract_rust_imports,
    "c": _extract_c_imports,
    "cpp": _extract_c_imports,
    "objc": _extract_c_imports,
    "arduino": _extract_c_imports,
    "ruby": _extract_ruby_imports,
    "csharp": _extract_csharp_imports,
    "php": _extract_php_imports,
    "swift": _extract_swift_imports,
    "scala": _extract_scala_imports,
    "haskell": _extract_haskell_imports,
    "dart": _extract_dart_imports,
    "sql": _extract_sql_dbt_imports,
    "asm": _extract_asm_imports,
    "vhdl": _extract_vhdl_imports,
    "verilog": _extract_verilog_imports,
}


def extract_imports(content: str, file_path: str, language: str) -> list[dict]:
    """Extract import edges from source file content.

    Args:
        content: Raw source file text.
        file_path: Path of the file (used for context; not used in extraction).
        language: Language name (must match LANGUAGE_REGISTRY keys).

    Returns:
        List of dicts: [{"specifier": str, "names": list[str]}, ...]
        where ``specifier`` is the raw module/path string and ``names`` are
        the specific identifiers imported from that module.
    """
    extractor = _LANGUAGE_EXTRACTORS.get(language)
    if extractor is None:
        return []
    try:
        return extractor(content)
    except Exception:
        return []


_JS_EXTENSIONS = (".js", ".ts", ".jsx", ".tsx", ".vue", ".mjs", ".cjs", ".svelte")
_PY_EXTENSIONS = (".py",)
_RUBY_EXTENSIONS = (".rb",)
_ALL_EXTENSIONS = _JS_EXTENSIONS + _PY_EXTENSIONS + _RUBY_EXTENSIONS + (".go",)

# ---------------------------------------------------------------------------
# PSR-4 namespace resolution (PHP / Composer)
# ---------------------------------------------------------------------------

# Module-level cache: source_root -> {namespace_prefix: relative_dir}
_psr4_map_cache: dict[str, dict[str, str]] = {}


def build_psr4_map(source_root: str) -> dict[str, str]:
    """Parse composer.json PSR-4 autoload mappings for a project root.

    Returns a dict mapping namespace prefix strings (e.g. ``"App\\\\"`` ) to
    repo-root-relative directory strings (e.g. ``"app/"``).  Includes both
    ``autoload`` and ``autoload-dev`` sections.  Results are module-level
    cached by ``source_root``; a re-index is needed if composer.json changes.

    Returns an empty dict when composer.json is absent or cannot be parsed.
    """
    if not source_root:
        return {}
    if source_root in _psr4_map_cache:
        return _psr4_map_cache[source_root]

    composer_path = Path(source_root) / "composer.json"
    if not composer_path.exists():
        _psr4_map_cache[source_root] = {}
        return {}

    try:
        data = json.loads(composer_path.read_text("utf-8", errors="replace"))
        mapping: dict[str, str] = {}
        for section in ("autoload", "autoload-dev"):
            for prefix, paths in data.get(section, {}).get("psr-4", {}).items():
                if prefix in mapping:
                    continue  # first definition wins
                if isinstance(paths, str):
                    paths = [paths]
                if paths:
                    rel_dir = paths[0].replace("\\", "/").rstrip("/") + "/"
                    mapping[prefix] = rel_dir
        _psr4_map_cache[source_root] = mapping
        return mapping
    except Exception:
        _psr4_map_cache[source_root] = {}
        return {}


def resolve_php_namespace(
    fqn: str,
    psr4_map: dict[str, str],
    source_files: set[str],
) -> Optional[str]:
    """Resolve a PHP fully-qualified class name to a repo-relative file path.

    Example: ``"App\\\\Models\\\\User"`` with ``{"App\\\\": "app/"}``
    resolves to ``"app/Models/User.php"``.

    Prefixes are matched longest-first so more specific mappings win.
    Returns ``None`` if no prefix matches or the resolved path is not in
    ``source_files``.
    """
    for prefix, base_dir in sorted(psr4_map.items(), key=lambda x: -len(x[0])):
        if fqn.startswith(prefix):
            relative = fqn[len(prefix):].replace("\\", "/") + ".php"
            candidate = base_dir + relative
            if candidate in source_files:
                return candidate
    return None


# Cache for SQL stem lookups — avoids O(n) scans when resolve_specifier is
# called repeatedly with the same source_files set (common in tight loops).
# Keyed by frozenset of .sql paths (content identity, not object identity) to
# prevent id() aliasing after GC (C7-A).
_sql_stem_cache: dict[frozenset, dict[str, str]] = {}
_SQL_STEM_CACHE_MAX = 4
_SQL_STEM_LOCK = threading.Lock()


def _get_sql_stems(source_files: set[str]) -> dict[str, str]:
    """Return a lowered-stem -> file_path dict for .sql files, cached by content."""
    key = frozenset(f for f in source_files if f.endswith(".sql"))
    with _SQL_STEM_LOCK:
        cached = _sql_stem_cache.get(key)
        if cached is not None:
            return cached

    # Miss: build without holding the lock
    stems: dict[str, str] = {}
    for sf in key:
        stem = posixpath.splitext(posixpath.basename(sf))[0].lower()
        if stem not in stems:  # first match wins
            stems[stem] = sf

    with _SQL_STEM_LOCK:
        if len(_sql_stem_cache) >= _SQL_STEM_CACHE_MAX:
            _sql_stem_cache.pop(next(iter(_sql_stem_cache)))
        _sql_stem_cache[key] = stems
    return stems


def _candidates(base: str) -> list[str]:
    """Generate path candidates with and without extension."""
    cands = [base]
    _, ext = posixpath.splitext(base)
    if not ext:
        for e in _ALL_EXTENSIONS:
            cands.append(base + e)
        # index file
        for e in _JS_EXTENSIONS:
            cands.append(posixpath.join(base, "index" + e))
        cands.append(posixpath.join(base, "__init__.py"))
    elif ext == ".js":
        # TypeScript ESM convention: 'import "./foo.js"' may resolve to './foo.ts' or './foo.tsx'
        stem = base[:-3]
        cands.append(stem + ".ts")
        cands.append(stem + ".tsx")
    return cands


# Cache: frozenset(source_files) -> tuple of source root prefixes ("" = repo root).
# Keyed by the frozenset itself (not id) so the cache stays correct across
# unrelated call sites that happen to reuse memory addresses. Frozenset hashing
# is cached by Python after the first call, so repeat lookups are O(1).
_python_roots_cache: dict[frozenset, tuple[str, ...]] = {}

# Cache: frozenset(source_files) -> dict mapping package basename to the list
# of parent directories where a same-named package dir (containing __init__.py)
# exists. Enables resolving specifiers whose effective source root is injected
# at runtime by conftest.py / PYTHONPATH / setuptools package_dir — the
# specifier's first segment names the package, and its parent must be acting
# as a source root, even if our structural detector can't see that.
_python_package_parents_cache: dict[frozenset, dict[str, tuple[str, ...]]] = {}


def _python_source_roots(source_files) -> tuple[str, ...]:
    """Detect Python package source roots from the indexed file set.

    A Python source root is the parent directory of a top-level package, where
    a top-level package is a directory containing ``__init__.py`` whose parent
    directory does NOT contain ``__init__.py``. For modern PEP 420 namespace
    packages (no __init__.py at all), falls back to top-level directories
    that contain at least one .py file. Repo root is included as ``""``.
    """
    # Normalize to frozenset for hashable cache key. set inputs become frozenset;
    # frozenset inputs pass through unchanged.
    cache_key = source_files if isinstance(source_files, frozenset) else frozenset(source_files)
    cached = _python_roots_cache.get(cache_key)
    if cached is not None:
        return cached

    # Collect every directory that has an __init__.py
    package_dirs: set[str] = set()
    for f in source_files:
        if f.endswith("/__init__.py"):
            package_dirs.add(f[: -len("/__init__.py")])
        elif f == "__init__.py":
            package_dirs.add("")

    roots: set[str] = set()
    if package_dirs:
        # A "top-level" package is one whose parent is NOT itself a package.
        for d in package_dirs:
            parent = posixpath.dirname(d)
            if parent not in package_dirs:
                roots.add(parent)
    else:
        # PEP 420 namespace packages: fall back to top-level directories
        # containing .py files.
        for f in source_files:
            if f.endswith(".py"):
                top = f.split("/", 1)[0] if "/" in f else ""
                roots.add(top)

    # Always include repo root as a fallback
    roots.add("")
    result = tuple(sorted(roots))
    _python_roots_cache[cache_key] = result
    return result


def _python_package_parents(source_files) -> dict[str, tuple[str, ...]]:
    """Map every package basename to the parent dirs where it appears.

    Used as a resolver fallback for Python layouts where the effective source
    root is injected at runtime (conftest.py sys.path shim, PYTHONPATH,
    setuptools ``package_dir``). The import specifier's first segment is the
    package name; its parent dir must be acting as a source root regardless
    of whether our structural ``_python_source_roots`` could deduce that.
    """
    cache_key = source_files if isinstance(source_files, frozenset) else frozenset(source_files)
    cached = _python_package_parents_cache.get(cache_key)
    if cached is not None:
        return cached

    parents: dict[str, set[str]] = {}
    for f in source_files:
        if f.endswith("/__init__.py"):
            pkg_dir = f[: -len("/__init__.py")]
            basename = posixpath.basename(pkg_dir)
            parent = posixpath.dirname(pkg_dir)
            parents.setdefault(basename, set()).add(parent)

    result = {name: tuple(sorted(dirs)) for name, dirs in parents.items()}
    _python_package_parents_cache[cache_key] = result
    return result


def _clear_python_roots_cache() -> None:
    """Test helper: drop the Python source roots cache between tests."""
    _python_roots_cache.clear()
    _python_package_parents_cache.clear()


# ---------------------------------------------------------------------------
# Path alias resolution (tsconfig.json / jsconfig.json compilerOptions.paths)
# ---------------------------------------------------------------------------

# Module-level cache: source_root -> alias_map (no mtime invalidation — tsconfig rarely
# changes during a session; a re-index is needed anyway if paths change).
_alias_map_cache: dict[str, dict[str, list[str]]] = {}
_ALIAS_MAP_LOCK = threading.Lock()

# Directories to skip when walking for tsconfig files.
_TSCONFIG_SKIP_DIRS = frozenset({
    "node_modules", ".git", "dist", "build", "out", ".cache",
    ".next", ".nuxt", ".svelte-kit", ".turbo", ".vercel",
})


def _norm_alias_replacement(rep: str, tsconfig_dir_rel: str = "") -> str:
    """Normalize one tsconfig paths replacement to a repo-root-relative prefix.

    The returned string has any wildcard suffix (``/*`` or ``*``) preserved so
    the caller can distinguish directory-prefix patterns from exact replacements.
    """
    is_wildcard = rep.endswith("/*") or rep == "*"
    if rep.endswith("/*"):
        base = rep[:-2]  # strip /*
    elif rep == "*":
        base = ""
    else:
        base = rep  # exact replacement — no wildcard

    if tsconfig_dir_rel:
        # Replacement is relative to tsconfig_dir_rel (e.g. ".svelte-kit").
        # posixpath.normpath resolves ".." segments.
        combined = posixpath.normpath(posixpath.join(tsconfig_dir_rel, base)) if base else tsconfig_dir_rel
        if combined == ".":
            combined = ""
        return (combined + "/*") if is_wildcard else combined
    else:
        # Root tsconfig: strip leading "./"
        if base.startswith("./"):
            base = base[2:]
        if base == ".":
            base = ""
        return (base + "/*") if is_wildcard else base


def _load_tsconfig_aliases(source_root: str) -> dict[str, list[str]]:
    """Read tsconfig.json / jsconfig.json path aliases for a project root.

    Returns a dict mapping tsconfig pattern strings (e.g. ``"@/*"``) to lists
    of normalized replacement strings (e.g. ``["src/*"]``).  All replacements
    are repo-root-relative.  Results are module-level cached by source_root.
    """
    if not source_root:
        return {}
    with _ALIAS_MAP_LOCK:
        if source_root in _alias_map_cache:
            return _alias_map_cache[source_root]

    # Miss: load tsconfig files without holding the lock (filesystem I/O)
    alias_map: dict[str, list[str]] = {}
    root = Path(source_root)

    def _ingest(paths: dict, tsconfig_dir_rel: str = "") -> None:
        for pattern, reps in paths.items():
            if pattern in alias_map:
                continue  # earlier config wins
            normalized = [_norm_alias_replacement(r, tsconfig_dir_rel) for r in (reps or []) if r]
            if normalized:
                alias_map[pattern] = normalized

    def _load_json(path: Path) -> dict:
        """Read a tsconfig/jsconfig file as plain JSON or JSONC (comments + trailing commas)."""
        try:
            from ..config import _strip_jsonc
            return json.loads(_strip_jsonc(path.read_text("utf-8", errors="replace")))
        except Exception:
            return {}

    # Root tsconfig.json / jsconfig.json (tsconfig.json takes priority)
    for cfg_name in ("tsconfig.json", "jsconfig.json"):
        cfg_path = root / cfg_name
        if cfg_path.is_file():
            data = _load_json(cfg_path)
            _ingest(data.get("compilerOptions", {}).get("paths", {}))
            break

    # SvelteKit: .svelte-kit/tsconfig.json (auto-generated; paths are relative to .svelte-kit/)
    svelte_cfg = root / ".svelte-kit" / "tsconfig.json"
    if svelte_cfg.is_file():
        data = _load_json(svelte_cfg)
        _ingest(data.get("compilerOptions", {}).get("paths", {}), tsconfig_dir_rel=".svelte-kit")

    # Generic discovery: walk all tsconfig*.json / jsconfig*.json files in the
    # repo tree (depth ≤ 4, skipping build/dependency dirs), following each
    # file's `extends` chain.  This covers any workspace layout — apps/, libs/,
    # services/, Nx/Turborepo — and repos that centralise aliases in a shared
    # tsconfig.base.json or tsconfig.paths.json at any level.
    seen_cfg: set[Path] = {
        root / "tsconfig.json",
        root / "jsconfig.json",
        root / ".svelte-kit" / "tsconfig.json",
    }

    def _ingest_tsconfig_file(cfg_path: Path) -> None:
        if cfg_path in seen_cfg:
            return
        seen_cfg.add(cfg_path)
        if not cfg_path.is_file():
            return
        data = _load_json(cfg_path)
        try:
            cfg_dir_rel = cfg_path.parent.relative_to(root).as_posix()
            if cfg_dir_rel == ".":
                cfg_dir_rel = ""
        except ValueError:
            return  # outside repo root
        paths = data.get("compilerOptions", {}).get("paths", {})
        if paths:
            _ingest(paths, tsconfig_dir_rel=cfg_dir_rel)
        # Follow extends chain — handles tsconfig.base.json / tsconfig.paths.json pattern.
        # TypeScript 5+ allows extends to be an array; normalise to list.
        extends_val = data.get("extends")
        if not extends_val:
            return
        if isinstance(extends_val, str):
            extends_val = [extends_val]
        for ref in extends_val:
            if not isinstance(ref, str):
                continue
            ref_path = ref if ref.endswith(".json") else ref + ".json"
            extended = (cfg_path.parent / ref_path).resolve()
            try:
                extended.relative_to(root)  # must stay inside the repo
            except ValueError:
                continue  # skip package references like "@tsconfig/recommended"
            _ingest_tsconfig_file(extended)

    def _walk_tsconfigs(directory: Path, depth: int) -> None:
        # Depth 5 covers layouts up to apps/x/frontend/packages/bar/tsconfig.json.
        if depth > 5:
            return
        try:
            for entry in sorted(directory.iterdir()):
                if entry.is_dir():
                    if entry.name not in _TSCONFIG_SKIP_DIRS and not entry.name.startswith("."):
                        _walk_tsconfigs(entry, depth + 1)
                elif (
                    entry.is_file()
                    and entry.suffix == ".json"
                    and (entry.name.startswith("tsconfig") or entry.name.startswith("jsconfig"))
                ):
                    _ingest_tsconfig_file(entry)
        except PermissionError:
            pass

    _walk_tsconfigs(root, 0)

    with _ALIAS_MAP_LOCK:
        _alias_map_cache[source_root] = alias_map
    return alias_map


def _expand_aliases(specifier: str, alias_map: dict[str, list[str]]) -> list[str]:
    """Return candidate repo-root-relative paths by applying tsconfig path aliases.

    Each replacement in *alias_map* is already normalized (no leading ``./``) by
    :func:`_load_tsconfig_aliases`.
    """
    results: list[str] = []
    for pattern, replacements in alias_map.items():
        if pattern.endswith("/*"):
            prefix = pattern[:-1]  # e.g. "@/"
            if not specifier.startswith(prefix):
                continue
            rest = specifier[len(prefix):]  # e.g. "lib/utils"
            for rep in replacements:
                if rep.endswith("/*"):
                    rep_dir = rep[:-2]  # e.g. "src/lib" or "" (repo root)
                    results.append((rep_dir + "/" + rest) if rep_dir else rest)
                # Non-wildcard replacement for wildcard pattern: unusual, skip
        elif pattern == specifier:
            for rep in replacements:
                results.append(rep[2:] if rep.startswith("./") else rep)
    return results


def resolve_specifier(
    specifier: str,
    importer_path: str,
    source_files: set[str],
    alias_map: Optional[dict[str, list[str]]] = None,
    psr4_map: Optional[dict[str, str]] = None,
) -> Optional[str]:
    """Attempt to resolve an import specifier to a concrete file in the index.

    Resolves relative imports (starting with '.') and tries common extension
    permutations.  For TypeScript/JS projects with path aliases (e.g. ``@/*``
    or ``$lib/*``), pass the project's ``alias_map`` (from
    :func:`_load_tsconfig_aliases`) to enable alias expansion.  For PHP
    projects using Composer, pass ``psr4_map`` (from :func:`build_psr4_map`)
    to resolve ``use App\\Models\\User`` → ``app/Models/User.php``.

    Args:
        specifier: Raw import specifier (e.g. '../intake/IntakeService' or '@/lib/utils').
        importer_path: POSIX path of the importing file (e.g. 'src/a/b.js').
        source_files: Set of all file paths present in the index.
        alias_map: Optional tsconfig path alias map for this project.
        psr4_map: Optional PSR-4 namespace map from composer.json.

    Returns:
        The matching source file path, or None if unresolvable.
    """
    # Relative import
    if specifier.startswith("."):
        importer_dir = posixpath.dirname(importer_path)
        joined = posixpath.normpath(posixpath.join(importer_dir, specifier))
        for c in _candidates(joined):
            if c in source_files:
                return c
        return None

    # PHP PSR-4 namespace resolution (specifiers containing backslashes)
    if psr4_map and "\\" in specifier:
        resolved = resolve_php_namespace(specifier, psr4_map, source_files)
        if resolved:
            return resolved

    # Absolute: try direct match first (e.g., for Go or absolute paths)
    for c in _candidates(specifier):
        if c in source_files:
            return c

    # Python module-style absolute import: 'app.notifications.mentions' →
    # 'app/notifications/mentions.py'. Also try prefixing with detected
    # Python source roots so layouts like backend/app/... or src/app/...
    # resolve correctly. Triggered when the specifier looks like a Python
    # module path: contains dots, no slashes, no backslashes, no leading dot.
    if (
        "." in specifier
        and "/" not in specifier
        and "\\" not in specifier
        and not specifier.startswith(".")
    ):
        module_path = specifier.replace(".", "/")
        # Try direct (repo-root layout)
        for c in _candidates(module_path):
            if c in source_files:
                return c
        # Try with each detected Python source root as a prefix
        for root in _python_source_roots(source_files):
            prefixed = f"{root}/{module_path}" if root else module_path
            for c in _candidates(prefixed):
                if c in source_files:
                    return c
        # Fallback for runtime-injected source roots (conftest.py sys.path
        # shims, PYTHONPATH, setuptools package_dir): the specifier's first
        # segment names a package that must sit directly under an effective
        # source root. If that package appears anywhere in the tree, its
        # parent dir is acting as a source root — even when the structural
        # detector above can't see that because the parent is itself a
        # package. Scoped by first-segment match, so no broad suffix sweep.
        first_segment = specifier.split(".", 1)[0]
        pkg_parents = _python_package_parents(source_files).get(first_segment)
        if pkg_parents:
            seen_roots = set(_python_source_roots(source_files))
            for parent in pkg_parents:
                if parent in seen_roots:
                    continue  # already tried above
                prefixed = f"{parent}/{module_path}" if parent else module_path
                for c in _candidates(prefixed):
                    if c in source_files:
                        return c

    # Alias expansion (tsconfig compilerOptions.paths: @/*, $lib/*, etc.)
    if alias_map:
        for expanded in _expand_aliases(specifier, alias_map):
            for c in _candidates(expanded):
                if c in source_files:
                    return c

    # Stem matching fallback: bare names like dbt ref('dim_client')
    # resolve to any .sql file whose stem matches.  Uses a cached stem
    # dict to avoid O(n) scans on repeated calls with the same source_files.
    if "/" not in specifier and "." not in specifier and "\\" not in specifier:
        return _get_sql_stems(source_files).get(specifier.lower())

    return None
