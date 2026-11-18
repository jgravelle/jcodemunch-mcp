"""A failed watch-service probe reads as unknown, never as inactive.

On Linux, `systemctl --user is-active` needs the user's session bus. A process
started without `DBUS_SESSION_BUS_ADDRESS` or `XDG_RUNTIME_DIR` (a managed MCP
host, a cron job, an ssh shell) gets "Failed to connect to user scope bus"
on stderr and a nonzero exit. `_status_systemd` compared the output with
"active" and reported `active: false`, so a probe that observed nothing was
answered as "the watcher is not running", and `suggest_corrections` told the
user to install a service that may be running fine.

The same contract was reported against jdocmunch-mcp (jgravelle/jdocmunch-mcp#206,
@dkiaulakis); this repository has its own copy of the probe. The cases are the
reporter's: a successful active query, a successful inactive or missing-unit
query, a missing user bus, a permission or transport failure, and a timeout.
The state is read from what `is-active` printed, never from its exit code.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jcodemunch_mcp import service_installer as si

NO_BUS = (
    "Failed to connect to user scope bus via local transport: $DBUS_SESSION_BUS_ADDRESS "
    "and $XDG_RUNTIME_DIR not defined (consider using --machine=<user>@.host --user "
    "to connect to bus of other user)"
)


@pytest.fixture
def systemd(monkeypatch, tmp_path: Path):
    """A systemctl that answers what the test says, and a unit file the test owns."""
    unit = tmp_path / "jcodemunch-watch.service"
    monkeypatch.setattr(si, "_systemd_unit_path", lambda: unit)
    monkeypatch.setattr(si.shutil, "which", lambda name: "/usr/bin/systemctl")
    answer: dict = {}

    def run(args, **kw):
        if "raise" in answer:
            raise answer["raise"]
        return subprocess.CompletedProcess(
            args, answer.get("rc", 0), answer.get("out", ""), answer.get("err", "")
        )

    monkeypatch.setattr(si.subprocess, "run", run)
    return unit, answer


def test_a_successful_active_query_is_active(systemd):
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer.update(out="active\n", rc=0)
    s = si._status_systemd()
    assert s["active"] is True and s["status"] == "active", s


def test_a_successful_inactive_query_is_inactive(systemd):
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer.update(out="inactive\n", rc=3)
    s = si._status_systemd()
    assert s["active"] is False and s["status"] == "inactive", s
    assert "probe_error" not in s


def test_a_failed_unit_is_inactive_and_keeps_its_state(systemd):
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer.update(out="failed\n", rc=3)
    s = si._status_systemd()
    assert (
        s["active"] is False and s["status"] == "inactive" and s["state"] == "failed"
    ), s


def test_a_missing_unit_is_not_installed(systemd):
    unit, answer = systemd
    answer.update(out="inactive\n", rc=4)
    s = si._status_systemd()
    assert s["active"] is False and s["status"] == "not_installed", s


def test_a_missing_user_bus_is_unknown_and_keeps_the_diagnostic(systemd):
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer.update(out="", err=NO_BUS + "\n", rc=1)
    s = si._status_systemd()
    assert s["active"] is None and s["status"] == "unknown", s
    assert s["probe_error"] == "no_user_bus", s
    assert s["detail"] == NO_BUS, s


def test_a_permission_failure_is_unknown(systemd):
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer.update(out="", err="Failed to connect to bus: Permission denied\n", rc=1)
    s = si._status_systemd()
    assert s["active"] is None and s["status"] == "unknown", s
    assert s["probe_error"] == "permission_denied", s


def test_an_unrecognised_failure_is_unknown(systemd):
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer.update(out="", err="Transport endpoint is not connected\n", rc=1)
    s = si._status_systemd()
    assert s["active"] is None and s["status"] == "unknown", s
    assert s["probe_error"] == "probe_failed", s


def test_a_probe_timeout_is_unknown(systemd):
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer["raise"] = subprocess.TimeoutExpired(["systemctl"], 5)
    s = si._status_systemd()
    assert s["active"] is None and s["status"] == "unknown", s
    assert s["probe_error"] == "timeout", s


def test_a_nonzero_exit_alone_is_not_inactive(systemd):
    """The reporter's last rule: classify the output, not the exit code."""
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer.update(out="", err="", rc=1)
    s = si._status_systemd()
    assert s["active"] is None and s["status"] == "unknown", s


def test_no_systemctl_means_no_systemd_service(monkeypatch, tmp_path):
    monkeypatch.setattr(si, "_systemd_unit_path", lambda: tmp_path / "x.service")
    monkeypatch.setattr(si.shutil, "which", lambda name: None)
    s = si._status_systemd()
    assert s["active"] is False and s["status"] == "not_installed", s


def test_get_watch_status_reports_a_raising_probe_as_unknown(monkeypatch, tmp_path):
    from jcodemunch_mcp.tools import get_watch_status as gws

    def boom():
        raise OSError("probe exploded")

    monkeypatch.setattr(gws, "service_status", boom)
    out = gws.get_watch_status(storage_path=str(tmp_path), check_completeness=False)
    assert out["service"]["active"] is None, out["service"]
    assert out["service"]["status"] == "unknown", out["service"]
    assert "probe exploded" in out["service"]["detail"], out["service"]


def test_every_platform_answer_carries_a_status(monkeypatch):
    monkeypatch.setattr(si.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        si, "_status_launchd", lambda: {"platform": "launchd", "active": True}
    )
    assert si.service_status()["status"] == "active"
    monkeypatch.setattr(
        si, "_status_launchd", lambda: {"platform": "launchd", "active": False}
    )
    assert si.service_status()["status"] == "inactive"


@pytest.mark.parametrize(
    "service,says",
    [
        ({"active": True, "status": "active"}, "is running"),
        ({"active": False, "status": "inactive"}, "isn't running"),
        (
            {"active": None, "status": "unknown", "probe_error": "no_user_bus"},
            "could not be read",
        ),
    ],
)
def test_suggest_corrections_does_not_call_an_unknown_service_stopped(
    monkeypatch, service, says
):
    from jcodemunch_mcp.tools import get_watch_status as gws
    from jcodemunch_mcp.tools import suggest_corrections as sc

    monkeypatch.setattr(gws, "get_watch_status", lambda **kw: {"service": service})
    out = sc._freshness_correction(
        {"severity": "high", "evidence": {"stale_rate": 0.5}}, None, None
    )
    assert says in out["recommended_action"], out["recommended_action"]
