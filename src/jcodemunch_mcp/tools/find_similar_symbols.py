"""Find clusters of semantically and structurally similar symbols.

Surfaces consolidation candidates: groups of functions/methods/classes that
appear to do the same thing, with a canonical pick (highest PageRank) and a
verdict tier (near_duplicate / similar_logic / parallel_implementation).

Three signals, blended:
  1. Semantic — embedding cosine similarity (when embed_repo has run)
  2. Structural — Jaccard over signature-token bag + byte-length ratio
  3. Behavioral — Jaccard over the set of callee names (call_references)

When embeddings are absent, the tool falls back to a structural+behavioral
blend and labels the mode accordingly so callers know confidence is lower.

Pre-filters pairs via the BM25 inverted index — only scores pairs sharing at
least one indexed term — to keep the cost sub-N^2 on large repos.
"""

from __future__ import annotations

import logging
import math
import re
import time
from fnmatch import fnmatch
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ._utils import symbol_span_bytes
from ._utils import index_status_to_tool_error, resolve_repo
from ._test_paths import is_test_file as _is_test_file  # noqa: F401  (the one rule, LEDGER L-101)
from .get_context_bundle import _count_tokens

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
_DEFAULT_THRESHOLD = 0.80
_DEFAULT_MIN_SIZE = 30          # byte_length floor; kills getter/wrapper noise
_DEFAULT_MAX_CLUSTERS = 25
_DEFAULT_SEMANTIC_WEIGHT = 0.6
_DEFAULT_TOKEN_BUDGET = 4000
_MAX_PAIRS_PER_BUCKET = 200     # cap to keep pair count bounded on dense terms
_HARD_PAIR_CAP = 100_000        # absolute cap on pairs scored (safety)
_NEAR_DUP_THRESHOLD = 0.92      # avg cluster similarity → near_duplicate verdict

# Why the semantic signal is not in play, and what the caller should do about it.
# ⚠ These are NOT interchangeable: "run embed_repo" is wrong advice for a
# repository that is already embedded, which is the state the old single note
# swallowed (#398, @rknighton). Keyed by `semantic_state`, which ships beside the
# note so a caller can branch without parsing prose.
_STRUCTURAL_NOTES = {
    "repo_not_embedded": (
        "This repository has no embeddings; using structural+behavioral signal only. "
        "Run embed_repo to enable semantic similarity (verdict tier upgrades from "
        "parallel_implementation to near_duplicate/similar_logic)."
    ),
    "candidates_not_embedded": (
        "This repository HAS embeddings, but fewer than two of the candidates in this "
        "scope do, so the semantic signal could not be blended — structural+behavioral "
        "only. Re-run embed_repo to cover symbols added since it last ran, or widen the "
        "scope; running it again on the same symbols will not change this result."
    ),
    "unknown": (
        "Semantic signal not applied and the reason could not be established — the "
        "embedding store could not be read. Using structural+behavioral signal only. "
        "This is not evidence that the repository lacks embeddings."
    ),
    # Unreachable while `mode == "structural"` gates the note, kept so the mapping
    # is total for every value `semantic_state` can take.
    "used": (
        "Semantic signal applied."
    ),
}

# Filenames we skip even when include_tests is True.
_GENERATED_PATTERNS = (
    "_pb2.py", "_pb2_grpc.py", ".pb.go", ".gen.go", ".g.dart",
    ".generated.ts", ".generated.tsx", "_generated.py", ".min.js",
)
# Dunders that look identical structurally but are forced by language.
_DUNDER_SKIP = frozenset({
    "__init__", "__repr__", "__str__", "__hash__", "__eq__",
    "__lt__", "__le__", "__gt__", "__ge__", "__ne__",
    "__enter__", "__exit__", "__getitem__", "__setitem__",
})

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    return dot / denom if denom else 0.0


def _signature_tokens(sym: dict) -> set[str]:
    """Tokenize a symbol's signature to a lowercased identifier set."""
    sig = sym.get("signature") or ""
    return {t.lower() for t in _TOKEN_RE.findall(sig) if len(t) > 1}


def _callee_set(sym: dict) -> set[str]:
    """Set of names this symbol calls (lowercased)."""
    refs = sym.get("call_references") or []
    out: set[str] = set()
    for ref in refs:
        if isinstance(ref, dict):
            name = ref.get("name") or ref.get("target") or ""
        else:
            name = str(ref)
        name = name.strip()
        if name:
            out.add(name.lower())
    return out


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _byte_ratio(a: int, b: int) -> float:
    """Symmetric size ratio: 1.0 = identical, 0.0 = 100× apart."""
    if a <= 0 or b <= 0:
        return 0.0
    lo, hi = (a, b) if a <= b else (b, a)
    return lo / hi


