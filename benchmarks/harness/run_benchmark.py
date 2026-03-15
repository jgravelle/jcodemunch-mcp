#!/usr/bin/env python3
"""jcodemunch-mcp benchmark harness.

Measures real (tiktoken cl100k_base) token counts for the jcodemunch
retrieval workflow vs an "open every file" baseline on identical tasks.

Usage:
    python benchmarks/harness/run_benchmark.py [owner/repo ...]
    python benchmarks/harness/run_benchmark.py --out results.md

If no repos are given, runs against all three canonical benchmark repos:
    expressjs/express  |  fastapi/fastapi  |  gin-gonic/gin

Those repos must already be indexed (jcodemunch index_repo owner/repo).

Methodology
-----------
Baseline:   concatenate all raw source files stored in the index, count tokens.
            This is the minimum tokens a "read everything first" agent would use.

jMunch:     for each task query —
              1. call search_symbols (max_results=5)     → count JSON response tokens
              2. call get_symbol for the top 3 hits      → count each source snippet
            Total = search response + 3 × symbol source.

Tokenizer:  tiktoken cl100k_base (GPT-4 / Claude family ballpark; consistent
            across runs regardless of model).

Output:     per-task rows + per-repo summary + grand summary table (markdown).
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Bootstrap: add src/ to path so we can import jcodemunch_mcp directly
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

try:
    import tiktoken
except ImportError:
    sys.exit("tiktoken not found — run: pip install tiktoken")

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.search_symbols import search_symbols
from jcodemunch_mcp.tools.get_symbol import get_symbol

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Task corpus — loaded from benchmarks/tasks.json if present, else hardcoded
# ---------------------------------------------------------------------------

_CORPUS_PATH = _REPO_ROOT / "benchmarks" / "tasks.json"

def _load_corpus():
    if _CORPUS_PATH.exists():
        import json as _json
        corpus = _json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
        repos = [r["id"] for r in corpus.get("repos", [])]
        tasks = [t["query"] for t in corpus.get("tasks", [])]
        return repos, tasks
    # Fallback hardcoded values (kept in sync with tasks.json)
    return (
        ["expressjs/express", "fastapi/fastapi", "gin-gonic/gin"],
        ["router route handler", "middleware", "error exception", "request response", "context bind"],
    )

DEFAULT_REPOS, TASKS = _load_corpus()

SEARCH_MAX_RESULTS = 5
SYMBOLS_FETCHED = 3        # get_symbol calls per query in the jMunch workflow

TOKENIZER = "cl100k_base"  # tiktoken encoding name

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_enc = tiktoken.get_encoding(TOKENIZER)


def count_tokens(text: str) -> int:
    return len(_enc.encode(text))


def _serialize(obj) -> str:
    """Stable JSON serialization of a tool response."""
    return json.dumps(obj, separators=(",", ":"), default=str)


def _parse_repo(repo_str: str) -> tuple[str, str]:
    """Split 'owner/repo' → (owner, repo). Handles 'local/name' too."""
    parts = repo_str.split("/", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "local", parts[0]


# ---------------------------------------------------------------------------
# Baseline measurement
# ---------------------------------------------------------------------------

def measure_baseline(store: IndexStore, owner: str, name: str) -> dict:
    """Count tokens across ALL raw source files stored in the index."""
    content_dir = store._content_dir(owner, name)
    index = store.load_index(owner, name)
    if index is None:
        return {"error": f"Not indexed: {owner}/{name}"}

    total_tokens = 0
    file_count = 0
    for rel_path in index.source_files:
        abs_path = content_dir / Path(rel_path.replace("/", "\\") if sys.platform == "win32" else rel_path)
        # Try both path separator forms
        if not abs_path.exists():
            abs_path = content_dir / rel_path
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            try:
                content = store.get_file_content_text(owner, name, rel_path) or ""
            except Exception:
                content = ""
        total_tokens += count_tokens(content)
        file_count += 1

    return {"tokens": total_tokens, "files": file_count}


# ---------------------------------------------------------------------------
# jMunch workflow measurement
# ---------------------------------------------------------------------------

def measure_jmunch(repo_str: str, query: str) -> dict:
    """
    jMunch workflow for one query:
      1. search_symbols → serialize response → count tokens
      2. get_symbol for top N hits → count tokens
    """
    # 1. Search
    t0 = time.perf_counter()
    search_result = search_symbols(repo=repo_str, query=query, max_results=SEARCH_MAX_RESULTS)
    search_ms = (time.perf_counter() - t0) * 1000

    search_text = _serialize(search_result)
    search_tokens = count_tokens(search_text)

    # Extract symbol IDs from results
    symbols = search_result.get("results") or search_result.get("symbols") or []
    symbol_ids = [s.get("id") or s.get("symbol_id") for s in symbols if s.get("id") or s.get("symbol_id")]
    symbol_ids = symbol_ids[:SYMBOLS_FETCHED]

    # 2. Get symbol sources
    fetch_tokens = 0
    for sid in symbol_ids:
        sym_result = get_symbol(repo=repo_str, symbol_id=sid)
        fetch_tokens += count_tokens(_serialize(sym_result))

    total = search_tokens + fetch_tokens
    return {
        "tokens": total,
        "search_tokens": search_tokens,
        "fetch_tokens": fetch_tokens,
        "hits_fetched": len(symbol_ids),
        "search_ms": round(search_ms, 1),
    }


# ---------------------------------------------------------------------------
# Per-repo benchmark
# ---------------------------------------------------------------------------

def benchmark_repo(repo_str: str) -> dict:
    store = IndexStore()
    owner, name = _parse_repo(repo_str)

    # Resolve: might be stored as local/name with hash suffix
    all_repos = store.list_repos()
    matched = None
    for r in all_repos:
        if r["repo"] == repo_str:
            matched = r
            break
    # Fallback: try display_name match
    if matched is None:
        for r in all_repos:
            display = r.get("display_name", "")
            if display and f"{owner}/{display}" == repo_str:
                matched = r
                break

    if matched is None:
        return {"error": f"Not indexed: {repo_str}. Run: jcodemunch index_repo {repo_str}"}

    actual_repo = matched["repo"]
    owner2, name2 = _parse_repo(actual_repo)

    # Baseline (compute once for the repo)
    baseline = measure_baseline(store, owner2, name2)
    if "error" in baseline:
        return baseline

    baseline_tokens = baseline["tokens"]
    file_count = baseline["files"]
    symbol_count = matched.get("symbol_count", 0)

    task_rows = []
    for query in TASKS:
        jm = measure_jmunch(actual_repo, query)
        if "error" in jm:
            task_rows.append({"query": query, "error": jm["error"]})
            continue
        reduction_pct = (1 - jm["tokens"] / baseline_tokens) * 100 if baseline_tokens > 0 else 0
        ratio = baseline_tokens / jm["tokens"] if jm["tokens"] > 0 else float("inf")
        task_rows.append({
            "query": query,
            "baseline_tokens": baseline_tokens,
            "jmunch_tokens": jm["tokens"],
            "reduction_pct": round(reduction_pct, 1),
            "ratio": round(ratio, 1),
            "search_tokens": jm["search_tokens"],
            "fetch_tokens": jm["fetch_tokens"],
            "hits_fetched": jm["hits_fetched"],
            "search_ms": jm["search_ms"],
        })

    return {
        "repo": repo_str,
        "display": matched.get("display_name", name2),
        "file_count": file_count,
        "symbol_count": symbol_count,
        "baseline_tokens": baseline_tokens,
        "tasks": task_rows,
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_markdown(results: list[dict], tokenizer: str) -> str:
    lines = []
    lines.append("# jcodemunch-mcp -- Token Efficiency Benchmark")
    lines.append("")
    lines.append(f"**Tokenizer:** `{tokenizer}` (tiktoken)  ")
    lines.append(f"**Workflow:** `search_symbols` (top {SEARCH_MAX_RESULTS}) + `get_symbol` x {SYMBOLS_FETCHED}  ")
    lines.append(f"**Baseline:** all source files concatenated (minimum for \"open every file\" agent)  ")
    lines.append("")

    grand_baseline = 0
    grand_jmunch = 0
    grand_tasks = 0

    for res in results:
        if "error" in res:
            lines.append(f"## {res.get('repo', '?')} — ERROR")
            lines.append(f"> {res['error']}")
            lines.append("")
            continue

        repo = res["repo"]
        lines.append(f"## {repo}")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Files indexed | **{res['file_count']:,}** |")
        lines.append(f"| Symbols extracted | **{res['symbol_count']:,}** |")
        lines.append(f"| Baseline tokens (all files) | **{res['baseline_tokens']:,}** |")
        lines.append("")

        lines.append("| Query | Baseline&nbsp;tokens | jMunch&nbsp;tokens | Reduction | Ratio |")
        lines.append("|-------|---------------------:|-------------------:|----------:|------:|")

        repo_jmunch_sum = 0
        valid_tasks = [t for t in res["tasks"] if "error" not in t]
        for t in valid_tasks:
            lines.append(
                f"| `{t['query']}` "
                f"| {t['baseline_tokens']:,} "
                f"| {t['jmunch_tokens']:,} "
                f"| **{t['reduction_pct']}%** "
                f"| {t['ratio']}x |"
            )
            repo_jmunch_sum += t["jmunch_tokens"]
            grand_jmunch += t["jmunch_tokens"]
            grand_baseline += t["baseline_tokens"]
            grand_tasks += 1

        if valid_tasks:
            avg_reduction = sum(t["reduction_pct"] for t in valid_tasks) / len(valid_tasks)
            avg_ratio = sum(t["ratio"] for t in valid_tasks) / len(valid_tasks)
            lines.append(
                f"| **Average** | — | — "
                f"| **{avg_reduction:.1f}%** "
                f"| **{avg_ratio:.1f}x** |"
            )
        lines.append("")

        # Detail table
        lines.append("<details><summary>Query detail (search + fetch tokens, latency)</summary>")
        lines.append("")
        lines.append("| Query | Search&nbsp;tokens | Fetch&nbsp;tokens | Hits&nbsp;fetched | Search&nbsp;ms |")
        lines.append("|-------|-----------------:|------------------:|------------------:|---------------:|")
        for t in valid_tasks:
            lines.append(
                f"| `{t['query']}` "
                f"| {t['search_tokens']:,} "
                f"| {t['fetch_tokens']:,} "
                f"| {t['hits_fetched']} "
                f"| {t['search_ms']} |"
            )
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # Grand summary
    if grand_tasks > 0:
        grand_reduction = (1 - grand_jmunch / grand_baseline) * 100
        grand_ratio = grand_baseline / grand_jmunch
        lines.append("---")
        lines.append("")
        lines.append("## Grand Summary")
        lines.append("")
        lines.append("| | Tokens |")
        lines.append("|--|-------:|")
        lines.append(f"| Baseline total ({grand_tasks} task-runs) | {grand_baseline:,} |")
        lines.append(f"| jMunch total | {grand_jmunch:,} |")
        lines.append(f"| **Reduction** | **{grand_reduction:.1f}%** |")
        lines.append(f"| **Ratio** | **{grand_ratio:.1f}x** |")
        lines.append("")
        lines.append(
            f"> Measured with tiktoken `{tokenizer}`. "
            "Baseline = all indexed source files. "
            f"jMunch = search_symbols (top {SEARCH_MAX_RESULTS}) + "
            f"get_symbol x {SYMBOLS_FETCHED} per query."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repos", nargs="*", help="owner/repo to benchmark (default: all 3 canonical repos)")
    parser.add_argument("--out", metavar="FILE", help="write markdown results to FILE")
    parser.add_argument("--json", metavar="FILE", dest="json_out", help="write raw JSON results to FILE")
    args = parser.parse_args()

    repos = args.repos or DEFAULT_REPOS

    print(f"jcodemunch-mcp benchmark harness  |  tokenizer: {TOKENIZER}", flush=True)
    print(f"Repos: {', '.join(repos)}", flush=True)
    print(f"Tasks: {len(TASKS)} queries × {len(repos)} repos = {len(TASKS) * len(repos)} measurements", flush=True)
    print()

    results = []
    for repo in repos:
        print(f"  benchmarking {repo} ...", end=" ", flush=True)
        t0 = time.perf_counter()
        res = benchmark_repo(repo)
        elapsed = time.perf_counter() - t0
        if "error" in res:
            print(f"ERROR: {res['error']}")
        else:
            valid = [t for t in res["tasks"] if "error" not in t]
            avg_r = sum(t["reduction_pct"] for t in valid) / len(valid) if valid else 0
            print(f"done ({elapsed:.1f}s)  avg reduction {avg_r:.1f}%")
        results.append(res)

    print()
    md = render_markdown(results, TOKENIZER)
    print(md)

    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"\nResults written to: {args.out}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print(f"JSON written to: {args.json_out}")


if __name__ == "__main__":
    main()
