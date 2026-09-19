"""A file summary counts every kind of class state, and names each one (#760).

`_heuristic_summary` counted a class's members with ONE hardcoded kind:

    field_count = sum(1 for s in symbols if s.kind == "field" and ...)

so a class whose members carry any other kind summarised as having none. Java
and PHP reach the index through the same channel and differ only in the word
each language uses for the thing:

    Java class, 2 methods + 5 fields      -> "Defines A class (2 methods, 5 fields)"
    PHP class,  2 methods + 5 properties  -> "Defines C class (2 methods)"

⚠⚠ **The file summary is what a reader sees BEFORE opening a file**, so a PHP
class read as having no state at all -- the symptom #743 and #735 were about,
surviving one layer up from the fix that closed them.

⚠⚠ **`KIND_ORDER` has four state kinds now** (`constant`, `field`, `property`,
`variable`) and a consumer keyed on one string sees one of them. The rule
"which kinds are declared state" is a property of the KIND VOCABULARY, so it
lives beside `KIND_ORDER` as `STATE_KINDS` and is imported -- a second copy in
the summariser is how this returns for the fifth kind.

⚠ **Not every absence here is this issue.** A C++ class summarises with no
members because its data members yield NO SYMBOL at all (#755); the summary is
faithful to the index and only #755 can change it. `test_cpp_is_not_this_issue`
pins that so the two are not confused.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.symbols import KIND_ORDER, VALID_KINDS
from jcodemunch_mcp.summarizer.file_summarize import _heuristic_summary


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _summary(source: str, filename: str, language: str) -> str:
    return _heuristic_summary(filename, parse_file(source, filename, language))


JAVA = (
    "public class A {\n"
    "    private int f1;\n"
    "    private int f2;\n"
    "    private String f3;\n"
    "    private String f4;\n"
    "    private String f5;\n"
    "    public void m1() {}\n"
    "    public void m2() {}\n"
    "}\n"
)

PHP = (
    "<?php\n"
    "class C {\n"
    "    private int $p1;\n"
    "    private int $p2;\n"
    "    private string $p3;\n"
    "    private string $p4;\n"
    "    private string $p5;\n"
    "    public function m1() {}\n"
    "    public function m2() {}\n"
    "}\n"
)


# ---------------------------------------------------------------------------
# The reported asymmetry
# ---------------------------------------------------------------------------

def test_php_properties_are_counted_and_named():
    """#760's reported case. Before: `Defines C class (2 methods)`."""
    assert _summary(PHP, "C.php", "php") == "Defines C class (2 methods, 5 properties)"


def test_java_fields_are_unchanged():
    """The side that already worked, pinned so the fix cannot move it."""
    assert _summary(JAVA, "A.java", "java") == "Defines A class (2 methods, 5 fields)"


def test_the_two_languages_now_agree_in_shape():
    """⚠ The whole issue in one assertion: both reach the index through
    `field_patterns` and differ only in each language's own word.
    """
    java = _summary(JAVA, "A.java", "java")
    php = _summary(PHP, "C.php", "php")
    assert java.replace("fields", "X") == php.replace("properties", "X").replace("C", "A")


# ---------------------------------------------------------------------------
# The other two state kinds, which a component makes reachable
# ---------------------------------------------------------------------------

def test_a_component_with_only_bindings_is_not_empty():
    """⚠⚠ A Svelte component's script bindings are parented to the component
    class (#752), so all three state kinds land here at once. Before this fix
    it summarised as `Defines C class (0 methods)` -- three bindings, none
    counted, and a "0 methods" that read as an empty file.
    """
    source = (
        "<script>\n"
        "  export let name;\n"
        "  let count = 0;\n"
        "  const MAX = 5;\n"
        "</script>\n"
    )
    assert _summary(source, "C.svelte", "svelte") == (
        "Defines C class (1 constant, 1 property, 1 variable)"
    )


