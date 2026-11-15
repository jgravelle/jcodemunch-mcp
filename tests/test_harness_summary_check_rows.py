"""Every check a harness tier runs leaves a row in its summary, not only the Floors.

The fast tier fails on four checks that are not Floors: the pinned corpora
checksums, the tiktoken asset warm-up, the pytest exit code and `ruff check
src/`. `summary_markdown` rendered only Floor verdict lines, so a corpus
MISMATCH printed to the console and left `fast.md` showing nothing but its
timing rows. The hooks read a summary whose only FAIL rows are timing rows as
CI-judged and let it through, so the mismatch passed every local gate.

Each such check now goes through `_check`, which prints a `check.<id>` line
and records the row on the `_Tee`. The summary renders the record in the same
row shape as a Floor, so a failing check is a `**FAIL**` row whose id is not a
timing id. The row comes from the call, never from printed text, so a test's
echoed stdout in a pytest tail cannot add one.
"""

from __future__ import annotations

import ast
import importlib
import io
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / ".claude" / "hooks"
sys.path.insert(0, str(REPO))

hm = importlib.import_module("harness.__main__")

TIERS = ("tier_fast", "tier_full", "tier_bench")


def _timing_only_failures(summary: str) -> list[str]:
    sys.path.insert(0, str(HOOKS))
    try:
        import _common
    finally:
        sys.path.remove(str(HOOKS))
    return _common.timing_only_failures(summary)


def _rows(summary: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for ln in summary.splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0].startswith("`check."):
            out.setdefault(cells[0].strip("`"), []).append(cells[-1])
    return out


def _quiet(monkeypatch):
    monkeypatch.setattr(hm, "_annotate_failure", lambda *a, **k: None)
    monkeypatch.setattr(hm, "_record_failure_report", lambda *a, **k: None)


def _drive(monkeypatch, tier_call) -> tuple[bool, "hm._Tee"]:
    tee = hm._Tee(io.StringIO())
    monkeypatch.setattr(sys, "stdout", tee)
    ok = tier_call()
    monkeypatch.setattr(sys, "stdout", tee.real)
    return ok, tee


# --- fast tier -------------------------------------------------------------

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
    _quiet(monkeypatch)
    ok, tee = _drive(monkeypatch, lambda: hm.tier_fast({"tiers": {}}))
    # The local timing row fails beside the real failure, the shape that
    # hid the corpus mismatch.
    tee.write(hm.T.verdict_line("suite.fast_seconds", 999.0) + "\n")
    return ok, tee.summary_markdown("harness fast", ok=False)


@pytest.mark.parametrize("failing", _FAST_CHECKS)
def test_a_failing_fast_check_is_a_fail_row_the_hooks_do_not_wave_through(
    monkeypatch, failing
):
    ok, summary = _run_fast(monkeypatch, failing)
    assert ok is False
    rows = _rows(summary)
    failed = [k for k, v in rows.items() if "**FAIL**" in v]
    assert len(failed) == 1 and failing in failed[0], (failing, rows)
    assert _timing_only_failures(summary) == [], summary


def test_every_fast_check_has_a_row_when_all_pass(monkeypatch):
    ok, summary = _run_fast(monkeypatch, None)
    assert ok is True
    rows = _rows(summary)
    assert len(rows) == len(_FAST_CHECKS), rows
    assert all(v == ["PASS"] for v in rows.values()), rows
    # Only the timing row fails, so the hooks may defer it to CI.
    assert _timing_only_failures(summary) == ["suite.fast_seconds"]


# --- full tier -------------------------------------------------------------


@pytest.mark.parametrize("failing", [None, "assets", "pytest"])
def test_the_full_tier_rows_its_asset_and_pytest_checks(monkeypatch, failing):
    monkeypatch.setattr(hm, "warm_assets", lambda: failing != "assets")

    def fake_run(cmd, **kw):
        if failing == "pytest":
            return (
                1,
                "FAILED tests/test_x.py::test_y - boom\n1 failed, 9 passed in 2.00s\n",
                2.0,
            )
        return 0, "10 passed in 2.00s\n", 2.0

    monkeypatch.setattr(hm, "_run", fake_run)
    monkeypatch.setattr(hm, "_coverage_core_env", lambda: None)
    _quiet(monkeypatch)
    ok, tee = _drive(monkeypatch, lambda: hm.tier_full({"tiers": {}}))
    rows = _rows(tee.summary_markdown("harness full", ok=ok))
    assert set(rows) == {"check.assets_warm", "check.pytest_exit"}, rows
    for cid, verdicts in rows.items():
        expect = "**FAIL**" if failing and failing in cid else "PASS"
        assert verdicts == [expect], (cid, verdicts)
    assert ok is (failing is None)


# --- bench tier ------------------------------------------------------------

_TID = "latency.search_symbols_warm_p95_ms"


