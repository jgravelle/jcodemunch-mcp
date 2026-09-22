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


@pytest.mark.parametrize("source,filename,language,expected", [
    (
        "public partial class C {\n"
        "    private int a = 0;\n"
        "    public void M1() {}\n"
        "}\n"
        "public partial class C {\n"
        "    private int b = 0;\n"
        "    public void M2() {}\n"
        "}\n",
        "c.cs", "csharp",
        "Defines C class (1 method, 1 field). Defines C class (1 method, 1 field)",
    ),
    (
        "class Sw {\n    var count: Int = 0\n    func f() {}\n}\n"
        "extension Sw {\n    var doubled: Int { count * 2 }\n}\n",
        "s.swift", "swift",
        "Defines Sw class (1 method, 1 property). Defines Sw class (1 property)",
    ),
])
def test_two_classes_of_one_name_report_the_members_they_declare(
    source, filename, language, expected
):
    """⚠⚠ **The regression the FIRST fix for the nested case shipped, and the
    shape no existing plant could express.**

    When one file holds two classes of the same name, the renumbering rewrites
    the CLASS id to `...#class~1`/`~2`. Until #821 it never rewrote its
    children's `parent`, so a bare `s.parent == cls.id` matched NOTHING and
    both classes summarised as empty -- this module's own symptom,
    reintroduced by the remedy for a different one.

    ⚠ A C# `partial class` and a Swift `class` + `extension` are idiomatic, not
    edge cases: the first draft unindexed the members of every one of them.

    ⚠⚠ **The expected strings CHANGED with #821 and the old ones were the
    defect's witness** (Practice 9). This module used to strip the ordinal off
    its own side, so each namesake reported the UNION of both halves'
    members -- and this test pinned that union while its own docstring named
    the producer fix it was waiting for. The producer follows children to
    their twin now, so each declaration reports what IT declares.

    ⚠ For a `partial class` the union was arguably the truer statement about
    the CLASS, and the per-declaration count is the truer statement about the
    FILE: the sentences sum to the real member count instead of reporting
    every member once per namesake. Swift shows the difference plainly -- the
    `extension` carries one property and says so, rather than claiming the
    class's method as well.
    """
    assert _summary(source, filename, language) == expected


def test_a_namespaced_class_counts_its_members():
    """⚠ A second defect the id match closed, found in review rather than aimed
    at: a class whose qualified name carries a NAMESPACE had its members
    uncounted, for the same reason the nested class did.

    `tests/fixtures/cpp/sample.cpp` holds `cpp/sample.cpp::sample.Box#class`,
    which does not end with `::Box#class`, so `origin/main` read
    `Defines Box class (0 methods)` for a class with four. Every namespaced
    C++, C# or Elixir class was affected. Uses the checked-in fixture rather
    than an inline source, so the case is the one the repo already carries.
    """
    import pathlib

    fixture = pathlib.Path(__file__).parent / "fixtures" / "cpp" / "sample.cpp"
    syms = parse_file(fixture.read_text(encoding="utf-8"), "cpp/sample.cpp", "cpp")
    box = [s for s in syms if s.kind == "class"]
    assert [s.id for s in box] == ["cpp/sample.cpp::sample.Box#class"]
    # `, 1 field` since #755: `T value_;` is a member the parser never emitted
    # before, so the closing paren used to follow the methods directly.
    assert _heuristic_summary("cpp/sample.cpp", syms).startswith(
        "Defines Box class (4 methods, 1 field)"
    )


