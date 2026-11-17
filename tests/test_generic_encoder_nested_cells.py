"""The generic encoder (`gen1`) gives back a table cell that holds a list or dict.

LEDGER L-312: a cell holding a list or dict was written with Python `repr`
and decoded as a string, so `{items: [{name, kids: [{x: 1}]}]}` came back
with `kids` as the string "[{'x': 1}]". The encoder's docstring promises a
round trip, and every tool without its own schema goes through it.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.encoding import decoder, encode_response, generic


def _round_trip(response: dict) -> dict:
    payload, enc_id = generic.encode("some_tool", response)
    assert enc_id == generic.ENCODING_ID
    return decoder.decode(payload)


def test_the_reported_shape_round_trips():
    response = {
        "items": [
            {"name": "a", "kids": [{"x": 1}]},
            {"name": "b", "kids": [{"x": 2}, {"x": 3}]},
        ]
    }
    assert _round_trip(response) == response


@pytest.mark.parametrize(
    "cells",
    [
        [{"k": "v"}, {"k": "w", "n": [1, 2]}],  # dicts
        [[1, 2, 3], []],  # lists, one empty
        [["a,b", 'quote "q"'], ["new\nline"]],  # CSV and newline characters inside
        [None, [1]],  # a null beside a list
        ["plain", ["mixed"]],  # a string and a list in one column
        [7, {"seven": 7}],  # an int and a dict in one column
        [True, [False]],  # a bool and a list in one column
        # Review round 1: two scalar types before the list, which the
        # scalar scan stops at.
        [1, "a", [1]],
        ["a", 1, {"k": 1}],
        [1, 2.5, [3]],
        [True, 2, [3]],
    ],
)
def test_a_column_holding_a_list_or_dict_round_trips(cells):
    response = {"rows": [{"id": i, "cell": c} for i, c in enumerate(cells)]}
    assert _round_trip(response) == response


def test_scalar_only_columns_keep_their_wire_form():
    """A table with no list or dict cell encodes exactly as before."""
    response = {
        "rows": [
            {"path": "src/a.py", "n": 1, "ok": True},
            {"path": "src/b.py", "n": 2, "ok": False},
        ]
    }
    payload, _ = generic.encode("some_tool", response)
    assert "json" not in payload.split("__tables=", 1)[1].split()[0]
    assert _round_trip(response) == response


def test_the_dispatcher_ships_a_nested_cell_intact_under_compact():
    response = {
        "items": [{"name": "a", "kids": [{"x": 1}]}, {"name": "b", "kids": [{"x": 2}]}]
    }
    payload, meta = encode_response(
        "some_tool_without_a_schema", response, requested_format="compact"
    )
    assert meta["encoding"] == generic.ENCODING_ID
    assert decoder.decode(payload)["items"] == response["items"]
