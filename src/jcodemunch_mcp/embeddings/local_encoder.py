"""Bundled ONNX local encoder — zero-config symbol embeddings.

Uses a permissively-licensed ONNX model (all-MiniLM-L6-v2, Apache 2.0, about
90 MB, 384-dim) that runs in-process via ``onnxruntime``.  No API key, no
internet after first download, no configuration required.  The download is
pinned to one upstream revision and each file is checked against its SHA-256.

Install::

    pip install 'jcodemunch-mcp[local-embed]'
    jcodemunch-mcp download-model          # fetch model on first use

The encoder lazily downloads the model on first call to ``encode_batch()`` if
``onnxruntime`` is installed but the model file is missing.
"""

import logging
import os
import sys
import unicodedata
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

MODEL_NAME = "all-MiniLM-L6-v2"
MODEL_REPO = "sentence-transformers/all-MiniLM-L6-v2"
ONNX_FILENAME = "model.onnx"
VOCAB_FILENAME = "vocab.txt"
TOKENIZER_CONFIG = "tokenizer_config.json"
MODEL_DIM = 384
MAX_SEQ_LENGTH = 256

_HF_BASE_URL = "https://huggingface.co"

# The fetch is pinned to one upstream revision and every file is checked
# against its SHA-256, so a changed upstream file can't be downloaded and used
# silently. Both were confirmed against the copy installs already cache:
# model.onnx matches the revision's published LFS checksum (90,405,214 bytes),
# and vocab.txt matches its git blob. To move to a newer revision, update both.
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
MODEL_SHA256 = {
    ONNX_FILENAME: "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
    VOCAB_FILENAME: "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3",
}


def _default_models_dir() -> Path:
    """Return ``~/.code-index/models/<model_name>/``."""
    root = Path(os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index")))
    return root / "models" / MODEL_NAME


def _env_model_path() -> Optional[Path]:
    """Return override path from ``JCODEMUNCH_LOCAL_EMBED_MODEL`` env var."""
    p = os.environ.get("JCODEMUNCH_LOCAL_EMBED_MODEL", "").strip()
    return Path(p) if p else None


def model_dir() -> Path:
    """Return the directory where the ONNX model + vocab are stored."""
    return _env_model_path() or _default_models_dir()


def is_model_available() -> bool:
    """Return True if the ONNX model file exists on disk."""
    d = model_dir()
    return (d / ONNX_FILENAME).exists() and (d / VOCAB_FILENAME).exists()


def is_onnxruntime_available() -> bool:
    """Return True if ``onnxruntime`` can be imported."""
    try:
        import onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


# ── Download ───────────────────────────────────────────────────────────────


def _files_to_fetch() -> list[tuple[str, str]]:
    """(url, filename) for each model file, at the pinned revision."""
    base = f"{_HF_BASE_URL}/{MODEL_REPO}/resolve/{MODEL_REVISION}"
    return [
        (f"{base}/onnx/{ONNX_FILENAME}", ONNX_FILENAME),
        (f"{base}/{VOCAB_FILENAME}", VOCAB_FILENAME),
    ]


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_model(target_dir: Optional[Path] = None, *, quiet: bool = False) -> Path:
    """Download the ONNX model + vocab from HuggingFace to *target_dir*.

    Fetches `MODEL_REVISION` and checks each file against `MODEL_SHA256`. A
    file already on disk is kept only if it matches; otherwise it is fetched
    again. A fetch goes to ``<name>.part`` and replaces the real file only
    after it matches, so an interrupted download never leaves a file that a
    later run would use. A fetched file that does not match is deleted and
    the download fails.
    Returns the directory containing the downloaded files.
    """
    import urllib.request

    dest = target_dir or _default_models_dir()
    dest.mkdir(parents=True, exist_ok=True)

    for url, filename in _files_to_fetch():
        out_path = dest / filename
        expected = MODEL_SHA256[filename]
        if out_path.exists():
            if _sha256(out_path) == expected:
                if not quiet:
                    logger.info("download_model: %s already present and verified", out_path)
                continue
            logger.warning(
                "download_model: %s does not match the pinned checksum; removing it and fetching it again",
                out_path,
            )
            # Removed first, so a failed re-fetch leaves no model rather than a bad one.
            out_path.unlink()
        if not quiet:
            logger.info("download_model: downloading %s ...", filename)
            print(f"  Downloading {filename} from {MODEL_REPO} ...", file=sys.stderr)  # noqa: T201
        part_path = dest / (filename + ".part")
        try:
            urllib.request.urlretrieve(url, str(part_path))
        except BaseException as exc:
            # Never leave a partial file behind, including on Ctrl-C.
            if part_path.exists():
                part_path.unlink()
            if not isinstance(exc, Exception):
                raise
            raise RuntimeError(
                f"Failed to download {filename} from {url}: {exc}"
            ) from exc
        actual = _sha256(part_path)
        if actual != expected:
            part_path.unlink()
            raise RuntimeError(
                f"Downloaded {filename} failed its checksum (expected sha256 {expected}, "
                f"got {actual}); the file was removed. Upstream may have changed; "
                f"nothing was installed."
            )
        os.replace(part_path, out_path)
        if not quiet:
            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"  Saved {filename} ({size_mb:.1f} MB)", file=sys.stderr)  # noqa: T201

    if not quiet:
        print(f"  Model ready at {dest}", file=sys.stderr)  # noqa: T201
    return dest