def test_a_module_scope_binding_is_not_a_class_member():
    """⚠⚠ `KIND_ORDER` warns in its own comment that reusing a kind across
    module and class scope "would mix module bindings into every consumer
    asking about a class's members". The PARENT filter is what keeps them
    apart, and widening the kinds is exactly the change that could lose it.
    """
    source = (
        "<?php\n"
        "const TOP = 1;\n"
        "class C {\n"
        "    private int $p1;\n"
        "    public function m1() {}\n"
        "}\n"
    )
    syms = parse_file(source, "C.php", "php")
    # The module constant is REAL and parented to nothing -- without that, this
    # test would pass for the wrong reason.
    assert ("TOP", "constant", None) in [(s.name, s.kind, s.parent) for s in syms]

    # ⚠⚠ Asserted as the WHOLE string. The first version of this row said
    # `"2 constants" not in summary`, which is true whether or not `TOP` is
    # counted -- dropping the parent filter yields "1 constant" and passed it.
    # A planted removal of the filter left all twelve tests green.
    assert _heuristic_summary("C.php", syms) == "Defines C class (1 method, 1 property)"


def test_a_nested_class_does_not_borrow_a_top_level_namesake_s_members():
    """⚠⚠ The member filter matched `parent.endswith(f"::{cls.name}#class")`,
    which cannot tell `Outer.Inner` from a top-level `Inner`.

    Measured before the fix: the nested `Inner` was reported with the TOP-LEVEL
    `Inner`'s member, because `::Outer.Inner#class` does NOT end with
    `::Inner#class` while `::Inner#class` does — so the nested class's own two
    properties were dropped and a stranger's one was counted in their place.

    ⚠ The leak PRE-DATES this change and carried `field` alone; widening the
    kinds would have handed it `property`, `constant` and `variable` as well.
    Matching the class's own `id` closes it outright.

    ⚠ The `Foo` / `MyFoo` shape was always safe — `"::MyFoo#class"` does not end
    with `"::Foo#class"` — so the separator, not the prefix, was the defect.
    """
    source = (
        "class Outer {\n"
        "    class Inner {\n"
        "        val a: Int = 1\n"
        "        val b: Int = 2\n"
        "    }\n"
        "    val o: Int = 3\n"
        "    fun mo() {}\n"
        "}\n"
        "class Inner {\n"
        "    val z: Int = 9\n"
        "}\n"
    )
    summary = _summary(source, "O.kt", "kotlin")
    assert summary == "Defines Outer class (1 method, 1 property). Defines Inner class (2 properties)"


def test_a_class_whose_name_is_a_suffix_of_another_keeps_its_own_members():
    """The prefix shape, pinned in both directions so the id match cannot
    regress to a suffix match without failing.
    """
    source = (
        "<?php\n"
        "class Foo {\n    private int $f1;\n}\n"
        "class MyFoo {\n    private int $m1;\n    private int $m2;\n}\n"
    )
    summary = _summary(source, "F.php", "php")
    assert "Defines Foo class (1 property)" in summary
    assert "Defines MyFoo class (2 properties)" in summary


# ---------------------------------------------------------------------------
# Prose: naming each kind forces correct plurals
# ---------------------------------------------------------------------------

def test_one_member_is_singular():
    """⚠ "1 properties" would be worse than the defect. Naming the kind forces
    a plural rule, and `property` -> `properties` is irregular, so the rule
    cannot be `kind + "s"`.
    """
    source = (
        "<?php\nclass C {\n    private int $p1;\n    public function m1() {}\n}\n"
    )
    assert _summary(source, "C.php", "php") == "Defines C class (1 method, 1 property)"


def test_a_class_with_no_members_has_no_empty_parenthetical():
    source = "<?php\nclass C {\n}\n"
    assert _summary(source, "C.php", "php") == "Defines C class"


# ---------------------------------------------------------------------------
# The file-level fallback counts the same vocabulary
# ---------------------------------------------------------------------------

def test_module_scope_variables_are_not_invisible():
    """The same defect a few lines up: the fallback counted `constant` alone,
    so `let`/`var` bindings (#741, #742) were outside it.
    """
    source = "const A = 1;\nconst B = 2;\nlet c = 3;\nvar d = 4;\n"
    summary = _summary(source, "m.js", "javascript")
    assert "2 constants" in summary
    assert "2 variables" in summary


# ---------------------------------------------------------------------------
# The rule lives once, beside the vocabulary it is about
# ---------------------------------------------------------------------------

