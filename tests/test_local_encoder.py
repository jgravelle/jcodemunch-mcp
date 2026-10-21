"""Tests for the bundled ONNX local encoder (Phase 3 — Gap 1).

Tests cover:
- WordPiece tokenizer behaviour (no ONNX runtime needed)
- Provider detection priority (local_onnx as priority 0)
- embed_texts routing for the local_onnx provider
- download-model CLI subcommand
- encode_batch output shape and normalisation (mocked ONNX)
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── WordPiece tokenizer tests ─────────────────────────────────────────────


class TestWordPieceTokenizer:
    """Test the minimal WordPiece tokenizer (no onnxruntime needed)."""

    @pytest.fixture()
    def vocab_file(self, tmp_path):
        """Create a minimal vocab.txt for testing."""
        tokens = [
            "[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]",
            "hello", "world", "test", "##ing", "##ed",
            "func", "##tion", "def", "class", "return",
            "the", "a", "is", ".",  ",",
            "python", "code", "search", "symbol",
        ]
        vocab_path = tmp_path / "vocab.txt"
        vocab_path.write_text("\n".join(tokens), encoding="utf-8")
        return vocab_path

    def test_basic_tokenize(self, vocab_file):
        from jcodemunch_mcp.embeddings.local_encoder import WordPieceTokenizer
        tok = WordPieceTokenizer(vocab_path=vocab_file, max_length=32)

        ids, mask, types = tok.encode("Hello World")
        # Should start with [CLS]=2 and end with [SEP]=3
        assert ids[0] == 2  # [CLS]
        # Find where [SEP] is
        sep_pos = ids.index(3)
        assert sep_pos > 1  # at least one real token between CLS and SEP
        # Attention mask should be 1 up to and including [SEP], 0 after
        assert all(m == 1 for m in mask[:sep_pos + 1])
        assert all(m == 0 for m in mask[sep_pos + 1:])
        # Token type IDs should all be 0
        assert all(t == 0 for t in types)

    def test_max_length_truncation(self, vocab_file):
        from jcodemunch_mcp.embeddings.local_encoder import WordPieceTokenizer
        tok = WordPieceTokenizer(vocab_path=vocab_file, max_length=8)

        long_text = "hello world test hello world test hello world test"
        ids, mask, types = tok.encode(long_text)
        assert len(ids) == 8
        assert len(mask) == 8
        assert len(types) == 8

    def test_unknown_tokens(self, vocab_file):
        from jcodemunch_mcp.embeddings.local_encoder import WordPieceTokenizer
        tok = WordPieceTokenizer(vocab_path=vocab_file, max_length=32)

        ids, mask, types = tok.encode("xyzzy foobar")
        # Unknown tokens map to [UNK]=1
        assert 1 in ids  # at least one UNK

    def test_wordpiece_subword(self, vocab_file):
        from jcodemunch_mcp.embeddings.local_encoder import WordPieceTokenizer
        tok = WordPieceTokenizer(vocab_path=vocab_file, max_length=32)

        # "testing" should split into "test" + "##ing"
        ids, mask, types = tok.encode("testing")
        test_id = 7   # "test" is at index 7
        ing_id = 8    # "##ing" is at index 8
        assert test_id in ids
        assert ing_id in ids

    def test_batch_encode(self, vocab_file):
        from jcodemunch_mcp.embeddings.local_encoder import WordPieceTokenizer
        tok = WordPieceTokenizer(vocab_path=vocab_file, max_length=16)

        texts = ["hello world", "test code"]
        all_ids, all_mask, all_types = tok.encode_batch(texts)
        assert len(all_ids) == 2
        assert len(all_mask) == 2
        assert all(len(ids) == 16 for ids in all_ids)

    def test_empty_string(self, vocab_file):
        from jcodemunch_mcp.embeddings.local_encoder import WordPieceTokenizer
        tok = WordPieceTokenizer(vocab_path=vocab_file, max_length=16)

        ids, mask, types = tok.encode("")
        # Should still have [CLS] and [SEP]
        assert ids[0] == 2  # [CLS]
        assert ids[1] == 3  # [SEP]
        assert mask[0] == 1
        assert mask[1] == 1
        assert all(m == 0 for m in mask[2:])

    def test_punctuation_splitting(self, vocab_file):
        from jcodemunch_mcp.embeddings.local_encoder import WordPieceTokenizer
        tok = WordPieceTokenizer(vocab_path=vocab_file, max_length=32)

        ids, mask, types = tok.encode("hello.world")
        # Punctuation should be split, so "." should be its own token
        dot_id = 18  # "." is at index 18 in our vocab
        assert dot_id in ids


# ── Provider detection tests ──────────────────────────────────────────────


class TestProviderDetection:
    """Test that local_onnx is priority 0 in _detect_provider()."""

    def test_local_onnx_wins_when_available(self):
        """local_onnx should be selected when onnxruntime + model are present."""
        with patch("jcodemunch_mcp.embeddings.local_encoder.is_onnxruntime_available", return_value=True), \
             patch("jcodemunch_mcp.embeddings.local_encoder.is_model_available", return_value=True):
            from jcodemunch_mcp.tools.embed_repo import _detect_provider
            result = _detect_provider()
            assert result is not None
            assert result[0] == "local_onnx"
            assert result[1] == "all-MiniLM-L6-v2"

    def test_falls_through_without_onnxruntime(self):
        """Should fall through to other providers if onnxruntime is missing."""
        with patch("jcodemunch_mcp.embeddings.local_encoder.is_onnxruntime_available", return_value=False), \
             patch("jcodemunch_mcp.embeddings.local_encoder.is_model_available", return_value=False):
            from jcodemunch_mcp.tools.embed_repo import _detect_provider
            # Clear any env vars that would trigger other providers
            env = {
                "JCODEMUNCH_EMBED_MODEL": "",
                "GOOGLE_API_KEY": "",
                "GOOGLE_EMBED_MODEL": "",
                "OPENAI_API_KEY": "",
                "OPENAI_EMBED_MODEL": "",
            }
            with patch.dict(os.environ, env, clear=False):
                from jcodemunch_mcp import config as _cfg
                with patch.object(_cfg, "get", return_value=""):
                    result = _detect_provider()
                    assert result is None

    def test_falls_through_without_model(self):
        """Should fall through if onnxruntime is installed but model isn't downloaded."""
        with patch("jcodemunch_mcp.embeddings.local_encoder.is_onnxruntime_available", return_value=True), \
             patch("jcodemunch_mcp.embeddings.local_encoder.is_model_available", return_value=False):
            from jcodemunch_mcp.tools.embed_repo import _detect_provider
            env = {
                "JCODEMUNCH_EMBED_MODEL": "",
                "GOOGLE_API_KEY": "",
                "GOOGLE_EMBED_MODEL": "",
                "OPENAI_API_KEY": "",
                "OPENAI_EMBED_MODEL": "",
            }
            with patch.dict(os.environ, env, clear=False):
                from jcodemunch_mcp import config as _cfg
                with patch.object(_cfg, "get", return_value=""):
                    result = _detect_provider()
                    assert result is None

    def test_sentence_transformers_still_works(self):
        """Explicit JCODEMUNCH_EMBED_MODEL should win when local_onnx unavailable."""
        with patch("jcodemunch_mcp.embeddings.local_encoder.is_onnxruntime_available", return_value=False), \
             patch("jcodemunch_mcp.embeddings.local_encoder.is_model_available", return_value=False):
            from jcodemunch_mcp.tools.embed_repo import _detect_provider
            with patch.dict(os.environ, {"JCODEMUNCH_EMBED_MODEL": "my-model"}, clear=False):
                from jcodemunch_mcp import config as _cfg
                with patch.object(_cfg, "get", return_value=""):
                    result = _detect_provider()
                    assert result == ("sentence_transformers", "my-model")


