"""The registry verify reads the one version row first (cicd FINDINGS C-27).

Three release runs (v1.108.378, v1.108.380, v1.108.381) ended `failure` with
every publish job green, because the paged `?search=` read timed out on all six
attempts, while `/v0/servers/<name>/versions/<v>` answered in about half the
time with the same nested row (FINDINGS C-27 carries the measurement). So each attempt asks
for the one row first.

What is pinned: the version row can only end the run EARLY, on a PASS.
Anything else (no answer, or an answer that does not pass) falls through to
the full paged read, which stays the authority for every FAIL and every
UNREADABLE, so neither verdict became easier to reach.
"""

from __future__ import annotations

import importlib.util
import io
import json
import time
import types
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "registry_verify.py"
NAME = "io.github.jgravelle/jcodemunch-mcp"
META = "io.modelcontextprotocol.registry/official"


@pytest.fixture()
def rv(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "registry_verify_version_row_under_test", SCRIPT
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # The module's own `time` name, not the real module's attribute (F-40).
    mod.slept = []
    monkeypatch.setattr(
        mod,
        "time",
        types.SimpleNamespace(
            sleep=lambda s: mod.slept.append(s), monotonic=time.monotonic
        ),
    )
    return mod


def _row(version: str, latest: bool = True) -> dict:
    return {
        "server": {
            "name": NAME,
            "version": version,
            "packages": [{"version": version}],
        },
        "_meta": {META: {"isLatest": latest}},
    }


def _serve(rv, monkeypatch, version_answer, search_answer):
    """Route by URL: the version row and the paged search answer separately.
    An answer is an exception to raise or a dict to return as JSON."""
    asked = {"version": [], "search": []}

    def urlopen(url, timeout=None):
        kind = "version" if "/versions/" in url else "search"
        asked[kind].append((url, timeout))
        a = version_answer if kind == "version" else search_answer
        if isinstance(a, BaseException):
            raise a
        return io.BytesIO(json.dumps(a).encode())

    monkeypatch.setattr(rv.urllib.request, "urlopen", urlopen)
    return asked


def test_a_passing_version_row_ends_the_run_without_paging(rv, monkeypatch, capsys):
    asked = _serve(
        rv, monkeypatch, _row("9.9.9"), TimeoutError("search would time out")
    )
    assert rv.main(["--version", "9.9.9"]) == 0
    assert asked["search"] == [], "a PASS on the version row needs no paged read"
    assert len(asked["version"]) == 1
    assert "PASS" in capsys.readouterr().out


def test_a_slow_search_no_longer_fails_a_good_publish(rv, monkeypatch):
    """The C-27 shape: the search times out every time and the row is there."""
    asked = _serve(rv, monkeypatch, _row("9.9.9"), TimeoutError("timed out"))
    assert rv.main(["--version", "9.9.9"]) == 0
    assert rv.slept == []


@pytest.mark.parametrize(
    "answer",
    [
        _row("9.9.8"),  # the old version still served
        _row("9.9.9", latest=False),  # not marked latest
        {
            "server": {
                "name": NAME,
                "version": "9.9.9",
                "packages": [{"version": "9.9.8"}],
            },
            "_meta": {META: {"isLatest": True}},
        },  # packages did not advance
    ],
)
def test_a_version_row_that_does_not_pass_defers_to_the_full_read(
    rv, monkeypatch, answer
):
    """A wrong row on the fast read is never the verdict: the full read decides."""
    asked = _serve(rv, monkeypatch, answer, {"servers": [_row("9.9.8")]})
    assert rv.main(["--version", "9.9.9", "--attempts", "2", "--delay", "0"]) == 1
    assert len(asked["search"]) == 2, "every attempt still made the full read"


@pytest.mark.parametrize(
    "answer",
    [
        urllib.error.HTTPError("u", 404, "Not Found", {}, None),
        TimeoutError("timed out"),
        {"servers": [_row("9.9.9")]},  # a page body, not a version row
        {"error": "nope"},
    ],
)
def test_no_answer_on_the_version_row_falls_through_to_the_full_read(
    rv, monkeypatch, answer
):
    asked = _serve(rv, monkeypatch, answer, {"servers": [_row("9.9.9")]})
    assert rv.main(["--version", "9.9.9"]) == 0
    assert len(asked["search"]) == 1


def test_unreadable_still_needs_the_full_read_to_fail_too(rv, monkeypatch, capsys):
    """UNREADABLE is reported only when the full read also got no answer; the
    version row cannot make it easier to reach."""
    asked = _serve(rv, monkeypatch, TimeoutError("v"), TimeoutError("s"))
    assert (
        rv.main(["--version", "9.9.9", "--attempts", "3", "--delay", "0"])
        == rv.UNREADABLE
    )
    assert len(asked["search"]) == 3 and len(asked["version"]) == 3
    assert "FAIL" not in capsys.readouterr().out


def test_the_version_read_asks_for_the_encoded_row_inside_its_own_timeout(
    rv, monkeypatch
):
    asked = _serve(rv, monkeypatch, _row("9.9.9"), TimeoutError("s"))
    rv.main(["--version", "9.9.9"])
    url, timeout = asked["version"][0]
    path = urllib.parse.urlsplit(url).path
    assert path.endswith(
        "/v0/servers/io.github.jgravelle%2Fjcodemunch-mcp/versions/9.9.9"
    )
    assert timeout == rv.VERSION_SECONDS
    assert rv.VERSION_SECONDS + rv.REQUEST_SECONDS < rv.BUDGET_SECONDS, (
        "one attempt's row read and first page must fit the run's budget"
    )


class _Clock:
    """A fake clock on which every request runs to its timeout."""

    def __init__(self):
        self.now = 1000.0
        self.timeouts = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_the_version_read_is_clipped_to_the_budget_left(rv, monkeypatch):
    """Review finding: nothing tested the version read's budget bound, and a
    mutant without `min(VERSION_SECONDS, left)` ran past the budget unseen."""
    clock = _Clock()
    monkeypatch.setattr(
        rv, "time", types.SimpleNamespace(sleep=clock.sleep, monotonic=clock.monotonic)
    )
    seen = []

    def urlopen(url, timeout=None):
        kind = "version" if "/versions/" in url else "search"
        seen.append((kind, timeout))
        clock.now += timeout  # every request runs to its timeout
        return io.BytesIO(b'{"not": "an answer"}')  # no row: no answer, every time

    monkeypatch.setattr(rv.urllib.request, "urlopen", urlopen)
    budget = rv.VERSION_SECONDS + rv.REQUEST_SECONDS + 3.0
    code = rv.main(
        [
            "--version",
            "9.9.9",
            "--budget",
            str(budget),
            "--delay",
            "0",
            "--attempts",
            "5",
        ]
    )
    assert code == rv.UNREADABLE
    assert clock.now - 1000.0 <= budget, (
        f"ran {clock.now - 1000.0} s on a {budget} s budget"
    )
    assert seen[:3] == [
        ("version", rv.VERSION_SECONDS),
        ("search", rv.REQUEST_SECONDS),
        ("version", 3.0),
    ], seen


def test_a_version_read_with_no_budget_left_asks_nothing(rv, monkeypatch):
    """The `left <= 0` guard: a spent budget raises before any request."""
    called = []
    monkeypatch.setattr(rv.urllib.request, "urlopen", lambda *a, **k: called.append(a))
    with pytest.raises(TimeoutError):
        rv.fetch_version(NAME, "9.9.9", deadline=time.monotonic() - 1.0)
    assert called == []


def test_pass_stays_the_last_line(rv, monkeypatch, capsys):
    """RUNBOOK 3a: the last line decides."""
    _serve(rv, monkeypatch, _row("9.9.9"), TimeoutError("s"))
    rv.main(["--version", "9.9.9"])
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-1] == "PASS"
    assert "(read from the version row)" in lines


def test_a_version_row_that_answers_wrong_says_so(rv, monkeypatch, capsys):
    """A wrong row on the fast read is logged before the full read decides."""
    _serve(rv, monkeypatch, _row("9.9.8"), {"servers": [_row("9.9.9")]})
    assert rv.main(["--version", "9.9.9"]) == 0
    out = capsys.readouterr().out
    assert "attempt 1: version row: FAIL" in out
    assert out.strip().splitlines()[-1] == "PASS"
