#!/usr/bin/env python3
"""LangChain RAG baseline benchmark harness for jcodemunch-mcp.

Builds a standard LangChain RAG pipeline and runs the same queries/repos
as run_benchmark.py, producing an apples-to-apples token-efficiency comparison:
    Raw baseline  vs.  RAG (512)  vs.  RAG (1024)  vs.  RAG (2048)  vs.  jCodemunch

Usage:
    pip install -r benchmarks/requirements-rag-bench.txt
    python benchmarks/harness/run_rag_baseline.py
    python benchmarks/harness/run_rag_baseline.py --out benchmarks/rag_baseline_results.md

Methodology
-----------
File source:  jCodemunch IndexStore (IndexStore.load_index → index.source_files).
              Repos are auto-indexed via index_repo if not already present.
              run_benchmark.py reads the same IndexStore, so baselines are identical.

Baseline:     concatenate all indexed source files, count tiktoken cl100k_base tokens.

RAG workflow: for each (repo × query × chunk_size):
              1. similarity_search(query, k=5)   → serialize 5 chunks → count tokens
              2. use top-3 chunks as "fetched"   → serialize 3 chunks → count tokens
              Total = search tokens + fetch tokens  (mirrors jCodemunch search+get_symbol)

Embeddings:   sentence-transformers/all-MiniLM-L6-v2  (local, no API key, LangChain default)
Vector store: FAISS  (faiss-cpu, in-memory per run)
Splitter:     RecursiveCharacterTextSplitter.from_tiktoken_encoder  (true token-based chunks)
Chunk sizes:  512, 1024, 2048 tokens  (~10 % overlap each)

Tokenizer:    tiktoken cl100k_base

Output:       benchmarks/rag_baseline_results.md  (markdown)
              benchmarks/rag_baseline_results.json (raw data)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Path bootstrap — add src/ so we can import jcodemunch_mcp directly,
# same as run_benchmark.py does.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BENCH_DIR = _REPO_ROOT / "benchmarks"
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# The jCodeMunch side of this comparison comes from run_benchmark.py's reference
# artifact — one measurement path, one definition of "jCodeMunch tokens".
from run_benchmark import (  # noqa: E402  (sibling module, same directory)
    load_reference,
    reference_drift,
    reference_entry,
)

try:
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_repo import index_repo as _jcm_index_repo
    _JCM_AVAILABLE = True
except ImportError:
    _JCM_AVAILABLE = False

# ---------------------------------------------------------------------------
# Required imports — fail fast with actionable message
# ---------------------------------------------------------------------------
try:
    import tiktoken
except ImportError:
    sys.exit("tiktoken not found — run: pip install -r benchmarks/requirements-rag-bench.txt")

try:
    from langchain_community.vectorstores import FAISS
except ImportError:
    sys.exit(
        "langchain-community not found — run: pip install -r benchmarks/requirements-rag-bench.txt"
    )

try:
    from langchain_huggingface import HuggingFaceEmbeddings
except ImportError:
    try:
        from langchain_community.embeddings import HuggingFaceEmbeddings  # type: ignore[no-redef]
    except ImportError:
        sys.exit(
            "langchain-huggingface not found — run: pip install -r benchmarks/requirements-rag-bench.txt"
        )

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    try:
        from langchain.text_splitter import RecursiveCharacterTextSplitter  # type: ignore[no-redef]
    except ImportError:
        sys.exit(
            "langchain-text-splitters not found — run: pip install -r benchmarks/requirements-rag-bench.txt"
        )

try:
    from langchain_core.documents import Document
except ImportError:
    try:
        from langchain.schema import Document  # type: ignore[no-redef]
    except ImportError:
        sys.exit(
            "langchain-core not found — run: pip install -r benchmarks/requirements-rag-bench.txt"
        )

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TOKENIZER = "cl100k_base"
CHUNK_SIZES = [512, 1024, 2048]
CHUNK_OVERLAP_PCT = 0.10   # 10 % overlap
SEARCH_K = 5               # k= for similarity_search (matches jCodemunch max_results=5)
TOP_K_USED = 3             # top chunks used as "fetched" (matches jCodemunch SYMBOLS_FETCHED=3)
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

DEFAULT_REPOS = ["expressjs/express", "fastapi/fastapi", "gin-gonic/gin"]
TASKS_PATH = _BENCH_DIR / "tasks.json"

# ---------------------------------------------------------------------------
# jCodeMunch reference measurements
#
# Read from benchmarks/jcm_reference.json, written by run_benchmark.py
# --reference. NOT hardcoded: hand-typed constants go stale silently while the
# RAG side of every table is measured live in this run, and a ratio of a fresh
# numerator to a stale denominator moves on its own. Regenerate with:
#
#     python benchmarks/harness/run_benchmark.py --reference
#
# A repo the artifact does not cover gets no jCodeMunch column and no ratio.
# "Not measured" is a legitimate cell; an estimate is not.
# ---------------------------------------------------------------------------
_REFERENCE = load_reference()

# ---------------------------------------------------------------------------
# Tokenizer helpers
# ---------------------------------------------------------------------------
_enc = tiktoken.get_encoding(TOKENIZER)


def count_tokens(text: str) -> int:
    return len(_enc.encode(text))


def _serialize(obj: object) -> str:
    """Stable JSON serialization — same as run_benchmark.py."""
    return json.dumps(obj, separators=(",", ":"), default=str)


# ---------------------------------------------------------------------------
# Task corpus loader
# ---------------------------------------------------------------------------
def _load_tasks() -> tuple[list[str], list[str]]:
    if TASKS_PATH.exists():
        corpus = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
        repos = [r["id"] for r in corpus.get("repos", [])]
        queries = [t["query"] for t in corpus.get("tasks", [])]
        return repos, queries
    return DEFAULT_REPOS, [
        "router route handler", "middleware", "error exception",
        "request response", "param bind",
    ]


# ---------------------------------------------------------------------------
# File loading via jCodemunch IndexStore
# ---------------------------------------------------------------------------
def _parse_repo(repo_str: str) -> tuple[str, str]:
    parts = repo_str.split("/", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else ("local", parts[0])


def _resolve_repo(store: "IndexStore", owner_repo: str) -> Optional[dict]:
    """Find the repo entry in the jCodemunch index, tolerating display-name aliases."""
    owner, name = _parse_repo(owner_repo)
    for r in store.list_repos():
        if r["repo"] == owner_repo:
            return r
        if r.get("display_name", "") and f"{owner}/{r['display_name']}" == owner_repo:
            return r
    return None


def _ensure_indexed(owner_repo: str) -> bool:
    """
    Auto-index owner_repo via jCodemunch index_repo if it is not yet in the index.
    Returns True on success, False on failure.
    """
    if not _JCM_AVAILABLE:
        return False
    store = IndexStore()
    if _resolve_repo(store, owner_repo) is not None:
        print(f"  [index] {owner_repo} already in jCodemunch index", file=sys.stderr)
        return True
    print(f"  [index] {owner_repo} not indexed — running index_repo (fetches from GitHub) ...", file=sys.stderr)
    try:
        result = asyncio.run(_jcm_index_repo(owner_repo))
        if result.get("success"):
            fc = result.get("file_count", "?")
            print(f"  [index] done: {fc} files indexed", file=sys.stderr)
            return True
        print(f"  [index] FAILED: {result}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"  [index] exception: {exc}", file=sys.stderr)
        return False


def load_documents_from_index(owner_repo: str) -> tuple[list[Document], int]:
    """
    Load source-file content from the jCodemunch index — the same byte-for-byte
    content that run_benchmark.py uses for its baseline measurement.

    Returns (docs, file_count).  Raises RuntimeError if the repo is not indexed.
    """
    if not _JCM_AVAILABLE:
        raise RuntimeError("jcodemunch_mcp not importable — check sys.path / installation")

    store = IndexStore()
    matched = _resolve_repo(store, owner_repo)
    if matched is None:
        raise RuntimeError(f"Not indexed: {owner_repo}")

    actual_repo = matched["repo"]
    owner, name = _parse_repo(actual_repo)

    content_dir = store._content_dir(owner, name)
    index = store.load_index(owner, name)
    if index is None:
        raise RuntimeError(f"load_index returned None for {owner}/{name}")

    docs: list[Document] = []
    for rel_path in index.source_files:
        # IndexStore may use OS-specific separators in stored paths
        if sys.platform == "win32":
            abs_path = content_dir / Path(rel_path.replace("/", "\\"))
        else:
            abs_path = content_dir / rel_path
        if not abs_path.exists():
            abs_path = content_dir / rel_path  # fallback: try as-is
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            try:
                content = store.get_file_content_text(owner, name, rel_path) or ""
            except Exception:
                content = ""
        if content:
            docs.append(Document(
                page_content=content,
                metadata={"source": rel_path, "repo": owner_repo},
            ))

    print(
        f"  [load] {owner_repo}: {len(docs)} files from jCodemunch index "
        f"(index.source_files={len(index.source_files)})",
        file=sys.stderr,
    )
    return docs, len(index.source_files)


# ---------------------------------------------------------------------------
# RAG index builder
# ---------------------------------------------------------------------------
def build_rag_index(
    docs: list[Document],
    chunk_size: int,
    embeddings: HuggingFaceEmbeddings,
) -> tuple[FAISS, list[Document], float, float, int]:
    """
    Split documents, embed, and build a FAISS index.

    Returns:
        (faiss_index, all_chunks, embed_time_s, total_build_time_s, chunk_count)
    """
    overlap = max(1, int(chunk_size * CHUNK_OVERLAP_PCT))
    splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name=TOKENIZER,
        chunk_size=chunk_size,
        chunk_overlap=overlap,
    )
    chunks = splitter.split_documents(docs)

    t0 = time.perf_counter()
    index = FAISS.from_documents(chunks, embeddings)
    embed_time = time.perf_counter() - t0

    return index, chunks, embed_time, embed_time, len(chunks)


def get_faiss_index_size_bytes(index: FAISS) -> int:
    """Serialize the FAISS index to a temp dir and return total bytes, then clean up."""
    tmpdir = tempfile.mkdtemp(prefix="rag_bench_faiss_")
    try:
        index.save_local(tmpdir)
        total = sum(
            Path(tmpdir, fname).stat().st_size
            for fname in os.listdir(tmpdir)
            if Path(tmpdir, fname).is_file()
        )
        return total
    except Exception:
        return -1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Chunk integrity analysis (heuristic, language-agnostic)
# ---------------------------------------------------------------------------
_DEFINITION_STARTS = (
    "def ", "async def ", "class ", "function ", "async function ",
    "func ", "pub fn ", "fn ", "type ", "interface ", "struct ",
    "impl ", "const ", "enum ", "module ", "package ",
)

_LOGIC_KEYWORDS: frozenset[str] = frozenset({
    "if", "else", "elif", "for", "while", "return", "switch", "case",
    "try", "catch", "except", "raise", "throw", "yield", "def", "class",
    "function", "func", "lambda", "async", "await", "select",
})


def analyze_chunk_integrity(chunk: Document) -> dict:
    """
    Heuristic: does the chunk contain a complete code unit (function/class/block)
    or was it cut mid-definition?

    chunk_complete: True  → starts with a top-level definition and braces balance
    chunk_split:    True  → strong evidence of truncation (imbalanced braces or
                            ends inside an indented Python block mid-statement)
    """
    content = chunk.page_content
    stripped = content.strip()
    lines = stripped.splitlines()

    starts_with_def = any(stripped.startswith(kw) for kw in _DEFINITION_STARTS)

    open_b = content.count("{")
    close_b = content.count("}")
    open_p = content.count("(")
    close_p = content.count(")")

    brace_imbalance = abs(open_b - close_b)
    paren_imbalance = abs(open_p - close_p)

    # Python-specific: last line is inside an indented block without a natural close
    last_line = lines[-1] if lines else ""
    py_truncated = (
        last_line.startswith(("    ", "\t"))
        and not stripped.endswith((":", "pass", "return", "}", ")", "]", ";", '"""', "'''"))
    )

    chunk_split = brace_imbalance > 2 or paren_imbalance > 3 or py_truncated
    chunk_complete = starts_with_def and not chunk_split

    return {
        "chunk_complete": chunk_complete,
        "chunk_split": chunk_split,
        "chunk_lines": len(lines),
        "starts_with_def": starts_with_def,
        "brace_imbalance": brace_imbalance,
        "paren_imbalance": paren_imbalance,
    }


