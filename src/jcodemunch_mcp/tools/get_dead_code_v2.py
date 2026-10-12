"""get_dead_code_v2 — multi-signal dead code detection with confidence scores.

Three independent evidence signals per symbol:
  1. Import graph: no file imports the symbol's defining file.
  2. Call graph: no indexed symbol calls this symbol.
  3. Barrel export: the symbol is not re-exported from an ``__init__`` or
     barrel/index file that is itself reachable.

Confidence = number of signals present / 3.
Only symbols with kind ``function`` or ``method`` are analysed (classes and
constants are excluded to reduce noise).
"""

from __future__ import annotations

import re
import time
from bisect import bisect_left
from collections import deque
from typing import Optional

from ..storage import IndexStore
from ..parser.imports import resolve_specifier
from ..parser.symbols import STATE_KINDS
from ._utils import resolve_repo as _resolve_repo
from ._call_graph import (
    LANGUAGES_CALLED_ACROSS_FILES_WITHOUT_AN_IMPORT,
    FileWords,
    _word_match,
    adjacent_word_pairs,
    build_symbols_by_file,
    constructors_as_their_classes,
    is_one_word,
    words_of,
)
# One matcher, not two: entry_point_patterns must mean the same thing in
# both dead-code tools or #436 gets replaced by a subtler version of itself.
from ._entry_points import (
    entry_point_spec,
    is_host_invoked,
    package_json_entries as _package_json_entries,
    unseen_consumer,
)
from .find_dead_code import _matches_any_pattern, unmatched_patterns
from ._runtime_discovery import discover_dynamic_packages
from ._dynamic_boundary import FILES_CAP, DynamicBoundary
from ._test_paths import is_test_file as _is_test_file  # noqa: F401  (the one rule, LEDGER L-101)
from ..parser.context._route_utils import ENTRY_POINT_DECORATOR_RE


# ---------------------------------------------------------------------------
# Helpers shared with find_dead_code
# ---------------------------------------------------------------------------

_ENTRY_POINT_FILENAMES = frozenset({
    "__main__.py", "conftest.py", "manage.py", "wsgi.py", "asgi.py",
    "setup.py", "app.py", "main.py", "run.py", "cli.py", "celery.py",
    "Makefile",
})

_BARREL_FILENAMES = frozenset({
    "__init__.py", "index.ts", "index.js", "index.tsx", "index.jsx",
    "index.mjs", "index.cjs",
    "mod.rs",
})

# CJS re-export: `module.exports = require('./X')` / `exports.foo = require('./X')`
_CJS_REEXPORT_RE = re.compile(
    r"""(?:module\.)?exports?(?:\.\w+)?\s*=\s*require\(\s*['"]([^'"]+)['"]\s*\)"""
)
# ES re-export-all: `export * from './X'` / `export * as ns from './X'`
_ESM_REEXPORT_STAR_RE = re.compile(
    r"""export\s+\*(?:\s+as\s+\w+)?\s+from\s+['"]([^'"]+)['"]"""
)
# ES named re-export: `export { foo, bar } from './X'`
# The three signals, in the order they are evaluated. Named once so the
# instrument (#408) and the scorer cannot drift apart on spelling.
_SIGNAL_NAMES = ("unreachable_file", "no_callers", "not_barrel_exported")

# Identifier tokens in a file's module-level residue (#409). ``_IDENT_ONLY_RE``
# decides whether a symbol name can be matched by token equality at all.
_IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_IDENT_ONLY_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")

_ESM_REEXPORT_NAMED_RE = re.compile(
    r"""export\s*\{[^}]+\}\s*from\s+['"]([^'"]+)['"]"""
)


def _filename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _is_entry_point(file_path: str) -> bool:
    return _filename(file_path) in _ENTRY_POINT_FILENAMES


def _is_barrel(file_path: str) -> bool:
    return _filename(file_path) in _BARREL_FILENAMES


def _build_reverse_adjacency(imports: dict, source_files: frozenset, alias_map: dict, psr4_map: Optional[dict] = None) -> dict[str, list[str]]:
    rev: dict[str, list[str]] = {}
    for src_file, file_imports in imports.items():
        for imp in file_imports:
            target = resolve_specifier(imp["specifier"], src_file, source_files, alias_map, psr4_map)
            if target and target != src_file:
                rev.setdefault(target, []).append(src_file)
    return {k: list(dict.fromkeys(v)) for k, v in rev.items()}


def _build_forward_adjacency(imports: dict, source_files: frozenset, alias_map: dict, psr4_map: Optional[dict] = None) -> dict[str, list[str]]:
    """Forward adjacency: ``forward[src_file] = [imported targets]``.

    Required so reachability BFS from entry points actually traverses the
    dependency graph (the pre-1.80.7 reverse-only walk only found importers
    of the entry, not files imported by it — which is why every library
    file was treated as unreachable).
    """
    fwd: dict[str, list[str]] = {}
    for src_file, file_imports in imports.items():
        for imp in file_imports:
            target = resolve_specifier(imp["specifier"], src_file, source_files, alias_map, psr4_map)
            if target and target != src_file and target in source_files:
                fwd.setdefault(src_file, []).append(target)
    return {k: list(dict.fromkeys(v)) for k, v in fwd.items()}


def _reachable_from_entry_points(
    source_files: list[str],
    rev: dict[str, list[str]],
    forward: dict[str, list[str]],
    extra_entries: Optional[set[str]] = None,
) -> set[str]:
    """Files reachable from entry points via the import graph.

    Walks both directions to maximise the live set:
      * **Forward**: from each entry, visit everything it imports
        (the standard "what does the entry depend on?" semantic). Pre-1.80.7
        only walked reverse, so library files imported by the entry were
        wrongly treated as unreachable.
      * **Reverse**: also pull in any file that imports the entry (e.g. a
        test that imports ``app.py``). Preserves prior behavior.

    ``extra_entries`` supplements the filename heuristic (e.g. files
    declared by ``package.json`` ``main``).
    """
    live: set[str] = set()
    queue: deque[str] = deque()
    for f in source_files:
        if _is_entry_point(f) or (extra_entries and f in extra_entries):
            live.add(f)
            queue.append(f)
    for f in (extra_entries or ()):
        if f not in live:
            live.add(f)
            queue.append(f)
    while queue:
        node = queue.popleft()
        for imported in forward.get(node, []):
            if imported not in live:
                live.add(imported)
                queue.append(imported)
        for importer in rev.get(node, []):
            if importer not in live:
                live.add(importer)
                queue.append(importer)
    return live


def _barrel_exports(
    index,
    store,
    owner,
    repo_name,
    source_files: frozenset,
    alias_map: dict,
    psr4_map: Optional[dict] = None,
) -> set[str]:
    """Return symbol names exported from any barrel / __init__ file.

    Recursively follows CommonJS ``module.exports = require('./X')`` and ES
    module ``export * from './X'`` / ``export {…} from './X'`` patterns so
    that names defined in ``./X`` count as barrel-exported. Without this,
    libraries that re-export through an index file (Express, Lodash, etc.)
    falsely appear dead. Bounded depth prevents pathological re-export
    chains from blowing up the scan. (Issue: sverklo bench v1 — Express
    `createApplication` flagged as dead due to `module.exports = require(
    './lib/express')`.)
    """
    exported: set[str] = set()
    visited: set[str] = set()
    MAX_DEPTH = 4

    def _collect(file_path: str, depth: int) -> None:
        if file_path in visited or depth > MAX_DEPTH:
            return
        visited.add(file_path)
        content = store.get_file_content(owner, repo_name, file_path)
        if not content:
            return
        # Identifiers literally present in this file (original behavior).
        exported.update(re.findall(r"\b([A-Za-z_]\w*)\b", content))
        # Recursively expand re-export targets.
        targets: set[str] = set()
        for m in _CJS_REEXPORT_RE.finditer(content):
            targets.add(m.group(1))
        for m in _ESM_REEXPORT_STAR_RE.finditer(content):
            targets.add(m.group(1))
        for m in _ESM_REEXPORT_NAMED_RE.finditer(content):
            targets.add(m.group(1))
        for spec in targets:
            resolved = resolve_specifier(spec, file_path, source_files,
                                         alias_map, psr4_map)
            if resolved and resolved in source_files:
                _collect(resolved, depth + 1)

    for f in index.source_files:
        if _is_barrel(f):
            _collect(f, 0)
    return exported


