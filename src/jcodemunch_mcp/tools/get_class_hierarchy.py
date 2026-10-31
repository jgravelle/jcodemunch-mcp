"""Traverse class inheritance chains from indexed symbol signatures."""

import re
import time
from collections import deque
from typing import Optional

from ..storage import IndexStore
from ._utils import index_status_to_tool_error, resolve_repo

# Patterns to extract base class / interface names from signatures
_EXTENDS_RE = re.compile(
    r'\bextends\s+([\w$][\w$,\s]*?)(?=\s+implements|\s*[{(<]|$)', re.IGNORECASE
)
_IMPLEMENTS_RE = re.compile(
    r'\bimplements\s+([\w$][\w$,\s]*?)(?=\s*[{(<]|$)', re.IGNORECASE
)
# Python style: class Foo(Bar, Baz)
_PAREN_BASES_RE = re.compile(r'\bclass\s+\w[\w$]*\s*\(([^)]+)\)')

# Scala / Dart: `extends B with M` names two bases.
_WITH_SPLIT_RE = re.compile(r',|\s+with\s+')

# A declaration head: one keyword, or two (`record struct`, `enum class`),
# then the name, which may be qualified (`N::S`, `a.b.C`).
_DECL_HEAD_RE = re.compile(
    r'\b(?P<kw>(?:class|struct|interface|record|object|protocol|trait|enum|extension)'
    r'(?:\s+(?:class|struct))?)\s+'
    # C++ attributes that sit before the name: `alignas(16)`, `__declspec(...)`, `[[...]]`.
    r'(?:(?:alignas|__declspec|__attribute__)\s*\((?:[^()]|\([^()]*\))*\)\s*|\[\[.*?\]\]\s*)*'
    r'[\w$.]+(?:::[\w$.]+)*'
)
# Comments a signature can carry: `// ...`, `/* ... */`, and `# ...` to end of line.
# A double-quoted string is matched first and kept, so a `#` or `//` inside an
# annotation argument (`@SerialName("#tag")`, `[Url("http://a")]`) is not a comment.
_COMMENT_RE = re.compile(r'("(?:\\.|[^"\\\n])*")|/\*.*?\*/|//[^\n]*|#[^\n]*', re.DOTALL)


def _strip_comments(text: str) -> str:
    """Replace each comment with a space; strings are left as written."""
    return _COMMENT_RE.sub(lambda m: m.group(1) or " ", text)
# Languages whose `:` after the head opens a block, never a base list.
_BLOCK_COLON_LANGUAGES = frozenset({"python"})
# Words that may sit between a declaration's name and its `:` base list:
# C++ `final`, Kotlin `private constructor` / `@Inject constructor`.
_HEAD_SKIP_WORDS = frozenset({
    "final", "sealed", "abstract", "open", "partial", "constructor",
    "public", "private", "protected", "internal",
})
_WORD_RE = re.compile(r'[A-Za-z_]\w*')
_ANNOTATION_RE = re.compile(r'@[\w.]+')
_WHERE_RE = re.compile(r'\bwhere\b')
# Ruby: class Foo < Bar   (one base; `< T>` generics never end the line)
_RUBY_BASE_RE = re.compile(r'\bclass\s+[\w:]+\s+<\s+([A-Z][\w:]*)\s*(?:$|\n|;)')
# Words before a base name that are not part of it (C++ access, Swift/C# modifiers).
_BASE_PREFIX_WORDS = frozenset({"public", "private", "protected", "internal", "virtual"})
_BASE_NAME_RE = re.compile(r'(?:::)?[A-Za-z_$][\w$.]*(?:::[A-Za-z_$][\w$.]*)*')
# After an `enum` head, the `:` names an underlying or raw type, not a base:
# C# `enum E : byte`, C++ `enum class E : uint8_t`, Swift `enum E: Int`.
# Kotlin's `enum class E : I` names an interface, which stays a base.
_ENUM_RAW_TYPES = frozenset({
    "Int", "Int8", "Int16", "Int32", "Int64", "UInt", "UInt8", "UInt16", "UInt32",
    "UInt64", "String", "Character", "Double", "Float", "Float16", "Float80",
    "Byte", "SByte", "Short", "UShort", "Long", "ULong", "Bool",
    "CGFloat", "Float32", "Float64",
})


def _is_arrow(text: str, j: int) -> bool:
    """True when text[j] is the `>` of `=>` or `->`, which closes no group."""
    return text[j] == ">" and j > 0 and text[j - 1] in "=-"


