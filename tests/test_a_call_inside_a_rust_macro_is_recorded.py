"""A call written inside a Rust macro invocation is recorded (LEDGER L-164, item 1).

The Rust grammar does not parse a macro's arguments: `assert_eq!(e(x), seq([y]))`
is a `macro_invocation` holding a `token_tree`, and the tree holds tokens, not
expressions. The parser recorded a call only for a `call_expression`, so a
function called only inside `assert_eq!`, `vec!`, `println!` or a test macro had
no caller: `get_dead_code_v2` reported it with `no_callers`, live code as dead.
Rust tests are written almost entirely inside such macros.

The rule reads the tokens: a name directly followed by a parenthesised group is
a call of that name. Every case goes through `parse_file`, and the dead-code
cases through `index_folder` and the tool.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.tools import get_dead_code_v2 as v2
from jcodemunch_mcp.tools.index_folder import index_folder


def _run_records(body: str) -> list[str]:
    source = "fn run(t: T) {\n" + body + "}\n"
    symbols = parse_file(source, "a.rs", "rust")
    runs = [s for s in symbols if s.name == "run"]
    assert len(runs) == 1, [s.name for s in symbols]
    return runs[0].call_references


# body of `run` -> what it records, in source order
CASES = {
    "a function call in an assertion": ("    assert_eq!(e(x), seq([y]));\n", ["e", "seq"]),
    "a chain of calls": ('    eqnice!(cmd().args(&["a"]).stdout(), "s");\n', ["cmd", "args", "stdout"]),
    "a method on a receiver": ("    assert!(t.is_ready());\n", ["is_ready"]),
    "a path": ('    println!("{}", Type::assoc(1));\n', ["assoc"]),
    "a longer path": ("    dbg!(crate::flags::config::args());\n", ["args"]),
    "a turbofish": ("    dbg!(t.parse::<u8>(2), make::<Vec<u8>>(3));\n", ["parse", "make"]),
    "a bracketed macro": ("    let v = vec![inner(1), other(2)];\n", ["inner", "other"]),
    "a braced macro": ("    let v = block! { first(1); second(2) };\n", ["first", "second"]),
    "an argument of a call": ("    assert!(outer(inner(1)));\n", ["outer", "inner"]),
    "a macro inside a macro": ("    nested!(other!(deep(1)));\n", ["deep"]),
    "a closure body": ("    rgtest!(name, |dir: Dir| { dir.create(1); helper(dir); });\n", ["create", "helper"]),
    "beside an ordinary call": ("    before();\n    assert!(inside());\n    after();\n", ["before", "inside", "after"]),
    "a turbofish inside a path": ("    dbg!(Vec::<u8>::new());\n", ["new"]),
    # `<<` is one token where a turbofish opens with a qualified type.
    "a turbofish that opens with a qualified type": (
        "    m!(f::<<T as Tr>::Out>(1), g::<Option<<T as Tr>::Out>>(2));\n", ["f", "g"],
    ),
    # A test that defines a local macro and calls its helper only in the body
    # (ripgrep's `sgramq`): every arm's body is read.
    "the body of a macro_rules arm": (
        "    macro_rules! t {\n"
        "        ($regex:expr, $expected:expr) => { assert_eq!(sgramq($regex), $expected); };\n"
        "        ($one:expr) => { second_arm($one) };\n"
        "    }\n",
        ["sgramq", "second_arm"],
    ),
    "a comment between the name and its group": ("    m!(g /* why */ (3), h // why\n (4));\n", ["g", "h"]),
    "calls at several depths, in source order": (
        "    m!(last(1), first(inner(2)), mid!(deep(3)), tail(4));\n", ["last", "first", "inner", "deep", "tail"],
    ),
    # `default`, `union` and `gen` are contextual: a function may carry the
    # name, and the grammar gives each its own token kind, not `identifier`.
    "a contextual keyword as the name": (
        "    assert_eq!(x, Foo::default());\n    assert!(a.union(&b).count() == 1);\n    let v = vec![gen(3), default()];\n",
        ["default", "union", "count", "gen"],
    ),
    # A tuple-struct or enum-variant constructor has the same tokens as a call.
    "a constructor": ("    assert!(matches!(x, Wrapped(y)));\n", ["Wrapped"]),
}

# tokens that look like `name (` and are not a call
LOOKALIKES = {
    "a keyword before a group": (
        "    m! { if (a) { } else { } while (q) { } for i in (0..n) { } return (z); match (a, b) { } }\n"
    ),
    "the macro's own name": ("    outer_macro!(1);\n    inner_macro!(other_macro!(2));\n"),
    "a function defined in the macro": ("    items! { fn defined(x: u8) { } pub fn also_defined(y: u8) { } }\n"),
    "a type declared in the macro": ("    items! { struct Pair(u8, u8); enum E { A(u8) } }\n"),
    "a group with no name before it": ("    m!((1, 2), [3], (4));\n"),
    "a bracket or brace after a name": ("    m!(xs[0], S { a: 1 });\n"),
    "a literal or a primitive type before a group": ('    m!(2 (3), u8 (4), "s" (5));\n'),
    # An attribute's arguments are a token tree too, and none of them is a call.
    "an attribute's arguments": (
        "    #[cfg(any(unix, not(windows)))]\n    let x = 1;\n    #[allow(clippy::too_many(x))]\n    let y = 2;\n"
    ),
    "closure-trait sugar": ("    m!(Box<dyn Fn(u8) -> u8>, FnOnce(u8), FnMut(u8));\n"),
    "a lifetime or a label before a group": ("    m!(&'a (dyn Tr), break 'lbl (1));\n"),
    "a reserved word the grammar calls an identifier": ("    m!(try (x), do (y), become (z));\n"),
    "a turbofish that never closes": ("    m!(x::<T (1));\n"),
    # Inside a macro an attribute is tokens too: `#` then a bracketed group.
    "an attribute written in the macro's body": (
        "    items! { #[cfg(not(test))] fn f() { } #[derive(Clone(x))] struct S; #![allow(unused(x))] }\n"
    ),
    "an underscore before a group": ("    m!(_ (1), matches!(x, _ (y)));\n"),
    "a macro called on its own result": ("    let x = mac!(1)(2);\n"),
    # The PATTERN of a `macro_rules!` arm declares shapes, it calls nothing;
    # and a metavariable before a group is not a name.
    "a macro_rules pattern and a metavariable": (
        "    macro_rules! mr { ($f:ident ( $x:expr ), named ( $y:expr )) => { $f($x); }; }\n"
    ),
    # Angle brackets that are comparisons, not a turbofish: no `::` before them.
    "a comparison before a group": ("    m!(k a < b > (d));\n"),
}
# `A(u8)` in the enum above is a variant with a field: the same tokens as a
# call, and recorded. Stated here so the list below is exact.
LOOKALIKE_RECORDS = {"a type declared in the macro": ["A"]}


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_call_in_a_macro_is_recorded(case):
    body, expected = CASES[case]
    assert _run_records(body) == expected


@pytest.mark.parametrize("case", sorted(LOOKALIKES))
def test_tokens_that_are_not_a_call_record_nothing(case):
    assert _run_records(LOOKALIKES[case]) == LOOKALIKE_RECORDS.get(case, [])


def test_a_call_outside_a_macro_is_recorded_as_before():
    assert _run_records("    bare();\n    t.recv();\n    Type::assoc();\n") == ["bare", "recv", "assoc"]


def test_an_existing_index_is_re_parsed_for_it():
    """Call references change on unchanged content, so the generation moved."""
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 13


def test_only_rust_reads_tokens_this_way():
    """The rule is in Rust's row of the table and nowhere else."""
    from jcodemunch_mcp.parser.extractor import _CALL_NODE_TYPES

    assert [lang for lang, types in _CALL_NODE_TYPES.items() if types & {"macro_invocation", "macro_definition"}] == ["rust"]