# ---------------------------------------------------------------------------
# Main tool
# ---------------------------------------------------------------------------

# A signal firing on >= this share of analysed symbols (or <= 1 - this) is a
# constant on that repository, not a discriminator, and gets no vote (#408).
#
# ⚠ Chosen from measurement, not taste. Sweeping the cutoff across 31 indexed
# repositories (Python, Go, TypeScript, JavaScript, Rust): 0.90 is the LARGEST
# value at which no repository reports more than 90% of its functions as dead.
# 0.95 still leaves httpx at 93.1%, which is the exact failure mode this closes.
# 0.80 over-suppresses — the median flag rate hits 0.0% and 17 of 31 repos go
# empty. At 0.90 the corpus mean falls 57.3% -> 27.4%, the median 54.0% -> 26.3%,
# and six repositories correctly return nothing.
#
# ⚠ It does NOT fire where all three signals discriminate: pylint, flask,
# matplotlib, astropy, scikit-learn, xarray, sphinx and next keep their previous
# answers exactly. This is not a blanket suppression.
_DEGENERACY_CUTOFF = 0.90


def _resolve_cutoff(value: Optional[float]) -> float:
    """Validate a caller-supplied degeneracy cutoff.

    Only ``0.5 < cutoff <= 1.0`` is coherent. The rule is
    ``1 - cutoff < rate < cutoff``, so at 0.5 the bounds meet and at anything
    below it they cross: every signal would be uninformative and the tool would
    return nothing for every repository on earth. That is a silent, total
    failure, so it is refused rather than clamped.

    ``1.0`` is the documented escape hatch: it excludes only a signal that fires
    on exactly every or exactly no symbol, which is close to the pre-v1.108.231
    behaviour for callers who want the old volume back.
    """
    if value is None:
        return _DEGENERACY_CUTOFF
    try:
        cutoff = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"degeneracy_cutoff must be a number between 0.5 (exclusive) and 1.0, got {value!r}"
        )
    if not (0.5 < cutoff <= 1.0):
        raise ValueError(
            f"degeneracy_cutoff must be >0.5 and <=1.0, got {cutoff}. Below 0.5 the "
            "informative band inverts and every signal is excluded, which returns "
            "nothing for every repository."
        )
    return cutoff


def _informative_signals(
    analysed: int,
    fire_counts: dict[str, int],
    cutoff: float = _DEGENERACY_CUTOFF,
    undecided: Optional[dict[str, int]] = None,
) -> set[str]:
    """The signals that actually discriminate on this repository.

    A signal that fires on nearly every symbol accuses everything; one that
    fires on nearly none accuses nothing. Either way it moved no verdict
    relative to its peers while still setting the scale they were divided by.

    With no symbols analysed there is nothing to measure, so every signal is
    treated as informative — refusing to score on an empty repository would be
    inventing a verdict, not withholding one.

    ``undecided`` (LEDGER L-70) counts symbols a signal could not be decided
    for. Its rate is measured over the rest; a signal decided for no symbol
    was not measured and gets no vote.
    """
    if not analysed:
        return set(_SIGNAL_NAMES)
    lo = 1.0 - cutoff
    out: set[str] = set()
    for s in _SIGNAL_NAMES:
        decided = analysed - (undecided or {}).get(s, 0)
        if decided > 0 and lo < (fire_counts.get(s, 0) / decided) < cutoff:
            out.add(s)
    return out


def _signal_diagnostics(
    analysed: int,
    fire_counts: dict[str, int],
    cofire_counts: dict[str, int],
    entry_point_count: int,
    cutoff: float = _DEGENERACY_CUTOFF,
    undecided: Optional[dict[str, int]] = None,
) -> dict:
    """Report what each signal actually measured on THIS repository (#408).

    ``confidence`` is an unweighted vote: each of the three signals contributes
    exactly one third, whatever it is worth here. Two facts decide what a vote
    is worth, and neither was visible in the response before this:

    * **fire_rate** — the fraction of analysed symbols the signal fired on. A
      signal that fires on everything is a constant, not a discriminator, and it
      still contributes its full third. This is the machine-readable form of the
      ``framework_warning`` prose: when no entry point is found, Signal 1's rate
      goes to 1.0 by construction.
    * **cofire_rate** — how often a pair fired together. The docstring calls
      these "three independent signals"; ``unreachable_file`` and
      ``not_barrel_exported`` are in fact strongly correlated, so a 2-of-3
      verdict built from that pair can be one underlying fact counted twice.

    ⚠ **This is an instrument, not a fix.** It changes no verdict and no
    confidence value. It exists so the weighting change that follows can be read
    against a measured before, rather than argued from first principles — the
    reason the two ship in separate releases.

    ``uninformative`` names a signal whose rate is at the degenerate end (0.0 or
    1.0). At 1.0 it accuses everything; at 0.0 it accuses nothing. Either way it
    moved no verdict relative to the others while still setting the scale.
    """
    diag: dict = {
        "analysed": analysed,
        "entry_points_detected": entry_point_count,
        "confidence_basis": "informative_signals_over_3",
    }
    if not analysed:
        return diag
    undecided = {s: n for s, n in (undecided or {}).items() if n}
    diag["fire_rate"] = {
        s: (
            round(fire_counts.get(s, 0) / (analysed - undecided.get(s, 0)), 4)
            if analysed - undecided.get(s, 0) > 0 else None
        )
        for s in _SIGNAL_NAMES
    }
    if undecided:
        # (LEDGER L-70) symbols a signal could not be decided for; its
        # fire_rate is over the rest, and None when nothing was decided.
        diag["undecided"] = undecided
    diag["cofire_rate"] = {
        k: round(v / analysed, 4) for k, v in sorted(cofire_counts.items())
    }
    informative = _informative_signals(analysed, fire_counts, cutoff, undecided)
    diag["informative"] = sorted(informative)
    # ⚠ Semantics tightened in v1.108.231. In .230 this meant "rate is exactly
    # 0.0 or 1.0", a placeholder written before there was a measurement. It now
    # means "does not get a vote", which is the thing a caller actually needs to
    # know. seaborn's not_barrel_exported at 0.995 was excluded under the old
    # definition and is a constant by any useful reading.
    diag["uninformative"] = sorted(set(_SIGNAL_NAMES) - informative)
    diag["degeneracy_cutoff"] = cutoff
    # The ceiling, not a score: with fewer than three voting signals nothing in
    # this repository can reach 1.0, and a caller comparing against the default
    # min_confidence=0.5 deserves to see why the result set is small or empty.
    diag["max_achievable_confidence"] = round(len(informative) / 3.0, 2)
    if entry_point_count == 0:
        diag["degraded"] = {"unreachable_file": "no_entry_points_detected"}
    return diag


#: Symbol kinds whose lines are code that runs outside every function: the
#: initialiser of a constant, a variable, a class field or a property.
#: Derived from the vocabulary (#806), so a state kind added there is read here.
_DATA_KINDS = frozenset(STATE_KINDS)

#: A quoted string that opens and closes on one line. A word inside one is
#: data, not a name the code uses: without this a constant holding the text
#: ``"name"`` counted as a use of every method called ``name``.
_DOUBLE_QUOTED_RE = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"")
_QUOTED_RE = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"" + "|" + r"'(?:[^'\\\n]|\\.)*'")

#: Languages that write a string between single quotes. Anywhere else an
#: apostrophe is something shorter (a Rust lifetime, a character), and pairing
#: two of them removed the code between: ``static ONE: &'static str =
#: helper('a');`` lost its call (review of L-169).
_SINGLE_QUOTE_STRING_LANGUAGES = frozenset({
    "python", "javascript", "typescript", "tsx", "vue", "ruby", "php", "perl",
    "lua", "luau", "dart", "bash", "groovy", "gdscript", "r", "sql",
})


def _without_declared_name(line: str, declared: str) -> str:
    """*line* with the first occurrence of *declared* blanked.

    The line that declares ``size`` names it once as the thing declared. Left
    in, every field would count as a use of the method that shares its name.
    Once only: in ``static int size = size();`` the second is a call.

    A name that is not a plain identifier (Ruby's ``@@after_reload``) is
    blanked where it is written, sigil and all; left in, the word behind
    the sigil counted as a use of the method ``after_reload``.
    """
    if not declared:
        return line
    if _IDENT_ONLY_RE.fullmatch(declared) is None:
        at = line.find(declared)
        if at < 0:
            return line
        return line[:at] + " " * len(declared) + line[at + len(declared):]
    for match in _IDENT_RE.finditer(line):
        if match.group(0) == declared:
            return line[: match.start()] + " " * len(declared) + line[match.end():]
    return line


