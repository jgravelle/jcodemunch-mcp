"""Compact encoder for search_text.

search_text returns a nested shape — matches grouped by file:

    {
      "result_count": int,
      "results": [
        {"file": str, "matches": [{"line": int, "text": str, "before"?: [str], "after"?: [str]}]}
      ],
      "_meta": {...}
    }

The MUNCH table format is inherently flat (one table = list of homogeneous
rows), so this encoder flattens matches into per-row records on encode and
regroups them by file on decode. `before` / `after` context lines — absent
on the common path (context_lines=0) — ride as JSON strings when present.
"""

from __future__ import annotations

import json

from .. import schema_driven as sd

TOOLS = ("search_text",)
ENCODING_ID = "st4"  # st4: `more_files` table + its three scalars; st3: enclosing-symbol columns, body `truncated`/`timed_out`/`note`
LEGACY_ENCODING_IDS = ("st1", "st2", "st3")

# Internal flat row shape used on the wire. `results` (nested) is pre-flattened
# into this key on encode, and regrouped back into `results` on decode. The
# key name avoids colliding with the public `results` field.
_ROWS_KEY = "__rows__"

_TABLES = [
    sd.TableSpec(
        key=_ROWS_KEY,
        tag="t",
        cols=["file", "line", "text", "before", "after", "in_id", "in_line", "in_end"],
        intern=["file", "in_id"],
        types={"line": "int", "in_line": "int", "in_end": "int"},
    ),
    # A truncated page's list of the matching files it left off.
    sd.TableSpec(
        key="more_files",
        tag="mf",
        cols=["file", "matches"],
        intern=["file"],
        types={"matches": "int"},
    ),
]
_SCALARS = (
    "result_count", "truncated", "timed_out", "note", "query", "repo",
    "more_files_total", "more_files_truncated", "more_files_complete",
)
_META = (
    "timing_ms",
    "files_searched",
    "truncated",
    "tokens_saved",
    "total_tokens_saved",
)
_META_JSON = ("verdict",)  # structured _meta that must survive compaction
# Per-scalar type hints so numeric/bool values round-trip as native types
# instead of raw strings.
_SCALAR_TYPES: dict[str, str] = {
    "result_count": "int",
    "truncated": "bool",
    "timed_out": "bool",
    "more_files_total": "int",
    "more_files_truncated": "bool",
    "more_files_complete": "bool",
    "_meta.timing_ms": "float",
    "_meta.files_searched": "int",
    "_meta.truncated": "bool",
    "_meta.tokens_saved": "int",
    "_meta.total_tokens_saved": "int",
}


def _flatten(response: dict) -> dict:
    """Replace nested `results: [{file, matches:[...]}]` with flat rows."""
    out = {k: v for k, v in response.items() if k != "results"}
    rows: list[dict] = []
    for group in response.get("results") or []:
        if not isinstance(group, dict):
            continue
        file_path = group.get("file")
        matches = group.get("matches") or []
        for m in matches:
            if not isinstance(m, dict):
                continue
            before = m.get("before")
            after = m.get("after")
            enclosing = m.get("in") or {}
            rows.append(
                {
                    "file": file_path,
                    "line": m.get("line"),
                    "text": m.get("text", ""),
                    "before": json.dumps(before, separators=(",", ":")) if before else "",
                    "after": json.dumps(after, separators=(",", ":")) if after else "",
                    "in_id": enclosing.get("id", ""),
                    "in_line": enclosing.get("line"),
                    "in_end": enclosing.get("end_line"),
                }
            )
    out[_ROWS_KEY] = rows
    return out


def _regroup(decoded: dict) -> dict:
    """Inverse of _flatten: rebuild `results` list preserving file order."""
    rows = decoded.pop(_ROWS_KEY, None) or []
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in rows:
        file_path = row.get("file")
        if not isinstance(file_path, str):
            continue
        match: dict = {"line": row.get("line"), "text": row.get("text") or ""}
        for ctx_key in ("before", "after"):
            raw = row.get(ctx_key)
            if isinstance(raw, str) and raw:
                try:
                    match[ctx_key] = json.loads(raw)
                except ValueError:
                    match[ctx_key] = [raw]
        if row.get("in_id"):
            match["in"] = {
                "id": row.get("in_id"),
                "line": row.get("in_line"),
                "end_line": row.get("in_end"),
            }
        if file_path not in groups:
            groups[file_path] = []
            order.append(file_path)
        groups[file_path].append(match)
    decoded["results"] = [{"file": f, "matches": groups[f]} for f in order]
    return decoded


def encode(tool: str, response: dict) -> tuple[str, str]:
    return sd.encode(
        tool, _flatten(response), ENCODING_ID, _TABLES, _SCALARS, meta_keys=_META, meta_json_blobs=_META_JSON,
    )


def decode(payload: str) -> dict:
    decoded = sd.decode(
        payload,
        _TABLES,
        _SCALARS,
        meta_keys=_META, meta_json_blobs=_META_JSON,
        scalar_types=_SCALAR_TYPES,
    )
    return _regroup(decoded)
