"""Generic fallback encoder — shape-sniffer for tools without a custom schema.

Walks a response dict once, separates scalars from array-of-dict tables, interns
repeated path-like strings, and emits a round-trippable MUNCH payload. Original
keys, column order, and per-column types are preserved in an embedded schema
line so decode fully reconstructs the response.

Round-trip guarantees:
    * Top-level scalars: str/int/float/bool/None preserved by type.
    * Homogeneous list-of-dict tables: original key, column order, and column
      types preserved; repeated path prefixes shared via legend.
    * Nested dict whose values are themselves tables: flattened with dotted key.
    * Anything else (mixed arrays, deeply nested dicts) falls through as a JSON
      literal scalar — preserved but uncompressed.

When shape detection fails entirely the dispatcher's savings gate will discard
the compact output and re-emit JSON, so this encoder is safe to fail-open.
"""

from __future__ import annotations

import json as _json
from typing import Any

from .format import (
    Legends,
    assemble,
    parse_header,
    parse_scalars,
    read_table,
    split_sections,
    write_header,
    write_scalars,
    write_table,
)

ENCODING_ID = "gen1"

# One-char tags for tables. Lowercase letters minus those likely to collide with
# scalar keys starting the first line of a scalar block.
_TABLE_TAGS = "tuvwxyzabcdefghijklmnopqrs"
_MIN_TABLE_ROWS = 2
_RESERVED_SCALAR_KEYS = ("__tables", "__stypes")


# Percent-encoding of the schema-embed separators so keys/column-names
# containing ``:``, ``|``, ``,`` (or the escape itself) survive round-trip.
# Audit finding F11.
_SCHEMA_ESCAPE = {"%": "%25", ":": "%3A", "|": "%7C", ",": "%2C"}
_SCHEMA_UNESCAPE = {v: k for k, v in _SCHEMA_ESCAPE.items()}


def _escape_schema(s: str) -> str:
    out = s.replace("%", "%25")
    out = out.replace(":", "%3A").replace("|", "%7C").replace(",", "%2C")
    return out


def _unescape_schema(s: str) -> str:
    # Decode in reverse order of escape — %25 last so intermediate %3A / %7C
    # / %2C that came from the original input don't get re-expanded.
    out = s.replace("%3A", ":").replace("%7C", "|").replace("%2C", ",")
    out = out.replace("%25", "%")
    return out


def _scalar_type(v: Any) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    return "str"


def _is_homogeneous_dict_array(value: Any) -> bool:
    if not isinstance(value, list) or len(value) < _MIN_TABLE_ROWS:
        return False
    if not all(isinstance(x, dict) for x in value):
        return False
    first_keys = tuple(value[0].keys())
    return all(tuple(x.keys()) == first_keys for x in value)


def _infer_col_types(rows: list[dict], cols: list[str]) -> dict[str, str]:
    """Infer a type per column by scanning non-null samples."""
    types: dict[str, str] = {}
    for c in cols:
        # ⚠ A cell holding a list or dict goes on the wire as JSON and comes
        # back as one (L-312: it was written with `repr` and decoded as a
        # string). Checked over EVERY row before the scalar scan below, which
        # stops at its second distinct type and would miss a later list.
        # Every cell of such a column is JSON, so a string beside it stays a
        # string.
        if any(isinstance(r.get(c), (list, dict)) for r in rows):
            types[c] = "json"
            continue
        seen: set[str] = set()
        for r in rows:
            v = r.get(c)
            if v is None:
                continue
            if isinstance(v, bool):
                seen.add("bool")
            elif isinstance(v, int):
                seen.add("int")
            elif isinstance(v, float):
                seen.add("float")
            else:
                seen.add("str")
            if len(seen) > 1:
                break
        if not seen or "str" in seen:
            types[c] = "str"
        elif seen == {"bool"}:
            types[c] = "bool"
        elif seen == {"int"}:
            types[c] = "int"
        elif seen == {"float"} or seen == {"int", "float"}:
            types[c] = "float"
        else:
            types[c] = "str"
    return types