def _sweep_module_level_callers(
    index,
    store,
    owner: str,
    name: str,
    rev: dict[str, list[str]],
    callee_has_caller: set[str],
    symbols=None,
) -> None:
    """Add callers that the per-symbol AST call index cannot record.

    ``CodeIndex.get_callers_by_name`` is built by walking each symbol's
    ``call_references``, so code at module level — which belongs to no symbol —
    contributes nothing to it. This scans exactly that residue: each file's text
    with every symbol body removed. That is both the precise blind spot and a
    small fraction of the file, and it is consulted only for symbols the AST
    pass left without a caller, so a repo whose callers all resolve reads no
    file contents at all.

    Mutates ``callee_has_caller`` in place. Only ever adds. *symbols* is what is
    asked about, every symbol of the index when not given.

    ⚠ Disclosed residual: this matches text, not calls, so a bare mention of the
    name at module level (an ``__all__`` entry, a decorator line above a
    neighbouring symbol) counts as a caller. That is the same trade the text
    fallback below already makes, and it is the safe direction for a signal that
    feeds deletion decisions: it can only fail to report something dead, never
    report live code as dead.
    """
    symbols_by_file = build_symbols_by_file(index)
    # Per-file: the identifier tokens appearing in that file's module-level
    # residue. Tokenising each file ONCE and testing set membership per symbol
    # is what makes this affordable — scanning the residue text per (symbol,
    # importer) pair instead measured 12x slower than the whole tool on a
    # 9.7k-function repo. This mirrors ``called_names_by_file`` above, which is
    # the same shape for the same reason.
    residue_names_cache: dict[str, set[str]] = {}
    # Names that are not plain identifiers (Clojure ``do-thing``, Ruby and
    # Elixir ``valid?``) cannot be found by token equality, so they keep
    # ``_word_match``'s word-boundary rule, asked of a
    # ``FileWords`` built ONCE per residue (LEDGER L-152). ⚠ They are not
    # rare: in Clojure nearly every function name is one, and searching the
    # whole residue text per symbol and caller file was quadratic in a file's
    # functions. Never call ``_word_match`` on a residue text per symbol; only
    # a name that holds a line separator still does, because it can match
    # across two residue lines and no per-line index can answer that.
    residue_text_cache: dict[str, str] = {}
    residue_words_cache: dict[str, Optional[FileWords]] = {}

    def _residue_text(file_path: str) -> str:
        """File text with the lines of every function, class and type removed.

        ⚠⚠ **A constant, variable, field or property is a symbol too, and its
        lines ARE code outside every function.** This removed every symbol's
        span, so the right-hand side of ``ONE = helper()`` and every entry of
        a dispatch table was taken out before it was read, and the function
        named only there read ``no_callers`` (LEDGER L-169). Such a symbol's
        lines are kept, with the name it declares blanked (``int size;`` is
        not a use of ``size()``) and what sits inside quotes on one line
        removed (``NAMES = ["helper"]`` is text).
        ⚠⚠ **A line a function or method holds stays out, whichever symbol
        wraps the other**: a function's body is answered by its call
        references, and read as text every property access in it
        (``b.size``) would count as a call of a method of that name. A
        constant whose initialiser holds functions that are symbols is read
        around them. Without this, zod lost 107 more rows.
        ⚠ A function written inside an initialiser that is NOT a symbol of
        its own (an unnamed handler in a table) is read as text with it:
        nothing else reads it, and what it calls was invisible before.
        ⚠ Text, not calls. On a kept line every word counts: a comment, a
        string over several lines, an object key or keyword argument
        (``size: 3``, ``name=1``), a type annotation (``x: T | None``) and a
        modifier (``readonly``) all name a function or a class of that
        word. Toward not reporting; LEDGER L-170.
        """
        if file_path in residue_text_cache:
            return residue_text_cache[file_path]
        content = store.get_file_content(owner, name, file_path) or ""
        if content:
            lines = content.splitlines()
            covered = bytearray(len(lines))
            in_function = bytearray(len(lines))
            data: list[tuple[int, int, str]] = []
            quoted = _DOUBLE_QUOTED_RE
            for s in symbols_by_file.get(file_path, ()):
                line = s.get("line") or 0
                if line <= 0:
                    # No usable span, so there is nothing to subtract. Leaving
                    # the body in can only find a caller, never invent one's
                    # absence — the safe direction, same call as .226's
                    # unspanned_files.
                    continue
                start = max(0, line - 1)
                end = min(len(lines), s.get("end_line") or line)
                if end <= start:
                    continue
                kind = s.get("kind")
                if (s.get("language") or "") in _SINGLE_QUOTE_STRING_LANGUAGES:
                    quoted = _QUOTED_RE
                if kind in _DATA_KINDS:
                    data.append((start, end, s.get("name") or ""))
                    continue
                covered[start:end] = b"\x01" * (end - start)
                if kind in ("function", "method"):
                    in_function[start:end] = b"\x01" * (end - start)
            # Second, so a field's lines come back out of its class's span.
            kept = bytearray(len(lines))
            for start, end, declared in data:
                if not in_function[start]:
                    lines[start] = _without_declared_name(lines[start], declared)
                for i in range(start, end):
                    if not kept[i] and not in_function[i]:
                        kept[i] = 1
                        covered[i] = 0
                        lines[i] = quoted.sub(" ", lines[i])
            content = "\n".join(
                ln for i, ln in enumerate(lines) if not covered[i]
            )
        residue_text_cache[file_path] = content
        return content

    def _residue_words(file_path: str) -> Optional[FileWords]:
        if file_path not in residue_words_cache:
            text = _residue_text(file_path)
            residue_words_cache[file_path] = FileWords(text) if text else None
        return residue_words_cache[file_path]

    def _residue_names(file_path: str) -> set[str]:
        if file_path not in residue_names_cache:
            residue_names_cache[file_path] = set(
                _IDENT_RE.findall(_residue_text(file_path))
            )
        return residue_names_cache[file_path]

    for sym in (index.symbols if symbols is None else symbols):
        if sym.get("kind") not in ("function", "method"):
            continue
        sid = sym.get("id", "")
        if not sid or sid in callee_has_caller:
            continue
        sym_file = sym.get("file", "")
        sym_name = sym.get("name", "")
        if not sym_name or not sym_file:
            continue
        plain = _IDENT_ONLY_RE.fullmatch(sym_name) is not None
        # Own file first (module-level call in the same module), then any file
        # that imports it. Same search order as the fast path above.
        for caller_file in (sym_file, *rev.get(sym_file, ())):
            if plain:
                hit = sym_name in _residue_names(caller_file)
            elif FileWords.spans_lines(sym_name):
                residue = _residue_text(caller_file)
                hit = bool(residue) and _word_match(residue, sym_name)
            else:
                words = _residue_words(caller_file)
                hit = words is not None and words.names(sym_name)
            if hit:
                callee_has_caller.add(sid)
                break


def _sweep_callers_that_need_no_import(index, callee_has_caller: set[str]) -> None:
    """Count a call recorded in another file where the language needs no import.

    The pass before this looks for a caller in the function's own file and in
    the files that import it. In Go, Java, C#, Rust and Ruby (the list is
    ``LANGUAGES_CALLED_ACROSS_FILES_WITHOUT_AN_IMPORT``) a file calls another
    with no import between them, so a function called only from
    another file of its package, namespace or crate read ``no_callers``: live
    code reported dead (LEDGER L-165; ripgrep's ``args``, called as
    ``crate::flags::config::args()``).

    A function of such a language that is still without a caller has one when
    any symbol of the SAME language records a call of its name.

    ⚠ By name, across the whole index: a function nothing calls is not
    reported when another function of that language shares its name and is
    called (``new``, ``get``, ``default``). Toward not reporting, the direction
    this signal errs in everywhere (the call-graph-only mode has always matched
    by name across every file and language).
    ⚠ Only ADDS callers, and only for functions the pass before left without
    one, so nothing here can report a function that pass cleared.
    """
    called: dict[str, set[str]] = {}
    for sym in index.symbols:
        references = sym.get("call_references")
        if references:
            language = sym.get("language") or ""
            if language in LANGUAGES_CALLED_ACROSS_FILES_WITHOUT_AN_IMPORT:
                called.setdefault(language, set()).update(references)
    if not called:
        return
    for sym in index.symbols:
        if sym.get("kind") not in ("function", "method"):
            continue
        sym_id = sym.get("id", "")
        if not sym_id or sym_id in callee_has_caller:
            continue
        if sym.get("name", "") in called.get(sym.get("language") or "", ()):
            callee_has_caller.add(sym_id)


