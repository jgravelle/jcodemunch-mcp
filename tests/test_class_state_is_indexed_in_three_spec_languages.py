"""A Dart, GDScript or Ruby class's state is indexed (#775, #777, #785).

The fourth mechanism in the member-kind audit family, and the first that is
purely SPEC-driven. #788 gave five custom parsers an owner, #774/#776/#779/#782
gave four of them the class state they never extracted, and #778 resolved Go's
receiver. Every language left in `_GAPS` reaches `_walk_tree` through a
`LanguageSpec`, so nothing here is a parser reproducing a rule it could have
asked for: the channels already exist and these three grammars were not wired
into them.

What was absent, measured through `parse_file` before the fix:

- **Dart**: `final int limit`, `int tally` and `static const int CAP` all
  absent; the class reported only its method and its getter.
- **GDScript**: `const LIMIT` and `var tally` absent INSIDE a class. ⚠ A
  top-level `const` was already indexed, so the const channel existed and the
  class-body scope was what it could not reach.
- **Ruby**: `LIMIT = 3`, `attr_accessor :view` and `@@count` all absent; the
  class reported only its method.

⚠⚠ **The shared constant rule decides two of these cells** (see
`_STATE_KIND_REFINERS`): a member is `constant` only where the language's own
dedicated constant keyword is used. Dart HAS `const`, so `final int limit` is a
`field` and only `static const int CAP` is a `constant` -- the C# `static
readonly` ruling, one language over. GDScript's `const` and Ruby's
SCREAMING_CASE assignment are the language's own constant form and are
`constant`.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """This box disables some languages in its own config; the gate is function-local."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _by_name(source: str, filename: str, language: str) -> dict[str, list]:
    out: dict[str, list] = {}
    for symbol in parse_file(source, filename, language):
        out.setdefault(symbol.name, []).append(symbol)
    return out


# --------------------------------------------------------------------------
# Dart (#775)
# --------------------------------------------------------------------------

_DART = (
    "class Audit {\n"
    "  final int limit = 3;\n"
    "  int tally = 0;\n"
    "  static const int CAP = 9;\n"
    "  int get view => 2;\n"
    "  int runIt() { return 1; }\n"
    "}\n"
)


@pytest.mark.parametrize("name,kind", [
    ("limit", "field"),
    ("tally", "field"),
    ("CAP", "constant"),
])
def test_a_dart_class_member_is_indexed_and_owned(name, kind):
    """⚠ `final` is NOT `constant`. Dart has its own `const`, so the shared rule
    puts `final int limit` in `field` and reserves `constant` for `const` --
    the same ruling C# `static readonly` got, and the opposite of the one Apex
    `static final` got, because Apex has no `const` to reserve."""
    found = _by_name(_DART, "a.dart", "dart")
    owner = found["Audit"][0]
    hits = found.get(name) or []
    assert hits, f"{name} was not extracted"
    assert len(hits) == 1, [(s.name, s.kind) for s in hits]
    assert hits[0].kind == kind, hits[0].kind
    assert hits[0].parent == owner.id, (name, hits[0].parent)
    assert hits[0].qualified_name == f"Audit.{name}"


def test_a_dart_declaration_naming_two_members_yields_two():
    """`int a, b;` is two members. Reading only the first indexes half a line --
    the rule Apex states and Groovy had to be fixed for (#779)."""
    source = "class Audit {\n  int a = 1, b = 2;\n}\n"
    found = _by_name(source, "two.dart", "dart")
    owner = found["Audit"][0]
    for name in ("a", "b"):
        assert found[name][0].kind == "field", name
        assert found[name][0].parent == owner.id, name


def test_a_dart_local_variable_is_not_a_member():
    """The channel next door. A binding inside a method body belongs to no
    class, and a fix reaching too widely would give it one -- the D defect of
    #776, where module-scope bindings became indexed members."""
    source = (
        "class Audit {\n"
        "  int runIt() {\n"
        "    final int local = 1;\n"
        "    return local;\n"
        "  }\n"
        "}\n"
    )
    hits = _by_name(source, "loc.dart", "dart").get("local", [])
    for hit in hits:
        assert hit.parent is None, hit.parent
        assert hit.kind not in ("field", "property", "constant"), hit.kind


