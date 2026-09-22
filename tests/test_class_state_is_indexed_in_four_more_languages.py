"""An Apex, D, Groovy or Objective-C class's state is indexed (#774, #776, #779, #782).

Four custom parsers walk a class body and emit the METHODS only. Every field,
every property and every constant in those four languages is ABSENT from the
index -- not mis-kinded, absent -- so a reader asking what a class holds is told
it holds nothing. The member-kind audit has carried nine ABSENT cells for them
since it was written.

⚠ **The second mechanism in the same functions #788 touched.** That fix gave
these parsers their owner (`_member_of`) and closed Solidity alone, because
Solidity was the only one of the five already extracting its state. Ownership
went first on purpose: doing this first would mean writing owner-less
`Symbol(...)` constructions and immediately fixing them. Every construction
added here asks the helper.

⚠⚠ **Two of the four kinds are RULINGS and they point opposite ways, so both
are pinned alone.** Apex and Groovy are Java-shaped and have no `const`, so
`static final` IS their constant spelling and `java_field_is_constant` already
requires exactly both -- the opposite of C#'s `readonly` (#770), which is a
`field` precisely BECAUSE C# also has `const`. D's `immutable` is a `constant`
for the same reason in reverse: Solidity's `immutable` is a `field` because
Solidity also has `constant`, and D has no such pair.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.symbols import STATE_KINDS


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """This box disables some languages in its own config; the gate is function-local."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


_APEX = (
    "public class Audit {\n"
    "    static final Integer LIMIT_N = 3;\n"
    "    Integer tally = 0;\n"
    "    public Integer View { get; set; }\n"
    "    Integer runIt() { return 1; }\n"
    "}\n"
)
_DLANG = (
    "class Audit {\n"
    "    immutable int limit = 3;\n"
    "    int tally = 0;\n"
    "    int runIt() { return 1; }\n"
    "}\n"
)
_GROOVY = (
    "class Audit {\n"
    "    static final int LIMIT = 3\n"
    "    int tally = 0\n"
    "    int runIt() { return 1 }\n"
    "}\n"
)
_OBJC = (
    "@interface Audit : NSObject {\n"
    "    int tally;\n"
    "}\n"
    "@property int view;\n"
    "- (int)runIt;\n"
    "@end\n"
)

#: (language, filename, source, {member: kind}). Only STATE kinds; the method
#: is asserted separately, because it must not move.
_EXPECTED: list[tuple[str, str, str, dict[str, str]]] = [
    ("apex", "Audit.cls", _APEX, {
        "LIMIT_N": "constant",   # `static final`, Apex's only constant spelling
        "tally": "field",
        "View": "property",      # a field_declaration carrying an accessor_list
    }),
    ("dlang", "a.d", _DLANG, {
        "limit": "constant",     # `immutable`; see the ruling below
        "tally": "field",
    }),
    ("groovy", "a.groovy", _GROOVY, {
        "LIMIT": "constant",     # `static final`, as in Java
        "tally": "field",
    }),
    ("objc", "a.m", _OBJC, {
        "tally": "field",        # an instance variable
        "view": "property",      # `@property`, which ObjC spells distinctly
    }),
]

_IDS = [row[0] for row in _EXPECTED]


def _state(language: str, filename: str, source: str) -> dict[str, str]:
    return {
        s.name: s.kind
        for s in parse_file(source, filename, language)
        if s.kind in STATE_KINDS
    }


@pytest.mark.parametrize("language,filename,source,expected", _EXPECTED, ids=_IDS)
def test_every_declared_member_is_indexed_with_its_language_s_word(
    language, filename, source, expected
):
    assert _state(language, filename, source) == expected


@pytest.mark.parametrize("language,filename,source,expected", _EXPECTED, ids=_IDS)
def test_every_extracted_member_is_owned_by_its_class(
    language, filename, source, expected
):
    """⚠ A member extracted without its owner is #788 all over again, one
    language later. Every construction added by this change asks `_member_of`,
    and this is what fails if one of them does not."""
    symbols = parse_file(source, filename, language)
    owners = {s.id for s in symbols if s.name == "Audit" and s.kind in ("class", "type")}
    assert owners, f"{language}: the container was not extracted"
    for member in expected:
        hits = [s for s in symbols if s.name == member]
        assert hits, f"{language}: {member!r} was not extracted"
        for hit in hits:
            assert hit.parent in owners, (language, member, hit.parent)
            assert hit.qualified_name == f"Audit.{member}", (
                language, member, hit.qualified_name
            )


@pytest.mark.parametrize("language,filename,source,expected", _EXPECTED, ids=_IDS)
def test_the_method_does_not_move(language, filename, source, expected):
    """The channel next door, unmoved.

    ⚠ A field predicate written too widely would swallow the method
    declaration, and every assertion above would still pass -- they only look
    at state kinds.
    """
    hits = [s for s in parse_file(source, filename, language) if s.name == "runIt"]
    assert len(hits) == 1, (language, [(s.name, s.kind) for s in hits])
    assert hits[0].kind == "method", (language, hits[0].kind)