def _files_read_as_text(index) -> set[str]:
    """Files whose language records no call reference anywhere in this index.

    ``call_references`` are written per symbol by the parsers that know their
    language's call nodes. Most do not (Clojure, Elixir, Erlang, C, Bash),
    and two that are declared to (PHP and Dart, in
    ``parser/extractor._CALL_NODE_TYPES``) wrote none when measured (LEDGER
    L-155, L-158). For a file of such a language the
    call index is not evidence of anything: an absent caller there means
    nobody looked.

    ⚠⚠ **Ask the INDEX, never a list of languages.** A table of languages
    that record calls is a second copy of the parsers' behaviour, and the
    one that exists is already wrong about PHP. A language is trusted here
    when some symbol of it carries a call reference in this index.

    ⚠ So a language that CAN record calls, in a repository whose files of it
    hold none, is read as text too. That is the safe direction: text only
    ever finds a caller, it never removes one.

    A file can hold symbols of more than one language (a template with a
    script block). It is read as text only when NONE of them records calls.
    """
    recording: set[str] = set()
    languages_of: dict[str, set[str]] = {}
    for sym in index.symbols:
        sym_file = sym.get("file")
        if not sym_file:
            continue
        language = sym.get("language") or ""
        languages_of.setdefault(sym_file, set()).add(language)
        if sym.get("call_references"):
            recording.add(language)
    return {f for f, languages in languages_of.items() if not (languages & recording)}


def _text_reading_disclosure(index, files_read: set[str]) -> dict:
    """What the response says when some files' text was read for a caller.

    Text is weaker evidence than a call reference: a mention in a comment
    counts. Disclosed, because the caller cannot otherwise tell which rows
    rest on it. A count and the languages, never the file list (a list
    shipped unbounded on 1.x cannot be bounded later).

    ⚠ *files_read* is what ``_TextCallers`` READ, never the files that were
    eligible. The eligible set holds every data file with a symbol
    (``pyproject.toml``, ``package.json``), which nothing asks about: the
    first version counted those and put this key on a repository where no
    text had been read (review of L-155).
    """
    return {
        "files": len(files_read),
        "languages": sorted({
            s["language"] for s in index.symbols if s.get("file") in files_read and s.get("language")
        }),
    }


class _TextCallers:
    """Does a file's text name a symbol outside the symbol's own lines?

    The evidence for a caller where no call reference exists: the whole
    index (the text fallback), or the files of a language that records none
    (L-155). A symbol has a caller in its own file if its name appears as a
    word outside its line span, and in another file if it appears anywhere.
    Matching the symbol's own lines would make every function "call" itself.

    ⚠⚠ **One file's text is read, split and tokenised ONCE** (LEDGER L-150).
    The loop this replaced split the symbol's file, joined every line outside
    the symbol and searched the result, once per symbol; the ledger row
    carries the timings. Never answer a per-symbol question here with a pass
    over the file's text; ask ``FileWords``.
    ``tests/test_dead_code_text_fallback_reads_a_file_once.py`` counts the
    work and holds the old loop as the oracle.

    A name that holds a line separator can match across two lines, which a
    per-line index cannot answer. No parser here is known to produce one;
    if one arrives it takes the old whole-text search for that one question.
    """

    def __init__(self, store, owner: str, name: str) -> None:
        self._store, self._owner, self._name = store, owner, name
        self._words: dict[str, Optional[FileWords]] = {}
        # Files whose text was consulted for a caller; what the response discloses.
        self.files_read: set[str] = set()

    def _file_words(self, file_path: str) -> Optional[FileWords]:
        if file_path not in self._words:
            content = self._store.get_file_content(self._owner, self._name, file_path) or ""
            self._words[file_path] = FileWords(content) if content else None
            if content:
                self.files_read.add(file_path)
        return self._words[file_path]

    def names_caller(self, sym: dict, caller_file: str) -> bool:
        """True if *caller_file*'s text names *sym* outside *sym*'s own lines."""
        sym_name = sym.get("name", "")
        own = caller_file == sym.get("file", "")
        sym_line = sym.get("line", 0)
        sym_end_line = sym.get("end_line", sym_line)
        if own and not sym_line:
            return False
        if FileWords.spans_lines(sym_name):
            content = self._store.get_file_content(self._owner, self._name, caller_file) or ""
            if content:
                self.files_read.add(caller_file)
            if content and own:
                lines = content.splitlines()
                start_idx = max(0, sym_line - 1)
                end_idx = min(len(lines), sym_end_line)
                content = "\n".join(lines[:start_idx] + lines[end_idx:])
            return bool(content) and _word_match(content, sym_name)
        words = self._file_words(caller_file)
        if words is None:
            return False
        if not own:
            return words.names(sym_name)
        start_idx = max(0, sym_line - 1)
        end_idx = min(words.line_count, sym_end_line)
        return words.names_outside(sym_name, start_idx, end_idx)


def _text_files_that_can_call(index, text_files: set[str]) -> set[str]:
    """The text-read files worth asking for a caller in ANOTHER file.

    Those holding a symbol of a language that has a function or method
    somewhere in this index. ``text_files`` also holds every data file with
    a symbol (JSON, TOML, YAML): a function named in a ``package-lock.json``
    is not called there, and reading every data file of a repository to
    find that out is the cost with none of the evidence. Asked of the
    index, like the rule above it: no table of code languages.
    """
    with_functions = {
        s.get("language") for s in index.symbols
        if s.get("kind") in ("function", "method") and s.get("language")
    }
    return {
        s["file"] for s in index.symbols
        if s.get("file") in text_files and s.get("language") in with_functions
    }


