"""The Grep answer is size-gated, and whole when it is sent.

The opt-in Grep-answer hook used to read one 20-line `search_text` page and
decline anything cut short, so it answered only the Greps that are cheapest to
run as written and never a large one. It now counts the whole match set first.
Under both thresholds (matching lines, matching files) the Grep runs untouched.
At or over either one the hook answers with every match: every line while the
page fits, otherwise every match counted by file and by the function or class
it sits in. A page that drops matches is never sent, and a match set over the
hook's scan cap lets the Grep run.
"""

from __future__ import annotations

import inspect
import io
import json
import sys
from unittest import mock

import pytest

from jcodemunch_mcp.cli.hooks import grep_answer, run_pretooluse


def _run(payload: dict) -> str:
    out = io.StringIO()
    with (
        mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))),
        mock.patch.object(sys, "stdout", out),
        mock.patch.object(sys, "stderr", io.StringIO()),
    ):
        assert run_pretooluse() == 0
    return out.getvalue()


def _ask(cwd, pattern="beacon", session="s1", **extra):
    out = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Grep",
            "tool_input": {"pattern": pattern, **extra},
            "cwd": str(cwd),
            "session_id": session,
        }
    )
    if not out:
        return None, ""
    hso = json.loads(out)["hookSpecificOutput"]
    return hso.get("permissionDecision"), (
        hso.get("permissionDecisionReason") or hso.get("additionalContext") or ""
    )


@pytest.fixture
def make_repo(tmp_path, monkeypatch):
    """Build and index a repo of `files` files, each holding `funcs` functions
    with `per_func` matching lines. Every matching line carries a unique
    marker `beacon_<file>_<func>_<n>`, padded to `width` characters."""
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", str(tmp_path))
    monkeypatch.delenv("JCODEMUNCH_ENFORCE", raising=False)
    monkeypatch.setenv("JCODEMUNCH_HOOK_GREP_ANSWER", "1")
    for var in (
        "JCODEMUNCH_HOOK_GREP_ANSWER_MIN_LINES",
        "JCODEMUNCH_HOOK_GREP_ANSWER_MIN_FILES",
    ):
        monkeypatch.delenv(var, raising=False)
    built = {"n": 0}

    def make(files=1, funcs=1, per_func=1, width=0):
        built["n"] += 1
        proj = tmp_path / f"proj{built['n']}"
        proj.mkdir()
        markers = []
        for fi in range(files):
            lines = []
            for fn in range(funcs):
                lines.append(f"def handler_{fi}_{fn}():")
                for n in range(per_func):
                    marker = f"beacon_{fi}_{fn}_{n}"
                    markers.append(marker)
                    lines.append(f"    x = '{marker}'".ljust(width))
                lines.append("    return x")
                lines.append("")
            (proj / f"mod_{fi}.py").write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )
        from jcodemunch_mcp.tools.index_folder import index_folder

        assert index_folder(str(proj), use_ai_summaries=False)["success"]
        return proj, markers

    return make


def _gate(monkeypatch, lines, files):
    monkeypatch.setenv("JCODEMUNCH_HOOK_GREP_ANSWER_MIN_LINES", str(lines))
    monkeypatch.setenv("JCODEMUNCH_HOOK_GREP_ANSWER_MIN_FILES", str(files))


def _grouped(text: str) -> dict:
    """The JSON object carried after the grouped answer's header line."""
    assert text.startswith(grep_answer.ANSWER_MARKER)
    return json.loads(text.split("\n", 1)[1])


def test_a_small_grep_runs_and_is_not_remembered(make_repo, monkeypatch):
    proj, _ = make_repo(files=2, per_func=1)
    decision, text = _ask(proj)
    assert decision != "deny" and grep_answer.ANSWER_MARKER not in text
    # Not remembered: once the gate is met, the same Grep is answered.
    _gate(monkeypatch, 1, 1)
    decision, text = _ask(proj)
    assert decision == "deny" and text.startswith(grep_answer.ANSWER_MARKER)


def test_the_line_threshold_answers(make_repo, monkeypatch):
    _gate(monkeypatch, 5, 1000)
    at, _ = make_repo(per_func=5)
    below, _ = make_repo(per_func=4)
    assert _ask(at)[0] == "deny"
    assert _ask(below)[0] != "deny"


def test_the_file_threshold_answers(make_repo, monkeypatch):
    _gate(monkeypatch, 1000, 3)
    at, _ = make_repo(files=3)
    below, _ = make_repo(files=2)
    assert _ask(at)[0] == "deny"
    assert _ask(below)[0] != "deny"


