"""Extract import statements from source files using language-specific regex patterns."""

import json
import logging
import os
import posixpath
import re
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Optional

from .astro_shared import mask_html_comments_keep_offsets, split_astro_frontmatter
from .languages import template_underlying_language
from .template_shared import TEMPLATE_ENGINE_LANGUAGES, mask_template_keep_offsets


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
# JS/TS: export { A, B as C } from 'specifier'  (selective re-export)
# Captures the brace contents so the graph builder can do per-name barrel
# routing — `import { A } from './barrel'` credits the leaf `A` came from,
# not every leaf the barrel re-exports.
_JS_REEXPORT_NAMED = re.compile(
    r"""(?:^|\n)\s*export\s+\{([^}]*)\}\s+from\s+['"]([^'"]+)['"]""",
    re.MULTILINE,
)
# JS/TS: export * from 'specifier'  or  export * as ns from 'specifier'  (wildcard re-export = barrel)
# Wildcard means "anyone importing this barrel could be using any exported
# symbol", so the graph builder transitively credits every re-exported leaf.
_JS_REEXPORT_STAR = re.compile(
    r"""(?:^|\n)\s*export\s*\*\s*(?:as\s+\w+\s+)?from\s+['"]([^'"]+)['"]""",
    re.MULTILINE,
)


def _parse_reexport_clause(raw: str) -> list[dict]:
    """Parse the brace contents of `export { ... } from <spec>`.

    Returns a list of {exposed, original} dicts. Handles:
        Foo              -> {exposed: Foo, original: Foo}
        Foo as Bar       -> {exposed: Bar, original: Foo}
        default as Qux   -> {exposed: Qux, original: default}
        type Foo         -> {exposed: Foo, original: Foo}  (TS type-only)
    """
    origins: list[dict] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        # Strip TS `type ` prefix.
        part = re.sub(r"^type\s+", "", part).strip()
        if not part:
            continue
        m = re.match(r"^(\S+)\s+as\s+(\S+)$", part)
        if m:
            origins.append({"original": m.group(1), "exposed": m.group(2)})
        else:
            # Single token; may be `default` (covers `export { default } from`).
            tok = part.split()[0]
            origins.append({"original": tok, "exposed": tok})
    return origins
# JS/TS: import('specifier') — dynamic import (Vue Router lazy routes, code splitting)
_JS_DYNAMIC_IMPORT = re.compile(r"""import\s*\(\s*['"]([^'"]+)['"]\s*\)""", re.MULTILINE)

# Python: from .module import A, B  /  import os
# Allow optional leading whitespace so function-local imports inside def/class
# bodies are also captured (common pattern for breaking circular imports).
logger = logging.getLogger(__name__)

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

# Gleam: import gleam/option.{type Option, None, Some} as opt
# Module paths are lowercase snake_case segments joined by '/'. The optional
# `.{...}` clause lists unqualified imports (values, constructors, and
# `type X` entries); the optional trailing `as` alias renames the module but
# does not change the specifier. gleam format may wrap long `.{...}` lists
# across lines, so the brace body must span newlines ([^}] does).
_GLEAM_IMPORT = re.compile(
    r"""^\s*import\s+([a-z][a-z0-9_]*(?:/[a-z][a-z0-9_]*)*)(?:\.\{([^}]*)\})?""",
    re.MULTILINE,
)


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
    edges: list[dict] = []
    seen: set[str] = set()

    def add(
        specifier: str,
        names: list[str],
        *,
        is_re_export: bool = False,
        re_export_kind: Optional[str] = None,
        re_export_origins: Optional[list[dict]] = None,
    ) -> None:
        if specifier not in seen:
            seen.add(specifier)
            edge: dict = {"specifier": specifier, "names": names}
            if is_re_export:
                edge["is_re_export"] = True
                if re_export_kind:
                    edge["re_export_kind"] = re_export_kind
                if re_export_origins:
                    edge["re_export_origins"] = list(re_export_origins)
            edges.append(edge)
            return
        # Merge into existing entry. Promote to re-export if either source
        # flagged it. For mixed-kind merges (selective + wildcard against the
        # same specifier — `export { X } from './x'; export * from './x'`),
        # wildcard wins because it's the looser semantic.
        for e in edges:
            if e["specifier"] != specifier:
                continue
            e["names"] = sorted(set(e["names"]) | set(names))
            if not is_re_export:
                return
            e["is_re_export"] = True
            existing_kind = e.get("re_export_kind")
            if re_export_kind == "wildcard" or existing_kind == "wildcard":
                e["re_export_kind"] = "wildcard"
                # Wildcard supersedes selective origins; drop them.
                e.pop("re_export_origins", None)
            elif re_export_kind == "selective":
                e["re_export_kind"] = "selective"
                if re_export_origins:
                    existing = e.get("re_export_origins", [])
                    seen_exposed = {o["exposed"] for o in existing}
                    for o in re_export_origins:
                        if o["exposed"] not in seen_exposed:
                            existing.append(o)
                            seen_exposed.add(o["exposed"])
                    e["re_export_origins"] = existing
            return

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

    for m in _JS_REEXPORT_NAMED.finditer(content):
        # Selective re-export `export { X, Y as Z } from <spec>`.
        # Tagged with re_export_kind="selective" + re_export_origins so the
        # graph builder routes per-name: importers of `X` from the barrel
        # credit the leaf, importers of unrelated names do not.
        raw_names, specifier = m.group(1), m.group(2)
        origins = _parse_reexport_clause(raw_names)
        if not origins:
            continue
        exposed_names = [o["exposed"] for o in origins]
        add(
            specifier,
            exposed_names,
            is_re_export=True,
            re_export_kind="selective",
            re_export_origins=origins,
        )

    for m in _JS_REEXPORT_STAR.finditer(content):
        # Wildcard re-export `export * from <spec>` — barrel pattern.
        # Anyone importing the barrel could be using any re-exported symbol,
        # so the graph builder transitively credits every leaf.
        add(m.group(1), [], is_re_export=True, re_export_kind="wildcard")

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

        # ⚠⚠ `from . import receipts` is a dependency on the SIBLING MODULE
        # `receipts`, not on the package's `__init__.py` (#550, @rknighton).
        # The specifier is a bare `.`, which names the package, so the resolver
        # -- which only ever sees the specifier -- had no way to reach the
        # sibling and every such edge pointed at `__init__.py`. This repo uses
        # the form 49 times across 16 files, and it alone reported 20 live files
        # as dead.
        #
        # ⚠ Emitted ALONGSIDE the bare specifier, never instead of it, and that
        # is what makes this safe without touching the 26 `resolve_specifier`
        # call sites. `from . import x` is `x` the submodule OR `x` an
        # attribute of `__init__.py`, and which one cannot be known from the
        # importing file. So both edges are offered: `.x` resolves when the
        # submodule exists, resolves to None (harmless, skipped by every
        # consumer) when it does not, and the `__init__.py` edge that already
        # worked is left exactly as it was.
        #
        # ⚠ The per-name loop runs even when the bare specifier was already
        # seen. `from . import a` followed by `from . import b` in one file
        # otherwise loses `b` entirely -- the dedup keys on the specifier, and
        # every bare-dot import in a file shares the same one.
        #
        # ⚠⚠ The guard was `set(specifier) == {"."}`, i.e. BARE dots only, so
        # #550 was fixed for `from . import x` and left standing for
        # `from ..retrieval import embed_drift` -- the same dependency on a
        # sibling module, written with the package named. Every reason above
        # applies to it word for word; only the spelling differs. Measured over
        # this repo's `src/`: 21 SYNTHESISED edges that resolve, from 18
        # importer files, reaching 12 modules -- `evidence/producers.py`,
        # `evidence/receipts.py`, `retrieval/embed_drift.py`,
        # `storage/embedding_matrix.py`, `storage/token_tracker.py` and seven
        # more, every one of which `find_importers` reported as having zero
        # importers while its importer sat at module scope in an indexed, live
        # file (#566).
        #
        # ⚠⚠ The first count written here was 134, and it was wrong the way a
        # measurement is usually wrong: it counted every edge matching the
        # SHAPE rather than the ones this change creates. `from .tools.index_repo
        # import index_repo` -- the convention where a module and its chief
        # export share a name -- is hand-written, already resolved, and looks
        # identical to a synthesised edge unless the raw source is consulted.
        # 113 of the 134 were that. **Compare against the import statements
        # actually written in the file, never against the specifier's spelling.**
        #
        # ⚠ A bare-dot specifier already ends in the separator (`.` + `x` is
        # `.x`); a named one needs it (`..retrieval` + `embed_drift` is
        # `..retrieval.embed_drift`). Absolute specifiers take the same path --
        # `from pkg.storage import matrix` yields `pkg.storage.matrix`, which
        # the module-path branch of `resolve_specifier` resolves.
        if names:
            _prefix = specifier if set(specifier) == {"."} else f"{specifier}."
            for _name in names:
                _sub = f"{_prefix}{_name}"
                if _sub not in seen:
                    seen.add(_sub)
                    edges.append({"specifier": _sub, "names": [_name]})

    for m in _PY_IMPORT.finditer(content):
        for mod in m.group(1).split(","):
            # ⚠⚠ `[0]` on an empty split raised IndexError, and `extract_imports`
            # swallows it and returns [], so ONE bad line cost the file EVERY
            # import edge it had. Found 2026-08-26 on this repo's own
            # `watcher.py`, whose docstring wraps to a line reading
            # "import keeps the core watcher free of a hard dependency ...," --
            # `_PY_IMPORT` matches any line starting `import `, prose included,
            # and a trailing comma leaves an empty final part.
            #
            # ⚠ A bogus specifier lifted out of prose is harmless: it resolves
            # to None and every consumer skips it. The CRASH was the defect,
            # and it was invisible because the file simply had no edges.
            parts = mod.strip().split()
            if not parts:
                continue
            mod = parts[0]  # handle 'import os as operating_system'
            if mod and mod not in seen:
                seen.add(mod)
                edges.append({"specifier": mod, "names": []})

    if "__import__" in content or "import_module" in content:
        edges.extend(_python_dynamic_imports(content, seen))

    return edges


