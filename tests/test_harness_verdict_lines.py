"""The bench summary carries every Floor verdict a step printed, not the last three
lines of its stdout (harness FINDINGS F-24).

`tier_bench` re-printed only a step's three-line tail into the verdict tee, and
`benchmarks/self_latency/measure.py` prints its six verdict lines last, so
`bench.md` showed two of the six Floors declared on the step. The tier's exit
code still carried a FAIL; the summary table a reviewer reads under DoD 10 did
not. Every `_VERDICT_RE` line of a step's stdout is re-printed now, and the
three-line tail stays for the log.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
hm = importlib.import_module("harness.__main__")

IDS = [
    "latency.search_symbols_warm_p95_ms",
    "latency.search_text_warm_p95_ms",
    "latency.get_symbol_source_warm_p95_ms",
    "latency.get_file_outline_warm_p95_ms",
    "index.cold_self_seconds",
    "index.one_file_reindex_ms",
]


@pytest.fixture
def six_verdict_step(tmp_path, monkeypatch):
    """A step that prints six verdict lines, then two lines that are not verdicts."""
    script = tmp_path / "measure.py"
    lines = [f"print({tid!r} + '  crit 5  floor <= 27  observed 3.1  PASS')" for tid in IDS]
    lines += ["print('wrote nothing')", "print('done')"]
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(hm, "REPO", tmp_path)
    monkeypatch.setattr(hm, "TIERS", {"bench": [{"name": "self_latency", "cmd": [str(script)], "thresholds": IDS}]})
    return script


def _verdict_ids(text: str) -> list[str]:
    return [m.group(1) for m in (hm._VERDICT_RE.match(ln.strip()) for ln in text.splitlines()) if m]


def test_every_verdict_line_a_step_prints_reaches_the_tee(six_verdict_step, capsys):
    result: dict = {"tiers": {}}
    assert hm.tier_bench(result, offline=True) is True
    printed = capsys.readouterr().out
    assert _verdict_ids(printed) == IDS, "the summary saw only the step's last three lines"


def test_a_verdict_line_is_printed_once(six_verdict_step, capsys):
    result: dict = {"tiers": {}}
    hm.tier_bench(result, offline=True)
    ids = _verdict_ids(capsys.readouterr().out)
    assert len(ids) == len(set(ids)), "a verdict in the tail was printed twice"


def test_the_record_keeps_the_three_line_tail(six_verdict_step):
    result: dict = {"tiers": {}}
    hm.tier_bench(result, offline=True)
    tail = result["artifacts"]["self_latency"]["tail"].splitlines()
    assert len(tail) == 3 and tail[-1] == "done"


def test_the_shipped_step_declares_every_floor_measure_prints():
    """The six ids the summary must carry are the step's own declaration, not this file's."""
    import json

    tiers = json.loads((REPO / "harness" / "tiers.json").read_text(encoding="utf-8"))
    step = next(s for s in tiers["bench"] if s["name"] == "self_latency")
    assert step["thresholds"] == IDS
