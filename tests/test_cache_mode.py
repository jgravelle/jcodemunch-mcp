"""Tests for the metadata-only cache mode (P1.4).

Covers:
- Default ``cache_mode="full"`` continues to write file bodies via
  ``_write_cached_text``.
- ``cache_mode="metadata_only"`` makes ``_write_cached_text`` a no-op:
  the symbol table is still written by the caller, but no body file
  appears on disk under ``~/.code-index/<repo>/bodies/``.
- ``cache_mode="metadata_only"`` config flag survives ``config --upgrade``.

The body-write site is gated inside ``_write_cached_text`` itself so the
test directly exercises that method rather than running a full indexing
pipeline. That keeps the test focused and fast; the broader indexing
suite continues to cover the call sites.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from jcodemunch_mcp import config as _config
from jcodemunch_mcp.config import apply_share_savings, set_bool_key
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore


class TestCacheModeGate:
    """Directly exercise the body-write gate."""

    @pytest.fixture(autouse=True)
    def _reset_config(self):
        """Each test runs with a clean config slate."""
        original = _config._GLOBAL_CONFIG.copy() if hasattr(_config, "_GLOBAL_CONFIG") else None
        yield
        if original is not None:
            _config._GLOBAL_CONFIG.clear()
            _config._GLOBAL_CONFIG.update(original)

    def test_full_mode_writes_body(self):
        """Default cache_mode='full' writes the file as expected."""
        # Force the global config to known state.
        if hasattr(_config, "_GLOBAL_CONFIG"):
            _config._GLOBAL_CONFIG["cache_mode"] = "full"

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteIndexStore(base_path=tmp)
            target = Path(tmp) / "body.txt"
            store._write_cached_text(target, "hello world")

            assert target.exists()
            assert target.read_text(encoding="utf-8") == "hello world"

    def test_metadata_only_mode_skips_body(self):
        """cache_mode='metadata_only' makes _write_cached_text a no-op."""
        if hasattr(_config, "_GLOBAL_CONFIG"):
            _config._GLOBAL_CONFIG["cache_mode"] = "metadata_only"

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteIndexStore(base_path=tmp)
            target = Path(tmp) / "body.txt"
            store._write_cached_text(target, "hello world")

            # No body file should exist.
            assert not target.exists()

    def test_default_is_full(self):
        """Unset cache_mode key falls back to 'full' behavior."""
        if hasattr(_config, "_GLOBAL_CONFIG"):
            _config._GLOBAL_CONFIG.pop("cache_mode", None)

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteIndexStore(base_path=tmp)
            target = Path(tmp) / "body.txt"
            store._write_cached_text(target, "default")

            assert target.exists()


class TestCacheModeUpgradePreservation:
    """User-set cache_mode survives config --upgrade."""

    def test_metadata_only_survives_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)
            # Create a config.jsonc with cache_mode=metadata_only.
            apply_share_savings(True, storage)  # creates the file from template
            path = storage / "config.jsonc"
            content = path.read_text(encoding="utf-8")
            # Use the generic set_bool_key won't work for strings; do a direct
            # template injection via the same regex shape.
            import re
            updated = re.sub(
                r'^(\s*)(?://\s*)?"cache_mode"\s*:\s*"[a-zA-Z_]+"\s*,?\s*$',
                r'\1"cache_mode": "metadata_only",',
                content,
                count=1,
                flags=re.MULTILINE,
            )
            path.write_text(updated, encoding="utf-8")
            assert '"cache_mode": "metadata_only",' in path.read_text(encoding="utf-8")

            _config.upgrade_config(path)

            after = path.read_text(encoding="utf-8")
            assert '"cache_mode": "metadata_only",' in after
            assert after.count('"cache_mode":') == 1
