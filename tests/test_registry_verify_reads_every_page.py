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
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "registry_verify.py"
NAME = "io.github.jgravelle/jcodemunch-mcp"
META = "io.modelcontextprotocol.registry/official"


@pytest.fixture()
def rv(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "registry_verify_pages_under_test", SCRIPT
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
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
        body = {
            "servers": [_row("9.9.8")],
            "metadata": {"nextCursor": f"c{len(asked)}"},
        }
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert rv.main(["--version", "9.9.9", "--attempts", "1"]) == rv.UNREADABLE
    assert len(asked) == rv.MAX_PAGES
    assert "FAIL" not in capsys.readouterr().out


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
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(
        sys, "argv", [str(SCRIPT), "--version", "9.9.9", "--attempts", "1"]
    )
    with pytest.raises(SystemExit) as done:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    capsys.readouterr()
    assert done.value.code == expected
