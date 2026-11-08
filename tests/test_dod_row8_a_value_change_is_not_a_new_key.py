"""DoD item 8 asks for a basis beside a NEW published rate, not beside a changed value.

`dod_checklist.py` flagged every added `src/` line whose key looks like a rate
(`*_pct`, `*_rate`, `*_share`, `confidence`), and passed only if `_basis` or
`refus` appeared somewhere in the diff. A line that changes an existing key's
value is added in the diff too, so moving the provenance registry's
`average_pct` from 99.6 to 99.7 to match a new benchmark reference read as a
new number with no basis, and `gh pr create` was refused, although the entry
already carries `"basis": BASIS_MEASURED` beside it.

A key now counts as new only when the same hunk does not remove a line with
the same key. A new key without a basis still fails the row.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude" / "hooks"


@pytest.fixture(scope="module")
def dod():
    sys.path.insert(0, str(HOOKS))
    try:
        spec = importlib.util.spec_from_file_location("_wf_dod_row8", HOOKS / "dod_checklist.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(HOOKS))


VALUE_CHANGE = """diff --git a/src/jcodemunch_mcp/retrieval/provenance.py b/src/jcodemunch_mcp/retrieval/provenance.py
--- a/src/jcodemunch_mcp/retrieval/provenance.py
+++ b/src/jcodemunch_mcp/retrieval/provenance.py
@@ -94,7 +94,7 @@ CONFIDENCE_PROVENANCE: dict[str, dict] = {
 MEASURED: dict[str, dict] = {
     "token_reduction": {
-        "average_pct": 99.6,
+        "average_pct": 99.7,
         "task_runs": 15,
         "tokenizer": "cl100k_base",
         "basis": BASIS_MEASURED,
"""


def _added(key: str) -> str:
    return (
        "diff --git a/src/jcodemunch_mcp/tools/x.py b/src/jcodemunch_mcp/tools/x.py\n"
        "--- a/src/jcodemunch_mcp/tools/x.py\n"
        "+++ b/src/jcodemunch_mcp/tools/x.py\n"
        "@@ -10,3 +10,4 @@ def run():\n"
        '     out = {"files": n}\n'
        f'+    out["{key}"] = hits / total\n'
        f'+    meta = {{"{key}": round(hits / total, 3)}}\n'
        "     return out\n"
    )


def test_a_changed_value_on_an_existing_key_is_not_a_new_key(dod):
    assert dod.new_rate_keys(VALUE_CHANGE) == []
    verdict, _ = dod.row8_verdict(VALUE_CHANGE)
    assert verdict == "n.a."


@pytest.mark.parametrize("key", ["hit_pct", "cache_rate", "head_share", "confidence"])
def test_a_new_rate_key_without_a_basis_still_fails(dod, key):
    diff = _added(key)
    assert dod.new_rate_keys(diff) == [key]
    verdict, note = dod.row8_verdict(diff)
    assert verdict == "unmet"
    assert key in note


def test_a_new_rate_key_with_a_basis_passes(dod):
    diff = _added("hit_pct").replace("     return out\n", '+    out["hit_pct_basis"] = "raw_key_presence"\n     return out\n')
    assert dod.row8_verdict(diff)[0] == "met"


def test_a_removal_in_another_hunk_does_not_excuse_a_new_key(dod):
    # The same key removed in a different hunk (or file) is a different line:
    # it moved or was renamed somewhere else, and the added one is still new.
    other_hunk = (
        "diff --git a/src/jcodemunch_mcp/tools/y.py b/src/jcodemunch_mcp/tools/y.py\n"
        "--- a/src/jcodemunch_mcp/tools/y.py\n"
        "+++ b/src/jcodemunch_mcp/tools/y.py\n"
        "@@ -5,3 +5,2 @@ def old():\n"
        '-    out = {"hit_pct": 1.0}\n'
        "     return out\n"
    )
    diff = other_hunk + _added("hit_pct")
    assert dod.new_rate_keys(diff) == ["hit_pct"]
    assert dod.row8_verdict(diff)[0] == "unmet"


def test_a_hunk_that_changes_one_key_and_adds_another_flags_only_the_new_one(dod):
    diff = VALUE_CHANGE.replace(
        '+        "average_pct": 99.7,\n',
        '+        "average_pct": 99.7,\n+        "median_pct": 96.2,\n',
    )
    assert dod.new_rate_keys(diff) == ["median_pct"]
