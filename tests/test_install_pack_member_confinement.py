"""A Starter Pack archive member must not write outside the install directory.

Issue #447, reported by @elfrost with a working fix in PR #443 that we could not
merge for CLA reasons. `install-pack`'s pre-scan rejected a leading separator and
`..` anywhere in the member name, which is necessary and not sufficient: a member
named `C:/Windows/Temp/evil.txt` carries neither, and `base / relative` with an
absolute `relative` DISCARDS `base`. The archive is served over TLS from a host
we control, so this is a compromise-amplifier rather than a drive-by — and that
is exactly the class where the guard has to hold anyway.

⚠ **The assertion here is CONFINEMENT, never that a particular string is
refused.** `C:/Windows/Temp/evil.txt` is absolute on Windows and an ordinary
relative name on Linux and macOS, where extracting it under the base is correct.
Pinning the refusal would encode platform trivia as if it were a security
property; pinning "nothing appeared outside the base" is the property.
"""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jcodemunch_mcp.cli.install_pack import _install_pack
from jcodemunch_mcp.security import resolve_within
from tests.conftest_helpers import stream_through_get

_SRC = Path(__file__).parent.parent / "src" / "jcodemunch_mcp"


def _pack_zip(files: dict[str, bytes], pack_id: str = "testpack") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(f"{pack_id}/{name}", content)
    return buf.getvalue()


def _zip_response(zip_bytes: bytes):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/zip", "X-Pack-Version": "1.0.0"}
    resp.content = zip_bytes
    resp.raise_for_status = MagicMock()
    return resp


# ── resolve_within: the rule itself ───────────────────────────────────────

def test_an_absolute_member_does_not_escape_the_base(tmp_path):
    """The mechanism, stated directly: joining an absolute path drops the base."""
    base = tmp_path / "install"
    base.mkdir()
    absolute = str((tmp_path / "outside" / "evil.txt").resolve())

    # Precondition — this is what makes the naive join dangerous.
    assert Path(str(base / absolute)) != base / "outside" / "evil.txt"

    assert resolve_within(base, absolute) is None


def test_a_parent_traversal_does_not_escape_the_base(tmp_path):
    base = tmp_path / "install"
    base.mkdir()
    assert resolve_within(base, os.path.join("..", "evil.txt")) is None


def test_an_ordinary_relative_member_resolves_inside(tmp_path):
    base = tmp_path / "install"
    base.mkdir()
    dest = resolve_within(base, "licenses/nodejs/LICENSE")
    assert dest is not None
    assert dest == (base / "licenses" / "nodejs" / "LICENSE").resolve()


def test_a_caller_supplied_resolved_base_does_not_change_the_rule(tmp_path):
    """The hot-path caching form must decide identically to the plain form."""
    base = tmp_path / "install"
    base.mkdir()
    cached = str(base.resolve())
    for relative in ("a/b.txt", os.path.join("..", "evil.txt")):
        assert resolve_within(base, relative, base_resolved=cached) == resolve_within(
            base, relative
        )


def test_a_path_that_cannot_be_resolved_is_refused_rather_than_admitted(tmp_path):
    """Could-not-establish is never a pass — the same asymmetry as #209.

    ⚠ The first version of this asserted that an embedded NUL byte returns None,
    which is an accident of how Windows resolves a non-existent path rather than
    the rule: it passed serially and failed under xdist, where the longer worker
    temp path took the other branch. **The rule is that a raising resolve refuses;
    which inputs happen to raise is the OS's business.**
    """
    base = tmp_path / "install"
    base.mkdir()

    def _boom(self, *args, **kwargs):
        raise OSError("resolution unavailable")

    with patch.object(Path, "resolve", _boom):
        assert resolve_within(base, "ordinary/name.txt") is None


# ── the reported call site ────────────────────────────────────────────────