class _CallersElsewhere:
    """Does some OTHER text-read file name a symbol? (LEDGER L-157)

    ``_TextCallers`` is asked about a symbol's own file and the files the
    import graph says import it. For the languages read as text that graph
    is often missing the edge that matters: a Clojure ``:require``, an
    Elixir ``alias``, an Erlang remote call and a Bash ``source`` gave none
    when measured, two C files that include one header are joined to the
    header and never to each other, and call-graph-only mode has no graph.
    So a function called only from another file read ``no_callers``.

    This asks every other text-read code file, import edge or not.

    ⚠⚠ **The line that DECLARES a symbol of the same name is not a call.**
    Inside each same-named symbol of the other file, the first line that
    names the name is its declaration or definition (the first, not the
    symbol's first line: a C return type can sit on the line above the
    name). Without this a C prototype in a header makes every declared
    function live, and two files that each define ``init`` call each other.
    Every other line counts, the same-named symbol's own body included: a
    facade ``(defn start [x] (impl/start x))`` written over two lines is a
    caller of ``impl``'s ``start``. ⚠ So a same-named function that
    recurses, or an Erlang function of several clauses, counts as a caller
    too (toward not reporting), and a call on the declaring line itself is
    missed (toward reporting).

    ⚠⚠ **Never one question per (symbol, file).** The answer is kept per
    NAME (``_calling_files``), so a name defined in a thousand files is
    worked out once. One map from each word to the files that hold it is
    built once, and a name made of word
    characters is a read of it. A name of two or more words (``do-thing``,
    ``ns::f``) is asked only of the files that hold its rarest adjacent
    word PAIR with the same separator, from a second map built on the
    first such question: narrowing by the rarest WORD asked every file of
    a repository about ``get-item-3-7`` when ``get``, ``item`` and the
    digits were all common, and the cost grew with files times functions.
    ⚠ The floor that is left: a name whose every pair is in many files.

    ⚠ Weaker evidence than anything above it: any file naming the word
    counts, so a function named like a common word is never reported.
    The error it removes is the worse one for a tool that feeds deletes.

    Not answered: a name with no word character (an operator) and a name
    that holds a line separator. Both read False here.
    """

    def __init__(self, index, text: _TextCallers, files: set[str]) -> None:
        self._text, self._files = text, files
        self._by_word: Optional[dict[str, list[str]]] = None
        self._by_pair: Optional[dict[tuple[str, str, str], list[str]]] = None
        self._calling: dict[str, tuple[str, ...]] = {}
        # (file, name) -> the line spans, as slices, of that file's symbols of that name
        self._same_name: dict[tuple[str, str], list[tuple[int, int]]] = {}
        for sym in index.symbols:
            sym_file, sym_name, line = sym.get("file"), sym.get("name"), sym.get("line", 0)
            if sym_file in files and sym_name and line:
                self._same_name.setdefault((sym_file, sym_name), []).append(
                    (line - 1, sym.get("end_line", line))
                )

    def _files_by_word(self) -> dict[str, list[str]]:
        if self._by_word is None:
            by_word: dict[str, list[str]] = {}
            for file_path in sorted(self._files):
                words = self._text._file_words(file_path)
                if words is None:
                    continue
                for word in words.words():
                    by_word.setdefault(word, []).append(file_path)
            self._by_word = by_word
        return self._by_word

    def _files_by_pair(self) -> dict[tuple[str, str, str], list[str]]:
        if self._by_pair is None:
            by_pair: dict[tuple[str, str, str], list[str]] = {}
            for file_path in sorted(self._files):
                words = self._text._file_words(file_path)
                if words is None:
                    continue
                for pair in words.word_pairs():
                    by_pair.setdefault(pair, []).append(file_path)
            self._by_pair = by_pair
        return self._by_pair

    def _calling_files(self, name: str) -> tuple[str, ...]:
        """Up to two files that name *name* beyond their own declarations of it.

        The answer for a symbol is whether one of these is not its own file,
        so two are enough, and it depends on the NAME only: it is computed
        once per name. ⚠ The first version walked the files per SYMBOL, and
        seven callback names defined in each of 1,998 files and called by
        nothing cost files times functions questions (review of L-157).
        """
        found = self._calling.get(name)
        if found is not None:
            return found
        if is_one_word(name):
            rarest = self._files_by_word().get(name, [])
        else:
            name_pairs = adjacent_word_pairs(name)
            if name_pairs:
                by_pair = self._files_by_pair()
                rarest = min((by_pair.get(p, []) for p in name_pairs), key=len)
            else:
                name_words = words_of(name)
                by_word = self._files_by_word()
                rarest = min((by_word.get(w, []) for w in name_words), key=len) if name_words else []
        calling: list[str] = []
        for file_path in rarest:
            words = self._text._file_words(file_path)
            if words is None:
                continue
            spans = self._same_name.get((file_path, name))
            if not spans:
                if not words.names(name):
                    continue
            else:
                named = words.lines_naming(name)
                declaring: set[int] = set()
                for start, end in spans:
                    k = bisect_left(named, start)
                    if k < len(named) and named[k] < end:
                        declaring.add(named[k])
                if len(named) <= len(declaring):
                    continue
            calling.append(file_path)
            if len(calling) == 2:
                break
        found = self._calling[name] = tuple(calling)
        return found

    def names_caller(self, sym: dict) -> bool:
        """True if a text-read code file other than *sym*'s own names it beyond a declaration of it."""
        sym_name, own = sym.get("name", ""), sym.get("file", "")
        if not sym_name or FileWords.spans_lines(sym_name):
            return False
        return any(file_path != own for file_path in self._calling_files(sym_name))


def _sweep_other_text_files(index, text: _TextCallers, text_files: set[str], callee_has_caller: set[str]) -> None:
    """Add the text-read functions some OTHER text-read file names (LEDGER L-157).

    Runs after the own-file and importer evidence, and only for a function
    those left without a caller. It only ever ADDS callers.
    """
    files = _text_files_that_can_call(index, text_files)
    if not files:
        return
    elsewhere = _CallersElsewhere(index, text, files)
    for sym in index.symbols:
        if sym.get("kind") not in ("function", "method"):
            continue
        if sym.get("id") in callee_has_caller or sym.get("file") not in text_files:
            continue
        if elsewhere.names_caller(sym):
            callee_has_caller.add(sym["id"])


def _text_fallback_callers(
    index, store, owner: str, name: str, rev: dict[str, list[str]],
    text: Optional["_TextCallers"] = None,
    symbols=None,
) -> set[str]:
    """Ids of the functions and methods some text names outside their own lines.

    The answer when the index holds no call reference at all: every file is
    read through ``_TextCallers``, the symbol's own file first, then each file
    that imports it. Pass *text* to learn afterwards which files were read, and
    *symbols* to ask about something other than every symbol of the index.

    ⚠ A file listed as its OWN importer is read as the symbol's own file (the
    symbol's lines excluded) both times; the loop this replaced read it
    whole the second time, which made every function there its own caller.
    ``_build_reverse_adjacency`` drops self-edges, so the tool never asks.
    """
    callee_has_caller: set[str] = set()
    if text is None:
        text = _TextCallers(store, owner, name)
    for sym in (index.symbols if symbols is None else symbols):
        if sym.get("kind") not in ("function", "method"):
            continue
        sym_file = sym.get("file", "")
        sym_name = sym.get("name", "")
        if not sym_name or not sym_file:
            continue
        for caller_file in (sym_file, *rev.get(sym_file, [])):
            if text.names_caller(sym, caller_file):
                callee_has_caller.add(sym["id"])
                break
    return callee_has_caller


def _mark_callers_from_references(
    symbols,
    rev: dict[str, list[str]],
    called_names_by_file: dict[str, set[str]],
    text_files: set[str],
    text_callers: "_TextCallers",
    callee_has_caller: set[str],
) -> None:
    """Add each of *symbols* that a call reference names, in its own file or an importer.

    A caller file whose language records no call in this index is read as
    text instead. Mutates ``callee_has_caller`` in place. Only ever adds.
    """
    for sym in symbols:
        if sym.get("kind") not in ("function", "method"):
            continue
        sym_file = sym.get("file", "")
        sym_name = sym.get("name", "")
        if not sym_name or not sym_file:
            continue
        # Check the symbol's own file (intra-file calls) and any importing
        # file. Same-file callers were missed pre-1.80.10, which produced
        # false positives in nested-root TS monorepos where a function is
        # defined and called within the same module.
        search_files = (sym_file, *rev.get(sym_file, ()))
        for caller_file in search_files:
            if caller_file in text_files:
                # ⚠ Which path ran used to be one repository-wide test,
                # so one Python call reference anywhere left a Clojure
                # function called inside another with no evidence at
                # all, reported `no_callers` (#409's flip, one level
                # down). The choice is per caller file now.
                hit = text_callers.names_caller(sym, caller_file)
            else:
                hit = sym_name in called_names_by_file.get(caller_file, set())
            if hit:
                callee_has_caller.add(sym["id"])
                break


