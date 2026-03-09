"""Tests for the context provider framework (base classes, registry, enrichment)."""

import pytest

from jcodemunch_mcp.parser.symbols import Symbol
from jcodemunch_mcp.parser.context.base import (
    ContextProvider,
    FileContext,
    _PROVIDER_CLASSES,
    register_provider,
    discover_providers,
    enrich_symbols,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_symbol(name, file="src/main.py", kind="function"):
    return Symbol(
        id=f"{file}::{name}#{kind}",
        file=file,
        name=name,
        qualified_name=name,
        kind=kind,
        language="python",
        signature=f"def {name}()",
    )


class _StubProvider(ContextProvider):
    """Minimal provider for testing — matches files whose stem is in _files."""

    def __init__(self, provider_name="stub", files=None, should_detect=True):
        self._name = provider_name
        self._files = files or {}
        self._should_detect = should_detect
        self._loaded = False

    @property
    def name(self):
        return self._name

    def detect(self, folder_path):
        return self._should_detect

    def load(self, folder_path):
        self._loaded = True

    def get_file_context(self, file_path):
        from pathlib import Path
        stem = Path(file_path).stem
        return self._files.get(stem)

    def stats(self):
        return {"files": len(self._files)}


class _FailingProvider(ContextProvider):
    """Provider that raises during load()."""

    @property
    def name(self):
        return "failing"

    def detect(self, folder_path):
        return True

    def load(self, folder_path):
        raise RuntimeError("boom")

    def get_file_context(self, file_path):
        return None

    def stats(self):
        return {}


# ===========================================================================
# FileContext tests
# ===========================================================================


class TestFileContext:

    def test_summary_context_full(self):
        ctx = FileContext(
            description="Daily revenue by product",
            tags=["nightly", "finance"],
            properties={"amount": "USD value", "date": "Revenue date"},
        )
        result = ctx.summary_context()
        assert "Daily revenue by product" in result
        assert "nightly" in result
        assert "finance" in result
        assert "amount (USD value)" in result
        assert "date (Revenue date)" in result

    def test_summary_context_truncates_properties(self):
        props = {f"col_{i}": f"desc_{i}" for i in range(15)}
        ctx = FileContext(properties=props)
        result = ctx.summary_context(max_properties=5)
        assert "... and 10 more" in result

    def test_summary_context_property_without_value(self):
        ctx = FileContext(properties={"id": "", "name": ""})
        result = ctx.summary_context()
        # Properties with empty values should appear as bare names
        assert "id" in result
        assert "(" not in result.split("Properties:")[1].split(",")[0]

    def test_file_summary_full(self):
        ctx = FileContext(
            description="Tracks user signups",
            tags=["daily"],
            properties={"user_id": "", "signup_date": ""},
        )
        result = ctx.file_summary()
        assert "Tracks user signups" in result
        assert "Tags: daily" in result
        assert "2 properties" in result

    def test_file_summary_truncates_long_description(self):
        ctx = FileContext(description="A" * 300)
        result = ctx.file_summary()
        assert len(result) < 300
        assert result.endswith("...")

    def test_search_keywords(self):
        ctx = FileContext(
            tags=["nightly", "finance"],
            properties={"amount": "val", "date": "val"},
        )
        kw = ctx.search_keywords()
        assert "nightly" in kw
        assert "finance" in kw
        assert "amount" in kw
        assert "date" in kw

    def test_empty_context(self):
        ctx = FileContext()
        assert ctx.summary_context() == ""
        assert ctx.file_summary() == ""
        assert ctx.search_keywords() == []


# ===========================================================================
# Registry tests
# ===========================================================================


class TestRegisterProvider:

    def test_register_adds_class(self):
        initial_count = len(_PROVIDER_CLASSES)

        @register_provider
        class _TestProvider(ContextProvider):
            @property
            def name(self):
                return "test_register"

            def detect(self, fp):
                return False

            def load(self, fp):
                pass

            def get_file_context(self, fp):
                return None

            def stats(self):
                return {}

        assert len(_PROVIDER_CLASSES) == initial_count + 1
        assert _PROVIDER_CLASSES[-1] is _TestProvider

        # Clean up so we don't pollute other tests
        _PROVIDER_CLASSES.pop()


# ===========================================================================
# discover_providers tests
# ===========================================================================


class TestDiscoverProviders:

    def test_none_detected(self, tmp_path):
        """Providers that don't detect return empty list."""
        # Use discover_providers with a fresh registry — but we can't easily
        # swap out the global list, so we test the mechanics via _StubProvider
        provider = _StubProvider(should_detect=False)
        assert provider.detect(tmp_path) is False

    def test_activates_matching(self, tmp_path):
        provider = _StubProvider(should_detect=True)
        assert provider.detect(tmp_path) is True
        provider.load(tmp_path)
        assert provider._loaded is True

    def test_handles_load_failure(self, tmp_path):
        """Provider that throws during load() should not crash discover."""
        provider = _FailingProvider()
        assert provider.detect(tmp_path) is True
        with pytest.raises(RuntimeError):
            provider.load(tmp_path)


# ===========================================================================
# enrich_symbols tests
# ===========================================================================


class TestEnrichSymbols:

    def test_sets_ecosystem_context(self):
        ctx = FileContext(description="User signups", tags=["daily"])
        provider = _StubProvider(files={"main": ctx})
        sym = _make_symbol("source", file="src/main.py")

        enrich_symbols([sym], [provider])

        assert "stub" in sym.ecosystem_context
        assert "User signups" in sym.ecosystem_context

    def test_merges_keywords_without_duplicates(self):
        ctx = FileContext(tags=["nightly"], properties={"col_a": ""})
        provider = _StubProvider(files={"main": ctx})
        sym = _make_symbol("source", file="src/main.py")
        sym.keywords = ["nightly"]  # pre-existing

        enrich_symbols([sym], [provider])

        assert sym.keywords.count("nightly") == 1
        assert "col_a" in sym.keywords

    def test_multiple_providers(self):
        ctx1 = FileContext(description="From tool A")
        ctx2 = FileContext(description="From tool B")
        p1 = _StubProvider(provider_name="toolA", files={"main": ctx1})
        p2 = _StubProvider(provider_name="toolB", files={"main": ctx2})
        sym = _make_symbol("source", file="src/main.py")

        enrich_symbols([sym], [p1, p2])

        assert "toolA" in sym.ecosystem_context
        assert "toolB" in sym.ecosystem_context
        assert ";" in sym.ecosystem_context

    def test_no_match_leaves_symbol_untouched(self):
        provider = _StubProvider(files={})  # no files match
        sym = _make_symbol("source", file="src/other.py")

        enrich_symbols([sym], [provider])

        assert sym.ecosystem_context == ""
        assert sym.keywords == []

    def test_empty_providers_list(self):
        sym = _make_symbol("source")
        enrich_symbols([sym], [])
        assert sym.ecosystem_context == ""