def test_state_kinds_is_derived_from_the_kind_vocabulary():
    from jcodemunch_mcp.parser.symbols import STATE_KINDS

    assert set(STATE_KINDS) <= VALID_KINDS
    # Rendered in KIND_ORDER order, so a summary is deterministic and a new
    # kind appears where the vocabulary puts it rather than where a literal did.
    assert list(STATE_KINDS) == [k for k in KIND_ORDER if k in set(STATE_KINDS)]


def test_state_kinds_names_every_kind_that_is_not_behaviour_or_wiring():
    """⚠ Stated as the COMPLEMENT, so adding a kind to `KIND_ORDER` without
    classifying it fails here rather than silently joining neither set.
    """
    from jcodemunch_mcp.parser.symbols import STATE_KINDS

    not_state = {"function", "class", "method", "type", "template", "import"}
    assert set(STATE_KINDS) | not_state == set(KIND_ORDER)
    assert not (set(STATE_KINDS) & not_state)


def test_the_summariser_asks_the_vocabulary_instead_of_naming_a_kind():
    """⚠⚠ The ratchet. A second copy of "which kinds are class state" is how
    this defect returns for the fifth kind, so the summariser must hold no
    state-kind literal of its own.
    """
    import pathlib

    import jcodemunch_mcp.summarizer.file_summarize as mod
    from jcodemunch_mcp.parser.symbols import STATE_KINDS

    source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    # ⚠ Scans for the kind as a STRING LITERAL in any spelling, not just
    # `== "field"`. The first version matched equality alone, and
    # `s.kind in ("field",)` -- the shape a re-introduction is likelier to take,
    # since `_counted` already compares against a loop variable -- walked past
    # it. Comments are stripped first so the module may still EXPLAIN itself.
    code = chr(10).join(ln.split("#", 1)[0] for ln in source.splitlines())
    for kind in STATE_KINDS:
        for spelling in (f'"{kind}"', f"'{kind}'"):
            assert spelling not in code, (
                f"file_summarize names {kind!r} directly; ask STATE_KINDS"
            )
    assert "STATE_KINDS" in source


# ---------------------------------------------------------------------------
# What this issue is NOT
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source,filename,language,summary,wrong_about", [
    (
        "class Sw {\n    var count: Int = 0\n    func bump() { count += 1 }\n}\n",
        "Sw.swift", "swift",
        "Defines Sw class (1 method, 1 constant)",
        "a Swift `var` is mutable and is declared `constant`",
    ),
    (
        "public class Cs {\n"
        "    private int counter = 0;\n"
        "    public string Name { get; set; }\n"
        "    public void M() {}\n"
        "}\n",
        "Cs.cs", "csharp",
        "Defines Cs class (1 method, 2 constants)",
        "a C# field and an auto-property are both declared `constant`",
    ),
])
def test_naming_the_kind_publishes_whatever_the_parser_decided(
    source, filename, language, summary, wrong_about
):
    """⚠⚠ **The cost of naming the kind, stated rather than discovered.**

    Counting only `field` omitted these members silently. Naming the kind
    publishes the parser's word for them — and for Swift and C# that word is
    WRONG, so a silent omission became a visible false statement. Filed as
    #769 (swift `var`) and #770 (csharp fields and auto-properties) rather than
    papered over here: this module reports what the index says, and the index is
    what needs fixing (#741's lesson, "a JS `let` is not a constant", in two
    more languages).

    ⚠ Pinned to the CURRENT WRONG OUTPUT deliberately, the way
    `test_cpp_is_not_this_issue` pins #755. It fails when the parser is fixed,
    which is the notification that this disclosure can go.
    """
    assert _summary(source, filename, language) == summary, wrong_about


def test_cpp_is_not_this_issue():
    """⚠ A C++ class summarises with no members because its data members yield
    no symbol AT ALL (#755, open). The summary is faithful to the index, and
    counting more kinds cannot conjure a symbol the parser never emitted.

    Pinned so a reader comparing languages does not read #755's absence as this
    one's, and so this test fails -- correctly -- when #755 is fixed.
    """
    source = "class K {\npublic:\n    int a;\n    int b;\n    void m1() {}\n};\n"
    syms = parse_file(source, "K.cpp", "cpp")
    assert [s.kind for s in syms if s.kind in ("field", "property")] == []
    assert _summary(source, "K.cpp", "cpp") == "Defines K class (1 method)"
