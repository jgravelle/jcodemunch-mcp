"""The local embedding model is fetched from a pinned revision and checked by SHA-256.

`download_model` fetched ``resolve/main``, so a changed upstream file would
have been downloaded and used with no check. It now fetches one fixed
revision and verifies each file against its published SHA-256. A file that
does not match is deleted and the download fails. The revision and checksums
were confirmed against the copy already cached on installs (the files are
byte-identical to that revision).

The size stated to users was "~23 MB". `model.onnx` is 90,405,214 bytes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from jcodemunch_mcp.embeddings import local_encoder as le

ROOT = Path(__file__).resolve().parents[1]


def test_the_fetch_names_a_fixed_revision_not_main():
    urls = [u for u, _ in le._files_to_fetch()]
    assert urls and all(f"/resolve/{le.MODEL_REVISION}/" in u for u in urls), urls
    assert not any("/resolve/main/" in u for u in urls)
    assert len(le.MODEL_REVISION) == 40 and all(c in "0123456789abcdef" for c in le.MODEL_REVISION)


def test_every_fetched_file_has_a_checksum():
    for _, name in le._files_to_fetch():
        digest = le.MODEL_SHA256[name]
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def test_a_file_that_does_not_match_is_deleted_and_the_download_fails(tmp_path):
    def fake(url, path):
        Path(path).write_bytes(b"not the model")

    with patch("urllib.request.urlretrieve", side_effect=fake):
        with pytest.raises(RuntimeError, match="checksum"):
            le.download_model(tmp_path, quiet=True)
    assert not (tmp_path / le.ONNX_FILENAME).exists()


def test_a_file_that_matches_is_kept(tmp_path):
    data = {le.ONNX_FILENAME: b"onnx-bytes", le.VOCAB_FILENAME: b"vocab-bytes"}
    sums = {k: hashlib.sha256(v).hexdigest() for k, v in data.items()}

    def fake(url, path):
        Path(path).write_bytes(data[Path(path).name.removesuffix(".part")])

    with patch.dict(le.MODEL_SHA256, sums), patch("urllib.request.urlretrieve", side_effect=fake):
        assert le.download_model(tmp_path, quiet=True) == tmp_path
    assert (tmp_path / le.ONNX_FILENAME).read_bytes() == b"onnx-bytes"
    assert not list(tmp_path.glob("*.part"))


def test_a_file_already_on_disk_that_does_not_match_is_fetched_again(tmp_path):
    """A truncated download, or a copy fetched before the pin, is not trusted."""
    data = {le.ONNX_FILENAME: b"onnx-bytes", le.VOCAB_FILENAME: b"vocab-bytes"}
    sums = {k: hashlib.sha256(v).hexdigest() for k, v in data.items()}
    (tmp_path / le.ONNX_FILENAME).write_bytes(b"onnx-by")  # truncated
    (tmp_path / le.VOCAB_FILENAME).write_bytes(b"vocab-bytes")  # good
    fetched = []

    def fake(url, path):
        name = Path(path).name.removesuffix(".part")
        fetched.append(name)
        Path(path).write_bytes(data[name])

    with patch.dict(le.MODEL_SHA256, sums), patch("urllib.request.urlretrieve", side_effect=fake):
        le.download_model(tmp_path, quiet=True)
    assert fetched == [le.ONNX_FILENAME]
    assert (tmp_path / le.ONNX_FILENAME).read_bytes() == b"onnx-bytes"


def test_a_bad_file_on_disk_is_gone_even_when_the_re_fetch_fails(tmp_path):
    """A mismatching file must not survive a failed re-fetch, or a search would load it."""
    sums = {le.ONNX_FILENAME: hashlib.sha256(b"good").hexdigest(), le.VOCAB_FILENAME: "0" * 64}
    (tmp_path / le.ONNX_FILENAME).write_bytes(b"bad")

    with patch.dict(le.MODEL_SHA256, sums), \
            patch("urllib.request.urlretrieve", side_effect=ConnectionError("down")):
        with pytest.raises(RuntimeError, match="Failed to download"):
            le.download_model(tmp_path, quiet=True)
    assert not (tmp_path / le.ONNX_FILENAME).exists()


def test_an_interrupted_download_leaves_no_file_behind(tmp_path):
    """Ctrl-C mid-download must not leave a file a later run would pick up."""

    def fake(url, path):
        Path(path).write_bytes(b"half")
        raise KeyboardInterrupt

    with patch("urllib.request.urlretrieve", side_effect=fake):
        with pytest.raises(KeyboardInterrupt):
            le.download_model(tmp_path, quiet=True)
    assert not list(tmp_path.iterdir())


def test_a_fetched_file_with_no_checksum_is_refused(tmp_path):
    """Fails closed: a file added to the fetch list without a digest cannot download."""
    with patch.object(le, "MODEL_SHA256", {}), patch("urllib.request.urlretrieve") as fetch:
        with pytest.raises(KeyError):
            le.download_model(tmp_path, quiet=True)
    fetch.assert_not_called()


def test_no_published_text_states_the_old_size():
    for rel in ("SECURITY.md", "CONFIGURATION.md", "src/jcodemunch_mcp/embeddings/local_encoder.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for old in ("~23 MB", "~23MB", "23MB", "~25MB", "~25 MB"):
            assert old not in text, (rel, old)
    assert "90 MB" in (ROOT / "SECURITY.md").read_text(encoding="utf-8")
