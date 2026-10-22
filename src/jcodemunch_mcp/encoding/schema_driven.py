"""Schema-driven encoder helper.

Tier-1 per-tool encoders declare a small schema and reuse the helper to
produce round-trippable MUNCH payloads. Each encoder module is ~30 lines.

Schema shape:
    SCALARS: list of top-level scalar keys to carry through
    TABLES:  list of TableSpec describing list-of-dict fields
    META:    list of _meta keys to preserve (rest of _meta is dropped unless
             passthrough=True, which copies it verbatim)

Nested single-dict fields (like call_hierarchy.symbol) are flattened with a
prefix — declare via NESTED_DICTS mapping {key: [subkeys...]}.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

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

# Structured `_meta` keys every encoder must preserve, whether or not its own
# schema remembered to list them.
#
# A per-schema allowlist is the right default for tool-specific payloads and the
# wrong one for CONTRACT keys, because the failure is silent and asymmetric: the
# encoder drops the key, the response still looks complete, and the agent loses
# exactly the field that told it not to trust the answer. That is how the whole
# verdict contract went invisible in v1.108.169, and adding a key to 45 tuples
# only guarantees the 46th encoder forgets it.
#
# Add a key here ONLY when losing it would make a response misleading rather
# than merely less informative.
UNIVERSAL_META_JSON = frozenset({
    "verdict",            # retrieval verdict incl. absence evidence_ref
    "ignored_arguments",  # v1.108.175: part of the call was silently discarded
    # #377 hardening item 11: the carrier the server re-attaches when the
    # verdict itself was filtered out. An encoder that drops it hands back a
    # response that looks complete while the proof token has vanished.
    "absence_evidence",
    # #377 phase 2: the evidence-receipt carrier. It is the ONLY thing in the
    # response that names the receipt — the body lives at munch://evidence/<id>
    # and is read on demand — so an encoder that drops it silently converts an
    # opted-in call back into one that proved nothing.
    "receipts",
})


# Top-level keys every encoder must carry, the body-level twin of
# UNIVERSAL_META_JSON. A warning in the answer was dropped by every schema that
# did not list it, so on the default `server_output: adaptive`, which picks the
# compact form whenever it is smaller, `search_symbols`' "Low-confidence
# matches ... Verify before claiming this feature exists" reached the agent as
# bare rows. Any top-level key whose name contains "warning" is carried too, so
# a warning a tool adds later needs no schema edit.
UNIVERSAL_BODY_JSON = frozenset({
    "negative_evidence",  # search_symbols: the weak-match or no-match verdict
})


def is_universal_body_key(key: str) -> bool:
    return key in UNIVERSAL_BODY_JSON or "warning" in key.lower()


@dataclass
class TableSpec:
    key: str                       # response dict key holding list[dict]
    tag: str                       # 1-char table tag
    cols: list[str] = field(default_factory=list)  # column order
    intern: list[str] = field(default_factory=list)  # cols to legend-intern
    types: dict[str, str] = field(default_factory=dict)  # col -> type hint


def _type_of(v: Any) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    return "str"


def _coerce(raw: str, hint: str) -> Any:
    if raw == "":
        return None
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


def _check_undeclared_tables(
    tool: str,
    response: dict,
    tables: list[TableSpec],
    nested_dicts: dict[str, list[str]],
    json_blobs: Iterable[str],
    allow_undeclared: Iterable[str],
) -> None:
    """Fail closed when a producer emits a table no schema declares (#555).

    The sibling guard below catches a schema that disagrees with its producer
    about COLUMNS: rows exist, no declared column populated. It is structurally
    blind to a disagreement about the KEY, because ``response.get(t.key, [])``
    returns ``[]``, ``out_rows`` stays empty and the check never runs. That was
    #553 -- ``search_ast`` declared ``results`` while the tool returned
    ``matches``, and served an empty table for every language and preset with
    nothing raised.

    ⚠ Runs on the dict handed to ``encode``, which is POST-transform by
    construction. A schema that pre-flattens a nested shape into a private key
    (``search_text._flatten`` turning ``results`` into ``__rows__``) has already
    removed the public key by the time this sees it, so those need no
    exemption. That placement is what keeps the exemption list near-empty.

    ⚠ Raising is deliberate and matches the column guard: the dispatcher falls
    back to JSON, so the real data survives the wire. A warning would leave the
    agent holding a response with a table silently missing, which is the defect
    this exists to make impossible.
    """
    declared = {t.key for t in tables}
    declared.update(nested_dicts)
    declared.update(json_blobs)
    declared.update(allow_undeclared)
    for key, value in response.items():
        if key.startswith("_") or key in declared:
            continue
        if not isinstance(value, list) or not value:
            continue
        if not all(isinstance(row, dict) for row in value):
            continue  # list of scalars is not a table
        raise ValueError(
            f"schema/producer mismatch: {tool} returned {len(value)} row(s) "
            f"under {key!r}, which no TableSpec declares "
            f"(declared: {sorted(t.key for t in tables) or 'none'}). Declare it, "
            f"or add it to allow_undeclared if dropping it is intended."
        )


def encode(
    tool: str,
    response: dict,
    encoding_id: str,
    tables: Iterable[TableSpec] = (),
    scalars: Iterable[str] = (),
    nested_dicts: dict[str, list[str]] | None = None,
    meta_keys: Iterable[str] = (),
    json_blobs: Iterable[str] = (),
    meta_json_blobs: Iterable[str] = (),
    allow_undeclared: Iterable[str] = (),
) -> tuple[str, str]:
    tables = list(tables)
    nested_dicts = nested_dicts or {}
    _check_undeclared_tables(
        tool, response, tables, nested_dicts, json_blobs, allow_undeclared,
    )

    # Build shared path/symbol legend across all string-interned columns.
    legend = Legends(prefix="@")
    for t in tables:
        rows = response.get(t.key, []) or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            for c in t.intern:
                v = row.get(c)
                if isinstance(v, str):
                    legend.observe(v)
    legend.finalize(min_uses=2, min_chars_saved=1)

    # Scalar section
    scalar_payload: dict[str, Any] = {}
    for k in scalars:
        if k in response:
            scalar_payload[k] = response[k]
    for key, subkeys in nested_dicts.items():
        sub = response.get(key) or {}
        if isinstance(sub, dict):
            for sk in subkeys:
                if sk in sub:
                    scalar_payload[f"{key}.{sk}"] = sub[sk]
    meta = response.get("_meta") or {}
    for k in meta_keys:
        if k in meta:
            scalar_payload[f"_meta.{k}"] = meta[k]
    # Structured _meta values (dicts/lists) that must survive compaction intact.
    # meta_keys flattens to a scalar, which would stringify a dict — the retrieval
    # verdict is the reason this exists: dropping it hands the agent a confident
    # zero-result answer with no way to know the scan was degraded (and silently
    # discards the absence evidence_ref minted in call_tool).
    for k in set(meta_json_blobs) | UNIVERSAL_META_JSON:
        if k in meta:
            scalar_payload[f"__json._meta.{k}"] = json.dumps(
                meta[k], separators=(",", ":")
            )
    json_blobs = list(json_blobs)
    for k in json_blobs:
        if k in response:
            scalar_payload[f"__json.{k}"] = json.dumps(response[k], separators=(",", ":"))
    declared_body = set(scalars) | set(json_blobs) | {t.key for t in tables} | set(nested_dicts)
    for k, v in response.items():
        if k not in declared_body and not k.startswith("_") and is_universal_body_key(k):
            scalar_payload[f"__json.{k}"] = json.dumps(v, separators=(",", ":"))
    scalar_types_out: dict[str, str] = {}
    for k, v in scalar_payload.items():
        if k.startswith("__"):
            continue
        if v is not None and not isinstance(v, str):
            scalar_types_out[k] = _type_of(v)
    if scalar_types_out:
        scalar_payload["__stypes"] = "|".join(f"{k}:{t}" for k, t in scalar_types_out.items())
    # Encode the table schema into the payload so decode is self-sufficient.
    scalar_payload["__tables"] = ",".join(
        f"{t.tag}:{t.key}:{'|'.join(t.cols)}" for t in tables
    )

    sections: list[str] = []
    leg_text = legend.write()
    if leg_text:
        sections.append(leg_text)
    sections.append(write_scalars(scalar_payload))

    for t in tables:
        rows = response.get(t.key, []) or []
        out_rows: list[list[Any]] = []
        intern_set = set(t.intern)
        any_value = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            encoded_row: list[Any] = []
            for c in t.cols:
                v = row.get(c)
                if v is not None and v != "":
                    any_value = True
                if c in intern_set and isinstance(v, str):
                    v = legend.encode_prefix(v)
                encoded_row.append(v)
            out_rows.append(encoded_row)
        # Fail closed (#354): a producer dict whose keys don't match any declared
        # column encodes to all-blank cells, so the agent sees "N items" with no
        # usable data. If a table has rows but every declared column came back
        # empty across all of them, the schema and producer disagree — raise so
        # the dispatcher falls back to JSON and the real data survives the wire.
        if out_rows and not any_value:
            raise ValueError(
                f"schema/producer mismatch: table {t.key!r} has "
                f"{len(out_rows)} row(s) but no declared column "
                f"({'|'.join(t.cols)}) was populated"
            )
        sections.append(write_table(t.tag, out_rows))

    header = write_header(tool, encoding_id)
    return assemble(header, *sections), encoding_id


def decode(
    payload: str,
    tables: Iterable[TableSpec] = (),
    scalars: Iterable[str] = (),
    nested_dicts: dict[str, list[str]] | None = None,
    meta_keys: Iterable[str] = (),
    json_blobs: Iterable[str] = (),
    meta_json_blobs: Iterable[str] = (),
    scalar_types: Mapping[str, str] | None = None,
) -> dict:
    tables = list(tables)
    nested_dicts = nested_dicts or {}
    scalar_set = set(scalars)
    stypes: dict[str, str] = dict(scalar_types or {})

    head, blocks = split_sections(payload)
    parse_header(head)

    legend = Legends(prefix="@")
    scalar_block: str | None = None
    table_block_text: list[str] = []
    for b in blocks:
        if b.startswith("@") and "=" in b.splitlines()[0]:
            legend = Legends.read(b, prefix="@")
        elif scalar_block is None and "=" in b.splitlines()[0]:
            scalar_block = b
        else:
            table_block_text.append(b)

    raw_scalars = parse_scalars(scalar_block) if scalar_block else {}
    raw_scalars.pop("__tables", None)
    stypes_text = raw_scalars.pop("__stypes", "")
    for part in [p for p in stypes_text.split("|") if p]:
        name, _, hint = part.partition(":")
        if name and hint and name not in stypes:
            stypes[name] = hint

    result: dict[str, Any] = {}
    # Top-level scalars — coerce per scalar_types hint when supplied,
    # otherwise fall through as raw string (back-compat for schemas that
    # don't declare types).
    for k, v in list(raw_scalars.items()):
        if k in scalar_set:
            result[k] = _coerce(v, stypes.get(k, "str"))
    # Nested dicts
    for key, subkeys in nested_dicts.items():
        sub: dict[str, Any] = {}
        for sk in subkeys:
            prefixed = f"{key}.{sk}"
            if prefixed in raw_scalars:
                sub[sk] = _coerce(raw_scalars[prefixed], stypes.get(prefixed, "str"))
        if sub:
            result[key] = sub
    # Meta
    meta_out: dict[str, Any] = {}
    for k in meta_keys:
        prefixed = f"_meta.{k}"
        if prefixed in raw_scalars:
            meta_out[k] = _coerce(raw_scalars[prefixed], stypes.get(prefixed, "str"))
    for k in set(meta_json_blobs) | UNIVERSAL_META_JSON:
        prefixed = f"__json._meta.{k}"
        if prefixed in raw_scalars:
            try:
                meta_out[k] = json.loads(raw_scalars[prefixed])
            except Exception:
                meta_out[k] = raw_scalars[prefixed]
    if meta_out:
        result["_meta"] = meta_out
    # JSON blobs
    for k in json_blobs:
        prefixed = f"__json.{k}"
        if prefixed in raw_scalars:
            try:
                result[k] = json.loads(raw_scalars[prefixed])
            except Exception:
                result[k] = raw_scalars[prefixed]
    # Universal body keys (see UNIVERSAL_BODY_JSON): whatever the schema did
    # not declare but the encoder carried anyway.
    for prefixed, raw in raw_scalars.items():
        if not prefixed.startswith("__json.") or prefixed.startswith("__json._meta."):
            continue
        k = prefixed[len("__json."):]
        if k in result or not is_universal_body_key(k):
            continue
        try:
            result[k] = json.loads(raw)
        except Exception:
            result[k] = raw

    # Tables
    for t in tables:
        decoded_rows: list[dict[str, Any]] = []
        intern_set = set(t.intern)
        for block in table_block_text:
            rows = read_table(block, t.tag)
            if not rows:
                continue
            for r in rows:
                row_dict: dict[str, Any] = {}
                for i, c in enumerate(t.cols):
                    raw = r[i] if i < len(r) else ""
                    if c in intern_set and isinstance(raw, str):
                        raw = legend.decode_prefix(raw)
                    row_dict[c] = _coerce(raw, t.types.get(c, "str"))
                decoded_rows.append(row_dict)
        result[t.key] = decoded_rows

    return result
