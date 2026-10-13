"""A C# `new Made(...)` records the class it creates (LEDGER L-170, item 4).

A C# constructor is named for its class. Java's `new Base()` records `Base`,
and JavaScript's `new K()` records `K`; C#'s `object_creation_expression` was
not among its call nodes, so creating a class recorded nothing. A constructor
then had no caller on record however often its class was created, and
`get_dead_code_v2` reported it with `no_callers`: live code as dead.

Every case goes through `parse_file`, and the dead-code cases through
`index_folder` and the tool.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.tools import get_dead_code_v2 as v2
from jcodemunch_mcp.tools.index_folder import index_folder


def _records(statement: str) -> list[str]:
    """What a method whose body is `statement` records."""
    source = "namespace N {\n  class C {\n    object M(object a) {\n      " + statement + "\n      return a;\n    }\n  }\n}\n"
    found = [s for s in parse_file(source, "A.cs", "csharp") if s.kind == "method" and s.name == "M"]
    assert len(found) == 1, found
    return found[0].call_references


# the statement -> what the method records
CASES = {
    "no arguments": ("var x = new Made();", ["Made"]),
    "arguments": ("var x = new Made(1, 2);", ["Made"]),
    "an object initialiser and no argument list": ("var x = new Made { X = 1 };", ["Made"]),
    "arguments and an initialiser": ("var x = new Made(1) { X = 1 };", ["Made"]),
    "a generic class": ("var x = new Made<int>();", ["Made"]),
    "a qualified class": ("var x = new Ns.Sub.Made();", ["Made"]),
    "a qualified generic class": ("var x = new Ns.Made<int>();", ["Made"]),
    "a nested class": ("var x = new Outer.Made();", ["Made"]),
    "an alias-qualified class": ("var x = new global::Made();", ["Made"]),
    "an alias-qualified namespace": ("var x = new global::Ns.Made();", ["Made"]),
    "a comment before the class": ("var x = new /* why */ Made();", ["Made"]),
    "created as an argument, in source order": ("var x = new Made(new Other(), Make(1));", ["Made", "Other", "Make"]),
    "a method called on what was created": ("new Made().Run();", ["Run", "Made"]),
    "created in a lambda": ("System.Func<object> f = () => new Made();", ["Made"]),
    "thrown": ("throw new Broken(\"x\");", ["Broken"]),
    "returned from a conditional": ("return a == null ? new Made() : new Other();", ["Made", "Other"]),
    "beside an ordinary call": ("Use(new Made());", ["Use", "Made"]),
    "created twice is recorded once": ("var x = new Made(); var y = new Made(1);", ["Made"]),
}

# What looks like creating a class and records nothing for it.
LOOKALIKES = {
    # An array holds no instance: no constructor runs.
    "an array": ("var x = new Made[3];", []),
    "an array with an initialiser": ("var x = new Made[] { };", []),
    "a stack array": ("System.Span<Made> x = stackalloc Made[2];", []),
    # The class is named by the target, not by the expression (LEDGER L-174).
    "a target-typed new": ("Made x = new();", []),
    "an anonymous object": ("var x = new { A = 1 };", []),
    # A keyword type has no symbol.
    "a predefined type": ("var x = new string('a', 3);", []),
    "a tuple type": ("var x = new (int, Made)(1, null);", []),
    # A cast, a type test and a declaration name a class and create nothing.
    "a cast": ("var x = (Made)a;", []),
    "a type test": ("var x = a is Made;", []),
    "a typeof": ("var x = typeof(Made);", []),
    "a declaration": ("Made x = null;", []),
    "a generic argument is not what is created": ("var x = new Holder<Made>();", ["Holder"]),
}


@pytest.mark.parametrize("label", sorted(CASES))
def test_creating_a_class_records_it(label):
    statement, expected = CASES[label]
    assert _records(statement) == expected


@pytest.mark.parametrize("label", sorted(LOOKALIKES))
def test_what_only_looks_like_creating_a_class_records_nothing_for_it(label):
    statement, expected = LOOKALIKES[label]
    assert _records(statement) == expected


def test_a_constructor_that_creates_its_own_class_records_nothing_for_it():
    """A call of the enclosing function's own name is dropped wherever calls are attributed."""
    source = "class Made {\n  Made(int x) { }\n  Made() { var m = new Made(1); Use(m); }\n}\n"
    made = [s for s in parse_file(source, "A.cs", "csharp") if s.kind == "method" and s.name == "Made"]
    assert [s.call_references for s in sorted(made, key=lambda s: s.byte_offset)] == [[], ["Use"]]


def test_only_csharp_gains_a_call_node():
    """Java already read `object_creation_expression`; no other language's row holds it."""
    from jcodemunch_mcp.parser.extractor import _CALL_NODE_TYPES

    holders = sorted(lang for lang, types in _CALL_NODE_TYPES.items() if "object_creation_expression" in types)
    assert holders == ["csharp", "java"]


# Two Python files joined by an import, so the index has an import graph and the
# tool runs its three-signal path.
PYTHON_PAIR = {
    "util.py": "def b():\n    return 1\n",
    "py.py": "from util import b\n\ndef a():\n    return b()\n",
}

CSHARP = {
    "Made.cs": "namespace N {\n  class Made {\n    public Made(int x) { }\n    public int Run() { return 1; }\n  }\n}\n",
    "Generic.cs": "namespace N {\n  class Boxed<T> {\n    public Boxed(T x) { }\n  }\n}\n",
    "Lonely.cs": "namespace N {\n  class Lonely {\n    public Lonely(int x) { }\n  }\n}\n",
    "Arrayed.cs": "namespace N {\n  class Arrayed {\n    public Arrayed(int x) { }\n  }\n}\n",
    # `m.Run()` is a recorded C# call on both trees, so the tool reads C# by
    # call references and not as text (L-155).
    "Use.cs": (
        "namespace N {\n  class Use {\n"
        "    public int Go() { var m = new Made(2); var b = new N.Boxed<int>(1); return m.Run(); }\n"
        "    public object Many() { return new Arrayed[3]; }\n"
        "  }\n}\n"
    ),
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


@pytest.mark.parametrize("key", [("Made.cs", "Made"), ("Generic.cs", "Boxed")])
def test_the_constructor_of_a_class_that_is_created_is_not_reported_without_callers(tmp_path, key):
    rows = _signals(tmp_path, {**PYTHON_PAIR, **CSHARP})
    assert "no_callers" not in rows.get(key, [])


def test_a_constructor_nothing_creates_is_still_reported(tmp_path):
    rows = _signals(tmp_path, {**PYTHON_PAIR, **CSHARP})
    assert "no_callers" in rows[("Lonely.cs", "Lonely")], rows
    # An array of a class creates no instance.
    assert "no_callers" in rows[("Arrayed.cs", "Arrayed")], rows


def test_an_existing_index_is_reparsed():
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 15
