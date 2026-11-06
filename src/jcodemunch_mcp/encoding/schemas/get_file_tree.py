"""Compact encoder for get_file_tree.

The tool returns a nested tree of directory and file nodes. As JSON every
node repeats its keys and every file repeats its whole directory path, and
no encoder could carry the nesting, so the tree always shipped as JSON.

On the wire each file is one row: its directory, its name, its language, its
symbol count and (when asked for) its summary. Directories and languages
repeat, so the legend writes each once. Directory nodes are not sent: they
follow from the file paths, and decode rebuilds the tree with the tool's own
nesting rule (`tools.get_file_tree.nest_file_nodes`), not a copy of it.

⚠ Encoding only. The encoder decodes its own payload and raises unless the
result equals the response, so a field this schema does not carry, or a tree
it cannot rebuild, ships as JSON instead of arriving with something missing.
"""

from __future__ import annotations

from .. import schema_driven as sd

TOOLS = ("get_file_tree",)
ENCODING_ID = "ft1"

_ROWS_KEY = "__rows__"

_TABLES = [
    sd.TableSpec(
        key=_ROWS_KEY,
        tag="f",
        cols=["dir", "name", "language", "symbol_count", "summary"],
        intern=["dir", "language"],
        types={"symbol_count": "int"},
    ),
]
_SCALARS = (
    "repo", "path_prefix", "truncated", "total_file_count", "hint",
    "summaries_included",
)
_META = ("timing_ms", "file_count", "tokens_saved", "total_tokens_saved")
_META_JSON = ("verdict",)
_SCALAR_TYPES = {
    "truncated": "bool",
    "total_file_count": "int",
    "summaries_included": "bool",
    "_meta.timing_ms": "float",
    "_meta.file_count": "int",
    "_meta.tokens_saved": "int",
    "_meta.total_tokens_saved": "int",
}


def _file_nodes(tree: list) -> list[dict]:
    """The file nodes of a tree, in tree order."""
    out: list[dict] = []
    for node in tree:
        if node.get("type") == "dir":
            out.extend(_file_nodes(node.get("children") or []))
        else:
            out.append(node)
    return out


def _flatten(response: dict) -> dict:
    out = {k: v for k, v in response.items() if k != "tree"}
    nodes = _file_nodes(response.get("tree") or [])
    rows = []
    for node in nodes:
        path = node["path"]
        directory, sep, name = path.rpartition("/")
        rows.append({
            "dir": directory + sep,
            "name": name,
            "language": node.get("language", ""),
            "symbol_count": node.get("symbol_count"),
            "summary": node.get("summary", ""),
        })
    out[_ROWS_KEY] = rows
    out["summaries_included"] = any("summary" in n for n in nodes)
    return out


def _unflatten(decoded: dict) -> dict:
    from ...tools.get_file_tree import nest_file_nodes

    rows = decoded.pop(_ROWS_KEY, None) or []
    summaries = decoded.pop("summaries_included", False) is True
    nodes = []
    for row in rows:
        node = {
            "path": (row.get("dir") or "") + (row.get("name") or ""),
            "type": "file",
            "language": row.get("language") or "",
            "symbol_count": row.get("symbol_count"),
        }
        if summaries:
            node["summary"] = row.get("summary") or ""
        nodes.append(node)
    # The format writes an empty string and None alike; the tool's prefix is
    # always a string, "" for the whole repository.
    if "path_prefix" in decoded and decoded["path_prefix"] is None:
        decoded["path_prefix"] = ""
    decoded["tree"] = nest_file_nodes(nodes, decoded.get("path_prefix") or "")
    return decoded


def _same(response: dict, back: dict) -> bool:
    body = {k: v for k, v in response.items() if not k.startswith("_")}
    back_body = {k: v for k, v in back.items() if not k.startswith("_")}
    if body != back_body:
        return False
    meta = response.get("_meta") or {}
    back_meta = back.get("_meta") or {}
    return all(back_meta.get(k) == meta[k] for k in _META if k in meta)


def encode(tool: str, response: dict) -> tuple[str, str]:
    payload, enc_id = sd.encode(
        tool, _flatten(response), ENCODING_ID, _TABLES, _SCALARS,
        meta_keys=_META, meta_json_blobs=_META_JSON,
    )
    if not _same(response, decode(payload)):
        raise ValueError("get_file_tree: the compact form does not reproduce the response")
    return payload, enc_id


def decode(payload: str) -> dict:
    decoded = sd.decode(
        payload, _TABLES, _SCALARS, meta_keys=_META, meta_json_blobs=_META_JSON,
        scalar_types=_SCALAR_TYPES,
    )
    return _unflatten(decoded)
