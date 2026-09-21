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


def test_a_groovy_field_without_an_initialiser_is_not_extracted_and_that_is_the_limit():
    """⚠⚠ The boundary of the Groovy rule, pinned because the grammar cannot
    draw it.

    tree-sitter-groovy has no field node. A field is a `command` of bare
    identifier units inside a class block, and `int tally` -- a declaration
    with no initialiser -- is indistinguishable from the method call
    `foo bar`. Requiring an `=` is what keeps a call out of the index, and the
    cost is the uninitialised field.

    ⚠ Asserted rather than left to a comment: a later widening must move this
    line, with a fixture proving calls stay out.
    """
    assert _state("groovy", "u.groovy", "class U {\n    int pending\n}\n") == {}
    called = parse_file(
        "class C {\n    int go() { println tally }\n}\n", "c.groovy", "groovy"
    )
    assert [s.name for s in called if s.kind in STATE_KINDS] == []


def test_an_objc_ivar_and_a_property_are_different_kinds():
    """⚠ Two different grammar nodes and two different words, so a fix that
    routed both through one branch would be wrong in one of them.

    `int tally;` inside the `{ }` block is an instance variable -- a `field`.
    `@property int view;` is what ObjC calls a property and is declared
    separately, which is #743's split: the CHANNEL is not the kind.
    """
    assert _state("objc", "a.m", _OBJC) == {"tally": "field", "view": "property"}


def test_a_module_scope_declaration_is_not_a_class_member():
    """⚠⚠ The half that has no owner, and the shape #699 and #807 both name.

    A binding outside any class belongs to no type. It must not acquire a
    member kind, and it must not acquire a parent.
    """
    for language, filename, source in (
        ("dlang", "top.d", "int topTally = 0;\n"),
        ("objc", "top.m", "int topTally;\n"),
    ):
        hits = [
            s for s in parse_file(source, filename, language)
            if s.name == "topTally"
        ]
        for hit in hits:
            assert hit.parent is None, (language, hit.parent)
            assert hit.kind not in ("field", "property"), (language, hit.kind)
