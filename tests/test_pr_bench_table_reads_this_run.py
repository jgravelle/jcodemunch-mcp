"""The PR bench table never compares the base ref's artifact with itself
(workflow FINDINGS W-52).

`/feature` step 7 ran `python -m harness bench --offline --summary ...` and
then `scripts/pr_bench_comment.py --results harness/results/latest.json`. A
bench run without `--write-results` leaves that tracked file alone (W-29,
harness F-23), so on a branch `--results` named the copy the base ref holds
and every row read base = PR, delta `+0`, `PASS`: a table of zeros by
construction, pasted into a PR as evidence. Three halves, each pinned here:
the renderer refuses the base's own artifact, the harness can write this
run's result to a caller's path without touching the tracked file, and no
command pairs a non-writing bench run with a table read from the tracked file.
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
hm = importlib.import_module("harness.__main__")
pbc = importlib.import_module("pr_bench_comment")

TRACKED = "harness/results/latest.json"
LATENCY_ID = "latency.search_symbols_warm_p95_ms"


def _result(value: float, date: str) -> dict:
    return {
        "schema": "jcm-harness-result/v1",
        "date": date,
        "env": {"commit": "0" * 40},
        "tiers": {"bench": {"seconds": 1.0, "ok": True}},
        "artifacts": {"self_latency": {LATENCY_ID: value}},
    }


@pytest.fixture
def base_artifact(monkeypatch):
    """The base ref's committed artifacts, supplied without running git."""
    base = _result(7.9, "2026-10-01T00:00:00")

    def fake_show(ref: str, path: str):
        return json.loads(json.dumps(base)) if path == TRACKED else None

    monkeypatch.setattr(pbc, "_git_show", fake_show)
    return base


def test_the_table_refuses_the_base_refs_own_artifact(base_artifact, tmp_path, capsys):
    results = tmp_path / "latest.json"
    results.write_text(json.dumps(base_artifact, indent=2) + "\n", encoding="utf-8")
    summary = tmp_path / "bench_table.md"

    rc = pbc.main(["--results", str(results), "--summary", str(summary)])

    out = capsys.readouterr()
    assert rc != 0
    assert not summary.exists(), "a refused table must not be written as evidence"
    assert "| threshold |" not in out.out
    assert "--results-out" in out.err


def test_the_table_renders_a_run_that_is_not_the_base(base_artifact, tmp_path, capsys):
    results = tmp_path / "latest.json"
    results.write_text(
        json.dumps(_result(9.4, "2026-10-11T00:00:00")), encoding="utf-8"
    )
    summary = tmp_path / "bench_table.md"

    rc = pbc.main(["--results", str(results), "--summary", str(summary)])

    assert rc == 0
    row = next(
        ln for ln in summary.read_text(encoding="utf-8").splitlines() if LATENCY_ID in ln
    )
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[3:6] == ["7.9", "9.4", "+1.5"]


def test_a_run_that_measures_the_same_values_on_another_day_is_not_refused(
    base_artifact, tmp_path
):
    """Equal VALUES are a legitimate result; only the same artifact is refused."""
    results = tmp_path / "latest.json"
    results.write_text(
        json.dumps(_result(7.9, "2026-10-11T00:00:00")), encoding="utf-8"
    )
    assert pbc.main(["--results", str(results)]) == 0


def test_no_base_artifact_is_not_a_refusal(monkeypatch, tmp_path):
    monkeypatch.setattr(pbc, "_git_show", lambda ref, path: None)
    results = tmp_path / "latest.json"
    results.write_text(
        json.dumps(_result(7.9, "2026-10-11T00:00:00")), encoding="utf-8"
    )
    assert pbc.main(["--results", str(results)]) == 0


@pytest.fixture
def stub_bench(monkeypatch, tmp_path):
    """A bench tier that measures nothing, with the tracked directory moved to
    one the test owns."""
    results_dir = tmp_path / "tracked_results"
    results_dir.mkdir()
    (results_dir / "latest.json").write_text('{"committed": true}\n', encoding="utf-8")
    monkeypatch.setattr(hm, "RESULTS_DIR", results_dir)

    def fake_bench(result, *, offline, write_results=False):
        result["tiers"]["bench"] = {"seconds": 0.0, "ok": True}
        result["artifacts"] = {"self_latency": {LATENCY_ID: 9.4}}
        return True

    monkeypatch.setattr(hm, "tier_bench", fake_bench)
    return results_dir


def test_results_out_writes_this_run_and_leaves_the_tracked_file(stub_bench, tmp_path):
    out = tmp_path / "evidence" / "latest_cur.json"

    rc = hm.main(["bench", "--offline", "--results-out", str(out)])

    assert rc == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["artifacts"]["self_latency"][LATENCY_ID] == 9.4
    assert (stub_bench / "latest.json").read_text(encoding="utf-8") == (
        '{"committed": true}\n'
    )


def test_write_results_still_writes_the_tracked_file(stub_bench):
    assert hm.main(["bench", "--offline", "--write-results"]) == 0
    tracked = json.loads((stub_bench / "latest.json").read_text(encoding="utf-8"))
    assert tracked["artifacts"]["self_latency"][LATENCY_ID] == 9.4


_BENCH_RE = re.compile(r"harness bench[^`\n]*")
_TABLE_RE = re.compile(r"pr_bench_comment\.py[^`\n]*--results\s+(\S+)")


def _joined(text: str) -> str:
    """Command text the way a reader runs it: a wrapped line is one line."""
    return re.sub(r"\s*\n\s+", " ", text)


def _unwritten_table_reads(text: str) -> list[str]:
    """Table commands that read the tracked file when the bench command before
    them (the nearest one above, or none) did not write it."""
    text = _joined(text)
    out = []
    for m in _TABLE_RE.finditer(text):
        if m.group(1).strip("`.,)") != TRACKED:
            continue
        before = _BENCH_RE.findall(text[: m.start()])
        if not before or "--write-results" not in before[-1]:
            out.append(TRACKED)
    return out


@pytest.mark.parametrize(
    "path",
    sorted(
        str(p.relative_to(REPO)).replace("\\", "/")
        for p in list((REPO / ".claude" / "commands").glob("*.md"))
        + [REPO / "docs" / "workflows" / "DESIGN.md"]
    ),
)
def test_no_command_tables_the_tracked_file_after_a_run_that_did_not_write_it(path):
    assert _unwritten_table_reads((REPO / path).read_text(encoding="utf-8")) == []


def test_the_command_scan_sees_the_defect_it_names():
    """Non-vacuity: the scan fires on step 7 as it stood, wrapped or not."""
    old = (
        "   `uv run python -m harness bench --offline --summary .claude/state/evidence/bench.md`\n"
        "   and then the per-criterion table against main:\n"
        "   `uv run python scripts/pr_bench_comment.py --base-ref origin/main\n"
        "   --results harness/results/latest.json --summary x.md`\n"
    )
    assert _unwritten_table_reads(old) == [TRACKED]
    assert _unwritten_table_reads(old.replace("--offline", "--offline --write-results")) == []
