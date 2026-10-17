"""The committed schema baseline is what the code publishes today (LEDGER L-187).

`benchmarks/schema_baseline.json` is quoted as the authority on what the tool
list costs (CLAUDE.md: "Quote `benchmarks/schema_baseline.json`, never a figure
from the harness"). `test_schema_budget.py` checks it only as a ceiling: live
may not exceed it by more than `schema.drift_tolerance`. A shrink never fails,
and growth inside the tolerance never fails, so the file can disagree with the
code while every test passes. It did: #1081 demoted the `kind` enum under
`compact_schemas` and changed six of the eight keys, and nothing noticed, because
DoD 4's surface diff sees tool names and descriptions, not input schemas.

This test captures every key the way `capture_schema_baseline.py` does and
requires equality. On a failure, regenerate with the script from an EMPTY
`CODE_INDEX_PATH`: the script measures through the global config, so a box whose
config disables tools or overrides descriptions writes values this test, which
runs under conftest's reset config, rejects. State the token delta in the
CHANGELOG (DoD 4). Stage the file before `harness bench`, whose own capture step
ends with `git checkout` on it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / "benchmarks" / "schema_baseline.json"
SCRIPT = REPO_ROOT / "benchmarks" / "harness" / "capture_schema_baseline.py"

_REGENERATE = (
    "Regenerate from an empty config dir (cmd.exe, with your own empty directory's absolute path: "
    "`set CODE_INDEX_PATH=C:\\temp\\empty-index` then "
    "`set PYTHONPATH=src` then `python benchmarks/harness/capture_schema_baseline.py`; bash: "
    "`CODE_INDEX_PATH=$(mktemp -d) PYTHONPATH=src python benchmarks/harness/capture_schema_baseline.py`), "
    "because a configured box writes values this test rejects. Then repin its checksum with "
    "`python -m harness corpora --pin` (the fast tier compares it). State the token delta in the CHANGELOG "
    "(DoD 4), and stage the file before running `harness bench`, which restores it with `git checkout`."
)

_HAS_TIKTOKEN = importlib.util.find_spec("tiktoken") is not None


def _capture_module():
    spec = importlib.util.spec_from_file_location("capture_schema_baseline", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(not _HAS_TIKTOKEN, reason="tiktoken not installed")
def test_the_committed_baseline_equals_a_live_capture():
    live = _capture_module().measure()  # restores the config keys it sets
    committed = json.loads(BASELINE.read_text(encoding="utf-8"))
    differ = {k: (committed.get(k), live.get(k)) for k in sorted(set(committed) | set(live)) if committed.get(k) != live.get(k)}
    assert not differ, (
        "benchmarks/schema_baseline.json disagrees with what the code publishes "
        "(key: committed, live): " + ", ".join(f"{k}: {c}, {v}" for k, (c, v) in differ.items())
        + ". " + _REGENERATE
    )
