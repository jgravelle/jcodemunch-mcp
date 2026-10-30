"""Compact encoder for get_blast_radius."""

from .. import schema_driven as sd

TOOLS = ("get_blast_radius",)
ENCODING_ID = "br3"  # br2 + caller_count, direct_caller_count, callers_by_depth
LEGACY_ENCODING_IDS = ("br2",)

_TABLES = [
    sd.TableSpec(
        key="confirmed",
        tag="c",
        cols=["file", "references", "has_test_reach"],
        intern=["file"],
        types={"references": "int", "has_test_reach": "bool"},
    ),
    sd.TableSpec(
        key="potential",
        tag="p",
        cols=["file", "reason"],
        intern=["file"],
    ),
]
_SCALARS = (
    "repo", "depth", "importer_count", "confirmed_count", "potential_count",
    "direct_dependents_count", "overall_risk_score",
    "caller_count", "direct_caller_count",
)
_NESTED = {"symbol": ["id", "name", "kind", "file", "line"]}
_META = ("timing_ms",)
_META_JSON = ("verdict",)  # structured _meta that must survive compaction
_JSON = ("impact_by_depth", "callers", "callers_by_depth", "cross_repo_confirmed")


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