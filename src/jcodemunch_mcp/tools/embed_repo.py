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
from ..embeddings.advice import NO_PROVIDER_MESSAGE
from ._utils import index_status_to_tool_error, resolve_repo
from ..embeddings.failures import FailureLedger

logger = logging.getLogger(__name__)

# Batch size used internally by the lazy embedding path in search_symbols.
EMBED_BATCH_SIZE = 50

# ── Provider detection ──────────────────────────────────────────────────────


_PROVIDER_BACKENDS: dict[str, tuple[str, str]] = {
    # provider -> (import name, the extra that installs it)
    "sentence_transformers": ("sentence_transformers", "jcodemunch-mcp[semantic]"),
    "gemini": ("google.generativeai", "jcodemunch-mcp[gemini]"),
    "openai": ("openai", "openai"),
}


def _backend_available(provider: str) -> bool:
    """Is the package this provider needs importable?

    ⚠ Import-checked, not merely configured. Every explicit provider's embed
    function raises ``ImportError`` when its package is missing, so selecting
    one we cannot run turns a silently-ignored setting (#488's complaint) into a
    hard failure at embed time — the same defect with a louder symptom.
    """
    spec = _PROVIDER_BACKENDS.get(provider)
    if spec is None:
        return True
    try:
        import importlib.util  # noqa: PLC0415

        return importlib.util.find_spec(spec[0]) is not None
    except Exception:
        logger.debug("backend probe failed for %s", provider, exc_info=True)
        return False


def _detect_provider_detailed() -> tuple[Optional[tuple[str, str]], str, list[str]]:
    """Return ((provider, model) or None, reason, skipped).

    ⚠⚠ **An explicit LOCAL choice outranks the local default (#488); a PAID
    REMOTE provider never does.** Until v1.108.286 the bundled ONNX encoder
    returned at priority 0, so once ``[local-embed]`` was installed
    ``embed_model`` / ``JCODEMUNCH_EMBED_MODEL`` was read after the early return
    and changed nothing — no warning, no field in any response. Absence of a
    setting and an explicit setting are different intents, and only the first
    should get the default.

    ⚠⚠ **The cloud branches are DELIBERATELY still below ONNX, and this is not
    an oversight.** ``tests/test_paid_embeddings_optin.py`` exists because
    jdocmunch's resolver auto-selected OpenAI from an ambient key and began
    billing a remote account *and shipping the indexed corpus off the machine*.
    jcm's second line of defence against that is precisely that ONNX wins before
    any cloud branch is reached. **`embed_model` is free and on-machine, so
    promoting it costs a re-embed; promoting Gemini or OpenAI costs money and
    exfiltrates the corpus.** Those are not the same decision and they do not
    get the same answer.

    ⚠ **The local promotion applies only when it is USABLE.**
    ``_embed_sentence_transformers`` raises ``ImportError`` without the package,
    so selecting it over a working ONNX install would turn a silently-ignored
    setting into a hard failure at embed time — the same defect with a louder
    symptom. An unusable explicit setting is skipped and NAMED, never silently
    dropped.
    """
    skipped: list[str] = []

    from ..embeddings.local_encoder import (
        is_onnxruntime_available, is_model_available, MODEL_NAME,
    )
    onnx_ready = is_onnxruntime_available() and is_model_available()

    # 1. sentence-transformers, named explicitly. Free, local, and therefore
    #    safe to rank above the bundled default.
    #
    #    Global-only by design (#301): per-project embedding models would break
    #    cross-project semantic search consistency. If per-repo selection ever
    #    becomes a feature, this needs a repo arg threaded from embed_repo().
    st_model = (
        _config.get("embed_model", "") or os.environ.get("JCODEMUNCH_EMBED_MODEL", "")
    ).strip()
    if st_model:
        # ⚠⚠ The usability probe decides PRECEDENCE ONLY, never selection.
        # Promoting an uninstalled sentence-transformers OVER a working ONNX
        # install would trade a silently-ignored setting for a hard
        # `ImportError` at embed time — the same defect, louder. But when ONNX
        # is not available there is nothing to protect, and returning this
        # provider is what hands the caller the actionable "pip install
        # 'jcodemunch-mcp[semantic]'" message instead of a bare None. Probing
        # unconditionally broke exactly that, on every machine without the
        # package.
        if not onnx_ready or _backend_available("sentence_transformers"):
            return ("sentence_transformers", st_model), "embed_model", skipped
        skipped.append(
            f"embed_model={st_model!r} (sentence-transformers not installed; "
            f"pip install '{_PROVIDER_BACKENDS['sentence_transformers'][1]}') — "
            f"using the bundled encoder instead"
        )

    # 2. The zero-config bundled encoder. Above the cloud branches, so a machine
    #    with an ambient API key never silently starts billing.
    if onnx_ready:
        return ("local_onnx", MODEL_NAME), "bundled_default", skipped

    # 3. Gemini. Requires TWO signals — the key AND an explicit model. Naming
    #    the model is the opt-in; a bare key must never select a paid provider.
    google_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    google_model = os.environ.get("GOOGLE_EMBED_MODEL", "").strip()
    if google_key and google_model:
        return ("gemini", google_model), "google_api_key", skipped

    # 4. OpenAI. OPENAI_API_KEY alone is used for the local-LLM summariser, so
    #    OPENAI_EMBED_MODEL must be set explicitly to avoid conflation.
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    openai_model = os.environ.get("OPENAI_EMBED_MODEL", "").strip()
    if openai_key and openai_model:
        return ("openai", openai_model), "openai_api_key", skipped

    return None, "none_configured", skipped


