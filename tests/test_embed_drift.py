"""Tests for v1.80.0 embedding drift detector."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from unittest.mock import patch

import pytest

from jcodemunch_mcp.retrieval import embed_drift as ed
from jcodemunch_mcp.tools.check_embedding_drift import check_embedding_drift


def _fake_vectors(n: int, dim: int = 8, seed: int = 0) -> list[list[float]]:
    rng = random.Random(seed)
    return [[rng.uniform(-1, 1) for _ in range(dim)] for _ in range(n)]


def _shifted(vectors: list[list[float]], scale: float, seed: int = 1) -> list[list[float]]:
    """Add a small noise vector — adjustable to simulate drift."""
    rng = random.Random(seed)
    return [
        [v + scale * rng.uniform(-1, 1) for v in vec]
        for vec in vectors
    ]


class TestCanaryBasics:
    def test_canary_strings_are_stable(self):
        # Order is part of the canary contract — never mutate.
        assert len(ed.CANARY_STRINGS) == 16
        assert ed.CANARY_STRINGS[0] == "def authenticate_user(username, password)"
        assert ed.CANARY_STRINGS[-1] == "// TODO: refactor this hack"

    def test_check_drift_no_canary_returns_hint(self, tmp_path):
        out = ed.check_drift(base_path=str(tmp_path))
        assert out["has_canary"] is False
        assert "hint" in out


class TestCaptureCanary:
    def test_capture_persists_snapshot(self, tmp_path, monkeypatch):
        v = _fake_vectors(16)
        monkeypatch.setattr(
            ed, "_resolve_provider", lambda: ("local_onnx", "test-model")
        )
        monkeypatch.setattr(ed, "_embed", lambda strings, p, m: v)

        out = ed.capture_canary(base_path=str(tmp_path))
        assert out["captured"] is True
        assert out["provider"] == "local_onnx"
        assert out["model"] == "test-model"
        assert out["dim"] == 8
        snap_path = ed._canary_path(str(tmp_path))
        assert snap_path.exists()
        snapshot = json.loads(snap_path.read_text())
        assert snapshot["dim"] == 8
        assert len(snapshot["vectors"]) == 16
        assert snapshot["strings"][0] == ed.CANARY_STRINGS[0]

    def test_capture_idempotent_unless_forced(self, tmp_path, monkeypatch):
        v = _fake_vectors(16)
        monkeypatch.setattr(
            ed, "_resolve_provider", lambda: ("local_onnx", "test-model")
        )
        monkeypatch.setattr(ed, "_embed", lambda strings, p, m: v)

        first = ed.capture_canary(base_path=str(tmp_path))
        assert first["captured"] is True
        second = ed.capture_canary(base_path=str(tmp_path))
        assert second["captured"] is False
        assert second["reason"] == "canary_already_exists"
        forced = ed.capture_canary(base_path=str(tmp_path), force=True)
        assert forced["captured"] is True

    def test_capture_returns_error_with_no_provider(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ed, "_resolve_provider", lambda: (None, None))
        out = ed.capture_canary(base_path=str(tmp_path))
        assert out["captured"] is False
        # #489: asserts the SHARED advice is served, not one spelling of it.
        # This used to pin the literal "No embedding provider configured", which
        # is how this site kept its own copy — one that named the bundled ONNX
        # encoder LAST, behind two providers that bill per call.
        from jcodemunch_mcp.embeddings.advice import NO_PROVIDER_MESSAGE

        assert out["error"] == NO_PROVIDER_MESSAGE
        assert "jcodemunch-mcp[local-embed]" in out["error"]


class TestCheckDrift:
    def test_zero_drift_when_vectors_match(self, tmp_path, monkeypatch):
        v = _fake_vectors(16)
        monkeypatch.setattr(
            ed, "_resolve_provider", lambda: ("local_onnx", "test-model")
        )
        monkeypatch.setattr(ed, "_embed", lambda strings, p, m: v)

        ed.capture_canary(base_path=str(tmp_path))
        out = ed.check_drift(base_path=str(tmp_path))
        assert out["alarm"] is False
        assert out["max_drift"] == 0.0
        assert out["mean_drift"] == 0.0
        assert out["n_canaries"] == 16

    def test_alarm_fires_on_large_drift(self, tmp_path, monkeypatch):
        v = _fake_vectors(16, dim=16)
        monkeypatch.setattr(
            ed, "_resolve_provider", lambda: ("local_onnx", "test-model")
        )
        # First call (capture): return original
        # Second call (check): return heavily perturbed
        calls = {"n": 0}

        def fake_embed(strings, provider, model):
            calls["n"] += 1
            return v if calls["n"] == 1 else _shifted(v, scale=2.0)

        monkeypatch.setattr(ed, "_embed", fake_embed)
        ed.capture_canary(base_path=str(tmp_path))
        out = ed.check_drift(base_path=str(tmp_path), threshold=0.05)
        assert out["alarm"] is True
        assert out["max_drift"] > 0.05
        assert "hint" in out

    def test_minor_noise_below_threshold(self, tmp_path, monkeypatch):
        v = _fake_vectors(16, dim=64)
        monkeypatch.setattr(
            ed, "_resolve_provider", lambda: ("local_onnx", "test-model")
        )
        calls = {"n": 0}

        def fake_embed(strings, provider, model):
            calls["n"] += 1
            return v if calls["n"] == 1 else _shifted(v, scale=0.0001)

        monkeypatch.setattr(ed, "_embed", fake_embed)
        ed.capture_canary(base_path=str(tmp_path))
        out = ed.check_drift(base_path=str(tmp_path), threshold=0.05)
        assert out["alarm"] is False


class TestCheckEmbeddingDriftTool:
    def test_capture_then_check(self, tmp_path, monkeypatch):
        v = _fake_vectors(16)
        monkeypatch.setattr(
            ed, "_resolve_provider", lambda: ("local_onnx", "test-model")
        )
        monkeypatch.setattr(ed, "_embed", lambda strings, p, m: v)

        cap = check_embedding_drift(capture=True, storage_path=str(tmp_path))
        assert cap["captured"] is True
        chk = check_embedding_drift(storage_path=str(tmp_path))
        assert chk["alarm"] is False
        assert "_meta" in chk
        assert "timing_ms" in chk["_meta"]

    def test_check_without_canary_reports_hint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            ed, "_resolve_provider", lambda: ("local_onnx", "test-model")
        )
        monkeypatch.setattr(ed, "_embed", lambda strings, p, m: _fake_vectors(16))
        out = check_embedding_drift(storage_path=str(tmp_path))
        assert out["has_canary"] is False
        assert "hint" in out


class TestCosineHelper:
    def test_identity(self):
        assert ed._cosine([1.0, 0.0], [1.0, 0.0]) == 1.0

    def test_orthogonal(self):
        assert ed._cosine([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_anti_parallel(self):
        assert ed._cosine([1.0, 0.0], [-1.0, 0.0]) == -1.0

    def test_zero_vector_returns_zero(self):
        assert ed._cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


class TestServerRegistration:
    def test_in_canonical_names(self):
        from jcodemunch_mcp.server import _CANONICAL_TOOL_NAMES
        assert "check_embedding_drift" in _CANONICAL_TOOL_NAMES

    def test_in_standard_tier(self):
        from jcodemunch_mcp.server import _TOOL_TIER_STANDARD
        assert "check_embedding_drift" in _TOOL_TIER_STANDARD

    def test_in_default_bundle(self):
        from jcodemunch_mcp.config import DEFAULTS
        assert "check_embedding_drift" in DEFAULTS["tool_tier_bundles"]["standard"]
