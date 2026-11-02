"""`jcodemunch-mcp storage` reports the index store's size (BACKLOG 61).

A user asks how much disk an index uses. `diagnose` names no index and counts no total, so the user had to browse
`~/.code-index` by hand. The report stats files and never opens a database:
opening one could move its mtime, which keys the in-memory index cache.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from jcodemunch_mcp.cli import storage_report as sr


def _write(p: Path, n: int) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * n)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    base = tmp_path / "store"
    _write(base / "pallets-flask.db", 100000)
    _write(base / "pallets-flask.db-wal", 200)
    _write(base / "pallets-flask.db-shm", 30)
    _write(base / "pallets-flask.textfilter", 40)
    _write(base / "pallets-flask" / "src" / "app.py", 500)
    _write(base / "pallets-flask" / "README.md", 5)
    _write(base / "local-tiny-ab12cd34.db", 100)
    # A real SQLite file in WAL mode with no sidecars: opening it creates
    # `-wal`/`-shm` and moves the mtime, so a report that opens a database fails.
    con = sqlite3.connect(base / "real-wal.db")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t(x)")
    con.commit()
    con.close()
    _write(base / "telemetry.db", 300)
    _write(base / "packs" / "nodejs.tar.gz", 2000)
    _write(base / "_savings.json", 7)
    return base


def test_the_total_is_every_file_and_each_index_is_its_own_files(store):
    rep = sr.storage_report(str(store))
    assert rep["exists"] is True
    every = sum(p.stat().st_size for p in store.rglob("*") if p.is_file())
    assert rep["total_bytes"] == every
    flask = next(r for r in rep["indexes"] if r["name"] == "pallets-flask")
    assert flask["db_bytes"] == 100000 + 200 + 30 + 40
    assert flask["content_bytes"] == 505
    assert flask["total_bytes"] == 100775
    assert rep["indexes_bytes"] + rep["other_bytes"] == rep["total_bytes"]


def test_rows_are_largest_first_and_other_entries_are_named(store):
    rep = sr.storage_report(str(store))
    assert [r["name"] for r in rep["indexes"]][0] == "pallets-flask"
    assert "local-tiny-ab12cd34" in [r["name"] for r in rep["indexes"]]
    names = [r["name"] for r in rep["other"]]
    assert names[0] == "packs", names
    assert "telemetry.db" in names, (
        "a .db that isn't an index's is reported under other"
    )
    assert all(a["bytes"] >= b["bytes"] for a, b in zip(rep["other"], rep["other"][1:]))


def test_no_database_is_opened(store, monkeypatch):
    # A connection that closes cleanly removes its own sidecars and leaves the
    # mtime alone, so the filesystem checks below can't see every open. A
    # connect that fails can.
    def refuse(*a, **k):
        raise AssertionError(f"storage opened a database: {a!r}")

    monkeypatch.setattr(sqlite3, "connect", refuse)
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in store.rglob("*.db")}
    sr.storage_report(str(store))
    sr.main(["--json"], storage_path=str(store))
    after = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in store.rglob("*.db")}
    assert before == after
    assert not (store / "real-wal.db-wal").exists()
    assert not (store / "real-wal.db-shm").exists()


def test_top_limits_rows_and_keeps_the_totals(store):
    rep = sr.storage_report(str(store), top=1)
    assert len(rep["indexes"]) == 1 and len(rep["other"]) == 1
    assert rep["indexes_more"]["count"] == 2
    shown = sum(r["total_bytes"] for r in rep["indexes"]) + rep["indexes_more"]["bytes"]
    assert shown == rep["indexes_bytes"]
    assert (
        sum(r["bytes"] for r in rep["other"]) + rep["other_more"]["bytes"]
        == rep["other_bytes"]
    )


def test_a_missing_store_reports_zero_and_says_so(tmp_path):
    rep = sr.storage_report(str(tmp_path / "nope"))
    assert rep["exists"] is False and rep["total_bytes"] == 0
    assert sr.main([], storage_path=str(tmp_path / "nope")) == 0


def test_an_unstatable_file_is_counted_as_unreadable_not_zero(store, monkeypatch):
    real = sr._size_of
    target = store / "packs" / "nodejs.tar.gz"

    def flaky(path):
        if Path(path) == target:
            raise PermissionError("denied")
        return real(path)

    monkeypatch.setattr(sr, "_size_of", flaky)
    rep = sr.storage_report(str(store))
    assert rep["unreadable"] == 1
    assert (
        rep["total_bytes"]
        == sum(p.stat().st_size for p in store.rglob("*") if p.is_file()) - 2000
    )


def test_an_unreadable_file_is_counted_on_its_index_row(store, monkeypatch):
    real = sr._size_of
    target = store / "pallets-flask" / "src" / "app.py"
    monkeypatch.setattr(
        sr,
        "_size_of",
        lambda p: (
            (_ for _ in ()).throw(PermissionError("x"))
            if Path(p) == target
            else real(p)
        ),
    )
    rep = sr.storage_report(str(store))
    flask = next(r for r in rep["indexes"] if r["name"] == "pallets-flask")
    assert flask["unreadable"] == 1 and flask["content_bytes"] == 5


def test_a_store_that_cannot_be_listed_reports_no_size(store, monkeypatch, capsys):
    real = Path.iterdir

    def deny(self):
        if Path(self) == Path(store).resolve():
            raise PermissionError("denied")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", deny)
    rep = sr.storage_report(str(store))
    assert rep["exists"] is True and rep["measured"] is False
    assert rep["total_bytes"] is None, "a store nobody could list is not 0 bytes"
    assert sr.main([], storage_path=str(store)) == 0
    out = capsys.readouterr().out
    assert "Not measured" in out and "Total:" not in out


def _dir_link(target, link):
    """A directory link that needs no privilege: a junction on Windows (the
    reparse point `os.path.islink` misses), a symlink elsewhere. It runs on
    every platform rather than skipping, since a skip counts against the
    CI skip ceiling."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_a_directory_link_out_of_the_store_is_not_followed(store, tmp_path):
    outside = tmp_path / "outside"
    _write(outside / "big.bin", 99999)
    baseline = sr.storage_report(str(store))["total_bytes"]
    _dir_link(outside, store / "pallets-flask" / "escape")
    _dir_link(outside, store / "escape-top")
    rep = sr.storage_report(str(store))
    assert rep["total_bytes"] == baseline
    assert "escape-top" in [r["name"] for r in rep["other"]]
    # A link reads as a link not followed, never as an empty entry (review).
    top = next(r for r in rep["other"] if r["name"] == "escape-top")
    assert top["bytes"] == 0 and top["links_not_followed"] == 1
    flask = next(r for r in rep["indexes"] if r["name"] == "pallets-flask")
    assert flask["links_not_followed"] == 1