def _detect_provider() -> Optional[tuple[str, str]]:
    """Return (provider_name, model_name) or None when nothing is configured.

    Priority order (first match wins):
    0. sentence-transformers  — ``embed_model`` config key or ``JCODEMUNCH_EMBED_MODEL`` env var
    1. local_onnx             — ``onnxruntime`` installed + ONNX model present (zero-config default)
    2. Gemini                 — ``GOOGLE_API_KEY`` + ``GOOGLE_EMBED_MODEL``
    3. OpenAI                 — ``OPENAI_API_KEY`` + ``OPENAI_EMBED_MODEL``

    ⚠ Only the FREE, on-machine option was promoted above the default. The paid
    remote providers stay below it — see :func:`_detect_provider_detailed`.

    Thin wrapper over :func:`_detect_provider_detailed`, kept at a two-tuple so
    every existing caller is unchanged. Use the detailed form when the caller
    can surface *why* a provider was chosen or what was skipped.
    """
    selected, _reason, _skipped = _detect_provider_detailed()
    return selected


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


# GHSA-jhr6-gm9c-rqjv: before this release, loading a LOCAL model directory
# bypassed `trust_remote_code` and executed the custom Python inside it.
_ST_LOCAL_CODE_FIXED = (5, 6, 0)


def _sentence_transformers_version() -> tuple[Optional[tuple[int, ...]], bool, str]:
    """(parsed release, is a pre-release, raw text) of the sentence-transformers actually imported.

    The module's own `__version__` first, then the distribution metadata.
    `None` means it could not be read, which is not the same as fixed.
    """
    import re
    import sys

    raw = getattr(sys.modules.get("sentence_transformers"), "__version__", None)
    if not isinstance(raw, str):
        try:
            import importlib.metadata

            raw = importlib.metadata.version("sentence-transformers")
        except Exception:
            logger.debug("sentence-transformers version could not be read", exc_info=True)
            return None, False, ""
    if not isinstance(raw, str):  # a distribution with no Version field answers None
        return None, False, ""
    match = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?(.*)", raw.strip())
    if not match:
        return None, False, raw
    pre = bool(re.match(r"[-_.]?(a|b|c|rc|alpha|beta|pre|preview|dev)\d*", match.group(4), re.IGNORECASE))
    return tuple(int(part or 0) for part in match.groups()[:3]), pre, raw


def _is_local_model_path(model_name: str) -> bool:
    """True when the name is a path that exists.

    The library tests `os.path.exists` on the RAW name, so the raw check is the
    one that matches what it would load. The expanded check (`~`, `$VAR`) only
    adds refusals: the library does not expand, so such a name would not load
    from disk there. Refusing it is the safe direction.
    """
    expanded = os.path.expanduser(os.path.expandvars(model_name))
    return os.path.exists(model_name) or os.path.exists(expanded)


def _refuse_local_model_on_an_old_release(model_name: str) -> None:
    """Raise before a local model directory reaches a release that runs its code unasked.

    The `semantic` extra's floor is install metadata: an upgrade that does not
    name the extra, or a `sentence-transformers` installed directly, keeps an
    older release. A Hub name is not refused; the advisory is the local path.
    """
    if not _is_local_model_path(model_name):
        return
    parsed, pre, raw = _sentence_transformers_version()
    # A pre-release OF the fixed release (5.6.0.dev0, 5.6.0rc1) may predate the fix.
    if parsed is not None and (parsed > _ST_LOCAL_CODE_FIXED or (parsed == _ST_LOCAL_CODE_FIXED and not pre)):
        return
    found = f"sentence-transformers {raw}" if parsed is not None else (
        "a sentence-transformers whose version could not be read"
    )
    # The cause and the remedy come first: the failure ledger keeps the first
    # 300 characters of a message, and a path can be longer than that.
    raise RuntimeError(
        f"Refused: this environment has {found}, and releases before 5.6.0 final run the custom "
        "code inside a local model directory with trust_remote_code off (GHSA-jhr6-gm9c-rqjv). "
        "Run: pip install -U 'jcodemunch-mcp[semantic]', or set embed_model to a Hub model name. "
        f"embed_model is the local path {model_name!r}."
    )


