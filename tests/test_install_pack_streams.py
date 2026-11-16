"""install-pack streams the download and the archive members; neither is held whole.

It read the whole response into memory (`resp.content`), wrote it to a temp file,
then read each archive member whole before writing it out. On the free Node.js
pack (a 150 MB download holding a 759 MB index) that peaked at 1,758 MB of
allocations, enough to fail an install on a small machine or container. The
download now goes to the temp file in chunks and each member is copied in
chunks, so peak memory no longer grows with the pack.
"""

from __future__ import annotations

import io
import json
import tracemalloc
import zipfile
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from jcodemunch_mcp.cli.install_pack import _install_pack

MEMBER_BYTES = 64 * 1024 * 1024  # 64 MB of zeros: compresses to almost nothing
PEAK_LIMIT = 16 * 1024 * 1024


def _pack_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bigpack/manifest.json", json.dumps({"name": "Big", "total_symbols": 1}))
        with zf.open("bigpack/big.textfilter", "w") as member:
            block = b"\0" * (1024 * 1024)
            for _ in range(MEMBER_BYTES // len(block)):
                member.write(block)
    return buf.getvalue()


def _response(body: bytes):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/zip", "X-Pack-Version": "1.0.0"}
    resp.content = body
    resp.iter_bytes = lambda chunk_size=None: (
        body[i:i + 65536] for i in range(0, len(body), 65536)
    )
    return resp


def _serve(mock_httpx, body: bytes) -> None:
    resp = _response(body)
    mock_httpx.get.return_value = resp

    @contextmanager
    def stream(*_a, **_k):
        yield resp

    mock_httpx.stream.side_effect = stream


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_a_large_member_is_copied_without_holding_it_whole(mock_httpx, tmp_path, monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
    _serve(mock_httpx, _pack_zip())

    tracemalloc.start()
    try:
        assert _install_pack("bigpack", base_path=tmp_path) == 0
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    landed = tmp_path / "big.textfilter"
    assert landed.stat().st_size == MEMBER_BYTES
    assert peak < PEAK_LIMIT, f"install peaked at {peak / 1e6:.1f} MB of allocations"


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_the_download_never_reads_the_whole_body(mock_httpx, tmp_path, monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
    body = _pack_zip()
    _serve(mock_httpx, body)
    resp = mock_httpx.get.return_value
    type(resp).content = property(lambda _s: (_ for _ in ()).throw(AssertionError("read whole body")))

    assert _install_pack("bigpack", base_path=tmp_path) == 0
    assert mock_httpx.stream.called


def _serve_cut_off(mock_httpx, tmp_path, monkeypatch, error):
    """Serve a download that fails after its first chunk; temp files land in tmp_path/tmp."""
    import tempfile

    import httpx as real_httpx

    monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()
    # tempfile reads TMPDIR, TEMP and TMP in turn; setting the cached value
    # directly makes every platform write here, whichever of them is set.
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))
    mock_httpx.HTTPError = real_httpx.HTTPError
    body = _pack_zip()
    resp = _response(body)

    def broken(chunk_size=None):
        yield body[:1000]
        raise error

    resp.iter_bytes = broken

    @contextmanager
    def stream(*_a, **_k):
        yield resp

    mock_httpx.stream.side_effect = stream
    mock_httpx.get.return_value = resp
    return temp_dir


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_a_download_cut_off_midway_leaves_no_temp_file(mock_httpx, tmp_path, monkeypatch):
    import httpx as real_httpx

    temp_dir = _serve_cut_off(mock_httpx, tmp_path, monkeypatch, real_httpx.ReadError("connection reset"))

    assert _install_pack("bigpack", base_path=tmp_path / "idx") == 1
    assert list(temp_dir.iterdir()) == []


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_an_interrupted_download_leaves_no_temp_file(mock_httpx, tmp_path, monkeypatch):
    import pytest

    temp_dir = _serve_cut_off(mock_httpx, tmp_path, monkeypatch, KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        _install_pack("bigpack", base_path=tmp_path / "idx")
    assert list(temp_dir.iterdir()) == []


def test_every_install_test_serves_the_streamed_download():
    """A test that patches httpx and installs a pack must script `httpx.stream`.

    Under `@patch(...install_pack.httpx)` an unscripted `stream` is a bare
    MagicMock: its content type is not JSON or a zip, so the install returns 1
    at "Unexpected response" before it reads a byte. Every `rc == 1` assertion
    then passes without reaching the code it guards (path traversal, a bad zip,
    a refused download). So each such test routes `stream` through its `get`
    mock with `stream_through_get`, or scripts `stream` itself.
    """
    import ast
    import pathlib

    offenders = []
    for path in sorted(pathlib.Path(__file__).parent.glob("test_*.py")):
        src = path.read_text(encoding="utf-8")
        if "install_pack" not in src:
            continue
        for fn in ast.walk(ast.parse(src)):
            if not isinstance(fn, ast.FunctionDef) or not fn.name.startswith("test_"):
                continue
            body = ast.get_source_segment(src, fn) or ""
            decorators = " ".join(ast.get_source_segment(src, d) or "" for d in fn.decorator_list)
            if "install_pack.httpx" not in decorators + body:
                continue
            if "_install_pack(" not in body:
                continue
            # This file's own `_serve` helpers script `stream` themselves.
            if any(s in body for s in ("stream_through_get(", ".stream", "_serve(", "_serve_cut_off(")):
                continue
            offenders.append(f"{path.name}:{fn.lineno} {fn.name}")
    assert offenders == [], "install tests with an unscripted httpx.stream: " + ", ".join(offenders)
