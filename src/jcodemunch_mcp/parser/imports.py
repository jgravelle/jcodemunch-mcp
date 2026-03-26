"""Extract import statements from source files using language-specific regex patterns."""

import json
import posixpath
import re
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
_PY_FROM = re.compile(
    r"""^from\s+(\.{0,4}[\w.]*)\s+import\s+(.+)$""", re.MULTILINE
)
_PY_IMPORT = re.compile(r"""^import\s+([\w.,][^\n]*)$""", re.MULTILINE)

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

_LANGUAGE_EXTRACTORS = {
    "javascript": _extract_js_imports,
    "typescript": _extract_js_imports,
    "tsx": _extract_js_imports,
    "jsx": _extract_js_imports,
    "vue": _extract_js_imports,
    "python": _extract_python_imports,
    "go": _extract_go_imports,
    "java": lambda c: _extract_java_imports(c, "java"),
    "kotlin": lambda c: _extract_java_imports(c, "kotlin"),
    "rust": _extract_rust_imports,
    "c": _extract_c_imports,
    "cpp": _extract_c_imports,
    "objc": _extract_c_imports,
    "ruby": _extract_ruby_imports,
    "csharp": _extract_csharp_imports,
    "php": _extract_php_imports,
    "swift": _extract_swift_imports,
    "scala": _extract_scala_imports,
    "haskell": _extract_haskell_imports,
    "sql": _extract_sql_dbt_imports,
    "asm": _extract_asm_imports,
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

# Cache for SQL stem lookups — avoids O(n) scans when resolve_specifier is
# called repeatedly with the same source_files set (common in tight loops).
_sql_stem_cache: tuple[int, dict[str, str]] = (0, {})


def _get_sql_stems(source_files: set[str]) -> dict[str, str]:
    """Return a lowered-stem -> file_path dict for .sql files, cached by set identity."""
    global _sql_stem_cache
    sf_id = id(source_files)
    if _sql_stem_cache[0] == sf_id:
        return _sql_stem_cache[1]
    stems: dict[str, str] = {}
    for sf in source_files:
        if sf.endswith(".sql"):
            stem = posixpath.splitext(posixpath.basename(sf))[0].lower()
            if stem not in stems:  # first match wins
                stems[stem] = sf
    _sql_stem_cache = (sf_id, stems)
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


# ---------------------------------------------------------------------------
# Path alias resolution (tsconfig.json / jsconfig.json compilerOptions.paths)
# ---------------------------------------------------------------------------

# Module-level cache: source_root -> alias_map (no mtime invalidation — tsconfig rarely
# changes during a session; a re-index is needed anyway if paths change).
_alias_map_cache: dict[str, dict[str, list[str]]] = {}


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
    if source_root in _alias_map_cache:
        return _alias_map_cache[source_root]

    alias_map: dict[str, list[str]] = {}
    root = Path(source_root)

    def _ingest(paths: dict, tsconfig_dir_rel: str = "") -> None:
        for pattern, reps in paths.items():
            if pattern in alias_map:
                continue  # earlier config wins
            normalized = [_norm_alias_replacement(r, tsconfig_dir_rel) for r in (reps or []) if r]
            if normalized:
                alias_map[pattern] = normalized

    # Root tsconfig.json / jsconfig.json (tsconfig.json takes priority)
    for cfg_name in ("tsconfig.json", "jsconfig.json"):
        cfg_path = root / cfg_name
        if cfg_path.is_file():
            try:
                data = json.loads(cfg_path.read_text("utf-8", errors="replace"))
                _ingest(data.get("compilerOptions", {}).get("paths", {}))
            except Exception:
                pass
            break

    # SvelteKit: .svelte-kit/tsconfig.json (auto-generated; paths are relative to .svelte-kit/)
    svelte_cfg = root / ".svelte-kit" / "tsconfig.json"
    if svelte_cfg.is_file():
        try:
            data = json.loads(svelte_cfg.read_text("utf-8", errors="replace"))
            _ingest(data.get("compilerOptions", {}).get("paths", {}), tsconfig_dir_rel=".svelte-kit")
        except Exception:
            pass

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
) -> Optional[str]:
    """Attempt to resolve an import specifier to a concrete file in the index.

    Resolves relative imports (starting with '.') and tries common extension
    permutations.  For TypeScript/JS projects with path aliases (e.g. ``@/*``
    or ``$lib/*``), pass the project's ``alias_map`` (from
    :func:`_load_tsconfig_aliases`) to enable alias expansion.

    Args:
        specifier: Raw import specifier (e.g. '../intake/IntakeService' or '@/lib/utils').
        importer_path: POSIX path of the importing file (e.g. 'src/a/b.js').
        source_files: Set of all file paths present in the index.
        alias_map: Optional tsconfig path alias map for this project.

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

    # Absolute: try direct match first (e.g., for Go or absolute paths)
    for c in _candidates(specifier):
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
    if "/" not in specifier and "." not in specifier:
        return _get_sql_stems(source_files).get(specifier.lower())

    return None
