"""Benchmark: languages=["python"] config vs no config (all languages).

Tests:
  1. Query tools on FlatCAM_EVO with python-only config
  2. Indexing a mixed-language folder to verify SQL/dbt gating
  3. Tool availability (search_columns, language enum)

Usage:
    python benchmarks/profile_language_filter.py [--iterations N] [--label LABEL]
"""

import asyncio
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

REPO = "local/FlatCAM_EVO-abd24de5"
ITERATIONS = 5


def _create_mixed_language_folder(base_dir):
    """Create a small folder with Python, JS, SQL, and dbt files for indexing."""
    folder = Path(base_dir) / "mixed_lang_project"
    folder.mkdir(parents=True, exist_ok=True)

    # Python file
    (folder / "app.py").write_text(
        'class UserService:\n'
        '    """Handles user operations."""\n'
        '    def get_user(self, user_id: int) -> dict:\n'
        '        return {"id": user_id, "name": "test"}\n'
        '\n'
        '    def list_users(self) -> list:\n'
        '        return []\n'
        '\n'
        'def main():\n'
        '    svc = UserService()\n'
        '    print(svc.get_user(1))\n',
        encoding="utf-8",
    )

    # JavaScript file
    (folder / "utils.js").write_text(
        'function formatDate(date) {\n'
        '  return date.toISOString();\n'
        '}\n'
        '\n'
        'class Logger {\n'
        '  log(msg) { console.log(msg); }\n'
        '}\n'
        '\n'
        'module.exports = { formatDate, Logger };\n',
        encoding="utf-8",
    )

    # SQL file (plain DDL)
    (folder / "schema.sql").write_text(
        'CREATE TABLE users (\n'
        '    id INTEGER PRIMARY KEY,\n'
        '    name VARCHAR(255) NOT NULL,\n'
        '    email VARCHAR(255) UNIQUE\n'
        ');\n'
        '\n'
        'CREATE TABLE orders (\n'
        '    id INTEGER PRIMARY KEY,\n'
        '    user_id INTEGER REFERENCES users(id),\n'
        '    total DECIMAL(10, 2)\n'
        ');\n',
        encoding="utf-8",
    )

    # dbt model (SQL with Jinja)
    models_dir = folder / "models"
    models_dir.mkdir(exist_ok=True)
    (models_dir / "stg_users.sql").write_text(
        '{{ config(materialized="view") }}\n'
        '\n'
        'SELECT\n'
        '    id,\n'
        '    name,\n'
        '    email,\n'
        '    created_at\n'
        'FROM {{ source("raw", "users") }}\n'
        'WHERE email IS NOT NULL\n',
        encoding="utf-8",
    )

    # dbt project file (triggers dbt provider detection)
    (folder / "dbt_project.yml").write_text(
        'name: test_project\n'
        'version: "1.0.0"\n'
        'profile: test\n'
        'model-paths: ["models"]\n',
        encoding="utf-8",
    )

    # TypeScript file
    (folder / "api.ts").write_text(
        'interface User {\n'
        '  id: number;\n'
        '  name: string;\n'
        '}\n'
        '\n'
        'export function fetchUser(id: number): Promise<User> {\n'
        '  return fetch(`/api/users/${id}`).then(r => r.json());\n'
        '}\n',
        encoding="utf-8",
    )

    return str(folder)


async def setup(languages_config=None):
    """Initialize config and return call_tool."""
    tmpdir = tempfile.mkdtemp(prefix="jcm_langbench_")

    try:
        from jcodemunch_mcp import config as config_module

        if languages_config is not None:
            config_path = os.path.join(tmpdir, "config.jsonc")
            with open(config_path, "w") as f:
                json.dump({"languages": languages_config}, f)
            config_module.load_config(tmpdir)
            print(f"  Config: languages={config_module.get('languages')}")
        else:
            config_module.load_config(tmpdir)
            print(f"  Config: languages={config_module.get('languages')} (all)")
    except (ImportError, ModuleNotFoundError):
        print("  Config: N/A (main branch, all languages)")

    from jcodemunch_mcp.server import call_tool, list_tools
    return call_tool, list_tools, tmpdir


async def check_tool_gating(list_tools):
    """Verify tool availability and schema based on config."""
    tools = await list_tools()
    tool_names = [t.name for t in tools]

    has_search_columns = "search_columns" in tool_names

    lang_enum = []
    for t in tools:
        if t.name == "search_symbols":
            lang_enum = t.inputSchema.get("properties", {}).get("language", {}).get("enum", [])
            break

    return {
        "total_tools": len(tool_names),
        "search_columns_available": has_search_columns,
        "search_symbols_languages": lang_enum,
    }


