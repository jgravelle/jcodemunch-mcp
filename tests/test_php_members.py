"""PHP class members: properties (#743) and class constants (#744).

Two reports, one language, and two DIFFERENT causes — which is why both are
asserted through the product rather than through the maps:

- **#743** `PHP_SPEC` declares `property_declaration`, maps it to `property`
  and gives it `name_fields["property_declaration"] = "name"`. The grammar sets
  no `name` field on that node: its named children are `visibility_modifier`
  and `property_element`, and the name is two levels down at
  `property_element > variable_name > name`. So the form is advertised, the
  map is satisfied, and no PHP class property has ever been indexed.
- **#744** the node type is RIGHT and the SCOPE GATE refuses it. A class
  constant is `const_declaration`, which `PHP_SPEC.constant_patterns` already
  names, but `_walk_tree` gates the constant channel on `parent_symbol is None`
  unless the language is in `_CLASS_SCOPED_CONSTANT_LANGUAGES` — `{"java",
  "kotlin"}` before this change. #428 opened that hole for Java and #732 closed
  it for Kotlin; PHP is the third language with the shape.

⚠⚠ **A PHP class indexed with its methods and none of its state** is #735's
symptom in a second language, and the kind here is `property` — the kind
`PHP_SPEC` has declared since before #571 and nothing ever emitted, which #732
recorded as "declared-and-dead" when Kotlin became its first live emitter.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _php_enabled(monkeypatch):
    """⚠ Patch `jcodemunch_mcp.config`, never the parser module.

    `parse_file` consults the gate through a function-local import, so a patch
    on the parser's namespace resolves nothing (the `cli/policy.py` trap). This
    box's config disables several languages, and a disabled language yields
    `[]` — indistinguishable from the defect under test.
    """
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _pairs(source: str) -> set[tuple[str, str]]:
    return {(s.name, s.kind) for s in parse_file(source, "a.php", "php")}


def _by_name(source: str, name: str) -> list[tuple[str, str, str]]:
    return [
        (s.name, s.kind, s.qualified_name)
        for s in parse_file(source, "a.php", "php")
        if s.name == name
    ]


# ---------------------------------------------------------------------------
# #743 — properties, in every visibility and modifier the grammar spells
# ---------------------------------------------------------------------------

def test_the_reported_case():
    """#743 verbatim: a class with four members, two of them indexed."""
    source = (
        "<?php\n"
        "class C {\n"
        "  public $prop = 1;\n"
        "  private $hidden;\n"
        "  public static $shared = 2;\n"
        "  public function m() {}\n"
        "}\n"
    )
    pairs = _pairs(source)
    assert ("prop", "property") in pairs
    assert ("hidden", "property") in pairs
    assert ("shared", "property") in pairs
    assert ("m", "method") in pairs


@pytest.mark.parametrize(
    "declaration,name",
    [
        ("public $pub = 1;", "pub"),
        ("private $priv;", "priv"),
        ("protected $prot;", "prot"),
        ("var $legacy;", "legacy"),
        ("public static $stat = 1;", "stat"),
        ("protected ?string $typed = null;", "typed"),
        ("public readonly int $ro;", "ro"),
        ("public int|string $union = 1;", "union"),
        ("public array $arr = [];", "arr"),
    ],
)
def test_a_property_is_a_symbol_whatever_its_modifiers(declaration, name):
    """⚠ The modifiers sit between the visibility and the name, and the name is
    always under `property_element`. A fix keyed on child POSITION rather than
    on the field would pass the first row and fail the typed ones.
    """
    source = f"<?php\nclass C {{\n  {declaration}\n}}\n"
    assert (name, "property") in _pairs(source)


def test_the_dollar_is_not_part_of_the_name():
    """`$this->prop` has no dollar, and that is what a reader searches for.

    The grammar's `variable_name` node text is `$prop`; the name lives one
    level further in. Taking the outer node would make every PHP property
    unfindable by the name it is used by.
    """
    source = "<?php\nclass C {\n  public $prop = 1;\n}\n"
    assert _by_name(source, "prop")
    assert not _by_name(source, "$prop")


def test_every_name_one_declaration_binds_is_a_symbol():
    """⚠⚠ `public $a = 1, $b = 2;` is ONE node and TWO declarations.

    This is why the fix routes through `field_patterns` rather than
    `symbol_node_types`: `_extract_symbol` returns one `Optional[Symbol]` per
    node, so the second name is structurally unreachable through that channel
    (#735's reason, and #741's again in JS).
    """
    source = "<?php\nclass C {\n  public $a = 1, $b = 2;\n}\n"
    pairs = _pairs(source)
    assert ("a", "property") in pairs
    assert ("b", "property") in pairs


