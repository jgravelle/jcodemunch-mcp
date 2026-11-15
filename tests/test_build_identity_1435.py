"""#1435 (@dkiaulakis): two servers on the same version can be told apart by build.

`version` is the recorded metadata number, so two installs of one release
built from different files report the same string. The identity resource and
`install-status` now carry `build`, a digest of the installed distribution's
RECORD, which lists every installed file with its own hash. Where no RECORD
describes the loaded code (a source or editable install, or a process that
loaded another copy), `build` is null and `build_reason` says why: an unknown
build is never presented as a known one.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from jcodemunch_mcp import runtime_identity as ri
from jcodemunch_mcp.cli import init as init_mod
from jcodemunch_mcp.storage import process_registry as pr

RECORD = (
    "jcodemunch_mcp/__init__.py,sha256=AAAA,120\n"
    "jcodemunch_mcp/server.py,sha256=BBBB,900000\n"
    "jcodemunch_mcp-1.108.447.dist-info/RECORD,,\n"
)


class _Dist:
    def __init__(self, root: Path, record):
        self.root, self.record = root, record

    def locate_file(self, rel):
        return self.root / rel

    def read_text(self, name):
        assert name == "RECORD"
        return self.record


@pytest.fixture
def installed(tmp_path):
    site = tmp_path / "site-packages"
    init = site / "jcodemunch_mcp" / "__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text("")
    return site, init


def test_an_installed_build_is_a_digest_of_its_record(installed):
    site, init = installed
    got = ri._probe_build(init, _Dist(site, RECORD))
    assert set(got) == {"build"}
    assert got["build"].startswith("sha256:") and len(got["build"]) == len("sha256:") + 16
    assert ri._probe_build(init, _Dist(site, RECORD)) == got  # stable


def test_the_same_version_built_from_other_files_has_another_build(installed):
    site, init = installed
    other = RECORD.replace("sha256=BBBB,900000", "sha256=CCCC,900016")
    assert ri._probe_build(init, _Dist(site, RECORD))["build"] != ri._probe_build(init, _Dist(site, other))["build"]


def test_one_build_installed_two_ways_has_one_build(installed):
    """Rows that depend on the install, not the build, are not hashed.

    A launcher script embeds the interpreter path, INSTALLER names pip or uv,
    REQUESTED depends on how the package was asked for, and .pyc files exist
    per Python version. Identical code must read as one build.
    """
    site, init = installed
    by_pip = RECORD + (
        "../../Scripts/jcodemunch-mcp.exe,sha256=PIPLAUNCH,108000\n"
        "jcodemunch_mcp-1.108.447.dist-info/INSTALLER,sha256=PIP,4\n"
        "jcodemunch_mcp-1.108.447.dist-info/REQUESTED,sha256=,0\n"
        "jcodemunch_mcp/__pycache__/server.cpython-310.pyc,,\n"
    )
    by_uv = (
        "jcodemunch_mcp/server.py,sha256=BBBB,900000\n"  # another row order
        "jcodemunch_mcp/__init__.py,sha256=AAAA,120\n"
        "../../bin/jcodemunch-mcp,sha256=UVLAUNCH,300\n"
        "jcodemunch_mcp-1.108.447.dist-info/INSTALLER,sha256=UV,2\n"
        "jcodemunch_mcp/__pycache__/server.cpython-313.pyc,sha256=PYC,5\n"
        "jcodemunch_mcp-1.108.447.dist-info/RECORD,,\n"
    )
    one = ri._probe_build(init, _Dist(site, by_pip))["build"]
    assert one is not None
    assert one == ri._probe_build(init, _Dist(site, by_uv))["build"]
    assert one == ri._probe_build(init, _Dist(site, RECORD))["build"]


def test_the_build_is_read_at_server_startup_not_on_first_use():
    """Read lazily, an in-place upgrade before the first identity read would
    make an old process report the new install's build."""
    src = (Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp" / "server.py").read_text(encoding="utf-8")
    at = src.index("_get_build()")
    assert at < src.index("_register_process(\n"), "the build must be read before the registry can fail"


def test_under_the_source_tree_the_default_probe_reports_a_source_install():
    """No fake distribution: the real default path, as this suite runs it."""
    from jcodemunch_mcp.install_layout import is_source_layout

    if not is_source_layout(Path(ri.__file__).parent / "__init__.py"):
        pytest.skip("not running from a source tree")
    got = ri._probe_build()
    assert got["build"] is None and got["build_reason"].startswith("source install")


def test_a_source_install_has_no_build_and_says_why(tmp_path):
    init = tmp_path / "src" / "jcodemunch_mcp" / "__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text("")
    (tmp_path / "pyproject.toml").write_text("")
    got = ri._probe_build(init, _Dist(tmp_path, RECORD))
    assert got["build"] is None and "source install" in got["build_reason"]


def test_an_editable_record_lists_no_package_files_so_there_is_no_build(installed):
    site, init = installed
    editable = "__editable__.jcodemunch_mcp-1.108.447.pth,sha256=DDDD,80\n"
    got = ri._probe_build(init, _Dist(site, editable))
    assert got["build"] is None and "editable" in got["build_reason"]


def test_a_process_that_loaded_another_copy_has_no_build(installed, tmp_path):
    site, init = installed
    elsewhere = tmp_path / "other-env" / "site-packages"
    got = ri._probe_build(init, _Dist(elsewhere, RECORD))
    assert got["build"] is None and "different copy" in got["build_reason"]


def test_a_missing_or_unreadable_record_has_no_build(installed):
    site, init = installed
    assert "no RECORD" in ri._probe_build(init, _Dist(site, None))["build_reason"]

    class Broken(_Dist):
        def read_text(self, name):
            raise OSError("denied")

    got = ri._probe_build(init, Broken(site, RECORD))
    assert got["build"] is None and "OSError" in got["build_reason"]


def test_the_identity_payload_always_carries_build(monkeypatch):
    monkeypatch.setattr(ri, "_build", {"build": "sha256:0123456789abcdef"})
    p = ri.identity_payload()
    assert p["build"] == "sha256:0123456789abcdef" and "build_reason" not in p
    monkeypatch.setattr(ri, "_build", {"build": None, "build_reason": "source install: x"})
    p = json.loads(ri.identity_json())
    assert p["build"] is None and p["build_reason"] == "source install: x"


def test_a_server_records_its_build_and_install_status_shows_it(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "_build", {"build": "sha256:feedfacecafebeef"})
    path = pr.register("stdio", "1.108.447", storage_path=str(tmp_path))
    try:
        assert json.loads(path.read_text(encoding="utf-8"))["build"] == "sha256:feedfacecafebeef"
        [entry] = pr.live_processes(str(tmp_path), prune=False)
        assert entry.build == "sha256:feedfacecafebeef"
        # only install-status, a local CLI, asks for it; the model-facing sprawl report does not carry it
        assert "build" not in entry.as_dict()
        assert entry.as_dict(include_package_dir=True)["build"] == "sha256:feedfacecafebeef"
    finally:
        pr.unregister()


def test_a_row_written_before_the_build_was_recorded_reads_as_none(tmp_path):
    row = pr.ProcessEntry(pid=1, client_id="c", transport="stdio", version="1.108.446", started_at="")
    assert row.build is None and "build" not in row.as_dict(include_package_dir=True)


def test_install_status_prints_each_servers_build_beside_the_installed_one():
    block = {
        "installed_version": "1.108.447",
        "installed_build": "sha256:1111111111111111",
        "servers": [
            {"pid": 7, "client_id": "svc", "transport": "streamable-http", "version": "1.108.447",
             "build": "sha256:2222222222222222", "restart_required": None, "reason": "different install"},
            {"pid": 8, "client_id": "old", "transport": "stdio", "version": "1.108.446",
             "restart_required": True, "reason": "behind"},
        ],
        "restart_required": 1,
    }
    buf = io.StringIO()
    with redirect_stdout(buf):
        init_mod._print_servers(block)
    out = buf.getvalue()
    assert "installed 1.108.447, build sha256:1111111111111111" in out
    assert "recorded 1.108.447, build sha256:2222222222222222)" in out
    assert "recorded 1.108.446)" in out  # an unrecorded build is left out, never printed as one