async def benchmark_query_tools(call_tool, iterations):
    """Benchmark query tools against FlatCAM_EVO."""
    tool_calls = [
        ("list_repos", {}),
        ("get_repo_outline", {"repo": REPO}),
        ("get_file_tree", {"repo": REPO}),
        ("get_file_outline", {"repo": REPO, "file_path": "appMain.py"}),
        ("get_file_content", {"repo": REPO, "file_path": "appMain.py", "start_line": 1, "end_line": 50}),
        ("get_symbol", {"repo": REPO, "symbol_id": "appMain.py::App#class"}),
        ("search_symbols", {"repo": REPO, "query": "parse", "max_results": 20}),
        ("search_text", {"repo": REPO, "query": "parse", "max_results": 20}),
        ("find_references", {"repo": REPO, "identifier": "self", "max_results": 10}),
        ("find_importers", {"repo": REPO, "file_path": "appMain.py", "max_results": 10}),
        ("get_dependency_graph", {"repo": REPO, "file": "appMain.py", "depth": 2}),
        ("get_class_hierarchy", {"repo": REPO, "class_name": "App"}),
    ]

    # Warmup
    for name, args in tool_calls:
        try:
            await call_tool(name, args)
        except Exception:
            pass

    results = {}
    for name, args in tool_calls:
        timings = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            try:
                await call_tool(name, args)
            except Exception:
                pass
            timings.append((time.perf_counter() - t0) * 1000)

        results[name] = {
            "median_ms": round(statistics.median(timings), 2),
            "mean_ms": round(statistics.mean(timings), 2),
            "stdev_ms": round(statistics.stdev(timings), 2) if len(timings) > 1 else 0,
        }

    return results


async def benchmark_indexing(call_tool, tmpdir, iterations):
    """Index a mixed-language folder and measure timing + symbol counts."""
    folder = _create_mixed_language_folder(tmpdir)
    storage_path = os.environ.get("CODE_INDEX_PATH")

    results = {"timings_ms": [], "symbols_by_language": {}, "files_by_language": {}}

    for i in range(iterations):
        # Invalidate previous index
        try:
            await call_tool("invalidate_cache", {"repo": Path(folder).name})
        except Exception:
            pass

        t0 = time.perf_counter()
        result = await call_tool("index_folder", {
            "path": folder,
            "use_ai_summaries": False,
        })
        elapsed = (time.perf_counter() - t0) * 1000
        results["timings_ms"].append(elapsed)

        data = json.loads(result[0].text)
        if i == 0:
            # Capture language breakdown from first run
            results["file_count"] = data.get("file_count", 0)
            results["symbol_count"] = data.get("symbol_count", 0)
            results["languages"] = data.get("languages", {})
            results["duration_seconds"] = data.get("duration_seconds", 0)

    results["median_ms"] = round(statistics.median(results["timings_ms"]), 2)
    results["mean_ms"] = round(statistics.mean(results["timings_ms"]), 2)

    # Check dbt provider detection
    dbt_detected = False
    try:
        outline_result = await call_tool("get_repo_outline", {"repo": Path(folder).name})
        outline = json.loads(outline_result[0].text)
        # dbt provider would add dbt-specific symbols
        dbt_detected = "dbt" in str(outline).lower()
    except Exception:
        pass
    results["dbt_detected"] = dbt_detected

    # Cleanup
    try:
        await call_tool("invalidate_cache", {"repo": Path(folder).name})
    except Exception:
        pass

    return results


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=ITERATIONS)
    parser.add_argument("--label", default="current")
    parser.add_argument("--languages", nargs="*", default=None,
                        help="Language filter (e.g. --languages python). Omit for all.")
    args = parser.parse_args()

    lang_config = args.languages  # None = all, ["python"] = python only
    label = args.label

    print(f"=== Language Filter Benchmark (label={label}) ===")
    call_tool, list_tools_fn, tmpdir = await setup(lang_config)

    # 1. Tool gating
    print("\n--- Tool Gating ---")
    gating = await check_tool_gating(list_tools_fn)
    print(f"  Total tools: {gating['total_tools']}")
    print(f"  search_columns available: {gating['search_columns_available']}")
    print(f"  search_symbols languages: {gating['search_symbols_languages']}")

    # 2. Query tools
    print(f"\n--- Query Tools ({args.iterations} iterations) ---")
    query_results = await benchmark_query_tools(call_tool, args.iterations)
    print(f"  {'Tool':<25} {'Median (ms)':>12} {'Mean (ms)':>12}")
    print(f"  {'-'*51}")
    total = 0
    for name, data in query_results.items():
        print(f"  {name:<25} {data['median_ms']:>12.2f} {data['mean_ms']:>12.2f}")
        total += data["median_ms"]
    print(f"  {'-'*51}")
    print(f"  {'TOTAL':<25} {total:>12.2f}")

    # 3. Indexing
    print(f"\n--- Indexing Mixed-Language Folder ({args.iterations} iterations) ---")
    index_results = await benchmark_indexing(call_tool, tmpdir, args.iterations)
    print(f"  Index time (median): {index_results['median_ms']:.1f} ms")
    print(f"  Files indexed: {index_results.get('file_count', '?')}")
    print(f"  Symbols found: {index_results.get('symbol_count', '?')}")
    print(f"  Languages parsed: {index_results.get('languages', {})}")
    print(f"  dbt detected: {index_results.get('dbt_detected', '?')}")

    # Save
    out_path = Path(__file__).parent / f"results_lang_{label}.json"
    out_path.write_text(json.dumps({
        "label": label,
        "languages_config": lang_config,
        "gating": gating,
        "query": query_results,
        "indexing": index_results,
    }, indent=2))
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    asyncio.run(main())
