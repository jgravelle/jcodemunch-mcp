#!/usr/bin/env python3
"""Odysseus rag_server vs jCodeMunch -- retrieval-layer benchmark (complementary).

WHAT THIS MEASURES
------------------
For the SAME code-navigation queries, how many tokens each retrieval layer
injects into the model's context:

    Odysseus built-in rag_server   vs   jCodeMunch (run inside Odysseus over SSE)

This is the RETRIEVAL-LAYER comparison only. It is NOT a live agent-loop run --
standing up the Odysseus Docker stack is sandbox/VM-only, and the container->host
SSE dial is still community-tested. The agent-loop (tokens/turns/cost/success)
harness is a separate, VM-only job. What this answers today is the honest core
question: does routing code retrieval through jCodeMunch put less (and cleaner)
context in front of the model than Odysseus's embedding RAG, on identical source.

FRAMING
-------
Complementary, not adversarial. Odysseus is an MCP HOST; jCodeMunch plugs into
it. The result is read as "jCodeMunch-over-SSE makes Odysseus cheaper/more
precise on code tasks vs its built-in rag_server" -- not "we beat Odysseus."

ODYSSEUS rag_server PIPELINE -- reproduced faithfully from source
----------------------------------------------------------------
Source: pewdiepie-archdaemon/odysseus @ src/rag_vector.py (VectorRAG), src/config.py.

    Embeddings : sentence-transformers/all-MiniLM-L6-v2
                 (Odysseus FASTEMBED_MODEL default; 384d, FastEmbed/ONNX).
                 Same model run_rag_baseline.py already uses as its default.
    Chunking   : VectorRAG._split_into_chunks, ported VERBATIM below
                 (character-based, sentence-aware; chunk_size=1000 CHARS,
                 overlap=200 CHARS -- NOT token-based).
    Retrieval  : VectorRAG.search default k=5.

FAITHFUL-MIRROR CAVEATS (documented, not hidden)
------------------------------------------------
  * Vector store: this harness uses FAISS; Odysseus uses ChromaDB. Immaterial to
    token accounting -- both return the k nearest chunks; only the ANN backend
    differs. (We are counting injected context tokens, not ANN recall.)
  * Retrieval scoring: Odysseus ranks HYBRID (0.7 vector + 0.3 keyword) across
    its lanes; this harness ranks by pure vector similarity. Hybrid can reorder
    which chunks land in the top-k, but the token COST of k chunks is identical.
    Retrieval relevance is reported separately via a term-overlap heuristic so a
    reorder shows up as a quality delta, not a hidden token delta.
  * jCodeMunch figures are read from benchmarks/jcm_reference.json, written by
    run_benchmark.py --reference (measure_jmunch) against the same jCodeMunch
    IndexStore content -- both sides read byte-identical source. When the index
    state behind the artifact differs from the one measured here, the affected
    rows are marked cross-run rather than silently divided against each other.

USAGE
-----
    pip install -r benchmarks/requirements-rag-bench.txt
    python benchmarks/harness/run_odysseus_compare.py
    python benchmarks/harness/run_odysseus_compare.py --repos expressjs/express
    python benchmarks/harness/run_odysseus_compare.py --out benchmarks/odysseus_compare_results.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import List

# ---------------------------------------------------------------------------
# Reuse the proven plumbing from run_rag_baseline.py (same dir): tokenizer,
# serialization, jCodeMunch IndexStore document loading, embedding model,
# FAISS/Document imports, task corpus, and the loaded jCodeMunch reference artifact.
# Importing it also enforces the same requirements-rag-bench.txt dependency set.
# ---------------------------------------------------------------------------
_HARNESS_DIR = Path(__file__).resolve().parent
_BENCH_DIR = _HARNESS_DIR.parent
sys.path.insert(0, str(_HARNESS_DIR))

import run_rag_baseline as rb  # noqa: E402  (sibling module, same directory)
from run_rag_baseline import (  # noqa: E402
    Document,
    FAISS,
    HuggingFaceEmbeddings,
    EMBED_MODEL,
    _REFERENCE,
    reference_drift,
    reference_entry,
    count_tokens,
    _serialize,
    _ensure_indexed,
    _load_tasks,
    load_documents_from_index,
    analyze_retrieval_precision,
    analyze_chunk_integrity,
)

# ---------------------------------------------------------------------------
# Odysseus rag_server configuration (mirrors src/rag_vector.py + src/config.py)
# ---------------------------------------------------------------------------
ODYSSEUS_CHUNK_SIZE = 1000     # VectorRAG._split_into_chunks default (CHARACTERS)
ODYSSEUS_OVERLAP = 200         # VectorRAG._split_into_chunks default (CHARACTERS)
ODYSSEUS_K = 5                 # VectorRAG.search default k
ODYSSEUS_EMBED_MODEL = EMBED_MODEL  # all-MiniLM-L6-v2 -- same default both sides


def odysseus_split_into_chunks(
    text: str, chunk_size: int = ODYSSEUS_CHUNK_SIZE, overlap: int = ODYSSEUS_OVERLAP
) -> List[str]:
    """Verbatim port of Odysseus VectorRAG._split_into_chunks (src/rag_vector.py).

    Character-based, sentence-aware. Kept byte-faithful to the upstream so the
    chunk boundaries this harness embeds are the ones Odysseus would actually
    produce. Do not "improve" it -- fidelity to upstream is the whole point.
    """
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    # Split into sentences first
    sentences = re.split(r'(?<=[.!?])\s+|\n{2,}', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    chunks: List[str] = []
    current_chunk: List[str] = []
    current_len = 0

    for sentence in sentences:
        sent_len = len(sentence)

        # If a single sentence exceeds chunk_size, split it by character
        if sent_len > chunk_size:
            if current_chunk:
                chunks.append(' '.join(current_chunk))
                current_chunk = []
                current_len = 0
            for start in range(0, sent_len, chunk_size - overlap):
                chunks.append(sentence[start:start + chunk_size])
            continue

        if current_len + sent_len + 1 > chunk_size and current_chunk:
            chunks.append(' '.join(current_chunk))
            overlap_sentences: List[str] = []
            overlap_len = 0
            for s in reversed(current_chunk):
                if overlap_len + len(s) > overlap:
                    break
                overlap_sentences.insert(0, s)
                overlap_len += len(s) + 1
            current_chunk = overlap_sentences
            current_len = sum(len(s) for s in current_chunk) + max(0, len(current_chunk) - 1)

        current_chunk.append(sentence)
        current_len += sent_len + (1 if current_len > 0 else 0)

    if current_chunk:
        chunks.append(' '.join(current_chunk))

    return chunks if chunks else [text]


def build_odysseus_index(
    docs: List[Document], embeddings: HuggingFaceEmbeddings
) -> tuple[FAISS, int, float]:
    """Chunk docs with Odysseus's splitter, embed with all-MiniLM-L6-v2, build FAISS."""
    chunks: List[Document] = []
    for d in docs:
        for piece in odysseus_split_into_chunks(d.page_content):
            chunks.append(Document(page_content=piece, metadata=dict(d.metadata)))
    t0 = time.perf_counter()
    index = FAISS.from_documents(chunks, embeddings)
    embed_time = time.perf_counter() - t0
    return index, len(chunks), embed_time