def _collect_prefixes(samples: list[str], min_len: int = 6) -> dict[str, int]:
    """Count path-like prefixes across samples for legend promotion."""
    buckets: dict[str, int] = {}
    for s in samples:
        if not isinstance(s, str) or len(s) < min_len:
            continue
        pos = 0
        while True:
            nxt = min(
                (i for i in (s.find("/", pos + 1), s.find("\\", pos + 1)) if i > 0),
                default=-1,
            )
            if nxt < 0 or nxt >= len(s) - 1:
                break
            prefix = s[: nxt + 1]
            if len(prefix) >= min_len:
                buckets[prefix] = buckets.get(prefix, 0) + 1
            pos = nxt
    return buckets


def _coerce(raw: str, hint: str) -> Any:
    if raw == "":
        return None
    if hint == "json":
        return _json.loads(raw)
    if hint == "bool":
        return raw == "T"
    if hint == "int":
        try:
            return int(raw)
        except ValueError:
            return raw
    if hint == "float":
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw


def _walk_tables(response: dict) -> tuple[list[tuple[str, list[dict]]], dict[str, Any]]:
    """Return (tables, leftover_scalars).

    Tables are (flat_key, rows). Nested dicts whose sole notable content is a
    list-of-dicts get flattened with a dotted key. Everything else lands in
    leftover_scalars where the main encode path decides scalar vs JSON blob.
    """
    tables: list[tuple[str, list[dict]]] = []
    leftovers: dict[str, Any] = {}
    for key, value in response.items():
        if key in _RESERVED_SCALAR_KEYS:
            continue
        if _is_homogeneous_dict_array(value):
            tables.append((key, value))
            continue
        if isinstance(value, dict):
            inner_tables: list[tuple[str, list[dict]]] = []
            inner_rest: dict[str, Any] = {}
            for sub_k, sub_v in value.items():
                if _is_homogeneous_dict_array(sub_v):
                    inner_tables.append((f"{key}.{sub_k}", sub_v))
                else:
                    inner_rest[sub_k] = sub_v
            if inner_tables:
                tables.extend(inner_tables)
                if inner_rest:
                    leftovers[key] = inner_rest
                continue
        leftovers[key] = value
    return tables, leftovers


def encode(tool_name: str, response: dict) -> tuple[str, str]:
    tables_raw, leftovers = _walk_tables(response)

    # Cap table count at available tags; excess collapses back into JSON blobs.
    if len(tables_raw) > len(_TABLE_TAGS):
        overflow = tables_raw[len(_TABLE_TAGS):]
        tables_raw = tables_raw[:len(_TABLE_TAGS)]
        for k, v in overflow:
            leftovers[k] = v

    tables: list[tuple[str, str, list[str], dict[str, str], list[dict]]] = []
    for i, (key, rows) in enumerate(tables_raw):
        tag = _TABLE_TAGS[i]
        cols = list(rows[0].keys())
        types = _infer_col_types(rows, cols)
        tables.append((tag, key, cols, types, rows))

    # Build path legend from string values across table rows.
    legend = Legends(prefix="@")
    all_strings: list[str] = []
    for _, _, cols, types, rows in tables:
        for row in rows:
            for c in cols:
                if types[c] != "str":
                    continue
                v = row.get(c)
                if isinstance(v, str) and v:
                    legend.observe(v)
                    all_strings.append(v)
    for prefix, count in _collect_prefixes(all_strings).items():
        if count >= 2:
            existing = legend._counts.get(prefix, 0)
            legend._counts[prefix] = max(existing, count)
            if prefix not in legend._order:
                legend._order.append(prefix)
    legend.finalize(min_uses=2, min_chars_saved=2)

    # Scalar section
    scalar_out: dict[str, Any] = {}
    scalar_types: dict[str, str] = {}
    for k, v in leftovers.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            scalar_out[k] = v
            if v is not None and not isinstance(v, str):
                scalar_types[k] = _scalar_type(v)
        else:
            scalar_out[f"__json.{k}"] = _json.dumps(v, separators=(",", ":"))
    if scalar_types:
        scalar_out["__stypes"] = "|".join(f"{k}:{t}" for k, t in scalar_types.items())

    # Embed table schema: tag:key:col1|col2|...:type1|type2|...
    # Keys/columns may contain ':' or '|' (e.g. a dotted key like
    # "stats:by_file"). Percent-encode those separators so the decoder can
    # split cleanly (audit finding F11).
    schema_parts: list[str] = []
    for tag, key, cols, types, _ in tables:
        type_list = "|".join(_escape_schema(types[c]) for c in cols)
        cols_enc = "|".join(_escape_schema(c) for c in cols)
        schema_parts.append(
            f"{_escape_schema(tag)}:{_escape_schema(key)}:{cols_enc}:{type_list}"
        )
    scalar_out["__tables"] = ",".join(schema_parts)

    sections: list[str] = []
    leg_text = legend.write()
    if leg_text:
        sections.append(leg_text)
    sections.append(write_scalars(scalar_out))

    for tag, _key, cols, types, rows in tables:
        data_rows: list[list[Any]] = []
        for r in rows:
            encoded_row: list[Any] = []
            for c in cols:
                v = r.get(c)
                if types[c] == "str" and isinstance(v, str):
                    v = legend.encode_prefix(v)
                elif types[c] == "json" and v is not None:
                    v = _json.dumps(v, separators=(",", ":"))
                encoded_row.append(v)
            data_rows.append(encoded_row)
        sections.append(write_table(tag, data_rows))

    header = write_header(tool_name, ENCODING_ID)
    return assemble(header, *sections), ENCODING_ID


