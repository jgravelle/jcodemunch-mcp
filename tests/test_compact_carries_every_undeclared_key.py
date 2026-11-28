"""A compact answer carries every body key its schema did not declare (LEDGER L-341).

`schema_driven.encode` carried an undeclared top-level key only when it was a
"universal" key (a warning, `negative_evidence`, ...) and dropped every other
one with nothing raised: `get_impact_preview`'s `decisions` and
`find_importers`' `cross_repo_importer_count` reached a compact answer as if
they had never been computed. The only loud case was a list of rows, which
`_check_undeclared_tables` refused. Each schema then learned its missing keys
one report at a time (L-58, L-73).

The fix is one layer down, in the shared encoder and decoder: a scalar, a dict
or a list of scalars under an undeclared key rides as JSON and decodes back.
`allow_undeclared` stays the one way to drop a key on purpose.

NON-VACUITY: the two named cases and the per-encoder property fail on main.
"""
from __future__ import annotations

import pytest

from jcodemunch_mcp.encoding import encode_response, schema_driven as sd
from jcodemunch_mcp.encoding.decoder import decode
from jcodemunch_mcp.encoding.schemas import registry

_ENCODED_TOOLS = sorted(registry._BY_TOOL)

_EXTRAS = {
    "l341_scalar": 7,
    "l341_text": "kept",
    "l341_dict": {"a": 1, "b": [1, 2]},
    "l341_list": ["x", "y"],
}


def _round_trip(tool: str, response: dict) -> dict:
    payload, meta = encode_response(tool, response, "compact")
    assert isinstance(payload, str) and meta["encoding"] != "json", (tool, meta)
    return decode(payload)


def test_get_impact_preview_keeps_decisions():
    # Producer-shaped (tools/get_impact_preview.py builds `symbol`,
    # `affected_files`, `affected_symbol_count`, `affected_symbols`), so the
    # declared path is exercised beside the undeclared key.
    resp = {
        "repo": "acme/app",
        "symbol": {"id": "a.py::f#function", "name": "f", "kind": "function",
                   "file": "a.py", "line": 1},
        "affected_files": 1,
        "affected_symbol_count": 1,
        "affected_symbols": [{"id": "b.py::g#function", "name": "g", "kind": "function",
                              "file": "b.py", "line": 3, "depth": 1}],
        "decisions": {"dynamic_boundary": "disclosed", "reason": "opaque loader"},
    }
    out = _round_trip("get_impact_preview", resp)
    assert out.get("decisions") == resp["decisions"], out
    assert out["symbol"]["name"] == "f", out


def test_get_dependency_graph_keeps_the_files_import_lists():
    # Measured on this repository's src/: main dropped `imports` and
    # `importers`, the file's own dependency lists, from the compact answer.
    resp = {
        "repo": "acme/app",
        "file": "src/a.py",
        "direction": "both",
        "depth": 1,
        "edges": [{"from": "src/a.py", "to": "src/b.py", "depth": 1}],
        "imports": ["src/b.py"],
        "importers": [],
    }
    out = _round_trip("get_dependency_graph", resp)
    assert out.get("imports") == ["src/b.py"] and out.get("importers") == [], out


def test_find_importers_keeps_cross_repo_importer_count():
    resp = {
        "repo": "acme/app",
        "file_path": "src/a.py",
        "importer_count": 1,
        "importers": [{"file": "src/b.py", "specifier": "a", "has_importers": False}],
        "cross_repo_importer_count": 3,
    }
    out = _round_trip("find_importers", resp)
    assert out.get("cross_repo_importer_count") == 3, out


def test_the_registry_is_not_empty():
    # Guards the parametrize below from passing over nothing.
    assert len(_ENCODED_TOOLS) >= 15, _ENCODED_TOOLS


# These two decode their own payload and refuse any that does not reproduce
# the whole response, so the dispatcher falls back to JSON: they were never
# lossy, and a minimal response does not take their compact path at all.
_SELF_VERIFYING = {"check_references", "get_file_tree"}