# ---------------------------------------------------------------------------
# Retrieval precision analysis (heuristic)
# ---------------------------------------------------------------------------
def analyze_retrieval_precision(chunk: Document, query: str) -> dict:
    """
    Heuristic relevance assessment for a retrieved chunk.

    contains_query_terms: majority of query words appear in the chunk
    contains_logic:       chunk has >= 2 distinct control-flow / definition keywords
    """
    content_lower = chunk.page_content.lower()
    query_terms = query.lower().split()

    hits = sum(1 for t in query_terms if t in content_lower)
    contains_query_terms = hits >= max(1, len(query_terms) // 2)

    words = set(content_lower.split())
    logic_hits = len(words & _LOGIC_KEYWORDS)
    contains_logic = logic_hits >= 2

    return {
        "contains_query_terms": contains_query_terms,
        "contains_logic": contains_logic,
        "query_term_hits": hits,
        "logic_keyword_count": logic_hits,
    }


# ---------------------------------------------------------------------------
# Per-query RAG measurement
# ---------------------------------------------------------------------------
def measure_rag_query(
    index: FAISS,
    query: str,
    baseline_tokens: int,
) -> dict:
    """
    Run one RAG query and return token counts + quality metrics.

    Token accounting (mirrors run_benchmark.py structure):
      search_tokens  = tiktoken count of all k=5 retrieved chunks serialized as JSON
      fetch_tokens   = tiktoken count of the top-3 chunks serialized as JSON
      total          = search_tokens + fetch_tokens

    This parallels jCodemunch's:
      search_tokens  = search_symbols JSON response (5 results)
      fetch_tokens   = get_symbol × 3 (full source for top 3 hits)
    """
    t_query = time.perf_counter()
    try:
        retrieved = index.similarity_search_with_score(query, k=SEARCH_K)
    except Exception as exc:
        return {"error": str(exc)}
    query_ms = round((time.perf_counter() - t_query) * 1000, 1)

    # retrieved: list of (Document, score) — already sorted best-first
    search_chunks = retrieved[:SEARCH_K]
    fetch_chunks = retrieved[:TOP_K_USED]

    # Serialize as JSON objects (mimics API response structure)
    search_objs = [
        {
            "file": doc.metadata.get("source", "?"),
            "content": doc.page_content,
            "score": float(score),
        }
        for doc, score in search_chunks
    ]
    fetch_objs = [
        {
            "file": doc.metadata.get("source", "?"),
            "content": doc.page_content,
        }
        for doc, score in fetch_chunks
    ]

    search_tokens = count_tokens(_serialize(search_objs))
    fetch_tokens = count_tokens(_serialize(fetch_objs))
    total_tokens = search_tokens + fetch_tokens

    reduction_pct = round((1 - total_tokens / baseline_tokens) * 100, 1) if baseline_tokens > 0 else 0.0
    ratio = round(baseline_tokens / total_tokens, 1) if total_tokens > 0 else float("inf")

    # Per-chunk quality analysis (top 3 only)
    chunk_analyses = []
    for doc, score in fetch_chunks:
        integrity = analyze_chunk_integrity(doc)
        precision = analyze_retrieval_precision(doc, query)
        chunk_analyses.append({
            "file": doc.metadata.get("source", "?"),
            "score": float(score),
            **integrity,
            **precision,
        })

    return {
        "tokens": total_tokens,
        "search_tokens": search_tokens,
        "fetch_tokens": fetch_tokens,
        "baseline_tokens": baseline_tokens,
        "reduction_pct": reduction_pct,
        "ratio": ratio,
        "query_ms": query_ms,
        "chunks_complete": sum(1 for a in chunk_analyses if a["chunk_complete"]),
        "chunks_split": sum(1 for a in chunk_analyses if a["chunk_split"]),
        "chunks_with_terms": sum(1 for a in chunk_analyses if a["contains_query_terms"]),
        "chunks_with_logic": sum(1 for a in chunk_analyses if a["contains_logic"]),
        "chunk_analyses": chunk_analyses,
    }


# ---------------------------------------------------------------------------
# Per-repo benchmark (all chunk sizes)
# ---------------------------------------------------------------------------
def benchmark_repo(
    owner_repo: str,
    embeddings: HuggingFaceEmbeddings,
    queries: list[str],
) -> dict:
    """Load repo from jCodemunch index, compute baseline, build RAG indexes, run all queries."""
    # Ensure the repo is indexed (auto-index if needed)
    if not _ensure_indexed(owner_repo):
        return {"repo": owner_repo, "error": f"Could not index {owner_repo}"}

    print(f"  loading source files from jCodemunch index ...", file=sys.stderr)
    try:
        docs, file_count = load_documents_from_index(owner_repo)
    except RuntimeError as exc:
        return {"repo": owner_repo, "error": str(exc)}

    if not docs:
        return {"repo": owner_repo, "error": "No content loaded from index"}

    # Baseline: identical computation to run_benchmark.py measure_baseline()
    baseline_tokens = sum(count_tokens(d.page_content) for d in docs)
    print(f"  baseline: {file_count} files, {baseline_tokens:,} tokens", file=sys.stderr)

    results_by_chunk: dict[int, dict] = {}

    for chunk_size in CHUNK_SIZES:
        overlap = max(1, int(chunk_size * CHUNK_OVERLAP_PCT))
        print(
            f"  [chunk={chunk_size}, overlap={overlap}] building FAISS index ...",
            file=sys.stderr,
            end=" ",
            flush=True,
        )
        t_build = time.perf_counter()
        try:
            index, chunks, embed_time, _, chunk_count = build_rag_index(docs, chunk_size, embeddings)
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            results_by_chunk[chunk_size] = {"error": str(exc)}
            continue
        build_time = round(time.perf_counter() - t_build, 2)
        print(f"{chunk_count} chunks, {round(embed_time, 1)}s embed, {build_time}s total", file=sys.stderr)

        faiss_bytes = get_faiss_index_size_bytes(index)

        task_rows = []
        for query in queries:
            row = measure_rag_query(index, query, baseline_tokens)
            row["query"] = query
            task_rows.append(row)
            status = "ok" if "error" not in row else f"ERR:{row['error'][:60]}"
            print(f"    [{chunk_size}] '{query}' → {row.get('tokens', '?'):,} tokens  ({status})", file=sys.stderr)

        results_by_chunk[chunk_size] = {
            "chunk_size": chunk_size,
            "chunk_overlap": overlap,
            "chunk_count": chunk_count,
            "embed_time_s": round(embed_time, 2),
            "build_time_s": build_time,
            "faiss_size_bytes": faiss_bytes,
            "tasks": task_rows,
        }

    return {
        "repo": owner_repo,
        "file_count": file_count,
        "baseline_tokens": baseline_tokens,
        "chunk_sizes": results_by_chunk,
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------
def _fmt_ratio(r: float) -> str:
    return "∞" if r == float("inf") else f"{r:.1f}x"


# NOTE: there is deliberately no estimator here. A previous revision allocated
# jCodeMunch's per-repo cost proportionally to repo size when the repo was not
# in the reference set. That number was never measured, and its premise — that
# retrieval cost scales with corpus size — is the opposite of what this project
# claims, so the estimate was biased in a direction nobody could see. A repo
# absent from the reference artifact now renders "not measured".


def render_markdown(all_results: list[dict]) -> str:
    lines: list[str] = []

    lines += [
        "# LangChain RAG Baseline Benchmark",
        "",
        f"**Tokenizer:** `{TOKENIZER}` (tiktoken)  ",
        f"**Embeddings:** `{EMBED_MODEL}` (sentence-transformers)  ",
        "**Vector store:** FAISS (faiss-cpu, in-memory)  ",
        f"**Retrieval:** similarity search, k={SEARCH_K}, top {TOP_K_USED} used  ",
        f"**Chunk sizes tested:** {', '.join(str(s) for s in CHUNK_SIZES)} tokens, ~10% overlap  ",
        "",
    ]

    # -----------------------------------------------------------------------
    # Per-repo section
    # -----------------------------------------------------------------------
    for res in all_results:
        repo = res["repo"]
        lines.append(f"## {repo}")
        lines.append("")

        if "error" in res:
            lines.append(f"> **ERROR:** {res['error']}")
            lines.append("")
            continue

        baseline = res["baseline_tokens"]
        file_count = res["file_count"]

        lines += [
            "| Metric | Value |",
            "|--------|-------|",
            f"| Files indexed | **{file_count:,}** |",
            f"| Baseline tokens (all files) | **{baseline:,}** |",
        ]
        for cs in CHUNK_SIZES:
            csr = res["chunk_sizes"].get(cs, {})
            if "error" not in csr:
                lines.append(f"| Chunks (size {cs}) | {csr.get('chunk_count', '?'):,} |")
        lines.append("")

        # Per-chunk-size tables
        for cs in CHUNK_SIZES:
            csr = res["chunk_sizes"].get(cs, {})
            lines.append(f"### Chunk size: {cs} tokens")
            lines.append("")

            if "error" in csr:
                lines.append(f"> **ERROR:** {csr['error']}")
                lines.append("")
                continue

            sz_kb = f"{csr['faiss_size_bytes'] // 1024:,} KB" if csr["faiss_size_bytes"] >= 0 else "n/a"
            lines.append(
                f"*{csr['chunk_count']:,} chunks | "
                f"embed {csr['embed_time_s']}s | "
                f"total build {csr['build_time_s']}s | "
                f"FAISS {sz_kb}*"
            )
            lines.append("")

            lines += [
                "| Query | RAG tokens | Baseline | Reduction | Ratio | Complete/3 | Split/3 |",
                "|-------|----------:|----------:|---------:|------:|:---------:|:-------:|",
            ]

            valid = [t for t in csr["tasks"] if "error" not in t]
            for t in valid:
                lines.append(
                    f"| `{t['query']}` "
                    f"| {t['tokens']:,} "
                    f"| {t['baseline_tokens']:,} "
                    f"| {t['reduction_pct']}% "
                    f"| {_fmt_ratio(t['ratio'])} "
                    f"| {t['chunks_complete']}/{TOP_K_USED} "
                    f"| {t['chunks_split']}/{TOP_K_USED} |"
                )

            if valid:
                avg_red = sum(t["reduction_pct"] for t in valid) / len(valid)
                avg_rat = sum(t["ratio"] for t in valid) / len(valid)
                avg_comp = sum(t["chunks_complete"] for t in valid) / len(valid)
                avg_split = sum(t["chunks_split"] for t in valid) / len(valid)
                lines.append(
                    f"| **Average** | — | — "
                    f"| **{avg_red:.1f}%** "
                    f"| **{avg_rat:.1f}x** "
                    f"| **{avg_comp:.1f}** "
                    f"| **{avg_split:.1f}** |"
                )
            lines.append("")

            # Detail table (collapsible)
            lines.append("<details><summary>Retrieval quality detail (search + fetch tokens, latency)</summary>")
            lines.append("")
            lines += [
                "| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | With&nbsp;terms/3 | With&nbsp;logic/3 | Query&nbsp;ms |",
                "|-------|-----------------:|------------------:|:-:|:-:|------:|",
            ]
            for t in valid:
                lines.append(
                    f"| `{t['query']}` "
                    f"| {t['search_tokens']:,} "
                    f"| {t['fetch_tokens']:,} "
                    f"| {t['chunks_with_terms']}/{TOP_K_USED} "
                    f"| {t['chunks_with_logic']}/{TOP_K_USED} "
                    f"| {t['query_ms']} |"
                )
            lines += ["", "</details>", ""]

        lines += ["---", ""]

    # -----------------------------------------------------------------------
    # Combined comparison table
    # -----------------------------------------------------------------------
    if _REFERENCE:
        provenance = (
            "jCodemunch per-repo figures are read from `benchmarks/jcm_reference.json` "
            f"(run {_REFERENCE.get('captured_at', 'unknown')}, "
            f"v{_REFERENCE.get('jcodemunch_version', 'unknown')}; grand summary: baseline "
            f"{_REFERENCE['grand']['baseline_tokens']:,}, jMunch "
            f"{_REFERENCE['grand']['jmunch_tokens']:,}, "
            f"{_REFERENCE['grand']['task_runs']} task-runs). "
            "Regenerate with `python benchmarks/harness/run_benchmark.py --reference`."
        )
    else:
        provenance = (
            "**No jCodemunch reference artifact found** — the jCodemunch columns are "
            "omitted rather than estimated. Run "
            "`python benchmarks/harness/run_benchmark.py --reference` first."
        )

    lines += [
        "## Combined Comparison",
        "",
        "Average RAG tokens per query (mean of 5 queries), compared to jCodemunch.",
        provenance,
        "",
        "| Repo | Baseline | RAG-512 | RAG-1024 | RAG-2048 | jCodemunch | Best-RAG-ratio | jCodemunch-ratio | Winner |",
        "|------|--------:|---------:|---------:|---------:|-----------:|--------------:|-----------------:|--------|",
    ]

    cross_run_notes: list[str] = []

    for res in all_results:
        repo = res["repo"]
        if "error" in res:
            lines.append(f"| {repo} | — | — | — | — | — | — | — | — |")
            continue

        baseline = res["baseline_tokens"]
        entry = reference_entry(_REFERENCE, repo)
        jm_avg = entry["avg_tokens_per_query"] if entry else 0
        drift = reference_drift(entry, res.get("file_count", 0), baseline)
        if drift:
            cross_run_notes.append(f"- `{repo}`: {drift}")
        jm_ratio = round(baseline / jm_avg, 1) if jm_avg > 0 else float("inf")

        rag_avgs: dict[int, float] = {}
        best_rag_avg = None
        best_ratio = 0.0
        for cs in CHUNK_SIZES:
            csr = res["chunk_sizes"].get(cs, {})
            if "error" not in csr:
                valid = [t for t in csr["tasks"] if "error" not in t]
                if valid:
                    avg = sum(t["tokens"] for t in valid) / len(valid)
                    rag_avgs[cs] = avg
                    r = baseline / avg if avg > 0 else 0.0
                    if r > best_ratio:
                        best_ratio = r
                        best_rag_avg = avg

        def _col(cs: int) -> str:
            v = rag_avgs.get(cs)
            return f"{v:,.0f}" if v is not None else "—"

        if jm_avg > 0:
            marker = "*" if drift else ""
            jm_col = f"{jm_avg:,}{marker}"
            jm_ratio_col = f"{_fmt_ratio(jm_ratio)}{marker}"
        else:
            jm_col = "not measured"
            jm_ratio_col = "—"

        if best_rag_avg is not None and jm_avg > 0:
            if jm_avg < best_rag_avg:
                winner = f"jCodemunch ({round(best_rag_avg / jm_avg, 1)}×)"
            else:
                # Name the chunk size that actually won and state its margin the
                # same way round. The old code labelled every RAG win "RAG-512"
                # and printed a sub-1.0 margin.
                best_cs = min(rag_avgs, key=lambda c: rag_avgs[c]) if rag_avgs else None
                label = f"RAG-{best_cs}" if best_cs else "RAG"
                winner = f"{label} ({round(jm_avg / best_rag_avg, 1)}×)"
        else:
            winner = "—"

        lines.append(
            f"| {repo} "
            f"| {baseline:,} "
            f"| {_col(512)} "
            f"| {_col(1024)} "
            f"| {_col(2048)} "
            f"| {jm_col} "
            f"| {_fmt_ratio(best_ratio)} "
            f"| {jm_ratio_col} "
            f"| {winner} |"
        )

    if cross_run_notes:
        lines += [
            "",
            "\\* **Cross-run comparison.** The RAG columns were measured against the index "
            "state in this run; the marked jCodemunch figures were measured against a "
            "different one, so the ratio mixes two corpora. Re-run "
            "`run_benchmark.py --reference` to bring both sides onto the same index.",
            "",
        ] + cross_run_notes

    lines += [""]

    # -----------------------------------------------------------------------
    # Infrastructure overhead
    # -----------------------------------------------------------------------
    lines += [
        "## Infrastructure Overhead",
        "",
        "| Repo | Chunk size | Chunks | Embed time | FAISS size |",
        "|------|:----------:|-------:|-----------:|-----------:|",
    ]
    for res in all_results:
        if "error" in res:
            continue
        for cs in CHUNK_SIZES:
            csr = res["chunk_sizes"].get(cs, {})
            if "error" not in csr:
                sz = f"{csr['faiss_size_bytes'] // 1024:,} KB" if csr["faiss_size_bytes"] >= 0 else "n/a"
                lines.append(
                    f"| {res['repo']} | {cs} "
                    f"| {csr['chunk_count']:,} "
                    f"| {csr['embed_time_s']}s "
                    f"| {sz} |"
                )
    lines.append("")

    # -----------------------------------------------------------------------
    # Chunk integrity summary
    # -----------------------------------------------------------------------
    lines += [
        "## Chunk Integrity Summary",
        "",
        "Percentage of retrieved top-3 chunks that are complete code units vs. split mid-function.",
        "(Heuristic: complete = starts with def/class/function/func and braces balance; "
        "split = brace imbalance > 2 or ends mid-indented-block.)",
        "",
        "| Repo | Chunk size | Complete % | Split % |",
        "|------|:----------:|-----------:|--------:|",
    ]
    for res in all_results:
        if "error" in res:
            continue
        for cs in CHUNK_SIZES:
            csr = res["chunk_sizes"].get(cs, {})
            if "error" not in csr:
                valid = [t for t in csr["tasks"] if "error" not in t]
                if valid:
                    total = len(valid) * TOP_K_USED
                    n_complete = sum(t["chunks_complete"] for t in valid)
                    n_split = sum(t["chunks_split"] for t in valid)
                    lines.append(
                        f"| {res['repo']} | {cs} "
                        f"| {100 * n_complete / total:.0f}% "
                        f"| {100 * n_split / total:.0f}% |"
                    )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repos", nargs="*",
        help="owner/repo to benchmark (default: all 3 canonical repos)",
    )
    parser.add_argument(
        "--out", metavar="FILE",
        default=str(_BENCH_DIR / "rag_baseline_results.md"),
        help="write markdown results to FILE",
    )
    parser.add_argument(
        "--json", metavar="FILE", dest="json_out",
        default=str(_BENCH_DIR / "rag_baseline_results.json"),
        help="write raw JSON results to FILE",
    )
    parser.add_argument(
        "--embed-model", default=EMBED_MODEL,
        help="HuggingFace sentence-transformers model name",
    )
    args = parser.parse_args()

    _, queries = _load_tasks()
    repos = args.repos or DEFAULT_REPOS

    # Print configuration header to stderr
    print("=" * 60, file=sys.stderr)
    print("LangChain RAG Baseline Benchmark", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  Tokenizer:     {TOKENIZER}", file=sys.stderr)
    print(f"  Embeddings:    {args.embed_model}", file=sys.stderr)
    print(f"  Chunk sizes:   {CHUNK_SIZES} tokens", file=sys.stderr)
    print(f"  Overlap:       {int(CHUNK_OVERLAP_PCT * 100)}%", file=sys.stderr)
    print(f"  Search k:      {SEARCH_K}", file=sys.stderr)
    print(f"  Top-k used:    {TOP_K_USED}", file=sys.stderr)
    print(f"  Repos:         {', '.join(repos)}", file=sys.stderr)
    print(f"  Queries:       {len(queries)}", file=sys.stderr)
    print(f"  Total runs:    {len(queries) * len(repos) * len(CHUNK_SIZES)}", file=sys.stderr)
    print(f"  File source:   jCodemunch IndexStore (auto-indexed if needed)", file=sys.stderr)
    print(f"  Output:        {args.out}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(file=sys.stderr)

    # Load embeddings once — shared across all repos and chunk sizes
    print(f"Loading embedding model: {args.embed_model}", file=sys.stderr)
    print("(First run will download ~90 MB model; subsequent runs use cache)", file=sys.stderr)
    try:
        embeddings = HuggingFaceEmbeddings(
            model_name=args.embed_model,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
    except Exception as exc:
        sys.exit(f"Failed to load embedding model '{args.embed_model}': {exc}")
    print("Embedding model ready.", file=sys.stderr)
    print(file=sys.stderr)

    all_results: list[dict] = []
    for repo in repos:
        print(f"{'=' * 40}", file=sys.stderr)
        print(f"Benchmarking: {repo}", file=sys.stderr)
        print(f"{'=' * 40}", file=sys.stderr)
        t0 = time.perf_counter()
        try:
            res = benchmark_repo(repo, embeddings, queries)
        except Exception as exc:
            res = {"repo": repo, "error": str(exc)}
        elapsed = round(time.perf_counter() - t0, 1)

        if "error" in res:
            print(f"  FAILED in {elapsed}s: {res['error']}", file=sys.stderr)
        else:
            print(f"  Completed in {elapsed}s", file=sys.stderr)
        all_results.append(res)
        print(file=sys.stderr)

    # Render and write outputs
    md = render_markdown(all_results)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"Markdown written: {out_path}", file=sys.stderr)

    json_path = Path(args.json_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(all_results, indent=2, default=str), encoding="utf-8")
    print(f"JSON written:     {json_path}", file=sys.stderr)
    print(file=sys.stderr)

    # Print markdown to stdout (can be piped/redirected)
    print(md)


if __name__ == "__main__":
    main()
