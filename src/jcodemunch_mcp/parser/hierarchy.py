"""Build symbol tree hierarchy for file outlines."""

from dataclasses import dataclass, field
from typing import Optional

from .symbols import Symbol


@dataclass
class SymbolNode:
    """A node in the symbol tree with children."""
    symbol: Symbol
    children: list["SymbolNode"] = field(default_factory=list)


def build_symbol_tree(symbols: list[Symbol]) -> list[SymbolNode]:
    """Build a hierarchical tree from flat symbol list.
    
    Methods become children of their parent classes.
    Returns top-level symbols (classes and standalone functions).
    """
    # Create a map of symbol ID to node
    node_map = {s.id: SymbolNode(symbol=s) for s in symbols}
    
    # Build parent-child relationships
    roots = []
    for symbol in symbols:
        node = node_map[symbol.id]
        if symbol.parent and symbol.parent in node_map:
            parent_node = node_map[symbol.parent]
            parent_node.children.append(node)
        else:
            roots.append(node)
    
    return roots


def flatten_tree(nodes: list[SymbolNode], depth: int = 0) -> list[tuple[Symbol, int]]:
    """Flatten symbol tree with depth information.
    
    Returns list of (symbol, depth) tuples for indentation.
    """
    result = []
    for node in nodes:
        result.append((node.symbol, depth))
        result.extend(flatten_tree(node.children, depth + 1))
    return result
