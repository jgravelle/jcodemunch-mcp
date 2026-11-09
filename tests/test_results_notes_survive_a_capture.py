"""The notes above results.md's run output survive a re-capture.

results.md is written whole by `run_benchmark.py --out`, so a note typed into it
(the 2026-11-08 methodology change) would be dropped by the next reference
capture. The note lives in `benchmarks/results_notes.md` and `render_markdown`
emits it under the title, so a capture writes it back byte for byte.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _harness():
    spec = importlib.util.spec_from_file_location(
        "_bench_harness_notes", ROOT / "benchmarks" / "harness" / "run_benchmark.py"
    )
    harness = importlib.util.module_from_spec(spec)
    sys.modules["_bench_harness_notes"] = harness
    spec.loader.exec_module(harness)
    return harness


def _results():
    def task(query, grep, jm):
        return {
            "query": query, "baseline_tokens": 10_000, "jmunch_tokens": jm,
            "reduction_pct": 99.0, "ratio": 10_000 / jm,
            "grep_baseline_tokens": grep, "grep_ratio": round(grep / jm, 2),
            "grep_reduction_pct": round((1 - jm / grep) * 100, 1),
            "search_tokens": 4, "fetch_tokens": 6, "hits_fetched": 3, "search_ms": 1.0,
        }

    return [{
        "repo": "o/n", "display": "n", "file_count": 3, "symbol_count": 9,
        "baseline_tokens": 10_000, "tasks": [task("a", 100, 10), task("b", 1000, 10)],
    }]


def test_a_capture_writes_the_notes_under_the_title(tmp_path, monkeypatch):
    h = _harness()
    notes = tmp_path / "results_notes.md"
    notes.write_bytes(b"**A note.** It says what changed.\r\n\r\nSecond paragraph.\n")  # a CRLF checkout
    monkeypatch.setattr(h, "RESULTS_NOTES_PATH", notes)
    lines = h.render_markdown(_results(), h.TOKENIZER).split("\n")
    assert lines[:6] == [
        "# jcodemunch-mcp -- Token Efficiency Benchmark", "",
        "**A note.** It says what changed.", "", "Second paragraph.", "",
    ]
    # A second capture over the first one's output writes the same text because
    # nothing in the rendering reads the results.md it is about to replace.
    # Record every file the render opens and check that none is a results.md.
    # Path.open is spied as well as open(): on Python 3.10 pathlib reads through
    # its own reference to io.open, which a patch of builtins.open never reaches.
    import builtins
    import pathlib

    opened = []
    real_open, real_path_open = builtins.open, pathlib.Path.open

    def spy(path, *a, **kw):
        opened.append(Path(path).name if not isinstance(path, int) else str(path))
        return real_open(path, *a, **kw)

    def path_spy(self, *a, **kw):
        opened.append(self.name)
        return real_path_open(self, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(pathlib.Path, "open", path_spy)
    h.render_markdown(_results(), h.TOKENIZER)
    monkeypatch.setattr(builtins, "open", real_open)
    monkeypatch.setattr(pathlib.Path, "open", real_path_open)
    assert "results_notes.md" in opened, opened  # the spy sees the render's reads
    assert "results.md" not in opened, opened


def test_no_notes_file_renders_as_before(tmp_path, monkeypatch):
    h = _harness()
    monkeypatch.setattr(h, "RESULTS_NOTES_PATH", tmp_path / "absent.md")
    lines = h.render_markdown(_results(), h.TOKENIZER).split("\n")
    assert lines[:3] == ["# jcodemunch-mcp -- Token Efficiency Benchmark", "", "**Tokenizer:** `cl100k_base` (tiktoken)  "]


def test_the_committed_results_md_carries_the_committed_notes():
    h = _harness()
    notes = h.results_notes_lines()
    assert notes, "benchmarks/results_notes.md is empty or missing"
    committed = (ROOT / "benchmarks" / "results.md").read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    assert committed[2:2 + len(notes)] == notes, "results.md does not open with results_notes.md; re-capture or re-sync it"
