"""A Ruby method called with no receiver and no arguments is recorded (LEDGER L-172, item 6).

Ruby writes a call of a method with no receiver and no arguments as a bare
name: `settings`. The grammar gives that the same `identifier` node as a local
variable, and only a `call` node was read for calls, so the commonest way one
Ruby method calls another recorded nothing unless the name happened to be a
receiver (`settings.environment`). A helper called only that way read
`no_callers` in `get_dead_code_v2`: live code as dead.

Ruby itself decides the question by what the method has assigned so far: a
bare name is a local variable once an assignment to it, a parameter, a block
parameter or a pattern has introduced it, and a method call otherwise. The
parser reads each method the same way.

⚠ A method's bare calls are listed before its other calls: they are read when
the method node is met, ahead of the call nodes inside it.

Every case goes through `parse_file`, and the dead-code cases through
`index_folder` and the tool.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import _CALL_NODE_TYPES, parse_file
from jcodemunch_mcp.tools import get_dead_code_v2 as v2
from jcodemunch_mcp.tools.index_folder import index_folder


def _method(source: str, name: str = "host") -> list[str]:
    found = [s for s in parse_file(source, "a.rb", "ruby") if s.kind in ("method", "function") and s.name == name]
    assert len(found) == 1, [(s.kind, s.qualified_name) for s in parse_file(source, "a.rb", "ruby")]
    return found[0].call_references


def _records(body: str, head: str = "def host(arg)") -> list[str]:
    """What a method with this body records."""
    lines = "\n".join("    " + line for line in body.split("\n"))
    return _method("class C\n  " + head + "\n" + lines + "\n  end\nend\n")


# the method's body -> what it records
CASES = {
    "alone": ("settings", ["settings"]),
    "a statement among others": ("a = 1\nsettings\na", ["settings"]),
    "an argument": ("use(settings)", ["settings", "use"]),
    "in an interpolation": ('"a #{name_of_it} b"', ["name_of_it"]),
    "in a heredoc's interpolation": ("<<~T\n  word #{inside_of}\nT", ["inside_of"]),
    "in an array and a hash value": ("[first_one, { k: second_one }]", ["first_one", "second_one"]),
    "the right of an assignment": ("x = compute\nx", ["compute"]),
    "both sides of an operator": ("total + offset", ["total", "offset"]),
    "returned": ("return result_of_it", ["result_of_it"]),
    "the arms of a ternary": ("ok? ? yes_path : no_path", ["yes_path", "no_path", "ok?"]),
    "a condition": ("if ready_now\n  1\nend", ["ready_now"]),
    "a modifier condition": ("return 1 unless finished", ["finished"]),
    "a case subject": ("case kind_of\nwhen 1 then 2\nend", ["kind_of"]),
    "inside a block": ("items.each { |i| handle(i, context_of) }", ["context_of", "each", "items", "handle"]),
    "inside a do block": ("items.each do |i|\n  emit\nend", ["emit", "each", "items"]),
    "a block parameter's default": ("items.each { |i, j = fallback| i }", ["fallback", "each", "items"]),
    "inside a lambda": ("f = -> { compute }\nf", ["compute"]),
    "an indexed result": ("table[1]", ["table"]),
    "the source of a multiple assignment": ("a, b = pair_of\na + b", ["pair_of"]),
    "the range of a for loop": ("for i in range_of\n  i\nend", ["range_of"]),
    "inside begin and rescue": ("begin\n  risky\nrescue StandardError => e\n  cleanup\n  e\nend", ["risky", "cleanup"]),
    "the subject of a pattern match": ("case thing\nin [a, b]\n  a\nend", ["thing"]),
    # Ruby reads a name as a call until an assignment to it has been seen.
    "before the assignment that makes it a local": ("y = x_before\nx_before = 2\ny", ["x_before"]),
    "the same name twice is recorded once": ("settings\nsettings", ["settings"]),
    "under defined?": ("defined?(maybe)", ["maybe"]),
}

# What is a local variable, or not a name being called. Nothing new is recorded.
LOOKALIKES = {
    "a local variable": ("x = 1\nx", []),
    "a parameter": ("arg", []),
    "a block parameter": ("items.each { |i| i }", ["each", "items"]),
    "a block parameter after its block": ("items.each { |i| i }\ni", ["each", "items"]),
    "an operator-assigned local": ("count ||= 0\ncount += 1\ncount", []),
    "a local read on the right of its own assignment": ("x = x\nx", []),
    "the targets of a multiple assignment": ("a, b = 1, 2\na + b", []),
    "a splat target": ("first, *rest = [1, 2]\nrest", []),
    "a for loop variable": ("for i in [1]\n  i\nend", []),
    "a rescue variable": ("begin\n  1\nrescue StandardError => e\n  e\nend", []),
    "a lambda parameter": ("f = ->(v) { v }\nf", []),
    "a pattern's captures": ("case 1\nin [a, b]\n  a + b\nend", []),
    "a hash pattern's capture": ("case 1\nin {name:}\n  name\nend", []),
    "a destructured block parameter": ("pairs.each { |(a, b), c| a + b + c }", ["each", "pairs"]),
    "a nested multiple-assignment target": ("a, (b, c) = 1, [2, 3]\na + b + c", []),
    "a one-line test pattern's captures": ("if arg in [a, b]\n  a + b\nend", []),
    "a rightward pattern's captures": ("arg => {name:, other: o}\nname + o", []),
    "an implicit block parameter": ("items.map { it + 1 }", ["map", "items"]),
    "a numbered block parameter": ("items.map { _1 + _2 }", ["map", "items"]),
    "a parameter the grammar could not parse": ("f = ->(x; y) { x + y }\nf", []),
    "the names in an alias": ("alias fresh stale", []),
    "the name in an undef": ("undef stale", []),
    "a constant": ("Settings", []),
    "an instance, class and global variable": ("@settings; @@c; $g", []),
    "a symbol and a string": ("[:settings, 'settings']", []),
    "a keyword argument's label": ("use(settings: 1)", ["use"]),
    "a hash key": ("{ settings: 1 }", []),
    "a method named by a symbol": ("method(:settings)", ["method"]),
    "yield and super": ("yield\nsuper", []),
    "self": ("self", []),
    # Already a call node, before and after.
    "a call with arguments": ("helper(1)", ["helper"]),
    "a call with a mark": ("ready?", ["ready?"]),
    "a call through self": ("self.settings", ["settings"]),
    "a receiver": ("settings.environment", ["environment", "settings"]),
    # A call of the enclosing method's own name is dropped wherever calls are attributed.
    "the method's own name": ("host", []),
}


@pytest.mark.parametrize("label", sorted(CASES))
def test_a_bare_name_that_is_no_local_is_recorded(label):
    body, expected = CASES[label]
    assert _records(body) == expected


@pytest.mark.parametrize("label", sorted(LOOKALIKES))
def test_a_local_or_a_name_not_called_records_nothing_new(label):
    body, expected = LOOKALIKES[label]
    assert _records(body) == expected


def test_a_parameter_default_is_read():
    assert _records("opt", head="def host(arg, opt = default_of, key: key_default)") == ["default_of", "key_default"]


def test_every_kind_of_parameter_is_a_local():
    head = "def host(a, b = 1, *c, d:, e: 2, **f, &g)"
    assert _records("a; b; c; d; e; f; g", head=head) == []


@pytest.mark.parametrize("head", [
    "def host=(value)", "def host= value", "def self.host=(value)", "def self.host= value",
])
def test_a_setter_does_not_call_the_reader_of_its_name(head):
    """`def host=(v)` names itself in a `setter` node. That name is not a call of `host`."""
    source = "class C\n  def host\n    @host\n  end\n  " + head + "\n    @host = value\n  end\nend\n"
    setter = [s for s in parse_file(source, "a.rb", "ruby") if s.name == "host="]
    assert [s.call_references for s in setter] == [[]]


def test_a_setter_whose_parameter_has_its_name_records_nothing():
    source = "class C\n  def self.assertions= assertions\n    @assertions = assertions\n  end\nend\n"
    setter = [s for s in parse_file(source, "a.rb", "ruby") if s.name == "assertions="]
    assert [s.call_references for s in setter] == [[]]


def test_a_singleton_method_is_read():
    source = "class C\n  def self.host(arg)\n    settings\n    arg\n  end\nend\n"
    assert _method(source) == ["settings"]


def test_a_method_outside_a_class_is_read():
    assert _method("def host(arg)\n  settings\nend\n") == ["settings"]


def test_a_nested_method_records_its_own():
    source = "class C\n  def host(arg)\n    def inner\n      settings\n    end\n    inner\n  end\nend\n"
    assert _method(source) == ["inner"]
    assert _method(source, "inner") == ["settings"]


def test_a_local_of_a_nested_method_is_not_a_local_of_the_method_around_it():
    """`def` opens a scope of its own: what it assigns is unknown outside it."""
    source = "class C\n  def host(arg)\n    def inner\n      leaked = 1\n    end\n    leaked\n  end\nend\n"
    assert _method(source) == ["leaked"]
    assert _method(source, "inner") == []


def test_the_object_of_a_singleton_method_is_not_a_call():
    """`def obj.m`: `obj` is a local of the scope around the method, and the method's own walk cannot know it."""
    source = "class C\n  def host(arg)\n    o = build\n    def o.inner\n      1\n    end\n    o\n  end\nend\n"
    assert _method(source) == ["build"]
    assert _method(source, "inner") == []


