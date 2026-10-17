"""A call through a receiver records the METHOD's name, never the receiver's (LEDGER L-159).

`_extract_call_name` knew two member shapes, JavaScript's and Python's. For
every other callee it fell back to "the first identifier inside", and in Go,
Java, Rust, Ruby and C# the first identifier inside `t.Recv()` is `t`. So a
call reference named the RECEIVER: the method read as never called
(`get_dead_code_v2` reported it with `no_callers`, live code as dead), and a
function that happened to share the receiver's name read as called.

Each case below is a function `run` whose body is one call per line. The
expected list is what `run` must record, in source order. Every fixture goes
through `parse_file`, and the dead-code cases through `index_folder` and the
tool, so nothing here can carry a reference the real parser never writes.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.tools import get_dead_code_v2 as v2
from jcodemunch_mcp.tools.index_folder import index_folder

# language -> (file name, source, what `run` records)
CASES = {
    "go": (
        "a.go",
        "package p\n"
        "func run(t *T, xs []int) {\n"
        "\tbare()\n"
        "\tt.Recv()\n"
        "\tt.inner.Deep()\n"
        "\tpkg.Qual()\n"
        "\tt.Chain().After()\n"
        "\tt.GenM[int]()\n"
        "\t(t).Paren()\n"
        "\tfs[0]()\n"
        "\tfunc() { InLit() }()\n"
        "\tgo t.Spawned()\n"
        "\tdefer t.Deferred()\n"
        "}\n",
        ["bare", "Recv", "Deep", "Qual", "After", "Chain", "GenM", "Paren", "fs", "InLit",
         "Spawned", "Deferred"],
    ),
    "java": (
        "A.java",
        "class A {\n"
        "  void run(B a) {\n"
        "    bare();\n"
        "    a.recv();\n"
        "    this.own();\n"
        "    a.inner.deep();\n"
        "    B.stat();\n"
        "    a.chain().after();\n"
        "    new Made();\n"
        "    new pkg.Qualified();\n"
        "    a.<String>generic();\n"
        "    super.parent();\n"
        "    new Outer.Inner<String>();\n"
        "  }\n"
        "}\n",
        ["bare", "recv", "own", "deep", "stat", "after", "chain", "Made", "Qualified", "generic", "parent",
         "Inner"],
    ),
    "rust": (
        "a.rs",
        "fn run(t: T) {\n"
        "    bare();\n"
        "    t.recv();\n"
        "    self.selfm();\n"
        "    t.inner.deep();\n"
        "    Type::assoc();\n"
        "    module::path::qual();\n"
        "    t.chain().after();\n"
        "    generic::<i32>(1);\n"
        "    t.genm::<i32>();\n"
        "    (t.field)();\n"
        "    Self::selfassoc();\n"
        "}\n",
        ["bare", "recv", "selfm", "deep", "assoc", "qual", "after", "chain", "generic", "genm", "field",
         "selfassoc"],
    ),
    "ruby": (
        "a.rb",
        "def run(t)\n"
        "  bare()\n"
        "  bare_no_parens 1\n"
        "  t.recv\n"
        "  t.recv_args(1)\n"
        "  self.selfm\n"
        "  t.inner.deep\n"
        "  Mod.stat\n"
        "  Mod::scoped(1)\n"
        "  t.chain.after\n"
        "  Made.new\n"
        "  t&.safe\n"
        "  blk { |x| x.inblock }\n"
        "end\n",
        # `t` and `x` too: see RUBY_BARE_RECEIVER below.
        ["bare", "bare_no_parens", "recv", "t", "recv_args", "selfm", "deep", "inner", "stat", "scoped", "after",
         "chain", "new", "safe", "blk", "inblock", "x"],
    ),
    "csharp": (
        "A.cs",
        "class A {\n"
        "  void Run(B a) {\n"
        "    Bare();\n"
        "    a.Recv();\n"
        "    this.Own();\n"
        "    a.Inner.Deep();\n"
        "    B.Stat();\n"
        "    a.Chain().After();\n"
        "    a.Generic<string>();\n"
        "    Generic2<int>(1);\n"
        "    a?.Cond();\n"
        "    base.Parent();\n"
        "  }\n"
        "}\n",
        ["Bare", "Recv", "Own", "Deep", "Stat", "After", "Chain", "Generic", "Generic2", "Cond", "Parent"],
    ),
    # The two shapes the old rule already knew, and the ones beside them it got
    # wrong: a private method and a parenthesised callee.
    "javascript": (
        "a.js",
        "function run(t) {\n"
        "  bare();\n"
        "  t.recv();\n"
        "  this.own();\n"
        "  t.inner.deep();\n"
        "  t.chain().after();\n"
        "  new Made();\n"
        "  new ns.Qualified();\n"
        "  t?.optional();\n"
        "  t.#priv();\n"
        "  (t.paren)();\n"
        "}\n",
        ["bare", "recv", "own", "deep", "after", "chain", "Made", "Qualified", "optional", "#priv", "paren"],
    ),
    "typescript": (
        "a.ts",
        "function run(t: T) {\n"
        "  bare();\n"
        "  t.recv();\n"
        "  t.chain().after();\n"
        "  new ns.Qualified<string>();\n"
        "  generic<string>(1);\n"
        "  t.genm<string>(1);\n"
        "  t!.nonnull();\n"
        "  (t as U).cast();\n"
        "  t.maybe!();\n"
        "}\n",
        ["bare", "recv", "after", "chain", "Qualified", "generic", "genm", "nonnull", "cast", "maybe"],
    ),
    "python": (
        "a.py",
        "def run(t):\n"
        "    bare()\n"
        "    t.recv()\n"
        "    self.selfm()\n"
        "    t.inner.deep()\n"
        "    t.chain().after()\n"
        "    (t.paren)()\n"
        "    Made()\n",
        ["bare", "recv", "selfm", "deep", "after", "chain", "paren", "Made"],
    ),
}

# A callee that is an element of a container records the container: there is
# no method name to record, and the container is what holds the function.
CONTAINER_CASES = {
    "go": ("a.go", "package p\nfunc run() {\n\tfs[0]()\n\tm.handlers[k]()\n}\n", ["fs", "handlers"]),
    "python": ("a.py", "def run(t):\n    fs[0]()\n    t.handlers[k]()\n", ["fs", "handlers"]),
    "javascript": ("a.js", "function run(t) {\n  fs[0]();\n  t.handlers[k]();\n}\n", ["fs", "handlers"]),
    # C#'s element access, its conditional form, and a null-forgiving callee.
    "csharp": (
        "A.cs",
        "class A {\n  void Run(B a) {\n    fs[0]();\n    a.handlers[k](1);\n    cond?[0]();\n    forgiven!();\n  }\n}\n",
        ["fs", "handlers", "cond", "forgiven"],
    ),
}

# A callee with no name records nothing, and never the name of something
# inside it.
NAMELESS_CASES = {
    "go": ("a.go", "package p\nfunc run(t T) {\n\tfunc(x int) { }(t.v)\n}\n"),
    "javascript": ("a.js", "function run(t) {\n  (function (x) { })(t.v);\n  ((x) => x)(t);\n}\n"),
    "python": ("a.py", "def run(t):\n    (lambda x: x)(t.v)\n"),
    # `t.call_me.()` holds one named call, `t.call_me`, and one nameless one;
    # `t` is Ruby's bare receiver (RUBY_BARE_RECEIVER).
    "ruby": ("a.rb", "def run(t)\n  t.()\n  t.call_me.()\n  Mod.()\nend\n"),
    "rust": ("a.rs", "fn run(t: T) {\n    (|x| x)(1);\n}\n"),
}
NAMELESS_RECORDS = {"ruby": ["t", "call_me"]}

# Ruby writes a method called with no receiver and no arguments exactly like a
# local variable, so the receiver in `settings.environment` may be a call of
# `settings`. It is recorded beside the method. Only a bare identifier: a
# constant, `self` or another call in that place is not.
RUBY_BARE_RECEIVER = (
    "a.rb",
    "def run(t)\n  settings.environment\n  Mod.stat\n  self.selfm\n  @ivar.read\n  t.a.b\nend\n",
    ["environment", "settings", "stat", "selfm", "read", "b", "a", "t"],
)

# Languages the parser names call nodes for and records nothing in (L-158).
# `get_dead_code_v2` reads such a file as text BECAUSE the language records no
# call; a parser that began recording some of their shapes would take that
# evidence away for the rest. This pins them at none until L-158 does it whole.
RECORDS_NOTHING = {
    "php": ("a.php", "<?php\nnamespace App;\nfunction run($t) {\n  bare();\n  $t->recv();\n  K::stat();\n  new Made();\n}\n"),
    "kotlin": ("a.kt", "fun run(t: T) {\n  bare()\n  t.recv()\n  T.stat()\n  Made()\n}\n"),
    "swift": ("a.swift", "func run(t: T) {\n  bare()\n  t.recv()\n  T.stat()\n  Made()\n}\n"),
    "dart": ("a.dart", "int run(T t) {\n  bare(1);\n  t.recv();\n  T.stat();\n  return 1;\n}\n"),
}


def _run_records(language, fname, source):
    symbols = parse_file(source, fname, language)
    runs = [s for s in symbols if s.name.lower() == "run"]
    assert len(runs) == 1, [s.name for s in symbols]
    return runs[0].call_references


@pytest.mark.parametrize("language", sorted(CASES))
def test_each_call_records_the_name_of_what_is_called(language):
    fname, source, expected = CASES[language]
    assert _run_records(language, fname, source) == expected


@pytest.mark.parametrize("language", sorted(CASES))
def test_the_receiver_is_never_recorded(language):
    fname, source, _ = CASES[language]
    recorded = set(_run_records(language, fname, source))
    receivers = {"t", "a", "x", "pkg", "ns", "Type", "Self", "B", "Mod", "module", "this", "self"}
    if language == "ruby":
        receivers -= {"t", "x"}  # a bare identifier may be a call there
    assert not recorded & receivers


def test_a_bare_ruby_receiver_is_recorded_beside_the_method():
    fname, source, expected = RUBY_BARE_RECEIVER
    assert _run_records("ruby", fname, source) == expected


@pytest.mark.parametrize("language", sorted(CONTAINER_CASES))
def test_a_call_of_a_container_element_records_the_container(language):
    fname, source, expected = CONTAINER_CASES[language]
    assert _run_records(language, fname, source) == expected


@pytest.mark.parametrize("language", sorted(NAMELESS_CASES))
def test_a_callee_with_no_name_records_nothing(language):
    fname, source = NAMELESS_CASES[language]
    assert _run_records(language, fname, source) == NAMELESS_RECORDS.get(language, [])


@pytest.mark.parametrize("language", sorted(RECORDS_NOTHING))
def test_a_language_that_recorded_no_call_still_records_none(language):
    fname, source = RECORDS_NOTHING[language]
    assert _run_records(language, fname, source) == []


def test_an_existing_index_is_re_parsed_for_it():
    """Call references change on unchanged content, so the generation moved."""
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 12


# ---- through index_folder and get_dead_code_v2: the ledger row's measurement

PYTHON_PAIR = {
    "util.py": "def b():\n    return 1\n",
    "py.py": "from util import b\n\ndef a():\n    return b()\n",
}

# `used` is called only through a receiver inside `run`; `unused` by nothing.
DEAD_CODE = {
    "go": (
        "a.go",
        "package p\n\ntype T struct{}\n\nfunc (t *T) UsedMethod() int { return 1 }\n\n"
        "func (t *T) UnusedMethod() int { return 2 }\n\nfunc helper() int { return 3 }\n\n"
        "func Run(t *T) int {\n\treturn t.UsedMethod() + helper()\n}\n",
        "UsedMethod", "UnusedMethod",
    ),
    "java": (
        "A.java",
        "class A {\n  int usedMethod() { return 1; }\n  int usedMethod2() { return 1; }\n"
        "  int unusedMethod() { return 2; }\n"
        "  int run(A a) { return a.usedMethod() + this.usedMethod2(); }\n}\n",
        "usedMethod", "unusedMethod",
    ),
    "rust": (
        "a.rs",
        "struct T;\n\nimpl T {\n    fn used_method(&self) -> i32 { 1 }\n    fn unused_method(&self) -> i32 { 2 }\n}\n\n"
        "fn run(t: T) -> i32 {\n    t.used_method()\n}\n",
        "used_method", "unused_method",
    ),
    "ruby": (
        "a.rb",
        "class T\n  def used_method\n    1\n  end\n\n  def unused_method\n    2\n  end\nend\n\n"
        "def run(t)\n  t.used_method\nend\n",
        "used_method", "unused_method",
    ),
    "csharp": (
        "A.cs",
        "class A {\n  int UsedMethod() { return 1; }\n  int UnusedMethod() { return 2; }\n"
        "  int Run(A a) { return a.UsedMethod(); }\n}\n",
        "UsedMethod", "UnusedMethod",
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
    return {(row["file"], row["name"]): row["signals"] for row in out["dead_symbols"]}


@pytest.mark.parametrize("language", sorted(DEAD_CODE))
def test_a_method_called_through_a_receiver_is_not_reported_without_callers(tmp_path, language):
    fname, body, used, unused = DEAD_CODE[language]
    rows = _signals(tmp_path, {**PYTHON_PAIR, fname: body})
    assert "no_callers" not in rows.get((fname, used), []), f"{language}: `{used}` is called in `run`"
    assert "no_callers" in rows[(fname, unused)], rows


def test_a_method_called_through_this_is_not_reported_without_callers(tmp_path):
    fname, body, _, _ = DEAD_CODE["java"]
    rows = _signals(tmp_path, {**PYTHON_PAIR, fname: body})
    assert "no_callers" not in rows.get((fname, "usedMethod2"), [])


def test_a_function_named_like_a_receiver_is_not_called_by_it(tmp_path):
    """The other direction: `t.UsedMethod()` is not a call of a function `t`."""
    body = (
        "package p\n\ntype T struct{}\n\nfunc (x *T) UsedMethod() int { return 1 }\n\n"
        "func t() int { return 2 }\n\n"
        "func Run(t *T) int {\n\treturn t.UsedMethod()\n}\n"
    )
    rows = _signals(tmp_path, {**PYTHON_PAIR, "a.go": body})
    assert "no_callers" in rows[("a.go", "t")], rows
