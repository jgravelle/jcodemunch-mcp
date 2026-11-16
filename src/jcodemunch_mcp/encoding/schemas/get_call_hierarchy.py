"""Compact encoder for get_call_hierarchy."""

from .. import schema_driven as sd

TOOLS = ("get_call_hierarchy",)
# ch3: callers carry `call_lines` (a comma-joined cell) with its cap keys and
# `call_lines_unknown`, and the response carries `call_sites` and its scalars.
ENCODING_ID = "ch3"
LEGACY_ENCODING_IDS = ("ch1", "ch2")

_ROW_COLS = ["id", "name", "kind", "file", "line", "depth", "resolution", "via"]

_TABLES = [
    sd.TableSpec(
        key="callers",
        tag="c",
        cols=_ROW_COLS
        + [
            "call_lines",
            "call_lines_total",
            "call_lines_truncated",
            "call_lines_unknown",
        ],
        intern=["file", "id", "via"],
        types={
            "line": "int",
            "depth": "int",
            "call_lines_total": "int",
            "call_lines_truncated": "bool",
        },
    ),
    sd.TableSpec(
        key="callees",
        tag="e",
        cols=_ROW_COLS,
        intern=["file", "id", "via"],
        types={"line": "int", "depth": "int"},
    ),
    sd.TableSpec(
        key="dispatches",
        tag="d",
        cols=["id", "name", "kind", "file", "line", "depth", "resolution"],
        intern=["file", "id"],
        types={"line": "int", "depth": "int"},
    ),
]
_SCALARS = (
    "repo",
    "direction",
    "depth",
    "depth_reached",
    "caller_count",
    "callee_count",
    "direct_caller_count",
    "direct_callee_count",
    "call_sites_total",
    "call_sites_truncated",
    "call_sites_unknown_callers",
    "call_lines_unknown_count",
    "call_lines_basis",
)
_NESTED = {"symbol": ["id", "name", "kind", "file", "line"]}
_META = ("timing_ms", "methodology", "confidence_level", "source", "tip")
_META_JSON = ("verdict",)  # structured _meta that must survive compaction
_JSON = (
    "resolution_tiers",
    "callers_by_depth",
    "callees_by_depth",
    "call_sites",
)


def _flatten(response: dict) -> dict:
    """``call_lines`` [5, 9] rides as the cell "5,9"; an empty list as "".

    An unknown row (``call_lines`` None) also rides as "", and decodes back to
    None because its ``call_lines_unknown`` cell carries the reason.
    """
    callers = response.get("callers")
    if not callers:
        return response
    rows = []
    for row in callers:
        if isinstance(row.get("call_lines"), list):
            row = {**row, "call_lines": ",".join(str(n) for n in row["call_lines"])}
        rows.append(row)
    return {**response, "callers": rows}


def _unflatten(decoded: dict) -> dict:
    for row in decoded.get("callers") or []:
        cell = row.get("call_lines")
        if cell is None and "call_lines" not in row:
            continue
        if row.get("call_lines_unknown"):
            row["call_lines"] = None
        elif isinstance(cell, int):
            row["call_lines"] = [cell]
        elif isinstance(cell, str):
            row["call_lines"] = [int(x) for x in cell.split(",") if x.strip()]
        elif cell is None:
            row["call_lines"] = []
    return decoded


def encode(tool: str, response: dict) -> tuple[str, str]:
    return sd.encode(
        tool,
        _flatten(response),
        ENCODING_ID,
        _TABLES,
        _SCALARS,
        nested_dicts=_NESTED,
        meta_keys=_META,
        meta_json_blobs=_META_JSON,
        json_blobs=_JSON,
    )


def decode(payload: str) -> dict:
    return _unflatten(
        sd.decode(
            payload,
            _TABLES,
            _SCALARS,
            nested_dicts=_NESTED,
            meta_keys=_META,
            meta_json_blobs=_META_JSON,
            json_blobs=_JSON,
        )
    )