# ── embed_texts routing tests ─────────────────────────────────────────────


class TestEmbedTextsRouting:
    """Test that embed_texts routes to the right provider."""

    def test_routes_to_local_onnx(self):
        with patch("jcodemunch_mcp.tools.embed_repo._embed_local_onnx", return_value=[[0.1] * 384]) as mock:
            from jcodemunch_mcp.tools.embed_repo import embed_texts
            result = embed_texts(["test"], "local_onnx", "all-MiniLM-L6-v2")
            mock.assert_called_once_with(["test"], "all-MiniLM-L6-v2")
            assert result == [[0.1] * 384]

    def test_unknown_provider_raises(self):
        from jcodemunch_mcp.tools.embed_repo import embed_texts
        with pytest.raises(ValueError, match="Unknown embedding provider"):
            embed_texts(["test"], "nonexistent", "model")


# ── Model management tests ────────────────────────────────────────────────


class TestModelManagement:

    def test_default_models_dir(self):
        from jcodemunch_mcp.embeddings.local_encoder import _default_models_dir, MODEL_NAME
        d = _default_models_dir()
        assert d.name == MODEL_NAME
        assert "models" in str(d)

    def test_env_override(self):
        from jcodemunch_mcp.embeddings.local_encoder import model_dir
        with patch.dict(os.environ, {"JCODEMUNCH_LOCAL_EMBED_MODEL": "/custom/path"}):
            assert model_dir() == Path("/custom/path")

    def test_is_model_available_false(self, tmp_path):
        with patch("jcodemunch_mcp.embeddings.local_encoder.model_dir", return_value=tmp_path):
            from jcodemunch_mcp.embeddings.local_encoder import is_model_available
            assert is_model_available() is False

    def test_is_model_available_true(self, tmp_path):
        (tmp_path / "model.onnx").write_bytes(b"fake")
        (tmp_path / "vocab.txt").write_text("token", encoding="utf-8")
        with patch("jcodemunch_mcp.embeddings.local_encoder.model_dir", return_value=tmp_path):
            from jcodemunch_mcp.embeddings.local_encoder import is_model_available
            assert is_model_available() is True

    def test_download_model_creates_files(self, tmp_path):
        """download_model should fetch ONNX + vocab to target directory."""
        from jcodemunch_mcp.embeddings.local_encoder import download_model

        import hashlib

        from jcodemunch_mcp.embeddings import local_encoder as le

        def fake_urlretrieve(url, path):
            Path(path).write_bytes(b"fake-model-data")

        # The download verifies each file's SHA-256 (tests/test_embedding_model_pin.py);
        # the fake bytes are declared as the expected content here.
        fake_sum = hashlib.sha256(b"fake-model-data").hexdigest()
        with patch.dict(le.MODEL_SHA256, {k: fake_sum for k in le.MODEL_SHA256}), \
                patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve):
            result_dir = download_model(tmp_path, quiet=True)

        assert result_dir == tmp_path
        assert (tmp_path / "model.onnx").exists()
        assert (tmp_path / "vocab.txt").exists()

    def test_download_model_skips_existing(self, tmp_path):
        """Should not re-download files that already exist AND match their checksum.

        A file on disk that does not match is fetched again
        (tests/test_embedding_model_pin.py); this test used to expect any
        existing file to be trusted unchecked.
        """
        import hashlib

        from jcodemunch_mcp.embeddings import local_encoder as le
        from jcodemunch_mcp.embeddings.local_encoder import download_model

        (tmp_path / "model.onnx").write_bytes(b"existing")
        (tmp_path / "vocab.txt").write_bytes(b"existing")
        same = hashlib.sha256(b"existing").hexdigest()

        with patch.dict(le.MODEL_SHA256, {k: same for k in le.MODEL_SHA256}), \
                patch("urllib.request.urlretrieve") as mock_fetch:
            download_model(tmp_path, quiet=True)
            mock_fetch.assert_not_called()

    def test_download_model_cleans_up_on_failure(self, tmp_path):
        """Should remove partial downloads on failure."""
        from jcodemunch_mcp.embeddings.local_encoder import download_model

        call_count = 0

        def fail_on_second(url, path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                Path(path).write_bytes(b"ok")
            else:
                Path(path).write_bytes(b"partial")
                raise ConnectionError("network down")

        import hashlib

        from jcodemunch_mcp.embeddings import local_encoder as le

        # The first file's bytes are declared valid, so the failure under test
        # is the network one on the second file, not a checksum mismatch.
        ok_sum = {le.ONNX_FILENAME: hashlib.sha256(b"ok").hexdigest()}
        with patch.dict(le.MODEL_SHA256, ok_sum), \
                patch("urllib.request.urlretrieve", side_effect=fail_on_second):
            with pytest.raises(RuntimeError, match="Failed to download"):
                download_model(tmp_path, quiet=True)

        # The second file (vocab.txt) should be cleaned up
        assert not (tmp_path / "vocab.txt").exists()
        assert not list(tmp_path.glob("*.part"))


# ── encode_batch tests (mocked ONNX session) ──────────────────────────────


class TestEncodeBatch:

    @pytest.fixture(autouse=True)
    def reset(self):
        """Reset the cached session before each test."""
        from jcodemunch_mcp.embeddings.local_encoder import reset_session
        reset_session()
        yield
        reset_session()

    def test_encode_batch_shape_and_normalisation(self, tmp_path):
        """encode_batch should return L2-normalised vectors of the right dim."""
        np = pytest.importorskip("numpy")

        # Create fake vocab
        tokens = ["[PAD]", "[UNK]", "[CLS]", "[SEP]"] + [f"tok{i}" for i in range(100)]
        (tmp_path / "vocab.txt").write_text("\n".join(tokens), encoding="utf-8")
        (tmp_path / "model.onnx").write_bytes(b"fake")

        # Mock onnxruntime
        mock_ort = MagicMock()
        mock_session = MagicMock()
        # Simulate output: token embeddings of shape (batch, seq_len, 384)
        batch_size = 2
        seq_len = 256
        fake_output = np.random.randn(batch_size, seq_len, 384).astype(np.float32)
        mock_session.run.return_value = [fake_output]
        mock_ort.InferenceSession.return_value = mock_session
        mock_ort.SessionOptions.return_value = MagicMock()
        mock_ort.GraphOptimizationLevel.ORT_ENABLE_ALL = 99

        with patch.dict(sys.modules, {"onnxruntime": mock_ort}), \
             patch("jcodemunch_mcp.embeddings.local_encoder.model_dir", return_value=tmp_path):
            from jcodemunch_mcp.embeddings.local_encoder import encode_batch
            result = encode_batch(["hello world", "test code"])

        assert len(result) == 2
        assert len(result[0]) == 384
        # Check L2 normalisation
        for vec in result:
            norm = sum(x * x for x in vec) ** 0.5
            assert abs(norm - 1.0) < 1e-4, f"Expected unit norm, got {norm}"


# ── CLI subcommand tests ──────────────────────────────────────────────────


class TestDownloadModelCLI:

    def test_download_model_subcommand_exists(self):
        """The download-model subcommand should be in known_commands."""
        from jcodemunch_mcp.server import main
        import inspect
        source = inspect.getsource(main)
        assert "download-model" in source

    def test_download_model_cli_invokes_download(self, tmp_path):
        """CLI should call download_model with the right target dir."""
        with patch("jcodemunch_mcp.embeddings.local_encoder.download_model") as mock_dl:
            from jcodemunch_mcp.server import main
            with pytest.raises(SystemExit) as exc_info:
                main(["download-model", "--target-dir", str(tmp_path)])
            mock_dl.assert_called_once_with(tmp_path)
            assert exc_info.value.code == 0

    def test_download_model_cli_default_dir(self):
        """CLI with no --target-dir should pass None."""
        with patch("jcodemunch_mcp.embeddings.local_encoder.download_model") as mock_dl:
            from jcodemunch_mcp.server import main
            with pytest.raises(SystemExit) as exc_info:
                main(["download-model"])
            mock_dl.assert_called_once_with(None)
            assert exc_info.value.code == 0

    def test_download_model_cli_handles_error(self):
        """CLI should exit 1 on download failure."""
        with patch("jcodemunch_mcp.embeddings.local_encoder.download_model", side_effect=RuntimeError("fail")):
            from jcodemunch_mcp.server import main
            with pytest.raises(SystemExit) as exc_info:
                main(["download-model"])
            assert exc_info.value.code == 1