def test_more_than_one_page_is_answered_with_every_line(make_repo, monkeypatch):
    _gate(monkeypatch, 1, 1)
    proj, markers = make_repo(files=3, per_func=10)
    assert len(markers) == 30  # over search_text's 20-line default page
    decision, text = _ask(proj)
    assert decision == "deny"
    missing = [m for m in markers if m not in text]
    assert not missing, f"answer dropped {len(missing)} of {len(markers)} matches"


def test_lines_that_do_not_fit_are_counted_by_file_and_symbol(make_repo, monkeypatch):
    _gate(monkeypatch, 1, 1)
    proj, markers = make_repo(files=4, funcs=2, per_func=25, width=180)
    decision, text = _ask(proj)
    assert decision == "deny"
    assert len(text) <= grep_answer.MAX_ANSWER_CHARS + 1000
    assert "lines" in text.split("\n", 1)[0] and "not" in text.split("\n", 1)[0]
    body = _grouped(text)
    assert body["result_count"] == len(markers) == 200
    assert body["file_count"] == 4
    assert sum(g["matches"] for g in body["groups"]) == 200
    for g in body["groups"]:
        assert (
            sum(s["matches"] for s in g["in"]) + g.get("outside_symbols", 0)
            == g["matches"]
        )
    ids = {s["id"] for g in body["groups"] for s in g["in"]}
    for fi in range(4):
        for fn in range(2):
            assert any(i.endswith(f"handler_{fi}_{fn}#function") for i in ids), ids
    assert not any(m in text for m in markers)


def test_an_answer_that_fits_in_neither_form_lets_the_grep_run(make_repo, monkeypatch):
    _gate(monkeypatch, 1, 1)
    monkeypatch.setattr(grep_answer, "MAX_ANSWER_CHARS", 120)
    proj, _ = make_repo(files=4, funcs=2, per_func=25, width=180)
    assert _ask(proj)[0] != "deny"


def test_a_match_set_over_the_scan_cap_lets_the_grep_run(make_repo, monkeypatch):
    _gate(monkeypatch, 1, 1)
    monkeypatch.setattr(grep_answer, "SCAN_LIMIT", 10)
    proj, _ = make_repo(files=3, per_func=10)
    assert _ask(proj)[0] != "deny"


def test_the_same_grep_in_another_directory_is_answered(make_repo, monkeypatch):
    # The once-per-Grep memory is keyed on where the Grep searches, not only on
    # its arguments: a relative Grep in a second repo is a different search.
    _gate(monkeypatch, 1, 1)
    first, _ = make_repo()
    second, _ = make_repo()
    assert _ask(first)[0] == "deny"
    decision, text = _ask(second)
    assert decision == "deny" and text.startswith(grep_answer.ANSWER_MARKER)
    assert _ask(second)[0] != "deny"  # the escape hatch still works per directory


def test_scan_limit_returns_the_whole_set(make_repo):
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo
    from jcodemunch_mcp.tools.search_text import search_text

    proj, markers = make_repo(files=4, funcs=2, per_func=25)
    repo = resolve_repo(str(proj))["repo"]
    page = search_text(repo, "beacon")
    assert page["truncated"] is True and page["result_count"] == 20
    whole = search_text(repo, "beacon", scan_limit=500)
    assert whole["truncated"] is False
    assert whole["result_count"] == len(markers) == 200
    capped = search_text(repo, "beacon", scan_limit=50)
    assert capped["truncated"] is True and capped["result_count"] == 50


def test_scan_limit_is_not_an_mcp_argument():
    from jcodemunch_mcp.server import _build_tools_list
    from jcodemunch_mcp.tools.search_text import search_text

    param = inspect.signature(search_text).parameters["scan_limit"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    tool = next(
        t for t in _build_tools_list(surface_override="full") if t.name == "search_text"
    )
    assert "scan_limit" not in tool.inputSchema.get("properties", {})


@pytest.mark.parametrize("raw", ["", " ", "0", "-3", "abc", "2.5", "1e3", "²", "٣"])
def test_a_threshold_that_is_not_a_positive_integer_reads_as_the_default(
    monkeypatch, raw
):
    _gate(monkeypatch, raw, raw)
    assert grep_answer._min_lines() == grep_answer.DEFAULT_MIN_LINES
    assert grep_answer._min_files() == grep_answer.DEFAULT_MIN_FILES
