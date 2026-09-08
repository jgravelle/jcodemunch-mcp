"""Compact encoder for search_symbols."""

from .. import schema_driven as sd

TOOLS = ("search_symbols",)
ENCODING_ID = "ss1"

_TABLES = [
    sd.TableSpec(
        key="results",
        tag="s",
        cols=["id", "name", "kind", "file", "line", "score", "signature", "summary"],
        intern=["file", "id"],
        types={"line": "int", "score": "float"},
    ),
]
_SCALARS = ("result_count", "query", "repo")
_META = (
    "timing_ms", "truncated", "total_symbols", "tokens_saved",
    "total_tokens_saved", "fusion", "channels",
)
# structured _meta that must survive compaction. exact_match joined verdict in
# v1.108.173: a dict left off this list is SILENTLY DROPPED by the encoder, which
# is exactly how the whole verdict contract went invisible in v1.108.169.
# semantic_topup (CF-66): the symbols a failed lazy-embedding batch left scored
# lexically only, with the cause; a dict, so it must be listed here to survive.
_META_JSON = ("verdict", "exact_match", "semantic_topup")


def encode(tool: str, response: dict) -> tuple[str, str]:
    return sd.encode(tool, response, ENCODING_ID, _TABLES, _SCALARS, meta_keys=_META, meta_json_blobs=_META_JSON)


def decode(payload: str) -> dict:
    return sd.decode(payload, _TABLES, _SCALARS, meta_keys=_META, meta_json_blobs=_META_JSON)
