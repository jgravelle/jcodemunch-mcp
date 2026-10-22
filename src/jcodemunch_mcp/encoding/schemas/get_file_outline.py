"""Compact encoder for get_file_outline.

Handles two response shapes:

* **Singular** (``file_path``): top-level ``symbols`` list + scalars.
* **Batch** (``file_paths``): ``results`` array, each entry carrying its own
  nested ``symbols`` list and ``_meta.symbol_count``.

The batch shape nests the symbols one level below where the singular schema
looks for them, so a single flat schema silently dropped every symbol in batch
mode (issue #319). We flatten the batch into a file-discriminated symbols table
on encode and regroup on decode.
"""

from .. import schema_driven as sd
from ..format import parse_scalars, split_sections

TOOLS = ("get_file_outline",)
ENCODING_ID = "fo1"

_SYMBOL_COLS = ["id", "name", "kind", "signature", "line", "end_line", "parent", "summary"]
_SYMBOL_TYPES = {"line": "int", "end_line": "int"}

# Singular mode: one symbols table, symbols live at the top level.
_SINGLE_TABLES = [
    sd.TableSpec(
        key="symbols",
        tag="s",
        cols=_SYMBOL_COLS,
        intern=["id", "parent"],
        types=_SYMBOL_TYPES,
    ),
]

# Batch mode: symbols flattened across files with a ``file`` discriminator so
# decode can regroup them, plus a per-file results table carrying the count,
# language, and summary that would otherwise be lost.
_BATCH_TABLES = [
    sd.TableSpec(
        key="symbols",
        tag="s",
        cols=_SYMBOL_COLS + ["file"],
        intern=["id", "parent", "file"],
        types=_SYMBOL_TYPES,
    ),
    sd.TableSpec(
        key="results",
        tag="b",
        cols=["file", "language", "file_summary", "symbol_count"],
        intern=["file", "language"],
        types={"symbol_count": "int"},
    ),
]

_SCALARS = ("repo", "file", "symbol_count", "language")
_META = ("timing_ms", "tokens_saved", "total_tokens_saved")
_META_JSON = ("verdict",)  # structured _meta that must survive compaction


def _is_batch(response: dict) -> bool:
    return isinstance(response.get("results"), list)


def _flatten_batch(response: dict) -> dict:
    """Lift each result's nested symbols into one file-tagged symbols table."""
    flat_symbols: list[dict] = []
    result_rows: list[dict] = []
    for entry in response.get("results") or []:
        if not isinstance(entry, dict):
            continue
        file = entry.get("file", "")
        syms = entry.get("symbols") or []
        for sym in syms:
            row = dict(sym)
            row["file"] = file
            flat_symbols.append(row)
        meta = entry.get("_meta") or {}
        count = meta.get("symbol_count")
        if count is None:
            count = len(syms)
        result_rows.append(
            {
                "file": file,
                "language": entry.get("language", ""),
                "file_summary": entry.get("file_summary", ""),
                "symbol_count": count,
            }
        )
    flat: dict = {"symbols": flat_symbols, "results": result_rows}
    if "repo" in response:
        flat["repo"] = response["repo"]
    if "_meta" in response:
        flat["_meta"] = response["_meta"]
    # A warning on the batch response (`budget_warning` is promoted to the top
    # level of every result) must reach the encoder, which carries it.
    for k, v in response.items():
        if not k.startswith("_") and sd.is_universal_body_key(k):
            flat[k] = v
    return flat


def _unflatten_batch(flat: dict) -> dict:
    """Regroup the file-tagged symbols table back into per-file results."""
    by_file: dict[str, list] = {}
    for sym in flat.get("symbols") or []:
        file = sym.pop("file", "") or ""
        by_file.setdefault(file, []).append(sym)

    repo = flat.get("repo")
    results_out: list[dict] = []
    for row in flat.get("results") or []:
        file = row.get("file", "")
        entry = {
            "file": file,
            "language": row.get("language", "") or "",
            "file_summary": row.get("file_summary", "") or "",
            "symbols": by_file.get(file, []),
            "_meta": {"symbol_count": row.get("symbol_count") or 0},
        }
        if repo is not None:
            entry["repo"] = repo
        results_out.append(entry)

    out: dict = {"results": results_out}
    if repo is not None:
        out["repo"] = repo
    if "_meta" in flat:
        out["_meta"] = flat["_meta"]
    for k, v in flat.items():
        if not k.startswith("_") and sd.is_universal_body_key(k):
            out[k] = v
    return out


def _payload_is_batch(payload: str) -> bool:
    """A batch payload advertises the ``results`` table in its embedded schema.

    Scalars share a single space-separated line, so we parse the scalar block
    and inspect ``__tables`` rather than scanning raw text (a symbol summary
    could otherwise contain the word "results").
    """
    try:
        _, blocks = split_sections(payload)
        for block in blocks:
            lines = block.splitlines()
            first = lines[0] if lines else ""
            if "=" in first and not first.startswith("@"):
                tables = parse_scalars(block).get("__tables", "")
                return ":results:" in tables
    except Exception:
        pass
    return False


def encode(tool: str, response: dict) -> tuple[str, str]:
    if _is_batch(response):
        return sd.encode(
            tool, _flatten_batch(response), ENCODING_ID, _BATCH_TABLES, _SCALARS, meta_keys=_META, meta_json_blobs=_META_JSON
        )
    return sd.encode(tool, response, ENCODING_ID, _SINGLE_TABLES, _SCALARS, meta_keys=_META, meta_json_blobs=_META_JSON)


def decode(payload: str) -> dict:
    if _payload_is_batch(payload):
        flat = sd.decode(payload, _BATCH_TABLES, _SCALARS, meta_keys=_META, meta_json_blobs=_META_JSON)
        return _unflatten_batch(flat)
    return sd.decode(payload, _SINGLE_TABLES, _SCALARS, meta_keys=_META, meta_json_blobs=_META_JSON)