def test_a_property_id_agrees_with_the_kind_it_carries():
    """The id embeds the kind, and the call site used to hardcode one.

    ⚠⚠ `_walk_tree` re-mints the id when it qualifies a member, and it wrote
    the literal `"field"` -- correct while Java was the channel's only member,
    and wrong the moment PHP emitted a `property`. It would mint
    `a.php::C.prop#field` for a symbol whose `kind` says `property`: an id no
    lookup keyed on the kind can resolve, and a mismatch nothing else in this
    file could see.

    ⚠ Found by the mutation pass, NOT by review: reverting the call site to the
    literal left every other assertion here green, because they all read
    `name`, `kind` and `qualified_name` and none read the id.
    """
    source = "<?php\nclass C {\n  public $prop = 1;\n}\n"
    rows = {s.id for s in parse_file(source, "a.php", "php") if s.name == "prop"}
    assert rows == {"a.php::C.prop#property"}, rows


def test_a_property_is_owned_by_its_class():
    """A member with no owner is #698's complaint in another language."""
    source = "<?php\nclass C {\n  public $prop = 1;\n}\n"
    assert _by_name(source, "prop") == [("prop", "property", "C.prop")]


@pytest.mark.parametrize("container,keyword", [("class C", "class"), ("trait T", "trait")])
def test_a_property_of_a_trait_is_a_symbol_too(container, keyword):
    """A trait body is `declaration_list`, the same node type a class body uses.

    Asserted rather than assumed: the containers differ and the member node
    does not, so a fix keyed on the CONTAINER would reach one of them.
    """
    source = f"<?php\n{container} {{\n  public $tp = 1;\n}}\n"
    assert ("tp", "property") in _pairs(source)


def test_a_local_variable_is_not_a_property():
    """⚠⚠ The scope direction, asserted as firmly as the extraction.

    PHP spells a local assignment `expression_statement > assignment_expression`
    rather than `property_declaration`, so the #732 Kotlin problem cannot arise
    here — but that is a fact about the grammar, and #735 asserted the same
    thing for Java rather than leaving the next reader to trust it.
    """
    source = "<?php\nfunction f() {\n  $local = 1;\n  return $local;\n}\n"
    pairs = _pairs(source)
    assert ("local", "property") not in pairs
    assert ("local", "field") not in pairs


# ---------------------------------------------------------------------------
# #744 — class constants, where the node type was right all along
# ---------------------------------------------------------------------------

def test_the_reported_case_for_constants():
    """#744 verbatim: the class constant absent, the file-scope one present."""
    source = "<?php\nclass C {\n  const K = 3;\n}\nconst TOP = 4;\n"
    pairs = _pairs(source)
    assert ("K", "constant") in pairs
    assert ("TOP", "constant") in pairs


@pytest.mark.parametrize(
    "declaration,name",
    [
        ("const K = 3;", "K"),
        ("public const PK = 4;", "PK"),
        ("final public const FK = 5;", "FK"),
        ("const A = 1, B = 2;", "A"),
        ("const A = 1, B = 2;", "B"),
    ],
)
def test_a_class_constant_is_a_symbol_whatever_its_modifiers(declaration, name):
    source = f"<?php\nclass C {{\n  {declaration}\n}}\n"
    assert (name, "constant") in _pairs(source)


@pytest.mark.parametrize(
    "container",
    ["class C", "interface I", "trait T", "enum E"],
)
def test_a_constant_in_every_container_the_grammar_spells(container):
    """⚠ One node type, four positions: class, interface, trait and file scope.

    The gate is per LANGUAGE, not per container, so membership buys all of
    them at once — which is worth asserting, because the issue asked whether an
    interface constant needed its own entry. It does not.
    """
    source = f"<?php\n{container} {{\n  const K = 1;\n}}\n"
    assert ("K", "constant") in _pairs(source)