@pytest.mark.parametrize("holder,owner_name,member,kind,source", [
    ("mixin", "Loud", "volume", "field",
     "mixin Loud {\n  int volume = 11;\n}\n"),
    ("extension", "Shout", "CAP", "constant",
     "extension Shout on String {\n  static const int CAP = 3;\n}\n"),
])
def test_a_dart_mixin_and_extension_member_is_owned_too(
    holder, owner_name, member, kind, source
):
    """⚠⚠ DART_SPEC declares three containers, so a fix reaching only
    `class_definition` reaches part of the language.

    ⚠ The set of holder NODE TYPES had to be measured rather than named: a
    `mixin_declaration` holds a `class_body`, not a `mixin_body`, so the
    obvious third entry would have been inert -- a guard written against a
    spelling the grammar does not use. `extension_body` is real.
    """
    found = _by_name(source, f"{holder}.dart", "dart")
    owner = found[owner_name][0]
    assert found[member][0].kind == kind
    assert found[member][0].parent == owner.id
    assert found[member][0].qualified_name == f"{owner_name}.{member}"


@pytest.mark.parametrize("label,source", [
    ("extension type",
     "extension type Meters(int v) {\n  static const int CAP = 1;\n}\n"),
    ("enum body",
     "enum E {\n  a, b;\n  static const int CAP = 1;\n}\n"),
])
def test_a_dart_body_with_no_container_symbol_contributes_no_member(label, source):
    """⚠⚠ The WITNESS for the holder gate, and the only shape that exercises
    it. Found in review, where the gate was measured INERT on everything else.

    Dart spells a method-body local `local_variable_declaration`, a different
    node type, so `test_a_dart_local_variable_is_not_a_member` stays green with
    the gate deleted -- it is excluded by the grammar, not by the rule. The one
    shape the gate actually decides is a body that looks like a class body and
    has no container above it.

    ⚠⚠ An `extension type` holds a `class_body` exactly as a class does, but
    `extension_type_declaration` is in no spec's `container_node_types`, so
    nothing stands above it to be the parent. Gated on the body type alone,
    `CAP` was published BARE -- a class member with no owner, which is #698's
    complaint and #788's entire subject. So the gate asks
    `DART_SPEC.container_node_types`, the list that already decides what will
    have a parent symbol, instead of keeping a second copy of it.

    ⚠ Both forms therefore contribute no members today. That is a limit in the
    absence direction and is pinned here so a later change moves it.
    """
    found = _by_name(source, f"{label.replace(' ', '_')}.dart", "dart")
    assert "CAP" not in found, [
        (s.name, s.kind, s.parent) for s in found.get("CAP", [])
    ]


def test_a_dart_method_and_getter_are_unchanged():
    """Non-regression: the two cells this language already answered correctly."""
    found = _by_name(_DART, "a.dart", "dart")
    owner = found["Audit"][0]
    for name in ("runIt", "view"):
        assert found[name][0].kind == "method", name
        assert found[name][0].parent == owner.id, name


# --------------------------------------------------------------------------
# GDScript (#777)
# --------------------------------------------------------------------------

_GDSCRIPT = (
    "class Audit:\n"
    "\tconst LIMIT = 3\n"
    "\tvar tally = 0\n"
    "\tfunc run_it():\n"
    "\t\treturn 1\n"
)


@pytest.mark.parametrize("name,kind", [
    ("LIMIT", "constant"),
    ("tally", "field"),
])
def test_a_gdscript_class_member_is_indexed_and_owned(name, kind):
    found = _by_name(_GDSCRIPT, "a.gd", "gdscript")
    owner = found["Audit"][0]
    hits = found.get(name) or []
    assert hits, f"{name} was not extracted"
    assert hits[0].kind == kind, hits[0].kind
    assert hits[0].parent == owner.id, (name, hits[0].parent)
    assert hits[0].qualified_name == f"Audit.{name}"


def test_a_gdscript_variable_inside_a_function_is_not_a_member():
    """⚠⚠ The over-reach guard, and GDScript needs it more than most: `var` is
    ONE node type (`variable_statement`) whether it declares class state or a
    local, so the only discriminator is what encloses it."""
    source = (
        "class Audit:\n"
        "\tfunc run_it():\n"
        "\t\tvar local = 1\n"
        "\t\treturn local\n"
    )
    hits = _by_name(source, "loc.gd", "gdscript").get("local", [])
    for hit in hits:
        assert hit.parent is None, hit.parent
        assert hit.kind not in ("field", "property", "constant"), hit.kind


