"""Compact encoder for check_references.

As JSON every import match repeated `"specifier"`, `"names"` and
`"match_type"`, every content line repeated `"line"` and `"text"`, and each
enclosing symbol's id (`in.id`, `<file>::<name>#<kind>`) repeated its file's
whole path. No encoder could carry the nested groups, so the answer always
shipped as JSON.

On the wire there are three tables. `q` holds one row per identifier (its
counts and flags; one row in the singular form, one per entry of `results` in
a batch). `i` holds one row per import match and `c` one row per content line,
each tagged with its identifier's row and its file's group. A file is split
into directory and name, so a repeated directory is written once, and an
enclosing id that starts with its own file is sent without that prefix.

⚠ Encoding only: no list is shortened. The encoder decodes its own payload and
raises unless the result equals the response, `_meta` included, so a field this
schema does not carry ships as JSON instead of arriving with something missing.
"""

from __future__ import annotations

from .. import schema_driven as sd
from ..format import parse_header

TOOLS = ("check_references",)
ENCODING_ID = "cr2"
# cr1 carried no import cut. cr2 appends `import_total` and
# `import_truncated` AFTER cr1's columns, so a cr1 row reads by position
# unchanged, and decode leaves both keys out of a cr1 answer.
LEGACY_ENCODING_IDS = ("cr1",)

_Q_KEY = "__q__"
_I_KEY = "__i__"
_C_KEY = "__c__"
# Every `_meta` key without its own column (the server's `budget`,
# `secrets_redacted`, ...), carried as one JSON value so it is never dropped.
_META_EXTRA = "__meta_extra__"
# Joins `names` in one cell. A name holding it makes the self-check fail and
# the response ship as JSON.
_NAMES_SEP = "|"

_TABLES = [
    sd.TableSpec(
        key=_Q_KEY, tag="q",
        cols=["identifier", "is_referenced", "import_count", "content_count",
              "content_truncated", "has_content", "import_total", "import_truncated"],
        types={"is_referenced": "bool", "import_count": "int", "import_total": "int",
               "import_truncated": "bool", "content_count": "int",
               "content_truncated": "bool", "has_content": "bool"},
    ),
    sd.TableSpec(
        key=_I_KEY, tag="i",
        cols=["q", "g", "dir", "name", "specifier", "names", "match_type"],
        intern=["dir", "specifier"],
        types={"q": "int", "g": "int"},
    ),
    sd.TableSpec(
        key=_C_KEY, tag="c",
        cols=["q", "g", "dir", "name", "line", "text", "in_id", "in_line", "in_end"],
        intern=["dir"],
        types={"q": "int", "g": "int", "line": "int", "in_line": "int", "in_end": "int"},
    ),
]
_SCALARS = ("repo", "batch")
_META = ("timing_ms", "identifiers_checked", "tokens_saved", "total_tokens_saved")
_META_JSON = ("verdict",)
_SCALAR_TYPES = {
    "batch": "bool",
    "_meta.timing_ms": "float",
    "_meta.identifiers_checked": "int",
    "_meta.tokens_saved": "int",
    "_meta.total_tokens_saved": "int",
}
_PER_IDENT = ("identifier", "is_referenced", "import_count", "import_references",
              "import_references_total", "import_references_truncated",
              "content_count", "content_references", "content_truncated")


def _split(path: str) -> tuple[str, str]:
    directory, sep, name = path.rpartition("/")
    return directory + sep, name


