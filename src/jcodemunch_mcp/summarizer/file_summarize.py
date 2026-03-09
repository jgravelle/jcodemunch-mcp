"""Generate per-file heuristic summaries from symbol information."""

from ..parser.symbols import Symbol


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


def generate_file_summaries(
    file_symbols: dict[str, list[Symbol]],
) -> dict[str, str]:
    """Generate heuristic summaries for each file from symbol data.

    Args:
        file_symbols: Maps file path -> list of Symbol objects for that file

    Returns:
        Dict mapping file path -> summary string
    """
    summaries = {}

    for file_path, symbols in file_symbols.items():
        heuristic = _heuristic_summary(file_path, symbols)
        summaries[file_path] = heuristic

    return summaries
