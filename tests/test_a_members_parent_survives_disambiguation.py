"""A member's `parent` names an id some symbol actually carries (#821).

Two same-named containers in one file are disambiguated to `~1` and `~2`.
Their members are not: each one's `parent` still holds the owner's
PRE-renumbering id, which no emitted symbol has. Measured on six languages
reached by three different member channels -- the class-field channel, the Go
receiver pass and the Ruby `attr_*` channel -- all producing the same dangling
string, which is what locates the shared site rather than a parser.

⚠⚠ **A parent-keyed reader sees the containers with NO members, not with the
wrong ones.** `build_symbol_tree` (`parser/hierarchy.py`) drops a child whose
`parent` does not resolve, so the failure is silent and looks like a type that
declares nothing. That is #771's residue.

⚠⚠ **The identity is the SPAN, which is the lesson this file exists to carry
one more time.** The renumbering cannot ask a string which twin it meant --
both twins had the same string. It can ask which twin's bytes CONTAIN the
member's bytes, and containment is exactly the relationship that made the
member a member. A name cannot answer it and neither can a line.

⚠ What this file does NOT settle, deliberately: whether two same-named
containers should be two symbols at all. The issue raises it -- a reader
asking what fields `Conf` has wants a union, and Ruby's twins are one class
REOPENED rather than two classes -- and states that two symbols is the current
answer and not this issue's to change. Every assertion below is written
against that answer, so changing it later moves these tests honestly instead
of breaking them accidentally.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.hierarchy import build_symbol_tree


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """This box disables some languages in its own config; the gate is function-local."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


#: language -> (filename, source, the member of the FIRST twin, of the SECOND).
#:
#: ⚠ One member each, and they are NAMED differently per twin so an assertion
#: can tell which owner it landed under. A shared member name would make the
#: wrong answer indistinguishable from the right one -- the shape that let this
#: defect live behind a passing suite.
_TWINS: dict[str, tuple[str, str, str, str]] = {
    "python": ("d.py", "class Conf:\n    a = 1\nclass Conf:\n    b = 2\n", "a", "b"),
    "java": ("D.java", "class Conf { int a; }\nclass Conf { int b; }\n", "a", "b"),
    "go": (
        "d.go",
        "package p\ntype Conf struct { a int }\ntype Conf struct { b int }\n",
        "a",
        "b",
    ),
    "rust": ("d.rs", "struct Conf { a: u8 }\nstruct Conf { b: u8 }\n", "a", "b"),
    "ruby": (
        "d.rb",
        "class Conf\n  attr_accessor :a\nend\nclass Conf\n  attr_accessor :b\nend\n",
        "a",
        "b",
    ),
    "dart": ("d.dart", "class Conf { int a = 1; }\nclass Conf { int b = 2; }\n", "a", "b"),
    # ⚠⚠ The three below are NOT the issue's table. They came from the
    # mechanism scan, and `tests/test_a_class_member_carries_its_owner.py`
    # had already NAMED all three as shipping broken since #771 -- "this
    # change does not fix it and does not make it worse". They are the same
    # defect wearing a language's own idiom rather than a mistake: a C#
    # `partial class` and a Swift `extension` are the language's supported
    # way to write one type twice, so unlike the twins above these are VALID,
    # ordinary source rather than a shadowing accident.
    "csharp": ("A.cs", "partial class Conf { int a; }\npartial class Conf { int b; }\n", "a", "b"),
    "swift": (
        "a.swift",
        "class Conf { var a = 1 }\nextension Conf { var b: Int { 2 } }\n",
        "a",
        "b",
    ),
}


def _syms(language: str):
    filename, source, _first, _second = _TWINS[language]
    return parse_file(source, filename, language)


@pytest.mark.parametrize("language", sorted(_TWINS))
def test_every_emitted_parent_resolves_to_an_emitted_symbol(language):
    """The property, and it needs no grammar knowledge to run.

    ⚠⚠ Two sets compared -- every `parent` against every `id` -- so it costs
    nothing over the whole sample set and cannot rot the way a fixture
    asserting specific ids would. This is what the issue asks for, and what
    nothing checked.
    """
    symbols = _syms(language)
    ids = {s.id for s in symbols}
    dangling = sorted({s.parent for s in symbols if s.parent and s.parent not in ids})
    assert not dangling, (
        f"{language}: {dangling} name no emitted symbol. A member is parented to "
        f"an id the renumbering moved out from under it (#821)."
    )


@pytest.mark.parametrize("language", sorted(_TWINS))
def test_each_twins_member_is_owned_by_that_twin(language):
    """Stronger than resolvability: the RIGHT twin, decided by containment.

    ⚠ A fix that pointed every member at the first twin would satisfy the test
    above and be wrong here, which is why both exist.
    """
    _filename, _source, first, second = _TWINS[language]
    symbols = _syms(language)
    containers = [s for s in symbols if s.kind in ("class", "type")]
    assert len(containers) == 2, f"{language}: expected two twins, got {len(containers)}"
    by_name = {s.name: s for s in symbols if s.name in (first, second)}
    assert by_name[first].parent == containers[0].id
    assert by_name[second].parent == containers[1].id