def test_a_gdscript_top_level_const_keeps_its_bare_name():
    """Non-regression on the half that already worked: a file-scope `const`
    belongs to no class and must not acquire one."""
    source = "extends Node\nconst LIMIT = 3\n"
    hit = _by_name(source, "top.gd", "gdscript")["LIMIT"][0]
    assert hit.kind == "constant"
    assert hit.parent is None
    assert hit.qualified_name == "LIMIT"


def test_a_gdscript_top_level_var_is_still_absent_and_that_is_a_limit():
    """⚠⚠ Pinned because the scan found it and this change does NOT fix it.

    A GDScript file is itself a class, so a top-level `var` is arguably script
    state -- but it is the same `variable_statement` node as a function local,
    and telling them apart at file scope is a locality predicate this change
    does not have. #777 reports the CLASS-BODY cell and that is what moves
    here. Widening to file scope without the predicate would publish every
    local in every script, which is #732 one language over.

    ⚠ A later change must move this line rather than discover it.
    """
    source = "extends Node\nvar tally = 0\n"
    assert "tally" not in _by_name(source, "topvar.gd", "gdscript")


# --------------------------------------------------------------------------
# Ruby (#785)
# --------------------------------------------------------------------------

_RUBY = (
    "class Audit\n"
    "  LIMIT = 3\n"
    "  attr_accessor :view\n"
    "  attr_reader :seen\n"
    "  attr_writer :noted\n"
    "  @@count = 0\n"
    "  def run_it\n"
    "    1\n"
    "  end\n"
    "end\n"
)


@pytest.mark.parametrize("name,kind", [
    ("LIMIT", "constant"),
    ("view", "property"),
    ("seen", "property"),
    ("noted", "property"),
    ("@@count", "field"),
])
def test_a_ruby_class_member_is_indexed_and_owned(name, kind):
    """⚠ All three `attr_*` forms, not just `attr_accessor`. A guard written
    against one spelling is fixed for that spelling only, and `attr_reader` is
    the commonest of the three in real Ruby."""
    found = _by_name(_RUBY, "a.rb", "ruby")
    owner = found["Audit"][0]
    hits = found.get(name) or []
    assert hits, f"{name} was not extracted"
    assert hits[0].kind == kind, hits[0].kind
    assert hits[0].parent == owner.id, (name, hits[0].parent)
    assert hits[0].qualified_name == f"Audit.{name}"


def test_one_ruby_attr_accessor_naming_three_members_yields_three():
    """`attr_accessor :a, :b, :c` is three members in one call."""
    source = "class Audit\n  attr_accessor :a, :b, :c\nend\n"
    found = _by_name(source, "three.rb", "ruby")
    owner = found["Audit"][0]
    for name in ("a", "b", "c"):
        assert found[name][0].kind == "property", name
        assert found[name][0].parent == owner.id, name


def test_a_ruby_assignment_inside_a_method_is_not_a_member():
    """The over-reach guard. A local is an `assignment` too, and the LHS node
    type plus the enclosing scope are the only discriminators."""
    source = (
        "class Audit\n"
        "  def run_it\n"
        "    total = 1\n"
        "    total\n"
        "  end\n"
        "end\n"
    )
    hits = _by_name(source, "loc.rb", "ruby").get("total", [])
    for hit in hits:
        assert hit.parent is None, hit.parent
        assert hit.kind not in ("field", "property", "constant"), hit.kind


def test_a_ruby_call_that_is_not_an_attr_declaration_is_not_a_member():
    """⚠⚠ The `call` node is how `attr_accessor` is spelled, and it is also how
    EVERY other method call in a class body is spelled. Reading the node type
    alone would index `include Comparable` and `puts x` as members."""
    source = (
        "class Audit\n"
        "  include Comparable\n"
        "  private\n"
        "  validates :name\n"
        "end\n"
    )
    found = _by_name(source, "calls.rb", "ruby")
    for name in ("Comparable", "include", "private", "validates", "name"):
        for hit in found.get(name, []):
            assert hit.kind not in ("field", "property", "constant"), (name, hit.kind)


