"""embed_repo tool — precompute and cache symbol embeddings for semantic search.

Calling this tool is optional.  ``search_symbols`` with ``semantic=true`` lazily
computes missing embeddings on first use; ``embed_repo`` just warms the cache in
one deliberate pass so that the first semantic query returns immediately.
"""

import logging
import os
import time
from typing import Callable, Optional

from .. import config as _config
from ..storage import IndexStore
from ._utils import index_status_to_tool_error, resolve_repo

logger = logging.getLogger(__name__)

# Batch size used internally by the lazy embedding path in search_symbols.
EMBED_BATCH_SIZE = 50

# ── Provider detection ──────────────────────────────────────────────────────


def _detect_provider() -> Optional[tuple[str, str]]:
    """Return (provider_name, model_name) or None when nothing is configured.

    Priority order (first match wins):
    0. local_onnx             — ``onnxruntime`` installed + ONNX model present
    1. sentence-transformers  — ``embed_model`` config key or ``JCODEMUNCH_EMBED_MODEL`` env var
    2. Gemini                 — ``GOOGLE_API_KEY`` + ``GOOGLE_EMBED_MODEL``
    3. OpenAI                 — ``OPENAI_API_KEY`` + ``OPENAI_EMBED_MODEL``
    """
    # Priority 0: bundled ONNX local encoder (zero-config)
    from ..embeddings.local_encoder import is_onnxruntime_available, is_model_available, MODEL_NAME
    if is_onnxruntime_available() and is_model_available():
        return ("local_onnx", MODEL_NAME)

    # Global-only by design (#301): per-project embedding models would
    # break cross-project semantic search consistency. Audit decision: if
    # per-repo embedding model selection ever becomes a feature, _detect_provider
    # needs a repo arg threaded from embed_repo().
    st_model = (_config.get("embed_model", "") or os.environ.get("JCODEMUNCH_EMBED_MODEL", "")).strip()
    if st_model:
        return ("sentence_transformers", st_model)

    google_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    google_model = os.environ.get("GOOGLE_EMBED_MODEL", "").strip()
    if google_key and google_model:
        return ("gemini", google_model)

    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    openai_model = os.environ.get("OPENAI_EMBED_MODEL", "").strip()
    # OPENAI_API_KEY alone is used for the local-LLM summariser; require
    # OPENAI_EMBED_MODEL to be set explicitly to avoid conflation.
    if openai_key and openai_model:
        return ("openai", openai_model)

    return None


# ── Eager backend warm-up (Windows loader-lock guard) ──────────────────────


def warm_up_embedding_backend() -> Optional[str]:
    """Import the active provider's native backend on the *calling* thread.

    onnxruntime and sentence-transformers/torch load native DLLs. On Windows,
    loading those from an ``asyncio.to_thread`` worker while the main thread is
    servicing its transport deadlocks on the loader lock: the first
    ``embed_repo`` / ``check_embedding_drift`` call never returns
    (jdatamunch-mcp#3, reproduced here). Doing that first import up front, on
    the main thread before the event loop starts, sidesteps it.

    Costs a few seconds of startup, so it only runs for the two providers that
    load native code. The network-backed providers stay lazy. Set
    ``JCODEMUNCH_EAGER_EMBED_IMPORT=0`` to opt out.

    Returns the provider warmed, or None. Never raises — a missing or broken
    install must not stop the server from starting.
    """
    if os.environ.get("JCODEMUNCH_EAGER_EMBED_IMPORT", "").strip() == "0":
        return None

    # numpy is a native extension too, and v1.108.223 (#399) made the semantic
    # ranking pass reach for it from inside the same `asyncio.to_thread` worker.
    # It is imported here for the same loader-lock reason as the backends below,
    # and unconditionally — the fast path is worth having on the network-backed
    # providers as well, and a missing numpy is a supported configuration.
    try:
        import numpy  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on local install
        logger.debug("numpy warm-up skipped: %s", exc)

    try:
        detected = _detect_provider()
    except Exception as exc:  # pragma: no cover - detection is defensive
        logger.debug("embedding warm-up: provider detection failed: %s", exc)
        return None
    if detected is None:
        return None

    provider = detected[0]
    try:
        if provider == "local_onnx":
            import onnxruntime  # noqa: F401
        elif provider == "sentence_transformers":
            import sentence_transformers  # noqa: F401
        else:
            return None
    except Exception as exc:  # pragma: no cover - depends on local install
        logger.debug("embedding warm-up skipped for %s: %s", provider, exc)
        return None
    return provider


# ── Per-provider embedding functions (all lazy-imported) ───────────────────


def _embed_sentence_transformers(texts: list[str], model_name: str) -> list[list[float]]:
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "sentence-transformers is not installed. "
            "Run: pip install 'jcodemunch-mcp[semantic]'"
        ) from exc
    model = SentenceTransformer(model_name)
    raw = model.encode(texts, convert_to_numpy=False, show_progress_bar=False)
    return [list(map(float, e)) for e in raw]


