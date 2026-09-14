"""An audit-record field has one type however the workflow quoted it (#690).

`ledger.py write --field k=v` passes every value through `json.loads`, so a
shell writer's bare `kill_switch_state=true` was stored as a JSON boolean
while an inline-Python writer's `json.dumps("true")` was stored as a string.
The digest compares consecutive records with `!=`, and W37 (#687) reported
12 kill-switch flips that never happened. `item=685` has the same shape
(13 int, 70 str on the ledger branch).

Red arms: the write CLI stores a boolean or an int; the digest reports a flip
between `True` and `"true"` already on the ledger branch; a real flip across
the two spellings goes unreported.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INBOUND = ROOT / ".github" / "inbound"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, INBOUND / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ledger = _load("ledger")
dg = _load("digest")


def _write(tmp_path: Path, *fields: str) -> dict:
    out = tmp_path / "record.json"
    args = ["write", str(out), "--field", "job=inbound-intake", "--field", "outcome=acted"]
    for f in fields:
        args += ["--field", f]
    assert ledger.main(args) == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_shell_spelling_and_the_python_spelling_store_the_same_value(tmp_path):
    shell = _write(tmp_path, "kill_switch_state=true", "item=685")
    python = _write(tmp_path, f"kill_switch_state={json.dumps('true')}", f"item={json.dumps('685')}")
    assert shell["kill_switch_state"] == python["kill_switch_state"] == "true"
    assert shell["item"] == python["item"] == "685"


def test_false_and_absent_keep_their_meaning(tmp_path):
    assert _write(tmp_path, "kill_switch_state=false")["kill_switch_state"] == "false"
    assert _write(tmp_path, "kill_switch_state=null")["kill_switch_state"] is None
    assert _write(tmp_path)["kill_switch_state"] is None
    # a mis-set variable is recorded as it was read, never coerced to on/off
    assert _write(tmp_path, "kill_switch_state=True")["kill_switch_state"] == "True"


def test_make_record_normalises_for_in_process_callers():
    rec = ledger.make_record(job="x", item=12, outcome="skipped", kill_switch_state=True)
    assert rec["kill_switch_state"] == "true" and rec["item"] == "12"


def _row(state, at):
    return {"job": "j", "outcome": "acted", "item": "1", "recorded_at": at,
            "kill_switch_state": state, "classification": {}, "run_id": at, "cost_usd": None}


def test_the_digest_sees_no_flip_between_the_two_spellings_already_on_the_ledger():
    rows = [_row(True, "2026-09-07T07:06:00+00:00"), _row("true", "2026-09-07T07:06:15+00:00"),
            _row(True, "2026-09-07T07:38:00+00:00")]
    assert dg.summarise(rows, {}, [], "o/r")["kill_switch_flips"] == []


def test_a_real_flip_across_the_two_spellings_is_still_reported():
    rows = [_row(True, "2026-09-07T07:00:00+00:00"), _row("false", "2026-09-07T08:00:00+00:00"),
            _row("true", "2026-09-07T09:00:00+00:00")]
    flips = dg.summarise(rows, {}, [], "o/r")["kill_switch_flips"]
    assert [(f["from"], f["to"]) for f in flips] == [("true", "false"), ("false", "true")]