# (#876) The marker edge for a dynamic import whose target is not a literal.
# Its specifier resolves to nothing, so every consumer skips it the way it
# skips any unresolvable specifier; `get_blast_radius.blast_verdict` reads it:
# a `package`/`prefix:` scope that reaches the file refuses an empty walk,
# naming the site; an `opaque` one is disclosed beside it (jjg, 2026-09-29).
DYNAMIC_IMPORT_UNRESOLVED = "<dynamic-import>"


def _is_dynamic_import_call(node) -> bool:
    import ast

    func = node.func
    if isinstance(func, ast.Name):
        return func.id in ("__import__", "import_module")
    if isinstance(func, ast.Attribute):
        return func.attr == "import_module" or func.attr == "__import__"
    return False


def _python_dynamic_imports(content: str, seen: set) -> list[dict]:
    """Dynamic imports as edges (#876), bounded: no symbolic execution.

    A target becomes edges when it is knowable:
    - a string literal;
    - a module-level literal table, indexed along its subscript path
      (``GRAMMARS[name][1]`` is each value's element 1, never a dict key);
    - a loop variable over a literal tuple/list (``for m in ("a", "b")``),
      when EVERY binding of that name in the file is such a loop;
    - a literal passed ONE step into a parameter of a function in this file
      that feeds one, by position or keyword -- only while the parameter is
      never rebound in that function, and only when EVERY use of the function
      in this file is a direct call (a function passed to ``map``, registered,
      or called on another object can receive anything).
    Otherwise the call site is recorded once per scope as a marker edge,
    ``dynamic_scope`` saying what it can reach: ``package`` (built from this
    package's own name, the #569 shape, or a leading-dot name anchored on it
    with ``package=``), ``prefix:<dotted>`` (a literal module prefix, or
    ``X.__name__`` of an imported module ``X``), or ``opaque``.
    ``blast_verdict`` refuses an empty walk only for a scope that reaches the
    file; an opaque site is disclosed, not refused (jjg, 2026-09-29: a
    registry-driven loader exists in most repos, and a refusal that fires on
    every empty result teaches people to ignore it).
    """
    import ast

    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        # Unparseable (py2, a template): no edges and no marker. One such file
        # must not become a boundary for the whole repository (review of #876).
        logger.debug("dynamic-import scan skipped: file does not parse", exc_info=True)
        return []

    out: list[dict] = []
    scopes: set[str] = set()

    def add(spec: str) -> None:
        if spec and spec not in seen:
            seen.add(spec)
            out.append({"specifier": spec, "names": [], "dynamic": True})

    def literal(node) -> Optional[str]:
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    def stores(root, name: str) -> int:
        """Bindings of ``name`` under ``root``: assignment targets and parameters."""
        n = 0
        for node in ast.walk(root):
            if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)):
                n += 1
            elif isinstance(node, ast.arg) and node.arg == name:
                n += 1
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                n += sum(1 for a in node.names if (a.asname or a.name.split(".")[0]) == name)
        return n

    def _frozen(node) -> bool:
        return isinstance(node, ast.Constant) or (
            isinstance(node, ast.Tuple) and all(_frozen(e) for e in node.elts)
        )

    def _frozen_below(node) -> bool:
        """Everything under the top level is a constant or a tuple of them.

        Only the table's own name is guarded against change (`_only_read`); a
        mutable value reached through it (`T["a"].append(cfg)`,
        `for v in T.values(): v.append(cfg)`) is a change no name-level rule
        sees, so such a table is not a literal at all (review of #876).
        """
        elts = node.values if isinstance(node, ast.Dict) else node.elts
        keys_ok = all(k is None or _frozen(k) for k in node.keys) if isinstance(node, ast.Dict) else True
        return keys_ok and all(_frozen(e) for e in elts)

    # Module-level literal tables.
    tables: dict[str, ast.AST] = {}
    for stmt in tree.body:
        targets = stmt.targets if isinstance(stmt, ast.Assign) else (
            [stmt.target] if isinstance(stmt, ast.AnnAssign) and stmt.value is not None else []
        )
        value = getattr(stmt, "value", None)
        if isinstance(value, (ast.Dict, ast.List, ast.Tuple, ast.Set)) and _frozen_below(value):
            for t in targets:
                if isinstance(t, ast.Name):
                    tables[t.id] = value
    # A table is the literal it was only while nothing can change what it
    # holds: a rebinding, `T[k] = cfg`, `del T[k]`, a mutating method
    # (`T.update(...)`), an alias (`U = T`) or handing `T` to a function we
    # cannot see all make it a site (review of #876). Reads stay reads:
    # `T[k]`, `k in T`, `for k in T`, `sorted(T)`, `T.get(k)`.
    read_methods = {"get", "keys", "values", "items", "copy", "index", "count"}
    read_builtins = {
        "sorted", "len", "list", "tuple", "set", "frozenset", "dict", "iter",
        "enumerate", "reversed", "any", "all", "min", "max", "str", "repr", "print",
    }

    def _use_is_a_read(parent, child) -> bool:
        if isinstance(parent, ast.Subscript):
            return parent.value is child and isinstance(parent.ctx, ast.Load)
        if isinstance(parent, ast.Attribute):
            return parent.attr in read_methods
        if isinstance(parent, ast.Call):
            return (
                child in parent.args and isinstance(parent.func, ast.Name)
                and parent.func.id in read_builtins
            )
        if isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)):
            return parent.iter is child
        return isinstance(parent, (ast.Compare, ast.FormattedValue, ast.BoolOp, ast.UnaryOp, ast.If, ast.While))

    def _only_read(name: str) -> bool:
        if stores(tree, name) != 1:
            return False
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                if (
                    isinstance(child, ast.Name) and child.id == name
                    and isinstance(child.ctx, ast.Load) and not _use_is_a_read(parent, child)
                ):
                    return False
        return True

    tables = {k: v for k, v in tables.items() if _only_read(k)}

    def _select(nodes: list, key) -> Optional[list]:
        """Apply one subscript to candidate container nodes; None when unknowable."""
        k = key.value if isinstance(key, ast.Constant) else None
        out_nodes: list = []
        for n in nodes:
            if isinstance(n, ast.Dict):
                if k is None:
                    out_nodes.extend(n.values)
                else:
                    hits = [v for kk, v in zip(n.keys, n.values) if isinstance(kk, ast.Constant) and kk.value == k]
                    out_nodes.extend(hits)
            elif isinstance(n, (ast.List, ast.Tuple)):
                if isinstance(k, int) and -len(n.elts) <= k < len(n.elts):
                    out_nodes.append(n.elts[k])
                elif k is None:
                    out_nodes.extend(n.elts)
                else:
                    return None
            else:
                return None
        return out_nodes

    def table_candidates(node) -> Optional[list[str]]:
        keys = []
        while isinstance(node, ast.Subscript):
            keys.append(node.slice)
            node = node.value
        if not keys or not isinstance(node, ast.Name) or node.id not in tables:
            return None
        nodes: Optional[list] = [tables[node.id]]
        for key in reversed(keys):
            nodes = _select(nodes, key) if nodes is not None else None
        if not nodes:
            return None
        strings = [literal(n) for n in nodes]
        return [s for s in strings if s] if all(strings) else None

    # Loop variables over literal sequences: `for m in ("a", "b")`. A name
    # also bound any other way (`m = cfg`) is not bounded by its loops.
    loop_values: dict[str, list[str]] = {}
    loop_bindings: dict[str, int] = {}
    for node in ast.walk(tree):
        gens = []
        if isinstance(node, (ast.For, ast.AsyncFor)):
            gens = [(node.target, node.iter)]
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            gens = [(g.target, g.iter) for g in node.generators]
        for target, it in gens:
            if isinstance(target, ast.Name) and isinstance(it, (ast.List, ast.Tuple, ast.Set)):
                vals = [literal(e) for e in it.elts]
                if vals and all(vals):
                    loop_values.setdefault(target.id, []).extend(v for v in vals if v)
                    loop_bindings[target.id] = loop_bindings.get(target.id, 0) + 1
    loop_values = {k: v for k, v in loop_values.items() if stores(tree, k) == loop_bindings[k]}
    # A comprehension's target is local to it, so an import call in its body
    # reading that target is bounded exactly, however else the name is used
    # outside it -- unless the comprehension itself binds the name again.
    comp_values: dict[int, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            continue
        for g in node.generators:
            if not (isinstance(g.target, ast.Name) and isinstance(g.iter, (ast.List, ast.Tuple, ast.Set))):
                continue
            vals = [literal(e) for e in g.iter.elts]
            if not (vals and all(vals)):
                continue
            if stores(node, g.target.id) != 1:
                continue  # shadowed by an inner comprehension or a later generator
            body = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
            for part in [*body, *g.ifs]:
                for call in ast.walk(part):
                    if (
                        isinstance(call, ast.Call) and _is_dynamic_import_call(call) and call.args
                        and isinstance(call.args[0], ast.Name) and call.args[0].id == g.target.id
                    ):
                        comp_values[id(call)] = [v for v in vals if v]

    # Names bound to a package or module name.
    own_pkg = {"__name__", "__package__"}
    module_alias: dict[str, str] = {}  # alias -> absolute dotted module
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    module_alias[a.asname] = a.name
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name in ("__name__", "__package__") and a.asname and node.level > 0 and not node.module:
                    own_pkg.add(a.asname)
                elif node.level == 0 and node.module:
                    module_alias[a.asname or a.name] = f"{node.module}.{a.name}"
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id in ("__name__", "__package__"):
            own_pkg |= {t.id for t in node.targets if isinstance(t, ast.Name)}

    def _leading(node):
        if isinstance(node, ast.Constant):
            return node
        if isinstance(node, ast.JoinedStr) and node.values:
            return node.values[0]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                node = node.left
            return node
        return None

    def _anchor(call) -> Optional[ast.AST]:
        """The ``package=`` argument of ``import_module(name, package)``."""
        if call is None:
            return None
        if len(call.args) > 1:
            return call.args[1]
        return next((k.value for k in call.keywords if k.arg == "package"), None)

    def scope_of(node, call=None) -> str:
        """What an unresolved target can reach."""
        head = _leading(node)
        if head is None:
            return "opaque"
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            if head.value.startswith("."):
                # `import_module(f".{m}", __package__)`: this package's own
                # modules, the relative spelling of the #569 shape. Only one
                # dot: `..` climbs out of the package.
                anchor = _anchor(call)
                if (
                    not head.value.startswith("..")
                    and isinstance(anchor, ast.Name) and anchor.id in own_pkg
                ):
                    return "package"
                return "opaque"
            prefix = head.value.rstrip(".")
            return f"prefix:{prefix}" if prefix else "opaque"
        inner = head.value if isinstance(head, ast.FormattedValue) else head
        if isinstance(inner, ast.Name) and inner.id in own_pkg:
            return "package"
        if (
            isinstance(inner, ast.Attribute) and inner.attr == "__name__"
            and isinstance(inner.value, ast.Name) and inner.value.id in module_alias
        ):
            return f"prefix:{module_alias[inner.value.id]}"
        return "opaque"

    def resolve_target(node) -> bool:
        target = literal(node)
        if target is not None:
            add(target)
            return True
        candidates = table_candidates(node)
        if candidates is not None:
            for c in candidates:
                add(c)
            return True
        if isinstance(node, ast.Name) and node.id in loop_values:
            for c in loop_values[node.id]:
                add(c)
            return True
        return False

    # Functions whose parameter feeds a dynamic import, unrebound. A name
    # defined twice is not propagated through: its call sites stay sites.
    feeders: dict[str, tuple[int, str, bool]] = {}  # name -> (arg position, param, public)
    feeder_node: dict[str, int] = {}
    feeder_calls: dict[str, set[int]] = {}
    duplicate: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = [a.arg for a in (*fn.args.posonlyargs, *fn.args.args)]
        if params and params[0] in ("self", "cls"):
            params = params[1:]  # a bound call passes them implicitly (review of #876)
        for call in ast.walk(fn):
            if isinstance(call, ast.Call) and _is_dynamic_import_call(call) and call.args:
                arg = call.args[0]
                if not (isinstance(arg, ast.Name) and arg.id in params):
                    continue
                if stores(fn, arg.id) != 1:
                    continue  # rebound: `name = "plugins." + name` is not the caller's literal
                if fn.name in feeder_node and feeder_node[fn.name] != id(fn):
                    duplicate.add(fn.name)
                if fn.name in feeders and feeders[fn.name][1] != arg.id:
                    duplicate.add(fn.name)
                feeder_node[fn.name] = id(fn)
                feeders[fn.name] = (params.index(arg.id), arg.id, not fn.name.startswith("_"))
                feeder_calls.setdefault(fn.name, set()).add(id(call))
    for name in duplicate:
        feeders.pop(name, None)

    def _direct_callee(call) -> Optional[str]:
        func = call.func
        if isinstance(func, ast.Name):
            return func.id
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name) and func.value.id in ("self", "cls")
        ):
            return func.attr
        return None

    # A feeder is resolved by its callers only when every use of its name in
    # this file is a direct call; `map(_load, cfg)`, `registry.add(_load)` or
    # `obj._load(x)` can hand it anything (review of #876).
    direct_uses: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee = _direct_callee(node)
            if callee in feeders:
                direct_uses[callee] = direct_uses.get(callee, 0) + 1
    all_uses: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in feeders and isinstance(node.ctx, ast.Load):
            all_uses[node.id] = all_uses.get(node.id, 0) + 1
        elif isinstance(node, ast.Attribute) and node.attr in feeders:
            all_uses[node.attr] = all_uses.get(node.attr, 0) + 1
    for name in list(feeders):
        used = direct_uses.get(name, 0)
        if used == 0 or all_uses.get(name, 0) != used:
            feeders.pop(name)
    fed_calls: set[int] = set().union(*(feeder_calls[n] for n in feeders)) if feeders else set()

    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        if _is_dynamic_import_call(call):
            if id(call) in fed_calls:
                continue
            if id(call) in comp_values:
                for c in comp_values[id(call)]:
                    add(c)
                continue
            if not (call.args and resolve_target(call.args[0])):
                scopes.add(scope_of(call.args[0], call) if call.args else "opaque")
            continue
        callee = _direct_callee(call)
        if callee in feeders:
            pos, pname, _public = feeders[callee]
            value = call.args[pos] if pos < len(call.args) else next(
                (k.value for k in call.keywords if k.arg == pname), None
            )
            if value is None or not resolve_target(value):
                scopes.add(scope_of(value) if value is not None else "opaque")

    # A PUBLIC feeder can be called from another file with any argument: its
    # own in-file calls cannot bound it (review of #876). A `_private` one is
    # taken as module-private.
    if any(public for _pos, _p, public in feeders.values()):
        scopes.add("opaque")

    for scope in sorted(scopes):
        out.append({
            "specifier": DYNAMIC_IMPORT_UNRESOLVED, "names": [],
            "dynamic_unresolved": True, "dynamic_scope": scope,
        })
    return out


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


