"""Tests for token tracker behavior and path consistency."""

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp" / "storage" / "token_tracker.py"
SPEC = importlib.util.spec_from_file_location("token_tracker_module", MODULE_PATH)
TOKEN_TRACKER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(TOKEN_TRACKER)


def test_savings_report_uses_code_index_path_env(monkeypatch, tmp_path):
    """When CODE_INDEX_PATH is set, record/report should use the same savings file."""
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))

    total = TOKEN_TRACKER.record_savings(123)
    report = TOKEN_TRACKER.get_savings_report()

    assert total == 123
    assert report["total_tokens_saved"] == 123
    assert report["savings_file"] == str(tmp_path / "_savings.json")


def test_record_savings_reads_utf8_bom_file(tmp_path):
    """Existing UTF-8 BOM JSON should be readable before incrementing totals."""
    savings_file = tmp_path / "_savings.json"
    savings_file.write_bytes('{"total_tokens_saved": 10}'.encode("utf-8-sig"))

    total = TOKEN_TRACKER.record_savings(5, str(tmp_path))

    assert total == 15
    assert TOKEN_TRACKER.get_total_saved(str(tmp_path)) == 15


def test_record_savings_reads_cp1252_file(tmp_path):
    """Legacy Windows cp1252 JSON should not reset totals on read."""
    savings_file = tmp_path / "_savings.json"
    # Include a cp1252-only byte in anon_id so UTF-8 decode fails.
    savings_file.write_bytes(b'{"total_tokens_saved": 10, "anon_id": "caf\xe9"}')

    total = TOKEN_TRACKER.record_savings(5, str(tmp_path))

    assert total == 15
    assert TOKEN_TRACKER.get_total_saved(str(tmp_path)) == 15