def _gemini_task_aware() -> bool:
    """Return True unless the user has opted out via GEMINI_EMBED_TASK_AWARE=0."""
    return os.environ.get("GEMINI_EMBED_TASK_AWARE", "1").strip() not in (
        "0", "false", "no", "off"
    )


# CODE_RETRIEVAL_QUERY was added in the newer google-genai SDK; the legacy
# google-generativeai SDK only exposes RETRIEVAL_QUERY.
_GEMINI_TASK_TYPE_FALLBACKS: dict[str, str] = {
    "CODE_RETRIEVAL_QUERY": "RETRIEVAL_QUERY",
}


def _normalise_gemini_task_type(genai_module, task_type: Optional[str]) -> Optional[str]:
    """Return the task_type value accepted by the installed Gemini SDK.

    Probes the SDK's ``TaskType`` proto enum at runtime so we degrade
    gracefully on legacy ``google-generativeai`` (which lacks
    ``CODE_RETRIEVAL_QUERY``) without requiring a version check.
    """
    if not task_type:
        return None
    try:
        supported = {e.name for e in genai_module.protos.TaskType}
        if task_type in supported:
            return task_type
        fallback = _GEMINI_TASK_TYPE_FALLBACKS.get(task_type)
        if fallback and fallback in supported:
            logger.debug(
                "Gemini SDK does not support task_type=%r; using %r instead",
                task_type,
                fallback,
            )
            return fallback
        logger.debug(
            "Gemini SDK does not support task_type=%r and no fallback found; omitting",
            task_type,
        )
        return None
    except Exception:
        # Cannot introspect the enum — pass through and let the API call surface errors.
        return task_type


def _embed_gemini(
    texts: list[str], model_name: str, task_type: Optional[str] = None
) -> list[list[float]]:
    try:
        import google.generativeai as genai  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "google-generativeai is not installed. "
            "Run: pip install 'jcodemunch-mcp[gemini]'"
        ) from exc
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    genai.configure(api_key=api_key)
    # Resolve to a task type the installed SDK actually supports.
    effective_task_type = _normalise_gemini_task_type(genai, task_type)
    results = []
    for text in texts:
        kwargs: dict = {}
        if effective_task_type:
            kwargs["task_type"] = effective_task_type
        resp = genai.embed_content(model=model_name, content=text, **kwargs)
        results.append(list(map(float, resp["embedding"])))
    return results


def _embed_local_onnx(texts: list[str], model_name: str) -> list[list[float]]:
    from ..embeddings.local_encoder import encode_batch
    return encode_batch(texts)


def _embed_openai(texts: list[str], model_name: str) -> list[list[float]]:
    try:
        from openai import OpenAI  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "openai package is not installed. Run: pip install openai"
        ) from exc
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    response = client.embeddings.create(model=model_name, input=texts)
    return [list(map(float, item.embedding)) for item in response.data]


def embed_texts(
    texts: list[str],
    provider: str,
    model: str,
    task_type: Optional[str] = None,
) -> list[list[float]]:
    """Embed a list of texts using the named provider.

    Called by ``embed_repo`` and lazily from ``search_symbols`` when
    ``semantic=True`` and embeddings are missing from the store.

    ``task_type`` is forwarded to providers that support it (currently Gemini).
    Pass ``"RETRIEVAL_DOCUMENT"`` when embedding index documents and
    ``"CODE_RETRIEVAL_QUERY"`` when embedding a search query.  Other providers
    silently ignore the parameter.
    """
    if provider == "local_onnx":
        return _embed_local_onnx(texts, model)
    if provider == "sentence_transformers":
        return _embed_sentence_transformers(texts, model)
    if provider == "gemini":
        return _embed_gemini(texts, model, task_type=task_type)
    if provider == "openai":
        return _embed_openai(texts, model)
    raise ValueError(f"Unknown embedding provider: {provider!r}")


# ── Symbol text representation ─────────────────────────────────────────────


def _sym_text(sym: dict) -> str:
    """Build the text string used to represent a symbol for embedding."""
    parts = [sym.get("name", ""), sym.get("signature", ""), sym.get("summary", "")]
    return " ".join(p for p in parts if p).strip() or sym.get("name", "")


# ── Tool ───────────────────────────────────────────────────────────────────


