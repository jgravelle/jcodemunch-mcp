"""Tests for the install-pack CLI subcommand."""

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jcodemunch_mcp.cli.install_pack import (
    STARTER_PACK_API,
    _install_pack,
    _list_packs,
    _mask_license,
    _safe_extract_path,
    run_install_pack,
)


# ── Pack host ─────────────────────────────────────────────────────────────
#
# The client must point at the host the pack pipeline deploys to. It pointed at
# a legacy host that stopped being refreshed, so `install-pack nodejs` served an
# artifact three months older than the one CI had built -- and every signal a
# user could see (a 200, a valid zip, a manifest, a symbol count) said it had
# worked. Nothing in the product could distinguish a current pack from an
# abandoned one, so the host is pinned by name here instead.

PACK_HOST = "jcodemunch.com"
_RETIRED_PACK_HOSTS = ("j.gravelle.us",)


def test_starter_pack_api_points_at_the_deploy_host():
    from urllib.parse import urlparse

    assert urlparse(STARTER_PACK_API).netloc == PACK_HOST


def test_no_retired_pack_host_in_the_install_pack_module():
    """Catch the catalog URL, the pricing link and any new sibling at once.

    The constant is only one of the places the legacy host was spelled; a
    reverting edit is as likely to land on a printed URL as on the API base.
    """
    source = Path(
        __import__("jcodemunch_mcp.cli.install_pack", fromlist=["__file__"]).__file__
    ).read_text(encoding="utf-8")
    body = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    for retired in _RETIRED_PACK_HOSTS:
        assert retired not in body, f"{retired} is no longer refreshed; use {PACK_HOST}"


# ── _mask_license ─────────────────────────────────────────────────────────

def test_mask_license_long():
    assert _mask_license("ABCD-1234-EFGH-5678") == "ABCD****5678"


def test_mask_license_short():
    assert _mask_license("ABCD") == "ABCD****"


def test_mask_license_exact_8():
    assert _mask_license("12345678") == "1234****"


# ── _list_packs ───────────────────────────────────────────────────────────

def _mock_catalog_response(packs):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"packs": packs}
    resp.raise_for_status = MagicMock()
    return resp


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_list_packs_empty(mock_httpx):
    mock_httpx.get.return_value = _mock_catalog_response([])
    assert _list_packs() == 0


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_list_packs_shows_entries(mock_httpx, capsys):
    mock_httpx.get.return_value = _mock_catalog_response([
        {"id": "fastapi", "name": "FastAPI Starter", "symbols": 5000, "free": True},
        {"id": "express", "name": "Express Starter", "symbols": 3000, "free": False},
    ])
    assert _list_packs() == 0
    out = capsys.readouterr().out
    assert "fastapi" in out
    assert "express" in out
    assert "FREE" in out


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_listed_download_url_ignores_the_servers_own_field(mock_httpx, capsys):
    """The printed URL is the one install-pack will fetch, not the catalog's.

    A server that still advertises a retired host would otherwise put that
    address on the user's screen, where it is the line they copy -- the same
    stale-host defect surfacing through a printed string instead of a request.
    """
    mock_httpx.get.return_value = _mock_catalog_response([
        {
            "id": "fastapi",
            "name": "FastAPI Starter",
            "symbols": 5000,
            "free": True,
            "download_url": "https://retired.example/old/index.php?action=download&pack=fastapi",
        },
    ])
    assert _list_packs() == 0
    out = capsys.readouterr().out
    assert "retired.example" not in out
    assert f"{STARTER_PACK_API}?action=download&pack=fastapi" in out


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_list_packs_names_the_upstream_licence(mock_httpx, capsys):
    """A pack is somebody else's source, indexed. Name the terms before the
    download, not only in a file the user finds afterwards."""
    mock_httpx.get.return_value = _mock_catalog_response([
        {
            "id": "nodejs", "name": "Node", "symbols": 100, "free": True,
            "licenses": [
                {"repo": "nodejs/node", "spdx": "MIT"},
                {"repo": "django/django", "spdx": "BSD-3-Clause"},
                {"repo": "expressjs/express", "spdx": "MIT"},
            ],
        },
    ])
    assert _list_packs() == 0
    out = capsys.readouterr().out
    assert "Upstream licence: BSD-3-Clause, MIT" in out, "deduped and sorted"


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_list_packs_silent_when_catalog_has_no_licences(mock_httpx, capsys):
    """Forward-compatible: a catalog built before this field must not print a
    blank or a guess."""
    mock_httpx.get.return_value = _mock_catalog_response([
        {"id": "nodejs", "name": "Node", "symbols": 100, "free": True},
    ])
    assert _list_packs() == 0
    assert "Upstream licence" not in capsys.readouterr().out


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_list_packs_network_error(mock_httpx):
    import httpx as real_httpx
    mock_httpx.HTTPError = real_httpx.HTTPError
    mock_httpx.get.side_effect = real_httpx.ConnectError("offline")
    assert _list_packs() == 1


