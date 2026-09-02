"""Dead code detection — find files and symbols unreachable from any entry point."""

from __future__ import annotations

import fnmatch
import json
import logging
import re
import time
from typing import Optional

from ..storage import IndexStore
from ..parser.imports import resolve_specifier
from ._utils import index_status_to_tool_error, resolve_repo
from ..parser.context._route_utils import ENTRY_POINT_DECORATOR_RE
from ._entry_points import entry_point_spec
from ._runtime_discovery import discover_dynamic_packages
from ._corpus_adequacy import assess_corpus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Entry-point heuristics
# ---------------------------------------------------------------------------

_ENTRY_POINT_FILENAMES = frozenset({
    "__main__.py",
    "conftest.py",
    "manage.py",
    "wsgi.py",
    "asgi.py",
    "setup.py",
    "app.py",
    "main.py",
    "run.py",
    "cli.py",
    "celery.py",
    "Makefile",
})

# ⚠⚠ Toolchain manifests and lockfiles (#562, @lilubot). These are indexed as
# source (JSON/YAML/TOML are real languages here) and NOTHING IMPORTS THEM BY
# DESIGN, so `zero_importers` fires on every one and the tool reported
# `pnpm-lock.yaml`, `package.json` and `tsconfig.json` as dead code. It is the
# same structural zero as a Next.js route handler in #561: an external runner
# invokes them, so absence of importers is a tautology rather than a finding.
#
# ⚠⚠ `package.json` is the sharpest instance -- `_package_json_entries` READS
# it to discover the repo's entry points and the same run then reported it
# dead.
#
# ⚠ `Makefile` was already in the set above for exactly this reason, which is
# why these belong beside it rather than in a new mechanism. Names only, never
# an extension rule: a genuinely orphaned `data/fixtures.json` is a real
# finding and must keep being reported.
_TOOLCHAIN_MANIFESTS = frozenset({
    # JS / TS
    "package.json", "package-lock.json", "pnpm-lock.yaml", "pnpm-workspace.yaml",
    "yarn.lock", "npm-shrinkwrap.json", "bun.lockb", "tsconfig.json",
    "jsconfig.json", "turbo.json", "lerna.json", "rush.json", "deno.json",
    "deno.lock",
    # Python
    "pyproject.toml", "setup.cfg", "Pipfile", "Pipfile.lock", "poetry.lock",
    "uv.lock", "requirements.txt",
    # Rust / Go / Ruby / PHP / Elixir / Dart
    "Cargo.toml", "Cargo.lock", "go.mod", "go.sum", "go.work",
    "Gemfile", "Gemfile.lock", "composer.json", "composer.lock",
    "mix.exs", "mix.lock", "pubspec.yaml", "pubspec.lock",
    # JVM / .NET
    "build.gradle", "build.gradle.kts", "settings.gradle", "pom.xml",
    # Containers / CI
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
})

_MAIN_GUARD_RE = re.compile(r'if\s+__name__\s*==\s*["\']__main__["\']')

# Documentation files are consumed by humans/agents, never imported by code —
# zero importers is their steady state, not a dead-code signal. Markdown is
# indexed (heading/code_block symbols); without this guard every .md file
# reports `zero_importers` at confidence 1.0 and all its headings show up as
# dead symbols.
_DOC_EXTENSIONS = (".md", ".markdown")


def _is_entry_point_filename(file_path: str) -> bool:
    filename = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    return filename in _ENTRY_POINT_FILENAMES or filename in _TOOLCHAIN_MANIFESTS


def _is_init_file(file_path: str) -> bool:
    filename = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    return filename == "__init__.py"


def _is_test_file(file_path: str) -> bool:
    fp = file_path.replace("\\", "/")
    fn = fp.rsplit("/", 1)[-1]
    base = fn.rsplit(".", 1)[0] if "." in fn else fn
    return (
        "/tests/" in fp
        or "/test/" in fp
        or "/__tests__/" in fp
        or fn.startswith("test_")
        or fn.endswith("_test.py")
        or fn == "conftest.py"
        or base.endswith(".spec")    # foo.spec.ts, foo.spec.js
        or base.endswith(".test")    # foo.test.ts, foo.test.js
    )


def _matches_any_pattern(file_path: str, patterns: list[str]) -> bool:
    fp_fwd = file_path.replace("\\", "/")
    for pat in patterns:
        if fnmatch.fnmatch(fp_fwd, pat) or fnmatch.fnmatch(fp_fwd.rsplit("/", 1)[-1], pat):
            return True
    return False