def test_apex_and_groovy_spell_a_constant_static_final_and_that_is_a_ruling():
    """⚠⚠ Argued, not inherited, because the C# precedent points the other way.

    `java_field_is_constant` requires BOTH `static` and `final`, and Java needs
    that rule because it has no other way to spell a constant. Apex and Groovy
    are the same shape: neither has `const`. C# does, which is why #770 ruled
    `static readonly` a `field` there -- `readonly` is the keyword you choose
    when you specifically do not want a constant, and Apex offers no such
    choice.

    ⚠ Pinned alone so reversing it is one obvious edit rather than a table
    tweak, and with the near-miss beside it: `final` without `static` is an
    instance field, not a constant, in both.
    """
    assert _state("apex", "Audit.cls", _APEX)["LIMIT_N"] == "constant"
    assert _state("groovy", "a.groovy", _GROOVY)["LIMIT"] == "constant"

    apex_final_only = _state(
        "apex", "F.cls",
        "public class F {\n    final Integer once = 1;\n}\n",
    )
    assert apex_final_only == {"once": "field"}, apex_final_only
    groovy_final_only = _state(
        "groovy", "f.groovy",
        "class F {\n    final int once = 1\n}\n",
    )
    assert groovy_final_only == {"once": "field"}, groovy_final_only


def test_d_immutable_is_a_constant_and_that_is_a_ruling():
    """⚠⚠ The opposite ruling to Solidity's `immutable`, and the reason is the
    pair each language offers.

    Solidity spells a real constant `constant`, so its `immutable` is the
    keyword you pick when you do NOT mean one -- #788 ruled it a `field` on
    exactly that ground. D has no such pair: `immutable` is a true immutability
    guarantee and the nearest alternative, a manifest `enum`, is a different
    declaration form rather than a competing modifier. `const` is the same
    guarantee through a different qualifier and gets the same answer.

    ⚠ Solidity is asserted here too, so the two rulings cannot drift into
    agreement without this failing.
    """
    assert _state("dlang", "a.d", _DLANG)["limit"] == "constant"
    assert _state(
        "dlang", "c.d", "class C {\n    const int k = 1;\n}\n"
    ) == {"k": "constant"}
    assert _state(
        "solidity", "s.sol",
        "contract S {\n    uint immutable cap = 1;\n}\n",
    ) == {"cap": "field"}


def _groovy_class_body(body: str) -> dict[str, str]:
    """One statement at CLASS-BODY level, which is where the field branch runs.

    ⚠⚠ A fixture inside a METHOD body cannot exercise this rule at all -- the
    walk never descends there -- so an assertion written that way passes for a
    reason unrelated to what it claims to pin. That is how the first version of
    the boundary test below was vacuous.
    """
    return _state("groovy", "c.groovy", "class C {\n    " + body + "\n}\n")


#: (label, statement, expected state). ⚠⚠ Every operator spelling, because the
#: rule that separates a declaration from a call is about the OPERATOR and the
#: grammar spells operators in three different shapes.
_GROOVY_STATEMENTS: list[tuple[str, str, dict[str, str]]] = [
    ("plain field", "int tally = 0", {"tally": "field"}),
    ("constant", "static final int L = 3", {"L": "constant"}),
    ("def field", "def x = 1", {"x": "field"}),
    ("string field", "String s = 'a'", {"s": "field"}),
    ("expression value", "int x = a + b", {"x": "field"}),
    ("comparison as value", "boolean f = a == b", {"f": "field"}),
    ("two declarators", "int a = 1, b = 2", {"a": "field", "b": "field"}),
    ("two def declarators", "def p = 1, q = 2", {"p": "field", "q": "field"}),
    ("equality call", "check tally == 1", {}),
    ("inequality call", "check tally != 1", {}),
    ("lte call", "check tally <= 1", {}),
    ("gte call", "check tally >= 1", {}),
    ("spaceship call", "check tally <=> 1", {}),
    ("compound assign", "tally += 1", {}),
    ("boolean call", "check a && b", {}),
    ("elvis call", "check a ?: b", {}),
    ("bare reassignment", "tally = 1", {}),
    ("qualified reassignment", "this.tally = 1", {}),
    ("uninitialised", "int p", {}),
    # ⚠⚠ `b` is an assignment to something already declared, so emitting it
    # would FABRICATE a member. Absence is the safe error; invention is not.
    ("chained assignment", "int a = b = 1", {"a": "field"}),
    ("ternary value", "int a = b ? 1 : 2", {"a": "field"}),
    ("elvis value", "int a = b ?: 1", {"a": "field"}),
    ("map literal value", "def m = [a: 1]", {"m": "field"}),
    ("string holding an operator", "def q = 'x == y'", {"q": "field"}),
    ("annotated field", "@Inject int a = 1", {"a": "field"}),
]


