"""A redirected Grep carries the index's answer in the refusal.

A redirect that only says "use search_text" costs two turns: the refused Grep,
then the search it asks for. The PreToolUse hook now runs that search itself
and puts the page in the refusal, once per session and Grep arguments. The same
Grep sent again runs normally. No index, no match, an argument with no index
translation, a search over its time budget, or the switch turned off all let
the Grep run untouched.
"""

from __future__ import annotations

import io
import json
import sys
import time
from unittest import mock

import pytest

from jcodemunch_mcp.cli.hooks import grep_answer, run_pretooluse


def _run(payload: dict) -> str:
    out = io.StringIO()
    with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
         mock.patch.object(sys, "stdout", out), \
         mock.patch.object(sys, "stderr", io.StringIO()):
        assert run_pretooluse() == 0
    return out.getvalue()


def _grep(cwd, session="s1", **tool_input) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Grep",
        "tool_input": tool_input,
        "cwd": str(cwd),
        "session_id": session,
    }


def _decision(out: str):
    if not out:
        return None, ""
    hso = json.loads(out)["hookSpecificOutput"]
    return hso.get("permissionDecision"), (
        hso.get("permissionDecisionReason") or hso.get("additionalContext") or ""
    )


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "mod.py").write_text(
        "def compute_total(xs):\n    return sum(xs)\n", encoding="utf-8"
    )
    (proj / "other.py").write_text(
        "from pkg.mod import compute_total\n", encoding="utf-8"
    )
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", str(tmp_path))
    monkeypatch.delenv("JCODEMUNCH_ENFORCE", raising=False)
    monkeypatch.delenv("JCODEMUNCH_HOOK_GREP_ANSWER", raising=False)
    from jcodemunch_mcp.tools.index_folder import index_folder

    assert index_folder(str(proj), use_ai_summaries=False)["success"]
    return proj


def test_the_first_grep_is_answered_in_the_refusal(indexed):
    decision, text = _decision(_run(_grep(indexed, pattern="compute_total")))
    assert decision == "deny"
    assert text.startswith(grep_answer.ANSWER_MARKER)
    assert "mod.py" in text and "other.py" in text


def test_the_same_grep_again_runs(indexed):
    _run(_grep(indexed, pattern="compute_total"))
    decision, _ = _decision(_run(_grep(indexed, pattern="compute_total")))
    assert decision != "deny"


def test_another_session_is_answered_again(indexed):
    _run(_grep(indexed, pattern="compute_total"))
    decision, text = _decision(_run(_grep(indexed, session="s2", pattern="compute_total")))
    assert decision == "deny" and grep_answer.ANSWER_MARKER in text


def test_a_path_scopes_the_answer(indexed):
    decision, text = _decision(_run(_grep(indexed, pattern="compute_total", path="pkg")))
    assert decision == "deny"
    assert "mod.py" in text and "other.py" not in text


def test_no_match_lets_the_grep_run(indexed):
    decision, text = _decision(_run(_grep(indexed, pattern="nothing_like_this_here")))
    assert decision != "deny"
    assert grep_answer.ANSWER_MARKER not in text


def test_an_argument_with_no_translation_lets_the_grep_run(indexed):
    decision, _ = _decision(_run(_grep(indexed, pattern="compute_total", type="py")))
    assert decision != "deny"


def test_the_switch_turns_it_off(indexed, monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_HOOK_GREP_ANSWER", "0")
    decision, _ = _decision(_run(_grep(indexed, pattern="compute_total")))
    assert decision != "deny"


def test_a_search_over_budget_lets_the_grep_run(indexed, monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_HOOK_ANSWER_BUDGET_SECONDS", "0.05")
    import jcodemunch_mcp.tools.search_text as st

    real = st.search_text

    def slow(*a, **kw):
        time.sleep(0.5)
        return real(*a, **kw)

    monkeypatch.setattr(st, "search_text", slow)
    decision, _ = _decision(_run(_grep(indexed, pattern="compute_total")))
    assert decision != "deny"


def test_an_unindexed_directory_is_not_answered(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert grep_answer.answer_grep({"pattern": "x"}, str(tmp_path), "s") is None


def test_a_page_over_the_cap_is_not_put_in_the_refusal(indexed, monkeypatch):
    monkeypatch.setattr(grep_answer, "MAX_ANSWER_CHARS", 10)
    decision, _ = _decision(_run(_grep(indexed, pattern="compute_total")))
    assert decision != "deny"
