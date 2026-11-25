"""Benchmark all MCP tools against FlatCAM_EVO to detect config-change regressions.

Measures both cold-index (first load from disk) and warm-index (LRU cache hit) performance.

Usage:
    python benchmarks/profile_config_regression.py [--iterations N] [--label LABEL]

Writes JSON results to benchmarks/results_{label}.json
"""

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

REPO = "local/FlatCAM_EVO-abd24de5"
ITERATIONS = 5


async def setup():
    """Initialize config and verify repo is indexed."""
    # Load config if available (feature branch), otherwise skip (main)
    try:
        from jcodemunch_mcp import config as config_module
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="jcm_bench_")
        config_module.load_config(tmpdir)
    except (ImportError, ModuleNotFoundError):
        pass

    from jcodemunch_mcp.server import call_tool
    result = await call_tool("list_repos", {})
    repos = json.loads(result[0].text)
    repo_names = [r["repo"] for r in repos.get("repos", [])]
    if REPO not in repo_names:
        print(f"ERROR: {REPO} not indexed. Available: {repo_names}")
        sys.exit(1)
    return call_tool


async def discover_targets(call_tool):
    """Discover file paths and symbols for benchmarking."""
    outline_result = await call_tool("get_file_tree", {"repo": REPO})
    tree = json.loads(outline_result[0].text)

    files = []
    for f in tree.get("files", []):
        if f.endswith(".py") and len(files) < 3:
            files.append(f)

    if not files:
        search_result = await call_tool("search_symbols", {"repo": REPO, "query": "App", "max_results": 5})
        search_data = json.loads(search_result[0].text)
        for sym in search_data.get("results", []):
            files.append(sym["file"])
            break

    symbols = []
    class_name = None
    if files:
        outline = await call_tool("get_file_outline", {"repo": REPO, "file_path": files[0]})
        outline_data = json.loads(outline[0].text)
        for sym in outline_data.get("symbols", [])[:3]:
            symbols.append(sym["id"])
            if sym.get("kind") == "class" and not class_name:
                class_name = sym["name"]

    if not class_name:
        search_result = await call_tool("search_symbols", {"repo": REPO, "query": "App", "kind": "class", "max_results": 1})
        search_data = json.loads(search_result[0].text)
        for sym in search_data.get("results", []):
            class_name = sym["name"]
            if not symbols:
                symbols.append(sym["id"])

    return {
        "files": files,
        "symbols": symbols,
        "class_name": class_name or "App",
        "search_query": "parse",
        "identifier": "self",
    }


def build_tool_calls(targets):
    """Build the list of (tool_name, arguments) to benchmark."""
    files = targets["files"]
    symbols = targets["symbols"]
    file0 = files[0] if files else "app_Main.py"
    sym0 = symbols[0] if symbols else None

    calls = [
        ("list_repos", {}),
        ("get_session_stats", {}),
        ("get_repo_outline", {"repo": REPO}),
        ("get_file_tree", {"repo": REPO}),
    ]

    if files:
        calls.append(("get_file_outline", {"repo": REPO, "file_path": file0}))
        calls.append(("get_file_content", {"repo": REPO, "file_path": file0, "start_line": 1, "end_line": 50}))
        calls.append(("find_importers", {"repo": REPO, "file_path": file0, "max_results": 10}))

    if sym0:
        calls.append(("get_symbol", {"repo": REPO, "symbol_id": sym0}))
        calls.append(("get_context_bundle", {"repo": REPO, "symbol_id": sym0}))
        calls.append(("get_related_symbols", {"repo": REPO, "symbol_id": sym0, "max_results": 5}))

    calls.extend([
        ("search_symbols", {"repo": REPO, "query": targets["search_query"], "max_results": 20}),
        ("search_text", {"repo": REPO, "query": targets["search_query"], "max_results": 20}),
        ("find_references", {"repo": REPO, "identifier": targets["identifier"], "max_results": 10}),
        ("get_dependency_graph", {"repo": REPO, "file": file0, "depth": 2}),
        ("get_blast_radius", {"repo": REPO, "symbol": targets["search_query"], "depth": 2}),
        ("get_class_hierarchy", {"repo": REPO, "class_name": targets["class_name"]}),
    ])

    return calls