@pytest.mark.parametrize("tool", _ENCODED_TOOLS)
def test_every_encoder_carries_undeclared_body_keys(tool):
    # At the client's side of the dispatcher: whatever the encoding, the key
    # arrives. Every other encoder must take its compact path, or a fallback
    # to JSON would pass this on both sides.
    resp = {"repo": "acme/app", **_EXTRAS}
    payload, meta = encode_response(tool, dict(resp), "compact")
    if tool not in _SELF_VERIFYING:
        assert meta["encoding"] != "json", (tool, meta)
    out = decode(payload) if isinstance(payload, str) else payload
    for k, v in _EXTRAS.items():
        assert out.get(k) == v, (tool, k, out.get(k))


def test_a_batch_file_outline_keeps_an_undeclared_key():
    # The defect in a reshaping schema's own spelling: the batch flatten copied
    # only the universal keys, so the encoder never saw the rest.
    resp = {
        "repo": "acme/app",
        "results": [{"file": "a.py", "language": "python", "file_summary": "",
                     "symbols": [], "_meta": {"symbol_count": 0}, "repo": "acme/app"}],
        "later_key": {"n": 3},
    }
    out = _round_trip("get_file_outline", resp)
    assert out.get("later_key") == {"n": 3}, out


def test_the_dispatcher_ships_json_when_a_schema_loses_a_body_key(monkeypatch):
    # The backstop one layer down: a schema that reshapes the response and
    # loses a key (the next one written) ships JSON, so the key still arrives.
    enc = registry.for_tool("find_importers")
    real = enc.encode

    def lossy(tool, response):
        return real(tool, {k: v for k, v in response.items() if k != "later_key"})

    monkeypatch.setattr(enc, "encode", lossy)
    resp = {"repo": "acme/app", "file_path": "a.py", "importer_count": 0,
            "importers": [], "later_key": 5}
    payload, meta = encode_response("find_importers", resp, "compact")
    assert meta["encoding"] == "json" and payload["later_key"] == 5, meta


def test_allow_undeclared_still_drops_on_purpose():
    payload, enc_id = sd.encode(
        "t", {"repo": "a/b", "noise": 1, "kept": 2}, "x1",
        scalars=("repo",), allow_undeclared=("noise",),
    )
    out = sd.decode(payload, [], ("repo",))
    assert "noise" not in out and out.get("kept") == 2, out


def test_an_undeclared_sub_key_of_a_nested_dict_reaches_the_client():
    # The same defect one level down: `symbol`'s sub-keys are named one by
    # one, so a new one dropped in silence. The encoder refuses it and the
    # dispatcher ships JSON, so the sub-key arrives.
    resp = {
        "repo": "acme/app",
        "symbol": {"id": "a.py::f#function", "name": "f", "kind": "function",
                   "file": "a.py", "line": 1, "signature": "def f()"},
        "affected_count": 0,
        "affected": [],
    }
    payload, meta = encode_response("get_impact_preview", resp, "compact")
    out = decode(payload) if isinstance(payload, str) else payload
    assert out["symbol"].get("signature") == "def f()", (meta, out)


def test_a_declared_nested_dict_still_takes_the_compact_path():
    resp = {
        "repo": "acme/app",
        "symbol": {"id": "a.py::f#function", "name": "f", "kind": "function",
                   "file": "a.py", "line": 1},
        "affected_count": 0,
        "affected": [],
    }
    payload, meta = encode_response("get_impact_preview", resp, "compact")
    assert meta["encoding"] != "json", meta
    assert decode(payload)["symbol"]["name"] == "f"


def test_an_undeclared_list_of_rows_still_refuses():
    # The table guard (#555) is unchanged: rows under an undeclared key raise,
    # and the dispatcher falls back to JSON.
    with pytest.raises(ValueError):
        sd.encode("t", {"repo": "a/b", "rows": [{"x": 1}]}, "x1", scalars=("repo",))
