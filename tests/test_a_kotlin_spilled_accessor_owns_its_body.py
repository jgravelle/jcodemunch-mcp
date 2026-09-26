"""A Kotlin accessor on its own line owns what its body declares (#858).

tree-sitter-kotlin spills a getter or setter written on its own line into a
SIBLING of the `property_declaration` (a `getter`/`setter` node, or an
expression starting `get(` it error-recovers). Everything declared in that
body was walked with the ENCLOSING owner: at file scope no owner at all (an
object literal's `val gg` a bare `property`, a local `fun loc` a fabricated
top-level `function`), at class scope the class (`C.gg`; an object literal's
`fun` promoted to a method of the class). The same bytes with the accessor on
the property's line gave `g.gg`, `g.h` and `k.loc`. #807 reads the sibling for
the property's KIND; ownership was a separate walk. Reporter: @jgravelle.

Rulings:
- The walk adopts a spilled accessor sibling as the property's own child, so
  the split form answers exactly what the one-line form answers: owner,
  qualified name, kind AND span (the property's span covers its accessor, as
  the one-line form's always has). This is agreement, not id stability: the
  split form's ids move to the one-line form's (`gg` -> `g.gg`), and every
  such property's span widens.
- Comments and annotations between the property and its accessor go with
  the accessor; with no accessor after them nothing is adopted.
- A `by` delegate on its own line is adopted under #807's gate (no
  initializer, no `;` before it); nothing follows a delegate. In a class
  body the grammar ends the class at it, so members AFTER it are still
  filed at file scope, as on main (LEDGER L-33).
- The CONSTANT channel owns nothing in either form (`val MAX: Any get() =
  object { val gg = 1 }`), recorded as LEDGER L-32; adoption keeps the two
  forms equal there too.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


def _rows(source: str):
    symbols = parse_file(source, "a.kt", "kotlin")
    by_id = {s.id: s for s in symbols}
    return [(s.kind, s.qualified_name, by_id[s.parent].name if s.parent else None) for s in symbols]


def test_the_reported_object_literal_is_owned_by_its_property():
    source = "val g: Any\n    get() = object {\n        val gg = 1\n        fun h() = 2\n    }\n"
    assert _rows(source) == [("variable", "g", None), ("property", "g.gg", "g"), ("function", "g.h", "g")]


#: (split form, one-line form) pairs over the same declarations.
_PAIRS = [
    pytest.param(
        "val g: Any\n    get() = object {\n        val gg = 1\n        fun h() = 2\n    }\n",
        "val g: Any get() = object {\n        val gg = 1\n        fun h() = 2\n    }\n",
        id="file-object-literal",
    ),
    pytest.param(
        "val k: Any\n    get() { fun loc() = object { val a = 1 }; return loc() }\n",
        "val k: Any get() { fun loc() = object { val a = 1 }; return loc() }\n",
        id="file-local-function",
    ),
    pytest.param(
        "val k: Any\n    get() = when (x) { 1 -> object { val a = 1 }\n        else -> object { fun g() = 2 } }\n",
        "val k: Any get() = when (x) { 1 -> object { val a = 1 }\n        else -> object { fun g() = 2 } }\n",
        id="file-when-branches",
    ),
    pytest.param(
        "var s: Int = 0\n    set(v) { fun inner() = 1; field = v }\n",
        "var s: Int = 0 set(v) { fun inner() = 1; field = v }\n",
        id="file-setter",
    ),
    pytest.param(
        "var s: Int = 0\n    get() = field\n    set(v) { fun inner() = 1; field = v }\n",
        "var s: Int = 0 get() = field; set(v) { fun inner() = 1; field = v }\n",
        id="file-getter-then-setter",
    ),
    pytest.param(
        "class C {\n    val g: Any\n        get() = object {\n            val gg = 1\n        }\n}\n",
        "class C {\n    val g: Any get() = object {\n            val gg = 1\n        }\n}\n",
        id="class-object-literal",
    ),
    pytest.param(
        "object O {\n    val g: Any\n        get() = object { fun h() = 1 }\n}\n",
        "object O {\n    val g: Any get() = object { fun h() = 1 }\n}\n",
        id="object-member-function",
    ),
    # Review round 1: a `by` delegate on its own line spills the same way; in
    # a class body the grammar error-recovers it into an `ERROR`.
    pytest.param(
        "val vm: Any\n    by lazy { object { val gg = 1 } }\n",
        "val vm: Any by lazy { object { val gg = 1 } }\n",
        id="file-delegate",
    ),
    pytest.param(
        "class C {\n    val vm: Any\n        by lazy { object { fun h() = 1 } }\n}\n",
        "class C {\n    val vm: Any by lazy { object { fun h() = 1 } }\n}\n",
        id="class-delegate",
    ),
]


def test_a_comment_and_an_annotation_go_with_the_accessor_they_precede():
    """The one-line form is no reference here: `val g: Any @JvmName("x")
    get() = object { ... }` loses `gg` on main too."""
    source = "val g: Any\n    // why\n    @JvmName(\"x\") get() = object { val gg = 1 }\n"
    assert _rows(source) == [("variable", "g", None), ("property", "g.gg", "g")]


@pytest.mark.parametrize("split, one_line", _PAIRS)
def test_the_split_form_answers_what_the_one_line_form_answers(split, one_line):
    assert _rows(split) == _rows(one_line)


@pytest.mark.parametrize("split, one_line", _PAIRS)
def test_nothing_in_a_spilled_accessor_is_left_without_an_owner(split, one_line):
    """Every symbol after the property is the property's: nothing inside an
    accessor is top-level, and nothing is misfiled under the class."""
    rows = _rows(split)
    prop = 1 if rows[0][0] == "class" else 0
    owner_name = rows[prop][1].rsplit(".", 1)[-1]
    assert len(rows) > prop + 1, rows
    assert all(owner == owner_name for _, _, owner in rows[prop + 1:]), rows


def test_the_property_span_covers_its_accessor():
    source = "val g: Any\n    get() = object {\n        val gg = 1\n    }\nfun after() = 2\n"
    g = next(s for s in parse_file(source, "a.kt", "kotlin") if s.name == "g")
    assert source.encode()[g.byte_offset:g.byte_offset + g.byte_length].decode().endswith("}")
    assert g.end_line == 4


def test_a_declaration_after_the_accessor_keeps_its_own_owner():
    source = "val p: Int\n    get() = 1\nfun after() = 2\n"
    assert _rows(source) == [("variable", "p", None), ("function", "after", None)]


def test_a_comment_with_no_accessor_after_it_is_not_adopted():
    source = "val p = 1\n// note\nfun after() = 2\n"
    p = next(s for s in parse_file(source, "a.kt", "kotlin") if s.name == "p")
    assert p.byte_length == len("val p = 1")


def test_the_split_form_takes_the_one_line_forms_ids():
    split = "val g: Any\n    get() = object {\n        val gg = 1\n    }\n"
    one_line = "val g: Any get() = object {\n        val gg = 1\n    }\n"
    ids = lambda s: [x.id.split("::", 1)[1] for x in parse_file(s, "a.kt", "kotlin")]
    assert ids(split) == ids(one_line) == ["g#variable", "g.gg#property"]


@pytest.mark.parametrize("source", ["val a = 1\nby(x)\n", "val a: Any;\nby(x)\n"])
def test_a_by_after_an_initializer_or_a_semicolon_is_not_a_delegate(source):
    """#807's gate: a delegate cannot follow an initializer or a `;`."""
    a = next(s for s in parse_file(source, "a.kt", "kotlin") if s.name == "a")
    assert source.encode()[a.byte_offset:a.byte_offset + a.byte_length].count(b"\n") == 0


def test_nothing_is_adopted_after_a_delegate():
    """Review round 2: a delegate ends the property; a line after it is not
    one of its accessors."""
    source = "val a: Any\n    by lazy { 1 }\n    get() = 2\n"
    a = next(s for s in parse_file(source, "a.kt", "kotlin") if s.name == "a")
    assert a.byte_length == len("val a: Any\n    by lazy { 1 }")
