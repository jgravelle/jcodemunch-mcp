"""Base classes for context providers.

A ContextProvider detects an ecosystem tool in a project folder, loads its
metadata, and enriches symbols with business context. The abstraction is
intentionally minimal — providers only need to implement detect/load/lookup.
"""

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Wall-clock ceiling for one provider's detect()+load(). Provider discovery runs
# BEFORE a single file is indexed, so a provider that grinds takes the whole
# index down with it and the caller sees only silence (#375: 4 minutes at 100%
# CPU inside one regex, then 90 minutes of nothing on a full repo). A budget
# cannot make a slow provider fast; it makes it SKIPPABLE and NAMED, so one
# pathological ecosystem degrades to "express skipped after 30s" instead of an
# unbounded stall with no attribution.
_DEFAULT_PROVIDER_BUDGET_SECONDS = 30.0


def provider_budget_seconds() -> float:
    """Per-provider wall-clock budget; ``0`` or negative disables the ceiling."""
    raw = os.environ.get("JCODEMUNCH_PROVIDER_BUDGET_SECONDS")
    if raw is None:
        return _DEFAULT_PROVIDER_BUDGET_SECONDS
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Ignoring non-numeric JCODEMUNCH_PROVIDER_BUDGET_SECONDS=%r; using %.1fs",
            raw, _DEFAULT_PROVIDER_BUDGET_SECONDS,
        )
        return _DEFAULT_PROVIDER_BUDGET_SECONDS


@dataclass
class FileContext:
    """Business context metadata for a single file from any ecosystem tool.

    This is the common currency between context providers and the indexing
    pipeline. Providers translate their tool-specific metadata into this
    structure, and the pipeline consumes it generically.
    """

    description: str = ""
    tags: list[str] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)

    def summary_context(self, max_properties: int = 10) -> str:
        """Build a concise context string for AI summarization prompts."""
        parts = []
        if self.description:
            parts.append(self.description)
        if self.tags:
            parts.append(f"Tags: {', '.join(self.tags)}")
        if self.properties:
            items = list(self.properties.items())[:max_properties]
            prop_strs = []
            for key, val in items:
                if val:
                    prop_strs.append(f"{key} ({val})")
                else:
                    prop_strs.append(key)
            if len(self.properties) > max_properties:
                prop_strs.append(f"... and {len(self.properties) - max_properties} more")
            parts.append(f"Properties: {', '.join(prop_strs)}")
        return ". ".join(parts)

    def file_summary(self, include_properties: bool = True) -> str:
        """Build an enriched file-level summary.

        ``include_properties=False`` drops the "N properties" count, for a
        provider whose properties are not vocabulary (L-189).
        """
        parts = []
        if self.description:
            desc = self.description.strip()
            if len(desc) > 200:
                desc = desc[:197] + "..."
            parts.append(desc)
        if self.tags:
            parts.append(f"Tags: {', '.join(self.tags)}")
        if self.properties and include_properties:
            parts.append(f"{len(self.properties)} properties")
        return ". ".join(parts)

    def search_keywords(self, include_properties: bool = True) -> list[str]:
        """Extract keywords for search indexing.

        ``include_properties=False`` for a provider whose property names are
        not vocabulary (see ``ContextProvider.properties_are_search_terms``).
        """
        keywords = []
        if self.tags:
            keywords.extend(self.tags)
        if self.properties and include_properties:
            keywords.extend(self.properties.keys())
        return keywords


