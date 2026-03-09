"""Generate per-file summaries from symbol information and optional context providers."""

from typing import Optional

from ..parser.symbols import Symbol
from ..parser.context.base import ContextProvider


def _heuristic_summary(file_path: str, symbols: list[Symbol]) -> str:
    """Generate summary from symbol information."""
    if not symbols:
        return ""

    classes = [s for s in symbols if s.kind == "class"]
    functions = [s for s in symbols if s.kind == "function"]
    methods = [s for s in symbols if s.kind == "method"]
    constants = [s for s in symbols if s.kind == "constant"]
    types = [s for s in symbols if s.kind == "type"]

    parts = []
    if classes:
        for cls in classes[:2]:
            method_count = sum(1 for s in symbols if s.parent and s.parent.endswith(f"::{cls.name}#class"))
            parts.append(f"Defines {cls.name} class ({method_count} methods)")
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
    if constants and not parts:
        parts.append(f"Defines {len(constants)} constants")

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
