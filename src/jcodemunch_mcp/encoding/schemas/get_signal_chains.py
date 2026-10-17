"""Compact encoder for get_signal_chains."""

from .. import schema_driven as sd

TOOLS = ("get_signal_chains",)
ENCODING_ID = "sc2"

_TABLES = [
    sd.TableSpec(
        key="chains",
        tag="c",
        cols=[
            "gateway", "gateway_name", "kind", "label", "depth", "reach",
            "file_count", "chain_reach", "depth_from_gateway",
        ],
        intern=["gateway"],
        types={"depth": "int", "reach": "int", "file_count": "int", "chain_reach": "int", "depth_from_gateway": "int"},
    ),
]
_SCALARS = (
    "repo", "gateway_count", "chain_count", "orphan_symbols", "orphan_symbol_pct",
    "symbol", "symbol_id", "on_no_chain", "gateway_warning",
)
_DISCOVERY_META = (
    "timing_ms", "max_depth", "include_tests",
    "symbols_on_chains", "total_functions_methods", "callee_resolutions",
)
_META_JSON = ("verdict", "flow_edges")  # structured _meta that must survive compaction
_LOOKUP_META = ("timing_ms", "total_gateways", "callee_resolutions")
_NO_GATEWAY_META = ("timing_ms",)
_META = tuple(dict.fromkeys(_DISCOVERY_META + _LOOKUP_META + _NO_GATEWAY_META))
_JSON = ("kind_summary",)


def _meta_keys(response: dict) -> tuple[str, ...]:
    if "gateway_warning" in response:
        return _NO_GATEWAY_META
    if "symbol" in response or "symbol_id" in response:
        return _LOOKUP_META
    return _DISCOVERY_META


def encode(tool: str, response: dict) -> tuple[str, str]:
    return sd.encode(
        tool, response, ENCODING_ID, _TABLES, _SCALARS,
        meta_keys=_meta_keys(response), meta_json_blobs=_META_JSON, json_blobs=_JSON,
    )


def decode(payload: str) -> dict:
    return sd.decode(
        payload, _TABLES, _SCALARS, meta_keys=_META, meta_json_blobs=_META_JSON, json_blobs=_JSON,
    )
