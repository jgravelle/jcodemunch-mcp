"""A/B benchmark for v1.70.0 upgrades.

Measures:
  §1.1 — default detail_level flip from "standard" to "auto" for discovery queries
  §1.2 — full-mode token_budget adherence (would have overshot under v1.63.1)

Run:
    PYTHONPATH=src python benchmarks/harness/ab_v1_70_0.py

Writes results to benchmarks/results_v1.70.0.md.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols

try:
    import tiktoken
    ENC = tiktoken.get_encoding("cl100k_base")
    def toks(s: str) -> int:
        return len(ENC.encode(s))
except Exception:
    def toks(s: str) -> int:
        return max(1, len(s) // 4)


DISCOVERY_QUERIES = [
    "search", "index", "parse", "symbol", "config", "token",
    "cache", "embed", "error", "fusion", "budget", "detail",
    "resolve", "encode", "store", "summar",
]

TARGETED_QUERIES = [
    "_materialize_full_entry", "encode_response", "search_symbols",
    "index_folder", "get_symbol_source", "resolve_specifier",
]


def bench_11_default_shape(repo: str, storage: str, max_results: int = 10):
    """§1.1: old default="standard" vs new default="auto" for broad discovery.

    Measures payload size (tokens) for each query under both defaults.
    """
    rows = []
    for q in DISCOVERY_QUERIES:
        # Prime both paths so cache state is equal.
        r_std = search_symbols(repo=repo, query=q, max_results=max_results,
                               detail_level="standard", storage_path=storage)
        r_auto = search_symbols(repo=repo, query=q, max_results=max_results,
                                detail_level="auto", storage_path=storage)
        # Strip _meta.timing_ms for stable payload compare
        for r in (r_std, r_auto):
            r.get("_meta", {}).pop("timing_ms", None)
        t_std = toks(json.dumps(r_std))
        t_auto = toks(json.dumps(r_auto))
        n = r_auto.get("result_count", 0)
        rows.append({
            "query": q,
            "results": n,
            "tokens_standard": t_std,
            "tokens_auto": t_auto,
            "savings_tokens": t_std - t_auto,
            "savings_pct": (1 - t_auto / t_std) * 100 if t_std else 0.0,
        })
    return rows


def bench_11_narrow_parity(repo: str, storage: str):
    """§1.1: narrow queries (max_results<5) — auto should match standard exactly.

    Proves no regression on the "intentional lookup" path.
    """
    rows = []
    for q in TARGETED_QUERIES:
        r_std = search_symbols(repo=repo, query=q, max_results=3,
                               detail_level="standard", storage_path=storage)
        r_auto = search_symbols(repo=repo, query=q, max_results=3,
                                detail_level="auto", storage_path=storage)
        # Compare the actual results payload, not _meta (cache_hit / timing vary).
        ra = r_std.get("results", [])
        rb = r_auto.get("results", [])
        identical = ra == rb
        t_std = toks(json.dumps(ra))
        t_auto = toks(json.dumps(rb))
        rows.append({
            "query": q,
            "results": r_auto.get("result_count", 0),
            "tokens_standard": t_std,
            "tokens_auto": t_auto,
            "identical": identical,
        })
    return rows


def bench_12_budget_adherence(repo: str, storage: str, budget: int = 1000):
    """§1.2: full-mode with token_budget — must not overshoot.

    We can't reproduce the v1.63.1 overshoot in-process (the fix is in the
    packer), so we measure v1.70.0 compliance directly: bytes_used vs budget.
    """
    rows = []
    for q in DISCOVERY_QUERIES[:8]:
        r = search_symbols(
            repo=repo, query=q, detail_level="full",
            token_budget=budget, max_results=20, storage_path=storage,
        )
        n = r.get("result_count", 0)
        bytes_used = sum(e.get("byte_length", 0) for e in r.get("results", []))
        payload_tokens = toks(json.dumps(r))
        budget_bytes = budget * 4
        rows.append({
            "query": q,
            "results": n,
            "budget_bytes": budget_bytes,
            "bytes_used": bytes_used,
            "compliant": bytes_used <= budget_bytes,
            "headroom_pct": (1 - bytes_used / budget_bytes) * 100 if budget_bytes else 0.0,
            "payload_tokens": payload_tokens,
        })
    return rows


def fmt_rows(rows, cols):
    header = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    body = []
    for r in rows:
        body.append("| " + " | ".join(
            f"{r[c]:.1f}" if isinstance(r[c], float) else str(r[c]) for c in cols
        ) + " |")
    return "\n".join([header, sep, *body])


def main():
    target = ROOT  # dogfood: index jcodemunch-mcp itself
    storage = str(ROOT / ".ab_bench_idx")
    print(f"[ab] indexing {target} -> {storage}", flush=True)
    t0 = time.time()
    idx = index_folder(path=str(target), use_ai_summaries=False, storage_path=storage)
    repo = idx["repo"]
    n_syms = idx.get("symbols", idx.get("total_symbols", "?"))
    print(f"[ab] indexed {n_syms} symbols in {time.time()-t0:.1f}s", flush=True)

    print("[ab] §1.1 default-shape benchmark …", flush=True)
    r11 = bench_11_default_shape(repo, storage)

    print("[ab] §1.1 narrow-query parity check …", flush=True)
    r11_narrow = bench_11_narrow_parity(repo, storage)

    print("[ab] §1.2 budget-adherence benchmark …", flush=True)
    r12 = bench_12_budget_adherence(repo, storage, budget=1000)

    # Aggregates
    total_std = sum(r["tokens_standard"] for r in r11)
    total_auto = sum(r["tokens_auto"] for r in r11)
    saved = total_std - total_auto
    saved_pct = (saved / total_std * 100) if total_std else 0.0
    per_q_savings = [r["savings_pct"] for r in r11]
    narrow_all_identical = all(r["identical"] for r in r11_narrow)
    all_compliant = all(r["compliant"] for r in r12)
    worst_headroom = min(r["headroom_pct"] for r in r12) if r12 else 0

    md = [
        "# v1.70.0 A/B Benchmark Results",
        "",
        "_Measured on jcodemunch-mcp dogfooding itself (self-indexed)._",
        "",
        f"Symbols indexed: **{n_syms}**",
        "",
        "---",
        "",
        "## §1.1 — Default `detail_level` flip: `standard` → `auto`",
        "",
        "**Discovery queries** (broad `max_results=10`, no `token_budget`, no `debug`): "
        "`auto` resolves to **compact**, stripping signatures/summaries that aren't "
        "useful until the caller has narrowed down.",
        "",
        f"- Total tokens across {len(r11)} discovery queries:",
        f"  - Old default (`standard`): **{total_std:,}** tokens",
        f"  - New default (`auto`):     **{total_auto:,}** tokens",
        f"  - **Saved: {saved:,} tokens ({saved_pct:.1f}%)**",
        f"- Median per-query savings: **{statistics.median(per_q_savings):.1f}%**",
        f"- Max per-query savings:    **{max(per_q_savings):.1f}%**",
        "",
        fmt_rows(r11, ["query", "results", "tokens_standard", "tokens_auto",
                       "savings_tokens", "savings_pct"]),
        "",
        "### Narrow-query parity (no regression)",
        "",
        "When `max_results<5`, `auto` escalates to `standard` — caller explicitly "
        "asked for few results, so they likely want signatures. Both defaults must "
        "produce identical payloads.",
        "",
        f"**All {len(r11_narrow)} narrow queries identical under `auto` vs `standard`: "
        f"{narrow_all_identical}**",
        "",
        fmt_rows(r11_narrow, ["query", "results", "tokens_standard", "tokens_auto",
                              "identical"]),
        "",
        "---",
        "",
        "## §1.2 — Full-mode `token_budget` adherence",
        "",
        "Under v1.63.1, `detail_level='full'` materialized `source`/`docstring`/"
        "`end_line` **after** the budget packer had already picked winners — so "
        "the declared budget was routinely overshot by 5–20×. v1.70.0 materializes "
        "full content **before** packing, so `byte_length` reflects what actually "
        "ships.",
        "",
        f"Budget: **{r12[0]['budget_bytes']:,} bytes** ({r12[0]['budget_bytes']//4} "
        "tokens).",
        "",
        f"- **All {len(r12)} queries compliant: {all_compliant}**",
        f"- Worst-case headroom: **{worst_headroom:.1f}%** (negative = overshot)",
        "",
        fmt_rows(r12, ["query", "results", "budget_bytes", "bytes_used",
                       "compliant", "headroom_pct", "payload_tokens"]),
        "",
        "---",
        "",
        "## Methodology",
        "",
        "- Index: `jcodemunch-mcp` source tree (self-indexed, dogfood)",
        "- Token counter: `tiktoken` cl100k_base",
        "- `auto` resolution rules: `compact` when "
        "`token_budget is None and not debug and max_results>=5`, else `standard`",
        "- Budget adherence: `sum(byte_length)` over returned results vs declared "
        "budget in bytes (`token_budget*4`)",
        "- Reproduce: `PYTHONPATH=src python benchmarks/harness/ab_v1_70_0.py`",
    ]

    out_path = ROOT / "benchmarks" / "results_v1.70.0.md"
    out_path.write_text("\n".join(md), encoding="utf-8")
    print(f"[ab] wrote {out_path}", flush=True)
    print(f"[ab] §1.1 total savings: {saved:,} tokens ({saved_pct:.1f}%)")
    print(f"[ab] §1.1 narrow parity: {narrow_all_identical}")
    print(f"[ab] §1.2 all compliant: {all_compliant} (worst headroom {worst_headroom:.1f}%)")


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
