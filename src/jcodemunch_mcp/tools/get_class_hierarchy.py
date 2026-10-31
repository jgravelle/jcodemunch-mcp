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

# A declaration head: keyword + name. What follows the name (generics,
# a primary constructor) is skipped before looking for a `:` base list.
_DECL_HEAD_RE = re.compile(
    r'\b(?:class|struct|interface|record|object|protocol|trait|enum|extension)\s+[\w$.]+'
)
# Ruby: class Foo < Bar   (one base; `< T>` generics never end the line)
_RUBY_BASE_RE = re.compile(r'\bclass\s+[\w:]+\s+<\s+([A-Z][\w:]*)\s*(?:$|\n|;)')
# Words before a base name that are not part of it (C++ access, Swift/C# modifiers).
_BASE_PREFIX_WORDS = frozenset({"public", "private", "protected", "internal", "virtual"})
_BASE_NAME_RE = re.compile(r'[A-Za-z_$][\w$.]*(?:::[A-Za-z_$][\w$.]*)*')


def _skip_group(text: str, i: int, open_ch: str, close_ch: str) -> int:
    """Index just past the group opened at text[i], or -1 if unbalanced."""
    depth = 0
    for j in range(i, len(text)):
        if text[j] == open_ch:
            depth += 1
        elif text[j] == close_ch:
            depth -= 1
            if depth == 0:
                return j + 1
    return -1


def _split_top_level(text: str, seps: str) -> list[str]:
    """Split on any char in `seps` outside <...>, (...) and [...]."""
    parts, depth, start = [], 0, 0
    for j, ch in enumerate(text):
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth = max(depth - 1, 0)
        elif depth == 0 and ch in seps:
            parts.append(text[start:j])
            start = j + 1
    parts.append(text[start:])
    return parts


def _colon_bases(signature: str) -> list[str]:
    """`Head : A, B<T>` — C#, Kotlin, Swift, C++ and Rust supertraits (#1284).

    The list ends at the body, a `where` clause, `;` or `=`. A `:` inside the
    name's generics or primary constructor (`class C(val x: Int)`, `Box<T :
    Comparable<T>>`) is skipped with the group, so it is never read as a base.
    """
    for head in _DECL_HEAD_RE.finditer(signature):
        i = head.end()
        while True:
            while i < len(signature) and signature[i] in " \t":
                i += 1
            if i < len(signature) and signature[i] in "<(":
                close = ">" if signature[i] == "<" else ")"
                i = _skip_group(signature, i, signature[i], close)
                if i < 0:
                    return []
                continue
            break
        if i >= len(signature) or signature[i] != ":" or signature.startswith("::", i):
            continue
        rest = signature[i + 1:]
        # The base list stops at the first top-level terminator.
        cut = len(rest)
        depth = 0
        for j, ch in enumerate(rest):
            if ch in "<([":
                depth += 1
            elif ch in ">)]":
                depth = max(depth - 1, 0)
            elif depth == 0 and (ch in "{;=\n" or rest.startswith(" where ", j)):
                cut = j
                break
        bases: list[str] = []
        for part in _split_top_level(rest[:cut], ",+"):
            words = part.split()
            while words and words[0] in _BASE_PREFIX_WORDS:
                words.pop(0)
            m = _BASE_NAME_RE.match(" ".join(words))
            if m:
                bases.append(m.group(0))
        return bases
    return []


def _parse_bases(signature: str) -> list[str]:
    """Extract base class / interface names from a class signature."""
    bases: list[str] = []

    # extends Foo, Bar   (Scala/Dart: extends Foo with Bar)
    m = _EXTENDS_RE.search(signature)
    if m:
        bases += [n.strip() for n in _WITH_SPLIT_RE.split(m.group(1)) if n.strip()]

    # implements Foo, Bar
    m = _IMPLEMENTS_RE.search(signature)
    if m:
        bases += [n.strip() for n in m.group(1).split(",") if n.strip()]

    # class Foo : Bar, Baz<T>  — C#, Kotlin, Swift, C++, Rust (#1284)
    if not bases:
        bases += _colon_bases(signature)

    # class Foo(Bar, Baz)  — Python
    if not bases:
        m = _PAREN_BASES_RE.search(signature)
        if m:
            candidates = [n.strip() for n in m.group(1).split(",") if n.strip()]
            # Filter out obviously non-class args (e.g. Generic[T], *args)
            bases += [c for c in candidates if re.match(r'^[A-Z][\w.]*$', c)]

    # class Foo < Bar  — Ruby
    if not bases:
        m = _RUBY_BASE_RE.search(signature)
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
        for base in _parse_bases(sym.get("signature", "")):
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
    queue: deque = deque(_parse_bases(target.get("signature", "")))
    while queue:
        base_name = queue.popleft()
        if base_name in visited_up:
            continue
        visited_up.add(base_name)
        if base_name in class_by_name:
            sym = class_by_name[base_name]
            ancestors.append(_fmt(sym))
            queue.extend(_parse_bases(sym.get("signature", "")))
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