def _looks_generated(file_path: str) -> bool:
    fl = file_path.lower()
    return any(p in fl for p in _GENERATED_PATTERNS)


def _structural_disclosure(mode: str, semantic_state: str) -> dict:
    """The `why no semantic signal` block, or nothing when the signal was used.

    One function because `find_similar_symbols` has three exits that can return a
    structural answer, and before v1.108.211 two of them disclosed nothing and
    the third disclosed the wrong reason.

    Deliberately empty in hybrid mode: `mode` already carries that, and this is
    the most-called shape.
    """
    if mode == "hybrid":
        return {}
    return {
        "semantic_state": semantic_state,
        "note": _STRUCTURAL_NOTES[semantic_state],
    }


def _classify_verdict(avg: float, mode: str) -> str:
    if mode == "structural":
        return "parallel_implementation"
    if avg >= _NEAR_DUP_THRESHOLD:
        return "near_duplicate"
    return "similar_logic"


def _differs_by(canonical: dict, member: dict) -> list[str]:
    """One-line breakdown of how two cluster members diverge."""
    out: list[str] = []
    # Body size
    bc = int(canonical.get("byte_length", 0) or 0)
    bm = int(member.get("byte_length", 0) or 0)
    if bc and bm:
        delta = abs(bc - bm)
        if delta <= 5:
            out.append("body: identical size")
        else:
            out.append(f"body: ±{delta} bytes")
    # Signature param count
    pc = int(canonical.get("param_count", 0) or 0)
    pm = int(member.get("param_count", 0) or 0)
    if pc == pm:
        out.append(f"params: {pc} (match)")
    else:
        out.append(f"params: {pc} vs {pm}")
    # Callee set overlap
    cc = _callee_set(canonical)
    cm = _callee_set(member)
    if cc or cm:
        shared = len(cc & cm)
        unique_total = len(cc ^ cm)
        out.append(f"callees: {shared} shared, {unique_total} unique")
    return out