def get_dead_code_v2(
    repo: str,
    min_confidence: float = 0.5,
    include_tests: bool = False,
    max_results: int = 100,
    file_pattern: Optional[str] = None,
    storage_path: Optional[str] = None,
    degeneracy_cutoff: Optional[float] = None,
    entry_point_patterns: Optional[list[str]] = None,
) -> dict:
    """Find likely-dead functions and methods using three independent signals.

    Args:
        repo:           Repo identifier.
        min_confidence: Minimum confidence threshold (0.0–1.0).
                        Default 0.5 means at least 2 of 3 signals must fire.
        include_tests:  When False (default), test files are treated as
                        reachable and skipped.
        max_results:    Cap on returned symbols (default 100). Pre-1.80.7
                        the response was unbounded; on large libraries this
                        could exceed 8k tokens per call. ``_meta.truncated``
                        + ``_meta.total_matches`` flag when capped. Use 0
                        for unlimited.
        file_pattern:   Optional glob (e.g. ``"src/**"``, ``"*.py"``) — only
                        analyse symbols whose file matches. Smaller scope
                        means smaller, faster, more actionable results.
        storage_path:   Optional index storage path override.

    Returns:
        ``{dead_symbols, total_analysed, min_confidence, timing_ms}``
        Each entry in ``dead_symbols``:
        ``{id, name, kind, file, line, confidence, signals}``
    """
    import fnmatch
    t0 = time.monotonic()
    try:
        cutoff = _resolve_cutoff(degeneracy_cutoff)
    except ValueError as e:
        return {"error": str(e)}
    try:
        owner, name = _resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}
    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)

    if index is None:
        return {"error": f"No index found for {repo!r}. Run index_folder first."}

    # v1.108.275 (#446). Computed BEFORE the call-graph-only branch, because that
    # branch returns early and shares the hazard.
    _unmatched = unmatched_patterns(entry_point_patterns, index.source_files)

    if not index.imports:
        # 1.80.9+: when there's no import graph (single-file libs like
        # pre-bundled lodash 4.x, monolithic IIFEs, etc.), fall through
        # to call-graph-only mode rather than erroring out. Reports
        # symbols whose names appear nowhere in any indexed function's
        # call_references.
        _fallback = _call_graph_only_dead_code(
            index, owner, name, t0,
            store=store,
            include_tests=include_tests,
            max_results=max_results,
            file_pattern=file_pattern,
        )
        # ⚠⚠ v1.108.275 (#446). This exit never received `entry_point_patterns` and
        # still does not use them — call-graph-only mode has no notion of a live
        # ROOT FILE, so there is nothing for a path pattern to seed. That is
        # defensible; **silently accepting a parameter and ignoring it is not.**
        # A caller who passes patterns here and reads an ordinary answer has no way
        # to learn they did nothing.
        if entry_point_patterns:
            _fallback["entry_point_patterns_ignored"] = True
            _fallback["entry_point_patterns_warning"] = (
                "entry_point_patterns was supplied but this repository has no import "
                "graph, so analysis fell back to call-graph-only mode, which has no "
                "file-level entry-point concept. The patterns were not applied."
                + (
                    f" Separately, {len(_unmatched)} of them match no indexed file "
                    f"at all: {', '.join(_unmatched[:5])}."
                    if _unmatched else ""
                )
            )
        return _fallback

    source_files = frozenset(index.source_files)
    alias_map = getattr(index, "alias_map", {}) or {}
    psr4_map = getattr(index, "psr4_map", None)
    rev = _build_reverse_adjacency(index.imports, source_files, alias_map, psr4_map)
    forward = _build_forward_adjacency(index.imports, source_files, alias_map, psr4_map)

    # Pre-compute reachable files from entry points (Signal 1 input).
    # Two heuristics: (a) classic filename match (app.py, main.py, etc.);
    # (b) any file declared as `main`/`module`/`exports`/`bin` in a
    # ``package.json`` (issue: sverklo bench v1 — Express has no
    # filename-style entry point).
    pkg_entries = _package_json_entries(index, store, owner, name)

    # (c) caller-declared roots. Both warnings below tell the caller to pass
    # entry_point_patterns; before v1.108.271 this function had no such
    # parameter at any layer, so that advice could not be followed and the
    # degenerate path offered a remedy that did not exist (#436). Matching
    # reuses find_dead_code's `_matches_any_pattern` rather than a second
    # implementation, so the two tools cannot disagree about what a pattern
    # means.
    declared_entries: set[str] = set()
    if entry_point_patterns:
        declared_entries = {
            f for f in index.source_files
            if _matches_any_pattern(f, entry_point_patterns)
        }

    # (d) framework-declared roots (#562). ⚠⚠ Without these, a Next.js repo
    # detected NO entry point, so signal 1 (`unreachable_file`) fired on every
    # symbol, all three signals were ruled non-discriminating, and this tool
    # returned `dead_symbols: []` -- which a downstream consumer reads as
    # "no dead code" rather than "could not tell". The advice in the warning
    # ("pass entry_point_patterns") asked the user to hand-type a list the
    # index was already carrying, detected at index time and never read.
    #
    # ⚠ Ordered AFTER the caller's own patterns and unioned, never substituted:
    # an explicit `entry_point_patterns` argument is a stronger statement than
    # our detection and must not be narrowed by it.
    fw_spec = entry_point_spec(index)
    fw_entries = {f for f in index.source_files if fw_spec.matches(f)}
    declared_entries |= fw_entries
    declared_entries |= {f for f in index.source_files if is_host_invoked(f)}

    # (e) runtime-discovered packages (#569). Fixed at BOTH call sites rather
    # than only the reported one: signal 1 here is `unreachable_file`, computed
    # from the same import graph, so a package enumerated by
    # `pkgutil.iter_modules` votes its modules dead in this tool for the same
    # invisible-edge reason. ⚠ It only ever ADDS roots, so its effect on a
    # published grade is one direction: fewer false dead symbols.
    dynamic_entries = {
        f for f in discover_dynamic_packages(index, store, owner, name).roots
        if f in source_files
    }

    extra_entries = pkg_entries | declared_entries | dynamic_entries
    entry_point_count = (
        sum(1 for f in index.source_files
            if _is_entry_point(f) or f in declared_entries)
        + len(pkg_entries - declared_entries)
    )
    reachable_files = _reachable_from_entry_points(
        list(index.source_files), rev, forward, extra_entries=extra_entries
    )

    # (f) (LEDGER L-70) a file a package- or prefix-scoped dynamic import can
    # load (#876's recorded sites, the rule `get_blast_radius` reads), and
    # every file it imports, MAY be loaded. That makes signal 1
    # (`unreachable_file`) UNDECIDED there, not false. ⚠⚠ It is not an entry
    # point: a root is skipped whole, which silenced `no_callers` and
    # `not_barrel_exported` too, and a loader at the repo root reaches every
    # file, so nothing was analysed and a withheld health grade published.
    # Undecided symbols leave signal 1's denominator, so its rate (and whether
    # it votes) is measured only where it could be decided.
    boundary = DynamicBoundary(index.imports)
    boundary_roots = {f for f in source_files if boundary.reaching(f)} if boundary else set()
    # Forward only: what a maybe-loaded file imports may load with it; what
    # imports it is a separate question the graph already answers.
    def _with_what_they_import(roots: set) -> set:
        loaded = set(roots)
        queue = deque(roots)
        while queue:
            for imported in forward.get(queue.popleft(), []):
                if imported not in loaded:
                    loaded.add(imported)
                    queue.append(imported)
        return loaded

    dynamic_loaded = _with_what_they_import(boundary_roots)
    # .NET controls and build items load through edges the index does not
    # carry. Undecided for the same reason-shape as (f), but it is a DIFFERENT
    # reason, and the response names it apart: these files were reported under
    # `dynamic_import_boundary` with no site, in a repository holding no dynamic
    # import (review of #1012).
    unseen_roots = {f: kind for f in source_files if (kind := unseen_consumer(f))}
    maybe_loaded = dynamic_loaded | _with_what_they_import(set(unseen_roots))

    # Pre-compute barrel exports (Signal 3 input). Recursively follows CJS
    # ``module.exports = require(...)`` / ESM ``export * from`` so that
    # symbols re-exported through index.js are not flagged as dead.
    barrel_names = _barrel_exports(
        index, store, owner, name, source_files, alias_map, psr4_map
    )

    # Pre-compute call graph: for each symbol, who calls it? (Signal 2 input)
    # Use AST call_references when available (O(N)), fall back to text heuristic.
    get_callers = getattr(index, "get_callers_by_name", None)
    callers_by_name = get_callers() if get_callers else None
    callee_has_caller: set[str] = set()
    # Files the call index says nothing about (L-155): their language records
    # no call reference in this index, so they are read as text. Empty for a
    # repository whose every language records calls, and then nothing below
    # differs from the call-reference path alone.
    text_files: set[str] = set()
    text_callers = _TextCallers(store, owner, name)
    class_rows = constructors_as_their_classes(index.symbols, rev)
    if callers_by_name:
        text_files = _files_read_as_text(index)
        # Fast path: use pre-computed AST call_references index
        # Any symbol whose name appears as a value in callers_by_name has at least one caller
        called_names_by_file: dict[str, set[str]] = {}
        for (caller_file, called_name) in callers_by_name:
            called_names_by_file.setdefault(caller_file, set()).add(called_name)
        _mark_callers_from_references(
            index.symbols, rev, called_names_by_file, text_files, text_callers, callee_has_caller
        )
        # Sweep what the AST index structurally cannot see (#409).
        # ``call_references`` are recorded PER SYMBOL, so a call written at
        # module level belongs to no symbol and never enters ``callers_by_name``
        # — CLI wiring, route tables, framework registration and ``__main__``
        # blocks all live there. Worse, which path ran was decided by
        # ``if callers_by_name:``, a repo-wide property: a tree where no symbol
        # called another fell through to the text heuristic below and got these
        # right, and adding a single intra-file call anywhere flipped the whole
        # repo onto a path that could not. Both now run. The fast path keeps its
        # precision because the sweep only ever ADDS callers, and only for
        # symbols the fast path left unresolved.
        _sweep_module_level_callers(
            index, store, owner, name, rev, callee_has_caller
        )
        # A language whose files call each other with no import (L-165).
        _sweep_callers_that_need_no_import(index, callee_has_caller)
        # A constructor named with a fixed word is called by creating its
        # class, never by that word (L-167). Asked the two questions above,
        # of the class.
        _mark_callers_from_references(
            class_rows, rev, called_names_by_file, text_files, text_callers, callee_has_caller
        )
        _sweep_module_level_callers(
            index, store, owner, name, rev, callee_has_caller, symbols=class_rows
        )
    else:
        # Fallback: text heuristic, one pass per file (LEDGER L-150).
        callee_has_caller = _text_fallback_callers(index, store, owner, name, rev, text_callers)
        callee_has_caller |= _text_fallback_callers(
            index, store, owner, name, rev, text_callers, symbols=class_rows
        )
        # No language records a call here, so every file with a symbol is read as text.
        text_files = _files_read_as_text(index)
    # A text-read function still without a caller may be called from a file
    # the import graph does not join to it (L-157).
    _sweep_other_text_files(index, text_callers, text_files, callee_has_caller)

    dead_symbols: list[dict] = []
    seen_ids: set[str] = set()
    # Signal instrumentation (#408). Counted over every ANALYSED symbol, not
    # just the returned ones, so the rates do not move when a caller changes
    # ``min_confidence`` — an instrument whose reading depends on the threshold
    # it is meant to inform is not an instrument.
    analysed_count = 0
    fire_counts: dict[str, int] = {s: 0 for s in _SIGNAL_NAMES}
    cofire_counts: dict[str, int] = {}
    undecided_counts: dict[str, int] = {}
    undecided_files: set[str] = set()
    undecided_symbols_in: dict[str, int] = {}
    # Pass 1 collects; pass 2 scores (v1.108.231). Which signals are worth a
    # vote is a property of the whole repository, so it cannot be known until
    # every symbol has been seen. Scoring inline was what made a signal that
    # fires on everything still worth a full third.
    scored: list[tuple[dict, list[str], list[str]]] = []

    for sym in index.symbols:
        sid = sym.get("id", "")
        if not sid:
            continue
        if sym.get("kind") not in ("function", "method"):
            continue

        sym_file = sym.get("file", "")
        sym_name = sym.get("name", "")

        # Skip entry-point files entirely (filename heuristic + package.json
        # main fields).
        if _is_entry_point(sym_file) or sym_file in extra_entries:
            continue

        # Skip test files unless requested
        if not include_tests and _is_test_file(sym_file):
            continue

        # ⚠ `file_pattern` is deliberately NOT applied here (v1.108.231). It is
        # the caller's view of the results, not a definition of the population
        # the signals are measured against. Filtering first made the fire rates
        # a property of the filter: scoping to a single file drove every rate to
        # 1.0, no signal discriminated, and the tool returned nothing for a
        # question it could answer perfectly well. Whether a signal separates
        # live from dead code is a fact about the codebase. The filter is
        # applied in pass 2, on what gets returned.

        # Skip symbols with entry-point decorators
        if any(ENTRY_POINT_DECORATOR_RE.search(str(d)) for d in (sym.get("decorators") or [])):
            continue

        signals: list[str] = []
        undecided: list[str] = []

        # Signal 1: File is not reachable from any entry point
        if sym_file not in reachable_files:
            if sym_file in maybe_loaded:
                undecided.append("unreachable_file")
                undecided_files.add(sym_file)
                undecided_symbols_in[sym_file] = undecided_symbols_in.get(sym_file, 0) + 1
            else:
                signals.append("unreachable_file")

        # Signal 2: No callers in the call graph
        if sid not in callee_has_caller:
            signals.append("no_callers")

        # Signal 3: Not mentioned in any barrel/init export
        if sym_name not in barrel_names:
            signals.append("not_barrel_exported")

        analysed_count += 1
        for s in signals:
            fire_counts[s] += 1
        for s in undecided:
            undecided_counts[s] = undecided_counts.get(s, 0) + 1
        for i, a in enumerate(signals):
            for b in signals[i + 1:]:
                cofire_counts[f"{a}+{b}"] = cofire_counts.get(f"{a}+{b}", 0) + 1

        scored.append((sym, signals, undecided))

    # Pass 2: only signals that discriminate on THIS repository get a vote.
    informative = _informative_signals(analysed_count, fire_counts, cutoff, undecided_counts)
    for sym, signals, undecided in scored:
        sid = sym["id"]
        if sid in seen_ids:
            continue
        # Caller's scope filter, applied to the OUTPUT only. See pass 1.
        if file_pattern and not fnmatch.fnmatch(sym.get("file", ""), file_pattern):
            continue
        counted = [s for s in signals if s in informative]
        # ⚠ Denominator stays 3, deliberately. Dividing by the number of
        # informative signals would scale a lone survivor back up to 1.0 and
        # report maximum confidence off one signal — the opposite of the fix.
        # Holding the denominator is what makes the ceiling fall instead, so a
        # repository where nothing discriminates returns nothing through the
        # caller's existing ``min_confidence`` rather than through a second,
        # invisible suppression rule.
        confidence = len(counted) / 3.0
        if confidence >= min_confidence:
            seen_ids.add(sid)
            entry = {
                "id": sid,
                "name": sym.get("name", ""),
                "kind": sym.get("kind", ""),
                "file": sym.get("file", ""),
                "line": sym.get("line", 0),
                "confidence": round(confidence, 2),
                "signals": signals,
            }
            if len(counted) != len(signals):
                # Which of the fired signals actually carried the verdict. Only
                # emitted when it differs, so the common case costs no tokens.
                entry["counted_signals"] = counted
            if undecided:
                entry["undecided_signals"] = undecided
            dead_symbols.append(entry)

    dead_symbols.sort(key=lambda x: (-x["confidence"], x["file"], x["line"]))

    total_matches = len(dead_symbols)
    truncated = False
    if max_results and max_results > 0 and total_matches > max_results:
        dead_symbols = dead_symbols[:max_results]
        truncated = True

    timing_ms = round((time.monotonic() - t0) * 1000, 1)
    result: dict = {
        "repo": f"{owner}/{name}",
        "dead_symbols": dead_symbols,
        "total_analysed": sum(
            1 for s in index.symbols
            if s.get("kind") in ("function", "method")
        ),
        "min_confidence": min_confidence,
        "_meta": {
            "timing_ms": timing_ms,
            "methodology": "multi_signal",
            "confidence_level": "medium",
            "total_matches": total_matches,
            "truncated": truncated,
            "signal_diagnostics": _signal_diagnostics(
                analysed_count, fire_counts, cofire_counts, entry_point_count, cutoff,
                undecided_counts,
            ),
        },
    }
    if text_callers.files_read:
        result["callers_read_as_text"] = _text_reading_disclosure(index, text_callers.files_read)
    by_dynamic_import = undecided_files & dynamic_loaded
    by_unseen_consumer = undecided_files - dynamic_loaded
    if by_unseen_consumer:
        kinds: dict[str, int] = {}
        for f in by_unseen_consumer:
            kind = unseen_roots.get(f, "imported_by_one")
            kinds[kind] = kinds.get(kind, 0) + 1
        result["unseen_consumer_boundary"] = {
            "files": len(by_unseen_consumer),
            "symbols": sum(undecided_symbols_in[f] for f in by_unseen_consumer),
            "files_by_kind": dict(sorted(kinds.items())),
            "note": (
                "unreachable_file was not decided for these files: a .NET project "
                "file may consume them (build_consumed), or a user control or master "
                "page may be loaded by path at runtime (runtime_loadable); "
                "imported_by_one counts the files such a file imports. The index "
                "carries neither edge. Their other signals still vote."
            ),
        }
    if by_dynamic_import:
        sites = sorted({s for f in by_dynamic_import for s in boundary.reaching(f)})
        result["dynamic_import_boundary"] = {
            "files": len(by_dynamic_import),
            "symbols": sum(undecided_symbols_in[f] for f in by_dynamic_import),
            "sites": sites[:FILES_CAP],
            "sites_total": len(sites),
            "note": (
                "unreachable_file was not decided for these files: a package- or "
                "prefix-scoped dynamic import may load them. Their other signals "
                "still vote."
            ),
        }
    if file_pattern:
        result["_meta"]["file_pattern"] = file_pattern
    if pkg_entries:
        result["_meta"]["package_json_entries"] = sorted(pkg_entries)
    diag = result["_meta"]["signal_diagnostics"]
    excluded = diag.get("uninformative") or []
    if excluded:
        # An empty or unexpectedly small result set is a finding, not a
        # malfunction, and it has to say so or it reads as one.
        ceiling = diag.get("max_achievable_confidence", 0.0)
        result["signal_warning"] = (
            f"{len(excluded)} of 3 signals do not discriminate on this repository and "
            f"were not counted: {', '.join(excluded)}. Each fired on more than "
            f"{int(cutoff * 100)}% or fewer than "
            f"{int((1 - cutoff) * 100)}% of analysed symbols, so it "
            f"accuses everything or nothing. Maximum reachable confidence here is "
            f"{ceiling}"
            + (
                f", which is below your min_confidence of {min_confidence} — nothing "
                "can be returned. This is a limit of the evidence, not a claim that "
                "the repository has no dead code."
                if ceiling < min_confidence else "."
            )
            + (
                " Pass entry_point_patterns to declare framework-specific roots, or "
                "lower min_confidence to see the weaker verdicts."
                if not entry_point_patterns else
                " entry_point_patterns was supplied and did not rescue this; the "
                "remaining lever is lowering min_confidence."
            )
        )
    if entry_point_count == 0:
        result["framework_warning"] = (
            "No standard entry points detected (e.g. main.py, app.py, __main__.py). "
            "Signal 1 (unreachable_file) fires for every symbol, inflating dead code counts. "
            "Pass entry_point_patterns to identify framework-specific roots "
            "(e.g. handler functions for AWS Lambda, route modules for FastAPI)."
            if not entry_point_patterns else
            "entry_point_patterns was supplied but matched no indexed file, so "
            "Signal 1 still fires for every symbol. Check the patterns against "
            "repo-relative paths."
        )

    # ⚠⚠ v1.108.275 (#446). The message above was RIGHT and almost never fired: it
    # is gated on `entry_point_count == 0`, so any repo carrying one ordinary
    # main.py/app.py made the count non-zero and the caller heard nothing about
    # patterns that matched nothing. **A correct warning behind the wrong gate reads
    # as "no problem found".** This one is ungated and names the offenders.
    if _unmatched:
        result["entry_point_patterns_unmatched"] = _unmatched
        result["entry_point_patterns_warning"] = (
            f"{len(_unmatched)} of {len(entry_point_patterns)} entry_point_patterns "
            f"matched no indexed file, so they declared no roots: "
            f"{', '.join(_unmatched[:5])}"
            + (f" (+{len(_unmatched) - 5} more)" if len(_unmatched) > 5 else "")
            + ". Patterns are matched with fnmatch against repo-relative paths: "
            "brace alternation ({ts,js}) is NOT expanded, and ** does not match "
            "zero directories (plugins/**/*.ts misses plugins/auth.ts). "
            "List each extension separately and add the flat form."
        )
    return result


