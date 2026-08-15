"""Capture tools/list schema token counts at each surface x profile x compact combo.

§0 pre-flight for the v2.0.0 context-optimization work. These numbers become
the regression guardrails in §7 (see tests/test_schema_budget.py).

Run from the repo root:
    PYTHONPATH=src python benchmarks/harness/capture_schema_baseline.py
    PYTHONPATH=src python benchmarks/harness/capture_schema_baseline.py --breakdown

Output: benchmarks/schema_baseline.json

Methodology: tokenize the JSON-serialized tool list with tiktoken cl100k_base
(the OpenAI/Anthropic-compatible GPT-4 tokenizer). Schemas are serialized with
the same compaction the server uses for its on-wire tool list.

⚠ cl100k_base is a PROXY for the tokenizer any given client actually uses. The
absolute counts here are approximate; the ratios between arms are not. Measured
2026-08-14, the same arms in o200k_base and in raw UTF-8 bytes reproduce every
percentage in this file to within 0.3 points. Publish the ratios, not the
absolutes.

⚠⚠ `tool_surface: "counter"` is measured here because it is the only arm that
changes the answer by an order of magnitude, and it was previously quoted from
memory rather than from a run. See --breakdown for where the remaining budget
sits: under `full`, tool DESCRIPTIONS are ~36% of the payload and
`compact_schemas` does not touch them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running directly from repo root without installing the package.
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import tiktoken  # noqa: E402

from jcodemunch_mcp import config as config_module  # noqa: E402
from jcodemunch_mcp.server import _build_tools_list  # noqa: E402


PROFILES = ["core", "standard", "full"]
COMPACT_FLAGS = [True, False]

# Keys that were frozen before `tool_surface` was measured. Every one of these
# is an arm of `tool_surface: "full"`; the counter arms carry the surface in the
# key so the two families can never be compared by accident.
_CFG_KEYS = ("tool_profile", "compact_schemas", "tool_surface")


def _tools_to_serialized(tools) -> str:
    payload = [
        {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
        for t in tools
    ]
    return json.dumps(payload, separators=(",", ":"))


def _count_tokens(text: str, encoding) -> int:
    return len(encoding.encode(text))


def _arms() -> list[tuple[str, dict]]:
    """(baseline key, config overrides) for every measured arm."""
    arms: list[tuple[str, dict]] = []
    for profile in PROFILES:
        for compact in COMPACT_FLAGS:
            key = f"{profile}_{'compact' if compact else 'full'}"
            arms.append(
                (key, {"tool_surface": "full", "tool_profile": profile, "compact_schemas": compact})
            )
    # The front door replaces the catalog outright, so tool_profile does not
    # apply to it. compact_schemas is measured anyway to record that it is a
    # no-op here rather than to leave a reader guessing.
    for compact in COMPACT_FLAGS:
        key = f"counter_{'compact' if compact else 'full'}"
        arms.append((key, {"tool_surface": "counter", "compact_schemas": compact}))
    return arms


def capture(out_path: Path) -> dict:
    encoding = tiktoken.get_encoding("cl100k_base")
    results: dict[str, int] = {}
    cfg = config_module._GLOBAL_CONFIG  # type: ignore[attr-defined]
    original = {k: cfg.get(k) for k in _CFG_KEYS}
    try:
        for key, overrides in _arms():
            cfg.update(overrides)
            tools = _build_tools_list()
            results[key] = _count_tokens(_tools_to_serialized(tools), encoding)
            print(f"  {key}: {results[key]} tokens ({len(tools)} tools)")
    finally:
        for k, v in original.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return results


def breakdown() -> None:
    """Where the payload actually goes, per arm and per tool.

    Answers the question the totals cannot: is there room left to cut, and in
    which of the three components. Prints only — nothing here is frozen as a
    guardrail, because per-tool costs move on every description edit.
    """
    encoding = tiktoken.get_encoding("cl100k_base")
    cfg = config_module._GLOBAL_CONFIG  # type: ignore[attr-defined]
    original = {k: cfg.get(k) for k in _CFG_KEYS}
    try:
        print(f"\n{'arm':18} {'names':>7} {'descriptions':>13} {'schemas':>9} {'desc share':>11}")
        for key, overrides in _arms():
            cfg.update(overrides)
            tools = _build_tools_list()
            names = sum(_count_tokens(t.name, encoding) for t in tools)
            descs = sum(_count_tokens(t.description or "", encoding) for t in tools)
            schemas = sum(
                _count_tokens(json.dumps(t.inputSchema, separators=(",", ":")), encoding)
                for t in tools
            )
            total = names + descs + schemas
            share = descs / total * 100 if total else 0.0
            print(f"{key:18} {names:7} {descs:13} {schemas:9} {share:10.1f}%")

        cfg.update({"tool_surface": "full", "tool_profile": "full", "compact_schemas": False})
        tools = _build_tools_list()
        per_tool = {
            t.name: _count_tokens(
                json.dumps(
                    {"name": t.name, "description": t.description, "inputSchema": t.inputSchema},
                    separators=(",", ":"),
                ),
                encoding,
            )
            for t in tools
        }
        grand = sum(per_tool.values())
        print("\ntop 10 schema cost (tool_surface=full, tool_profile=full):")
        for name, count in sorted(per_tool.items(), key=lambda kv: -kv[1])[:10]:
            print(f"  {name:34} {count:6}  {count / grand * 100:4.1f}%")
    finally:
        for k, v in original.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--breakdown",
        action="store_true",
        help="also print the names/descriptions/schemas split and the 10 costliest tools",
    )
    args = parser.parse_args()

    out = Path(__file__).resolve().parent.parent / "schema_baseline.json"
    data = capture(out)
    print(f"\nWrote baseline to {out}")
    print(json.dumps(data, indent=2))
    if args.breakdown:
        breakdown()