# ---- through index_folder and get_dead_code_v2

PYTHON_PAIR = {
    "util.py": "def b():\n    return 1\n",
    "py.py": "from util import b\n\ndef a():\n    return b()\n",
}

RUST = (
    "fn used_in_assert(x: i32) -> i32 { x }\n\n"
    "fn used_in_vec(x: i32) -> i32 { x }\n\n"
    "fn unused_thing(x: i32) -> i32 { x }\n\n"
    "struct T;\n\nimpl T {\n    fn used_method(&self) -> i32 { 1 }\n    fn unused_method(&self) -> i32 { 2 }\n}\n\n"
    "fn run(t: T) {\n"
    "    assert_eq!(used_in_assert(1), 1);\n"
    "    let _v = vec![used_in_vec(2)];\n"
    "    assert!(t.used_method() == 1);\n"
    "}\n"
)


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
    return {(row["file"], row["name"]): row["signals"] for row in out["dead_symbols"]}


@pytest.mark.parametrize("name", ["used_in_assert", "used_in_vec", "used_method"])
def test_a_function_called_only_inside_a_macro_is_not_reported_without_callers(tmp_path, name):
    rows = _signals(tmp_path, {**PYTHON_PAIR, "a.rs": RUST})
    assert "no_callers" not in rows.get(("a.rs", name), []), rows


@pytest.mark.parametrize("name", ["unused_thing", "unused_method"])
def test_a_function_nothing_calls_is_still_reported(tmp_path, name):
    rows = _signals(tmp_path, {**PYTHON_PAIR, "a.rs": RUST})
    assert "no_callers" in rows[("a.rs", name)], rows