# ── WordPiece tokenizer (no transformers dependency) ──────────────────────


class WordPieceTokenizer:
    """Minimal WordPiece tokenizer matching BERT's uncased behaviour.

    Loads ``vocab.txt`` (one token per line) and performs:
    1. Unicode normalisation (NFD → strip accents → NFC)
    2. Lowercasing
    3. Whitespace + punctuation splitting
    4. WordPiece sub-word segmentation
    5. [CLS] / [SEP] wrapping + padding/truncation to *max_length*
    """

    def __init__(self, vocab_path: Path, max_length: int = MAX_SEQ_LENGTH) -> None:
        self.max_length = max_length
        self.vocab: dict[str, int] = {}
        with open(vocab_path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                self.vocab[line.rstrip("\n")] = idx
        self.unk_id = self.vocab.get("[UNK]", 0)
        self.cls_id = self.vocab.get("[CLS]", 101)
        self.sep_id = self.vocab.get("[SEP]", 102)
        self.pad_id = self.vocab.get("[PAD]", 0)

    # ── Text preprocessing ────────────────────────────────────────────────

    @staticmethod
    def _strip_accents(text: str) -> str:
        output = []
        for ch in unicodedata.normalize("NFD", text):
            if unicodedata.category(ch) == "Mn":
                continue
            output.append(ch)
        return "".join(output)

    @staticmethod
    def _is_punctuation(ch: str) -> bool:
        cp = ord(ch)
        if (33 <= cp <= 47) or (58 <= cp <= 64) or (91 <= cp <= 96) or (123 <= cp <= 126):
            return True
        return unicodedata.category(ch).startswith("P")

    def _basic_tokenize(self, text: str) -> list[str]:
        """Lowercase, strip accents, split on whitespace + punctuation."""
        text = self._strip_accents(text.lower())
        # Insert spaces around punctuation
        out: list[str] = []
        for ch in text:
            if self._is_punctuation(ch):
                out.append(f" {ch} ")
            elif ch.isspace():
                out.append(" ")
            else:
                out.append(ch)
        return "".join(out).split()

    def _wordpiece(self, token: str) -> list[int]:
        """Segment a single token into WordPiece sub-tokens."""
        if token in self.vocab:
            return [self.vocab[token]]

        ids: list[int] = []
        start = 0
        while start < len(token):
            end = len(token)
            found = False
            while start < end:
                substr = token[start:end]
                if start > 0:
                    substr = "##" + substr
                if substr in self.vocab:
                    ids.append(self.vocab[substr])
                    found = True
                    break
                end -= 1
            if not found:
                ids.append(self.unk_id)
                start += 1
            else:
                start = end
        return ids

    # ── Public API ────────────────────────────────────────────────────────

    def encode(self, text: str) -> tuple[list[int], list[int], list[int]]:
        """Tokenize *text* and return (input_ids, attention_mask, token_type_ids).

        All lists are padded/truncated to ``self.max_length``.
        """
        tokens = self._basic_tokenize(text)
        # WordPiece each token, reserving 2 slots for [CLS] and [SEP]
        wp_ids: list[int] = []
        max_wp = self.max_length - 2
        for t in tokens:
            sub_ids = self._wordpiece(t)
            if len(wp_ids) + len(sub_ids) > max_wp:
                remaining = max_wp - len(wp_ids)
                wp_ids.extend(sub_ids[:remaining])
                break
            wp_ids.extend(sub_ids)

        input_ids = [self.cls_id] + wp_ids + [self.sep_id]
        attention_mask = [1] * len(input_ids)
        token_type_ids = [0] * len(input_ids)

        # Pad to max_length
        pad_len = self.max_length - len(input_ids)
        if pad_len > 0:
            input_ids.extend([self.pad_id] * pad_len)
            attention_mask.extend([0] * pad_len)
            token_type_ids.extend([0] * pad_len)

        return input_ids, attention_mask, token_type_ids

    def encode_batch(
        self, texts: list[str]
    ) -> tuple[list[list[int]], list[list[int]], list[list[int]]]:
        """Tokenize a batch of texts."""
        all_ids, all_mask, all_types = [], [], []
        for text in texts:
            ids, mask, types = self.encode(text)
            all_ids.append(ids)
            all_mask.append(mask)
            all_types.append(types)
        return all_ids, all_mask, all_types


# ── ONNX inference session (singleton) ────────────────────────────────────

_session = None
_tokenizer: Optional[WordPieceTokenizer] = None


def _get_session():
    """Return the cached ONNX InferenceSession, creating it on first call."""
    global _session, _tokenizer

    if _session is not None:
        return _session, _tokenizer

    try:
        import onnxruntime as ort  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "onnxruntime is not installed. "
            "Run: pip install 'jcodemunch-mcp[local-embed]'"
        ) from exc

    d = model_dir()
    onnx_path = d / ONNX_FILENAME
    vocab_path = d / VOCAB_FILENAME

    if not onnx_path.exists():
        # Auto-download on first use. quiet=True is required: this path runs
        # inside an MCP tool call, where stdout is the JSON-RPC frame channel
        # under stdio transport — any print() corrupts the stream.
        logger.info("ONNX model not found at %s — downloading ...", d)
        download_model(d, quiet=True)

    if not onnx_path.exists():
        raise FileNotFoundError(
            f"ONNX model not found at {onnx_path}. "
            f"Run: jcodemunch-mcp download-model"
        )
    if not vocab_path.exists():
        raise FileNotFoundError(
            f"Vocabulary file not found at {vocab_path}. "
            f"Run: jcodemunch-mcp download-model"
        )

    opts = ort.SessionOptions()
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = 2
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    _session = ort.InferenceSession(str(onnx_path), sess_options=opts)
    _tokenizer = WordPieceTokenizer(vocab_path)
    logger.info(
        "Local ONNX encoder loaded: %s (%d-dim)", MODEL_NAME, MODEL_DIM
    )
    return _session, _tokenizer