# ── _install_pack ─────────────────────────────────────────────────────────

def _make_pack_zip(files: dict[str, bytes], pack_id: str = "testpack") -> bytes:
    """Create an in-memory zip with pack_id/ prefix and given files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(f"{pack_id}/{name}", content)
    return buf.getvalue()


def _mock_zip_response(zip_bytes: bytes, pack_version: str = "1.0.0"):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {
        "content-type": "application/zip",
        "X-Pack-Version": pack_version,
    }
    resp.content = zip_bytes
    resp.raise_for_status = MagicMock()
    return resp


def _mock_error_response(error_msg: str, extra: dict | None = None):
    body = {"error": error_msg}
    if extra:
        body.update(extra)
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/json"}
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    return resp


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_install_extracts_and_reports_the_upstream_licences(
    mock_httpx, tmp_path, monkeypatch, capsys
):
    """The licence text must land on disk and be pointed at, not merely bundled.

    A pack redistributes third-party source; the terms travel with it, and the
    user should not have to go looking to find out what they are.
    """
    monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
    manifest = {
        "name": "Node", "total_symbols": 42, "repos": ["nodejs/node"],
        "licenses": [{
            "repo": "nodejs/node", "spdx": "MIT",
            "files": ["licenses/nodejs-node/LICENSE"],
            "digest": "abc", "commit": "deadbeef",
        }],
    }
    zip_bytes = _make_pack_zip({
        "manifest.json": json.dumps(manifest).encode(),
        "nodejs-node.db": b"fake-index-data",
        "licenses/nodejs-node/LICENSE": b"Node.js is licensed for use as follows:",
    })
    mock_httpx.get.return_value = _mock_zip_response(zip_bytes)

    assert _install_pack("nodejs", base_path=tmp_path) == 0

    landed = tmp_path / "licenses" / "nodejs-node" / "LICENSE"
    assert landed.exists(), "licence text must be extracted, not just carried"
    assert landed.read_bytes().startswith(b"Node.js is licensed")

    out = capsys.readouterr().out
    assert "nodejs/node: MIT" in out
    # And the marker records the terms this install happened under.
    marker = json.loads((tmp_path / ".pack-nodejs.json").read_text(encoding="utf-8"))
    assert marker["licenses"][0]["commit"] == "deadbeef"


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_install_pack_happy_path(mock_httpx, tmp_path, monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
    manifest = {"name": "Test Pack", "total_symbols": 42, "repos": ["org/repo"]}
    zip_bytes = _make_pack_zip({
        "manifest.json": json.dumps(manifest).encode(),
        "local/test-abc123.db": b"fake-index-data",
    })
    mock_httpx.get.return_value = _mock_zip_response(zip_bytes)

    result = _install_pack("testpack", base_path=tmp_path)
    assert result == 0

    # Index file extracted
    assert (tmp_path / "local" / "test-abc123.db").exists()
    assert (tmp_path / "local" / "test-abc123.db").read_bytes() == b"fake-index-data"

    # Marker written
    marker = tmp_path / ".pack-testpack.json"
    assert marker.exists()
    marker_data = json.loads(marker.read_text())
    assert marker_data["name"] == "Test Pack"
    assert marker_data["installed_version"] == "1.0.0"


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_install_pack_already_installed(mock_httpx, tmp_path, monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
    marker = tmp_path / ".pack-testpack.json"
    marker.write_text("{}")

    result = _install_pack("testpack", base_path=tmp_path)
    assert result == 0
    # No HTTP call made
    mock_httpx.get.assert_not_called()


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_install_pack_force_reinstall(mock_httpx, tmp_path, monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
    marker = tmp_path / ".pack-testpack.json"
    marker.write_text("{}")

    zip_bytes = _make_pack_zip({"local/x.db": b"data"})
    mock_httpx.get.return_value = _mock_zip_response(zip_bytes)

    result = _install_pack("testpack", force=True, base_path=tmp_path)
    assert result == 0
    # `--force` must actually re-download. Asserted on the DOWNLOAD call, not
    # the total GET count: the CDN cache-key pin added a best-effort catalog
    # probe ahead of it, and a raw count turns that required change into a
    # phantom failure of an unrelated property.
    downloads = [c for c in mock_httpx.get.call_args_list if "action=download" in c.args[0]]
    assert len(downloads) == 1


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_install_pack_json_error(mock_httpx, tmp_path, monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
    mock_httpx.get.return_value = _mock_error_response(
        "License required for premium pack",
        {"get_license": "https://example.com/pricing"},
    )
    result = _install_pack("premium-pack", base_path=tmp_path)
    assert result == 1


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_install_pack_network_error(mock_httpx, tmp_path, monkeypatch):
    import httpx as real_httpx
    monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
    mock_httpx.HTTPError = real_httpx.HTTPError
    mock_httpx.get.side_effect = real_httpx.ConnectError("offline")
    result = _install_pack("testpack", base_path=tmp_path)
    assert result == 1


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_install_pack_path_traversal_rejected(mock_httpx, tmp_path, monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("testpack/../../../etc/passwd", b"pwned")
    mock_httpx.get.return_value = _mock_zip_response(buf.getvalue())

    result = _install_pack("testpack", base_path=tmp_path)
    assert result == 1


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_install_pack_bad_zip(mock_httpx, tmp_path, monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/zip", "X-Pack-Version": "1.0.0"}
    resp.content = b"this is not a zip file"
    resp.raise_for_status = MagicMock()
    mock_httpx.get.return_value = resp

    result = _install_pack("testpack", base_path=tmp_path)
    assert result == 1


# ── run_install_pack ──────────────────────────────────────────────────────

@patch("jcodemunch_mcp.cli.install_pack._list_packs", return_value=0)
def test_run_install_pack_list_flag(mock_list):
    assert run_install_pack(list_packs=True) == 0
    mock_list.assert_called_once()


@patch("jcodemunch_mcp.cli.install_pack._list_packs", return_value=0)
def test_run_install_pack_no_pack_id(mock_list):
    assert run_install_pack(pack_id=None) == 0
    mock_list.assert_called_once()


# ── Archive member confinement ────────────────────────────────────────────
#
# The pre-scan string test (leading "/", or ".." anywhere) is necessary but not
# sufficient on Windows. A member named "C:/Windows/Temp/x" contains no ".."
# and does not start with "/", yet `base / relative` yields an absolute path
# that discards `base` entirely -- so the write lands wherever the archive says.
# `_safe_extract_path` resolves and compares against the base instead, which is
# the same form `IndexStore._safe_content_path` already applies to untrusted
# repository paths on every cached write.

class TestSafeExtractPath:
    """Destination paths are confined to the install directory."""

    def test_ordinary_relative_member_resolves_under_base(self, tmp_path):
        base = tmp_path / "code-index"
        base.mkdir()
        dest = _safe_extract_path(base, "repo/symbols.db")
        assert dest is not None
        assert dest == (base / "repo" / "symbols.db").resolve()

    def test_parent_traversal_is_rejected(self, tmp_path):
        base = tmp_path / "code-index"
        base.mkdir()
        assert _safe_extract_path(base, "../escape.txt") is None
        assert _safe_extract_path(base, "a/../../escape.txt") is None

    @pytest.mark.parametrize(
        "member",
        [
            "C:/Windows/Temp/evil.txt",     # drive-absolute, forward slashes
            r"C:\Windows\Temp\evil.txt",    # drive-absolute, backslashes
            r"\\server\share\evil.txt",     # UNC-shaped
        ],
    )
    def test_absolute_member_names_are_rejected(self, tmp_path, member):
        """None of these contain ".." or a leading "/", so the string test
        passes them; joining one to the base discards the base."""
        base = tmp_path / "code-index"
        base.mkdir()
        assert _safe_extract_path(base, member) is None
