"""The innermost function, method or class around a line, from the index.

Shared by the tools that return line-level matches (``search_text``,
``check_references``), so an agent reading a match learns where it sits
without a per-file outline call.
"""

from __future__ import annotations

from ..parser.symbols import STATE_KINDS

# Symbol kinds that hold a value rather than a body of code (declared state,
# from the one list of them) and imports; a match is never said to be "in" one.
_NOT_ENCLOSING = frozenset(STATE_KINDS) | {"import"}


def attach_enclosing_symbols(results: list, symbols) -> None:
    """Give each match the innermost function, method or class around it.

    The index already holds every symbol's line span, so this is a lookup, not
    a parse: one pass over the symbols of the matched files only. A match at
    module level gets no ``in``.
    """
    files = {group["file"] for group in results}
    if not files:
        return
    spans: dict[str, list] = {}
    for sym in symbols:
        file = sym.get("file")
        if file not in files or sym.get("kind") in _NOT_ENCLOSING:
            continue
        start, end = sym.get("line"), sym.get("end_line")
        if not isinstance(start, int) or not isinstance(end, int) or end < start:
            continue
        spans.setdefault(file, []).append((start, end, sym))
    for group in results:
        candidates = spans.get(group["file"])
        if not candidates:
            continue
        for match in group["matches"]:
            line = match.get("line")
            best = None
            for start, end, sym in candidates:
                # The smallest span wins. On a tie (a one-line method inside a
                # one-line class) the later symbol wins: symbols are stored
                # outer-first, so the later one is the deeper one.
                if start <= line <= end and (best is None or end - start <= best[1] - best[0]):
                    best = (start, end, sym)
            if best is not None:
                start, end, sym = best
                # The id (``file::Name#kind``) carries the name and kind and is
                # what get_symbol_source takes, so they are not repeated.
                match["in"] = {"id": sym.get("id"), "line": start, "end_line": end}
