"""Generate per-file summaries from symbol information and optional context providers."""

from typing import Optional

from ..parser.symbols import STATE_KINDS, Symbol, plural_kind
from ..parser.context.base import ContextProvider


def _counted(symbols: list[Symbol], kinds: tuple[str, ...]) -> list[str]:
    """`["2 methods", "5 properties"]` for the kinds present, in vocabulary order.

    ⚠ A zero count is omitted rather than rendered: "0 methods" on a class whose
    state IS its content read as an empty file, which is the symptom this fixes.
    """
    bits = []
    for kind in kinds:
        count = sum(1 for s in symbols if s.kind == kind)
        if count:
            bits.append(f"{count} {plural_kind(kind, count)}")
    return bits


def _heuristic_summary(file_path: str, symbols: list[Symbol]) -> str:
    """Generate summary from symbol information."""
    if not symbols:
        return ""

    classes = [s for s in symbols if s.kind == "class"]
    functions = [s for s in symbols if s.kind == "function"]
    methods = [s for s in symbols if s.kind == "method"]
    state = [s for s in symbols if s.kind in STATE_KINDS]
    types = [s for s in symbols if s.kind == "type"]

    parts = []
    if classes:
        for cls in classes[:2]:
            # ⚠⚠ The PARENT filter is what keeps module-scope bindings out of a
            # class's member count -- `constant` and `variable` are reached in
            # both scopes, and `KIND_ORDER`'s own comment names that mixing as
            # the thing to avoid. Widening the KINDS is safe only because the
            # SCOPE question is still asked here.
            #
            # ⚠⚠ Matched against the class's OWN id, not by name suffix. The old
            # `parent.endswith(f"::{cls.name}#class")` could not tell a NESTED
            # class from a top-level one of the same name: Kotlin's
            # `class Outer { class Inner { val a; val b } }` beside a top-level
            # `class Inner { val z }` reported the nested `Inner` with the
            # TOP-LEVEL one's member, because `::Outer.Inner#class` does not end
            # with `::Inner#class` while `::Inner#class` does. That leak
            # pre-dates this change and carried `field` alone; widening the
            # kinds would have given it three more to mis-attribute.
            members = [s for s in symbols if s.parent == cls.id]
            bits = _counted(members, ("method",) + STATE_KINDS)
            desc = f"Defines {cls.name} class"
            if bits:
                desc += f" ({', '.join(bits)})"
            parts.append(desc)
    if functions:
        if len(functions) <= 3:
            names = ", ".join(f.name for f in functions)
            parts.append(f"Contains {len(functions)} functions: {names}")
        else:
            names = ", ".join(f.name for f in functions[:3])
            parts.append(f"Contains {len(functions)} functions: {names}, ...")
    if types and not parts:
        names = ", ".join(t.name for t in types[:3])
        parts.append(f"Defines types: {names}")
    if state and not parts:
        # The same defect a few lines up: this counted `constant` alone, so a
        # file of `let`/`var` bindings (#741, #742) summarised as having none.
        parts.append("Defines " + ", ".join(_counted(state, STATE_KINDS)))

    return ". ".join(parts) if parts else ""


def _context_summary(file_path: str, providers: list[ContextProvider]) -> str:
    """Build a combined context summary from all active providers."""
    parts = []
    for provider in providers:
        ctx = provider.get_file_context(file_path)
        if ctx is not None:
            summary = ctx.file_summary()
            if summary:
                parts.append(summary)
    return ". ".join(parts)


def generate_file_summaries(
    file_symbols: dict[str, list[Symbol]],
    context_providers: Optional[list[ContextProvider]] = None,
    # Backward compat: accept dbt_project as keyword arg and ignore it.
    # Callers should migrate to context_providers.
    dbt_project: Optional[object] = None,
) -> dict[str, str]:
    """Generate summaries for each file from symbol data and optional context providers.

    Args:
        file_symbols: Maps file path -> list of Symbol objects for that file
        context_providers: Optional list of active ContextProvider instances

    Returns:
        Dict mapping file path -> summary string
    """
    providers = context_providers or []
    summaries = {}

    for file_path, symbols in file_symbols.items():
        ctx_summary = _context_summary(file_path, providers) if providers else ""
        heuristic = _heuristic_summary(file_path, symbols)

        if ctx_summary and heuristic:
            summaries[file_path] = f"{ctx_summary}. {heuristic}"
        elif ctx_summary:
            summaries[file_path] = ctx_summary
        else:
            summaries[file_path] = heuristic

    return summaries
