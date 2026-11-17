"""Capture a token-savings + latency snapshot for the current MCP session.

Writes ``benchmarks/token_baselines/v{VERSION}.json`` derived from
``get_session_stats``. Usage:

    PYTHONPATH=src python benchmarks/harness/capture_token_baseline.py

The capture is a side-effect-free read; if no calls have been recorded in
this Python process yet, the snapshot will simply have empty tool fields
(useful for the very first capture after a release).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from jcodemunch_mcp import __version__
from jcodemunch_mcp.storage.token_tracker import get_session_stats, latency_stats


def build_snapshot() -> dict:
    stats = get_session_stats()
    latency = latency_stats()

    # Merge per-tool token-savings (from tool_breakdown) with latency stats.
    breakdown = stats.get("tool_breakdown", {})
    tools: dict = {}
    for tool, tokens_saved in breakdown.items():
        tools.setdefault(tool, {})["tokens_saved"] = int(tokens_saved)
    for tool, info in latency.items():
        bucket = tools.setdefault(tool, {})
        bucket["calls"] = int(info.get("count", 0))
        bucket["p50_ms"] = float(info.get("p50_ms", 0.0))
        bucket["p95_ms"] = float(info.get("p95_ms", 0.0))
        bucket["max_ms"] = float(info.get("max_ms", 0.0))

    session = {
        "session_calls": int(stats.get("session_calls", 0)),
        "session_tokens_saved": int(stats.get("session_tokens_saved", 0)),
        "session_duration_s": float(stats.get("session_duration_s", 0.0)),
    }

    return {
        "version": __version__,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session": session,
        "tools": tools,
    }


def main() -> int:
    snapshot = build_snapshot()
    out_dir = REPO_ROOT / "benchmarks" / "token_baselines"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"v{__version__}.json"
    out_path.write_text(json.dumps(snapshot, indent=2) + "\n")
    print(
        f"wrote {out_path.relative_to(REPO_ROOT)} "
        f"(tools={len(snapshot['tools'])}, "
        f"session_calls={snapshot['session']['session_calls']})"
    )
    return 0


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