def _extract_gleam_imports(content: str) -> list[dict]:
    """Extract Gleam import statements.

    Handles:
        import gleam/io
        import gleam/option.{type Option, None, Some}
        import holmes_msg as msg
        import webse/config_types.{type Config, Config}

    ``names`` are the unqualified imports from the ``.{...}`` clause with the
    ``type `` prefix stripped and ``X as Y`` reduced to the original name
    (both handled by :func:`_clean_names`). Gleam forbids duplicate imports
    of the same module, so first-wins dedup is sufficient.
    """
    edges: list[dict] = []
    seen: set[str] = set()
    for m in _GLEAM_IMPORT.finditer(content):
        specifier, names_raw = m.group(1), m.group(2)
        if specifier in seen:
            continue
        seen.add(specifier)
        names = _clean_names(names_raw) if names_raw else []
        edges.append({"specifier": specifier, "names": names})
    return edges


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

# Svelte has no <template> wrapper, so component-usage scanning runs over the
# whole file — but the <script> block is TypeScript, where generics like
# `identity<T>()`, `Array<Item>`, `Writable<AppState>` would be misread as
# `<T>` / `<Item>` / `<AppState>` component tags.  Strip <script>/<style> block
# *contents* before the tag scan (their tag names are HTML-standard and filtered
# anyway; only the inner text produces false positives).
_SVELTE_SCRIPT_STYLE_BLOCK = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1\s*>",
    re.DOTALL | re.IGNORECASE,
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