def unmatched_patterns(patterns: Optional[list[str]], source_files) -> list[str]:
    """Which caller-supplied patterns matched no indexed file.

    v1.108.275 (#446). ``entry_point_patterns`` is documented as glob patterns and
    matched with ``fnmatch``, which supports only ``*``, ``?`` and ``[seq]``. Two
    constructs that work in every shell do not work here and neither fails loudly:

    * **Brace alternation.** ``src/main.{ts,js}`` needs a filename literally
      containing ``{ts,js}``. We shipped this mistake ourselves in v1.108.271 and
      did not notice for a release (#445).
    * **``**`` as "zero or more directories".** ``**/`` becomes ``(?>.*?/)``, which
      REQUIRES a slash, so ``plugins/**/*.ts`` misses ``plugins/auth.ts``.

    ⚠⚠ **A pattern that matches nothing is indistinguishable from a repo that
    genuinely has no such entry points** — same output, same confidence, no marker.
    The caller gets more symbols reported unreachable and no reason to doubt it.
    Naming the patterns that did nothing is what turns that into a signal, and it
    covers every cause at once (braces, ``**``, a typo, the wrong path root)
    rather than the one spelling we happened to get wrong.

    ⚠ Deliberately NOT a rejection. A pattern matching nothing is legitimate — a
    caller may pass one set of patterns across several repos. This reports; it
    never refuses.
    """
    if not patterns:
        return []
    files = list(source_files)
    return [p for p in patterns if not any(_matches_any_pattern(f, [p]) for f in files)]


def _package_json_entries(index, store, owner: str, repo_name: str) -> set[str]:
    """Return source files referenced by any ``package.json``'s ``main`` /
    ``module`` / ``exports`` / ``bin`` field. JS-library equivalent of the
    Python ``app.py``/``main.py`` filename heuristic. (Backported from
    get_dead_code_v2 in v1.80.8 — sverklo bench parity.)
    """
    entries: set[str] = set()
    source_files = frozenset(index.source_files)
    for f in index.source_files:
        fn = f.replace("\\", "/").rsplit("/", 1)[-1]
        if fn != "package.json":
            continue
        content = store.get_file_content(owner, repo_name, f)
        if not content:
            continue
        try:
            pkg = json.loads(content)
        except (ValueError, TypeError):
            continue
        if not isinstance(pkg, dict):
            continue
        candidates: list[str] = []
        for key in ("main", "module", "browser"):
            v = pkg.get(key)
            if isinstance(v, str):
                candidates.append(v)
        exports = pkg.get("exports")
        if isinstance(exports, str):
            candidates.append(exports)
        elif isinstance(exports, dict):
            def _walk_exports(node):
                if isinstance(node, str):
                    candidates.append(node)
                elif isinstance(node, dict):
                    for v in node.values():
                        _walk_exports(v)
            _walk_exports(exports)
        bins = pkg.get("bin")
        if isinstance(bins, str):
            candidates.append(bins)
        elif isinstance(bins, dict):
            candidates.extend(v for v in bins.values() if isinstance(v, str))

        pkg_dir = f.replace("\\", "/").rsplit("/", 1)[0] if "/" in f else ""
        for cand in candidates:
            cand = cand.lstrip("./").replace("\\", "/")
            joined = f"{pkg_dir}/{cand}" if pkg_dir else cand
            joined = joined.lstrip("/")
            if joined in source_files:
                entries.add(joined)
                continue
            for ext in ("", ".js", ".ts", ".mjs", ".cjs", ".mts", ".cts",
                        ".jsx", ".tsx",
                        "/index.js", "/index.ts", "/index.mjs",
                        "/index.cjs"):
                trial = joined + ext
                if trial in source_files:
                    entries.add(trial)
                    break
    return entries


def _has_entry_point_decorator(sym: dict) -> bool:
    for dec in sym.get("decorators") or []:
        if ENTRY_POINT_DECORATOR_RE.search(str(dec)):
            return True
    return False


def _build_reverse_adjacency(
    imports: dict, source_files: frozenset, alias_map: Optional[dict] = None,
    psr4_map: Optional[dict] = None,
) -> dict[str, list[str]]:
    """Return {file: [files_that_import_it]} from raw import data."""
    rev: dict[str, list[str]] = {}
    for src_file, file_imports in imports.items():
        for imp in file_imports:
            target = resolve_specifier(imp["specifier"], src_file, source_files, alias_map, psr4_map)
            if target and target != src_file:
                rev.setdefault(target, []).append(src_file)
    return {k: list(dict.fromkeys(v)) for k, v in rev.items()}