def _call_graph_only_dead_code(
    index,
    owner: str,
    name: str,
    t0: float,
    include_tests: bool = False,
    max_results: int = 100,
    file_pattern: Optional[str] = None,
    store=None,
) -> dict:
    """Fallback dead-code detection when ``index.imports`` is empty.

    Single-file libraries (pre-bundled lodash 4.x, monolithic IIFEs,
    minified-then-indexed bundles) have no inter-file imports — the
    main 3-signal analyzer can't run. This mode falls back to the
    call-graph signal: a function whose name appears nowhere in any
    indexed function's ``call_references`` is a dead candidate.

    The result is intentionally lower-confidence than the 3-signal
    output; ``_meta.mode = "call_graph_only"`` flags this so callers
    can interpret. Each returned symbol has a single signal
    (``no_callers``); ``confidence`` is fixed at 0.5 to reflect the
    weaker evidence (cf. 3-signal where each signal is worth 1/3).

    A file whose language records no call reference in this index is not
    covered by that graph at all, so its own text is read for a caller
    (LEDGER L-155, the same rule as the 3-signal path), whether or not any
    other language records one, and then every other text-read code file
    is asked (L-157): this mode has no import graph to say which files
    could call it.
    """
    import fnmatch

    get_callers = getattr(index, "get_callers_by_name", None)
    if not get_callers:
        return {
            "repo": f"{owner}/{name}",
            "dead_symbols": [],
            "total_analysed": 0,
            "_meta": {
                "mode": "unavailable",
                "warning": (
                    "No import data and no call-references index. "
                    "Re-index with jcodemunch-mcp >= 1.78.0 (INDEX_VERSION 8) "
                    "to enable AST call-reference indexing."
                ),
                "timing_ms": round((time.monotonic() - t0) * 1000, 1),
            },
        }

    callers_by_name = get_callers() or {}
    # Names that have at least one caller in the indexed call graph.
    called_names: set[str] = {ref for (_caller_file, ref) in callers_by_name.keys()}
    # ⚠ No `if callers_by_name` in front of this. The first version had one,
    # which kept the repository-wide flip in this mode: a Clojure file was
    # read as text only when some OTHER language recorded a call (review of
    # L-155). With no call reference at all every file is read as text.
    text_files = _files_read_as_text(index) if store is not None else set()
    text_callers = _TextCallers(store, owner, name)
    # This mode has no import graph, so another file is reached only this way (L-157).
    elsewhere = _CallersElsewhere(index, text_callers, _text_files_that_can_call(index, text_files))

    # A fixed-word constructor is called by creating its class (L-167). This
    # mode has no import graph to bound the join, so a subclass counts in its
    # base's own file only. ⚠ Script files that share globals with no import
    # extend across files; a subclass created there leaves its base's
    # constructor reported (L-169).
    class_rows: dict[str, list[dict]] = {}
    for row in constructors_as_their_classes(index.symbols, {}):
        class_rows.setdefault(row["id"], []).append(row)

    dead_symbols: list[dict] = []
    seen: set[str] = set()
    total_analysed = 0

    for sym in index.symbols:
        if sym.get("kind") not in ("function", "method"):
            continue
        sid = sym.get("id", "")
        if not sid or sid in seen:
            continue
        sym_file = sym.get("file", "")
        sym_name = sym.get("name", "")
        if not sym_name or not sym_file:
            continue
        if not include_tests and _is_test_file(sym_file):
            continue
        if file_pattern and not fnmatch.fnmatch(sym_file, file_pattern):
            continue
        # Skip entry-point decorated symbols (Flask routes, click commands etc.)
        if any(ENTRY_POINT_DECORATOR_RE.search(str(d))
               for d in (sym.get("decorators") or [])):
            continue
        total_analysed += 1
        if sym_name in called_names:
            continue
        if sym_file in text_files and (
            text_callers.names_caller(sym, sym_file) or elsewhere.names_caller(sym)
        ):
            continue
        if any(
            row["name"] in called_names
            or (
                row["file"] in text_files
                and (text_callers.names_caller(row, row["file"]) or elsewhere.names_caller(row))
            )
            for row in class_rows.get(sid, ())
        ):
            continue
        seen.add(sid)
        dead_symbols.append({
            "id": sid,
            "name": sym_name,
            "kind": sym.get("kind", ""),
            "file": sym_file,
            "line": sym.get("line", 0),
            "confidence": 0.5,
            "signals": ["no_callers"],
        })

    dead_symbols.sort(key=lambda x: (x["file"], x["line"]))

    total_matches = len(dead_symbols)
    truncated = False
    if max_results and max_results > 0 and total_matches > max_results:
        dead_symbols = dead_symbols[:max_results]
        truncated = True

    result = {
        "repo": f"{owner}/{name}",
        "dead_symbols": dead_symbols,
        "total_analysed": total_analysed,
        "_meta": {
            "mode": "call_graph_only",
            "warning": (
                "Import graph is empty (single-file project, monolithic "
                "bundle, or pre-tree-shaken library). Falling back to "
                "call-graph-only analysis: a function with no callers "
                "elsewhere in the indexed call graph is treated as a dead "
                "candidate. Confidence is fixed at 0.5 to reflect the "
                "single-signal nature; expect more false positives than "
                "the standard 3-signal mode."
            ),
            "timing_ms": round((time.monotonic() - t0) * 1000, 1),
            "total_matches": total_matches,
            "truncated": truncated,
        },
    }
    if text_callers.files_read:
        result["callers_read_as_text"] = _text_reading_disclosure(index, text_callers.files_read)
    return result
