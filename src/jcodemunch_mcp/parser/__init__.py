"""Parser package for extracting symbols from source code."""

from .symbols import Symbol, slugify, make_symbol_id, compute_content_hash
from .languages import LanguageSpec, LANGUAGE_REGISTRY, LANGUAGE_EXTENSIONS, PYTHON_SPEC, get_language_for_path
from .extractor import parse_file
from .hierarchy import SymbolNode, build_symbol_tree, flatten_tree

__all__ = [
    "Symbol",
    "slugify",
    "make_symbol_id",
    "compute_content_hash",
    "LanguageSpec",
    "LANGUAGE_REGISTRY",
    "LANGUAGE_EXTENSIONS",
    "PYTHON_SPEC",
    "get_language_for_path",
    "parse_file",
    "SymbolNode",
    "build_symbol_tree",
    "flatten_tree",
]
