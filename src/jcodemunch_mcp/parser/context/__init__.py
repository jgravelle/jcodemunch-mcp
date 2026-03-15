"""Context providers for enriching code indexes with business metadata.

Context providers detect ecosystem tools (dbt, Terraform, OpenAPI, etc.)
and inject business context into symbols and file summaries during indexing.
"""

from .base import ContextProvider, FileContext, discover_providers, enrich_symbols, collect_metadata

# Import provider modules so @register_provider decorators execute.
# Each module registers itself on import — add new providers here.
from . import dbt  # noqa: F401

__all__ = [
    "ContextProvider",
    "FileContext",
    "collect_metadata",
    "discover_providers",
    "enrich_symbols",
]
