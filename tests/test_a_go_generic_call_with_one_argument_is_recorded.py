"""A Go call written `F[T](x)` is recorded (LEDGER L-171, item 5).

Go's grammar cannot tell `Generic[int](1)`, a generic function called with one
argument, from `List[int](y)`, a conversion to a generic type: it parses both
as a `type_conversion_expression` whose type is a `generic_type`. That node was
not among Go's call nodes, so the call recorded nothing, and a generic function
called only that way read `no_callers` in `get_dead_code_v2`: live code as
dead. With two call arguments, or none, the grammar gives a `call_expression`,
which was always recorded.

The same node is what `fs[i](1)` parses as when the index is a bare name, so a
function taken from a slice or a map by a variable recorded nothing either,
where `fs[0](1)` recorded `fs`.

Every case goes through `parse_file`, and the dead-code cases through
`index_folder` and the tool.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import _CALL_NODE_TYPES, parse_file
from jcodemunch_mcp.tools import get_dead_code_v2 as v2
from jcodemunch_mcp.tools.index_folder import index_folder


def _records(statement: str) -> list[str]:
    """What a function whose body is `statement` records."""
    source = "package a\n\nfunc host(y int) {\n\t" + statement + "\n}\n"
    found = [s for s in parse_file(source, "a.go", "go") if s.kind == "function" and s.name == "host"]
    assert len(found) == 1, found
    return found[0].call_references


# the statement -> what the function records
CASES = {
    "one type argument, one argument": ("x := Generic[int](1); _ = x", ["Generic"]),
    "two type arguments, one argument": ("x := Generic[int, string](1); _ = x", ["Generic"]),
    "a function of another package": ("x := pkg.Generic[int](1); _ = x", ["Generic"]),
    "a qualified type argument": ("x := Generic[pkg.T](1); _ = x", ["Generic"]),
    "a slice type argument": ("x := Generic[[]int](nil); _ = x", ["Generic"]),
    "a map type argument": ("x := Generic[map[string]int](nil); _ = x", ["Generic"]),
    "a pointer type argument": ("x := Generic[*T](nil); _ = x", ["Generic"]),
    "a generic type argument": ("x := Generic[T[int]](1); _ = x", ["Generic"]),
    "as a statement": ("Generic[int](1)", ["Generic"]),
    "in a go statement": ("go Generic[int](1)", ["Generic"]),
    "in a defer statement": ("defer Generic[int](1)", ["Generic"]),
    "what it returns is called": ("x := Generic[int](1)(2); _ = x", ["Generic"]),
    "a call as its argument, in source order": ("x := Generic[int](make2(y)); _ = x", ["Generic", "make2"]),
    "as an argument of a call": ("use(Generic[int](1))", ["use", "Generic"]),
    "called twice is recorded once": ("Generic[int](1); Generic[string](2)", ["Generic"]),
    # `*F[T](x)` dereferences what the call returns; `(F[T])(x)` is the call in parentheses.
    "its result dereferenced": ("x := *Ptr[int](y); _ = x", ["Ptr"]),
    "its result dereferenced twice": ("x := **Ptr[int](y); _ = x", ["Ptr"]),
    "a qualified function's result dereferenced": ("x := *pkg.Ptr[int](y); _ = x", ["Ptr"]),
    "the function in parentheses": ("x := (Generic[int])(1); _ = x", ["Generic"]),
    "a qualified function in parentheses": ("x := (pkg.Generic[int])(1); _ = x", ["Generic"]),
    "a container element's result dereferenced": ("x := *fs[i](y); _ = x", ["fs"]),
    "a container element in parentheses": ("x := (fs[i])(y); _ = x", ["fs"]),
    # An element of a container resolves to the container, as `fs[0](1)` does.
    "a function taken from a slice by a variable": ("x := fs[i](1); _ = x", ["fs"]),
    "a function taken from a map by a variable": ("x := table[key](1); _ = x", ["table"]),
    "a function taken from a field's slice by a variable": ("x := t.fs[i](y); _ = x", ["fs"]),
    # The grammar cannot tell this from the first case. The name is recorded:
    # a type and a function share one namespace in a Go package.
    "a conversion to a generic type": ("x := List[int](y); _ = x", ["List"]),
    "a conversion to a pointer to a generic type": ("x := (*List[int])(p); _ = x", ["List"]),
}

# Spellings the grammar already gave a `call_expression`. Recorded before and after.
ALREADY = {
    "two type arguments and two arguments": ("x := Generic[int, string](1, \"a\"); _ = x", ["Generic"]),
    "no argument": ("x := Generic[int](); _ = x", ["Generic"]),
    "two arguments": ("x := Generic[int](1, 2); _ = x", ["Generic"]),
    "a literal index": ("x := fs[0](1); _ = x", ["fs"]),
    "an index that is an expression": ("x := fs[i+1](1); _ = x", ["fs"]),
    "a named type's conversion": ("x := MyInt(y); _ = x", ["MyInt"]),
}

# A conversion to a type that is not a name, and what else holds a generic type.
LOOKALIKES = {
    "a slice conversion": ("x := []byte(s); _ = x", []),
    "a map conversion": ("x := map[string]int(m); _ = x", []),
    "an array conversion": ("x := [3]int(a); _ = x", []),
    "a function-type conversion": ("x := func(int) int(f); _ = x", []),
    "a channel conversion": ("x := chan int(c); _ = x", []),
    "an interface conversion": ("x := interface{}(y); _ = x", []),
    "a struct conversion": ("x := struct{}(e); _ = x", []),
    "a slice of a generic type": ("x := []List[int](s); _ = x", []),
    "a pointer conversion": ("x := (*T)(p); _ = x", []),
    "a slice conversion in parentheses": ("x := ([]int)(s); _ = x", []),
    "a conversion to a pointer to a slice": ("x := (*[]int)(p); _ = x", []),
    "a conversion to a pointer to an array": ("x := (*[4]byte)(p); _ = x", []),
    "a channel conversion in parentheses": ("x := (chan int)(c); _ = x", []),
    "a function-type conversion in parentheses": ("x := (func(int) int)(f); _ = x", []),
    "a generic composite literal": ("x := Pair[int, string]{1, \"a\"}; _ = x", []),
    "a generic type in a declaration": ("var x Generic[int]; _ = x", []),
    "a generic function not called": ("x := Generic[int]; _ = x", []),
}


@pytest.mark.parametrize("label", sorted(CASES))
def test_a_call_the_grammar_reads_as_a_conversion_is_recorded(label):
    statement, expected = CASES[label]
    assert _records(statement) == expected


@pytest.mark.parametrize("label", sorted(ALREADY))
def test_a_call_the_grammar_reads_as_a_call_is_still_recorded(label):
    statement, expected = ALREADY[label]
    assert _records(statement) == expected


@pytest.mark.parametrize("label", sorted(LOOKALIKES))
def test_a_conversion_to_a_type_with_no_name_records_nothing(label):
    statement, expected = LOOKALIKES[label]
    assert _records(statement) == expected


def test_a_generic_function_that_calls_itself_records_nothing_for_it():
    """A call of the enclosing function's own name is dropped wherever calls are attributed."""
    source = "package a\n\nfunc Generic[T any](x T) T {\n\tif done(x) {\n\t\treturn x\n\t}\n\treturn Generic[T](x)\n}\n"
    found = [s for s in parse_file(source, "a.go", "go") if s.name == "Generic"]
    assert [s.call_references for s in found] == [["done"]]