# ---------------------------------------------------------------------------
# Public tool function
# ---------------------------------------------------------------------------

def find_dead_code(
    repo: str,
    granularity: str = "symbol",
    min_confidence: float = 0.8,
    include_tests: bool = False,
    entry_point_patterns: Optional[list[str]] = None,
    storage_path: Optional[str] = None,
) -> dict:
    """Find dead code — files and symbols with no importers and no entry-point role.

    Args:
        repo: Repository identifier (owner/repo or bare name).
        granularity: "symbol" returns dead symbols (default); "file" returns dead files only.
        min_confidence: Minimum confidence threshold (0.0–1.0). Default 0.8.
        include_tests: Treat test files as live roots (default false).
        entry_point_patterns: Additional glob patterns to treat as live roots.
        storage_path: Custom storage path.
    """
    start = time.perf_counter()
    entry_point_patterns = entry_point_patterns or []

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    if index.imports is None:
        return {
            "error": (
                "No import data available. Re-index with jcodemunch-mcp >= 1.3.0 "
                "to enable dead code analysis."
            )
        }

    source_files = frozenset(index.source_files)
    rev = _build_reverse_adjacency(index.imports, source_files, index.alias_map, getattr(index, "psr4_map", None))

    # -----------------------------------------------------------------------
    # Phase 1: identify live roots by filename pattern + package.json (no I/O
    # for the filename pass; package.json parsing is bounded by # of manifests).
    # -----------------------------------------------------------------------
    # ⚠⚠ `_ENTRY_POINT_FILENAMES` is Python and nothing else -- `main.py`,
    # `app.py`, `__main__.py` and eleven siblings. On a Next.js repo it names
    # nothing, so every signal fired on every symbol and `get_dead_code_v2`
    # answered `dead_symbols: []` with a warning, which a downstream consumer
    # reads as proof of zero dead code (#562, @lilubot). The framework profile
    # detected at index time already declares the right roots -- `route.ts`,
    # `page.tsx`, `layout.tsx`, `middleware.ts` for Next -- and had no reader
    # in the tree. Ask the authority.
    fw_spec = entry_point_spec(index)
    live_roots: set[str] = set()
    for f in index.source_files:
        if _is_entry_point_filename(f):
            live_roots.add(f)
        elif _is_init_file(f):
            live_roots.add(f)
        elif include_tests and _is_test_file(f):
            live_roots.add(f)
        elif entry_point_patterns and _matches_any_pattern(f, entry_point_patterns):
            live_roots.add(f)
        elif fw_spec.matches(f):
            live_roots.add(f)
    # JS-library entry points: whatever package.json declares as main/module/
    # exports/bin. Without this, library files like Express's lib/express.js
    # had only `index.js` as importer, that index had no further importers,
    # so the file was misclassified as `all_importers_dead` at confidence 0.7.
    pkg_entries = _package_json_entries(index, store, owner, name)
    live_roots.update(pkg_entries)

    # -----------------------------------------------------------------------
    # Phase 1b: render-edge reachability (#461)
    # -----------------------------------------------------------------------
    # A template is never IMPORTED. It is reached by a render edge — a string
    # argument (`render(request, "page.html")`) that `flow_edges` resolves to a
    # file. Classifying from the import graph alone therefore reports an
    # actively-rendered template as `zero_importers` at confidence 1.0, the
    # value reserved for "no importers and not a test file", while
    # `_resolve_template` resolves that same file from the same index in the
    # same process. Two subsystems disagreeing is bad; the one that was wrong
    # being the one with no hedge is worse.
    #
    # ⚠ This is deliberately NOT an extension exemption. A template that
    # nothing renders IS dead and must still be reported — `.html` is not
    # special, having an inbound render edge is. An exemption would suppress
    # the true positives with the false ones and would not generalise to the
    # other edge families `resolve_flow_edges` already emits.
    #
    # ⚠ Always-on rather than behind a parameter: this tool ALREADY reads file
    # content in two places (package.json entries above, the `__main__` guard
    # in Phase 2), so consulting a content-scanning resolver introduces no new
    # class of work. The issue text originally claimed otherwise and was wrong.
    render_reachable: set[str] = set()
    try:
        from .flow_edges import resolve_flow_edges

        for edge in resolve_flow_edges(index, store, owner, name, kinds=("render",)):
            if edge.get("resolution") != "resolved":
                continue
            dst = edge.get("dst_file")
            if dst and dst in source_files:
                render_reachable.add(dst)
    except Exception:  # pragma: no cover - resolver must never fail the tool
        logger.debug("render-edge reachability pass failed", exc_info=True)
    live_roots.update(render_reachable)

    # -----------------------------------------------------------------------
    # Phase 1c: runtime-discovered packages (#569)
    # -----------------------------------------------------------------------
    # A package that enumerates itself at import time — `pkgutil.iter_modules`
    # over its own `__path__`, then `importlib.import_module` on each name —
    # builds an edge no static graph can see. Twelve live encoders under
    # `encoding/schemas/` were published here at confidence 1.0, and which of
    # the fifteen escaped depended only on whether a test happened to import
    # the module directly. That is test-authoring habit, not reachability.
    #
    # ⚠ The unresolved half is NOT silent: a loader whose target directory we
    # cannot name feeds `assess_corpus` below and caps the confidence instead,
    # because the alternative is publishing a proof over a graph we know has an
    # invisible edge somewhere in it.
    dynamic = discover_dynamic_packages(index, store, owner, name)
    live_roots.update(f for f in dynamic.roots if f in source_files)

    # -----------------------------------------------------------------------
    # Phase 2: content check for `if __name__ == "__main__"` (Python only,
    # only for files not yet classified as live and with zero importers)
    # -----------------------------------------------------------------------
    for f in index.source_files:
        if f in live_roots or rev.get(f):
            continue
        if not (f.endswith(".py") or f.endswith(".pyw")):
            continue
        content = store.get_file_content(owner, name, f)
        if content and _MAIN_GUARD_RE.search(content):
            live_roots.add(f)

    # -----------------------------------------------------------------------
    # Phase 3: classify files
    # -----------------------------------------------------------------------
    # Pre-compute which files have only dead importers (for cascading 0.7 case)
    # A file's importers are "all dead" when each importer has zero importers
    # of its own and is not a live root — simple one-hop check, avoids deep BFS.

    # ⚠⚠ (#566) `confidence: 1.0` is documented as PROVABLY UNREACHABLE, which
    # is a claim about the tree. It was being computed from the index with
    # nothing in between, so a stale index and a withheld `too_large` file each
    # published live files as proven dead. `assess_corpus` reads the disclosures
    # the index already carries — the same ones `search_text` reads to refuse an
    # absence claim on the identical corpus — and caps what may be asserted.
    adequacy = assess_corpus(
        index,
        extra_blockers=(
            ["runtime_discovery_unresolved"] if dynamic.unresolved else []
        ),
    )
    ceiling = adequacy.ceiling

    dead_files: list[dict] = []

    for f in sorted(index.source_files):
        if f in live_roots:
            continue
        if not include_tests and _is_test_file(f):
            continue
        if f.lower().endswith(_DOC_EXTENSIONS):
            continue

        importers = rev.get(f, [])

        if not importers:
            confidence = 0.9 if _is_test_file(f) else 1.0
            reason = "zero_importers"
        else:
            # Check for cascading dead code: all importers themselves have
            # zero importers and are not live roots
            all_dead = all(
                not rev.get(imp) and imp not in live_roots
                for imp in importers
            )
            if all_dead:
                confidence = 0.7
                reason = "all_importers_dead"
            else:
                continue  # file is reachable, skip

        capped = min(confidence, ceiling)
        if capped < min_confidence:
            continue

        entry = {
            "file": f,
            "confidence": capped,
            "reason": reason,
            "importer_count": len(importers),
        }
        if capped < confidence:
            # Both numbers, never just the survivor: a reader auditing this
            # verdict needs to see that the graph said one thing and the corpus
            # could not back it, which a single clamped figure hides.
            entry["uncapped_confidence"] = confidence
            entry["confidence_capped_by"] = list(adequacy.blockers)
        dead_files.append(entry)

    # -----------------------------------------------------------------------
    # Phase 4: symbol-level results
    # -----------------------------------------------------------------------
    dead_symbols: list[dict] = []

    if granularity == "symbol":
        dead_file_map = {d["file"]: d for d in dead_files}

        for sym in index.symbols:
            sym_file = sym.get("file", "")
            if sym_file not in dead_file_map:
                continue

            file_entry = dead_file_map[sym_file]
            confidence = file_entry["confidence"]
            reason = file_entry["reason"]

            # Framework decorators lower confidence
            if _has_entry_point_decorator(sym):
                confidence = min(confidence, 0.5)
                reason = "framework_decorator"

            if confidence < min_confidence:
                continue

            dead_symbols.append({
                "symbol_id": sym.get("id", ""),
                "file": sym_file,
                "kind": sym.get("kind", ""),
                "confidence": confidence,
                "reason": reason,
            })

    # -----------------------------------------------------------------------
    # Response
    # -----------------------------------------------------------------------
    elapsed = (time.perf_counter() - start) * 1000

    sample_roots = sorted(live_roots)[:5]
    analysis_notes = [
        f"Entry points detected: {len(live_roots)}",
        f"Total files analyzed: {len(index.source_files)}",
    ]
    if sample_roots:
        analysis_notes.append(f"Sample entry points: {', '.join(sample_roots)}")
    # Surfaced separately from the entry-point total (#461): a file kept alive by
    # an inbound render edge is reachable for a different reason than a file that
    # looks like an entry point, and a caller auditing this tool's verdict cannot
    # tell them apart from a single count.
    if render_reachable:
        analysis_notes.append(
            f"Reachable via render edges (not imports): {len(render_reachable)}"
        )
    # Same rule as the render-edge line above: a file kept alive by a runtime
    # enumeration is reachable for a reason the import graph does not contain,
    # and a single entry-point total cannot say so.
    if dynamic.roots:
        analysis_notes.append(
            f"Reachable via runtime package enumeration (not imports): "
            f"{len(dynamic.roots)} in "
            f"{', '.join(sorted(dynamic.packages)[:3])}"
        )
    # ⚠ Which framework supplied the roots is part of the verdict, not trivia:
    # "42 entry points" and "42 entry points, because we recognised Next.js"
    # are different claims, and only the second lets a reader see that the
    # answer would change on a framework we do not profile.
    if fw_spec.profile_name:
        fw_roots = sum(1 for f in live_roots if fw_spec.matches(f))
        analysis_notes.append(
            f"Framework profile '{fw_spec.profile_name}' declared "
            f"{fw_roots} of them"
        )

    result: dict = {
        "repo": f"{owner}/{name}",
        "granularity": granularity,
        "min_confidence": min_confidence,
        "dead_symbols": dead_symbols,
        "dead_files": dead_files,
        "dead_file_count": len(dead_files),
        "dead_symbol_count": len(dead_symbols),
        "live_root_count": len(live_roots),
        "framework_profile": fw_spec.profile_name,
        "render_reachable_count": len(render_reachable),
        "runtime_discovered_count": len(dynamic.roots),
        "corpus_adequacy": adequacy.as_dict(),
        "analysis_notes": analysis_notes,
        "_meta": {"timing_ms": round(elapsed, 1)},
    }
    if dynamic.packages:
        result["runtime_discovered_packages"] = {
            d: sorted(loaders) for d, loaders in sorted(dynamic.packages.items())
        }
    if dynamic.unresolved:
        result["runtime_discovery_unresolved"] = dynamic.unresolved
    # ⚠⚠ A capped run returns FEWER findings, and an empty list read alone is
    # the `dead_code_pct: 0.0` shape (#559) seen from the other side — an
    # admission that nothing was established, rendered as a clean bill of
    # health. `signal_warning` is the spelling `get_dead_code_v2` already uses
    # for exactly this, so a consumer gates on one field across both tools.
    _adequacy_warning = adequacy.warning()
    if _adequacy_warning:
        result["signal_warning"] = _adequacy_warning
        analysis_notes.append(_adequacy_warning)

    # v1.108.275 (#446). Until now this tool reported NOTHING when a caller's
    # patterns matched no file, at any confidence — the sibling `get_dead_code_v2`
    # at least had a message, though gated. Silence here is the worse half: the
    # answer looks the same as an honest one.
    _unmatched = unmatched_patterns(entry_point_patterns, index.source_files)
    if _unmatched:
        result["entry_point_patterns_unmatched"] = _unmatched
        result["warning"] = (
            f"{len(_unmatched)} of {len(entry_point_patterns)} entry_point_patterns "
            f"matched no indexed file, so they contributed no live roots: "
            f"{', '.join(_unmatched[:5])}"
            + (f" (+{len(_unmatched) - 5} more)" if len(_unmatched) > 5 else "")
            + ". Patterns are matched with fnmatch against repo-relative paths: "
            "brace alternation ({ts,js}) is NOT expanded, and ** does not match "
            "zero directories (plugins/**/*.ts misses plugins/auth.ts). "
            "List each extension separately and add the flat form."
        )
    return result
