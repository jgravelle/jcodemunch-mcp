"""What each tool surface puts in front of the model, for a deferring and a non-deferring client.

`schema_baseline.json` counts the whole tool list, which is what a client that
loads every schema sends. A deferring client (Claude Code with tool search)
sends something else: the schemas of tools marked always-loaded, the NAME of
every other tool, and the server instructions. This script records both views
for `counter` and `full`, with the basis of every figure written into the
file, so a figure quoted from it can be compared like-for-like with a figure
measured elsewhere.

Run from the repo root, with CODE_INDEX_PATH naming an EMPTY directory (the
counts go through the global config, as for the schema baseline):
    CODE_INDEX_PATH=$(mktemp -d) PYTHONPATH=src python benchmarks/harness/capture_surface_tokens.py

Output: benchmarks/surface_tokens.json. `tests/test_surface_tokens_are_current.py`
requires it to equal a live capture.

⚠ cl100k_base is a proxy for the tokenizer a client actually uses (see
capture_schema_baseline.py). The ratios between arms hold; quote absolutes only
with the basis beside them.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "capture_schema_baseline", _HERE / "capture_schema_baseline.py"
)
_base = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_base)

import tiktoken  # noqa: E402

from jcodemunch_mcp import config as config_module  # noqa: E402
from jcodemunch_mcp import server  # noqa: E402

OUT = _HERE.parent / "surface_tokens.json"
SURFACES = ("counter", "full")
ENCODING = "cl100k_base"

BASIS = {
    "tokenizer": f"tiktoken {ENCODING}, a proxy: Claude's tokenizer counts differently",
    "schema": (
        "one tool = compact JSON of {name, description, inputSchema}, the serialization "
        "capture_schema_baseline.py uses; a tool's tokens are counted on its own row"
    ),
    "deferred_names": (
        f"each deferred tool as {server._MCP_TOOL_PREFIX}<name>, newline-joined, counted once; "
        "the prefix is what a deferring client shows the model"
    ),
    "instructions": (
        "server._mcp_instructions(surface) with no repository context, counted once; "
        "a live session names its indexed file count, which can move this by a few tokens"
    ),
    "deferring_total": "schemas of always-loaded tools + deferred_names + instructions",
    "non_deferring_total": "schemas of every published tool + instructions",
    "config": "the default config (empty CODE_INDEX_PATH), tool_profile full, compact_schemas default",
    "excludes": "the client's own tool-search wrapper and any per-request framing around a schema",
}


def _row(tool) -> str:
    return _base._tools_to_serialized([tool])[1:-1]


def measure() -> dict:
    """Both views for each surface, written nowhere."""
    enc = tiktoken.get_encoding(ENCODING)
    tok = lambda s: len(enc.encode(s))  # noqa: E731
    cfg = config_module._GLOBAL_CONFIG  # type: ignore[attr-defined]
    original = {k: cfg.get(k) for k in _base._CFG_KEYS}
    out: dict = {"basis": BASIS}
    try:
        for surface in SURFACES:
            cfg.update({"tool_surface": surface, "tool_profile": "full"})
            tools = server._build_tools_list(surface_override=surface)
            loaded = [
                t
                for t in tools
                if (getattr(t, "meta", None) or {}).get("anthropic/alwaysLoad")
            ]
            deferred = [t for t in tools if t not in loaded]
            per_tool = {t.name: tok(_row(t)) for t in loaded}
            names = (
                tok("\n".join(server._MCP_TOOL_PREFIX + t.name for t in deferred))
                if deferred
                else 0
            )
            instructions = tok(server._mcp_instructions(surface=surface))
            all_schemas = sum(tok(_row(t)) for t in tools)
            out[surface] = {
                "published": len(tools),
                "always_loaded": per_tool,
                "always_loaded_schema_tokens": sum(per_tool.values()),
                "always_loaded_schema_bytes": sum(
                    len(_row(t).encode("utf-8")) for t in loaded
                ),
                "deferred": len(deferred),
                "deferred_name_tokens": names,
                "instructions_tokens": instructions,
                "deferring_total": sum(per_tool.values()) + names + instructions,
                "non_deferring_total": all_schemas + instructions,
            }
    finally:
        for k, v in original.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v
    return out


if __name__ == "__main__":
    data = measure()
    OUT.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {OUT}")
    for s in SURFACES:
        print(s, {k: v for k, v in data[s].items() if k != "always_loaded"})
