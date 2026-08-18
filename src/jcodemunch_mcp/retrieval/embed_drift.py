"""Embedding drift detector (v1.80.0).

Provider model versions sometimes change silently — Gemini bumps a model
revision, OpenAI changes weights under the same name, the bundled ONNX
file is replaced by a download. The result is a search index whose
embeddings no longer match what the live query encoder produces, and
hybrid retrieval quietly degrades.

This module pins a small *canary* — 16 deterministic strings embedded
with the active provider — and recomputes them on demand. Cosine drift
between captured and current vectors signals a change.

Storage: ``~/.code-index/embed_canary.json`` with
``{provider, model, dim, captured_at, strings: [...], vectors: [[...]]}``.

Drift threshold (cosine distance ``1 - cos`` averaged across canaries)
defaults to 0.05 — well above floating-point noise on stable models.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..embeddings.advice import NO_PROVIDER_MESSAGE

logger = logging.getLogger(__name__)

_CANARY_FILE = "embed_canary.json"
_DEFAULT_DRIFT_THRESHOLD = 0.05  # cosine distance; cosine sim < 0.95 alarms

# Sixteen short, semantically diverse strings picked to span common code
# embedding regions (function names, keywords, prose, code-like tokens).
# Order is part of the canary contract — never reorder; append only.
CANARY_STRINGS: tuple[str, ...] = (
    "def authenticate_user(username, password)",
    "class DatabaseConnection",
    "import asyncio",
    "return self.value",
    "Calculate the cyclomatic complexity of a function",
    "Convert UTC timestamp to local time",
    "Parse JSON request body",
    "throw new ValueError",
    "TypeScript generic function with constraints",
    "async function fetchUserProfile",
    "rust trait implementation",
    "go channel send and receive",
    "regex pattern for email validation",
    "binary search tree insert",
    "Kubernetes deployment manifest",
    "// TODO: refactor this hack",
)


def _canary_path(base_path: Optional[str] = None) -> Path:
    root = Path(base_path) if base_path else Path.home() / ".code-index"
    root.mkdir(parents=True, exist_ok=True)
    return root / _CANARY_FILE


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom == 0.0:
        return 0.0
    return dot / denom


def _resolve_provider() -> tuple[Optional[str], Optional[str]]:
    """Reuse embed_repo's detection so we don't drift from the live encoder."""
    try:
        from ..tools.embed_repo import _detect_provider
        detected = _detect_provider()
        if detected:
            return detected
    except Exception:
        logger.debug("Failed to resolve embedding provider", exc_info=True)
    return (None, None)


def _embed(strings: list[str], provider: str, model: str) -> list[list[float]]:
    from ..tools.embed_repo import embed_texts
    return embed_texts(strings, provider=provider, model=model)


def capture_canary(base_path: Optional[str] = None, *, force: bool = False) -> dict:
    """Embed CANARY_STRINGS with the active provider and persist them.

    Returns a dict describing what was captured (or an error). When a
    canary already exists and ``force`` is False, returns the existing
    snapshot's metadata without re-embedding.
    """
    path = _canary_path(base_path)
    if path.exists() and not force:
        try:
            existing = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            return {
                "captured": False,
                "reason": "canary_already_exists",
                "provider": existing.get("provider"),
                "model": existing.get("model"),
                "captured_at": existing.get("captured_at"),
                "dim": existing.get("dim"),
                "path": str(path),
            }
        except Exception:
            logger.debug("Failed to read existing canary at %s", path, exc_info=True)
            # fall through to re-capture

    provider, model = _resolve_provider()
    if not provider or not model:
        return {
            "captured": False,
            "error": NO_PROVIDER_MESSAGE,
        }
    try:
        vectors = _embed(list(CANARY_STRINGS), provider, model)
    except Exception as exc:
        return {
            "captured": False,
            "provider": provider,
            "model": model,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if not vectors or any(not v for v in vectors):
        return {
            "captured": False,
            "provider": provider,
            "model": model,
            "error": "Provider returned an empty vector set",
        }
    dim = len(vectors[0])
    snapshot = {
        "provider": provider,
        "model": model,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dim": dim,
        "strings": list(CANARY_STRINGS),
        "vectors": vectors,
    }
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    return {
        "captured": True,
        "provider": provider,
        "model": model,
        "captured_at": snapshot["captured_at"],
        "dim": dim,
        "n_canaries": len(CANARY_STRINGS),
        "path": str(path),
    }


def check_drift(
    base_path: Optional[str] = None,
    *,
    threshold: float = _DEFAULT_DRIFT_THRESHOLD,
) -> dict:
    """Recompute embeddings for the pinned canary and compare to the snapshot.

    Returns ``{alarm, max_drift, mean_drift, threshold, per_canary: [...],
    provider, model, captured_provider, captured_model, captured_at}``. If
    no canary exists, returns ``{has_canary: False}``. If the live provider
    differs from the captured provider, the diff is reported but the
    cosine comparison still runs (often that's what the alarm catches).
    """
    path = _canary_path(base_path)
    if not path.exists():
        return {
            "has_canary": False,
            "hint": (
                "Call capture_canary() (or check_embedding_drift(force=True))"
                " to pin a canary first."
            ),
        }
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return {
            "has_canary": False,
            "error": f"Failed to read canary: {type(exc).__name__}: {exc}",
        }

    cur_provider, cur_model = _resolve_provider()
    if not cur_provider or not cur_model:
        return {
            "has_canary": True,
            "captured_provider": snapshot.get("provider"),
            "captured_model": snapshot.get("model"),
            "error": "No embedding provider configured at check time.",
        }

    saved_strings = snapshot.get("strings") or list(CANARY_STRINGS)
    saved_vectors = snapshot.get("vectors") or []
    try:
        cur_vectors = _embed(saved_strings, cur_provider, cur_model)
    except Exception as exc:
        return {
            "has_canary": True,
            "captured_provider": snapshot.get("provider"),
            "captured_model": snapshot.get("model"),
            "provider": cur_provider,
            "model": cur_model,
            "error": f"Re-embedding failed: {type(exc).__name__}: {exc}",
        }

    per_canary: list[dict] = []
    drifts: list[float] = []
    for i, (s, saved, cur) in enumerate(zip(saved_strings, saved_vectors, cur_vectors)):
        if not saved or not cur:
            continue
        sim = _cosine(saved, cur)
        drift = round(1.0 - sim, 6)
        drifts.append(drift)
        per_canary.append({
            "index": i,
            "string": s if len(s) <= 60 else s[:57] + "...",
            "cosine": round(sim, 6),
            "drift": drift,
        })

    if not drifts:
        return {
            "has_canary": True,
            "alarm": False,
            "error": "No comparable vectors — dimension or count mismatch?",
        }

    max_drift = max(drifts)
    mean_drift = sum(drifts) / len(drifts)
    alarm = max_drift > threshold

    out = {
        "has_canary": True,
        "alarm": alarm,
        "threshold": threshold,
        "max_drift": round(max_drift, 6),
        "mean_drift": round(mean_drift, 6),
        "n_canaries": len(drifts),
        "captured_provider": snapshot.get("provider"),
        "captured_model": snapshot.get("model"),
        "captured_at": snapshot.get("captured_at"),
        "captured_dim": snapshot.get("dim"),
        "provider": cur_provider,
        "model": cur_model,
        "current_dim": len(cur_vectors[0]) if cur_vectors and cur_vectors[0] else None,
        "per_canary": per_canary,
    }
    if alarm:
        out["hint"] = (
            "Embedding output has shifted beyond the drift threshold. "
            "Provider model likely changed; re-run embed_repo to refresh "
            "stored vectors, then capture_canary(force=True) to re-pin."
        )
    return out