def test_a_symlink_out_of_the_store_is_not_followed(store, tmp_path):
    outside = tmp_path / "outside"
    _write(outside / "big.bin", 9999)
    link = store / "pallets-flask" / "escape"
    baseline = sr.storage_report(str(store))["total_bytes"]
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privileges on this host")
    rep = sr.storage_report(str(store))
    assert rep["total_bytes"] == baseline
    flask = next(r for r in rep["indexes"] if r["name"] == "pallets-flask")
    assert flask["links_not_followed"] == 1


def test_an_unfollowed_link_is_named_in_the_text_report(store, tmp_path, capsys):
    outside = tmp_path / "outside"
    _write(outside / "big.bin", 9999)
    try:
        os.symlink(outside, store / "escape-top", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privileges on this host")
    rep = sr.storage_report(str(store))
    row = next(r for r in rep["other"] if r["name"] == "escape-top")
    assert row["links_not_followed"] == 1
    assert sr.main([], storage_path=str(store)) == 0
    out = capsys.readouterr().out
    assert "escape-top  (1 link(s) not followed, not counted)" in out


def test_json_output_parses_and_text_output_names_the_path(store, capsys):
    assert sr.main(["--json"], storage_path=str(store)) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_bytes"] > 0 and data["path"]
    assert sr.main([], storage_path=str(store)) == 0
    out = capsys.readouterr().out
    assert str(Path(store).resolve()) in out and "pallets-flask" in out


def test_the_cli_dispatches_the_subcommand(store):
    env = dict(
        os.environ,
        CODE_INDEX_PATH=str(store),
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
    )
    r = subprocess.run(
        [sys.executable, "-m", "jcodemunch_mcp", "storage", "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["indexes"][0]["name"] == "pallets-flask"