def _flatten(response: dict) -> dict:
    batch = "results" in response
    entries = response.get("results") if batch else [response]
    out = {k: v for k, v in response.items() if k not in _PER_IDENT and k != "results"}
    out["batch"] = batch
    q_rows, i_rows, c_rows = [], [], []
    for qi, entry in enumerate(entries or []):
        q_rows.append({
            "identifier": entry.get("identifier"),
            "is_referenced": entry.get("is_referenced"),
            "import_count": entry.get("import_count"),
            "import_total": entry.get("import_references_total"),
            "import_truncated": entry.get("import_references_truncated"),
            "content_count": entry.get("content_count"),
            "content_truncated": entry.get("content_truncated"),
            "has_content": "content_references" in entry,
        })
        for gi, group in enumerate(entry.get("import_references") or []):
            d, n = _split(group.get("file") or "")
            for m in group.get("matches") or []:
                i_rows.append({
                    "q": qi, "g": gi, "dir": d, "name": n,
                    "specifier": m.get("specifier"),
                    "names": _NAMES_SEP.join(m.get("names") or []),
                    "match_type": m.get("match_type"),
                })
        for gi, group in enumerate(entry.get("content_references") or []):
            path = group.get("file") or ""
            d, n = _split(path)
            for m in group.get("matches") or []:
                enclosing = m.get("in") or {}
                in_id = enclosing.get("id") or ""
                if in_id.startswith(path + "::"):
                    in_id = in_id[len(path):]  # "::name#kind", the file is the row's own
                c_rows.append({
                    "q": qi, "g": gi, "dir": d, "name": n,
                    "line": m.get("line"), "text": m.get("text"),
                    "in_id": in_id, "in_line": enclosing.get("line"),
                    "in_end": enclosing.get("end_line"),
                })
    out[_Q_KEY], out[_I_KEY], out[_C_KEY] = q_rows, i_rows, c_rows
    carried = set(_META) | set(_META_JSON) | set(sd.CARRIED_META_JSON)
    extra = {k: v for k, v in (response.get("_meta") or {}).items() if k not in carried}
    if extra:
        out[_META_EXTRA] = extra
    return out


def _groups(rows: list[dict], qi: int, make) -> list[dict]:
    groups: list[dict] = []
    current = None
    for row in rows:
        if row.get("q") != qi:
            continue
        if current is None or row.get("g") != current[0]:
            current = (row.get("g"), {"file": (row.get("dir") or "") + (row.get("name") or ""), "matches": []})
            groups.append(current[1])
        current[1]["matches"].append(make(row, current[1]["file"]))
    return groups


def _import_match(row: dict, _file: str) -> dict:
    names = row.get("names") or ""
    return {
        "specifier": row.get("specifier") or "",
        "names": names.split(_NAMES_SEP) if names else [],
        "match_type": row.get("match_type"),
    }


def _content_match(row: dict, file: str) -> dict:
    match = {"line": row.get("line"), "text": row.get("text") or ""}
    in_id = row.get("in_id") or ""
    if in_id:
        match["in"] = {
            "id": file + in_id if in_id.startswith("::") else in_id,
            "line": row.get("in_line"),
            "end_line": row.get("in_end"),
        }
    return match


def _unflatten(decoded: dict, enc_id: str = ENCODING_ID) -> dict:
    q_rows = decoded.pop(_Q_KEY, None) or []
    i_rows = decoded.pop(_I_KEY, None) or []
    c_rows = decoded.pop(_C_KEY, None) or []
    batch = decoded.pop("batch", False) is True
    extra = decoded.pop(_META_EXTRA, None)
    if isinstance(extra, dict) and extra:
        decoded.setdefault("_meta", {}).update(extra)
    entries = []
    for qi, q in enumerate(q_rows):
        entry = {
            "identifier": q.get("identifier"),
            "is_referenced": q.get("is_referenced"),
            "import_count": q.get("import_count"),
            "import_references": _groups(i_rows, qi, _import_match),
            "import_references_total": q.get("import_total"),
            "import_references_truncated": q.get("import_truncated"),
            "content_count": q.get("content_count"),
        }
        if enc_id == "cr1":
            del entry["import_references_total"], entry["import_references_truncated"]
        if q.get("has_content"):
            entry["content_references"] = _groups(c_rows, qi, _content_match)
            entry["content_truncated"] = q.get("content_truncated")
        entries.append(entry)
    if batch:
        decoded["results"] = entries
    elif entries:
        decoded.update(entries[0])
    return decoded


def encode(tool: str, response: dict) -> tuple[str, str]:
    payload, enc_id = sd.encode(
        tool, _flatten(response), ENCODING_ID, _TABLES, _SCALARS,
        meta_keys=_META, meta_json_blobs=_META_JSON, json_blobs=(_META_EXTRA,),
    )
    if decode(payload) != response:
        raise ValueError("check_references: the compact form does not reproduce the response")
    return payload, enc_id


def decode(payload: str) -> dict:
    decoded = sd.decode(
        payload, _TABLES, _SCALARS, meta_keys=_META, meta_json_blobs=_META_JSON,
        json_blobs=(_META_EXTRA,), scalar_types=_SCALAR_TYPES,
    )
    enc_id = parse_header(payload.partition("\n")[0]).get("enc", ENCODING_ID)
    return _unflatten(decoded, enc_id)