def reset_session() -> None:
    """Release the cached ONNX session (useful for testing)."""
    global _session, _tokenizer
    _session = None
    _tokenizer = None


# ── Public API ─────────────────────────────────────────────────────────────


def _mean_pool(token_embeddings: list, attention_mask: list) -> list[float]:
    """Mean-pool token embeddings using the attention mask."""
    dim = len(token_embeddings[0])
    pooled = [0.0] * dim
    total = 0.0
    for i, mask_val in enumerate(attention_mask):
        if mask_val == 1:
            for j in range(dim):
                pooled[j] += token_embeddings[i][j]
            total += 1.0
    if total > 0:
        for j in range(dim):
            pooled[j] /= total
    # L2 normalise
    norm = sum(x * x for x in pooled) ** 0.5
    if norm > 0:
        pooled = [x / norm for x in pooled]
    return pooled


def encode_batch(texts: list[str]) -> list[list[float]]:
    """Encode a list of texts into L2-normalised 384-dim embeddings.

    Uses the bundled all-MiniLM-L6-v2 ONNX model.  Auto-downloads the model
    on first call if ``onnxruntime`` is installed but the model is missing.

    Returns:
        List of float vectors, one per input text.
    """
    import numpy as np  # onnxruntime already depends on numpy

    session, tokenizer = _get_session()

    all_ids, all_mask, all_types = tokenizer.encode_batch(texts)

    feeds = {
        "input_ids": np.array(all_ids, dtype=np.int64),
        "attention_mask": np.array(all_mask, dtype=np.int64),
        "token_type_ids": np.array(all_types, dtype=np.int64),
    }

    # Run inference — output[0] is token embeddings (batch, seq, hidden)
    outputs = session.run(None, feeds)
    token_embs = outputs[0]  # shape: (batch, seq_len, 384)

    # Mean-pool + L2-normalise
    results: list[list[float]] = []
    for i in range(len(texts)):
        mask = all_mask[i]
        emb = token_embs[i]  # (seq_len, 384)

        # Use numpy for efficiency
        mask_arr = np.array(mask, dtype=np.float32).reshape(-1, 1)
        pooled = (emb * mask_arr).sum(axis=0) / mask_arr.sum()
        norm = np.linalg.norm(pooled)
        if norm > 0:
            pooled = pooled / norm
        results.append(pooled.tolist())

    return results