def test_only_go_reads_a_conversion_for_calls():
    holders = sorted(lang for lang, types in _CALL_NODE_TYPES.items() if "type_conversion_expression" in types)
    assert holders == ["go"]


# Two Python files joined by an import, so the index has an import graph and the
# tool runs its three-signal path.
PYTHON_PAIR = {
    "util.py": "def b():\n    return 1\n",
    "py.py": "from util import b\n\ndef a():\n    return b()\n",
}

GO = {
    "gen.go": "package a\n\nfunc Generic[T any](x T) T {\n\treturn x\n}\n",
    "lonely.go": "package a\n\nfunc Lonely[T any](x T) T {\n\treturn x\n}\n",
    "plain.go": "package a\n\nfunc plain() int {\n\treturn 1\n}\n",
    # `plain()` is a recorded Go call on both trees, so the tool reads Go by
    # call references and not as text (L-155).
    "use.go": "package a\n\nfunc Use() int {\n\tx := Generic[int](1)\n\treturn x + plain()\n}\n",
}


def _signals(tmp_path, files):
    src = tmp_path / "src"
    src.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    for name, body in files.items():
        (src / name).write_text(body, encoding="utf-8")
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True
    out = v2.get_dead_code_v2(repo=r["repo"], min_confidence=0.0, max_results=0, storage_path=str(store))
    assert "error" not in out, out
    assert out.get("_meta", {}).get("mode") != "call_graph_only", out.get("_meta")
    return {(row["file"], row["name"]): row["signals"] for row in out["dead_symbols"]}


def test_a_generic_function_called_with_one_argument_is_not_reported_without_callers(tmp_path):
    rows = _signals(tmp_path, {**PYTHON_PAIR, **GO})
    assert "no_callers" not in rows.get(("gen.go", "Generic"), [])


def test_a_generic_function_nothing_calls_is_still_reported(tmp_path):
    rows = _signals(tmp_path, {**PYTHON_PAIR, **GO})
    assert "no_callers" in rows[("lonely.go", "Lonely")], rows


def test_an_existing_index_is_reparsed():
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 16