@pytest.mark.parametrize("language", sorted(_TWINS))
def test_both_twins_keep_their_members_in_the_symbol_tree(language):
    """The consumer half, and the reason the failure is silent (#771).

    `build_symbol_tree` drops a child whose `parent` does not resolve, so a
    dangling pointer renders as a container that declares NOTHING rather than
    as a container with somebody else's member.
    """
    _filename, _source, first, second = _TWINS[language]
    tree = build_symbol_tree(_syms(language))
    placed: dict[str, str] = {}

    def _walk(nodes, owner_id):
        # ⚠ `build_symbol_tree` returns `SymbolNode(symbol=..., children=[...])`.
        # Read it off the real type rather than accepting a dict too: a walker
        # that tolerates a shape the producer never emits is a walker that can
        # silently visit nothing, which is how this test first "failed" for the
        # wrong reason.
        for node in nodes:
            placed[node.symbol.name] = owner_id
            _walk(node.children, node.symbol.id)

    _walk(tree, "")
    missing = sorted({first, second} - set(placed))
    assert not missing, f"{language}: {missing} dropped from the tree entirely"
    containers = [s for s in _syms(language) if s.kind in ("class", "type")]
    assert placed[first] == containers[0].id
    assert placed[second] == containers[1].id


def test_three_twins_each_keep_their_own_member():
    """Ordinals past `~2`, because a fix written for a PAIR is a fix for a pair."""
    source = "class Conf:\n    a = 1\nclass Conf:\n    b = 2\nclass Conf:\n    c = 3\n"
    symbols = parse_file(source, "d.py", "python")
    ids = {s.id for s in symbols}
    assert not [s for s in symbols if s.parent and s.parent not in ids]
    containers = [s for s in symbols if s.kind == "class"]
    members = {s.name: s for s in symbols if s.name in ("a", "b", "c")}
    assert [members[n].parent for n in ("a", "b", "c")] == [c.id for c in containers]


def test_a_file_with_no_duplicates_is_untouched():
    """The renumbering does not run, and nothing else may change either.

    ⚠ The regression guard for the fix, not for the defect: a rewrite keyed on
    the wrong condition would re-point parents in every file in the corpus.
    """
    source = "class Alpha:\n    a = 1\nclass Beta:\n    b = 2\n"
    symbols = parse_file(source, "d.py", "python")
    assert not [s for s in symbols if "~" in s.id]
    owners = {s.name: s.id for s in symbols if s.kind == "class"}
    by_name = {s.name: s for s in symbols}
    assert by_name["a"].parent == owners["Alpha"]
    assert by_name["b"].parent == owners["Beta"]


def test_an_objc_interface_and_implementation_each_own_their_own_members():
    """The spelling the tree had already named as shipping broken (#771).

    ⚠⚠ `@interface Audit` and `@implementation Audit` are two symbols with one
    id -- the same `(file, qualified, kind)` -- so this is the defect arriving
    through a language's ordinary structure rather than through duplicated
    code. `tests/test_a_class_member_carries_its_owner.py` carried a helper
    that stripped the ordinal off both sides to tolerate it, and said in its
    own docstring that re-pointing children was "a different fix in a
    different layer". This is that layer; the helper is gone and that file
    asserts exact ids now.

    The declaration's method and the implementation's method are SEPARATE
    symbols with the same name, so each must land on its own half -- which is
    the assertion a stripped comparison could not make.
    """
    source = (
        "@interface Audit : NSObject { int tally; }\n"
        "-(int)runIt;\n"
        "@end\n"
        "@implementation Audit\n"
        "-(int)runIt { return 1; }\n"
        "@end\n"
    )
    symbols = parse_file(source, "a.m", "objc")
    ids = {s.id for s in symbols}
    assert not [s for s in symbols if s.parent and s.parent not in ids]
    halves = [s for s in symbols if s.kind == "class"]
    assert len(halves) == 2
    declared, implemented = halves
    field = next(s for s in symbols if s.name == "tally")
    methods = [s for s in symbols if s.name == "runIt"]
    assert field.parent == declared.id
    assert {m.parent for m in methods} == {declared.id, implemented.id}


def test_a_go_method_on_twin_types_resolves_and_the_owner_is_a_known_limit():
    """Go's receiver pass picks the FIRST twin for every method, and that
    predates this fix.

    ⚠⚠ A method is NOT inside its receiver's span -- Go attaches it to a
    receiver rather than nesting it -- so containment cannot answer for one,
    and the fallback preserves the attribution the stamper made
    (`types_by_name.setdefault`, first twin wins) rather than inventing a
    different one. `B` therefore lands on twin 1, which is the wrong owner.

    ⚠ It is pinned rather than fixed because two same-named types in one Go
    FILE do not compile -- Go's build tags are per-file, so the `#[cfg]` shape
    that makes this valid in Rust has no Go spelling. The requirement this
    fix owes is that the pointer RESOLVES; which twin an impossible program's
    method belongs to is not a question with a right answer.
    """
    source = (
        "package p\n\ntype Conf struct{ a int }\n\nfunc (c Conf) A() {}\n\n"
        "type Conf struct{ b int }\n\nfunc (c Conf) B() {}\n"
    )
    symbols = parse_file(source, "d.go", "go")
    ids = {s.id for s in symbols}
    assert not [s for s in symbols if s.parent and s.parent not in ids]
    twins = [s for s in symbols if s.kind == "type"]
    methods = {s.name: s for s in symbols if s.kind == "method"}
    fields = {s.name: s for s in symbols if s.kind == "field"}
    # The fields ARE inside their own twin, so containment answers for them.
    assert fields["a"].parent == twins[0].id
    assert fields["b"].parent == twins[1].id
    # The methods are not, and both keep the stamper's first-twin choice.
    assert methods["A"].parent == twins[0].id
    assert methods["B"].parent == twins[0].id