def _evict_lru_cache():
    """Evict the in-memory LRU index cache to simulate cold reads."""
    # Primary cache: sqlite_store._index_cache (OrderedDict, module-level)
    try:
        from jcodemunch_mcp.storage.sqlite_store import _index_cache, _cache_lock
        with _cache_lock:
            _index_cache.clear()
    except (ImportError, AttributeError):
        pass
    # Legacy cache: index_store._load_index_json_cached (functools.lru_cache)
    try:
        from jcodemunch_mcp.storage.index_store import _load_index_json_cached
        _load_index_json_cached.cache_clear()
    except (ImportError, AttributeError):
        pass
    # resolve_repo bare-name cache
    try:
        from jcodemunch_mcp.tools._utils import _BARE_NAME_CACHE
        _BARE_NAME_CACHE.clear()
    except (ImportError, AttributeError):
        pass


async def benchmark(call_tool, tool_calls, iterations, cold=False):
    """Run each tool call N times and collect timings.

    Args:
        cold: If True, evict LRU cache before each iteration to measure
              cold-index (disk read) performance.
    """
    results = {}

    if not cold:
        # Warmup run (fills LRU cache)
        for name, args in tool_calls:
            try:
                await call_tool(name, args)
            except Exception:
                pass

    for name, args in tool_calls:
        timings = []
        errors = 0
        for _ in range(iterations):
            if cold:
                _evict_lru_cache()
            t0 = time.perf_counter()
            try:
                await call_tool(name, args)
            except Exception:
                errors += 1
            elapsed_ms = (time.perf_counter() - t0) * 1000
            timings.append(elapsed_ms)

        results[name] = {
            "mean_ms": round(statistics.mean(timings), 2),
            "median_ms": round(statistics.median(timings), 2),
            "stdev_ms": round(statistics.stdev(timings), 2) if len(timings) > 1 else 0,
            "min_ms": round(min(timings), 2),
            "max_ms": round(max(timings), 2),
            "iterations": iterations,
            "errors": errors,
        }

    return results


def _print_table(results, header="Tool"):
    print(f"{header:<25} {'Mean (ms)':>10} {'Median':>10} {'StDev':>10} {'Min':>10} {'Max':>10}")
    print("-" * 77)
    for name, data in results.items():
        print(f"{name:<25} {data['mean_ms']:>10.2f} {data['median_ms']:>10.2f} "
              f"{data['stdev_ms']:>10.2f} {data['min_ms']:>10.2f} {data['max_ms']:>10.2f}")


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=ITERATIONS)
    parser.add_argument("--label", default="current")
    args = parser.parse_args()

    print(f"Setting up (label={args.label}, iterations={args.iterations})...")
    call_tool = await setup()

    print("Discovering benchmark targets...")
    targets = await discover_targets(call_tool)
    print(f"  Files: {targets['files']}")
    print(f"  Symbols: {targets['symbols'][:2]}...")
    print(f"  Class: {targets['class_name']}")

    tool_calls = build_tool_calls(targets)

    # --- Warm index benchmark (LRU cache hit) ---
    print(f"\n=== WARM INDEX (LRU cache hit) ===")
    print(f"Benchmarking {len(tool_calls)} tools x {args.iterations} iterations...\n")
    warm_results = await benchmark(call_tool, tool_calls, args.iterations, cold=False)
    _print_table(warm_results)

    # --- Cold index benchmark (disk read per call) ---
    print(f"\n=== COLD INDEX (cache evicted per call) ===")
    print(f"Benchmarking {len(tool_calls)} tools x {args.iterations} iterations...\n")
    cold_results = await benchmark(call_tool, tool_calls, args.iterations, cold=True)
    _print_table(cold_results)

    # --- Cold vs Warm comparison ---
    print(f"\n=== COLD vs WARM COMPARISON ===")
    print(f"{'Tool':<25} {'Warm (ms)':>10} {'Cold (ms)':>10} {'Overhead':>10} {'Factor':>8}")
    print("-" * 65)
    for tool in warm_results:
        w = warm_results[tool]["median_ms"]
        c = cold_results[tool]["median_ms"]
        overhead = c - w
        factor = c / w if w > 0 else float("inf")
        print(f"{tool:<25} {w:>10.2f} {c:>10.2f} {overhead:>+10.2f} {factor:>7.1f}x")

    # Save to JSON
    out_path = Path(__file__).parent / f"results_{args.label}.json"
    out_path.write_text(json.dumps({
        "label": args.label,
        "repo": REPO,
        "iterations": args.iterations,
        "targets": {k: v for k, v in targets.items() if k != "symbols" or len(v) <= 3},
        "warm": warm_results,
        "cold": cold_results,
    }, indent=2))
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    asyncio.run(main())
