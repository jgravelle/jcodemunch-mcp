"""A class member you can reassign is not a constant (#769, #770, #787, #788).

Four languages route their class-state declaration to the constant channel with
no check of the declaration's own keyword, so every member it binds claims to be
immutable. `constant` is the one kind a `var` cannot be. #741 settled this for
JS/TS ("a JS `let` is not a constant") and #732 refused the same shortcut for
Kotlin; this is the same question in C#, Swift, Scala and Solidity.

⚠⚠ **The kind is the language's own word, not one word for all four.** #743 and
#735 already split this way on purpose -- Java calls them fields and PHP calls
them properties, and the CHANNEL is not the kind. So C# gets `field` AND
`property` because C# spells the two differently; Swift gets `property`, which
is what Kotlin's `var` carries (#769 names that as the likely answer); Scala and
Solidity get `field`.

⚠ **What is NOT here: ownership.** C#, Swift and Scala members are already owned
by their class. Solidity's carry `parent=None` because it has a custom parser,
so #788's owner half belongs with #774/#776/#779/#782/#778, and #788 closes
there rather than here.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.symbols import STATE_KINDS


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """This box disables some languages in its own config; the gate is function-local."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


_CSHARP = (
    "class Audit {\n"
    "    const int Limit = 3;\n"
    "    int tally = 0;\n"
    "    static readonly int Ro = 1;\n"
    "    public int View { get; set; }\n"
    "    public int Computed { get { return 2; } }\n"
    "    int RunIt() { return 1; }\n"
    "}\n"
)
_SWIFT = (
    "class Audit {\n"
    "    let limit = 3\n"
    "    var tally = 0\n"
    "    var view: Int { return 2 }\n"
    "    func runIt() -> Int { return 1 }\n"
    "}\n"
)
_SCALA = (
    "class Audit {\n"
    "  val limit = 3\n"
    "  var tally = 0\n"
    "  def runIt(): Int = 1\n"
    "}\n"
)
_SOLIDITY = (
    "contract Audit {\n"
    "    uint constant LIMIT = 3;\n"
    "    uint tally = 0;\n"
    "    function runIt() public returns (uint) { return 1; }\n"
    "}\n"
)

#: (language, filename, source, {member: kind}). The kind is what the LANGUAGE
#: calls the member, which is why one table holds three different words.
_EXPECTED: list[tuple[str, str, str, dict[str, str]]] = [
    ("csharp", "Audit.cs", _CSHARP, {
        "Limit": "constant",     # `const` -- a real compile-time constant
        "tally": "field",        # an ordinary field
        "Ro": "field",           # `static readonly`; see the ruling below
        "View": "property",      # an auto-property
        "Computed": "property",  # get-only is still not a constant (#770)
    }),
    ("swift", "Audit.swift", _SWIFT, {
        "limit": "constant",
        "tally": "property",
        "view": "property",      # computed
    }),
    ("scala", "a.scala", _SCALA, {
        "limit": "constant",
        "tally": "field",
    }),
    ("solidity", "a.sol", _SOLIDITY, {
        "LIMIT": "constant",
        "tally": "field",
    }),
]

_IDS = [row[0] for row in _EXPECTED]


def _kinds(language: str, filename: str, source: str) -> dict[str, str]:
    return {
        s.name: s.kind
        for s in parse_file(source, filename, language)
        if s.kind in STATE_KINDS
    }


@pytest.mark.parametrize("language,filename,source,expected", _EXPECTED, ids=_IDS)
def test_each_member_carries_the_kind_its_language_gives_it(
    language, filename, source, expected
):
    assert _kinds(language, filename, source) == expected