def embed_repo(
    repo: str,
    batch_size: int = EMBED_BATCH_SIZE,
    force: bool = False,
    storage_path: Optional[str] = None,
    progress_cb: "Optional[Callable[[int, int, str], None]]" = None,
) -> dict:
    """Precompute and store all symbol embeddings for a repository.

    This is an optional warm-up step.  ``search_symbols`` with
    ``semantic=true`` will lazily embed any missing symbols on first call,
    but that first call may be slow on large repos.  Running ``embed_repo``
    upfront eliminates that latency.

    Args:
        repo: Repository identifier (owner/repo or bare name).
        batch_size: Symbols per embedding batch (default 50).
        force: When True, recompute all embeddings even if they already
               exist in the store (default False).
        storage_path: Custom storage path (defaults to CODE_INDEX_PATH).

    Returns:
        Dict with embedding stats and _meta envelope.
        On error: ``{"error": "...", "message": "..."}``
    """
    start = time.perf_counter()

    provider_info = _detect_provider()
    if provider_info is None:
        return {
            "error": "no_embedding_provider",
            "message": (
                "No embedding provider is configured. Options: "
                "pip install 'jcodemunch-mcp[local-embed]' (zero-config ONNX, recommended), "
                "JCODEMUNCH_EMBED_MODEL (sentence-transformers, free/local), "
                "GOOGLE_API_KEY + GOOGLE_EMBED_MODEL (Gemini), or "
                "OPENAI_API_KEY + OPENAI_EMBED_MODEL (OpenAI)."
            ),
        }
    provider, model = provider_info

    # Determine document-side task type (Gemini only).
    doc_task_type: Optional[str] = None
    if provider == "gemini" and _gemini_task_aware():
        doc_task_type = "RETRIEVAL_DOCUMENT"

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    from ..storage.embedding_store import EmbeddingStore
    db_path = store._sqlite._db_path(owner, name)
    emb_store = EmbeddingStore(db_path)

    # Detect a model change and force a rebuild.
    #
    # ⚠⚠ This comment described the behaviour for four releases and the code did
    # not implement it (#500). `stored_dim` was read and only ever used to seed
    # `dim`, nothing compared the stored model against the active one, and a
    # store therefore accumulated vectors of two widths behind a meta row that
    # still named the first. `EmbeddingMatrix` then infers its dimension from
    # the FIRST row and drops every row that disagrees, so the symbols embedded
    # after the change stopped being searchable — silently, and cumulatively.
    stored_dim = emb_store.get_dimension()
    stored_model = emb_store.get_model()
    # ⚠ Unknown is NOT a change. A store written before `embed_model` was
    # persisted has no name, and forcing a re-embed on that would bill every
    # existing user a full rebuild for a model that may well be identical.
    model_changed = bool(stored_model) and bool(model) and stored_model != model
    if not force and model_changed and emb_store.count() > 0:
        logger.info(
            "embed_repo: model changed (%r → %r); forcing re-embed",
            stored_model, model,
        )
        force = True

    # If the task type changed (e.g. Gemini task-awareness toggled), existing
    # embeddings were built with a different task type and must be regenerated.
    stored_task_type = emb_store.get_task_type()
    if not force and stored_task_type != (doc_task_type or "") and emb_store.count() > 0:
        logger.info(
            "embed_repo: task_type changed (%r → %r); forcing re-embed",
            stored_task_type,
            doc_task_type,
        )
        force = True

    if force:
        emb_store.clear()
        symbols_to_embed = list(index.symbols)
        # ⚠ The meta row must be re-derived with the vectors. `dim` is seeded
        # from `stored_dim` below, so leaving it set means the `dim is None`
        # gate never re-fires and the store keeps advertising the OLD dimension
        # and model against freshly written vectors. This also repairs the
        # pre-existing `task_type` force path, which had the same hole.
        stored_dim = None
    else:
        existing_ids = emb_store.get_all_ids()
        symbols_to_embed = [s for s in index.symbols if s["id"] not in existing_ids]

    if not symbols_to_embed:
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "repo": f"{owner}/{name}",
            "provider": provider,
            "model": model,
            "symbols_total": len(index.symbols),
            "symbols_embedded": 0,
            "cached": True,
            "_meta": {"timing_ms": round(elapsed, 1)},
        }

    embedded_count = 0
    error_count = 0
    dim: Optional[int] = stored_dim
    batch_size = max(1, min(batch_size, 200))

    _embed_total = len(symbols_to_embed)
    for i in range(0, _embed_total, batch_size):
        batch = symbols_to_embed[i : i + batch_size]
        texts = [_sym_text(s) for s in batch]
        try:
            vecs = embed_texts(texts, provider, model, task_type=doc_task_type)
        except Exception as exc:
            logger.warning("embed_repo: batch %d failed: %s", i // batch_size, exc)
            error_count += len(batch)
            if progress_cb:
                progress_cb(min(i + len(batch), _embed_total), _embed_total, "")
            continue

        if dim is None and vecs:
            dim = len(vecs[0])
            emb_store.set_dimension(dim, model)
            emb_store.set_task_type(doc_task_type or "")

        emb_store.set_many({batch[j]["id"]: vecs[j] for j in range(len(batch))})
        embedded_count += len(batch)
        if progress_cb:
            progress_cb(min(i + len(batch), _embed_total), _embed_total, batch[-1].get("name", ""))

    elapsed = (time.perf_counter() - start) * 1000
    result: dict = {
        "repo": f"{owner}/{name}",
        "provider": provider,
        "model": model,
        "symbols_total": len(index.symbols),
        "symbols_embedded": embedded_count,
        "symbols_skipped_error": error_count,
        "embedding_dimension": dim,
        "_meta": {"timing_ms": round(elapsed, 1)},
    }
    if doc_task_type:
        result["task_type"] = doc_task_type
    if model_changed:
        # Disclosed, not silent: a forced re-embed is expensive on a large
        # corpus and the caller did not ask for one.
        result["model_changed_from"] = stored_model
        result["rebuild_reason"] = "embedding_model_changed"
    return result