def test_an_enum_body_is_a_container_and_that_took_a_second_fix():
    """⚠⚠ The scope gate has TWO halves and membership buys only one.

    `_CLASS_SCOPED_CONSTANT_LANGUAGES` unlocks `parent_is_container`, and
    `parent_is_container` is computed from the spec's `container_node_types` --
    which named class, trait and interface, and not `enum_declaration`. So a
    class constant started extracting and an ENUM constant still did not, for a
    reason one list over from the one #744 names. Found by reading the output
    of the fix rather than the issue.

    ⚠⚠ **The method row below is a PIN, not proof of this change.** An enum
    method was ALREADY owned before it: PHP spells one `method_declaration`,
    which `symbol_node_types` maps straight to `method`, and
    `parent_is_container` only promotes a `function`. The first version of this
    docstring claimed the container change gave enum methods their owner, and
    review measured the pre-change tree and found `E.m` there already. The row
    stays, because naming an enum a container is exactly the kind of change
    that could move member ownership and nothing else here would notice.
    """
    source = (
        "<?php\n"
        "enum E {\n"
        "  const EK = 2;\n"
        "  public function m() {}\n"
        "}\n"
    )
    rows = {(s.name, s.kind, s.qualified_name) for s in parse_file(source, "a.php", "php")}
    # ⚠ The qualified name was bare `EK` until #780/#783 gave every class-scoped
    # constant its owner. What this test is about is that `EK` is extracted AT
    # ALL, which is what naming the enum body a container bought.
    assert ("EK", "constant", "E.EK") in rows
    assert ("m", "method", "E.m") in rows


def test_a_class_constant_is_owned_by_its_class():
    """⚠⚠ This REVERSES `test_a_class_constant_keeps_the_bare_name_java_and_
    kotlin_give_it`, retired in `harness/retired.json` (#780, #783).

    That test recorded the asymmetry rather than fixing it: `_constant_symbol`
    hardcodes `qualified_name = name` and `_walk_tree` qualified at the call
    site under `if language == "rust"`, so PHP, Java and Kotlin were all bare
    and qualifying PHP alone would have made the answer depend on which
    language you asked. The condition is `parent_symbol is not None` now, so all
    three are qualified together and the asymmetry it was recording is gone.

    ⚠ The half that has NOT changed is which constants are extracted; see
    `test_a_function_local_constant_is_still_not_a_constant` below.
    """
    source = "<?php\nclass C {\n  const K = 3;\n}\n"
    assert _by_name(source, "K") == [("K", "constant", "C.K")]


def test_a_function_local_constant_is_still_not_a_constant():
    """⚠⚠ The gate is widened to a CONTAINER parent, never to a function body.

    `_CLASS_SCOPED_CONSTANT_LANGUAGES` buys `parent_is_container`, and this is
    the half that says what was NOT bought. A `define()` call inside a function
    is not a declaration the constant channel should reach.
    """
    source = "<?php\nfunction f() {\n  define('INNER', 1);\n}\n"
    assert ("INNER", "constant") not in _pairs(source)


def test_the_class_scoped_set_is_still_a_named_set():
    """⚠⚠ Membership is per language, and the set stays SMALL on purpose.

    Relaxing the gate to `parent_is_container` for every language would start
    emitting constants from Python class bodies and JS class fields, moving
    symbol counts in every index and every published dead-code grade. The set
    is the blast-radius control (#428's note, kept by #732), so its size is
    asserted rather than left to a comment.

    ⚠ gdscript joined in #777, and it is the one entry that widened nothing:
    `const_statement` was already the language's declared constant pattern and
    a file-scope `const` already indexed, so the name here buys the class-body
    SCOPE and no new node type. Its sample is in
    `test_constant_extraction_guard.py::test_gdscript_class_scoped_constants_are_extracted`,
    which is what `test_every_class_scoped_language_has_a_sample` enforces.
    """
    from jcodemunch_mcp.parser.extractor import _CLASS_SCOPED_CONSTANT_LANGUAGES

    assert _CLASS_SCOPED_CONSTANT_LANGUAGES == {"java", "kotlin", "php", "gdscript"}


# ---------------------------------------------------------------------------
# The two channels, and what they each own
# ---------------------------------------------------------------------------

def test_no_member_is_emitted_twice():
    """⚠⚠ Keyed on (name, kind, line), because two same-named members of
    different classes are not one member emitted by two channels.

    `property_declaration` and `const_declaration` are different node types
    here, so the #735 double-emit trap does not apply — but a property reaching
    BOTH the field channel and `symbol_node_types` would, and that is exactly
    what this change removes from the spec.
    """
    source = (
        "<?php\n"
        "class C {\n"
        "  public $prop = 1;\n"
        "  const K = 1;\n"
        "}\n"
    )
    rows = [
        (s.name, s.kind, s.line)
        for s in parse_file(source, "a.php", "php")
    ]
    assert len(rows) == len(set(rows)), rows


def test_an_enum_case_is_a_known_separate_gap():
    """⚠ `case A;` is `enum_case`, a node type no PHP spec map names.

    It HAS a `name` field, so it is one entry away — but it is a different node
    type with a different verdict (one issue, one verdict), filed rather than
    folded in here. This test FAILS when that gap closes, naming the line to
    delete.
    """
    source = "<?php\nenum E {\n  case A;\n}\n"
    assert not _by_name(source, "A")
