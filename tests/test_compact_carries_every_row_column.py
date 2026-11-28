"""A compact table row keeps every column its schema did not name (LEDGER L-340).

The compact encoders write a table row from the columns its `TableSpec` names.
Any other key in the row was dropped with nothing raised, and the `auto` gate
then picked the compact form because it was smaller. Measured on this
repository's own `src/`: `get_ranked_context` lost every item's `source` (the
code the tool exists to return) and the `symbol_id`, `relevance_score` and
`tokens` its producer writes under names the schema does not use;
`get_signal_chains` lost each chain's `symbols` and `files_touched`;
`get_tectonic_map` each plate's `files` and `coupled_to`.

L-341 carried an unnamed TOP-LEVEL key as JSON; this is the same rule one level
down. An unnamed row key rides as one more column after the declared ones,
named `__json.<key>` in `__tables`, its cell the value as JSON and blank where
the row has no such key. `TableSpec.drop` stays the one way to leave a column
out on purpose.
"""

from __future__ import annotations

import json

import pytest

from jcodemunch_mcp.encoding import decoder, encode_response
from jcodemunch_mcp.encoding import schema_driven as sd
from jcodemunch_mcp.encoding.schemas import registry
from jcodemunch_mcp.tools.index_folder import index_folder

_SPEC = sd.TableSpec(key="rows", tag="r", cols=["name", "line"], types={"line": "int"})
_ROWS = [
    {"name": "a", "line": 1, "source": "def a():\n    return 'x, \"y\"'\n", "tags": ["p", "q"],
     "meta": {"k": 1}, "flag": False, "score": 0.5, "nothing": None},
    {"name": "b", "line": 2},
    {"name": "c", "line": 3, "source": "", "tags": []},
]


def _round_trip(rows, spec=_SPEC):
    payload, _ = sd.encode("t", {"rows": rows}, "t1", [spec])
    return sd.decode(payload, [spec])["rows"]


def test_every_unnamed_row_key_comes_back_with_its_value():
    back = _round_trip(_ROWS)
    assert back == _ROWS, back


def test_a_row_without_the_key_does_not_gain_it():
    back = _round_trip(_ROWS)
    assert "source" not in back[1] and "tags" not in back[1]
    assert back[2]["source"] == "" and back[2]["tags"] == []  # present-but-empty stays present
    assert back[0]["nothing"] is None and "nothing" not in back[2]  # null is not absence


def test_an_answer_with_no_unnamed_key_keeps_its_bytes():
    """No figure measured on a schema-complete answer moves."""
    rows = [{"name": "a", "line": 1}, {"name": "b", "line": 2}]
    payload, _ = sd.encode("t", {"rows": rows}, "t1", [_SPEC])
    assert "__json." not in payload
    assert "__tables=r:rows:name|line" in payload


def test_drop_is_the_one_way_to_leave_a_column_out():
    spec = sd.TableSpec(key="rows", tag="r", cols=["name", "line"], types={"line": "int"},
                        drop=["source"])
    back = _round_trip(_ROWS, spec)
    assert all("source" not in r for r in back)
    assert back[0]["tags"] == ["p", "q"]  # only the named key is dropped


def _schema_modules():
    registry._discover()  # noqa: SLF001 - the registry is the authority on which encoders exist
    return sorted({m for m in registry._BY_TOOL.values()}, key=lambda m: m.__name__)  # noqa: SLF001


def _sample(spec: sd.TableSpec) -> dict:
    row = {}
    for c in spec.cols:
        hint = spec.types.get(c, "str")
        row[c] = 1 if hint == "int" else 0.5 if hint == "float" else True if hint == "bool" else f"v_{c}"
    row["zz_unnamed"] = {"k": [1, 2], "s": "a,b\nc"}
    return row


def test_no_compact_encoder_drops_an_unnamed_row_column():
    """Not a per-schema list: a schema written next inherits the rule."""
    modules = _schema_modules()
    assert len(modules) > 10  # non-vacuity: the registry really loaded
    dropped, compact = [], 0
    for mod in modules:
        tables = getattr(mod, "_TABLES", None) or []
        for spec in tables:
            tool = mod.TOOLS[0]
            response = {spec.key: [_sample(spec), _sample(spec)]}
            payload, meta = encode_response(tool, response, requested_format="compact")
            if isinstance(payload, str):
                compact += 1
                back = decoder.decode(payload)
            else:
                back = payload  # JSON fallback keeps every key by construction
            rows = back.get(spec.key)
            if not isinstance(rows, list) or not rows:
                continue  # the schema reshapes this table under another key
            if any(r.get("zz_unnamed") != {"k": [1, 2], "s": "a,b\nc"} for r in rows):
                dropped.append(f"{tool}.{spec.key}")
    assert compact > 10, compact  # non-vacuity: the compact form really was chosen
    assert not dropped, f"unnamed row column dropped by: {dropped}"


# --- the reported tools, through the dispatcher --------------------------------

@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("l340") / "p"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "a.py").write_text(
        "from pkg.b import helper\n\n\ndef ranked_target(x):\n    '''Rank me.'''\n"
        "    return helper(x) + 1\n",
        encoding="utf-8",
    )
    (root / "pkg" / "b.py").write_text(
        "def helper(x):\n    '''Help.'''\n    return x * 2\n", encoding="utf-8"
    )
    store = tmp_path_factory.mktemp("l340store")
    out = index_folder(str(root), use_ai_summaries=False, storage_path=str(store),
                       identity_mode="local")
    assert out.get("success"), out
    return out["repo"], str(store)


def _served(tool, response):
    payload, meta = encode_response(tool, json.loads(json.dumps(response, default=str)),
                                    requested_format="compact")
    return (decoder.decode(payload) if isinstance(payload, str) else payload), meta


def test_ranked_context_keeps_the_source_it_loaded(repo):
    from jcodemunch_mcp.tools.get_ranked_context import get_ranked_context

    rid, store = repo
    res = get_ranked_context(repo=rid, query="ranked_target", storage_path=store)
    items = res["context_items"]
    assert items and all(i.get("source") for i in items), items  # non-vacuity
    back, _ = _served("get_ranked_context", res)
    for orig, got in zip(items, back["context_items"]):
        assert got.get("source") == orig["source"], (sorted(got), sorted(orig))
        for k in orig:
            if not k.startswith("_"):
                assert k in got, (k, sorted(got))
