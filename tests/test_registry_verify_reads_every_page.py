"""The registry verify reads every page, and a row it cannot read is not an answer (LEDGER L-99, L-98).

The registry returns `limit` rows a page, ordered by version string, with
`metadata.nextCursor` naming the next page. `scripts/registry_verify.py` read
one page of 100 and followed no cursor: 66 rows on 2026-10-01, one more per
release, so past the page size the latest row can be on a page nobody asked
for and a good publish reads "none marked isLatest".

L-98 is the same script one level down: a row whose inside is malformed raised
out of `verdict()`, outside the retry, and the process exited 1. And every test
called `main()` in-process, so nothing bound its return value to the exit
status `release.yml` reads; the last test runs the file the way the workflow
does.
"""

from __future__ import annotations

import importlib.util
import io
import json
import runpy
import sys
import time
import types
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "registry_verify.py"
NAME = "io.github.jgravelle/jcodemunch-mcp"
META = "io.modelcontextprotocol.registry/official"
_REAL_SLEEP, _REAL_MONOTONIC = time.sleep, time.monotonic


@pytest.fixture()
def rv(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "registry_verify_pages_under_test", SCRIPT
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # The module's own `time` name, not the real module's attribute (F-40).
    monkeypatch.setattr(
        mod,
        "time",
        types.SimpleNamespace(sleep=lambda s: None, monotonic=time.monotonic),
    )
    return mod


def _row(version: str, latest: bool = False) -> dict:
    return {
        "server": {
            "name": NAME,
            "version": version,
            "packages": [{"version": version}],
        },
        "_meta": {META: {"isLatest": latest}},
    }


def _paged(monkeypatch, pages: dict[str | None, dict]):
    """Serve `pages` keyed by the cursor the request carries (None: no cursor)."""
    asked = []

    def urlopen(url, timeout=None):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        cursor = (q.get("cursor") or [None])[0]
        asked.append(cursor)
        return io.BytesIO(json.dumps(pages[cursor]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return asked


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


def test_the_latest_row_on_a_later_page_is_found(rv, monkeypatch, capsys):
    asked = _paged(
        monkeypatch,
        {
            None: {
                "servers": [_row("9.9.7"), _row("9.9.8")],
                "metadata": {"nextCursor": "c1", "count": 2},
            },
            "c1": {"servers": [_row("9.9.9", latest=True)], "metadata": {"count": 1}},
        },
    )
    assert rv.main(["--version", "9.9.9", "--attempts", "1"]) == 0
    assert asked == [None, "c1"]
    assert "3 row(s)" in capsys.readouterr().out


def test_a_cursor_that_does_not_advance_is_no_answer(rv, monkeypatch, capsys):
    """A page that names itself as the next page would loop for ever."""
    asked = _paged(
        monkeypatch,
        {
            None: {"servers": [_row("9.9.8")], "metadata": {"nextCursor": "c1"}},
            "c1": {"servers": [_row("9.9.8")], "metadata": {"nextCursor": "c1"}},
        },
    )
    assert rv.main(["--version", "9.9.9", "--attempts", "1"]) == rv.UNREADABLE
    assert asked == [None, "c1"]
    assert "FAIL" not in capsys.readouterr().out


def test_cursors_that_never_end_are_no_answer(rv, monkeypatch, capsys):
    """Every cursor is new, so the repeat check never fires; the page cap is what stops it."""
    asked = []

    def urlopen(url, timeout=None):
        asked.append(url)
        # With the cap gone the script would ask for ever; fail by name instead of hanging.
        assert len(asked) <= rv.MAX_PAGES + 1, "the script asked past its page cap"
        body = {
            "servers": [_row("9.9.8")],
            "metadata": {"nextCursor": f"c{len(asked)}"},
        }
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert rv.main(["--version", "9.9.9", "--attempts", "1"]) == rv.UNREADABLE
    assert len(asked) == rv.MAX_PAGES
    assert "FAIL" not in capsys.readouterr().out


def test_the_cursor_reaches_the_registry_as_it_was_given(rv, monkeypatch):
    """A live cursor is `<name>:<version>`; unquoted, a `&` or `#` in one would cut the query."""
    cursor = NAME + ":1.108.287&x=1#y z"
    asked = _paged(
        monkeypatch,
        {
            None: {"servers": [_row("9.9.8")], "metadata": {"nextCursor": cursor}},
            cursor: {"servers": [_row("9.9.9", latest=True)]},
        },
    )
    assert rv.main(["--version", "9.9.9", "--attempts", "1"]) == 0
    assert asked == [None, cursor]


@pytest.mark.parametrize(
    "metadata",
    [
        {"nextCursor": 5},
        {"nextCursor": {"after": "c1"}},
        {"nextCursor": ""},
        "c1",
        ["c1"],
    ],
    ids=[
        "cursor-a-number",
        "cursor-an-object",
        "cursor-empty",
        "metadata-a-string",
        "metadata-a-list",
    ],
)
def test_a_cursor_in_an_unknown_shape_is_no_answer(rv, monkeypatch, capsys, metadata):
    """Read as "last page", it would end the read early and the partial read would be judged:
    the first page here says the old version is latest, and that must not become a FAIL."""
    _paged(
        monkeypatch,
        {None: {"servers": [_row("9.9.8", latest=True)], "metadata": metadata}},
    )
    assert rv.main(["--version", "9.9.9", "--attempts", "1"]) == rv.UNREADABLE
    assert "FAIL" not in capsys.readouterr().out


def test_no_cursor_and_a_null_cursor_are_the_last_page(rv, monkeypatch):
    for metadata in ({}, {"nextCursor": None}, {"count": 1}):
        _paged(
            monkeypatch,
            {None: {"servers": [_row("9.9.9", latest=True)], "metadata": metadata}},
        )
        assert rv.main(["--version", "9.9.9", "--attempts", "1"]) == 0
    _paged(monkeypatch, {None: {"servers": [_row("9.9.9", latest=True)]}})
    assert rv.main(["--version", "9.9.9", "--attempts", "1"]) == 0


class _Clock:
    """Time that moves only when the script waits: a request costs what it was allowed."""

    def __init__(self):
        self.now = 1000.0
        self.timeouts: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_the_whole_run_ends_inside_its_budget(rv, monkeypatch, capsys):
    """`release.yml` cancels the job at its timeout, and a cancelled job opens no issue.
    Six attempts of fifty pages at thirty seconds each is hours; the budget is what bounds it."""
    clock = _Clock()
    monkeypatch.setattr(rv.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(rv.time, "sleep", clock.sleep)

    def urlopen(url, timeout=None):
        clock.timeouts.append(timeout)
        clock.now += timeout  # every request runs to its timeout
        body = {
            "servers": [_row("9.9.8")],
            "metadata": {"nextCursor": f"c{len(clock.timeouts)}"},
        }
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    code = rv.main(["--version", "9.9.9", "--budget", "100", "--delay", "20"])
    out = capsys.readouterr().out
    assert code == rv.UNREADABLE
    assert clock.now - 1000.0 <= 100.0, f"ran {clock.now - 1000.0} s on a 100 s budget"
    assert clock.timeouts == [30.0, 30.0, 30.0, 10.0], clock.timeouts
    assert "budget" in out and "FAIL" not in out


def test_the_default_budget_fits_the_registry_job(rv):
    """The job's `timeout-minutes` covers the install and the publish as well."""
    flow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    job = flow[
        flow.index("Verify the registry row") - 4000 : flow.index(
            "Verify the registry row"
        )
    ]
    minutes = int(job[job.rindex("timeout-minutes:") :].split(":")[1].split()[0])
    assert rv.BUDGET_SECONDS <= minutes * 60 * 0.75, (rv.BUDGET_SECONDS, minutes)


def test_a_bug_in_the_paging_code_raises_and_is_not_retried(rv, monkeypatch):
    """Only a row `verdict` cannot read is no answer. Anything else of that type is this
    script's own error and must surface with a traceback, not as an unreadable registry."""
    calls = []

    def broken(name, deadline=None):
        calls.append(name)
        return None.rows  # AttributeError inside fetch

    monkeypatch.setattr(rv, "fetch", broken)
    with pytest.raises(AttributeError):
        rv.main(["--version", "9.9.9"])
    assert len(calls) == 1


def test_a_later_page_that_is_not_the_registrys_makes_the_whole_read_no_answer(
    rv, monkeypatch, capsys
):
    """Half a read must not be judged: the latest row may be on the page that failed."""
    _paged(
        monkeypatch,
        {
            None: {
                "servers": [_row("9.9.8", latest=True)],
                "metadata": {"nextCursor": "c1"},
            },
            "c1": {"error": "rate limited"},
        },
    )
    assert rv.main(["--version", "9.9.9", "--attempts", "1"]) == rv.UNREADABLE
    assert "FAIL" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "row",
    [
        {"server": "x"},
        {"server": {"name": NAME, "version": "9.9.9"}, "_meta": "x"},
        {"server": {"name": NAME, "version": "9.9.9"}, "_meta": {META: "x"}},
        {
            "server": {"name": NAME, "version": "9.9.9", "packages": "x"},
            "_meta": {META: {"isLatest": True}},
        },
        {
            "server": {"name": NAME, "version": "9.9.9", "packages": [None]},
            "_meta": {META: {"isLatest": True}},
        },
        {
            "server": {"name": NAME, "version": "9.9.9", "packages": 5},
            "_meta": {META: {"isLatest": True}},
        },
    ],
    ids=[
        "server-not-an-object",
        "meta-not-an-object",
        "official-not-an-object",
        "packages-not-a-list",
        "package-null",
        "packages-a-number",  # TypeError, where the others raise AttributeError
    ],
)
def test_a_row_with_a_malformed_inside_is_no_answer(rv, monkeypatch, capsys, row):
    _paged(monkeypatch, {None: {"servers": [row]}})
    assert rv.main(["--version", "9.9.9", "--attempts", "2"]) == rv.UNREADABLE
    assert "FAIL" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("pages", "expected"),
    [
        ({None: {"servers": [_row("9.9.9", latest=True)]}}, 0),
        ({None: {"servers": [_row("9.9.8", latest=True)]}}, 1),
        ({None: {"error": "rate limited"}}, 75),
    ],
    ids=["pass", "wrong-row", "unreadable"],
)
def test_the_process_exits_with_what_main_returns(monkeypatch, capsys, pages, expected):
    """Run as `python scripts/registry_verify.py`, the way `release.yml` runs it."""
    _paged(monkeypatch, pages)
    monkeypatch.setattr(
        sys, "argv", [str(SCRIPT), "--version", "9.9.9", "--attempts", "1"]
    )
    with pytest.raises(SystemExit) as done:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    capsys.readouterr()
    assert done.value.code == expected