def measure_odysseus_query(index: FAISS, query: str) -> dict:
    """Tokens Odysseus's rag_server would inject for one query.

    rag_server.search(k=5) returns the k retrieved documents straight into the
    model context (no separate symbol-fetch step). So injected context = the
    k chunks serialized. We count tiktoken cl100k_base tokens of that payload,
    matching run_rag_baseline.py's accounting unit.
    """
    t = time.perf_counter()
    retrieved = index.similarity_search_with_score(query, k=ODYSSEUS_K)
    query_ms = round((time.perf_counter() - t) * 1000, 1)

    objs = [
        {"file": doc.metadata.get("source", "?"), "content": doc.page_content, "score": float(score)}
        for doc, score in retrieved
    ]
    tokens = count_tokens(_serialize(objs))

    # Relevance heuristic (term overlap) -- surfaces a hybrid-vs-vector reorder
    # as a quality delta rather than letting it hide inside identical token cost.
    with_terms = sum(
        1 for doc, _ in retrieved
        if analyze_retrieval_precision(doc, query)["contains_query_terms"]
    )
    # Completeness heuristic -- the load-bearing one. Embedding RAG's "cheaper"
    # tokens are often a code unit cut mid-definition. jCodeMunch returns whole
    # symbols by construction, so fewer tokens for RAG is not free: it can mean
    # truncated context. Count how many of the k chunks are complete vs split.
    integ = [analyze_chunk_integrity(doc) for doc, _ in retrieved]
    return {
        "query": query,
        "tokens": tokens,
        "chunks_returned": len(retrieved),
        "chunks_with_terms": with_terms,
        "chunks_complete": sum(1 for a in integ if a["chunk_complete"]),
        "chunks_split": sum(1 for a in integ if a["chunk_split"]),
        "query_ms": query_ms,
    }