@pytest.mark.parametrize("language,filename,source,expected", _EXPECTED, ids=_IDS)
def test_no_reassignable_member_is_a_constant(language, filename, source, expected):
    """The property the four issues share, asserted without naming a target kind.

    ⚠ Separate from the table above on purpose. The table encodes a CHOICE of
    vocabulary that review may move; this encodes the defect, and it must keep
    failing if someone changes `field` to `property` and calls the issue closed.
    """
    observed = _kinds(language, filename, source)
    reassignable = {m for m, k in expected.items() if k != "constant"}
    wrong = sorted(m for m in reassignable if observed.get(m) == "constant")
    assert wrong == [], f"{language}: still constant: {wrong}"


def test_a_real_constant_is_still_a_constant():
    """Non-vacuity, and the half a careless fix drops.

    ⚠ A predicate that answered False everywhere would pass the test above and
    turn every `const`, `let` and `val` into a field. All four languages declare
    one, and all four must survive.
    """
    for language, filename, source, expected in _EXPECTED:
        observed = _kinds(language, filename, source)
        constants = sorted(m for m, k in expected.items() if k == "constant")
        assert constants, f"{language}: the sample declares no constant to guard"
        for member in constants:
            assert observed.get(member) == "constant", (language, member, observed)


def test_csharp_static_readonly_is_a_field_and_that_is_a_ruling():
    """⚠⚠ Argued, not inherited, because the Java precedent points the other way.

    `java_field_is_constant` requires BOTH `static` and `final`, and Java needs
    that rule because it has no other way to spell a constant. C# does: `const`
    IS the compile-time constant, so `readonly` is the keyword you choose when
    you specifically do not want one. Mirroring Java here would erase a
    distinction C# draws itself.

    ⚠ The audit's `_RULE` accepts ANY state kind for an immutable member, so the
    harness does not force this and it is a decision the fix makes. It is pinned
    alone so that reversing it is one obvious edit rather than a table tweak.
    """
    assert _kinds("csharp", "Audit.cs", _CSHARP)["Ro"] == "field"


#: (language, filename, source, {name: kind}) at MODULE scope, where nothing
#: owns the binding. The mutable one is `variable`, never a member word.
_MODULE_SCOPE: list[tuple[str, str, str, dict[str, str]]] = [
    ("swift", "top.swift", "let topLet = 1\nvar topVar = 2\n",
     {"topLet": "constant", "topVar": "variable"}),
    ("scala", "top.scala", "val topVal = 1\nvar topTally = 0\n",
     {"topVal": "constant", "topTally": "variable"}),
    ("csharp", "Top.cs", "class Holder {\n    int inner = 0;\n}\n",
     {"inner": "field"}),
]


@pytest.mark.parametrize(
    "language,filename,source,expected", _MODULE_SCOPE,
    ids=[r[0] for r in _MODULE_SCOPE],
)
def test_a_binding_with_no_type_to_belong_to_is_a_variable(
    language, filename, source, expected
):
    """⚠⚠ The half the first draft of this fix dropped, and the fixture above
    could not have caught it.

    #769 says it in one sentence: "`variable` is the module-scope word and a
    class member belongs to a type." Giving Swift's `property_declaration` the
    kind `property` is right inside a class and wrong at file scope, where a
    top-level `var` came out `property` with `parent=None`. `KIND_ORDER`'s own
    entry for `variable` gives the reason: reusing a member kind for a module
    binding mixes it into every consumer asking about a class's members.

    ⚠ The C# row is the control. Its members only occur inside a type, so it
    must NOT move -- a demotion rule written too widely would take it.
    """
    assert _kinds(language, filename, source) == expected


#: (language, filename, source, {name: kind}) inside a FUNCTION body.
_FUNCTION_LOCAL: list[tuple[str, str, str, dict[str, str]]] = [
    ("swift", "f.swift", "func f() {\n    var localV = 3\n    let localL = 4\n}\n",
     {"localV": "variable", "localL": "constant"}),
    ("scala", "g.scala", "class A {\n  def g(): Int = { var localV = 3; 1 }\n}\n",
     {"localV": "variable"}),
]


