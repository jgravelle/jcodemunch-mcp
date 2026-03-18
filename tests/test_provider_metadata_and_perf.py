"""Tests for PR #96 review feedback fixes and additional optimizations."""

import logging
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from jcodemunch_mcp.parser.context.base import collect_metadata, ContextProvider
from jcodemunch_mcp.parser.context.dbt import _resolve_description, _parse_yml_files
import jcodemunch_mcp.parser.imports as _imports_mod
from jcodemunch_mcp.parser.imports import resolve_specifier, _get_sql_stems


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class _MetadataProvider(ContextProvider):
    """Stub provider that returns configurable metadata."""

    def __init__(self, provider_name: str, metadata: dict):
        self._name = provider_name
        self._metadata = metadata

    @property
    def name(self):
        return self._name

    def detect(self, folder_path):
        return True

    def load(self, folder_path):
        pass

    def get_file_context(self, file_path):
        return None

    def stats(self):
        return {}

    def get_metadata(self):
        return self._metadata


# ===========================================================================
# collect_metadata collision warning (#4)
# ===========================================================================


class TestCollectMetadataCollision:

    def test_no_collision_no_warning(self, caplog):
        p1 = _MetadataProvider("dbt", {"dbt_columns": {"m1": {"c1": "d1"}}})
        p2 = _MetadataProvider("sqlmesh", {"sqlmesh_columns": {"m2": {"c2": "d2"}}})

        with caplog.at_level(logging.WARNING):
            result = collect_metadata([p1, p2])

        assert "dbt_columns" in result
        assert "sqlmesh_columns" in result
        assert "overwrites" not in caplog.text

    def test_collision_logs_warning(self, caplog):
        p1 = _MetadataProvider("providerA", {"shared_key": {"a": 1}})
        p2 = _MetadataProvider("providerB", {"shared_key": {"b": 2}})

        with caplog.at_level(logging.WARNING):
            result = collect_metadata([p1, p2])

        # Second provider's value wins
        assert result["shared_key"] == {"b": 2}
        assert "overwrites" in caplog.text
        assert "providerB" in caplog.text

    def test_empty_metadata_not_merged(self):
        p1 = _MetadataProvider("empty", {})
        result = collect_metadata([p1])
        assert result == {}


# ===========================================================================
# _resolve_description inline substitution (#9)
# ===========================================================================


class TestResolveDescriptionInline:

    def test_preserves_surrounding_text(self):
        blocks = {"my_doc": "resolved text"}
        result = _resolve_description("Prefix {{ doc('my_doc') }} suffix", blocks)
        assert result == "Prefix resolved text suffix"

    def test_multiple_doc_refs(self):
        blocks = {"doc_a": "AAA", "doc_b": "BBB"}
        result = _resolve_description(
            "{{ doc('doc_a') }} and {{ doc('doc_b') }}", blocks
        )
        assert result == "AAA and BBB"

    def test_missing_ref_becomes_empty(self):
        result = _resolve_description("Before {{ doc('missing') }} after", {})
        assert result == "Before  after"

    def test_plain_text_unchanged(self):
        result = _resolve_description("No doc refs here.", {"some_doc": "val"})
        assert result == "No doc refs here."

    def test_full_doc_ref_still_works(self):
        """Backward compat: full {{ doc() }} descriptions still resolve."""
        blocks = {"my_doc": "Full resolved text."}
        result = _resolve_description("{{ doc('my_doc') }}", blocks)
        assert result == "Full resolved text."


# ===========================================================================
# dbt top-level tags + config.tags merge (#8)
# ===========================================================================


yaml = pytest.importorskip("yaml", reason="pyyaml required")


