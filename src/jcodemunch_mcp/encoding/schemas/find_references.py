"""Compact encoder for find_references."""

from .. import schema_driven as sd

TOOLS = ("find_references",)
ENCODING_ID = "fr2"

_ROWS_KEY = "__rows__"
_EMPTY_GROUPS_KEY = "__empty_groups__"
# (L-325) An empty group's row verdicts, {file: {verdict: value}}. Written only
# when one is present, so an answer without runtime evidence keeps its bytes.
_EMPTY_VERDICTS_KEY = "__empty_group_verdicts__"

_TABLES = [
    sd.TableSpec(
        key=_ROWS_KEY,
        tag="r",
        cols=["file", "specifier", "match_type"],
        intern=["file", "specifier"],
    ),
]
_SCALARS = ("repo", "identifier", "reference_count", "note")
_META = ("timing_ms", "truncated", "tokens_saved", "total_tokens_saved")
_META_JSON = ("verdict",)  # structured _meta that must survive compaction
_JSON = ("results", _EMPTY_GROUPS_KEY, _EMPTY_VERDICTS_KEY)


def _group_verdicts(group: dict) -> dict:
    """The row verdicts a references group carries (L-325)."""
    return {
        k: group[k]
        for k, default in sd.UNIVERSAL_ROW_VERDICTS.items()
        if group.get(k) not in (None, "", default)
    }


def _flatten(response: dict) -> dict:
    """Replace nested references[].matches[] with flat rows.

    A group's row verdicts (`_runtime_confidence`, stamped per file) ride on each
    of its rows, where the shared encoder carries them (L-325).
    """
    out = {k: v for k, v in response.items() if k != "references"}
    rows = []
    empty_groups: list[str] = []
    empty_verdicts: dict[str, dict] = {}
    for group in response.get("references") or []:
        if not isinstance(group, dict):
            continue
        file_path = group.get("file")
        matches = group.get("matches") or []
        verdicts = _group_verdicts(group)
        if not matches:
            if isinstance(file_path, str):
                empty_groups.append(file_path)
                if verdicts:
                    empty_verdicts[file_path] = verdicts
            continue
        for m in matches:
            if not isinstance(m, dict):
                continue
            rows.append(
                {
                    "file": file_path,
                    "specifier": m.get("specifier", ""),
                    "match_type": m.get("match_type", ""),
                    **verdicts,
                }
            )
    out[_ROWS_KEY] = rows
    if empty_groups:
        out[_EMPTY_GROUPS_KEY] = empty_groups
    if empty_verdicts:
        out[_EMPTY_VERDICTS_KEY] = empty_verdicts
    return out


def _regroup(decoded: dict) -> dict:
    """Inverse of _flatten: rebuild references list."""
    rows = decoded.pop(_ROWS_KEY, None) or []
    empty_groups = decoded.pop(_EMPTY_GROUPS_KEY, None) or []
    empty_verdicts = decoded.pop(_EMPTY_VERDICTS_KEY, None) or {}
    groups: dict[str, list[dict]] = {}
    verdicts: dict[str, dict] = {
        f: v for f, v in empty_verdicts.items() if isinstance(f, str) and isinstance(v, dict)
    }
    order: list[str] = []
    for file_path in empty_groups:
        if not isinstance(file_path, str):
            continue
        if file_path not in groups:
            groups[file_path] = []
            order.append(file_path)
    for row in rows:
        file_path = row.get("file")
        if not isinstance(file_path, str):
            continue
        match = {"specifier": row.get("specifier", ""), "match_type": row.get("match_type", "")}
        for k in sd.UNIVERSAL_ROW_VERDICTS:
            if row.get(k) not in (None, ""):
                verdicts.setdefault(file_path, {}).setdefault(k, row[k])
        if file_path not in groups:
            groups[file_path] = []
            order.append(file_path)
        groups[file_path].append(match)
    decoded["references"] = [{"file": f, "matches": groups[f], **verdicts.get(f, {})} for f in order]
    return decoded


def encode(tool: str, response: dict) -> tuple[str, str]:
    if "references" not in response:
        return sd.encode(tool, response, ENCODING_ID, _TABLES, _SCALARS, meta_keys=_META, meta_json_blobs=_META_JSON, json_blobs=_JSON)
    return sd.encode(tool, _flatten(response), ENCODING_ID, _TABLES, _SCALARS, meta_keys=_META, meta_json_blobs=_META_JSON, json_blobs=_JSON)


def decode(payload: str) -> dict:
    decoded = sd.decode(payload, _TABLES, _SCALARS, meta_keys=_META, meta_json_blobs=_META_JSON, json_blobs=_JSON)
    if _ROWS_KEY in decoded:
        return _regroup(decoded)
    return decoded
