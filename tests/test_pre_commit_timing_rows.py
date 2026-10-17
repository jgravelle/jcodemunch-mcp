"""pre_commit does not refuse a commit whose fast tier failed ONLY on timing rows.

jjg ruled on 2026-10-16 that CI judges every timing row, and #1119 taught
`pre_pr`, `run_full` and the checklist that rule. `pre_commit` was missed: on a
busy laptop it refused a commit whose 1423 tests all passed, because
`suite.fast_seconds` read 145 s against 90 (2026-10-17, the L-207 branch).

The hook is driven in process with the harness call faked, so no pytest runs
inside pytest. The fake writes a summary in the real shape `harness --summary`
writes. ⚠ The load-bearing case is the second one: a non-timing failure must
still refuse.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / ".claude" / "hooks"
sys.path.insert(0, str(HOOKS))

import pre_commit  # noqa: E402

_PYTEST_GREEN = "1423 passed, 8 skipped, 97 warnings in 145.39s"


def _summary(*rows: tuple[str, str]) -> str:
    body = "\n".join(
        f"| `{rid}` | N1 | <= 1 | 2 | {'**FAIL**' if v == 'FAIL' else v} |" for rid, v in rows
    )
    return (
        "## harness fast: FAIL\n\n| threshold | criterion | floor | observed | verdict |\n"
        f"|---|---|---|---|---|\n{body}\n\n```\n{_PYTEST_GREEN}\n```\n"
    )


def _run(monkeypatch, tmp_path, summary_text: str) -> int:
    monkeypatch.setattr(pre_commit, "EVIDENCE", tmp_path)
    monkeypatch.setattr(
        pre_commit,
        "read_hook_input",
        lambda: {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "x"'}},
    )
    monkeypatch.setattr(
        pre_commit, "git", lambda *a, **k: "src/jcodemunch_mcp/config.py" if a[:2] == ("diff", "--cached") else ""
    )
    monkeypatch.setattr(pre_commit, "_format_command", lambda: "true")

    def fake_run(argv, budget, shell=False):
        if not shell and "harness" in argv and "fast" in argv:
            path = Path(argv[argv.index("--summary") + 1])
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("\n" + summary_text)
            return 1, "HARNESS FAIL"
        if not shell and "-c" in argv:
            return 1, ""  # pyright not importable: the type ratchet is skipped
        return 0, ""

    monkeypatch.setattr(pre_commit, "run_budgeted", fake_run)
    with pytest.raises(SystemExit) as exc:
        pre_commit.main()
    return exc.value.code


def test_a_timing_only_fast_tier_does_not_refuse_the_commit(monkeypatch, tmp_path):
    code = _run(monkeypatch, tmp_path, _summary(("coverage.min", "PASS"), ("suite.fast_seconds", "FAIL")))
    assert code in (0, None)


def test_a_non_timing_failure_still_refuses_the_commit(monkeypatch, tmp_path):
    code = _run(monkeypatch, tmp_path, _summary(("suite.fast_seconds", "FAIL"), ("suite.fast_skips_max", "FAIL")))
    assert code == 2


def test_a_cadence_failure_still_refuses_the_commit(monkeypatch, tmp_path):
    """Release cadence is not a timing row (unit `hours`)."""
    code = _run(monkeypatch, tmp_path, _summary(("release.unreleased_max_hours", "FAIL")))
    assert code == 2