@pytest.mark.parametrize(
    "label,statement,expected", _GROOVY_STATEMENTS,
    ids=[r[0].replace(" ", "_") for r in _GROOVY_STATEMENTS],
)
def test_groovy_separates_a_declaration_from_a_call_by_the_operator(
    label, statement, expected
):
    """⚠⚠ The rule, and the four spellings that defeated its first version.

    tree-sitter-groovy has no field node and no assignment node: it emits
    `unit` runs and `operators` tokens. Three measurements shaped this:

    - `==` is TWO ADJACENT `operators` nodes each holding a bare `=`, so a
      scan for "an `operators` child containing `=`" indexed
      `check tally == 1` as a field named `tally`.
    - `!=` is ONE `operators(=)` with the `!` dropped from the tree, making it
      structurally IDENTICAL to a real `=`. No count, adjacency or
      ERROR-sibling test can separate them.
    - `<=` and `>=` put an `ERROR` node where the name would be.

    So the test is the SOURCE TEXT: the contiguous operator run must read
    exactly `=`, and nothing but whitespace may sit between the name and it.
    Everything short of that was a spelling, which is the 09-01 standing
    lesson.

    ⚠ A bare statement at class-body level is not valid Groovy, so the call
    rows are synthetic. They are what the BRANCH sees, which is the point: the
    rule has to hold on the input, not on the input someone would write.
    """
    assert _groovy_class_body(statement) == expected


#: Real Groovy fields this rule MISSES, each measured rather than reasoned.
#: ⚠⚠ Named individually because "uninitialised fields are missed" understates
#: it, and an understated limit is the kind of sentence a reader reuses.
_GROOVY_MISSED: list[tuple[str, str]] = [
    # No operator at all: `int tally` and the call `foo bar` are the same two
    # bare units, so requiring the assignment is what keeps calls out.
    ("uninitialised", "int pending"),
    ("uninitialised with modifier", "private int alsoPending"),
    # A generic type splits on its own comma -- `Map<String` / `,` /
    # `Integer` / ERROR(`> m`) -- so the name never reaches a `unit`.
    ("generic type", "Map<String, Integer> m = [:]"),
    # The "nothing but whitespace between the name and the operator" rule,
    # paying for itself: a comment or a newline there is not whitespace.
    ("comment before the operator", "int tally /*c*/ = 1"),
    ("newline before the operator", "int tally\n        = 1"),
]


@pytest.mark.parametrize(
    "label,statement", _GROOVY_MISSED,
    ids=[r[0].replace(" ", "_") for r in _GROOVY_MISSED],
)
def test_the_groovy_rule_misses_these_real_fields_and_that_is_the_cost(
    label, statement
):
    """⚠⚠ The boundary, pinned per shape because the GRAMMAR draws it here,
    not the rule's author.

    Every row is valid Groovy declaring a real field that this does not index.
    Two of them follow directly from the whitespace clause the rule needs to
    reject `!=`, and one from the grammar splitting a generic on its own comma.

    ⚠ These fail in the SAFE direction -- absence, never fabrication -- which
    is the trade the rule makes deliberately. A later widening must move these
    lines and re-run every row of `_GROOVY_STATEMENTS`, because the calls this
    keeps out are the reason the limit exists.
    """
    assert _groovy_class_body(statement) == {}


def test_an_objc_ivar_and_a_property_are_different_kinds():
    """⚠ Two different grammar nodes and two different words, so a fix that
    routed both through one branch would be wrong in one of them.

    `int tally;` inside the `{ }` block is an instance variable -- a `field`.
    `@property int view;` is what ObjC calls a property and is declared
    separately, which is #743's split: the CHANNEL is not the kind.
    """
    assert _state("objc", "a.m", _OBJC) == {"tally": "field", "view": "property"}


def test_a_module_scope_declaration_does_not_become_a_class_member():
    """⚠⚠ The scope boundary, asserted as ABSENCE rather than as a kind.

    D spells a module-scope `int x = 1;` with the SAME `variable_declaration`
    node it uses inside an aggregate, so an unguarded branch would add a whole
    new symbol class to every D file in every user's index -- a scope change
    nobody asked for, under an issue about class state. The first draft of
    this fix carried a comment saying "only inside an aggregate" with no test
    under it, and shipped exactly that: two new module-scope symbols. Review
    measured them by diffing parse output against the parent branch.

    ⚠ It is asserted as absence, not as `variable`, because whether a D
    module-scope binding should be indexed at all -- and as which kind -- is
    its own decision with #807's shape. This pins that the decision has not
    been made here by accident.

    ⚠ Objective-C is the control: its member branch is guarded by
    `current_class[0]`, so a top-level declaration was never reachable.
    """
    for language, filename, source in (
        ("dlang", "top.d", "int topTally = 0;\nimmutable int topK = 1;\n"),
        ("objc", "top.m", "int topTally;\n"),
    ):
        state = [
            (s.name, s.kind) for s in parse_file(source, filename, language)
            if s.kind in STATE_KINDS
        ]
        assert state == [], (language, state)