@pytest.mark.parametrize(
    "language,filename,source,expected", _FUNCTION_LOCAL,
    ids=[r[0] for r in _FUNCTION_LOCAL],
)
def test_a_mutable_local_is_a_variable_not_a_member(
    language, filename, source, expected
):
    """⚠⚠ The demotion's condition is NO TYPE TO OWN IT, not module scope.

    `parent_is_container` is false for a FUNCTION parent too, so a mutable local
    takes `variable` with its function as parent. That is the right answer -- a
    local is a member of nothing -- but the first version of the rule's comment
    claimed module scope while the branch fired here as well, and review found
    it. Asserted rather than left to the comment: this is the same unpinned-
    scope shape that made the module-scope half invisible in round one.
    """
    observed = _kinds(language, filename, source)
    assert {k: observed.get(k) for k in expected} == expected


def test_kotlin_is_excluded_from_the_module_scope_demotion_and_that_is_recorded():
    """⚠⚠ The boundary of the rule above, pinned so widening it is deliberate.

    Kotlin has published a top-level `val`/`var` as `property` since #732. That
    contradicts `KIND_ORDER`'s rule, and demoting it here would be wrong a
    SECOND way: `variable` is defined as a module-scope MUTABLE binding, and a
    Kotlin top-level `val` is immutable without being SCREAMING_CASE, so
    `kotlin_property_is_constant` has already declined to call it a constant.
    Neither word is obviously right, the decision is outside these four issues,
    and it moves ids in a released language.

    ⚠ The two languages that ARE in the set are safe by construction: their
    refiner OR SPEC MAP turns every immutable module-scope binding into a
    `constant` first, so whatever still carries a member word is reassignable.
    Swift gets that from `_swift_member_kind`; Scala has no refiner and gets it
    from `SCALA_SPEC.symbol_node_types`.
    """
    from jcodemunch_mcp.parser.extractor import _MODULE_SCOPE_VARIABLE_LANGUAGES

    assert _MODULE_SCOPE_VARIABLE_LANGUAGES == {"swift", "scala"}
    kinds = _kinds("kotlin", "Top.kt", "val topLevel = 1\nvar topVar = 2\n")
    assert kinds == {"topLevel": "property", "topVar": "property"}


def test_the_class_sample_and_the_module_sample_disagree_on_purpose():
    """Non-vacuity for the rule above: the SAME declaration yields a different
    kind by scope, so a rule that ignored scope cannot pass both."""
    in_class = _kinds("swift", "Audit.swift", _SWIFT)["tally"]
    at_module = _kinds("swift", "top.swift", "var topVar = 2\n")["topVar"]
    assert (in_class, at_module) == ("property", "variable")


def test_a_method_stays_a_method_in_all_four():
    """The channel next door, unmoved. A predicate that reclassified too widely
    would take the methods with it, and no assertion above would see it."""
    expected_methods = {
        "csharp": ("Audit.cs", _CSHARP, "RunIt"),
        "swift": ("Audit.swift", _SWIFT, "runIt"),
        "scala": ("a.scala", _SCALA, "runIt"),
    }
    for language, (filename, source, name) in expected_methods.items():
        hits = [
            s for s in parse_file(source, filename, language)
            if s.name == name
        ]
        assert len(hits) == 1, (language, [(s.name, s.kind) for s in hits])
        assert hits[0].kind == "method", (language, hits[0].kind)


def test_solidity_ownership_is_a_separate_issue_and_still_open():
    """⚠ #788 has TWO halves and this PR fixes one. Its members are qualified by
    the contract's name and carry no `parent`, which is PR 3's family
    (#774, #776, #779, #782, #778). Pinned so that closing #788 here would fail,
    and so the owner fix has a witness that flips when it arrives."""
    members = [
        s for s in parse_file(_SOLIDITY, "a.sol", "solidity")
        if s.name in ("LIMIT", "tally")
    ]
    assert len(members) == 2
    assert all(s.parent is None for s in members)
    assert all((s.qualified_name or "").startswith("Audit.") for s in members)
