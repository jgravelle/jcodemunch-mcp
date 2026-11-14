"""#1435 (@dkiaulakis): `install-status` says which running servers need a restart.

An upgrade replaces the installed package; a server already running keeps the
modules it imported at startup. The presence registry records every server's
version and start time, but nothing compared a running server with what is
installed now, so an operator learned of the gap only by asking the server.

The verdict is tri-state and never `False` for a question it could not ask: a
server on a different install, or one whose row predates the install record,
is UNKNOWN. Nothing here stops a process; restarting is the client's or the
service manager's job.
"""

from __future__ import annotations

import ast
import io
import os
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jcodemunch_mcp.storage import process_registry as pr

PKG = str(Path("/opt/tools/jcm/site-packages/jcodemunch_mcp"))


def _entry(version="1.108.442", package_dir=PKG, started=None):
    started = started or datetime.now(timezone.utc) - timedelta(hours=2)
    return pr.ProcessEntry(
        pid=4242,
        client_id="svc",
        transport="streamable-http",
        version=version,
        started_at=started.isoformat(),
        package_dir=package_dir,
    )


def test_copied_install_behind_the_installed_version_requires_restart():
    v = pr.restart_verdict(
        _entry("1.108.442"),
        installed_version="1.108.444",
        package_dir=PKG,
        source_changed_at=None,
    )
    assert v["restart_required"] is True
    assert v["basis"] == "version"
    assert "1.108.442" in v["reason"] and "1.108.444" in v["reason"]


def test_copied_install_on_the_installed_version_needs_no_restart():
    v = pr.restart_verdict(
        _entry("1.108.444"),
        installed_version="1.108.444",
        package_dir=PKG,
        source_changed_at=None,
    )
    assert v["restart_required"] is False
    assert v["basis"] == "version"


def test_a_server_on_another_install_is_unknown_not_current():
    v = pr.restart_verdict(
        _entry("1.108.444", package_dir=str(Path("/elsewhere/jcodemunch_mcp"))),
        installed_version="1.108.444",
        package_dir=PKG,
        source_changed_at=None,
    )
    assert v["restart_required"] is None
    assert "different install" in v["reason"]


def test_a_row_without_an_install_record_is_unknown():
    v = pr.restart_verdict(
        _entry("1.108.442", package_dir=None),
        installed_version="1.108.444",
        package_dir=PKG,
        source_changed_at=None,
    )
    assert v["restart_required"] is None


def test_an_unknown_installed_version_is_unknown():
    v = pr.restart_verdict(
        _entry("1.108.442"),
        installed_version="unknown",
        package_dir=PKG,
        source_changed_at=None,
    )
    assert v["restart_required"] is None


def test_source_install_uses_the_source_mtime_not_the_version():
    # On a source install every process reports the same metadata version, so
    # the version cannot answer; the source mtime against the start time can.
    started = datetime.now(timezone.utc) - timedelta(hours=2)
    later = (started + timedelta(minutes=5)).timestamp()
    earlier = (started - timedelta(minutes=5)).timestamp()
    same = _entry("1.108.444", started=started)
    stale = pr.restart_verdict(
        same,
        installed_version="1.108.444",
        package_dir=PKG,
        source_changed_at=later,
    )
    fresh = pr.restart_verdict(
        same,
        installed_version="1.108.444",
        package_dir=PKG,
        source_changed_at=earlier,
    )
    assert stale["restart_required"] is True and stale["basis"] == "source_mtime"
    assert fresh["restart_required"] is False and fresh["basis"] == "source_mtime"


def test_register_records_the_install_and_the_reader_returns_it(tmp_path):
    try:
        assert pr.register("stdio", "1.108.444", str(tmp_path)) is not None
        rows = [e for e in pr.live_processes(str(tmp_path)) if e.pid == os.getpid()]
    finally:
        pr.unregister()
    assert len(rows) == 1
    import jcodemunch_mcp

    assert rows[0].package_dir == str(Path(jcodemunch_mcp.__file__).parent)


def test_serving_report_counts_the_three_verdicts(tmp_path, monkeypatch):
    rows = [
        _entry("1.108.442"),
        _entry("1.108.444"),
        _entry("1.108.444", package_dir=None),
    ]
    for i, r in enumerate(rows):
        r.pid = 5000 + i
    monkeypatch.setattr(pr, "live_processes", lambda *a, **k: rows)
    monkeypatch.setattr(pr, "_package_dir", lambda: PKG)
    monkeypatch.setattr(pr, "_installed_version", lambda: "1.108.444")
    monkeypatch.setattr(
        "jcodemunch_mcp.install_layout.running_source_changed_at", lambda: None
    )
    rep = pr.serving_report(str(tmp_path))
    assert rep["installed_version"] == "1.108.444"
    assert rep["restart_required"] == 1
    assert rep["current"] == 1
    assert rep["unknown"] == 1
    assert [s["restart_required"] for s in rep["servers"]] == [True, False, None]


def test_install_status_renders_restart_required():
    from jcodemunch_mcp.cli import init

    block = {
        "installed_version": "1.108.444",
        "servers": [
            {
                "pid": 1,
                "client_id": "svc",
                "transport": "streamable-http",
                "version": "1.108.442",
                "restart_required": True,
                "reason": "serving 1.108.442; installed is 1.108.444",
            },
            {
                "pid": 2,
                "client_id": "cli",
                "transport": "stdio",
                "version": "1.108.444",
                "restart_required": None,
                "reason": "serves a different install",
            },
        ],
        "restart_required": 1,
        "current": 0,
        "unknown": 1,
    }
    buf = io.StringIO()
    with redirect_stdout(buf):
        init._print_servers(block)
    out = buf.getvalue()
    assert "RESTART REQUIRED" in out and "pid 1" in out
    assert "[?]" in out and "pid 2" in out


def test_nothing_in_the_registry_stops_a_process():
    # Restarting belongs to the client or the service manager (#1435 asks for
    # exactly this). A verdict that could kill a shared server is the worse error.
    src = Path(pr.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {"kill", "killpg", "terminate", "send_signal", "TerminateProcess"}
    called = {
        n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert not (called & banned), called & banned
