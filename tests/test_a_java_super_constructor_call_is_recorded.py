"""A Java `super(...)` call records the superclass's constructor (LEDGER L-166, item 3).

A Java constructor is named for its class, and `new Base()` records `Base`. A
subclass constructor reaches the same constructor with `super(...)`, which the
grammar gives its own node, `explicit_constructor_invocation`, and the parser
recorded nothing for it. So the constructor of a class that is only ever
subclassed (every abstract class) had no caller on record, and
`get_dead_code_v2` reported it with `no_callers`: live code as dead.

`super(...)` in a constructor now records the name the class extends. Every
case goes through `parse_file`, and the dead-code cases through `index_folder`
and the tool.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import _CALL_NODE_TYPES, parse_file
from jcodemunch_mcp.tools import get_dead_code_v2 as v2
from jcodemunch_mcp.tools.index_folder import index_folder


def _constructors(source: str, owner: str) -> list[list[str]]:
    """What each constructor of `owner` records, in source order."""
    symbols = parse_file(source, "A.java", "java")
    found = [s for s in symbols if s.kind == "method" and s.name == owner.rsplit(".", 1)[-1]
             and s.qualified_name == owner + "." + owner.rsplit(".", 1)[-1]]
    assert found, [(s.kind, s.qualified_name) for s in symbols]
    return [s.call_references for s in sorted(found, key=lambda s: s.byte_offset)]


# source, the class whose constructors are read -> what each records
CASES = {
    "a super call": (
        "class Kid extends Base {\n  Kid() { super(2); }\n}\n",
        "Kid", [["Base"]],
    ),
    "a super call with no arguments": (
        "class Kid extends Base {\n  Kid() { super(); }\n}\n",
        "Kid", [["Base"]],
    ),
    "a generic superclass": (
        "class Kid extends Base<String> {\n  Kid() { super(2); }\n}\n",
        "Kid", [["Base"]],
    ),
    "a qualified superclass": (
        "class Kid extends pkg.sub.Base {\n  Kid() { super(2); }\n}\n",
        "Kid", [["Base"]],
    ),
    "a qualified generic superclass": (
        "class Kid extends pkg.Base<String> {\n  Kid() { super(2); }\n}\n",
        "Kid", [["Base"]],
    ),
    "a super call qualified by an outer instance": (
        "class Kid extends Outer.Inner {\n  Kid(Outer o) { o.super(2); }\n}\n",
        "Kid", [["Inner"]],
    ),
    "a comment before the super call": (
        "class Kid extends Base {\n  Kid() {\n    // why\n    /* and */ super(2);\n  }\n}\n",
        "Kid", [["Base"]],
    ),
    "beside ordinary calls, in source order": (
        "class Kid extends Base {\n  Kid() { super(make(1)); after(); }\n}\n",
        "Kid", [["Base", "make", "after"]],
    ),
    "each constructor that has one": (
        "class Kid extends Base {\n  Kid() { super(1); }\n  Kid(int x) { super(x, 2); }\n  Kid(String s) { helper(s); }\n}\n",
        "Kid", [["Base"], ["Base"], ["helper"]],
    ),
    "a nested class records its own superclass": (
        "class Outer extends Top {\n  Outer() { super(1); }\n"
        "  static class Kid extends Base {\n    Kid() { super(2); }\n  }\n}\n",
        "Outer.Kid", [["Base"]],
    ),
    "the outer class beside a nested one": (
        "class Outer extends Top {\n  Outer() { super(1); }\n"
        "  static class Kid extends Base {\n    Kid() { super(2); }\n  }\n}\n",
        "Outer", [["Top"]],
    ),
    "a block comment before the superclass": (
        "class Kid extends /* why */ Base {\n  Kid() { super(2); }\n}\n",
        "Kid", [["Base"]],
    ),
    "a line comment before the superclass": (
        "class Kid extends // why\n    Base {\n  Kid() { super(2); }\n}\n",
        "Kid", [["Base"]],
    ),
    "an annotated superclass": (
        "class Kid extends @Ann Base {\n  Kid() { super(2); }\n}\n",
        "Kid", [["Base"]],
    ),
    "an annotated qualified generic superclass": (
        "class Kid extends pkg.@Ann Base<String> {\n  Kid() { super(2); }\n}\n",
        "Kid", [["Base"]],
    ),
    "implements beside extends": (
        "class Kid extends Base implements Runnable, Other {\n  Kid() { super(2); }\n  public void run() {}\n}\n",
        "Kid", [["Base"]],
    ),
}

# What looks like the shape and records nothing for it.
LOOKALIKES = {
    # A class that extends nothing calls Object's constructor, which is no symbol.
    "super in a class that extends nothing": (
        "class Kid {\n  Kid() { super(); }\n}\n", "Kid", [[]],
    ),
    "super in a class that only implements": (
        "class Kid implements Runnable {\n  Kid() { super(); }\n  public void run() {}\n}\n", "Kid", [[]],
    ),
    # A constructor with no explicit call calls the superclass's implicitly. Not written, not read.
    "no explicit call": (
        "class Kid extends Base {\n  Kid() { helper(1); }\n}\n", "Kid", [["helper"]],
    ),
    # `super.m()` is a method call on the superclass, an ordinary `method_invocation`.
    "a super method call": (
        "class Kid extends Base {\n  Kid() { super.init(1); }\n}\n", "Kid", [["init"]],
    ),
    # `this(...)` calls a constructor of the SAME name as the one it is in, and a
    # call of the enclosing function's own name is dropped (L-166 item 8).
    "this in a constructor": (
        "class Kid extends Base {\n  Kid() { this(1); }\n  Kid(int x) { helper(x); }\n}\n", "Kid", [[], ["helper"]],
    ),
    # Since JDK 25 statements may come before the call. The grammar here parses
    # that `super` as an error node; nothing is recorded for it (L-169).
    "a statement before the super call": (
        "class Kid extends Base {\n  Kid(int x) { check(x); super(x); }\n}\n", "Kid", [["check"]],
    ),
    # A class that extends another of its own name: the recorded name is the
    # constructor's own, which is dropped as recursion (L-169).
    "a superclass of the class's own name": (
        "class Base extends other.Base {\n  Base() { super(1); }\n}\n", "Base", [[]],
    ),
    "an enum constructor": (
        "enum Kid {\n  A(1);\n  Kid(int x) { helper(x); }\n}\n", "Kid", [["helper"]],
    ),
}


@pytest.mark.parametrize("label", sorted(CASES))
def test_a_super_call_records_the_superclass(label):
    source, owner, expected = CASES[label]
    assert _constructors(source, owner) == expected


@pytest.mark.parametrize("label", sorted(LOOKALIKES))
def test_what_only_looks_like_a_super_constructor_call_records_nothing_for_it(label):
    source, owner, expected = LOOKALIKES[label]
    assert _constructors(source, owner) == expected


def test_a_method_records_nothing_for_its_class_superclass():
    """Only a constructor's `super(...)` is read: a method of the class records what it calls."""
    source = "class Kid extends Base {\n  Kid() { super(2); }\n  int run() { return other(); }\n}\n"
    symbols = parse_file(source, "A.java", "java")
    run = [s for s in symbols if s.name == "run"]
    assert [s.call_references for s in run] == [["other"]]


