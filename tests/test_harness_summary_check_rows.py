"""Every check a harness tier runs leaves a row in its summary, not only the Floors.

The fast tier fails on four checks that are not Floors: the pinned corpora
checksums, the tiktoken asset warm-up, the pytest exit code and `ruff check
src/`. `summary_markdown` rendered only Floor verdict lines, so a corpus
MISMATCH printed to the console and left `fast.md` showing nothing but its
timing rows. The hooks read a summary whose only FAIL rows are timing rows as
CI-judged and let it through, so the mismatch passed every local gate.

Each such check now prints one `check <id> PASS|FAIL <detail>` line, and the
summary renders it in the same row shape as a Floor, so a failing check is a
`**FAIL**` row whose id is not a timing id.
"""

from __future__ import annotations

import importlib
import io
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / ".claude" / "hooks"
sys.path.insert(0, str(REPO))

hm = importlib.import_module("harness.__main__")


def _timing_only_failures(summary: str) -> list[str]:
    sys.path.insert(0, str(HOOKS))
    try:
        import _common
    finally:
        sys.path.remove(str(HOOKS))
    return _common.timing_only_failures(summary)


_FAST_CHECKS = ("corpora", "assets", "pytest", "ruff")


def _run_fast(monkeypatch, failing: str | None) -> tuple[bool, str]:
    """tier_fast with every external step stubbed; `failing` names the one that fails."""
    monkeypatch.setattr(
        hm.C,
        "verify",
        lambda: ["token_tasks: expected abc, got def"] if failing == "corpora" else [],
    )
    monkeypatch.setattr(hm, "warm_assets", lambda: failing != "assets")

    def fake_run(cmd, **kw):
        if "pytest" in cmd:
            if failing == "pytest":
                return (
                    1,
                    "FAILED tests/test_x.py::test_y - boom\n1 failed, 4 passed in 1.00s\n",
                    1.0,
                )
            return 0, "5 passed in 1.00s\n", 1.0
        if "ruff" in cmd:
            if failing == "ruff":
                return 1, "src/x.py:1:1: F401 unused\nFound 1 error.\n", 0.1
            return 0, "All checks passed!\n", 0.1
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr(hm, "_run", fake_run)
    monkeypatch.setattr(hm, "offline_checks", lambda: (True, []))
    monkeypatch.setattr(hm, "_annotate_failure", lambda *a, **k: None)
    monkeypatch.setattr(hm, "_record_failure_report", lambda *a, **k: None)
    tee = hm._Tee(io.StringIO())
    monkeypatch.setattr(sys, "stdout", tee)
    result = {"tiers": {}}
    ok = hm.tier_fast(result)
    monkeypatch.undo()
    # The local timing row fails beside the real failure, the shape that
    # hid the corpus mismatch.
    tee.write(hm.T.verdict_line("suite.fast_seconds", 999.0) + "\n")
    return ok, tee.summary_markdown("harness fast", ok=False)


def _rows(summary: str) -> dict[str, str]:
    out = {}
    for ln in summary.splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0].startswith("`check."):
            out[cells[0].strip("`")] = cells[-1]
    return out


@pytest.mark.parametrize("failing", _FAST_CHECKS)
def test_a_failing_fast_check_is_a_fail_row_the_hooks_do_not_wave_through(
    monkeypatch, failing
):
    ok, summary = _run_fast(monkeypatch, failing)
    assert ok is False
    rows = _rows(summary)
    failed = [k for k, v in rows.items() if v == "**FAIL**"]
    assert len(failed) == 1 and failing in failed[0], (failing, rows)
    assert _timing_only_failures(summary) == [], summary


def test_every_fast_check_has_a_row_when_all_pass(monkeypatch):
    ok, summary = _run_fast(monkeypatch, None)
    assert ok is True
    rows = _rows(summary)
    assert len(rows) == len(_FAST_CHECKS), rows
    assert all(v == "PASS" for v in rows.values()), rows
    # Only the timing row fails, so the hooks may defer it to CI.
    assert _timing_only_failures(summary) == ["suite.fast_seconds"]


def test_a_check_line_renders_its_detail():
    tee = hm._Tee(io.StringIO())
    tee.write(
        hm._check_line("check.corpora_checksums", False, "1 mismatched: token_tasks")
        + "\n"
    )
    md = tee.summary_markdown("harness fast", ok=False)
    assert "1 mismatched: token_tasks" in md
    assert _rows(md) == {"check.corpora_checksums": "**FAIL**"}