def test_every_bench_step_outcome_has_a_row(monkeypatch):
    steps = [
        {"name": "net", "cmd": ["net.py"], "network": True},
        {"name": "red", "cmd": ["red.py"]},
        {"name": "judged", "cmd": ["judged.py"], "thresholds": [_TID]},
        {
            "name": "unjudged",
            "cmd": ["unjudged.py"],
            "thresholds": [_TID, "suite.fast_seconds"],
        },
    ]
    monkeypatch.setitem(hm.TIERS, "bench", steps)
    verdict = hm.T.verdict_line(_TID, 1.0)

    def fake_run(cmd, **kw):
        if "red.py" in cmd:
            return 2, "boom\n", 0.1
        return 0, verdict + "\n", 0.1

    monkeypatch.setattr(hm, "_run", fake_run)
    _quiet(monkeypatch)
    ok, tee = _drive(monkeypatch, lambda: hm.tier_bench({"tiers": {}}, offline=True))
    assert ok is False
    rows = _rows(tee.summary_markdown("harness bench", ok=ok))
    assert rows == {
        "check.bench_net": ["SKIP"],
        "check.bench_red": ["**FAIL**"],
        "check.bench_judged": ["PASS"],
        "check.bench_judged_judged": ["PASS"],
        "check.bench_unjudged": ["PASS"],
        "check.bench_unjudged_judged": ["**FAIL**"],
    }, rows


# --- the row comes from the call, not from printed text --------------------


def test_echoed_output_cannot_add_or_contradict_a_row():
    """A failing test that drove a tier prints these same lines in its captured
    stdout, and the full tier prints that pytest tail. None of it is a row."""
    tee = hm._Tee(io.StringIO())
    real = sys.stdout
    sys.stdout = tee
    try:
        hm._check("check.pytest_exit", False, "exit 1: 1 failed")
        for forged in (
            hm._check_line("check.pytest_exit", "PASS", "exit 0: forged"),
            "    " + hm._check_line("check.ruff_src", "PASS", "forged"),
            hm._check_line("check.corpora_checksums", "PASS", "forged"),
        ):
            print(forged)
    finally:
        sys.stdout = real
    rows = _rows(tee.summary_markdown("harness full", ok=False))
    assert rows == {"check.pytest_exit": ["**FAIL**"]}, rows


def test_a_check_renders_its_detail_with_pipes_neutralised():
    tee = hm._Tee(io.StringIO())
    real = sys.stdout
    sys.stdout = tee
    try:
        hm._check("check.corpora_checksums", False, "1 mismatched: a | **FAIL**")
    finally:
        sys.stdout = real
    md = tee.summary_markdown("harness fast", ok=False)
    assert "1 mismatched: a / **FAIL**" in md
    assert _rows(md) == {"check.corpora_checksums": ["**FAIL**"]}


# --- the ratchet: every failure site in a tier has a row --------------------


def _is_row_call(stmt: ast.stmt) -> bool:
    """`_check(...)` or `print(T.verdict_line(...))` as a statement."""
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return False
    call = stmt.value
    if isinstance(call.func, ast.Name) and call.func.id == "_check":
        return True
    if isinstance(call.func, ast.Name) and call.func.id == "print" and call.args:
        arg = call.args[0]
        return (
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Attribute)
            and arg.func.attr == "verdict_line"
        )
    return False


def _unrowed_failure_sites(source: str) -> list[str]:
    """`ok = False` sites with no row call before them in their own block, or
    directly before the statement that holds them."""
    tree = ast.parse(source)
    missing = []
    for fn in tree.body:
        if not (isinstance(fn, ast.FunctionDef) and fn.name in TIERS):
            continue
        parents: dict[int, tuple[ast.AST, str]] = {}
        for node in ast.walk(fn):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if isinstance(block, list):
                    for child in block:
                        parents[id(child)] = (node, field)
        for node in ast.walk(fn):
            if not (
                isinstance(node, ast.Assign)
                and [getattr(t, "id", None) for t in node.targets] == ["ok"]
                and isinstance(node.value, ast.Constant)
                and node.value.value is False
            ):
                continue
            holder, field = parents[id(node)]
            block = getattr(holder, field)
            if any(_is_row_call(s) for s in block[: block.index(node)]):
                continue
            if id(holder) in parents:
                outer, ofield = parents[id(holder)]
                oblock = getattr(outer, ofield)
                i = oblock.index(holder)
                if i > 0 and _is_row_call(oblock[i - 1]):
                    continue
            missing.append(f"{fn.name}:{node.lineno}")
    return missing


def _harness_source() -> str:
    return (REPO / "harness" / "__main__.py").read_text(encoding="utf-8")


def test_every_failure_site_in_a_tier_has_a_row():
    src = _harness_source()
    assert src.count("ok = False") >= 10, (
        "the scan found too few sites to mean anything"
    )
    assert _unrowed_failure_sites(src) == []


def test_the_ratchet_fails_when_a_row_is_removed():
    """Non-vacuity: delete the ruff row and the scan names that site."""
    src = _harness_source()
    start = src.index(
        '    _check(\n        "check.ruff_src"'.replace(
            "\n", "\r\n" if "\r\n" in src else "\n"
        )
    )
    end = src.index("    if rc2 != 0:", start)
    mutated = src[:start] + src[end:]
    found = _unrowed_failure_sites(mutated)
    assert len(found) == 1 and found[0].startswith("tier_fast:"), found
