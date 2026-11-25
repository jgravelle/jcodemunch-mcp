# MUNCH — Compact Output Encoding Specification

Version: 1
Status: stable (since jcodemunch-mcp 1.54.0)

MUNCH is jcodemunch's purpose-built wire format for MCP tool responses. It is
an opt-in second-axis token optimization that complements the retrieval-side
savings jcodemunch already provides: **retrieval decides *what* to send; MUNCH
decides *how to pack it*.**

This document specifies the on-wire format so third-party clients and alternate
MCP servers can decode MUNCH payloads without depending on the Python reference
decoder.

---

## 1. Design goals

1. **Deterministic** — the same response dict encodes to the same bytes.
2. **Round-trippable** — a decoder reconstructs the original dict structure
   (top-level keys, column order, per-column types) for all schemas covered by
   a tier-1 encoder, and for most shapes handled by the generic fallback.
3. **Streaming-friendly** — the format is line-oriented; a decoder can tokenise
   sections with `splitlines()` and a blank-line separator.
4. **Fail-open** — any client that cannot decode MUNCH can request
   `format="json"` and receive a standard JSON dict.
5. **Size-justified** — encoding only fires when it saves at least 15% of bytes
   (configurable). Small payloads fall back to JSON automatically.

---

## 2. Envelope

A MUNCH payload is a UTF-8 text document with one **header line** followed by
one or more sections separated by a single blank line (`\n\n`). Sections
themselves may contain multiple `\n`-terminated lines.

```
#MUNCH/<VERSION> tool=<tool_name> enc=<encoding_id>

<optional legend section>

<scalars section>

<optional table section 1>
<optional table section 2>
...
```

All payloads end with a trailing `\n`.

### 2.1 Header

Exactly one line, always the first line of the payload. Format:

```
#MUNCH/1 tool=<tool_name> enc=<encoding_id>
```

- `VERSION` is an integer. This spec describes version `1`.
- `tool_name` is the MCP tool name (e.g. `find_references`).
- `encoding_id` is a short mnemonic identifying the encoder/schema used
  (e.g. `gen1`, `fr1`, `dg1`). Decoders select the matching rehydration
  routine by this id.

Decoders must reject any payload whose first line does not start with
`#MUNCH/`.

---

## 3. Legends section

Optional. Interns frequently-repeated string prefixes (typically file paths)
so each subsequent occurrence collapses to a short numeric handle.

Format — one line per entry, each:

```
@<N>=<literal prefix string>
```

- `@` is the fixed legend prefix.
- `N` is a 1-based integer handle. Handles are dense (`@1`, `@2`, ...).
- The literal is an unquoted string running to end-of-line. It may contain
  any character except `\n`.

Values that match a registered prefix are rewritten in the output as
`@N<remainder>`. For example, given `@1=src/models/`, the path
`src/models/user.py` is emitted as `@1user.py`. Decoding reverses this:
any token starting with `@` followed by digits has those digits parsed as
a handle, the literal substituted, and the trailing remainder kept.

Tokens that start with `@` but do not match a known handle are kept verbatim.

---

## 4. Scalars section

Exactly one line of whitespace-separated `key=value` pairs. Keys are MCP
response field names. Values follow these rules:

- **Strings** — emitted as-is if they contain none of `, = <whitespace> "`.
  Otherwise double-quoted with doubled-quote escaping (`"a ""b"" c"`
  represents `a "b" c`), per RFC 4180.
- **Integers** — decimal, no quotes (`42`).
- **Floats** — Python `repr(float)` form (`0.15`, `1.5e-05`).
- **Booleans** — `T` (true) or `F` (false).
- **Null** — empty string. Empty strings are encoded as `""` to disambiguate.
- **Nested dicts and complex objects** — flattened via a dotted key
  (`symbol.file=x.py`) or wrapped as a JSON blob scalar
  (`__json.channels={"a":1}`). A dedicated `__json.` prefix signals to the
  decoder that the value is a JSON string to rehydrate.

### 4.1 Reserved scalar keys

Generic-encoder payloads include these synthetic scalars (tier-1 encoders
carry the same information in a schema file instead):

- `__tables` — comma-separated table schema. One entry per table:
  `<tag>:<original_key>:<col1>|<col2>|...:<type1>|<type2>|...`.
  Types are `int`, `float`, `bool`, `str` or `json`. Example:
  `t:references:file|line|kind:str|int|str`.