def decode(payload: str) -> dict:
    head, blocks = split_sections(payload)
    parse_header(head)

    legend = Legends(prefix="@")
    scalar_block: str | None = None
    table_blocks: list[str] = []
    for b in blocks:
        lines = b.splitlines()
        if not lines:
            continue
        first = lines[0]
        is_table_row = len(first) >= 2 and first[1] == "," and first[0] in _TABLE_TAGS
        if first.startswith("@") and "=" in first and not is_table_row:
            legend = Legends.read(b, prefix="@")
        elif is_table_row:
            table_blocks.append(b)
        elif scalar_block is None and "=" in first:
            scalar_block = b
        else:
            table_blocks.append(b)

    raw_scalars = parse_scalars(scalar_block) if scalar_block else {}
    schema_text = raw_scalars.pop("__tables", "")
    stypes_text = raw_scalars.pop("__stypes", "")
    scalar_type_map: dict[str, str] = {}
    for part in [p for p in stypes_text.split("|") if p]:
        name, _, hint = part.partition(":")
        if name and hint:
            scalar_type_map[name] = hint

    schemas: list[tuple[str, str, list[str], list[str]]] = []
    for part in [p for p in schema_text.split(",") if p]:
        pieces = part.split(":")
        if len(pieces) < 3:
            continue
        tag = _unescape_schema(pieces[0])
        key = _unescape_schema(pieces[1])
        col_spec = pieces[2]
        type_spec = pieces[3] if len(pieces) >= 4 else ""
        cols = [_unescape_schema(c) for c in col_spec.split("|")] if col_spec else []
        types = (
            [_unescape_schema(t) for t in type_spec.split("|")]
            if type_spec else ["str"] * len(cols)
        )
        if len(types) < len(cols):
            types = types + ["str"] * (len(cols) - len(types))
        schemas.append((tag, key, cols, types))

    result: dict[str, Any] = {}
    for k, v in raw_scalars.items():
        if k.startswith("__json."):
            real = k[len("__json."):]
            try:
                result[real] = _json.loads(v)
            except Exception:
                result[real] = v
        elif k in scalar_type_map:
            result[k] = _coerce(v, scalar_type_map[k])
        else:
            result[k] = v

    for tag, key, cols, types in schemas:
        rows_out: list[dict[str, Any]] = []
        for block in table_blocks:
            rows = read_table(block, tag)
            if not rows:
                continue
            for r in rows:
                row_dict: dict[str, Any] = {}
                for i, c in enumerate(cols):
                    raw = r[i] if i < len(r) else ""
                    hint = types[i] if i < len(types) else "str"
                    if hint == "str" and isinstance(raw, str):
                        raw = legend.decode_prefix(raw)
                    row_dict[c] = _coerce(raw, hint)
                rows_out.append(row_dict)
        # Support dotted keys created by nested-dict flattening. When parent
        # was already restored from a `__json.<parent>` scalar as a non-dict,
        # wrap it so we don't silently drop the nested table (audit F5).
        if "." in key:
            parent, _, child = key.partition(".")
            existing = result.get(parent)
            if not isinstance(existing, dict):
                if existing is not None:
                    result[parent] = {"_scalar": existing}
                else:
                    result[parent] = {}
            result[parent][child] = rows_out
        else:
            result[key] = rows_out

    return result
