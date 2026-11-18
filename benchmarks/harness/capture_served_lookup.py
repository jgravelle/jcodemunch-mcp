"""Capture the raw text one served lookup returns, call by call.

The benchmark counts a lookup as `search_symbols` (max_results=5) plus
`get_symbol_source` on the top three rows, read from the text the server
serves (`run_benchmark._served_text`). Two machines on the same code, `mcp`
and `tiktoken` have counted different token totals for the same lookup, and a
total cannot say which bytes differ. This writes every served text to a file,
with its token count, byte count and digest, so two captures can be diffed
byte for byte.

It measures nothing new. It wraps `run_benchmark._served_text` and runs
`run_benchmark._measure_jmunch_served` unchanged, after the same setup the
benchmark's served method does, so the texts it writes are the texts the
benchmark counted.

It also writes `environment.json`: the NAMES of the environment variables
(never their values), the platform, the interpreter and package versions, and
the config path and storage path the process resolved.

Usage:
    python benchmarks/harness/capture_served_lookup.py --repo gin-gonic/gin --out <dir>
    python benchmarks/harness/capture_served_lookup.py --repo gin-gonic/gin --query "param bind" --out <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import run_benchmark as rb  # noqa: E402


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower() or "q"


def _version(dist: str) -> str | None:
    from importlib import metadata

    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


def environment() -> dict:
    """What the process ran on. Variable names only: a value can be a secret."""
    from jcodemunch_mcp import config as cfg

    try:
        config_path = str(cfg._global_config_path())
    except Exception as exc:  # noqa: BLE001 - recorded, not hidden
        config_path = f"unresolved: {type(exc).__name__}"
    return {
        "env_var_names": sorted(os.environ),
        "platform": sys.platform,
        "machine": platform.machine(),
        "python": platform.python_version(),
        "packages": {d: _version(d) for d in ("jcodemunch-mcp", "mcp", "tiktoken")},
        "config_path": config_path,
        "config_path_exists": Path(config_path).exists() if not config_path.startswith("unresolved") else None,
        "code_index_path": os.environ.get("CODE_INDEX_PATH"),
        "tokenizer": rb.TOKENIZER,
    }


def capture(repo: str, queries: list[str], out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    rb._use_default_install_config()
    rb._quiet_once_per_process_notices()
    repo_id = rb.resolve_repo_arg(repo)

    calls: list[dict] = []
    real = rb._served_text
    current = {"query": ""}

    def recording(tool: str, args: dict) -> str:
        text = real(tool, args)
        n = sum(1 for c in calls if c["query"] == current["query"])
        name = f"{_slug(current['query'])}.{n:02d}.{tool}.txt"
        (out / name).write_bytes(text.encode("utf-8"))
        raw = text.encode("utf-8")
        calls.append({
            "query": current["query"],
            "tool": tool,
            "args": args,
            "file": name,
            "tokens": rb.count_tokens(text),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "crlf": text.count("\r\n"),
        })
        return text

    rb._served_text = recording
    lookups = []
    try:
        for query in queries:
            current["query"] = query
            row = rb._measure_jmunch_served(repo_id, query)
            lookups.append({"query": query, **row})
    finally:
        rb._served_text = real

    manifest = {"repo": repo, "repo_id": repo_id, "lookups": lookups, "calls": calls}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8")
    (out / "environment.json").write_text(json.dumps(environment(), indent=1), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True, help="owner/repo as indexed, or an indexed folder")
    ap.add_argument("--query", action="append", help="a query; repeat for several (default: every task in tasks.json)")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    queries = args.query or list(rb.TASKS)
    manifest = capture(args.repo, queries, args.out)
    for row in manifest["lookups"]:
        print(f"{row['query']!r}: {row['tokens']} tokens "
              f"(search {row['search_tokens']}, fetch {row['fetch_tokens']}, {row['hits_fetched']} fetched)")
    print(f"{len(manifest['calls'])} served texts written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