def benchmark_repo(owner_repo: str, embeddings: HuggingFaceEmbeddings, queries: list[str]) -> dict:
    if not _ensure_indexed(owner_repo):
        return {"repo": owner_repo, "error": f"Could not index {owner_repo}"}
    try:
        docs, file_count = load_documents_from_index(owner_repo)
    except RuntimeError as exc:
        return {"repo": owner_repo, "error": str(exc)}
    if not docs:
        return {"repo": owner_repo, "error": "No content loaded from index"}

    baseline_tokens = sum(count_tokens(d.page_content) for d in docs)
    print(f"  baseline: {file_count} files, {baseline_tokens:,} tokens", file=sys.stderr)

    print("  [odysseus] chunking (char/sentence) + FAISS build ...", file=sys.stderr, end=" ", flush=True)
    index, chunk_count, embed_time = build_odysseus_index(docs, embeddings)
    print(f"{chunk_count:,} chunks, {round(embed_time, 1)}s embed", file=sys.stderr)

    tasks = []
    for q in queries:
        row = measure_odysseus_query(index, q)
        tasks.append(row)
        print(f"    '{q}' -> {row['tokens']:,} tokens", file=sys.stderr)

    return {
        "repo": owner_repo,
        "file_count": file_count,
        "baseline_tokens": baseline_tokens,
        "chunk_count": chunk_count,
        "embed_time_s": round(embed_time, 2),
        "tasks": tasks,
    }


