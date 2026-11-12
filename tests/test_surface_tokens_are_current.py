"""The committed surface token record is what the code publishes today.

`benchmarks/surface_tokens.json` records what each tool surface puts in front
of the model for a deferring and a non-deferring client, with the basis of
every figure written beside it. Figures quoted from it in the CHANGELOG or on
the site must be reproducible, so this test captures it live (under conftest's
reset config) and requires equality, the rule `test_schema_baseline_is_current.py`
applies to the schema baseline. The byte pin on the counter surface
(`test_counter_surface_stability.py`) says the prefix did not move; this says
what it costs in tokens.

On a failure, regenerate from an EMPTY config dir:
`CODE_INDEX_PATH=$(mktemp -d) PYTHONPATH=src python benchmarks/harness/capture_surface_tokens.py`.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORD = REPO_ROOT / "benchmarks" / "surface_tokens.json"
SCRIPT = REPO_ROOT / "benchmarks" / "harness" / "capture_surface_tokens.py"

_HAS_TIKTOKEN = importlib.util.find_spec("tiktoken") is not None


def _capture():
    spec = importlib.util.spec_from_file_location("capture_surface_tokens", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(not _HAS_TIKTOKEN, reason="tiktoken not installed")
def test_the_committed_record_equals_a_live_capture():
    live = _capture().measure()
    committed = json.loads(RECORD.read_text(encoding="utf-8"))
    assert committed == live, (
        "benchmarks/surface_tokens.json disagrees with a live capture. Regenerate it from an "
        "empty CODE_INDEX_PATH with benchmarks/harness/capture_surface_tokens.py, and correct "
        "any CHANGELOG or site figure quoted from the old values."
    )


def test_every_figure_has_its_basis_written_down():
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    basis = record["basis"]
    for key in (
        "tokenizer",
        "schema",
        "deferred_names",
        "instructions",
        "deferring_total",
        "non_deferring_total",
        "config",
    ):
        assert basis.get(key), key
    for surface in ("counter", "full"):
        row = record[surface]
        assert row["deferring_total"] == (
            row["always_loaded_schema_tokens"]
            + row["deferred_name_tokens"]
            + row["instructions_tokens"]
        ), surface
        assert row["always_loaded_schema_tokens"] == sum(
            row["always_loaded"].values()
        ), surface
        assert row["published"] == len(row["always_loaded"]) + row["deferred"], surface
        per_tool = row["schema_tokens_per_tool"]
        assert len(per_tool) == row["published"], surface
        assert (
            sum(per_tool.values()) + row["instructions_tokens"]
            == row["non_deferring_total"]
        ), surface


def test_the_deferring_view_is_the_cheaper_one():
    """A deferring client never gets more than a client that loads every schema."""
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    for surface in ("counter", "full"):
        assert (
            record[surface]["deferring_total"] <= record[surface]["non_deferring_total"]
        ), surface
