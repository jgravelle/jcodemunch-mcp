"""Compact encoder for get_blast_radius."""

from .. import schema_driven as sd

TOOLS = ("get_blast_radius",)
ENCODING_ID = "br6"
# br3 (main): br2 + caller_count, direct_caller_count, callers_by_depth.
# br4 (L-262): br3 + the body-level `incomplete` and `dynamic_imports_unfollowed`
# disclosures, which br3 dropped as undeclared top-level dicts.
# br5 (L-58): br4 + `cross_repo_confirmed_count` and the `include_decisions`
# `decisions` dict, both dropped because no list here named them.
# br6: br5 + `callers_truncated`, the callers row cap's disclosure. Scalars are
# read by name, so a br5 payload decodes without it.
# ⚠ A key the tool sets and this schema does not declare is dropped with
# nothing raised; tests/test_blast_radius_compact_carries_every_key.py reads
# the producer's source and fails until the schema declares the new key.
LEGACY_ENCODING_IDS = ("br2", "br3", "br4", "br5")

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
    "caller_count", "direct_caller_count", "cross_repo_confirmed_count",
    "callers_truncated",
)
_NESTED = {"symbol": ["id", "name", "kind", "file", "line"]}
_META = ("timing_ms",)
_META_JSON = ("verdict",)  # structured _meta that must survive compaction
_JSON = (
    "impact_by_depth", "callers", "callers_by_depth", "cross_repo_confirmed",
    "incomplete", "dynamic_imports_unfollowed", "decisions",
)


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