class ContextProvider(ABC):
    """Base class for ecosystem context providers.

    Subclasses detect a specific tool (dbt, Terraform, etc.), load its
    metadata, and provide per-file context lookups. Providers are
    auto-discovered and run during indexing — no configuration needed.
    """

    # Cooperative deadline, set by discover_providers before detect()/load().
    # A provider that walks a tree should break out of its loop when this
    # passes; see budget_expired(). The thread watchdog in _run_with_budget is
    # the backstop for code that CANNOT check (a single long regex call), but a
    # watchdog only stops the CALLER waiting — it cannot stop the work. Loops
    # that poll this actually stop burning CPU, so poll it wherever you walk.
    _deadline: Optional[float] = None

    # Whether this provider's property NAMES become search keywords on every
    # symbol of a file it covers. True for a framework or data provider, whose
    # property names describe the code (`materialized`, a route verb). False for
    # one whose coverage depends on something outside the corpus: git blame
    # covers a file only when a timed `git log` finished, and BM25 scores the
    # keywords, so a timeout reordered search results on unchanged code (L-188).
    # The same flag keeps the bare "N properties" count out of a file summary
    # (`FileContext.file_summary`), which names nothing for such a provider (L-189).
    properties_are_search_terms: bool = True

    def budget_expired(self) -> bool:
        """True once this provider has overrun its discovery budget."""
        deadline = self._deadline
        return deadline is not None and time.monotonic() >= deadline

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this provider (e.g., 'dbt', 'terraform')."""
        ...

    @abstractmethod
    def detect(self, folder_path: Path) -> bool:
        """Return True if this provider's ecosystem is present in the folder."""
        ...

    @abstractmethod
    def load(self, folder_path: Path) -> None:
        """Load metadata from the project. Called only if detect() returned True."""
        ...

    @abstractmethod
    def get_file_context(self, file_path: str) -> Optional[FileContext]:
        """Look up context for a file by its relative path (stem match is fine)."""
        ...

    @abstractmethod
    def stats(self) -> dict:
        """Return provider-specific stats for the index response."""
        ...

    def get_metadata(self) -> dict:
        """Return structured metadata to persist in the index.

        Override in subclasses to expose searchable metadata.
        Keys should be namespaced by provider (e.g., 'dbt_columns').

        Column metadata convention:
            To expose columns for ``search_columns``, emit a key ending in
            ``_columns`` whose value is ``{model_name: {col_name: col_desc}}``.
            Example::

                {"dbt_columns": {"fact_orders": {"order_id": "Primary key", ...}}}

            Any provider following this convention will be automatically
            discovered by ``search_columns`` — no tool-side changes needed.
        """
        return {}

    def get_extra_imports(self) -> dict[str, list[dict]]:
        """Return additional import edges discovered by this provider.

        Override in subclasses to inject framework-specific import edges
        into the dependency graph (e.g., Blade template references,
        route-to-controller mappings).

        Returns:
            Dict mapping relative file paths to lists of import dicts::

                {"routes/web.php": [{"specifier": "App\\\\Http\\\\Controllers\\\\UserController", "names": ["UserController"]}]}
        """
        return {}


# -- Registry of known providers --

_PROVIDER_CLASSES: list[type[ContextProvider]] = []


def register_provider(cls: type[ContextProvider]) -> type[ContextProvider]:
    """Class decorator to register a context provider."""
    _PROVIDER_CLASSES.append(cls)
    return cls


def _run_with_budget(fn: Callable[[], None], seconds: float) -> tuple[bool, Optional[BaseException]]:
    """Run ``fn`` on a daemon thread, returning ``(finished, exception)``.

    ``finished`` is False when the budget elapsed first. The thread is NOT
    killed — Python cannot preempt one — so an overrun provider may keep
    running in the background until its cooperative deadline check fires or it
    completes. What this guarantees is that the INDEX stops waiting on it, and
    that the overrun is named. Callers must discard the provider's data on a
    timeout: it is still being mutated by the abandoned thread.
    """
    box: dict[str, BaseException] = {}

    def _target() -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised to the caller
            box["exc"] = exc

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        return False, None
    return True, box.get("exc")