class TestDbtTagsMerge:

    def test_top_level_tags_only(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        _write(models_dir / "schema.yml", """
models:
  - name: my_model
    description: "A model"
    tags: ['daily', 'finance']
""")
        result = _parse_yml_files([models_dir], {})
        assert result["my_model"].tags == ["daily", "finance"]

    def test_config_tags_only(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        _write(models_dir / "schema.yml", """
models:
  - name: my_model
    description: "A model"
    config:
      tags: ['nightly']
""")
        result = _parse_yml_files([models_dir], {})
        assert result["my_model"].tags == ["nightly"]

    def test_both_merged_deduplicated(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        _write(models_dir / "schema.yml", """
models:
  - name: my_model
    description: "A model"
    tags: ['daily', 'core']
    config:
      tags: ['core', 'nightly']
""")
        result = _parse_yml_files([models_dir], {})
        tags = result["my_model"].tags
        # daily + core from top-level, nightly from config; core deduplicated
        assert tags == ["daily", "core", "nightly"]

    def test_invalid_top_level_tags_ignored(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        _write(models_dir / "schema.yml", """
models:
  - name: my_model
    description: "A model"
    tags: "not_a_list"
    config:
      tags: ['valid']
""")
        result = _parse_yml_files([models_dir], {})
        assert result["my_model"].tags == ["valid"]


# ===========================================================================
# SQL stem cache (#7)
# ===========================================================================


class TestSqlStemCache:

    def _reset_cache(self):
        """Reset the module-level stem cache to avoid cross-test pollution."""
        _imports_mod._sql_stem_cache = (0, {})

    def test_cached_lookup_matches_linear_scan(self):
        self._reset_cache()
        source_files = {
            "DBT/models/dim/dim_client.sql",
            "DBT/models/fact/fact_orders.sql",
            "src/app.js",
        }
        # Verify O(1) lookup produces same results as original O(n) scan
        assert resolve_specifier("dim_client", "any.sql", source_files) == "DBT/models/dim/dim_client.sql"
        assert resolve_specifier("fact_orders", "any.sql", source_files) == "DBT/models/fact/fact_orders.sql"
        assert resolve_specifier("nonexistent", "any.sql", source_files) is None

    def test_cache_reused_for_same_set(self):
        self._reset_cache()
        source_files = {"models/a.sql", "models/b.sql"}
        _get_sql_stems(source_files)
        cached_id = _imports_mod._sql_stem_cache[0]
        # Second call with same object reuses cache
        _get_sql_stems(source_files)
        assert _imports_mod._sql_stem_cache[0] == cached_id

    def test_cache_invalidated_for_different_set(self):
        self._reset_cache()
        # Both sets must be alive simultaneously so Python can't reuse the id()
        set1 = set(["models/alpha.sql"])
        set2 = set(["models/beta.sql"])
        stems1 = _get_sql_stems(set1)
        assert "alpha" in stems1
        stems2 = _get_sql_stems(set2)
        assert "beta" in stems2
        assert "alpha" not in stems2


# ===========================================================================
# Stale context_metadata fix (#6)
# ===========================================================================


class TestIncrementalMetadataNotStale:

    def test_empty_dict_clears_metadata(self, tmp_path):
        """Empty {} from active providers should replace old metadata, not preserve it."""
        from jcodemunch_mcp.storage.index_store import IndexStore, CodeIndex
        from jcodemunch_mcp.parser.symbols import Symbol

        store = IndexStore(base_path=str(tmp_path / "store"))

        # Create initial index with metadata
        sym = Symbol(
            id="m.sql::source#function", file="m.sql", name="source",
            qualified_name="source", kind="function", language="sql",
            signature="WITH source AS (...)",
        )
        index = store.save_index(
            owner="local", name="test",
            source_files=["m.sql"],
            symbols=[sym],
            raw_files={"m.sql": "SELECT 1"},
            context_metadata={"dbt_columns": {"model": {"col": "desc"}}},
        )
        assert index.context_metadata == {"dbt_columns": {"model": {"col": "desc"}}}

        # Incremental save with empty metadata (providers active but no columns)
        updated = store.incremental_save(
            owner="local", name="test",
            changed_files=[], new_files=[], deleted_files=[],
            new_symbols=[],
            raw_files={},
            context_metadata={},  # empty dict — should NOT fall back to old
        )
        assert updated.context_metadata == {}

    def test_none_preserves_metadata(self, tmp_path):
        """None (no providers active) should preserve existing metadata."""
        from jcodemunch_mcp.storage.index_store import IndexStore
        from jcodemunch_mcp.parser.symbols import Symbol

        store = IndexStore(base_path=str(tmp_path / "store"))

        sym = Symbol(
            id="m.sql::source#function", file="m.sql", name="source",
            qualified_name="source", kind="function", language="sql",
            signature="WITH source AS (...)",
        )
        index = store.save_index(
            owner="local", name="test",
            source_files=["m.sql"],
            symbols=[sym],
            raw_files={"m.sql": "SELECT 1"},
            context_metadata={"dbt_columns": {"model": {"col": "desc"}}},
        )

        # Incremental save with None (no providers active)
        updated = store.incremental_save(
            owner="local", name="test",
            changed_files=[], new_files=[], deleted_files=[],
            new_symbols=[],
            raw_files={},
            context_metadata=None,  # None — should preserve old
        )
        assert updated.context_metadata == {"dbt_columns": {"model": {"col": "desc"}}}


# ===========================================================================
# Concurrent summarization (#11)
# ===========================================================================


class TestConcurrentSummarization:

    def _make_symbols(self, count):
        from jcodemunch_mcp.parser.symbols import Symbol
        return [
            Symbol(
                id=f"test::fn_{i}#function", file="test.py",
                name=f"fn_{i}", qualified_name=f"fn_{i}",
                kind="function", language="python",
                signature=f"def fn_{i}():",
            )
            for i in range(count)
        ]

    def test_multi_batch_concurrent(self):
        """Multiple batches are processed concurrently when max_workers > 1."""
        from jcodemunch_mcp.summarizer.batch_summarize import BaseSummarizer

        summarizer = BaseSummarizer()
        summarizer.client = True  # non-None to enable processing
        summarizer.batch_count = 0

        original_method = summarizer._summarize_one_batch

        def _counting_batch(batch):
            summarizer.batch_count += 1
            for sym in batch:
                sym.summary = f"Summary for {sym.name}"

        summarizer._summarize_one_batch = _counting_batch

        symbols = self._make_symbols(25)
        with patch.dict("os.environ", {"JCODEMUNCH_SUMMARIZER_CONCURRENCY": "4"}):
            summarizer.summarize_batch(symbols, batch_size=10)

        # 25 symbols / 10 per batch = 3 batches
        assert summarizer.batch_count == 3
        assert all(s.summary.startswith("Summary for") for s in symbols)

    def test_single_batch_no_threadpool(self):
        """Single batch skips ThreadPoolExecutor overhead."""
        from jcodemunch_mcp.summarizer.batch_summarize import BaseSummarizer

        summarizer = BaseSummarizer()
        summarizer.client = True
        summarizer.called = False

        def _tracking_batch(batch):
            summarizer.called = True
            for sym in batch:
                sym.summary = "done"

        summarizer._summarize_one_batch = _tracking_batch

        symbols = self._make_symbols(1)
        with patch.dict("os.environ", {"JCODEMUNCH_SUMMARIZER_CONCURRENCY": "4"}):
            summarizer.summarize_batch(symbols, batch_size=10)

        assert summarizer.called
        assert symbols[0].summary == "done"

    def test_concurrency_env_var_sequential(self):
        """JCODEMUNCH_SUMMARIZER_CONCURRENCY=1 forces sequential processing."""
        from jcodemunch_mcp.summarizer.batch_summarize import BaseSummarizer

        summarizer = BaseSummarizer()
        summarizer.client = True
        summarizer.batch_count = 0

        def _counting_batch(batch):
            summarizer.batch_count += 1
            for sym in batch:
                sym.summary = "done"

        summarizer._summarize_one_batch = _counting_batch

        symbols = self._make_symbols(15)
        with patch.dict("os.environ", {"JCODEMUNCH_SUMMARIZER_CONCURRENCY": "1"}):
            summarizer.summarize_batch(symbols, batch_size=10)

        assert summarizer.batch_count == 2
        assert all(s.summary == "done" for s in symbols)