def test_only_java_reads_a_class_declaration_for_calls():
    """`class_declaration` is a node of several grammars here; only Java's row asks for it."""
    holders = sorted(lang for lang, types in _CALL_NODE_TYPES.items() if "class_declaration" in types)
    assert holders == ["java"]


# Two Python files joined by an import, so the index has an import graph and the
# tool runs its three-signal path.
PYTHON_PAIR = {
    "util.py": "def b():\n    return 1\n",
    "py.py": "from util import b\n\ndef a():\n    return b()\n",
}

JAVA = {
    "Base.java": "package p;\n\nabstract class Base {\n  Base(int x) { }\n  abstract int run();\n}\n",
    "Kid.java": "package p;\n\nclass Kid extends Base {\n  Kid() { super(2); }\n  int run() { return 1; }\n}\n",
    "Lonely.java": "package p;\n\nabstract class Lonely {\n  Lonely(int x) { }\n}\n",
    # A recorded Java call, so the tool reads Java by call references and not as text (L-155).
    "Use.java": "package p;\n\nclass Use {\n  int go(Base b) { return b.run(); }\n}\n",
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


def test_a_constructor_reached_only_by_super_is_not_reported_without_callers(tmp_path):
    rows = _signals(tmp_path, {**PYTHON_PAIR, **JAVA})
    assert "no_callers" not in rows.get(("Base.java", "Base"), [])


def test_a_constructor_nothing_reaches_is_still_reported(tmp_path):
    rows = _signals(tmp_path, {**PYTHON_PAIR, **JAVA})
    assert "no_callers" in rows[("Lonely.java", "Lonely")], rows
    assert "no_callers" in rows[("Kid.java", "Kid")], rows


def test_an_existing_index_is_reparsed():
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 14
