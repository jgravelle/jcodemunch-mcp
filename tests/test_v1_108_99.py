"""v1.108.99 — audit WS-4 ranking fixes V5 (fusion embedding channel) + V6
(graded retrieval confidence).

V5: the fusion similarity (embedding) channel had three stacked bugs — all
swallowed by a bare ``except Exception: pass`` — so ``search_symbols(fusion=True)``
never used embeddings and still recorded ``semantic_used=True`` in the ledger.

V6: result entries only carried a ``score`` field under ``debug=True``, so
``compute_confidence`` took its no-score neutral early-return and ``_meta.confidence``
was a near-constant (~0.584 fresh) for every non-empty search — the regret and
weight-tuner signals keyed on it could never fire.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# V5 — fusion similarity (embedding) channel wiring                           #
# --------------------------------------------------------------------------- #

def test_v5_embedding_api_shapes_the_channel_uses(tmp_path):
    """The three forms the dead channel got wrong must resolve now: the real
    export is ``embed_texts`` (not ``_embed_texts``), ``EmbeddingStore`` takes a
    positional ``db_path``, and ``get_all()`` takes no arguments."""
    from jcodemunch_mcp.tools import embed_repo
    from jcodemunch_mcp.storage.embedding_store import EmbeddingStore

    assert hasattr(embed_repo, "embed_texts")
    assert hasattr(embed_repo, "_detect_provider")
    assert not hasattr(embed_repo, "_embed_texts"), "the dead channel imported a name that never existed"

    # EmbeddingStore(db_path) — positional, single arg (no base_path= kwarg).
    init_params = list(inspect.signature(EmbeddingStore.__init__).parameters)
    assert init_params[1] == "db_path", init_params

    # get_all() takes only self.
    getall_params = list(inspect.signature(EmbeddingStore.get_all).parameters)
    assert getall_params == ["self"], getall_params

    # And it actually constructs + returns a dict from a fresh db.
    assert isinstance(EmbeddingStore(tmp_path / "e.db").get_all(), dict)


def test_v5_fusion_runs_without_embeddings_and_reports_channels(tmp_path):
    """With no embeddings indexed, fusion still runs (lexical/identity/structural)
    and does not crash on the now-live similarity block."""
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(
        "def get_user(uid):\n    return uid\n\ndef save_user(u):\n    return u\n"
    )
    store = tmp_path / "store"
    res = index_folder(path=str(repo), use_ai_summaries=False, incremental=False, storage_path=str(store))
    out = search_symbols(repo=res["repo"], query="get_user", fusion=True, storage_path=str(store))
    assert "error" not in out, out
    assert out.get("result_count", 0) >= 1
    # The similarity channel is absent (no embeddings) but the others fused.
    channels = out.get("_meta", {}).get("channels", [])
    assert "similarity" not in channels  # nothing to fuse without embeddings
    assert channels  # lexical/identity/structural still ran


# --------------------------------------------------------------------------- #
# V6 — graded retrieval confidence                                            #
# --------------------------------------------------------------------------- #

def _index_sample(tmp: Path):
    from jcodemunch_mcp.tools.index_folder import index_folder
    repo = tmp / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text(
        "def authenticate_user(name):\n    return name\n\n"
        "def validate_token(t):\n    return t\n\n"
        "def parse_config(c):\n    return c\n\n"
        "def load_settings(s):\n    return s\n"
    )
    store = tmp / "store"
    res = index_folder(path=str(repo), use_ai_summaries=False, incremental=False, storage_path=str(store))
    return res["repo"], str(store)


def test_v6_confidence_grades_real_scores(tmp_path: Path):
    """A dominant single match must score higher confidence than a diffuse
    multi-term query, and neither may be the flat no-score neutral value."""
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    repo, store = _index_sample(tmp_path)
    strong = search_symbols(repo=repo, query="authenticate_user", storage_path=store)
    broad = search_symbols(repo=repo, query="parse validate authenticate load", storage_path=store)

    c_strong = strong["_meta"]["confidence"]
    c_broad = broad["_meta"]["confidence"]
    assert c_strong != c_broad, (c_strong, c_broad)
    assert c_strong > c_broad
    # The old flat non-debug value was ~0.584 for BOTH; graded now.
    assert not (abs(c_strong - 0.584) < 1e-3 and abs(c_broad - 0.584) < 1e-3)


def test_v6_ledger_records_real_top_scores(tmp_path: Path):
    """The ranking ledger now records real top1/top2 scores instead of None
    (previously the debug-gated score field left them null)."""
    from jcodemunch_mcp.retrieval.confidence import extract_ledger_features

    # The same list shape search_symbols now passes to the ledger.
    feats = extract_ledger_features([{"score": 12.5}, {"score": 3.1}, {"score": 1.0}])
    assert feats["top1_score"] == 12.5
    assert feats["top2_score"] == 3.1