@pytest.mark.parametrize("spelling", ["forward_slash", "backslash", "parent_traversal"])
@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_no_member_spelling_writes_outside_the_install_directory(
    mock_httpx, spelling, tmp_path, monkeypatch
):
    """⚠ The hostile member is built from `tmp_path`, never from a real system path.

    The first version of this test named `C:/Windows/Temp/evil.txt` — the
    reported attack verbatim. Two things were wrong with that, and the second is
    the one worth remembering. The escape landed outside `tmp_path`, so the
    assertion could not see it and the test PASSED against the unfixed source.
    And the non-vacuity pass, which runs the unfixed source ON PURPOSE, wrote a
    real file into a real Windows system directory. **A test for an arbitrary-write
    defect executes that defect every time you prove the test is not vacuous, so
    the target has to be somewhere the test owns.**
    """
    stream_through_get(mock_httpx)
    monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
    base = tmp_path / "install"
    sentinel = tmp_path / "outside"
    sentinel.mkdir()
    target = sentinel / "evil.txt"

    member = {
        # Absolute for THIS platform, and containing neither a leading separator
        # on Windows nor `..` — the shape the pre-scan cannot see.
        "forward_slash": str(target).replace("\\", "/"),
        "backslash": str(target),
        "parent_traversal": "sub/../../../../evil.txt",
    }[spelling]

    mock_httpx.get.return_value = _zip_response(
        _pack_zip({
            "manifest.json": json.dumps({"name": "T", "total_symbols": 1}).encode(),
            member: b"pwned",
        })
    )

    rc = _install_pack("testpack", base_path=base)

    assert not target.exists(), f"member {member!r} wrote outside the install directory"
    escaped = [
        p for p in tmp_path.rglob("*")
        if p.is_file() and base.resolve() not in p.resolve().parents
    ]
    assert escaped == [], f"member {member!r} escaped to {escaped}"
    # rc is 1 where the member is genuinely absolute for this platform, and 0
    # where it is an ordinary relative name that landed inside. Both are correct;
    # only escaping is not.
    assert rc in (0, 1)


@pytest.mark.skipif(sys.platform != "win32", reason="drive-absolute names need Windows")
@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_a_drive_absolute_member_is_refused_on_windows(mock_httpx, tmp_path, monkeypatch):
    """The reported attack, on the platform where the name is actually absolute."""
    stream_through_get(mock_httpx)
    monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
    target = tmp_path / "outside" / "evil.txt"
    target.parent.mkdir()
    mock_httpx.get.return_value = _zip_response(
        _pack_zip({
            "manifest.json": json.dumps({"name": "T", "total_symbols": 1}).encode(),
            str(target).replace("\\", "/"): b"pwned",
        })
    )

    assert _install_pack("testpack", base_path=tmp_path / "install") == 1
    assert not target.exists()


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_an_ordinary_pack_still_installs(mock_httpx, tmp_path, monkeypatch):
    """The control. A guard that refused everything would satisfy every test above."""
    stream_through_get(mock_httpx)
    monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
    base = tmp_path / "install"
    mock_httpx.get.return_value = _zip_response(
        _pack_zip({
            "manifest.json": json.dumps({"name": "T", "total_symbols": 1}).encode(),
            "licenses/nodejs/LICENSE": b"MIT",
        })
    )

    assert _install_pack("testpack", base_path=base) == 0
    assert (base / "licenses" / "nodejs" / "LICENSE").read_bytes() == b"MIT"


# ── one definition of the rule ────────────────────────────────────────────

def test_confinement_is_defined_only_in_security_py():
    """Three spellings of this rule existed; a fourth is how they drift apart.

    `IndexStore` and `SQLiteIndexStore` each carried their own resolve-and-compare
    block. Both now delegate, so `commonpath` appearing anywhere else in `src/`
    means someone rebuilt the rule instead of calling it.
    """
    offenders = sorted(
        str(path.relative_to(_SRC))
        for path in _SRC.rglob("*.py")
        if path.name != "security.py" and "commonpath" in path.read_text(encoding="utf-8")
    )
    assert offenders == [], (
        f"path-confinement re-implemented in {offenders}; import "
        "`security.resolve_within` instead"
    )