def _extract_astro_template_components(content: str) -> list[str]:
    """Extract component tags from Astro template content."""
    template = mask_html_comments_keep_offsets(content)

    components: set[str] = set()
    for cm in _VUE_TEMPLATE_COMPONENT.finditer(template):
        tag = cm.group("tag")
        if tag.lower() in _HTML_STANDARD_ELEMENTS:
            continue
        components.add(_kebab_to_pascal(tag) if "-" in tag else tag)
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


def _extract_astro_imports(content: str) -> list[dict]:
    """Extract imports from Astro frontmatter + synthetic template usage edges."""
    frontmatter, template_body, _, _ = split_astro_frontmatter(content)
    edges = _extract_js_imports(frontmatter) if frontmatter is not None else []

    template_components = _extract_astro_template_components(template_body)
    if not template_components:
        return edges

    imported_names: set[str] = set()
    for edge in edges:
        imported_names.update(edge.get("names", []))

    for component in template_components:
        if component in imported_names:
            continue
        edges.append({"specifier": component, "names": [component]})

    deduped: list[dict] = []
    seen_keys: set[tuple[Optional[str], tuple[str, ...]]] = set()
    for edge in edges:
        key = (
            edge.get("specifier"),
            tuple(edge.get("names", [])),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(edge)
    return deduped


def _extract_svelte_imports(content: str) -> list[dict]:
    """Extract imports from a Svelte SFC: <script> ESM imports + template usage.

    Svelte has no frontmatter fence or <template> wrapper — imports live in the
    <script> block and components are used directly in markup. The ESM import scan
    runs over the whole file (the JS import regexes only match import statements).
    The component-tag scan runs over the markup with <script>/<style> block bodies
    stripped, so TypeScript generics (`identity<T>`, `Array<Item>`) aren't misread
    as component tags. Mirrors _extract_astro_imports (mask HTML comments,
    PascalCase component tags, dedupe).
    """
    edges = _extract_js_imports(content)

    markup = _SVELTE_SCRIPT_STYLE_BLOCK.sub("", content)
    template_components = _extract_astro_template_components(markup)
    if not template_components:
        return edges

    imported_names: set[str] = set()
    for edge in edges:
        imported_names.update(edge.get("names", []))

    for component in template_components:
        if component in imported_names:
            continue
        edges.append({"specifier": component, "names": [component]})

    deduped: list[dict] = []
    seen_keys: set[tuple[Optional[str], tuple[str, ...]]] = set()
    for edge in edges:
        key = (
            edge.get("specifier"),
            tuple(edge.get("names", [])),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(edge)
    return deduped


# ---------------------------------------------------------------------------
# Racket
# ---------------------------------------------------------------------------
# `(require ...)` nests arbitrarily -- (only-in ...), (prefix-in ...),
# (rename-in ...), (for-syntax ...) -- so a flat regex cannot read it. This is a
# minimal balanced reader over the require form only; it never parses the whole
# file, so it stays as cheap as the regex extractors around it.

#: `\b` after `require` also matched `(require-syntax ...)` (`-` is a word
#: boundary), which is a different form; the lookahead demands a delimiter.
#: Wrappers that carry the real module path deeper inside.
_RACKET_REQUIRE_UNWRAP = frozenset({
    "for-syntax", "for-template", "for-label", "for-meta", "combine-in",
})


def _racket_atom_specifier(node: str) -> Optional[str]:
    """A bare module path or a `"string"` path; None for anything else.

    `"."` and `".."` are the submod spellings for THIS module and its
    enclosing module, never a file.
    """
    if node.startswith('"'):
        node = node[1:-1]
    if node.startswith("#") or node in ("", ".", ".."):
        return None
    return node


def _racket_edges(node) -> list[tuple[str, list[str]]]:
    """Every (module path, imported names) pair a require sub-form carries.

    ⚠ Plural on purpose. The wrappers -- `for-syntax`, `for-template`,
    `for-label`, `for-meta`, `combine-in` -- take ANY number of module paths,
    and a reducer that returned one string kept the first and dropped the
    rest: `(for-syntax racket/base "private/helpers.rkt")` recorded
    `racket/base` and lost the local file, and `(for-meta 1 "m.rkt")` recorded
    the phase level `1` as a module path. 166 multi-path wrappers in the
    distribution's pkgs; a phase-1 helper's only importer is usually one of
    them, so it read as dead.

    Names are reduced to their SOURCE-side spelling: `(rename-in m [f g])`
    yields `f`, the name at the definition site, which is what makes the edge
    point at a real symbol -- the reduction :func:`_clean_names` applies to
    `import {a as b}` and Gleam's `X as Y` for every other language here.
    """
    if isinstance(node, str):
        spec = _racket_atom_specifier(node)
        return [(spec, [])] if spec else []
    if not node:
        return []
    head = node[0] if isinstance(node[0], str) else ""
    if head == "submod":
        # `(submod "." test)` / `(submod ".." x)` name a submodule of THIS
        # file; `(submod "other.rkt" sub)` names a submodule of ANOTHER file,
        # which is a dependency on that file.
        if len(node) >= 2 and isinstance(node[1], str):
            spec = _racket_atom_specifier(node[1])
            return [(spec, [])] if spec else []
        return []
    if head in ("file", "lib", "planet", "quote"):
        return _racket_edges(node[1]) if len(node) >= 2 else []
    if head in _RACKET_REQUIRE_UNWRAP:
        # `(for-meta 1 a b)`: the first argument is a phase level, not a path.
        rest = node[2:] if head == "for-meta" else node[1:]
        out: list[tuple[str, list[str]]] = []
        for sub in rest:
            out.extend(_racket_edges(sub))
        return out
    if head in ("only-in", "rename-in", "prefix-in", "except-in", "relative-in"):
        idx = 2 if head == "prefix-in" else 1
        if len(node) <= idx:
            return []
        inner = _racket_edges(node[idx])
        if not inner:
            return []
        spec, names = inner[0]
        if head == "only-in":
            names = list(names)
            for item in node[2:]:
                if isinstance(item, str):
                    names.append(item)
                elif item and isinstance(item[0], str):
                    names.append(item[0])
        elif head == "rename-in":
            names = list(names) + [
                p[0] for p in node[2:] if isinstance(p, list) and p and isinstance(p[0], str)
            ]
        return [(spec, names)] + inner[1:]
    return []


#: Node types under which a `(require ...)` is DATA, not a require of this
#: file: quoted and syntax-quoted forms (macro templates, `eval` payloads),
#: comments (`#;` above all), and a span the reader could not read.
_RACKET_NOT_CODE = frozenset({
    "quote", "quasiquote", "syntax", "quasisyntax",
    "comment", "block_comment", "sexp_comment", "ERROR",
})


def _racket_datum(node):
    """The nested-list-of-strings view `_racket_edges` consumes: atoms are
    their source text (a string path keeps its quotes), lists are lists."""
    if node.type == "list":
        return [_racket_datum(c) for c in node.children
                if c.type not in ("comment", "block_comment", "sexp_comment", "dot")]
    return node.text.decode("utf-8", "replace")


def _extract_racket_imports(content: str, repo: Optional[str] = None) -> list[dict]:
    """Extract Racket `(require ...)` edges, read by ``racket_reader.py``.

    Handles bare collection paths, string paths, `(submod "file" sub)`, and
    the `only-in` / `rename-in` / `prefix-in` / `except-in` / `for-syntax` /
    `for-meta` / `combine-in` wrappers, each of which may carry several
    module paths. `(submod "." test)` is deliberately skipped -- it names a
    submodule of the same file, not another file.

    ⚠ One reader. This used to carry its own comment-stripper and form reader
    and find `(require` by regex, which matched inside `#;` comments, `#'`
    syntax templates, quasiquoted `eval` payloads and here-strings: measured
    over 2,489 files, 131 "requires" the files do not make and none missed.
    Now the `#lang` tier decides the reader mode (so a project's own at-exp
    lang needs `repo`, the same way the walker does), a `require` is a list
    whose head is that symbol at any depth of CODE, and a document-tier
    file contributes no edges -- a reader it does not have cannot say where
    the Racket in it is.
    """
    from .extractor import _racket_command_char, _racket_tier   # lazy: the cycle runs the other way
    from .racket_reader import read_racket

    source = content.encode("utf-8", "surrogatepass")
    tier, written = _racket_tier(source, repo)
    if tier == "text":
        return []
    tree = read_racket(source, at_exp=(tier == "at-exp"),
                       command_char=_racket_command_char(written, repo))
    edges: list[dict] = []
    seen: set[tuple] = set()
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in _RACKET_NOT_CODE:
            continue
        if node.type == "list":
            kids = [c for c in node.children if c.type not in ("comment", "block_comment", "sexp_comment")]
            if kids and kids[0].type == "symbol" and kids[0].text == b"require":
                for item in kids[1:]:
                    for spec, names in _racket_edges(_racket_datum(item)):
                        key = (spec, tuple(names))
                        if key in seen:
                            continue
                        seen.add(key)
                        edges.append({"specifier": spec, "names": names})
        stack.extend(reversed(node.children))
    return edges


# `(define collection "name")` or `(define collection 'multi)` in an info.rkt.
_RACKET_INFO_COLLECTION_RE = re.compile(
    r"\(define\s+collection\s+(?:\"([^\"]+)\"|'multi\b|\(quote\s+multi\))"
)
_racket_collection_cache: dict[tuple, dict[str, list[str]]] = {}


def build_racket_collection_map(source_root: str, source_files) -> dict[str, list[str]]:
    """Collection name -> root-relative directories, from every `info.rkt`.

    ⚠ A Racket collection path names a DIRECTORY that `info.rkt` declares,
    not a path in the repo. In the layout the packaging docs prescribe --
    `foo-lib/info.rkt` holding `(define collection "foo")` -- `(require
    foo/bar)` means `foo-lib/bar.rkt`, and there is no way to know that
    without reading the file. Measured before this existed: **splitflap, 0 of
    70 require edges resolved**; congame, 147 own-collection specifiers
    unresolved. Every library file read as dead. This is the PSR-4 shape
    (:func:`build_psr4_map`), where `composer.json` maps a namespace prefix
    to a directory.

    A `'multi` package makes every subdirectory a collection named after
    itself. ⚠ Several directories may declare the SAME collection -- Racket
    splices them (`congame-cli`, `congame-core` and `congame-doc` all declare
    `"congame"`) -- so the value is a LIST, in discovery order.

    Cached per (source_root, info.rkt set); the set is part of the key so an
    added package is seen without a restart.
    """
    infos = tuple(sorted(
        p for p in source_files if p == "info.rkt" or p.endswith("/info.rkt")
    ))
    key = (source_root, infos)
    cached = _racket_collection_cache.get(key)
    if cached is not None:
        return cached
    mapping: dict[str, list[str]] = {}
    for info in infos:
        try:
            with open(os.path.join(source_root, *info.split("/")), encoding="utf-8", errors="replace") as fh:
                text = fh.read(65536)
        except OSError:
            logger.debug("info.rkt unreadable: %s", info, exc_info=True)
            continue
        m = _RACKET_INFO_COLLECTION_RE.search(text)
        if not m:
            continue
        d = posixpath.dirname(info)
        if m.group(1):
            dirs = mapping.setdefault(m.group(1), [])
            if d not in dirs:
                dirs.append(d)
        else:
            prefix = d + "/" if d else ""
            for f in source_files:
                if not (f.startswith(prefix) and f.endswith(_RACKET_EXTENSIONS)):
                    continue
                rest = f[len(prefix):]
                if "/" not in rest:
                    continue
                sub = rest.split("/", 1)[0]
                sub_dir = posixpath.join(d, sub) if d else sub
                dirs = mapping.setdefault(sub, [])
                if sub_dir not in dirs:
                    dirs.append(sub_dir)
    _racket_collection_cache[key] = mapping
    return mapping


def augment_racket_collection_edges(imports: dict, source_root: str, source_files) -> int:
    """Add a root-relative file edge beside each collection-path require.

    `(require splitflap/constructs)` from `splitflap-lib/main.rkt` keeps its
    edge to the specifier `splitflap/constructs` -- which resolves to nothing,
    and every consumer already skips an unresolved edge -- and gains one to
    `splitflap-lib/constructs.rkt`, which `resolve_specifier` matches
    directly. A bare collection name (`(require splitflap)`) names the
    collection's `main.rkt`. Same shape as #550: the edge is ADDED at the
    index, so the 26 `resolve_specifier` call sites keep their single-target
    contract and nothing threads a map through them.

    Idempotent: an edge already present is not added twice, so running at
    every `CodeIndex` construction (index time AND load time) is safe.
    Returns the number of edges added.
    """
    if not imports:
        return 0
    cmap = build_racket_collection_map(source_root, source_files)
    if not cmap:
        return 0
    added = 0
    for importer, edges in imports.items():
        if not importer.endswith(_RACKET_EXTENSIONS) or not edges:
            continue
        present = {e.get("specifier") for e in edges}
        new: list[dict] = []
        for e in edges:
            spec = e.get("specifier") or ""
            if not spec or spec.startswith((".", "/")) or spec.endswith(_RACKET_EXTENSIONS):
                continue
            head, _, rest = spec.partition("/")
            for d in cmap.get(head, ()):
                leaf = rest + ".rkt" if rest else "main.rkt"
                target = posixpath.normpath(posixpath.join(d, leaf) if d else leaf)
                if target in source_files and target not in present:
                    new.append({"specifier": target, "names": list(e.get("names") or [])})
                    present.add(target)
                    added += 1
        if new:
            edges.extend(new)
    return added


_LANGUAGE_EXTRACTORS = {
    "javascript": _extract_js_imports,
    "typescript": _extract_js_imports,
    "tsx": _extract_js_imports,
    "jsx": _extract_js_imports,
    "astro": _extract_astro_imports,
    "vue": _extract_vue_imports,
    "svelte": _extract_svelte_imports,
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
    "gleam": _extract_gleam_imports,
    "racket": _extract_racket_imports,
    "dart": _extract_dart_imports,
    "sql": _extract_sql_dbt_imports,
    "asm": _extract_asm_imports,
    "vhdl": _extract_vhdl_imports,
    "verilog": _extract_verilog_imports,
}


def extract_imports(content: str, file_path: str, language: str,
                    repo: Optional[str] = None) -> list[dict]:
    """Extract import edges from source file content.

    Args:
        content: Raw source file text.
        file_path: Path of the file (used for context; not used in extraction).
        language: Language name (must match LANGUAGE_REGISTRY keys).
        repo: Index source root, for extractors whose reading depends on
            project config (Racket's `racket_langs`); None where unknown.

    Returns:
        List of dicts: [{"specifier": str, "names": list[str]}, ...]
        where ``specifier`` is the raw module/path string and ``names`` are
        the specific identifiers imported from that module.
    """
    # Template files (foo.ts.j2, widget.ts.twig, …): mask the engine constructs
    # offset-preserving, then run the underlying language's import extractor on
    # the result. The underlying language is re-derived from the path, so this is
    # handled before the generic _LANGUAGE_EXTRACTORS lookup (whose callbacks
    # receive only `content`).
    if language in TEMPLATE_ENGINE_LANGUAGES:
        underlying = template_underlying_language(file_path)
        underlying_extractor = _LANGUAGE_EXTRACTORS.get(underlying) if underlying else None
        if underlying_extractor is None:
            return []
        try:
            return underlying_extractor(mask_template_keep_offsets(content, language))
        except Exception:
            return []

    extractor = _LANGUAGE_EXTRACTORS.get(language)
    if extractor is None:
        return []
    try:
        if language == "racket":
            return _extract_racket_imports(content, repo=repo)
        return extractor(content)
    except Exception:
        # Practice 2: an extractor that raises loses EVERY edge for this file,
        # and the caller cannot tell that from a file with no imports. Say so.
        logger.warning(
            "import extraction failed for %s (%s); the file will have no import edges",
            file_path, language, exc_info=True,
        )
        return []


_JS_EXTENSIONS = (
    ".js", ".ts", ".jsx", ".tsx", ".vue", ".astro",
    ".mjs", ".cjs", ".mts", ".cts", ".svelte",
)
_PY_EXTENSIONS = (".py",)
_RUBY_EXTENSIONS = (".rb",)
_ALL_EXTENSIONS = _JS_EXTENSIONS + _PY_EXTENSIONS + _RUBY_EXTENSIONS + (".go",)
_RACKET_EXTENSIONS = (".rkt", ".rktl", ".rktd")

# TypeScript's ESM rules require the specifier to name the EMITTED file, so a
# `.mts` source is imported as `./foo.mjs` and a `.cts` source as `./foo.cjs`.
# The specifier therefore names an extension that is never on disk; without the
# rewrite the edge resolves to nothing and the target reports as never imported.
_JS_SPECIFIER_REWRITES = {
    ".js": (".ts", ".tsx"),
    ".mjs": (".mts",),
    ".cjs": (".cts",),
}

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
    """Generate path candidates with and without extension.

    Cases:
    - No extension (`./foo`): try every known source extension and the
      barrel-index forms.
    - JS extension (`./foo.js`, `./foo.mjs`, `./foo.cjs`): plus the TS
      equivalents the specifier stands in for (TS-ESM convention).
    - Recognized file extension other than .js: keep as-is.
    - Unrecognized "extension" (`./injectable.decorator`, `./foo.service`,
      `./order.spec` if treated as code): the dotted suffix is part of
      the basename, not a file extension. Try the same candidates as
      the no-extension case so TS/JS naming conventions like
      `*.service.ts`, `*.decorator.ts`, `*.controller.ts` resolve.
    """
    cands = [base]
    _, ext = posixpath.splitext(base)
    if not ext:
        for e in _ALL_EXTENSIONS:
            cands.append(base + e)
        for e in _JS_EXTENSIONS:
            cands.append(posixpath.join(base, "index" + e))
        cands.append(posixpath.join(base, "__init__.py"))
    elif ext in _JS_SPECIFIER_REWRITES:
        stem = base[: -len(ext)]
        for e in _JS_SPECIFIER_REWRITES[ext]:
            cands.append(stem + e)
    elif ext not in _ALL_EXTENSIONS:
        # Dotted basename: TS/JS convention (`*.service`, `*.decorator`,
        # `*.module`, `*.spec`, etc.). Treat the whole `base` as a stem.
        for e in _ALL_EXTENSIONS:
            cands.append(base + e)
        for e in _JS_EXTENSIONS:
            cands.append(posixpath.join(base, "index" + e))
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


# Cache: frozenset(source_files) -> tuple of Gleam source root prefixes.
# Same keying rationale as _python_roots_cache above.
_gleam_roots_cache: dict[frozenset, tuple[str, ...]] = {}


def _gleam_source_roots(source_files) -> tuple[str, ...]:
    """Detect Gleam source roots from the indexed file set.

    A Gleam module path like ``webse/config_types`` is relative to a
    package's ``src/``, ``test/`` or ``dev/`` directory, so those
    directories are the source roots. They are derived structurally: every
    path prefix of an indexed ``.gleam`` file that ends in a ``src``,
    ``test`` or ``dev`` segment (``cells/webse/src/webse/config.gleam`` ->
    ``cells/webse/src``), plus ``<dir>/src``, ``<dir>/test`` and
    ``<dir>/dev`` for every indexed ``gleam.toml``. The two signals overlap
    for normal packages; the gleam.toml one also covers packages whose
    files did not make it into the index, and the structural one covers
    indexes that exclude toml files.
    """
    cache_key = source_files if isinstance(source_files, frozenset) else frozenset(source_files)
    cached = _gleam_roots_cache.get(cache_key)
    if cached is not None:
        return cached

    roots: set[str] = set()
    for f in source_files:
        if f.endswith(".gleam"):
            parts = f.split("/")
            for i, seg in enumerate(parts[:-1]):
                if seg in ("src", "test", "dev"):
                    roots.add("/".join(parts[: i + 1]))
        elif f == "gleam.toml" or f.endswith("/gleam.toml"):
            pkg_dir = f[: -len("/gleam.toml")] if "/" in f else ""
            for sub in ("src", "test", "dev"):
                roots.add(f"{pkg_dir}/{sub}" if pkg_dir else sub)

    result = tuple(sorted(roots))
    _gleam_roots_cache[cache_key] = result
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
# ⚠⚠ **The FOURTH copy of a skip list in this tree, and the only one that
# derived from nothing** (#557, @Ticki84). `security._SKIP_DIRECTORY_NAMES` is
# the authority -- CLAUDE.md says so, and `SKIP_DIRECTORIES` and `SKIP_PATTERNS`
# already derive from it -- but this set was hand-maintained beside it and had
# never heard of Rust's `target`. So `_walk_tsconfigs` descended into a Tauri
# project's build directory on EVERY watcher event: **13.58s of a 13.75s
# reindex, measured by the reporter in his own `watch-all` process, against
# 0.27s once `target` was excluded.**
#
# ⚠⚠ Adding `"target"` here was the reported fix and would have been the wrong
# one -- that is "fix the call site, leave the mechanism", our own standing
# lesson. Ask the authority instead: every build-tree spelling it already knows
# (`target`, `_build`, `.gradle`, `DerivedData`, the eight dotted framework
# trees) arrives at once, and the next one arrives without touching this file.
#
# ⚠ **UNION, never replacement.** The extras below are tsconfig-specific and
# some are deliberately absent from the authority: `out` names a real source
# directory for the INDEXING walk (CLAUDE.md: "DOTTED ONLY"), but it has been
# skipped for tsconfig discovery for this function's whole life and removing a
# skip is the one direction this change must not take. Union can only make the
# walk cheaper.
_TSCONFIG_EXTRA_SKIP_DIRS = frozenset({
    "out", ".cache", ".next", ".nuxt", ".svelte-kit", ".turbo", ".vercel",
})


def _tsconfig_skip_dirs() -> frozenset[str]:
    """Directory names `_walk_tsconfigs` must not descend into.

    Imported lazily: `security` imports `config`, and resolving it at module
    scope here would put a parser module in that chain for no benefit.
    """
    from ..security import _SKIP_DIRECTORY_NAMES  # noqa: PLC0415
    return frozenset(_SKIP_DIRECTORY_NAMES) | _TSCONFIG_EXTRA_SKIP_DIRS


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

    skip_dirs = _tsconfig_skip_dirs()

    def _walk_tsconfigs(directory: Path, depth: int) -> None:
        # Depth 5 covers layouts up to apps/x/frontend/packages/bar/tsconfig.json.
        if depth > 5:
            return
        try:
            for entry in sorted(directory.iterdir()):
                if entry.is_dir():
                    if entry.name not in skip_dirs and not entry.name.startswith("."):
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


def build_re_export_maps(
    imports: dict,
    source_files: frozenset,
    alias_map: Optional[dict] = None,
    psr4_map: Optional[dict] = None,
) -> tuple[dict[str, list[str]], dict[str, dict[str, tuple[str, str]]]]:
    """Build wildcard + name-keyed re-export maps from raw import data.

    Returns ``(wildcard_map, named_map)``:

    * ``wildcard_map: {barrel_file -> [leaf_file]}`` for ``export * from <spec>``.
    * ``named_map: {barrel_file -> {exposed_name -> (leaf_file, original_name)}}``
      for ``export { Foo as Bar } from <spec>``. The ``original_name`` lets the
      walker chase chains across renames (consumer imports ``Bar``, barrel
      forwards ``Foo``, leaf may itself re-export ``Foo``).

    Old indexes lacking ``re_export_kind`` default to wildcard semantics —
    matches the v1.93 behavior so a fresh re-index is not strictly required.
    """
    wildcard: dict[str, list[str]] = {}
    named: dict[str, dict[str, tuple[str, str]]] = {}
    for src_file, file_imports in imports.items():
        wild_leaves: list[str] = []
        named_leaves: dict[str, tuple[str, str]] = {}
        for imp in file_imports:
            if not imp.get("is_re_export"):
                continue
            target = resolve_specifier(imp["specifier"], src_file, source_files, alias_map, psr4_map)
            if not target or target == src_file:
                continue
            kind = imp.get("re_export_kind", "wildcard")
            if kind == "selective":
                for o in imp.get("re_export_origins", ()):
                    exposed = o.get("exposed")
                    original = o.get("original", exposed)
                    if exposed and exposed not in named_leaves:
                        named_leaves[exposed] = (target, original)
            else:
                wild_leaves.append(target)
        if wild_leaves:
            wildcard[src_file] = list(dict.fromkeys(wild_leaves))
        if named_leaves:
            named[src_file] = named_leaves
    return wildcard, named


def expand_barrel_leaves(
    direct: str,
    consumer_names: list[str],
    wildcard_map: dict[str, list[str]],
    named_map: dict[str, dict[str, tuple[str, str]]],
) -> set[str]:
    """Walk barrel chains to enumerate every leaf an importer transitively credits.

    Args:
        direct: The directly resolved import target (the barrel itself).
        consumer_names: Names imported from ``direct`` by the consumer. An
            empty list means namespace import / side-effect / require — no name
            context, so we wildcard-expand AND walk every named leaf (the safe
            over-credit fallback).
        wildcard_map: Output of :func:`build_re_export_maps`.
        named_map: Output of :func:`build_re_export_maps`.

    Returns the set of leaf files (including ``direct``) the consumer should
    credit. Cycle-safe via a visited set.
    """
    leaves: set[str] = {direct}
    # Each queue entry is (barrel, names) — names=[] means "expand everything"
    queue: deque = deque([(direct, list(consumer_names))])
    visited: set[tuple[str, str]] = set()  # (barrel, name) — re-walk barrel under different name contexts

    while queue:
        barrel, names = queue.popleft()
        wildcard_leaves = wildcard_map.get(barrel, ())
        named_table = named_map.get(barrel, {})

        if not names:
            # No name context — wildcard fallback. Expand every wildcard leaf
            # AND every named leaf (we don't know which name was used, so
            # over-credit; matches the spec for namespace imports).
            for leaf in wildcard_leaves:
                if (leaf, "") not in visited:
                    visited.add((leaf, ""))
                    leaves.add(leaf)
                    queue.append((leaf, []))
            for exposed, (leaf, original) in named_table.items():
                if (leaf, original) not in visited:
                    visited.add((leaf, original))
                    leaves.add(leaf)
                    # Walk the leaf with the original name so chained selective
                    # re-exports (`export { Foo } from './leaf'` where ./leaf is
                    # itself a barrel) resolve correctly.
                    queue.append((leaf, [original]))
            continue

        # Per-name routing
        unrouted: list[str] = []
        for n in names:
            entry = named_table.get(n)
            if entry is not None:
                leaf, original = entry
                if (leaf, original) not in visited:
                    visited.add((leaf, original))
                    leaves.add(leaf)
                    queue.append((leaf, [original]))
            else:
                unrouted.append(n)

        # Names not found in the named table might come from a wildcard
        # re-export inside the same barrel (mixed barrel pattern).
        if unrouted and wildcard_leaves:
            for leaf in wildcard_leaves:
                if (leaf, "") not in visited:
                    visited.add((leaf, ""))
                    leaves.add(leaf)
                    queue.append((leaf, list(unrouted)))

    return leaves


def _resolve_python_relative(
    specifier: str, importer_path: str, source_files: "set[str]"
) -> Optional[str]:
    """Resolve a Python package-relative specifier (jcm#423).

    ``from ..parser.fqn import x`` in ``src/pkg/tools/_utils.py`` resolves to
    ``src/pkg/parser/fqn.py``: N leading dots walk N-1 packages up from the
    importer's own package, and the remaining dotted path names a module.

    Returns None rather than guessing when the walk climbs past the repo root or
    nothing on disk matches, so a caller can fall through to the path reading.
    """
    match = re.match(r"^(\.+)(.*)$", specifier)
    if not match:
        return None
    dots, remainder = match.group(1), match.group(2)

    base_dir = posixpath.dirname(importer_path)
    for _ in range(len(dots) - 1):  # one dot means the importer's own package
        parent = posixpath.dirname(base_dir)
        if parent == base_dir:  # climbed past the root; refuse to guess
            return None
        base_dir = parent

    if remainder:
        base = posixpath.join(base_dir, remainder.replace(".", "/"))
    else:
        base = base_dir  # `from . import x` -> the package's own __init__
    if not base:
        return None

    for candidate in _candidates(base):
        if candidate in source_files:
            return candidate
    return None


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
        # Python's relative form is NOT a path. `..parser.fqn` means "up one
        # package, then parser/fqn" -- the leading dots count package levels and
        # the remaining dots are module separators. Joining it as a path (the
        # JS/TS reading below) yields the single segment `tools/..parser.fqn`,
        # which matches nothing, so package-relative Python imports never built
        # an edge (jcm#423: 71 of 818 resolved on this repo, 8.7%).
        #
        # Everything gated on the import graph inherited that: find_importers,
        # get_blast_radius, get_dependency_graph, get_call_hierarchy's callers
        # direction, and check_delete_safe. A symbol whose only production
        # importer used a relative import presented as imported by tests only,
        # which is the bucket a delete-safety check calls removable.
        #
        # ⚠ Gated on the IMPORTER's extension, not on the specifier's shape, so
        # no JS/TS specifier can take this branch. `./foo` and `../foo/bar` keep
        # the path reading exactly. Python semantics are TRIED FIRST and fall
        # through on miss, so the forms that already resolved still do.
        if importer_path.endswith((".py", ".pyi")):
            resolved = _resolve_python_relative(specifier, importer_path, source_files)
            if resolved:
                return resolved

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

    # Racket: neither real `require` shape reaches a file through the generic
    # candidates, so both are resolved here.
    #
    # ⚠ A STRING require is relative to the IMPORTING FILE and carries no `./`
    # convention -- `(require "helper.rkt")` from `app/main.rkt` means
    # `app/helper.rkt`. The generic relative branch only fires on a leading
    # dot, so this, the commonest intra-project form in Racket, resolved to
    # nothing. A COLLECTION path (`racket/list`) names `<path>.rkt`, and `.rkt`
    # is not in `_ALL_EXTENSIONS`, so that resolved to nothing either.
    #
    # ⚠⚠ Measured before this existed: 2 of the 4 real require shapes resolved,
    # so almost every Racket file showed zero importers and `find_dead_code`
    # reported 78% of the Racket collects tree as dead (against 13% for a Python
    # stdlib corpus indexed the same isolated way). Extracting an import edge
    # that nothing downstream can resolve is indistinguishable from not
    # extracting it.
    #
    # The two shapes are told apart by the extension: a Racket string require
    # must name a file, a collection path never carries one.
    if importer_path.endswith(_RACKET_EXTENSIONS):
        importer_dir = posixpath.dirname(importer_path)
        if specifier.endswith(_RACKET_EXTENSIONS):
            joined = posixpath.normpath(posixpath.join(importer_dir, specifier))
            if joined in source_files:
                return joined
        else:
            for e in _RACKET_EXTENSIONS:
                for cand in (specifier + e,
                             posixpath.normpath(posixpath.join(importer_dir, specifier + e))):
                    if cand in source_files:
                        return cand

    # Python bare module name: a script's own directory is `sys.path[0]`, so
    # `import checks` in `tools/run.py` is `tools/checks.py`, ahead of the root.
    # Climb out of packages (there a bare name is absolute) to the first plain
    # directory and stop: only that one is treated as being on `sys.path`.
    #
    # ⚠⚠ Two refusals, and both err toward NO edge. A false edge gives a file
    # an importer it does not have, so `find_dead_code` stops reporting it and
    # nothing shows; a missed edge is the behaviour before this branch existed.
    # (1) A standard-library name is the standard library: `import types`
    # beside a `types.py` is not that file. (2) A directory with no
    # `__init__.py` that has a package ANYWHERE above it is a namespace
    # sub-package, not a script directory (`pkg/files/main.py` is imported as
    # `pkg.files.main`), so a bare name there is absolute. Any level, not the
    # parent alone: `pkg/types/llms/` is the same shape one directory deeper.
    # That also refuses a real script directory kept inside a package
    # (`pkg/tests/`, `pkg/demos/`).
    if (
        importer_path.endswith((".py", ".pyi"))
        and "." not in specifier
        and "/" not in specifier
        and specifier not in sys.stdlib_module_names
    ):
        script_dir = posixpath.dirname(importer_path)
        while script_dir and f"{script_dir}/__init__.py" in source_files:
            script_dir = posixpath.dirname(script_dir)
        inside_a_package = False
        above = script_dir
        while above and not inside_a_package:
            above = posixpath.dirname(above)
            init = f"{above}/__init__.py" if above else "__init__.py"
            inside_a_package = init in source_files
        if script_dir and not inside_a_package:
            for c in (f"{script_dir}/{specifier}/__init__.py", f"{script_dir}/{specifier}.py"):
                if c in source_files:
                    return c

    # Absolute: try direct match first (e.g., for Go or absolute paths)
    for c in _candidates(specifier):
        if c in source_files:
            return c

    # Gleam module-style import: 'webse/config_types' →
    # 'cells/webse/src/webse/config_types.gleam'. Gleam module paths are
    # slash-joined lowercase segments resolved against a package's src/,
    # test/ or dev/ root; the target extension is always .gleam, so candidates
    # are built directly instead of via _candidates. The importer's own package
    # root is tried first: module paths are only unique per package, and
    # same-package imports are the common case. Stdlib and hex-dependency
    # imports (gleam/io, gleam/list) resolve to nothing — no edge is created.
    if importer_path.endswith(".gleam") and not specifier.startswith("."):
        candidate = specifier + ".gleam"
        if candidate in source_files:
            return candidate
        roots = _gleam_source_roots(source_files)
        # "Own" roots share the importer's package dir (the part above
        # src/test), so a test module prefers its package's src/ root too.
        # A root-level src/test (dirname "") means a single-package repo.
        own = [
            r for r in roots
            if not posixpath.dirname(r) or importer_path.startswith(posixpath.dirname(r) + "/")
        ]
        for root in own + [r for r in roots if r not in own]:
            prefixed = f"{root}/{candidate}"
            if prefixed in source_files:
                return prefixed

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
