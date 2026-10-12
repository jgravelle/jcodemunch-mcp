"""Shared AST-derived call-graph computation.

Strategy
--------
call_references (v8+): Store call sites in the index. When available,
use _callers_by_name (caller lookup) and sym.call_references (callee lookup)
for precise AST-derived call graphs.

Fallback (v7 and earlier): No call-site data is stored in the index. Callers
and callees are derived at query time using text heuristics:

Callers (who calls symbol X?):
  1. Find files that import X's defining file (import graph, same as blast radius).
  2. Within each importer, check which indexed symbols' source bodies mention
     X's name as a word token.

Callees (what does symbol X call?):
  1. Extract X's source body (by line range from file content).
  2. Find files that X's file imports (import graph).
  3. Within each imported file, check which indexed symbols' names appear in
     X's body.

Fallback heuristics are approximate (no type resolution, no dynamic dispatch
awareness), consistent with the rest of jCodemunch's AST-level analysis.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import deque
from typing import TYPE_CHECKING, Optional

# THE word rule lives in a leaf every layer can import; never rebuild it here.
from ..word_rule import word_pattern

if TYPE_CHECKING:
    from ..storage import IndexStore
    from ..storage.index_store import CodeIndex


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _word_match(content: str, name: str) -> bool:
    """Return True if *name* appears as a word token in *content*."""
    return bool(re.search(word_pattern(name), content))


def _word_starts(content: str, name: str) -> list[int]:
    """Offsets in *content* where *name* starts as a word token, in one scan.

    Occurrences that overlap an earlier one are not listed; they share a
    character with it, so a caller that maps offsets to lines loses nothing.
    """
    return [m.start() for m in re.finditer(word_pattern(name), content)]


_WORD_RE = re.compile(r"\w+")
_WORD_ONLY_RE = re.compile(r"\w+")
# Every character ``str.splitlines`` cuts on. A name holding one can match
# across two lines of the text, which a per-line index cannot see.
_LINE_SEP_RE = re.compile("[\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")


def words_of(text: str) -> list[str]:
    """Each ``\\w+`` run of *text*, in order."""
    return _WORD_RE.findall(text)


def is_one_word(name: str) -> bool:
    """True if *name* is made of word characters only (a plain identifier)."""
    return _WORD_ONLY_RE.fullmatch(name) is not None


def adjacent_word_pairs(text: str) -> list[tuple[str, str, str]]:
    """``(word, separator, next word)`` for each two adjacent ``\\w+`` runs of *text*."""
    pairs: list[tuple[str, str, str]] = []
    previous, previous_end = None, 0
    for match in _WORD_RE.finditer(text):
        if previous is not None:
            pairs.append((previous, text[previous_end:match.start()], match.group()))
        previous, previous_end = match.group(), match.end()
    return pairs


class FileWords:
    """Which lines of one file name what, from ONE pass over its text.

    For a caller that asks ``_word_match`` about many names against the same
    file (LEDGER L-150: one question per function, each a whole-file search).
    ``lines_naming(name)`` is the ascending list of line indices where
    ``_word_match(line, name)`` holds. Every line separator is a non-word
    character, so for a name that holds none that is the same answer a search
    over any subset of the lines joined by ``"\\n"`` gives.

    ⚠ **Precondition: the name holds no line separator.** ``spans_lines(name)``
    says whether it does; such a name can match across two lines, so its
    caller must search the text itself. ``lines_naming`` raises on one
    rather than answer "no line".

    A name made only of word characters is a dictionary read. Any other name
    (``do-thing``, ``ns::f``, ``$x``, ``save!``) is searched only on the lines
    that hold its rarest word, because each ``\\w+`` run inside a matched name
    is a whole word of the line. When asking those lines would cost more than
    reading the whole text (their characters plus ``_ASK_COST`` each, against
    the text's characters), and for a name with no word character (an
    operator), the text is searched once for that name instead. Answers are
    kept per name, so two hundred overloads of one name cost one lookup.

    ``names`` asks only whether a line exists and stops at the first one, by
    either route; ``lines_naming`` lists them all. A caller that needs
    existence must ask ``names``: listing every line of a name that is on
    every line costs a full scan where the first match would have done.

    ⚠ **A search of the text is the floor, not zero.** N distinct compound
    names whose every word is on most lines cost N searches of the text,
    which is what asking ``_word_match`` once per name costs.

    ⚠ Holds the file's lines and one list entry per distinct word per line:
    several times the text. Build it for a file asked about more than once.
    """

    __slots__ = ("_lines", "_by_word", "_by_name", "_named", "_chars", "_text", "_line_starts")

    # What asking one line costs beyond reading its characters, in characters
    # of a scan (a substring test and, on a hit, one ``_word_match`` call).
    _ASK_COST = 16

    def __init__(self, content: str) -> None:
        self._lines = content.splitlines()
        by_word: dict[str, list[int]] = {}
        for i, line in enumerate(self._lines):
            for word in set(_WORD_RE.findall(line)):
                by_word.setdefault(word, []).append(i)
        self._by_word = by_word
        self._by_name: dict[str, list[int]] = {}
        self._named: dict[str, bool] = {}
        self._chars = sum(len(line) for line in self._lines) + len(self._lines)
        self._text: Optional[str] = None
        self._line_starts: list[int] = []

    def _joined(self) -> str:
        if self._text is None:
            starts, offset = [], 0
            for line in self._lines:
                starts.append(offset)
                offset += len(line) + 1
            self._line_starts = starts
            self._text = "\n".join(self._lines)
        return self._text

    def _scan(self, name: str) -> list[int]:
        """Lines naming *name*, from one scan of the text joined by newlines."""
        text = self._joined()
        found: list[int] = []
        for offset in _word_starts(text, name):
            line = bisect_right(self._line_starts, offset) - 1
            if not found or found[-1] != line:
                found.append(line)
        return found

    def _candidates(self, name: str) -> Optional[list[int]]:
        """The lines worth asking about a compound *name*, or None to search the text.

        None when *name* has no word to narrow by, and when asking the lines
        that hold its rarest word would cost more than reading the text.
        """
        words = _WORD_RE.findall(name)
        if not words:
            return None
        rarest = min((self._by_word.get(w, []) for w in words), key=len)
        lines = self._lines
        asking = self._ASK_COST * len(rarest)
        if asking > self._chars:
            return None
        asking += sum(len(lines[i]) for i in rarest)
        return None if asking > self._chars else rarest

    @property
    def line_count(self) -> int:
        return len(self._lines)

    def words(self):
        """The distinct words of the file (each ``\\w+`` run of any line)."""
        return self._by_word.keys()

    def word_pairs(self) -> set[tuple[str, str, str]]:
        """Every ``(word, separator, next word)`` of two adjacent words on a line.

        A line that names ``do-thing`` holds the pair ``("do", "-", "thing")``,
        so a file without that pair cannot name it. Far more selective than
        either word. Not kept: one pass over the lines per call.
        """
        pairs: set[tuple[str, str, str]] = set()
        for line in self._lines:
            pairs.update(adjacent_word_pairs(line))
        return pairs

    def lines_naming(self, name: str) -> list[int]:
        """Ascending indices of the lines that name *name* as a word."""
        found = self._by_name.get(name)
        if found is not None:
            return found
        if _LINE_SEP_RE.search(name):
            raise ValueError("a name holding a line separator cannot be answered per line")
        if _WORD_ONLY_RE.fullmatch(name):
            found = self._by_word.get(name, [])
        else:
            candidates = self._candidates(name)
            if candidates is None:
                found = self._scan(name)
            else:
                lines = self._lines
                found = [i for i in candidates if name in lines[i] and _word_match(lines[i], name)]
        self._by_name[name] = found
        self._named[name] = bool(found)
        return found

    @staticmethod
    def spans_lines(name: str) -> bool:
        """True if *name* holds a line separator and so cannot be asked here."""
        return _LINE_SEP_RE.search(name) is not None

    def names(self, name: str) -> bool:
        """True if any line names *name*. Stops at the first one."""
        named = self._named.get(name)
        if named is not None:
            return named
        if _LINE_SEP_RE.search(name):
            raise ValueError("a name holding a line separator cannot be answered per line")
        if _WORD_ONLY_RE.fullmatch(name):
            named = name in self._by_word
        else:
            candidates = self._candidates(name)
            if candidates is None:
                named = _word_match(self._joined(), name)
            else:
                lines = self._lines
                named = any(name in lines[i] and _word_match(lines[i], name) for i in candidates)
        self._named[name] = named
        return named

    def names_outside(self, name: str, start: int, end: int) -> bool:
        """True if a line outside ``[start, end)`` names *name*.

        The lines kept are those of ``lines[:start] + lines[end:]``, read the
        way a slice reads them: an ``end`` at or before ``start`` removes
        nothing, and a negative bound counts from the end of the file.
        """
        found = self.lines_naming(name)
        if not found:
            return False
        count = len(self._lines)
        if start < 0:
            start = max(0, count + start)
        if end < 0:
            end = max(0, count + end)
        return found[0] < start or found[-1] >= end


def _symbol_body(file_lines: list[str], sym: dict) -> str:
    """Slice *file_lines* to the lines covered by *sym* (1-indexed)."""
    line = sym.get("line", 0)
    end_line = sym.get("end_line", line)
    if not line:
        return ""
    start_idx = max(0, line - 1)
    end_idx = min(len(file_lines), end_line)
    return "\n".join(file_lines[start_idx:end_idx])


def build_symbols_by_file(index: "CodeIndex") -> dict[str, list[dict]]:
    """Build ``{file_path: [symbol_dicts]}`` from *index.symbols*."""
    result: dict[str, list[dict]] = {}
    for sym in index.symbols:
        f = sym.get("file")
        if f:
            result.setdefault(f, []).append(sym)
    return result


class _ContentCache:
    """Per-traversal memo of file content + split lines.

    A file's content is invariant during a single BFS call, but the traversal
    re-reads the same importer/source files once per visited node (callers BFS
    re-reads an importer for every target whose file it imports). Memoizing the
    storage read and the ``splitlines()`` turns that from O(nodes x files) into
    O(distinct files) without touching the matching logic, so results are
    byte-identical — only redundant I/O is removed. Threaded in as an optional
    argument so direct callers of the finders keep their original behavior.
    """

    __slots__ = ("_store", "_owner", "_repo", "_content", "_lines")

    def __init__(self, store: "IndexStore", owner: str, repo_name: str):
        self._store = store
        self._owner = owner
        self._repo = repo_name
        self._content: dict[str, Optional[str]] = {}
        self._lines: dict[str, list[str]] = {}

    def content(self, file_path: str) -> Optional[str]:
        if file_path not in self._content:
            self._content[file_path] = self._store.get_file_content(
                self._owner, self._repo, file_path
            )
        return self._content[file_path]

    def lines(self, file_path: str) -> list[str]:
        cached = self._lines.get(file_path)
        if cached is None:
            content = self.content(file_path)
            cached = content.splitlines() if content else []
            self._lines[file_path] = cached
        return cached


class _CalleeNameIndex:
    """Per-traversal memo of the callee ``name -> definitions`` map.

    ``symbols_by_file`` is invariant during one traversal, yet
    ``_callees_from_references`` rebuilt this whole-repository map on every
    visited node carrying call references: O(nodes x symbols) where O(symbols)
    would do. @rknighton measured the traversal, not the function
    (`direction="both"`, `depth=3`, session result cache invalidated per
    sample, 22 pairs): django 9.25s -> 1.26s (45,561 symbols, 244 nodes with
    references), fastapi 399ms -> 315ms, and a Q=1 express control whose 99%
    interval on the paired saving spans zero. **The gain scales with the number
    of nodes that resolve callees; a small or narrow traversal sees none.**

    Built on FIRST LOOKUP, never in ``__init__``. ``symbols_by_file`` is built
    before the direction branch in ``get_call_hierarchy``, so an eager build
    would charge a full symbol pass to ``direction="callers"``, which never
    resolves a callee.

    Entries are ``(container_path, symbol)`` where *container_path* is the
    ``symbols_by_file`` KEY, not ``symbol["file"]``. ``build_symbols_by_file``
    makes those agree, but a caller-supplied mapping need not, and resolving
    through the key is what keeps this byte-identical to the scan it replaces
    rather than merely equivalent on well-formed input.

    Threaded in as an optional argument so direct callers of the finders keep
    their original behavior, exactly like ``_ContentCache``.
    """

    __slots__ = ("_symbols_by_file", "_index")

    def __init__(self, symbols_by_file: dict[str, list[dict]]):
        self._symbols_by_file = symbols_by_file
        self._index: Optional[dict[str, list[tuple[str, dict]]]] = None

    def matches(self, symbols_by_file: dict[str, list[dict]]) -> bool:
        """Whether this memo was built over *symbols_by_file* itself.

        A cache handed down a call chain must not answer for a different
        mapping than the one the caller is resolving against. Identity, not
        equality: the point is to catch a threading mistake, not to pay for a
        deep comparison on every node.
        """
        return self._symbols_by_file is symbols_by_file

    def get(self, name: str) -> list[tuple[str, dict]]:
        if self._index is None:
            built: dict[str, list[tuple[str, dict]]] = {}
            for file_path, syms in self._symbols_by_file.items():
                for s in syms:
                    s_name = s.get("name", "")
                    if s_name:
                        built.setdefault(s_name, []).append((file_path, s))
            self._index = built
        return self._index.get(name, [])


# ---------------------------------------------------------------------------
# Direct caller / callee finders
# ---------------------------------------------------------------------------

#: Languages whose files call each other with NO import between the two files.
#: Two files of one Go package, two classes of one Java package and two C#
#: files of one namespace need none; a Rust path call
#: (``crate::flags::config::args()``) needs no ``use``; a Ruby ``require`` is
#: global to the process; Kotlin (one package), Swift (one module) and PHP
#: (one namespace, or an autoloader) are the same. For these the import graph
#: cannot say which files may call a function, so "no importer calls it" is
#: not "nothing calls it" (LEDGER L-165).
#:
#: ⚠ A property of the LANGUAGE, not of what a parser records: whether a
#: language's calls are recorded at all is asked of the index
#: (``get_dead_code_v2._files_read_as_text``), never of a list.
#: Kotlin, Swift and PHP record no call today (L-158) and are read as text; they
#: are listed so the fix for that inherits this rule.
#: ⚠⚠ **Python, JavaScript, TypeScript and Dart must stay out.** A file there calls
#: only what it imports or defines, so a same-named call in a file that does
#: not import the function is a call of another function.
LANGUAGES_CALLED_ACROSS_FILES_WITHOUT_AN_IMPORT: frozenset[str] = frozenset(
    {"go", "java", "csharp", "rust", "ruby", "kotlin", "swift", "php"}
)

#: The names a language gives a constructor, where that name is a fixed word
#: nothing ever calls: ``T()`` records a call of ``T`` and ``new K()`` a call
#: of ``K``, so a search for a caller of ``__init__`` or ``constructor`` finds
#: none however often the class is created (LEDGER L-167).
#:
#: ⚠ A language belongs here only when creating a class RECORDS a call of the
#: class's name. Ruby's ``initialize`` is a fixed word too and is left out:
#: ``T.new(1)`` records ``new``, so there is no call of ``T`` to count (L-169).
CONSTRUCTOR_WORDS: dict[str, frozenset[str]] = {
    "python": frozenset({"__init__", "__new__"}),
    "javascript": frozenset({"constructor"}),
    "typescript": frozenset({"constructor"}),
    "tsx": frozenset({"constructor"}),
}

def constructors_as_their_classes(symbols, importers: dict[str, list[str]]) -> list[dict]:
    """Each fixed-word constructor, once per class whose creation runs it.

    A row is the CLASS's symbol (its name, file and lines) carrying the
    CONSTRUCTOR's id and kind. Asking "does this function have a caller" of
    such a row asks whether the class is created, by whatever rule the asker
    applies to a function; an answer of yes is filed under the constructor.

    A class that is created runs the constructor it inherits, and a subclass's
    own constructor is taken to call up, so a constructor gets a row for its
    own class and for every descendant. *importers* maps a file to the files
    that import it (``{}`` when the index has no import graph).

    ⚠⚠ **A subclass is joined to its base by name ONLY in the base's own file
    or a file that imports it.** These languages must import a class to extend
    it, so a same-named base anywhere else is another class, and a subclass is
    never in another language than its imports reach. The first version
    joined by name across the whole index: it cleared the constructor of an
    unimported ``Base`` whenever any ``Kid(Base)`` was created, and a name
    shared by N bases and their subclasses cost N squared rows (review of
    L-167: 1.47 s to 15.21 s at 800 files).
    ⚠ A subclass that reaches its base through a re-export is therefore not
    joined. Toward reporting.
    ⚠ Bases come from ``get_class_hierarchy._parse_bases``, which reads a bare
    capitalised name; ``a.Base`` joins nothing (L-169). What follows
    ``implements`` is cut off first: implementing a class runs no constructor.
    ⚠ Two classes of one name in one file are both taken as the base, and a
    subclass constructor that never calls up still counts for its ancestors
    (JavaScript requires the call; Python does not). Toward not reporting.
    """
    from .get_class_hierarchy import _parse_bases  # noqa: PLC0415  (it imports nothing of ours)

    classes_by_id: dict[str, dict] = {}
    # (base name, file of the subclass) -> the subclasses
    children: dict[tuple[str, str], list[dict]] = {}
    for sym in symbols:
        if sym.get("kind") != "class":
            continue
        if (sym.get("language") or "") not in CONSTRUCTOR_WORDS or not sym.get("id") or not sym.get("name"):
            continue
        classes_by_id[sym["id"]] = sym
        extends = (sym.get("signature") or "").split(" implements ", 1)[0]
        for base in _parse_bases(extends):
            children.setdefault((base, sym.get("file") or ""), []).append(sym)
    if not classes_by_id:
        return []
    rows: list[dict] = []
    for sym in symbols:
        if sym.get("kind") != "method" or not sym.get("id"):
            continue
        if sym.get("name") not in CONSTRUCTOR_WORDS.get(sym.get("language") or "", ()):
            continue
        own = classes_by_id.get(sym.get("parent") or "")
        if own is None:
            continue
        seen: set[str] = set()
        pending = [own]
        while pending:
            cls = pending.pop()
            if cls["id"] in seen:
                continue
            seen.add(cls["id"])
            rows.append({**cls, "id": sym["id"], "kind": "method"})
            cls_file = cls.get("file") or ""
            for where in (cls_file, *importers.get(cls_file, ())):
                pending.extend(children.get((cls["name"], where), ()))
    return rows

def _callers_from_references(
    index: "CodeIndex",
    sym: dict,
    reverse_adj: dict[str, list[str]],
) -> list[dict]:
    """Return callers using stored call_references data (AST-based).

    Uses the _callers_by_name reverse index (lazy-built on first access).
    Keyed by (caller_file, called_name) to avoid bare-name collisions between
    different files (e.g. auth.py::process vs data.py::process).
    Only returns callers from files that import sym's file (via reverse_adj).
    """
    get_callers = getattr(index, "get_callers_by_name", None)
    callers_by_name = get_callers() if get_callers else None
    if not callers_by_name:
        return []

    sym_name: str = sym.get("name", "")
    sym_file: str = sym.get("file", "")
    if not sym_name or not sym_file:
        return []

    # Files that import sym's file + sym's own file (for same-file callers)
    importing_files = set(reverse_adj.get(sym_file, []))
    search_files = importing_files | {sym_file}

    # Look up by (file, sym_name) for each candidate file
    sym_id = sym.get("id", "")
    results: list[dict] = []

    for candidate_file in search_files:
        for cid in callers_by_name.get((candidate_file, sym_name), []):
            if cid == sym_id:
                continue  # Skip self-reference
            caller = index._symbol_index.get(cid)
            if caller:
                caller_file = caller.get("file", "")
                if caller_file and caller_file in search_files:
                    results.append({
                        "id": cid,
                        "name": caller.get("name", ""),
                        "kind": caller.get("kind", ""),
                        "file": caller_file,
                        "line": caller.get("line", 0),
                        "resolution": "ast_resolved",
                    })
    return results


def _callees_from_references(
    index: "CodeIndex",
    sym: dict,
    symbols_by_file: dict[str, list[dict]],
    callee_index: Optional["_CalleeNameIndex"] = None,
) -> list[dict]:
    """Return callees using stored call_references data (AST-based).

    Resolves the names in sym.call_references to actual symbol dicts.
    Uses a name→symbols index for O(1) lookup instead of O(N) scan.

    Pass *callee_index* (a ``_CalleeNameIndex``) to share that map across a BFS
    traversal; when omitted, it is built per call as before.
    """
    call_refs = sym.get("call_references", [])
    if not call_refs:
        return []

    if callee_index is None or not callee_index.matches(symbols_by_file):
        callee_index = _CalleeNameIndex(symbols_by_file)

    sym_name = sym.get("name", "")
    sym_file = sym.get("file", "")
    seen_ids: set[str] = set()
    results: list[dict] = []

    for called_name in call_refs:
        # Skip self-recursion
        if called_name == sym_name:
            continue
        definitions = callee_index.get(called_name)
        # Look up in the same file first (local calls). EVERY same-named
        # definition in that file is emitted — the duplicate-name behavior here
        # is deliberately asymmetric with the cross-file branch below, and
        # collapsing it to one match would look like a speedup and be a
        # regression.
        if sym_file:
            for container, s in definitions:
                if container != sym_file:
                    continue
                cid = s.get("id", "")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    results.append({
                        "id": cid,
                        "name": called_name,
                        "kind": s.get("kind", ""),
                        "file": sym_file,
                        "line": s.get("line", 0),
                        "resolution": "ast_resolved",
                    })
        # Also look in other files (imported calls) — the FIRST definition per
        # file only, in first-appearance order.
        seen_files: set[str] = set()
        for container, s in definitions:
            if container == sym_file or container in seen_files:
                continue
            seen_files.add(container)
            cid = s.get("id", "")
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                results.append({
                    "id": cid,
                    "name": called_name,
                    "kind": s.get("kind", ""),
                    "file": container,
                    "line": s.get("line", 0),
                    "resolution": "ast_inferred",
                })

    return results


def _lsp_callers(index: "CodeIndex", sym: dict, symbols_by_file: dict[str, list[dict]]) -> list[dict]:
    """Return callers from LSP-resolved edges stored in context_metadata."""
    lsp_edges = (getattr(index, "context_metadata", None) or {}).get("lsp_edges", [])
    if not lsp_edges:
        return []

    sym_name = sym.get("name", "")
    sym_file = sym.get("file", "")
    if not sym_name or not sym_file:
        return []

    results: list[dict] = []
    seen_ids: set[str] = set()
    symbol_index: dict[str, dict] = getattr(index, "_symbol_index", {})

    for edge in lsp_edges:
        # An LSP edge says: from caller_file, something called called_name,
        # and LSP resolved it to target_file:target_line.
        if edge.get("called_name") != sym_name:
            continue
        if edge.get("target_file") != sym_file:
            continue
        caller_file = edge.get("caller_file", "")
        if not caller_file:
            continue
        # Find the caller symbol in caller_file
        for candidate in symbols_by_file.get(caller_file, []):
            cid = candidate.get("id", "")
            if not cid or cid in seen_ids:
                continue
            # Check if the caller's line range encompasses the call site
            # (approximate: any symbol in that file that has this call_reference)
            call_refs = candidate.get("call_references", [])
            if sym_name in call_refs:
                seen_ids.add(cid)
                results.append({
                    "id": cid,
                    "name": candidate.get("name", ""),
                    "kind": candidate.get("kind", ""),
                    "file": caller_file,
                    "line": candidate.get("line", 0),
                    "resolution": "lsp_resolved",
                })
    return results


def _lsp_callees(index: "CodeIndex", sym: dict, symbols_by_file: dict[str, list[dict]]) -> list[dict]:
    """Return callees from LSP-resolved edges stored in context_metadata."""
    lsp_edges = (getattr(index, "context_metadata", None) or {}).get("lsp_edges", [])
    if not lsp_edges:
        return []

    sym_name = sym.get("name", "")
    sym_file = sym.get("file", "")
    if not sym_name or not sym_file:
        return []

    call_refs = sym.get("call_references", [])
    if not call_refs:
        return []

    results: list[dict] = []
    seen_ids: set[str] = set()

    for edge in lsp_edges:
        if edge.get("caller_file") != sym_file:
            continue
        called_name = edge.get("called_name", "")
        if called_name not in call_refs:
            continue
        target_file = edge.get("target_file", "")
        target_line = edge.get("target_line", 0)
        if not target_file:
            continue
        # Find the target symbol
        for candidate in symbols_by_file.get(target_file, []):
            cid = candidate.get("id", "")
            if not cid or cid in seen_ids:
                continue
            if candidate.get("name") == called_name:
                seen_ids.add(cid)
                results.append({
                    "id": cid,
                    "name": called_name,
                    "kind": candidate.get("kind", ""),
                    "file": target_file,
                    "line": candidate.get("line", 0),
                    "resolution": "lsp_resolved",
                })
                break  # Take the first match for this called_name in target_file
    return results


def _dispatch_callees(index: "CodeIndex", sym: dict, symbols_by_file: dict[str, list[dict]]) -> list[dict]:
    """Return concrete implementations for interface methods called by sym.

    If sym calls an interface method, dispatch_edges tell us which concrete
    types implement that method.  Returns those implementations with
    resolution="lsp_dispatch".
    """
    dispatch_edges = (getattr(index, "context_metadata", None) or {}).get("dispatch_edges", [])
    if not dispatch_edges:
        return []

    call_refs = sym.get("call_references", [])
    if not call_refs:
        return []

    results: list[dict] = []
    seen_ids: set[str] = set()

    for edge in dispatch_edges:
        method_name = edge.get("method_name", "")
        if method_name not in call_refs:
            continue
        impl_file = edge.get("impl_file", "")
        impl_line = edge.get("impl_line", 0)
        impl_name = edge.get("impl_name", "")
        if not impl_file:
            continue
        # Find the implementing symbol
        for candidate in symbols_by_file.get(impl_file, []):
            cid = candidate.get("id", "")
            if not cid or cid in seen_ids:
                continue
            cand_line = candidate.get("line", 0)
            cand_name = candidate.get("name", "")
            # Match by line if we have it, or by name
            if (impl_line and cand_line == impl_line) or (impl_name and cand_name == impl_name) or cand_name == method_name:
                seen_ids.add(cid)
                results.append({
                    "id": cid,
                    "name": cand_name,
                    "kind": candidate.get("kind", ""),
                    "file": impl_file,
                    "line": cand_line,
                    "resolution": "lsp_dispatch",
                    "dispatch_interface": edge.get("interface_name", ""),
                })
                break

    return results


def _dispatch_callers(index: "CodeIndex", sym: dict, symbols_by_file: dict[str, list[dict]]) -> list[dict]:
    """Return callers that invoke sym's method through an interface dispatch.

    If sym is a concrete implementation of an interface method, find callers
    that call the interface method (which dispatches to sym at runtime).
    """
    dispatch_edges = (getattr(index, "context_metadata", None) or {}).get("dispatch_edges", [])
    if not dispatch_edges:
        return []

    sym_name = sym.get("name", "")
    sym_file = sym.get("file", "")
    if not sym_name or not sym_file:
        return []

    # Find dispatch edges where sym is the implementation
    matching_interfaces: list[dict] = []
    for edge in dispatch_edges:
        if edge.get("impl_file") == sym_file and edge.get("impl_line") == sym.get("line", -1):
            matching_interfaces.append(edge)
        elif edge.get("impl_name") == sym_name and edge.get("impl_file") == sym_file:
            matching_interfaces.append(edge)

    if not matching_interfaces:
        return []

    # For each matching interface method, find callers of that interface method
    results: list[dict] = []
    seen_ids: set[str] = set()

    for edge in matching_interfaces:
        iface_method = edge.get("method_name", "")
        if not iface_method:
            continue
        # Find symbols that have this interface method in their call_references
        for file_syms in symbols_by_file.values():
            for candidate in file_syms:
                cid = candidate.get("id", "")
                if not cid or cid in seen_ids:
                    continue
                call_refs = candidate.get("call_references", [])
                if iface_method in call_refs:
                    seen_ids.add(cid)
                    results.append({
                        "id": cid,
                        "name": candidate.get("name", ""),
                        "kind": candidate.get("kind", ""),
                        "file": candidate.get("file", ""),
                        "line": candidate.get("line", 0),
                        "resolution": "lsp_dispatch",
                        "dispatch_interface": edge.get("interface_name", ""),
                    })

    return results


def find_direct_callers(
    index: "CodeIndex",
    store: "IndexStore",
    owner: str,
    repo_name: str,
    sym: dict,
    reverse_adj: dict[str, list[str]],
    symbols_by_file: dict[str, list[dict]],
    content_cache: Optional["_ContentCache"] = None,
) -> list[dict]:
    """Return symbols in importing files whose bodies mention *sym*'s name.

    Each result is ``{id, name, kind, file, line, resolution}``.

    Pass *content_cache* (a ``_ContentCache``) to share file reads across a BFS
    traversal; when omitted, files are read directly per call as before.
    """
    # Dispatch callers (interface → impl) take highest priority
    dispatch_crs = _dispatch_callers(index, sym, symbols_by_file)
    dispatch_ids: set[str] = {c["id"] for c in dispatch_crs}

    # LSP-resolved callers next
    lsp_callers = _lsp_callers(index, sym, symbols_by_file)
    lsp_callers = [c for c in lsp_callers if c["id"] not in dispatch_ids]
    lsp_ids: set[str] = {c["id"] for c in lsp_callers} | dispatch_ids

    # Try AST-derived call_references (when available and non-empty)
    get_callers = getattr(index, "get_callers_by_name", None)
    callers_by_name = get_callers() if get_callers else None
    ast_caller_ids: set[str] = set()
    ast_callers: list[dict] = []
    if callers_by_name:
        ast_callers = _callers_from_references(index, sym, reverse_adj)
        if ast_callers:
            for c in ast_callers:
                if c["id"] not in lsp_ids:
                    ast_caller_ids.add(c["id"])
            # Filter out duplicates with LSP/dispatch
            ast_callers = [c for c in ast_callers if c["id"] not in lsp_ids]

    # Always use text heuristic as fallback (handles partial AST data)
    sym_name: str = sym.get("name", "")
    sym_file: str = sym.get("file", "")
    if not sym_name or not sym_file:
        if dispatch_crs or lsp_callers or ast_caller_ids:
            return dispatch_crs + lsp_callers + ast_callers
        return []

    callers: list[dict] = []
    seen_ids: set[str] = set(ast_caller_ids) | lsp_ids

    for imp_file in reverse_adj.get(sym_file, []):
        if content_cache is not None:
            file_content = content_cache.content(imp_file)
        else:
            file_content = store.get_file_content(owner, repo_name, imp_file)
        if not file_content:
            continue
        # Fast gate: skip file if sym_name not present anywhere
        if not _word_match(file_content, sym_name):
            continue

        file_lines = (
            content_cache.lines(imp_file)
            if content_cache is not None
            else file_content.splitlines()
        )
        for candidate in symbols_by_file.get(imp_file, []):
            cid = candidate.get("id", "")
            if not cid or cid in seen_ids or not candidate.get("line"):
                continue
            body = _symbol_body(file_lines, candidate)
            if body and _word_match(body, sym_name):
                seen_ids.add(cid)
                callers.append({
                    "id": cid,
                    "name": candidate.get("name", ""),
                    "kind": candidate.get("kind", ""),
                    "file": imp_file,
                    "line": candidate.get("line", 0),
                    "resolution": "text_matched",
                })

    return dispatch_crs + lsp_callers + ast_callers + callers


def find_direct_callees(
    index: "CodeIndex",
    store: "IndexStore",
    owner: str,
    repo_name: str,
    sym: dict,
    symbols_by_file: dict[str, list[dict]],
    content_cache: Optional["_ContentCache"] = None,
    callee_index: Optional["_CalleeNameIndex"] = None,
) -> list[dict]:
    """Return symbols from imported files whose names appear in *sym*'s body.

    Each result is ``{id, name, kind, file, line, resolution}``.

    Pass *content_cache* (a ``_ContentCache``) to share file reads across a BFS
    traversal; when omitted, files are read directly per call as before.
    Pass *callee_index* (a ``_CalleeNameIndex``) to share the callee name map
    the same way; when omitted, it is built per call as before.
    """
    # Dispatch callees (interface → concrete impls) take highest priority
    dispatch_cls = _dispatch_callees(index, sym, symbols_by_file)
    dispatch_ids: set[str] = {c["id"] for c in dispatch_cls}

    # LSP-resolved callees next
    lsp_callees = _lsp_callees(index, sym, symbols_by_file)
    lsp_callees = [c for c in lsp_callees if c["id"] not in dispatch_ids]
    lsp_ids: set[str] = {c["id"] for c in lsp_callees} | dispatch_ids

    # Fast path: use AST-derived call_references if available
    call_refs = sym.get("call_references", [])
    if call_refs:
        ast_callees = _callees_from_references(
            index, sym, symbols_by_file, callee_index
        )
        # Merge: dispatch + LSP results override AST results for same symbol
        merged = list(dispatch_cls) + list(lsp_callees)
        for c in ast_callees:
            if c["id"] not in lsp_ids:
                merged.append(c)
        return merged

    # Fallback: text heuristic
    from ..parser.imports import resolve_specifier

    sym_file: str = sym.get("file", "")
    if not sym_file:
        return list(dispatch_cls) + list(lsp_callees)

    if content_cache is not None:
        file_content = content_cache.content(sym_file)
    else:
        file_content = store.get_file_content(owner, repo_name, sym_file)
    if not file_content:
        return list(dispatch_cls) + list(lsp_callees)

    file_lines = (
        content_cache.lines(sym_file)
        if content_cache is not None
        else file_content.splitlines()
    )
    sym_body = _symbol_body(file_lines, sym)
    if not sym_body:
        return list(dispatch_cls) + list(lsp_callees)

    # Resolve files that sym's file imports
    file_imports = (index.imports or {}).get(sym_file, [])
    source_files_fs = frozenset(index.source_files)
    alias_map = getattr(index, "alias_map", {}) or {}
    psr4_map = getattr(index, "psr4_map", None)

    imported_files: set[str] = set()
    for imp in file_imports:
        target = resolve_specifier(imp["specifier"], sym_file, source_files_fs, alias_map, psr4_map)
        if target and target != sym_file:
            imported_files.add(target)

    callees: list[dict] = []
    seen_ids: set[str] = set(lsp_ids)

    for imported_file in imported_files:
        for candidate in symbols_by_file.get(imported_file, []):
            cid = candidate.get("id", "")
            cname = candidate.get("name", "")
            if not cid or not cname or cid in seen_ids:
                continue
            if _word_match(sym_body, cname):
                seen_ids.add(cid)
                callees.append({
                    "id": cid,
                    "name": cname,
                    "kind": candidate.get("kind", ""),
                    "file": imported_file,
                    "line": candidate.get("line", 0),
                    "resolution": "text_matched",
                })

    return list(dispatch_cls) + list(lsp_callees) + callees


# ---------------------------------------------------------------------------
# BFS traversals
# ---------------------------------------------------------------------------

def bfs_callers(
    index: "CodeIndex",
    store: "IndexStore",
    owner: str,
    repo_name: str,
    sym: dict,
    reverse_adj: dict[str, list[str]],
    symbols_by_file: dict[str, list[dict]],
    max_depth: int,
) -> tuple[list[dict], int]:
    """BFS over callers up to *max_depth* hops.

    Returns ``(results, depth_reached)`` where each result has a ``depth`` field.
    """
    sym_id = sym.get("id", "")
    visited: set[str] = {sym_id}
    queue: deque[tuple[dict, int]] = deque()
    results: list[dict] = []
    depth_reached = 0
    symbol_index: dict[str, dict] = getattr(index, "_symbol_index", {})
    content_cache = _ContentCache(store, owner, repo_name)

    # Depth-1 callers
    for c in find_direct_callers(index, store, owner, repo_name, sym, reverse_adj, symbols_by_file, content_cache):
        if c["id"] not in visited:
            visited.add(c["id"])
            results.append({**c, "depth": 1})
            depth_reached = 1
            if max_depth > 1:
                queue.append((c, 1))

    while queue:
        curr_dict, curr_depth = queue.popleft()
        if curr_depth >= max_depth:
            continue
        curr_full = symbol_index.get(curr_dict["id"])
        if not curr_full:
            continue
        for c in find_direct_callers(index, store, owner, repo_name, curr_full, reverse_adj, symbols_by_file, content_cache):
            if c["id"] not in visited:
                visited.add(c["id"])
                new_depth = curr_depth + 1
                results.append({**c, "depth": new_depth})
                depth_reached = max(depth_reached, new_depth)
                if new_depth < max_depth:
                    queue.append((c, new_depth))

    return results, depth_reached


def bfs_callees(
    index: "CodeIndex",
    store: "IndexStore",
    owner: str,
    repo_name: str,
    sym: dict,
    symbols_by_file: dict[str, list[dict]],
    max_depth: int,
) -> tuple[list[dict], int]:
    """BFS over callees up to *max_depth* hops.

    Returns ``(results, depth_reached)`` where each result has a ``depth`` field.
    """
    sym_id = sym.get("id", "")
    visited: set[str] = {sym_id}
    queue: deque[tuple[dict, int]] = deque()
    results: list[dict] = []
    depth_reached = 0
    symbol_index: dict[str, dict] = getattr(index, "_symbol_index", {})
    content_cache = _ContentCache(store, owner, repo_name)
    callee_index = _CalleeNameIndex(symbols_by_file)

    for c in find_direct_callees(index, store, owner, repo_name, sym, symbols_by_file, content_cache, callee_index):
        if c["id"] not in visited:
            visited.add(c["id"])
            results.append({**c, "depth": 1})
            depth_reached = 1
            if max_depth > 1:
                queue.append((c, 1))

    while queue:
        curr_dict, curr_depth = queue.popleft()
        if curr_depth >= max_depth:
            continue
        curr_full = symbol_index.get(curr_dict["id"])
        if not curr_full:
            continue
        for c in find_direct_callees(index, store, owner, repo_name, curr_full, symbols_by_file, content_cache, callee_index):
            if c["id"] not in visited:
                visited.add(c["id"])
                new_depth = curr_depth + 1
                results.append({**c, "depth": new_depth})
                depth_reached = max(depth_reached, new_depth)
                if new_depth < max_depth:
                    queue.append((c, new_depth))

    return results, depth_reached
