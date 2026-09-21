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


def test_a_ruby_top_level_constant_keeps_its_bare_name():
    """A file-scope constant belongs to no class; qualifying it would invent an
    owner, which is the rule the constant channel already states."""
    source = "LIMIT = 3\n"
    for hit in _by_name(source, "top.rb", "ruby").get("LIMIT", []):
        assert hit.parent is None, hit.parent
        assert hit.qualified_name == "LIMIT"


def test_a_ruby_method_is_unchanged():
    """Non-regression: the one cell Ruby already answered correctly."""
    found = _by_name(_RUBY, "a.rb", "ruby")
    assert found["run_it"][0].kind == "method"
    assert found["run_it"][0].parent == found["Audit"][0].id