def _skip_group(text: str, i: int, open_ch: str, close_ch: str) -> int:
    """Index just past the group opened at text[i], or -1 if unbalanced."""
    depth = 0
    for j in range(i, len(text)):
        if text[j] == open_ch:
            depth += 1
        elif text[j] == close_ch and not _is_arrow(text, j):
            depth -= 1
            if depth == 0:
                return j + 1
    return -1


def _strip_type_args(text: str) -> str:
    """`B<int>` -> `B`, `B[T]` -> `B`: drop balanced <...> and [...] groups.

    `extends` / `implements` stop at `<`, so a generic first base hid the
    `with` mixin after it (`extends B<int> with M`). An unbalanced `<` is kept.
    """
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "<[":
            end = _skip_group(text, i, ch, ">" if ch == "<" else "]")
            if end > 0:
                i = end
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _split_top_level(text: str, seps: str) -> list[str]:
    """Split on any char in `seps` outside <...>, (...) and [...]."""
    parts, depth, start = [], 0, 0
    for j, ch in enumerate(text):
        if ch in "<([":
            depth += 1
        elif ch in ">)]" and not _is_arrow(text, j):
            depth = max(depth - 1, 0)
        elif depth == 0 and ch in seps:
            parts.append(text[start:j])
            start = j + 1
    parts.append(text[start:])
    return parts


def _colon_bases(signature: str, language_known: bool = False) -> list[str]:
    """`Head : A, B<T>` - C#, Kotlin, Swift, C++ and Rust supertraits (#1284).

    After the name, generics, a primary constructor, annotations, modifiers
    (`final`, `private constructor`) and line breaks are skipped, so a `:`
    inside them (`class C(val x: Int)`, `Box<T : Comparable<T>>`) is never
    read as a base. The list ends at the body, a `where` clause, `;` or `=`,
    and may run across lines. Comments are removed first, so a comment line
    never supplies a base. When the language is unknown, a `:` with nothing
    after it on its line is taken as a block colon (Python's `class X:`).
    When it is known, and not Python, the list may start on the next line
    (C# `class W :` then `Base, IFoo`).
    """
    signature = _strip_comments(signature)
    n = len(signature)
    for head in _DECL_HEAD_RE.finditer(signature):
        i = head.end()
        while i < n:
            ch = signature[i]
            if ch in " \t\r\n":
                i += 1
                continue
            if ch in "<(":
                i = _skip_group(signature, i, ch, ">" if ch == "<" else ")")
                if i < 0:
                    return []
                continue
            if ch == "@":
                m = _ANNOTATION_RE.match(signature, i)
                if m:
                    i = m.end()
                    continue
            m = _WORD_RE.match(signature, i)
            if m and m.group(0) in _HEAD_SKIP_WORDS:
                i = m.end()
                continue
            break
        if i >= n or signature[i] != ":" or signature.startswith("::", i):
            continue
        rest = signature[i + 1:]
        if not language_known and not rest.split("\n", 1)[0].strip():
            # Language unknown and nothing after the colon on its line: a block colon.
            return []
        cut = len(rest)
        depth = 0
        for j, ch in enumerate(rest):
            if ch in "<([":
                depth += 1
            elif ch in ">)]" and not _is_arrow(rest, j):
                depth = max(depth - 1, 0)
            elif depth == 0 and (ch in "{;=" or _WHERE_RE.match(rest, j)):
                cut = j
                break
        is_enum = head.group("kw").startswith("enum")
        bases: list[str] = []
        for part in _split_top_level(rest[:cut], ",+"):
            words = part.split()
            while words and words[0] in _BASE_PREFIX_WORDS:
                words.pop(0)
            m = _BASE_NAME_RE.match(" ".join(words))
            if not m:
                continue
            name = m.group(0).lstrip(":")
            if is_enum:
                last = re.split(r'::|\.', name)[-1]
                if last[:1].islower() or last in _ENUM_RAW_TYPES:
                    continue
            bases.append(name)
        return bases
    return []