def test_a_ruby_file_scope_constant_is_still_absent_and_that_is_a_limit():
    """⚠⚠ Pinned as a LIMIT, because the obvious version of this test is
    vacuous and shipped that way until review.

    It was written as "a file-scope `LIMIT = 3` keeps its bare name", looping
    over `.get("LIMIT", [])` and asserting `parent is None` -- and a file-scope
    constant emits NO symbol at all in Ruby, so the loop body never ran. It
    passed against `origin/main` and would pass against an empty
    implementation: a guard covered only by positive assertions about something
    that is not there.

    RUBY_SPEC's `constant_patterns` is empty, so Ruby has no file-scope
    constant channel and this change deliberately does not add one -- the
    issues report CLASS members. Asserting the absence is what makes the next
    change move this line rather than discover it.
    """
    assert "LIMIT" not in _by_name("LIMIT = 3\n", "top.rb", "ruby")


@pytest.mark.parametrize("label,member,source", [
    ("constant", "LOCALC",
     "class Audit\n  def go\n    LOCALC = 3\n  end\nend\n"),
    ("class variable", "@@c",
     "class Audit\n  def go\n    @@c = 1\n  end\nend\n"),
    ("attr call", "nope",
     "class Audit\n  def go\n    attr_accessor :nope\n  end\nend\n"),
])
def test_the_ruby_scope_gate_is_what_stops_a_method_body(label, member, source):
    """⚠⚠ The WITNESS for `_ruby_class_body`, and it was missing.

    Three tests in this file claimed to guard that gate and all three stayed
    green when it was deleted, because their fixtures are excluded by a
    DIFFERENT mechanism: a lowercase LHS is not a `constant` node, and `puts`
    is not in `_RUBY_ATTR_CALLS`. They passed for a reason unrelated to the
    rule -- the vacuity the sibling file already paid for and recorded.

    These three are the shapes that reach every other test the channel has and
    are stopped by SCOPE alone. With the gate removed they are published owned
    by the METHOD: `LOCALC` as a constant, `@@c` as a field, `nope` as a
    property of `Audit.go`. Found in review.
    """
    found = _by_name(source, f"scope_{label.replace(' ', '_')}.rb", "ruby")
    for hit in found.get(member, []):
        assert hit.parent is None, (member, hit.parent)
        assert hit.kind not in ("field", "property", "constant"), hit.kind


def test_a_ruby_call_with_an_explicit_receiver_declares_nothing():
    """⚠⚠ Fabrication, found in review and shipped in the first draft.

    `attr_accessor` is always an implicit-self call. Reading only the `method`
    field made `foo.attr_accessor :sneaky` in a class body publish
    `Audit.sneaky` as an owned property that appears NOWHERE in the source --
    an invented member, where `origin/main` emitted only the class. The guard's
    own comment said the receiver was the discriminator while the code never
    asked for one.

    ⚠ This family fails toward absence. A missing member is a gap; a member
    that does not exist is a lie told to every consumer downstream.
    """
    source = "class Audit\n  foo.attr_accessor :sneaky\nend\n"
    assert "sneaky" not in _by_name(source, "recv.rb", "ruby")


@pytest.mark.parametrize("label,source", [
    ("singleton class",
     "class Audit\n  class << self\n    attr_accessor :view\n  end\nend\n"),
    ("splat argument",
     "class Audit\n  attr_accessor *NAMES\nend\n"),
])
def test_two_more_ruby_forms_are_absent_and_both_are_limits(label, source):
    """⚠ Written down rather than left to be discovered. `class << self` gives
    a `singleton_class` node, which is not a `class` or `module` and so is not
    a member scope here; a splat names no symbol this parser can resolve. Both
    fail toward ABSENCE, which is the right direction, and both are shapes a
    later change should move deliberately."""
    found = _by_name(source, f"{label.replace(' ', '_')}.rb", "ruby")
    for hit in found.get("view", []) + found.get("NAMES", []):
        assert hit.kind not in ("field", "property", "constant"), hit.kind


def test_a_ruby_method_is_unchanged():
    """Non-regression: the one cell Ruby already answered correctly."""
    found = _by_name(_RUBY, "a.rb", "ruby")
    assert found["run_it"][0].kind == "method"
    assert found["run_it"][0].parent == found["Audit"][0].id