def test_a_local_of_a_nested_singleton_method_is_not_a_local_of_the_method_around_it():
    source = "class C\n  def host(arg)\n    def self.inner\n      leaked = 1\n    end\n    leaked\n  end\nend\n"
    assert _method(source) == ["leaked"]


@pytest.mark.parametrize("opener", ["class << self", "class Inner", "module Inner"])
def test_a_class_body_inside_a_method_is_a_scope_of_its_own(opener):
    """A class body is in no method: its bare names are not the method's, and neither are its locals."""
    body = opener + "\n  inner_local = 1\n  bare_in_the_body\nend\ninner_local"
    assert _records(body) == ["inner_local"]


def test_a_local_of_one_method_is_not_a_local_of_the_next():
    source = "class C\n  def first\n    value = 1\n    value\n  end\n  def host(arg)\n    value\n  end\nend\n"
    assert _method(source) == ["value"]
    assert _method(source, "first") == []


def test_only_ruby_reads_a_method_node_for_calls():
    holders = sorted(lang for lang, types in _CALL_NODE_TYPES.items() if "method" in types or "singleton_method" in types)
    assert holders == ["ruby"]


# Two Python files joined by an import, so the index has an import graph and the
# tool runs its three-signal path.
PYTHON_PAIR = {
    "util.py": "def b():\n    return 1\n",
    "py.py": "from util import b\n\ndef a():\n    return b()\n",
}

RUBY = {
    "helpers.rb": (
        "class Helpers\n"
        "  def helper_thing\n    1\n  end\n"
        "  def lonely_thing\n    2\n  end\n"
        "  def shadowed\n    3\n  end\n"
        "end\n"
    ),
    # `puts(x)` is a recorded Ruby call on both trees, so the tool reads Ruby
    # by call references and not as text (L-155).
    "user.rb": (
        "class User < Helpers\n"
        "  def go\n    x = helper_thing\n    puts(x)\n    shadowed = 4\n    shadowed\n  end\n"
        "end\n"
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


def test_a_method_called_by_its_bare_name_is_not_reported_without_callers(tmp_path):
    rows = _signals(tmp_path, {**PYTHON_PAIR, **RUBY})
    assert "no_callers" not in rows.get(("helpers.rb", "helper_thing"), [])


def test_a_method_nothing_calls_is_still_reported(tmp_path):
    rows = _signals(tmp_path, {**PYTHON_PAIR, **RUBY})
    assert "no_callers" in rows[("helpers.rb", "lonely_thing")], rows
    # A local variable of the same name is not a call of the method.
    assert "no_callers" in rows[("helpers.rb", "shadowed")], rows


def test_an_existing_index_is_reparsed():
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 17
