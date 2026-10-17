"""Compact encoder for search_ast.

⚠⚠ The previous schema declared table key ``results`` / scalar ``result_count``
/ meta ``files_searched``; the tool has always returned ``matches`` /
``total_matches`` / ``files_scanned``. ``response.get("results", [])`` found
nothing, so EVERY search_ast call encoded to a header and an empty table
(#553, @RascoApps). The fail-closed guard in ``schema_driven`` cannot see this
class: it fires on "rows exist but no declared column was populated", and a
wrong table key produces no rows at all.

⚠ The match dict is HETEROGENEOUS across the ten presets. Eleven keys are
common to every detector and are real columns; five are pattern-specific
(``marker`` for todo_fixme, ``value`` for magic_number, ``callee`` for
eval_exec, ``loop_depth`` for nested_loops, ``nesting_depth`` for
deeply_nested) and ride as one JSON ``details`` column, the same shape
search_text uses for its optional ``before``/``after``.

⚠⚠ Declaring only the common columns would have been WORSE than the defect it
replaces. Those five keys carry the finding's actual payload -- a todo_fixme
row without ``marker`` cannot say whether it found a TODO or a HACK -- and
once ``file`` and ``line`` populate, the guard sees ``any_value`` and stays
quiet. A total, loud data loss would have become a partial, silent one.
"""

from __future__ import annotations

import json

from .. import schema_driven as sd

TOOLS = ("search_ast",)
ENCODING_ID = "sa2"  # bumped from sa1: correct table key, columns and scalars
LEGACY_ENCODING_IDS = ("sa1",)

# Keys every detector emits. Anything else on a match row is pattern-specific
# and is carried in `details` rather than becoming a mostly-empty column.
_COMMON_COLS = (
    "file", "line", "end_line", "column", "language",
    "pattern", "severity", "snippet",
    "enclosing_symbol", "symbol_kind", "symbol_complexity",
)
_DETAILS_COL = "details"

_TABLES = [
    sd.TableSpec(
        key="matches",
        tag="a",
        cols=[*_COMMON_COLS, _DETAILS_COL],
        intern=["file", "enclosing_symbol", "pattern", "language", "severity", "symbol_kind"],
        types={
            "line": "int", "end_line": "int", "column": "int",
            "symbol_complexity": "int",
        },
    ),
]
_SCALARS = ("total_matches", "repo", "category", "pattern", "description", "truncated")
_JSON = ("severity_counts", "patterns_run")
_META = (
    "elapsed_ms", "files_scanned", "files_with_matches", "languages_scanned",
    "timing_ms", "tokens_saved", "total_tokens_saved",
)
_META_JSON = ("verdict", "languages_scanned")  # lists must survive compaction
_SCALAR_TYPES: dict[str, str] = {
    "total_matches": "int",
    "truncated": "bool",
    "_meta.elapsed_ms": "int",
    "_meta.files_scanned": "int",
    "_meta.files_with_matches": "int",
    "_meta.timing_ms": "float",
    "_meta.tokens_saved": "int",
    "_meta.total_tokens_saved": "int",
}


def _pack(response: dict) -> dict:
    """Fold each match's pattern-specific keys into one JSON `details` cell."""
    out = {k: v for k, v in response.items() if k != "matches"}
    rows: list[dict] = []
    for m in response.get("matches") or []:
        if not isinstance(m, dict):
            continue
        row = {c: m.get(c) for c in _COMMON_COLS}
        extra = {k: v for k, v in m.items() if k not in _COMMON_COLS}
        row[_DETAILS_COL] = (
            json.dumps(extra, separators=(",", ":"), default=str) if extra else ""
        )
        rows.append(row)
    out["matches"] = rows
    return out


def _unpack(decoded: dict) -> dict:
    """Inverse of _pack: re-expand `details` back onto each match."""
    rows = decoded.get("matches") or []
    restored: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        m = {k: v for k, v in row.items() if k != _DETAILS_COL and v is not None}
        raw = row.get(_DETAILS_COL)
        if isinstance(raw, str) and raw:
            try:
                extra = json.loads(raw)
            except ValueError:
                extra = {}
            if isinstance(extra, dict):
                m.update(extra)
        restored.append(m)
    decoded["matches"] = restored
    return decoded


def encode(tool: str, response: dict) -> tuple[str, str]:
    return sd.encode(
        tool, _pack(response), ENCODING_ID, _TABLES, _SCALARS,
        meta_keys=_META, json_blobs=_JSON, meta_json_blobs=_META_JSON,
    )


def decode(payload: str) -> dict:
    decoded = sd.decode(
        payload, _TABLES, _SCALARS,
        meta_keys=_META, json_blobs=_JSON, meta_json_blobs=_META_JSON,
        scalar_types=_SCALAR_TYPES,
    )
    return _unpack(decoded)
