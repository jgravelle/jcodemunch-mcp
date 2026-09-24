"""A Kotlin top-level `val`/`var` is a module binding, not a member (#807).

Kotlin published `val topLevel = 1` and `var topVar = 2` as `property`, the
word `KIND_ORDER` reserves for class state: "a top-level binding belongs to no
type, and reusing that kind would mix module bindings into every consumer
asking about a class's members." Both had `parent=None`.

Ruling, the one Swift and Scala already carry (`let`/`val` -> `constant`,
`var` -> `variable` at module scope), and JS `const` and Go `const` beside
them:
- a file-scope `var` is a `variable`;
- a file-scope `val` whose value is its initializer is a `constant`;
- a file-scope `val` whose READ runs code (a getter, which every extension
  property has, or a delegate) is a `variable`: its value can differ between
  reads, which is what Swift's top-level computed `var` already reads as.
  A getter or a `by` delegate on its own line is a SIBLING of the
  declaration at file scope (tree-sitter-kotlin spills it), so the sibling
  is read too, past comments.
- The constant channel decides first: `const val` and a SCREAMING_CASE
  `val` stay `constant` even with an accessor (#428, #732).

#732's SCREAMING_CASE rule still decides the class-body case, where Kotlin
uses `val` for ordinary properties; members of classes, objects, companions
and object literals stay `property`. Scope is the node's DIRECT parent
(`source_file`), never "no container parent": an object literal's members
have a function or a property as their parent symbol and are members.
"""

from __future__ import annotations

from unittest import mock

from jcodemunch_mcp.parser.extractor import parse_file


def _kinds(source: str, filename: str = "Top.kt") -> dict[str, str]:
    with mock.patch("jcodemunch_mcp.config.is_language_enabled", return_value=True):
        return {s.qualified_name: s.kind for s in parse_file(source, filename, "kotlin")}


def test_the_reported_pair_splits_by_mutability():
    assert _kinds("val topLevel = 1\nvar topVar = 2\n") == {
        "topLevel": "constant",
        "topVar": "variable",
    }


def test_no_top_level_binding_carries_a_member_word():
    source = (
        "val a = 1\n"
        "var b = 2\n"
        "private val c = 3\n"
        "internal var d = 4\n"
        "lateinit var e: String\n"
        "@JvmField val f = 5\n"
        "val g: Int = 6\n"
        "expect val h: Int\n"
        "val String.i: Char get() = this[0]\n"
        "val j by lazy { 7 }\n"
        "val k: Int\n  get() = 8\n"
        "var l: Int = 9\n  set(v) { field = v }\n"
    )
    kinds = _kinds(source)
    assert set(kinds) == set("abcdefghijkl"), kinds
    assert not {q for q, k in kinds.items() if k in ("property", "field")}, kinds


def test_an_initialised_val_is_a_constant_whatever_its_modifiers():
    kinds = _kinds("private val c = 3\n@JvmField val f = 5\nval g: Int = 6\n")
    assert kinds == {"c": "constant", "f": "constant", "g": "constant"}


def test_a_val_whose_read_runs_code_is_a_variable():
    """A getter (same line or the next) or a delegate: the value can differ
    between reads, so it is not a constant."""
    kinds = _kinds(
        "val String.i: Char get() = this[0]\n"
        "val j by lazy { 7 }\n"
        "val k: Int\n  get() = 8\n"
        "val after = 9\n"
    )
    assert kinds == {"i": "variable", "j": "variable", "k": "variable", "after": "constant"}


def test_a_spilled_accessor_or_delegate_is_read_past_comments():
    """Review: tree-sitter-kotlin spills a delegate on its own line into a
    sibling expression starting with `by`, and a comment between a declaration
    and its spilled getter hid the getter. Both published `constant`."""
    kinds = _kinds(
        "val d\n  by lazy { 1 }\n"
        "private val vm: VM\n    by viewModels()\n"
        "val k: Int\n  // why\n  get() = 8\n"
        "val m: Int\n  /** doc */\n  get() = 9\n"
        "val after = 10\n"
    )
    assert kinds == {"d": "variable", "vm": "variable", "k": "variable", "m": "variable", "after": "constant"}


def test_a_call_named_by_after_an_initialised_val_is_not_its_delegate():
    kinds = _kinds("val a = 1\nby(3)\n")
    assert kinds["a"] == "constant", kinds


def test_expect_and_actual_are_pinned_as_they_read():
    """An `expect val` has no accessor or delegate in its own file, so it is a
    constant; an `actual` with a getter is a variable. One multiplatform
    declaration can carry two kinds across its files, decided per file."""
    assert _kinds("expect val h: Int\n") == {"h": "constant"}
    assert _kinds("actual val h: Int get() = 2\n") == {"h": "variable"}
    assert _kinds("actual val h: Int = 2\n") == {"h": "constant"}


def test_the_constant_channel_decides_first_even_over_an_accessor():
    """A SCREAMING_CASE or `const` name is the author's declaration (#428,
    #732) and wins at file scope; the name-less twin follows the #807 rule."""
    assert _kinds("val LOG by lazy { 1 }\nval MAX get() = 3\nval log by lazy { 1 }\n") == {
        "LOG": "constant",
        "MAX": "constant",
        "log": "variable",
    }


def test_every_var_is_a_variable():
    kinds = _kinds("var b = 2\ninternal var d = 4\nlateinit var e: String\nvar l: Int = 9\n  set(v) { field = v }\n")
    assert kinds == {"b": "variable", "d": "variable", "e": "variable", "l": "variable"}


def test_the_screaming_case_and_const_channel_is_unmoved():
    assert _kinds("const val C = 1\nval MAX_X = 2\n") == {"C": "constant", "MAX_X": "constant"}


def test_members_stay_properties():
    source = (
        "class K {\n  val p = 1\n  var q = 2\n"
        "  companion object {\n    val cv = 3\n    var cvar = 4\n  }\n}\n"
        "object O {\n  val r = 5\n  var s = 6\n}\n"
        "enum class E { A;\n  val t = 7\n}\n"
    )
    kinds = _kinds(source)
    for member in ("K.p", "K.q", "K.cv", "K.cvar", "O.r", "O.s", "E.t"):
        assert kinds.get(member) == "property", (member, kinds)


def test_an_object_literal_member_is_a_member_although_its_parent_is_not_a_type():
    """The scope test is the node's direct parent, never `parent_is_container`:
    these members' parent SYMBOL is a function and a property."""
    kinds = _kinds(
        "fun f() = object : Runnable {\n  val a = 1\n  override fun run() {}\n}\n"
        "val o = object {\n  val b = 2\n}\n"
    )
    assert kinds["f.a"] == "property", kinds
    assert kinds["o.b"] == "property", kinds
    assert kinds["o"] == "constant", kinds


def test_a_script_file_follows_the_same_rule():
    assert _kinds("val x = 1\nvar y = 2\n", "build.gradle.kts") == {"x": "constant", "y": "variable"}


def test_a_local_is_still_not_a_symbol():
    assert _kinds("fun f() {\n  val local = 1\n  var other = 2\n}\n") == {"f": "function"}