def _embed_sentence_transformers(texts: list[str], model_name: str) -> list[list[float]]:
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "sentence-transformers is not installed. "
            "Run: pip install 'jcodemunch-mcp[semantic]'"
        ) from exc
    _refuse_local_model_on_an_old_release(model_name)
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

    provider_info, _provider_reason, _provider_skipped = _detect_provider_detailed()
    if provider_info is None:
        return {
            "error": "no_embedding_provider",
            "message": NO_PROVIDER_MESSAGE,
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

    from ..storage.embedding_store import (
        STALE_MODEL_CHANGED,
        EmbeddingStore,
        stale_reason,
    )
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
    #
    # ⚠ ONE reading of the store decides the rebuild AND what is disclosed
    # (LEDGER L-121, review rounds 4 and 5): with the model read by one call
    # and the rule fed by another, a failed second reading skipped the rebuild
    # while the first still reported `embedding_model_changed`. `for_writer`
    # reads over the read-write connection this function writes with.
    stored_meta = emb_store.read_meta(for_writer=True)
    stored_dim: Optional[int] = None
    stored_model: Optional[str] = None
    if stored_meta is None:
        # Nothing could be read, so nothing is claimed about the model; a
        # dimension row that can be read still stops a re-stamp over vectors
        # this call did not write.
        stored_dim = emb_store.get_dimension()
    elif stored_meta["has_vectors"]:
        stored_dim = stored_meta["dimension"]
        stored_model = stored_meta["model"]
    else:
        # An empty store has no stamp to honour. `clear()` removes the rows
        # with the vectors now; a store emptied before that still carries
        # them, and seeding `dim` from one skipped the re-stamp below, so new
        # vectors were written under the old model's name.
        emb_store.drop_orphan_stamp()
    # ⚠ Unknown is NOT a change. A store written before `embed_model` was
    # persisted has no name, and forcing a re-embed on that would bill every
    # existing user a full rebuild for a model that may well be identical.
    model_changed = bool(stored_model) and bool(model) and stored_model != model

    # A changed model, a changed task type (e.g. Gemini task-awareness toggled)
    # and vectors with no metadata each force a rebuild. The rule and its three
    # states (#523) are `embedding_store.stale_reason`, which the semantic
    # top-up in `search_symbols` asks too (LEDGER L-121); do not restate it here.
    rebuild_reason: Optional[str] = None
    if not force:
        stale = stale_reason(stored_meta, model, doc_task_type or "")
        if stale:
            logger.info(
                "embed_repo: %s (stored model %r, active %r, task type %r); forcing re-embed",
                stale, stored_model, model, doc_task_type,
            )
            force = True
            if stale != STALE_MODEL_CHANGED:
                rebuild_reason = stale

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
    # CF-66 (zvec-grep #81's applicable half): the CAUSE of a failed batch used
    # to reach the caller as a count and the log only, so a bad key, an outage
    # and an unserved model all read as `symbols_skipped_error: N`. The ledger
    # is shared with search_symbols' lazy top-up, the same loop one tool over.
    failures = FailureLedger()
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
            failures.record(exc, items=len(batch))
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
    if failures:
        failures.disclose(result)
        result["all_batches_failed"] = embedded_count == 0
    # #488: name WHY this provider was chosen, so an index is reproducible from
    # the record rather than by re-deriving the resolver's precedence.
    result["provider_reason"] = _provider_reason
    if _provider_skipped:
        # An explicit setting we could not honour is disclosed, never dropped.
        # Silently ignoring it is the defect #488 reported; silently failing on
        # it at embed time would be the same defect with a louder symptom.
        result["provider_skipped"] = _provider_skipped
    if model_changed:
        # Disclosed, not silent: a forced re-embed is expensive on a large
        # corpus and the caller did not ask for one.
        result["model_changed_from"] = stored_model
        result["rebuild_reason"] = "embedding_model_changed"
    elif rebuild_reason:
        # The same disclosure for the two rebuilds this function decides on
        # its own besides a model change (#523).
        result["rebuild_reason"] = rebuild_reason
    return result
