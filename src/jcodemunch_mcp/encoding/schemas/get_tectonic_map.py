"""Compact encoder for get_tectonic_map."""

from .. import schema_driven as sd

TOOLS = ("get_tectonic_map",)
ENCODING_ID = "tm2"

_TABLES = [
    sd.TableSpec(
        key="plates",
        tag="p",
        cols=["plate_id", "anchor", "file_count", "cohesion", "majority_directory", "drifter_count", "nexus_alert"],
        intern=["anchor"],
        types={"plate_id": "int", "file_count": "int", "cohesion": "float", "drifter_count": "int", "nexus_alert": "bool"},
    ),
    sd.TableSpec(
        key="drifter_summary",
        tag="z",
        cols=["file", "current_directory", "belongs_with", "plate_anchor"],
        intern=["file"],
    ),
]
_SCALARS = ("repo", "plate_count", "file_count")
_META = ("timing_ms", "methodology")
_META_JSON = ("verdict",)  # structured _meta that must survive compaction
_JSON = ("signals_used", "signals_withheld", "isolated_files")


def _prune_optional_plate_fields(decoded: dict) -> dict:
    plates = decoded.get("plates")
    if not isinstance(plates, list):
        return decoded
    for plate in plates:
        if not isinstance(plate, dict):
            continue
        for key in ("drifter_count", "nexus_alert"):
            if plate.get(key) is None:
                plate.pop(key, None)
    return decoded


def encode(tool: str, response: dict) -> tuple[str, str]:
    return sd.encode(
        tool, response, ENCODING_ID, _TABLES, _SCALARS,
        meta_keys=_META, meta_json_blobs=_META_JSON, json_blobs=_JSON,
    )


def decode(payload: str) -> dict:
    decoded = sd.decode(
        payload, _TABLES, _SCALARS, meta_keys=_META, meta_json_blobs=_META_JSON, json_blobs=_JSON,
    )
    return _prune_optional_plate_fields(decoded)
