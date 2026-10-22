"""Compact encoder for get_call_hierarchy."""

from .. import schema_driven as sd

TOOLS = ("get_call_hierarchy",)
ENCODING_ID = "ch2"  # ch1 + `via` on callers/callees, direct and per-depth counts
LEGACY_ENCODING_IDS = ("ch1",)

_TABLES = [
    sd.TableSpec(
        key="callers",
        tag="c",
        cols=["id", "name", "kind", "file", "line", "depth", "resolution", "via"],
        intern=["file", "id", "via"],
        types={"line": "int", "depth": "int"},
    ),
    sd.TableSpec(
        key="callees",
        tag="e",
        cols=["id", "name", "kind", "file", "line", "depth", "resolution", "via"],
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
    "repo", "direction", "depth", "depth_reached", "caller_count", "callee_count",
    "direct_caller_count", "direct_callee_count",
)
_NESTED = {"symbol": ["id", "name", "kind", "file", "line"]}
_META = ("timing_ms", "methodology", "confidence_level", "source", "tip")
_META_JSON = ("verdict",)  # structured _meta that must survive compaction
_JSON = ("resolution_tiers", "callers_by_depth", "callees_by_depth")


def encode(tool: str, response: dict) -> tuple[str, str]:
    return sd.encode(
        tool, response, ENCODING_ID, _TABLES, _SCALARS,
        nested_dicts=_NESTED, meta_keys=_META, meta_json_blobs=_META_JSON, json_blobs=_JSON,
    )


def decode(payload: str) -> dict:
    return sd.decode(
        payload, _TABLES, _SCALARS,
        nested_dicts=_NESTED, meta_keys=_META, meta_json_blobs=_META_JSON, json_blobs=_JSON,
    )