def discover_providers(
    folder_path: Path,
    budget_seconds: Optional[float] = None,
    skipped: Optional[list] = None,
) -> list[ContextProvider]:
    """Instantiate and detect all registered context providers.

    Returns only providers whose ecosystem was detected in the folder AND that
    finished detect()+load() inside their wall-clock budget. Pass ``skipped`` a
    list to receive one ``{provider, reason, seconds, error}`` record per
    provider that overran or raised, so the caller can disclose the gap instead
    of silently indexing with less context than it advertises.

    ``budget_seconds`` defaults to ``provider_budget_seconds()``; ``0`` or
    negative runs providers inline with no ceiling (pre-1.108.182 behaviour).
    """
    budget = provider_budget_seconds() if budget_seconds is None else budget_seconds
    active: list[ContextProvider] = []
    for cls in _PROVIDER_CLASSES:
        started = time.monotonic()
        name = getattr(cls, "__name__", "provider")
        try:
            provider = cls()
            name = provider.name or name
        except Exception as e:
            logger.warning("Context provider '%s' failed to construct: %s", name, e)
            if skipped is not None:
                skipped.append({"provider": name, "reason": "error", "error": str(e)})
            continue

        outcome: dict[str, bool] = {}

        def _detect_and_load(p: ContextProvider = provider, out: dict = outcome) -> None:
            if p.detect(folder_path):
                p.load(folder_path)
                out["detected"] = True

        if budget > 0:
            provider._deadline = started + budget
            finished, exc = _run_with_budget(_detect_and_load, budget)
        else:
            provider._deadline = None
            try:
                _detect_and_load()
                finished, exc = True, None
            except Exception as e:  # noqa: BLE001
                finished, exc = True, e

        elapsed = time.monotonic() - started
        # A provider that polls budget_expired() returns on its own at the
        # deadline, so the watchdog can see it finish with part of its work
        # (L-190). That is an overrun too: keeping the partial context would
        # make what an index carries depend on which thread won the race.
        if finished and exc is None and budget > 0 and outcome.get("detected") and provider.budget_expired():
            finished = False
        if not finished:
            logger.warning(
                "Context provider '%s' exceeded its %.1fs budget and was skipped; "
                "this index carries no %s context. Raise or disable the ceiling with "
                "JCODEMUNCH_PROVIDER_BUDGET_SECONDS.",
                name, budget, name,
            )
            if skipped is not None:
                skipped.append({
                    "provider": name,
                    "reason": "budget_exceeded",
                    "seconds": round(elapsed, 3),
                    "budget_seconds": budget,
                })
            continue
        if exc is not None:
            logger.warning("Context provider '%s' failed: %s", name, exc)
            if skipped is not None:
                skipped.append({
                    "provider": name, "reason": "error",
                    "seconds": round(elapsed, 3), "error": str(exc),
                })
            continue
        if not outcome.get("detected"):
            continue
        logger.info("Context provider '%s' activated in %.2fs", name, elapsed)
        active.append(provider)
    return active


def collect_metadata(providers: list[ContextProvider]) -> dict:
    """Collect structured metadata from all active providers for index persistence."""
    metadata: dict = {}
    for provider in providers:
        try:
            provider_meta = provider.get_metadata()
            if provider_meta:
                for key, value in provider_meta.items():
                    if key in metadata:
                        logger.warning(
                            "Metadata key '%s' from provider '%s' overwrites "
                            "existing key (previous value dropped)",
                            key,
                            provider.name,
                        )
                    metadata[key] = value
        except Exception as e:
            logger.warning("Metadata collection from '%s' failed: %s", provider.name, e)
    return metadata


def enrich_symbols(symbols: list, providers: list[ContextProvider]) -> None:
    """Attach context from all active providers to symbols in-place."""
    if not providers:
        return

    # Pre-compute file contexts once per unique file per provider
    # to avoid O(symbols × providers) redundant lookups.
    unique_files = {sym.file for sym in symbols}
    file_ctx_cache: dict[tuple[str, str], tuple[str, list[str]]] = {}
    for provider in providers:
        pname = provider.name
        for fpath in unique_files:
            ctx = provider.get_file_context(fpath)
            if ctx is not None:
                summary = ctx.summary_context(max_properties=8)
                kw = ctx.search_keywords(include_properties=provider.properties_are_search_terms)
                if summary or kw:
                    file_ctx_cache[(pname, fpath)] = (summary, kw)

    for sym in symbols:
        context_parts = []
        for provider in providers:
            cached = file_ctx_cache.get((provider.name, sym.file))
            if cached is None:
                continue
            summary, kw = cached
            if summary:
                context_parts.append(f'{provider.name}: {summary}')
            if kw:
                existing = set(sym.keywords)
                sym.keywords.extend(k for k in kw if k not in existing)

        if context_parts:
            sym.ecosystem_context = "; ".join(context_parts)


def collect_extra_imports(
    providers: list[ContextProvider],
    file_imports: dict[str, list[dict]],
) -> None:
    """Merge extra import edges from context providers into file_imports in-place.

    Providers can inject framework-specific import edges (e.g., Blade template
    references, route→controller mappings) that the standard language-level
    import extractor cannot detect.
    """
    for provider in providers:
        try:
            extras = provider.get_extra_imports()
            for file_path, imports in extras.items():
                if file_path in file_imports:
                    # Deduplicate: only add specifiers not already present
                    existing = {imp["specifier"] for imp in file_imports[file_path]}
                    for imp in imports:
                        if imp["specifier"] not in existing:
                            file_imports[file_path].append(imp)
                            existing.add(imp["specifier"])
                else:
                    file_imports[file_path] = list(imports)
        except Exception as e:
            logger.warning(
                "Extra imports from '%s' failed: %s", provider.name, e
            )
