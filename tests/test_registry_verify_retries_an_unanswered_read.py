"""A registry read that got no answer is not a publish that failed (LEDGER L-96).

`scripts/registry_verify.py` made one request with a 30 s timeout. On the
1.108.321 release the publish succeeded, that one read timed out, the run read
Failure and `release.yml` opened "registry publish failed" (#952) against a
registry already serving the version as latest.

The script now retries, and it separates the two outcomes by exit code: 1 when
the registry ANSWERED and the row is wrong, `UNREADABLE` when no attempt got an
answer (a code argparse and a missing file do not share).
`release.yml` titles its issue from that code, so nobody is told to re-publish
over a read that never happened.
"""

from __future__ import annotations

import http.client
import importlib.util
import io
import json
import re
import threading
import time
import types
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NAME = "io.github.jgravelle/jcodemunch-mcp"
META = "io.modelcontextprotocol.registry/official"
_REAL_SLEEP, _REAL_MONOTONIC = time.sleep, time.monotonic


@pytest.fixture()
def rv(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "registry_verify_under_test", ROOT / "scripts" / "registry_verify.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.slept = []
    # The module's own `time` name, not the real module's attribute (F-40).
    monkeypatch.setattr(
        mod,
        "time",
        types.SimpleNamespace(sleep=mod.slept.append, monotonic=time.monotonic),
    )
    return mod


def _payload(version: str) -> io.BytesIO:
    row = {
        "server": {
            "name": NAME,
            "version": version,
            "packages": [{"version": version}],
        },
        "_meta": {META: {"isLatest": True}},
    }
    return io.BytesIO(json.dumps({"servers": [row]}).encode())


def _serve(rv, monkeypatch, answers):
    """Each call to urlopen takes the next answer: an exception to raise or a body to return."""
    calls = []

    def urlopen(url, timeout=None):
        calls.append(url)
        a = answers[min(len(calls), len(answers)) - 1]
        if isinstance(a, BaseException):
            raise a
        return a

    monkeypatch.setattr(rv.urllib.request, "urlopen", urlopen)
    return calls


def test_the_fixture_leaves_the_process_clock_alone(rv):
    """The script's `time` NAME is replaced, never an attribute of the real module.

    The first form of this fixture set `time.sleep` on the module every thread
    in the worker shares. `tests/test_v1_108_182.py` abandons a provider thread
    that sleeps 10 ms at a time for 30 s; under xdist it called the fake 258,047
    times in one test and `len(slept) == 1` failed on two CI legs (harness
    FINDINGS F-40). Each of those sleeps also returned at once.
    """
    assert rv.time is not time
    assert time.sleep is _REAL_SLEEP and time.monotonic is _REAL_MONOTONIC


def test_a_thread_sleeping_elsewhere_does_not_reach_the_fixture(
    rv, monkeypatch, capsys
):
    """F-40 reproduced on purpose: another test's leftover thread sleeps while this one runs."""
    stop = threading.Event()

    def napper():
        while not stop.is_set():
            time.sleep(0.001)

    t = threading.Thread(target=napper, daemon=True)
    t.start()
    try:
        _serve(rv, monkeypatch, [TimeoutError("timed out"), _payload("9.9.9")])
        assert rv.main(["--version", "9.9.9"]) == 0
        _REAL_SLEEP(0.05)
    finally:
        stop.set()
        t.join(5)
    assert not t.is_alive()
    assert rv.slept == [20.0], f"{len(rv.slept)} sleeps recorded"


def test_one_timed_out_read_does_not_fail_a_good_publish(rv, monkeypatch, capsys):
    calls = _serve(rv, monkeypatch, [TimeoutError("timed out"), _payload("9.9.9")])
    assert rv.main(["--version", "9.9.9"]) == 0
    assert len(calls) == 2 and len(rv.slept) == 1
    assert "PASS" in capsys.readouterr().out


def test_a_registry_that_never_answers_is_unreadable_not_failed(
    rv, monkeypatch, capsys
):
    calls = _serve(rv, monkeypatch, [urllib.error.URLError("no route")])
    code = rv.main(["--version", "9.9.9", "--attempts", "3"])
    out = capsys.readouterr().out
    assert code == rv.UNREADABLE
    assert len(calls) == 3
    assert "UNREADABLE" in out and "FAIL" not in out
    assert "re-publish" in out


def test_a_body_that_is_not_json_is_an_unanswered_read(rv, monkeypatch, capsys):
    _serve(rv, monkeypatch, [io.BytesIO(b"<html>502</html>"), _payload("9.9.9")])
    assert rv.main(["--version", "9.9.9"]) == 0


@pytest.mark.parametrize(
    "unanswered",
    [
        http.client.IncompleteRead(b"{"),  # the response was cut; NOT an OSError
        http.client.BadStatusLine("garbage"),
        urllib.error.HTTPError("u", 503, "unavailable", None, None),
        ConnectionResetError("reset"),
        b"null",  # valid JSON, and not the registry's object
        b"[]",
        b"",
        b'{"error": "rate limited"}',  # an object, served with a 200, with no row list
        b'{"servers": "x"}',
        b'{"servers": [null]}',
    ],
    ids=[
        "cut",
        "bad-status",
        "http-503",
        "reset",
        "json-null",
        "json-list",
        "empty",
        "error-object",
        "rows-not-a-list",
        "row-not-an-object",
    ],
)
def test_every_spelling_of_no_answer_is_unreadable(rv, monkeypatch, capsys, unanswered):
    answer = io.BytesIO(unanswered) if isinstance(unanswered, bytes) else unanswered
    calls = _serve(rv, monkeypatch, [answer])
    assert rv.main(["--version", "9.9.9", "--attempts", "2"]) == rv.UNREADABLE
    assert len(calls) == 2
    assert "FAIL" not in capsys.readouterr().out


def test_the_unreadable_code_is_one_nothing_else_exits_with(rv, capsys):
    """argparse exits 2 on a usage error and so does python on a missing file;
    the workflow would title either "could not be read ... the publish step passed"."""
    with pytest.raises(SystemExit) as usage:
        rv.main([])
    capsys.readouterr()
    assert usage.value.code == 2
    assert rv.UNREADABLE not in (0, 1, 2)


def test_an_empty_row_list_is_an_answer(rv, monkeypatch, capsys):
    """`{"servers": []}` is the registry saying it has no such server: exit 1, not UNREADABLE."""
    _serve(rv, monkeypatch, [io.BytesIO(b'{"servers": []}')])
    assert rv.main(["--version", "9.9.9", "--attempts", "2"]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_a_registry_still_serving_the_old_version_is_asked_again(
    rv, monkeypatch, capsys
):
    calls = _serve(rv, monkeypatch, [_payload("9.9.8"), _payload("9.9.9")])
    assert rv.main(["--version", "9.9.9"]) == 0
    assert len(calls) == 2


def test_a_registry_that_answers_wrong_every_time_fails_with_one(
    rv, monkeypatch, capsys
):
    calls = _serve(
        rv, monkeypatch, [_payload("9.9.8"), _payload("9.9.8"), _payload("9.9.8")]
    )
    assert rv.main(["--version", "9.9.9", "--attempts", "3"]) == 1
    assert len(calls) == 3
    assert "FAIL" in capsys.readouterr().out


def test_an_answer_after_unanswered_reads_decides_the_code(rv, monkeypatch):
    """One real answer outranks the reads that got none: the registry was readable."""
    _serve(
        rv,
        monkeypatch,
        [TimeoutError("timed out"), _payload("9.9.8"), TimeoutError("timed out")],
    )
    assert rv.main(["--version", "9.9.9", "--attempts", "3"]) == 1


def _registry_steps() -> tuple[str, str]:
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    verify = re.search(
        r"- name: Verify the registry row.*?(?=\n      - name: )", text, re.S
    )
    issue = re.search(
        r"- name: Open an issue \(PyPI is live.*?(?=\n  \S|\Z)", text, re.S
    )
    assert verify and issue, (
        "release.yml no longer has the two registry steps by these names"
    )
    return verify.group(0), issue.group(0)


def _commands(step: str) -> list[str]:
    """The step's shell lines, comments dropped, so a token inside a comment proves nothing."""
    out = []
    for ln in step.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln)
    return out


def test_the_workflow_carries_the_scripts_code_into_the_issue():
    verify, _ = _registry_steps()
    cmds = _commands(verify)
    assert "id: verify" in cmds
    run = next(c for c in cmds if "registry_verify.py" in c)
    assert "|" not in run.replace("||", ""), (
        "the verify script's exit status is the left side of a pipe"
    )
    assert 'echo "code=$code" >> "$GITHUB_OUTPUT"' in cmds
    assert cmds[-1] == 'exit "$code"', cmds[-1]
    # The whole script, in order. Actions runs it under `bash -e`, so a bare
    # failing line ends the step before the code is written and the issue is
    # titled as a failed publish; `|| code=$?` is what keeps the step alive.
    assert run.endswith(" > verify.txt 2>&1 || code=$?"), run
    assert cmds[cmds.index("run: |") + 1 :] == [
        "code=0",
        run,
        'echo "code=$code" >> "$GITHUB_OUTPUT"',
        "cat verify.txt",
        'cat verify.txt >> "$GITHUB_STEP_SUMMARY"',
        'exit "$code"',
    ]
    _, issue = _registry_steps()
    assert "steps.verify.outputs.code" in "".join(_commands(issue))
    # A failed append to the summary must not lose the code.
    summary = next(i for i, c in enumerate(cmds) if "GITHUB_STEP_SUMMARY" in c)
    assert cmds.index('echo "code=$code" >> "$GITHUB_OUTPUT"') < summary


def test_the_issue_does_not_call_an_unanswered_read_a_failed_publish(rv):
    _, issue = _registry_steps()
    cmds = _commands(issue)
    assert "if: failure()" in cmds
    test = f'if [ "${{{{ steps.verify.outputs.code }}}}" = "{rv.UNREADABLE}" ]; then'
    assert cmds.count(test) == 1, f"expected exactly one {test!r}"
    assert cmds.count("else") == 1 and cmds[-1] == "fi"
    then = "\n".join(cmds[cmds.index(test) + 1 : cmds.index("else")])
    other = "\n".join(cmds[cmds.index("else") + 1 : -1])
    for branch in (then, other):
        assert branch.count("gh issue create") == 1 and branch.count("--title") == 1, (
            branch
        )
    then_title = next(ln for ln in then.splitlines() if "--title" in ln)
    other_title = next(ln for ln in other.splitlines() if "--title" in ln)
    assert "could not be read" in then_title and "failed" not in then_title
    assert "publish failed" in other_title
    assert "do not re-publish" in then


def test_the_runbook_says_what_a_human_does_with_each_title():
    book = (ROOT / "docs" / "cicd" / "RUNBOOK.md").read_text(encoding="utf-8")
    assert "registry could not be read after publishing" in book
    assert "registry publish failed for" in book
    assert "registry_verify.py --version" in book