def test_a_class_whose_name_is_a_suffix_of_another_keeps_its_own_members():
    """The prefix shape, pinned as an OUTPUT.

    ⚠ It does not discriminate between the two matchers and the docstring
    used to claim it did: `"F.php::MyFoo#class".endswith("::Foo#class")` is
    False, so a suffix match answers this case correctly too. The separator was
    the defect, not the prefix -- `test_a_nested_class_...` is the row that
    fails against a suffix match, and it is the only one.
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
    import ast
    import pathlib

    import jcodemunch_mcp.summarizer.file_summarize as mod
    from jcodemunch_mcp.parser.symbols import STATE_KINDS

    source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")

    # ⚠⚠ Parsed, not scanned. Two earlier versions were blind:
    #   1. matching `== "field"` only -- `s.kind in ("field",)` evaded it, which
    #      is the likelier spelling since `_counted` already compares against a
    #      loop variable;
    #   2. stripping comments by cutting each line at the first `#` -- which
    #      truncates at a `#` inside a STRING too, so any line carrying a `#`
    #      before the literal hid it. A reviewer demonstrated it with a
    #      CONSTRUCTED line joining the old suffix match to a `"field"` test;
    #      that exact line is not in the history, and on `origin/main`'s real
    #      source the `#class` sat on its own `suffix = ...` line while the
    #      `field_count` line carried no `#` at all, so the cutting guard would
    #      have caught it there. The evasion is real and the reachable spelling
    #      is one line away -- `plants.md` runs it -- but it is a probe, not a
    #      quotation.
    # Walking the AST finds a string literal wherever it sits and cannot be
    # fooled by punctuation, while leaving comments and docstrings free to
    # EXPLAIN the kinds.
    literals = {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    named = sorted(set(STATE_KINDS) & literals)
    assert not named, f"file_summarize names {named} directly; ask STATE_KINDS"
    assert "STATE_KINDS" in source


# ---------------------------------------------------------------------------
# What this issue is NOT
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source,filename,language,summary,wrong_about", [
    (
        "class Sw {\n    var count: Int = 0\n    func bump() { count += 1 }\n}\n",
        "Sw.swift", "swift",
        "Defines Sw class (1 method, 1 property)",
        "a Swift `var` used to be declared `constant` (#769)",
    ),
    (
        "public class Cs {\n"
        "    private int counter = 0;\n"
        "    public string Name { get; set; }\n"
        "    public void M() {}\n"
        "}\n",
        "Cs.cs", "csharp",
        "Defines Cs class (1 method, 1 field, 1 property)",
        "a C# field and an auto-property both used to be `constant` (#770)",
    ),
])
def test_naming_the_kind_publishes_whatever_the_parser_decided(
    source, filename, language, summary, wrong_about
):
    """⚠⚠ **The cost of naming the kind, and it has now been paid back.**

    Counting only `field` omitted these members silently. Naming the kind
    publishes the parser's word for them — and for Swift and C# that word WAS
    wrong, so a silent omission became a visible false statement. It was filed
    as #769 and #770 rather than papered over here, because this module reports
    what the index says and the index is what needed fixing (#741's lesson, "a
    JS `let` is not a constant", in two more languages).

    ⚠⚠ This test was pinned to the WRONG output and said it would fail when the
    parser was fixed. It did, on #769/#770/#787/#788. **Inverted rather than
    retired**, the way `test_cpp_is_not_this_issue` below was when #755 closed:
    same sources, the assertion flipped, and the name still true — the summary
    publishes whatever the parser decided, and what it decides is now right.
    #760's disclosure about publishing a wrong kind goes with it.
    """
    assert _summary(source, filename, language) == summary, wrong_about


def test_cpp_is_not_this_issue():
    """A C++ class used to summarise with no state because its data members
    yielded no symbol AT ALL (#755). This pinned `(1 method)` while #755 was
    open and said it would fail, correctly, when #755 was fixed. It did.

    The summary was always faithful to the index; the index was missing the
    members. Same source, the assertion inverted, and the name still true:
    C++ was never #760's defect.
    """
    source = "class K {\npublic:\n    int a;\n    int b;\n    void m1() {}\n};\n"
    syms = parse_file(source, "K.cpp", "cpp")
    assert [s.kind for s in syms if s.kind in ("field", "property")] == ["field", "field"]
    assert _summary(source, "K.cpp", "cpp") == "Defines K class (1 method, 2 fields)"