def find_similar_symbols(
    repo: str,
    threshold: float = _DEFAULT_THRESHOLD,
    min_size: int = _DEFAULT_MIN_SIZE,
    max_clusters: int = _DEFAULT_MAX_CLUSTERS,
    include_tests: bool = False,
    scope: Optional[str] = None,
    include_kinds: Optional[list] = None,
    semantic_weight: float = _DEFAULT_SEMANTIC_WEIGHT,
    token_budget: int = _DEFAULT_TOKEN_BUDGET,
    storage_path: Optional[str] = None,
) -> dict:
    """Find clusters of similar symbols and surface consolidation candidates.

    Blends three signals (semantic embeddings, structural signature, behavioral
    call graph), runs union-find on edges above ``threshold``, classifies each
    cluster into a verdict tier, and picks a canonical symbol per cluster
    (highest PageRank). Pre-filters pairs via the BM25 inverted index to keep
    the cost sub-N^2.

    Args:
        repo: Repository identifier.
        threshold: Minimum combined similarity to form an edge (default 0.80).
        min_size: Minimum byte_length per symbol (default 30; kills wrappers).
        max_clusters: Cap on clusters returned (default 25).
        include_tests: When False (default), test files are skipped.
        scope: Optional glob to limit to a subdirectory.
        include_kinds: Optional kind whitelist; defaults to function/method/class.
        semantic_weight: Embedding weight when embeddings are available (0–1).
        token_budget: Hard cap on the response's signature payload (default 4000).
        storage_path: Custom storage path.

    Returns:
        Dict with ``clusters`` list and summary fields.
    """
    start = time.perf_counter()

    if not (0.0 <= threshold <= 1.0):
        return {"error": "threshold must be in [0.0, 1.0]"}
    if not (0.0 <= semantic_weight <= 1.0):
        return {"error": "semantic_weight must be in [0.0, 1.0]"}
    if min_size < 0:
        return {"error": "min_size must be >= 0"}
    if max_clusters < 1:
        return {"error": "max_clusters must be >= 1"}
    if token_budget < 1:
        return {"error": "token_budget must be >= 1"}

    kinds_filter = set(include_kinds) if include_kinds else {"function", "method", "class"}

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    # Collect candidates with all the per-symbol fields we'll need
    candidates: list[dict] = []
    for sym in index.symbols:
        if sym.get("kind") not in kinds_filter:
            continue
        if sym.get("name", "") in _DUNDER_SKIP:
            continue
        if int(sym.get("byte_length", 0) or 0) < min_size:
            continue
        f = sym.get("file", "")
        if not f:
            continue
        if not include_tests and _is_test_file(f):
            continue
        if _looks_generated(f):
            continue
        if scope and not (fnmatch(f, scope) or f.startswith(scope.rstrip("/") + "/")):
            continue
        candidates.append(sym)

    candidates_considered = len(candidates)

    if candidates_considered < 2:
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "clusters": [],
            "clusters_returned": 0,
            "candidates_considered": candidates_considered,
            "pairs_compared": 0,
            "mode": "structural",
            "total_tokens": 0,
            "budget_tokens": token_budget,
            # The embedding store is never consulted on this path, so neither
            # structural reason applies — do not let a caller read this as
            # "not embedded".
            "semantic_state": "not_applicable",
            "note": "Not enough candidates for similarity analysis.",
            "_meta": {"timing_ms": round(elapsed, 1), "tokens_saved": 0, "total_tokens_saved": 0},
        }

    # Index candidates by id for O(1) lookup
    cand_by_id: dict[str, dict] = {s["id"]: s for s in candidates}
    cand_ids: list[str] = [s["id"] for s in candidates]
    idx_by_id: dict[str, int] = {sid: i for i, sid in enumerate(cand_ids)}

    # ── Embedding signal (optional) ────────────────────────────────────
    embeddings: dict[str, list[float]] = {}
    mode = "structural"
    emb_store = None
    try:
        from ..storage.embedding_store import EmbeddingStore  # noqa: PLC0415
        db_path = store._sqlite._db_path(owner, name)
        emb_store = EmbeddingStore(db_path)
        # Fetch ONLY the prefiltered candidates. The previous count()+get_all()
        # pair read every vector in the repository and then discarded all but
        # `cand_ids`, which is already known one line above (#398, @rknighton).
        # Dropping count() is part of the fix, not a shortcut: it opened a
        # read-WRITE connection whose PRAGMA/CREATE-TABLE bumped the .db mtime
        # before the read even started, so a read-only fetch alone would not
        # have stopped the file from being touched.
        embeddings = emb_store.get_many(cand_ids)
        if len(embeddings) >= 2:
            mode = "hybrid"
    except Exception as exc:  # noqa: BLE001
        logger.debug("find_similar_symbols: embedding load skipped: %s", exc, exc_info=True)

    # Only when the semantic signal is NOT in play, and only then: which of the
    # two structural reasons is it? A candidate we did fetch a vector for already
    # proves the repository is embedded, so the probe runs solely when we fetched
    # none (#398, @rknighton).
    semantic_state = "used" if mode == "hybrid" else "unknown"
    if mode != "hybrid":
        if embeddings:
            semantic_state = "candidates_not_embedded"
        elif emb_store is not None:
            try:
                present = emb_store.has_any()
            except Exception as exc:  # noqa: BLE001
                logger.debug("find_similar_symbols: has_any failed: %s", exc, exc_info=True)
                present = None
            if present is True:
                semantic_state = "candidates_not_embedded"
            elif present is False:
                semantic_state = "repo_not_embedded"

    # ── Pre-filter pairs via BM25 inverted index ───────────────────────
    # Only score pairs that share at least one indexed term.
    cache = getattr(index, "_bm25_cache", {}) or {}
    inverted: dict[str, list[int]] = cache.get("inverted") or {}

    pair_set: set[tuple[int, int]] = set()
    if inverted:
        cand_idx_set = set(idx_by_id.values())
        for _term, postings in inverted.items():
            # postings is a list of indices into index.symbols (not our cand list)
            cand_postings = [i for i in postings if index.symbols[i]["id"] in idx_by_id]
            if len(cand_postings) < 2:
                continue
            if len(cand_postings) > _MAX_PAIRS_PER_BUCKET:
                # Dense term — sample to bound pair count
                cand_postings = cand_postings[:_MAX_PAIRS_PER_BUCKET]
            # Map to our local indices
            local = [idx_by_id[index.symbols[i]["id"]] for i in cand_postings]
            local.sort()
            for a_idx in range(len(local)):
                for b_idx in range(a_idx + 1, len(local)):
                    pair_set.add((local[a_idx], local[b_idx]))
                    if len(pair_set) >= _HARD_PAIR_CAP:
                        break
                if len(pair_set) >= _HARD_PAIR_CAP:
                    break
            if len(pair_set) >= _HARD_PAIR_CAP:
                break
    else:
        # No BM25 index available — fall back to a sized-bucket pre-filter
        # (group by (kind, param_count) to keep N^2 bounded).
        buckets: dict[tuple, list[int]] = {}
        for i, sym in enumerate(candidates):
            key = (sym.get("kind", ""), int(sym.get("param_count", 0) or 0))
            buckets.setdefault(key, []).append(i)
        for bucket_indices in buckets.values():
            if len(bucket_indices) < 2:
                continue
            if len(bucket_indices) > _MAX_PAIRS_PER_BUCKET:
                bucket_indices = bucket_indices[:_MAX_PAIRS_PER_BUCKET]
            for a_idx in range(len(bucket_indices)):
                for b_idx in range(a_idx + 1, len(bucket_indices)):
                    pair_set.add((bucket_indices[a_idx], bucket_indices[b_idx]))
                    if len(pair_set) >= _HARD_PAIR_CAP:
                        break
                if len(pair_set) >= _HARD_PAIR_CAP:
                    break
            if len(pair_set) >= _HARD_PAIR_CAP:
                break

    pairs_compared = 0

    # ── Score pairs and collect edges ──────────────────────────────────
    # Pre-compute per-symbol signature token sets and callee sets.
    sig_sets: list[set[str]] = [_signature_tokens(s) for s in candidates]
    callee_sets: list[set[str]] = [_callee_set(s) for s in candidates]
    byte_lens: list[int] = [int(s.get("byte_length", 0) or 0) for s in candidates]

    edges: list[tuple[float, int, int]] = []  # (score, a_idx, b_idx)
    for a_idx, b_idx in pair_set:
        pairs_compared += 1
        a = candidates[a_idx]
        b = candidates[b_idx]

        # Never cluster a symbol with itself across renames in same file —
        # require different ids (set already guarantees this).

        # Structural: signature-token Jaccard blended with size ratio
        struct_jac = _jaccard(sig_sets[a_idx], sig_sets[b_idx])
        size_ratio = _byte_ratio(byte_lens[a_idx], byte_lens[b_idx])
        structural = 0.6 * struct_jac + 0.4 * size_ratio

        # Behavioral: callee-set Jaccard
        behavioral = _jaccard(callee_sets[a_idx], callee_sets[b_idx])

        # Semantic (optional)
        if mode == "hybrid" and a["id"] in embeddings and b["id"] in embeddings:
            sem = _cosine(embeddings[a["id"]], embeddings[b["id"]])
            sem = max(0.0, sem)  # treat negative cosine as 0 for blending
            non_sem = 0.5 * structural + 0.5 * behavioral
            score = semantic_weight * sem + (1.0 - semantic_weight) * non_sem
        else:
            score = 0.5 * structural + 0.5 * behavioral

        if score >= threshold:
            edges.append((score, a_idx, b_idx))

    if not edges:
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "clusters": [],
            "clusters_returned": 0,
            "candidates_considered": candidates_considered,
            "pairs_compared": pairs_compared,
            "mode": mode,
            "total_tokens": 0,
            "budget_tokens": token_budget,
            # ⚠ This exit carried NO disclosure at all before v1.108.211: a
            # zero-cluster answer from an un-embedded repository read exactly
            # like a zero-cluster answer from an embedded one. Same block as the
            # main return, because a caller cannot tell which exit produced its
            # response.
            **_structural_disclosure(mode, semantic_state),
            "_meta": {"timing_ms": round(elapsed, 1), "tokens_saved": 0, "total_tokens_saved": 0},
        }

    # ── Union-find clustering ──────────────────────────────────────────
    parent = list(range(len(candidates)))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb

    # Aggregate per-cluster similarity stats; use min cluster member as id later.
    pair_score: dict[tuple[int, int], float] = {}
    for score, a_idx, b_idx in edges:
        key = (a_idx, b_idx) if a_idx < b_idx else (b_idx, a_idx)
        pair_score[key] = max(pair_score.get(key, 0.0), score)
        _union(a_idx, b_idx)

    cluster_members: dict[int, list[int]] = {}
    for i in range(len(candidates)):
        # Only include indices that participated in at least one edge
        # (singletons aren't interesting).
        pass
    edge_member_ids = {a for _s, a, _b in edges} | {b for _s, _a, b in edges}
    for i in edge_member_ids:
        root = _find(i)
        cluster_members.setdefault(root, []).append(i)

    # ── PageRank for canonical pick ────────────────────────────────────
    pagerank: dict[str, float] = {}
    try:
        if "pagerank" in cache:
            pagerank = cache["pagerank"]
        else:
            from .pagerank import compute_pagerank  # noqa: PLC0415
            pr, _ = compute_pagerank(
                index.imports or {}, index.source_files, index.alias_map,
                psr4_map=getattr(index, "psr4_map", None),
            )
            pagerank = pr
            cache["pagerank"] = pr
    except Exception as exc:  # noqa: BLE001
        logger.debug("find_similar_symbols: pagerank skipped: %s", exc, exc_info=True)

    # ── Build cluster records ──────────────────────────────────────────
    cluster_records: list[dict] = []
    for root, member_indices in cluster_members.items():
        if len(member_indices) < 2:
            continue

        # Average similarity across all known intra-cluster pair edges
        sims: list[float] = []
        for i_idx in range(len(member_indices)):
            for j_idx in range(i_idx + 1, len(member_indices)):
                a = member_indices[i_idx]
                b = member_indices[j_idx]
                key = (a, b) if a < b else (b, a)
                if key in pair_score:
                    sims.append(pair_score[key])
        if not sims:
            continue
        avg_sim = sum(sims) / len(sims)

        # Canonical pick: highest PageRank by *file*; ties broken by byte_length.
        def _rank_key(idx: int) -> tuple:
            sym = candidates[idx]
            pr = pagerank.get(sym.get("file", ""), 0.0)
            bl = int(sym.get("byte_length", 0) or 0)
            return (-pr, -bl, sym["id"])
        canonical_idx = min(member_indices, key=_rank_key)
        canonical = candidates[canonical_idx]
        canonical_pr = pagerank.get(canonical.get("file", ""), 0.0)
        score_reason = "highest_pagerank" if canonical_pr > 0 else "largest_body"

        verdict = _classify_verdict(avg_sim, mode)

        # Member list, with similarity-to-canonical and differs_by hints.
        members_out: list[dict] = []
        max_bytes = 0
        for mi in member_indices:
            if mi == canonical_idx:
                continue
            key = (canonical_idx, mi) if canonical_idx < mi else (mi, canonical_idx)
            sim_to_canon = pair_score.get(key, avg_sim)
            mem = candidates[mi]
            mb = int(mem.get("byte_length", 0) or 0)
            if mb > max_bytes:
                max_bytes = mb
            members_out.append({
                "symbol_id": mem["id"],
                "similarity": round(sim_to_canon, 4),
                "byte_length": mb,
                "file": mem.get("file", ""),
                "line": mem.get("line", 0),
            })
        # Cluster impact = size × largest member byte_length
        max_bytes = max(max_bytes, int(canonical.get("byte_length", 0) or 0))
        impact = len(member_indices) * max(max_bytes, 1)

        differs = _differs_by(canonical, candidates[member_indices[1 if member_indices[0] == canonical_idx else 0]])

        cluster_records.append({
            "verdict": verdict,
            "size": len(member_indices),
            "avg_similarity": round(avg_sim, 4),
            "mode": mode,
            "impact": impact,
            "canonical": {
                "symbol_id": canonical["id"],
                "score_reason": score_reason,
                "pagerank": round(canonical_pr, 6),
                "byte_length": int(canonical.get("byte_length", 0) or 0),
                "file": canonical.get("file", ""),
                "line": canonical.get("line", 0),
                "signature": (canonical.get("signature") or "").strip(),
            },
            "members": members_out,
            "differs_by": differs,
        })

    # Sort by impact, take top max_clusters, then token-pack.
    cluster_records.sort(key=lambda r: r["impact"], reverse=True)
    cluster_records = cluster_records[:max_clusters]

    out_clusters: list[dict] = []
    total_tokens = 0
    for idx, rec in enumerate(cluster_records):
        # Per-cluster token cost: signature + member ids
        sig = rec["canonical"].get("signature", "")
        cost = _count_tokens(sig) or 1
        cost += sum(_count_tokens(m["symbol_id"]) for m in rec["members"])
        if total_tokens + cost > token_budget and out_clusters:
            break
        rec_out = dict(rec)
        rec_out["id"] = idx
        rec_out["tokens"] = cost
        out_clusters.append(rec_out)
        total_tokens += cost

    # Token-savings ledger
    raw_bytes = symbol_span_bytes(candidates)
    response_bytes = total_tokens * 4
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="find_similar_symbols")

    elapsed = (time.perf_counter() - start) * 1000

    result = {
        "clusters": out_clusters,
        "clusters_returned": len(out_clusters),
        "candidates_considered": candidates_considered,
        "pairs_compared": pairs_compared,
        "mode": mode,
        "total_tokens": total_tokens,
        "budget_tokens": token_budget,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
        },
    }

    result.update(_structural_disclosure(mode, semantic_state))

    return result