- `__stypes` — pipe-separated scalar type map for any non-string top-level
  scalar. Example: `total:int|ratio:float|ok:bool`.

Decoders must pop these keys before materialising the response.

### 4.2 Meta fields

`_meta` values are flattened with the `_meta.` prefix
(`_meta.timing_ms=3.1 _meta.truncated=F`). Decoders re-nest them into a
single `_meta` dict on output.

---

## 5. Tables section

Each table is a separate section. A section is one or more CSV-style rows
where the **first field is a single-character tag**. Rows are emitted with
RFC 4180 quoting. The first field discriminates both which table a row
belongs to and, when multiple tables share a section block, how to re-split
them.

Example:

```
t,src/a.py,10,call
t,src/a.py,22,call
t,src/b.py,5,ref
```

With schema `t:references:file|line|kind:str|int|str`, this decodes to:

```json
{
  "references": [
    {"file": "src/a.py", "line": 10, "kind": "call"},
    {"file": "src/a.py", "line": 22, "kind": "call"},
    {"file": "src/b.py", "line": 5,  "kind": "ref"}
  ]
}
```

### 5.1 Tag alphabet

Tags are single characters drawn from `a`–`z`. The first non-header section
whose first line matches `^[a-z],` is classified as a table section; any
other section is treated as the scalars section (or, if it begins with `@`,
the legend section).

### 5.2 Per-column interning

Columns declared in a tier-1 encoder's `intern` set are legend-prefix-encoded
using the shared legend. The generic encoder interns any string column whose
prefix repeats enough times to save bytes.

### 5.3 Type coercion

On decode, each field is coerced using the column's declared type:

- `str` — legend-decoded then returned as-is.
- `int` — `int(raw)`, unchanged on `ValueError`.
- `float` — `float(raw)`, unchanged on `ValueError`.
- `bool` — `T → True`, anything else → `False`.
- `json` — `json.loads(raw)`. A column gets this type when any of its cells
  holds a list or dict; every non-empty cell of it is written as JSON text,
  so a string, number or bool beside a list decodes to itself.
- Empty field (`""`) always decodes to `None`.

---

## 6. Dispatcher contract

Every MCP tool in jcodemunch accepts an optional `format` argument:

- `"auto"` (default) — encode, measure, emit compact if savings ≥ threshold,
  otherwise emit JSON. Threshold is 15% by default, overridable via
  `JCODEMUNCH_ENCODING_THRESHOLD`.
- `"compact"` — always emit MUNCH (no threshold check).
- `"json"` — never encode; emit standard JSON.

Server-wide default is overridable via `JCODEMUNCH_DEFAULT_FORMAT`.

Every compact response adds three fields to `_meta`:

- `encoding` — the encoding id used (e.g. `fr1`, `gen1`).
- `encoding_tokens_saved` — estimated tokens saved for this call.
- `total_encoding_tokens_saved` — lifetime total across the session.

Clients that cannot decode MUNCH should request `format="json"`.

---

## 7. Reference decoder

The canonical decoder ships at `jcodemunch_mcp.encoding.decoder.decode`. It:

1. Returns `json.loads(payload)` if the input does not start with `#MUNCH/`.
2. Parses the header, resolves the encoder module by `enc=` id.
3. Delegates to that module's `decode(payload)` function.

Tier-1 encoder ids currently defined (v1.56.0):

| id | tool |
|----|------|
| `fr1` | `find_references` |
| `fi1` | `find_importers` |
| `ch1` | `get_call_hierarchy` |
| `dg1` | `get_dependency_graph` |
| `br1` | `get_blast_radius` |
| `ip1` | `get_impact_preview` |
| `sc1` | `get_signal_chains` |
| `dc1` | `get_dependency_cycles` |
| `tm1` | `get_tectonic_map` |
| `ss1` | `search_symbols` |
| `st1` | `search_text` |
| `sa1` | `search_ast` |
| `fo1` | `get_file_outline` |
| `ro1` | `get_repo_outline` |
| `rc1` | `get_ranked_context` |
| `gen1` | generic fallback for all other tools |

Any tool not listed above still encodes through `gen1`, which is
round-trippable for homogeneous-table responses and falls open to JSON
passthrough when the savings gate rejects the result.

---

## 8. Versioning

Changes that would break an existing decoder (re-purposing a character,
changing the scalar delimiter, etc.) bump the major version in the header
(`#MUNCH/2`). Additive changes (new encoding ids, new reserved scalar keys)
keep version `1` and new clients must ignore unknown scalar keys they do not
recognise.