def render_markdown(all_results: list[dict]) -> str:
    L: list[str] = []
    L += [
        "# Odysseus rag_server vs jCodeMunch -- retrieval-layer benchmark",
        "",
        "**What this is:** for identical code-navigation queries, the tokens each",
        "retrieval layer injects into the model's context. Complementary framing --",
        "jCodeMunch runs *inside* Odysseus over SSE; this shows the delta of routing",
        "code retrieval through it instead of the built-in `rag_server`.",
        "",
        "**Odysseus pipeline reproduced from source** (`src/rag_vector.py`): "
        f"embeddings `{ODYSSEUS_EMBED_MODEL}`; chunking `_split_into_chunks` "
        f"(char/sentence, size={ODYSSEUS_CHUNK_SIZE} chars, overlap={ODYSSEUS_OVERLAP}); "
        f"retrieval `search(k={ODYSSEUS_K})`.",
        "",
        "**jCodeMunch figures** are read from `benchmarks/jcm_reference.json`, written by",
        "`run_benchmark.py --reference` (`measure_jmunch`) against the same IndexStore",
        "content -- both sides read byte-identical source. A repo the artifact does not",
        "cover gets no jCodeMunch number; nothing here is estimated.",
        "",
        "**Read the two axes together.** Token count alone is a trap: Odysseus's",
        "rag_server returns fixed ~1000-char fragments, so on repos with large",
        "symbols it can inject *fewer* tokens than jCodeMunch -- but those fragments",
        "are frequently cut mid-definition. jCodeMunch returns *complete* symbols by",
        "construction. So compare `tokens/query` next to `complete chunks` (of 5):",
        "RAG cheapness that comes with split chunks is truncated context, not a win.",
        "",
        "**Caveats:** FAISS here vs ChromaDB in Odysseus (immaterial to token count);",
        "pure-vector ranking here vs Odysseus's 0.7/0.3 hybrid (can reorder top-k, not",
        "its token cost -- relevance reported separately). Not a live agent-loop run.",
        "",
        "| Repo | Files | Odysseus rag tokens/q | jCodeMunch tokens/q | Token delta | Odysseus complete/5 | Odysseus split/5 | Odysseus terms-hit/5 |",
        "|------|------:|----------------------:|--------------------:|------------:|:-------------------:|:----------------:|:--------------------:|",
    ]

    cross_run_notes: list[str] = []

    for res in all_results:
        repo = res["repo"]
        if "error" in res:
            L.append(f"| {repo} | -- | -- | -- | **ERROR:** {res['error']} | -- | -- | -- |")
            continue
        valid = [t for t in res["tasks"] if "tokens" in t]
        n = len(valid) or 1
        ody_avg = sum(t["tokens"] for t in valid) / n
        terms_avg = sum(t["chunks_with_terms"] for t in valid) / n
        comp_avg = sum(t["chunks_complete"] for t in valid) / n
        split_avg = sum(t["chunks_split"] for t in valid) / n
        entry = reference_entry(_REFERENCE, repo)
        jcm_avg = entry["avg_tokens_per_query"] if entry else 0
        drift = reference_drift(entry, res.get("file_count", 0), res.get("baseline_tokens", 0))
        if drift:
            cross_run_notes.append(f"- `{repo}`: {drift}")
        if jcm_avg:
            marker = "†" if drift else ""
            ratio = ody_avg / jcm_avg
            delta = (
                f"jcm **{ratio:.1f}x leaner**{marker}"
                if ratio >= 1
                else f"RAG {1 / ratio:.1f}x leaner*{marker}"
            )
            jcm_col = f"{jcm_avg:,}{marker}"
        else:
            # No estimate. A repo the reference artifact does not cover has no
            # jCodeMunch number, and the table says so.
            delta = "not measured"
            jcm_col = "n/a"
        L.append(
            f"| {repo} | {res['file_count']:,} | {ody_avg:,.0f} | {jcm_col} | {delta} "
            f"| {comp_avg:.1f} | {split_avg:.1f} | {terms_avg:.1f} |"
        )

    L += [
        "",
        "\\* *Where RAG shows fewer tokens, check its complete/5 and split/5: the "
        "saving comes from truncated ~1000-char fragments, while jCodeMunch's tokens "
        "are whole symbols. Cheaper context that is cut mid-function is not cheaper "
        "to reason over.*",
    ]

    if cross_run_notes:
        L += [
            "",
            "† *Cross-run comparison: the Odysseus column was measured against the index "
            "state in this run and the jCodeMunch column against a different one, so the "
            "ratio mixes two corpora. Re-run `run_benchmark.py --reference` to put both "
            "sides on the same index.*",
            "",
        ] + cross_run_notes

    L += [
        "",
        "## Per-query detail",
        "",
    ]
    for res in all_results:
        if "error" in res:
            continue
        L.append(f"### {res['repo']}")
        L.append("")
        L.append(f"*{res['chunk_count']:,} Odysseus chunks | {res['embed_time_s']}s embed*")
        L.append("")
        L += [
            "| Query | Odysseus rag tokens | Complete/5 | Split/5 | Terms-hit/5 | Query ms |",
            "|-------|-------------------:|:----------:|:-------:|:-----------:|---------:|",
        ]
        for t in res["tasks"]:
            L.append(
                f"| `{t['query']}` | {t['tokens']:,} | "
                f"{t['chunks_complete']}/{t['chunks_returned']} | "
                f"{t['chunks_split']}/{t['chunks_returned']} | "
                f"{t['chunks_with_terms']}/{t['chunks_returned']} | {t['query_ms']} |"
            )
        L.append("")

    return "\n".join(L)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repos", nargs="*", help="owner/repo to benchmark (default: canonical 3)")
    parser.add_argument("--out", default=str(_BENCH_DIR / "odysseus_compare_results.md"))
    parser.add_argument("--json", dest="json_out", default=str(_BENCH_DIR / "odysseus_compare_results.json"))
    parser.add_argument("--embed-model", default=ODYSSEUS_EMBED_MODEL)
    args = parser.parse_args()

    _, queries = _load_tasks()
    repos = args.repos or rb.DEFAULT_REPOS

    print("=" * 60, file=sys.stderr)
    print("Odysseus rag_server vs jCodeMunch -- retrieval-layer benchmark", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  Embeddings:  {args.embed_model}", file=sys.stderr)
    print(f"  Chunking:    char/sentence, size={ODYSSEUS_CHUNK_SIZE}, overlap={ODYSSEUS_OVERLAP}", file=sys.stderr)
    print(f"  Retrieval:   k={ODYSSEUS_K}", file=sys.stderr)
    print(f"  Repos:       {', '.join(repos)}", file=sys.stderr)
    print(f"  Queries:     {len(queries)}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    print(f"Loading embedding model: {args.embed_model}", file=sys.stderr)
    try:
        embeddings = HuggingFaceEmbeddings(
            model_name=args.embed_model,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
    except Exception as exc:
        sys.exit(f"Failed to load embedding model '{args.embed_model}': {exc}")

    all_results: list[dict] = []
    for repo in repos:
        print(f"\n{'=' * 40}\nBenchmarking: {repo}\n{'=' * 40}", file=sys.stderr)
        t0 = time.perf_counter()
        try:
            res = benchmark_repo(repo, embeddings, queries)
        except Exception as exc:
            res = {"repo": repo, "error": str(exc)}
        print(f"  done in {round(time.perf_counter() - t0, 1)}s", file=sys.stderr)
        all_results.append(res)

    md = render_markdown(all_results)
    Path(args.out).write_text(md, encoding="utf-8")
    Path(args.json_out).write_text(json.dumps(all_results, indent=2, default=str), encoding="utf-8")
    print(f"\nMarkdown written: {args.out}", file=sys.stderr)
    print(f"JSON written:     {args.json_out}", file=sys.stderr)
    print(md)


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
