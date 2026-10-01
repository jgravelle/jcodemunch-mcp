"""A registry read that got no answer is not a publish that failed (LEDGER L-96).

`scripts/registry_verify.py` made one request with a 30 s timeout. On the
1.108.321 release the publish succeeded, that one read timed out, the run read
Failure and `release.yml` opened "registry publish failed" (#952) against a
registry already serving the version as latest.

The script now retries, and it separates the two outcomes by exit code: 1 when
the registry ANSWERED and the row is wrong, 2 when no attempt got an answer.
`release.yml` titles its issue from that code, so nobody is told to re-publish
over a read that never happened.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NAME = "io.github.jgravelle/jcodemunch-mcp"
META = "io.modelcontextprotocol.registry/official"


@pytest.fixture()
def rv(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "registry_verify_under_test", ROOT / "scripts" / "registry_verify.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.slept = []
    monkeypatch.setattr(mod.time, "sleep", mod.slept.append)
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
    assert code == rv.UNREADABLE == 2
    assert len(calls) == 3
    assert "UNREADABLE" in out and "FAIL" not in out
    assert "re-publish" in out


def test_a_body_that_is_not_json_is_an_unanswered_read(rv, monkeypatch, capsys):
    _serve(rv, monkeypatch, [io.BytesIO(b"<html>502</html>"), _payload("9.9.9")])
    assert rv.main(["--version", "9.9.9"]) == 0


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


def test_the_workflow_carries_the_scripts_code_into_the_issue():
    verify, issue = _registry_steps()
    line = next(ln for ln in verify.splitlines() if "registry_verify.py" in ln)
    assert "|" not in line, "the verify script's exit status is the left side of a pipe"
    assert "id: verify" in verify and "GITHUB_OUTPUT" in verify
    assert "steps.verify.outputs.code" in issue


def test_the_issue_does_not_call_an_unanswered_read_a_failed_publish():
    _, issue = _registry_steps()
    titles = re.findall(r'title="([^"]+)"', issue) or re.findall(
        r"title='([^']+)'", issue
    )
    assert len(titles) == 2, titles
    unread = [t for t in titles if "could not be read" in t]
    assert len(unread) == 1 and "failed" not in unread[0], titles
    assert "re-publish" in issue