def _parse_bases(signature: str, language: Optional[str] = None) -> list[str]:
    """Extract base class / interface names from a class signature.

    ``language`` is the symbol's language when the caller knows it. A Python
    signature never takes the colon branch, because Python's `:` opens the
    class body, and the stored signature can carry the comment lines under it.
    """
    bases: list[str] = []
    flat = _strip_type_args(signature)

    # extends Foo, Bar   (Scala/Dart: extends Foo with Bar)
    m = _EXTENDS_RE.search(flat)
    if m:
        bases += [n.strip() for n in _WITH_SPLIT_RE.split(m.group(1)) if n.strip()]

    # implements Foo, Bar
    m = _IMPLEMENTS_RE.search(flat)
    if m:
        bases += [n.strip() for n in m.group(1).split(",") if n.strip()]

    # class Foo(Bar, Baz)  - Python
    if not bases:
        m = _PAREN_BASES_RE.search(signature)
        if m:
            candidates = [n.strip() for n in m.group(1).split(",") if n.strip()]
            # Filter out obviously non-class args (e.g. Generic[T], *args)
            bases += [c for c in candidates if re.match(r'^[A-Z][\w.]*$', c)]

    # class Foo : Bar, Baz<T>  - C#, Kotlin, Swift, C++, Rust (#1284)
    if not bases and (language or "").lower() not in _BLOCK_COLON_LANGUAGES:
        bases += _colon_bases(signature, language_known=bool(language))

    # class Foo < Bar  - Ruby; a trailing `# :nodoc:` must not hide the base
    if not bases:
        m = _RUBY_BASE_RE.search(_strip_comments(signature))
        if m:
            bases.append(m.group(1))

    return bases


def _build_class_maps(symbols: list[dict]) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """
    Returns:
        class_by_name: {name -> symbol}  (first match wins for duplicates)
        children_of:   {name -> [child_names]}
    """
    class_syms = [s for s in symbols if s.get("kind") in ("class", "type")]
    class_by_name: dict[str, dict] = {}
    for sym in class_syms:
        name = sym.get("name", "")
        if name and name not in class_by_name:
            class_by_name[name] = sym

    children_of: dict[str, list[str]] = {}
    for sym in class_syms:
        for base in _parse_bases(sym.get("signature", ""), sym.get("language")):
            if base in class_by_name:
                children_of.setdefault(base, []).append(sym["name"])

    return class_by_name, children_of


def get_class_hierarchy(
    repo: str,
    class_name: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Get the full inheritance hierarchy for a class.

    Traverses both upward (ancestors via ``extends``/``implements``) and
    downward (subclasses / implementors) from the named class.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        class_name: Name of the class to analyse.
        storage_path: Custom storage path.

    Returns:
        Dict with ``ancestors`` list (base classes, ordered nearest-first),
        ``descendants`` list (subclasses, BFS order), and the target class info.
    """
    start = time.perf_counter()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    class_by_name, children_of = _build_class_maps(index.symbols)

    if class_name not in class_by_name:
        # Case-insensitive fallback
        lower = class_name.lower()
        match = next((n for n in class_by_name if n.lower() == lower), None)
        if not match:
            return {"error": f"Class '{class_name}' not found in index. Only 'class' and 'type' kinds are searched."}
        class_name = match

    target = class_by_name[class_name]

    def _fmt(sym: dict) -> dict:
        return {
            "name": sym["name"],
            "file": sym.get("file", ""),
            "line": sym.get("line", 0),
            "signature": sym.get("signature", ""),
        }

    # Ancestors: walk up via _parse_bases, BFS
    ancestors: list[dict] = []
    visited_up: set[str] = {class_name}
    queue: deque = deque(_parse_bases(target.get("signature", ""), target.get("language")))
    while queue:
        base_name = queue.popleft()
        if base_name in visited_up:
            continue
        visited_up.add(base_name)
        if base_name in class_by_name:
            sym = class_by_name[base_name]
            ancestors.append(_fmt(sym))
            queue.extend(_parse_bases(sym.get("signature", ""), sym.get("language")))
        else:
            # External base (not in index) — record name only
            ancestors.append({"name": base_name, "file": "(external)", "line": 0, "signature": ""})

    # Descendants: walk down via children_of, BFS
    descendants: list[dict] = []
    visited_down: set[str] = {class_name}
    queue = deque(children_of.get(class_name, []))
    while queue:
        child_name = queue.popleft()
        if child_name in visited_down:
            continue
        visited_down.add(child_name)
        if child_name in class_by_name:
            sym = class_by_name[child_name]
            descendants.append(_fmt(sym))
            queue.extend(children_of.get(child_name, []))

    elapsed = (time.perf_counter() - start) * 1000
    return {
        "repo": f"{owner}/{name}",
        "class": _fmt(target),
        "ancestor_count": len(ancestors),
        "descendant_count": len(descendants),
        "ancestors": ancestors,
        "descendants": descendants,
        "_meta": {"timing_ms": round(elapsed, 1)},
    }
