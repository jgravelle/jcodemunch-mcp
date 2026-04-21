"""Compact encoder for get_blast_radius."""

from .. import schema_driven as sd

TOOLS = ("get_blast_radius",)
ENCODING_ID = "br2"

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
)
_NESTED = {"symbol": ["id", "name", "kind", "file", "line"]}
_META = ("timing_ms",)
_JSON = ("impact_by_depth", "callers", "cross_repo_confirmed")


def encode(tool: str, response: dict) -> tuple[str, str]:
    return sd.encode(
        tool, response, ENCODING_ID, _TABLES, _SCALARS,
        nested_dicts=_NESTED, meta_keys=_META, json_blobs=_JSON,
    )


def decode(payload: str) -> dict:
    return sd.decode(
        payload, _TABLES, _SCALARS,
        nested_dicts=_NESTED, meta_keys=_META, json_blobs=_JSON,
    )