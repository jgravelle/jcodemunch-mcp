"""#784: a Python class's state yielded no symbol unless the class was a
dataclass, an attrs class or a DIRECT subclass of `BaseModel`.

`tally: int = 0` and `LIMIT = 3` in a plain class were absent, so the class read
as methods-only to `search_symbols`, the outline and the file summary. #355
indexed annotated fields for "field-centric" classes and left every other class
alone on purpose; jjg reversed that on 2026-09-19, for consistency with Java
(#735), PHP (#743), Kotlin, Swift and C++ (#755).

The rule: every class-body binding of ONE plain name is a symbol owned by its
class. UPPER_CASE is a `constant` (the module-level convention, one predicate),
anything else a `field`. Dunders are class machinery and stay out.

⚠ This moves symbol COUNTS, deliberately, and moves no grade: the dead-code and
untested tools read `function` and `method` alone.
"""

from collections import Counter

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """Answer the parser, not the developer's config file (#411)."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source):
    symbols = parse_file(source, "m.py", "python")
    by_id = {s.id: s for s in symbols}
    return [
        (s.kind, s.qualified_name, by_id[s.parent].name if s.parent else None)
        for s in symbols
    ]


REPORTED = (
    "class Audit:\n"
    "    LIMIT = 3\n"
    "    tally: int = 0\n"
    "    def run_it(self):\n"
    "        return 1\n"
    "    @property\n"
    "    def view(self):\n"
    "        return 2\n"
)


def test_the_reported_class_has_its_state():
    assert _rows(REPORTED) == [
        ("class", "Audit", None),
        ("constant", "Audit.LIMIT", "Audit"),
        ("field", "Audit.tally", "Audit"),
        ("method", "Audit.run_it", "Audit"),
        ("method", "Audit.view", "Audit"),
    ]


@pytest.mark.parametrize("label, body, expected", [
    ("annotated with a default", "x: int = 0", ("field", "x")),
    ("annotated, no default", "x: int", ("field", "x")),
    ("bare assignment", "x = 0", ("field", "x")),
    ("upper case is a constant", "MAX_SIZE = 10", ("constant", "MAX_SIZE")),
    ("annotated upper case", "MAX_SIZE: int = 10", ("constant", "MAX_SIZE")),
    ("a ClassVar is class state", "registry: ClassVar[dict] = {}", ("field", "registry")),
    ("an enum-style member", "RED = 1", ("constant", "RED")),
    ("a private name", "_cache = None", ("field", "_cache")),
])
def test_every_single_name_binding_in_a_plain_class_is_state(label, body, expected):
    symbols = parse_file(f"class C:\n    {body}\n", "m.py", "python")
    assert [(s.kind, s.name) for s in symbols] == [("class", "C"), expected], label


@pytest.mark.parametrize("body", [
    "__slots__ = ('a',)",
    "__all__ = []",
    "__hash__ = None",
    "a, b = 1, 2",
    "x += 1",
    "self.x = 1",
    "d['k'] = 1",
    "pass",
    "'''docstring'''",
])
def test_what_is_not_a_single_name_binding_adds_nothing(body):
    """The over-emission direction, written first. Dunders are class
    machinery; a tuple, subscript, attribute or augmented target binds no ONE
    plain name a reader could look up."""
    symbols = parse_file(f"class C:\n    {body}\n", "m.py", "python")
    assert [(s.kind, s.name) for s in symbols] == [("class", "C")]


def test_a_binding_inside_a_method_is_a_local_and_never_class_state():
    source = (
        "class C:\n"
        "    def m(self):\n"
        "        local = 1\n"
        "        LOCAL_MAX = 2\n"
        "        self.attr = 3\n"
        "    if True:\n"
        "        guarded = 4\n"
    )
    names = [s.name for s in parse_file(source, "m.py", "python")]
    assert names == ["C", "m"]


def test_a_method_is_not_also_state_and_nothing_is_emitted_twice():
    """A Counter, because a set cannot count. `handler = staticmethod(f)` is a
    binding and IS state; `def` is a method, once."""
    source = (
        "class C:\n"
        "    x: int = 0\n"
        "    def m(self): pass\n"
        "    handler = staticmethod(len)\n"
    )
    symbols = parse_file(source, "m.py", "python")
    assert Counter(s.name for s in symbols) == Counter(["C", "x", "m", "handler"])
    assert all("~" not in s.id for s in symbols)


def test_a_nested_classs_state_belongs_to_the_nested_class():
    source = "class Outer:\n    a = 1\n    class Meta:\n        ordering = ['a']\n"
    rows = _rows(source)
    assert ("field", "Outer.a", "Outer") in rows
    assert ("field", "Outer.Meta.ordering", "Meta") in rows


def test_a_model_that_inherits_its_base_indirectly_has_its_fields():
    """⚠ Found while measuring #784. The field-centric gate keyed on the base
    NAME (`BaseModel`), so `class Child(Base)` got nothing: 221 of the 314
    class-body names missing from the `mcp` package were this shape. A guard
    written against a spelling; the property needs no gate at all."""
    source = (
        "from pydantic import BaseModel\n"
        "class Base(BaseModel):\n"
        "    id: int\n"
        "class Child(Base):\n"
        "    name: str\n"
    )
    assert [q for k, q, _ in _rows(source) if k == "field"] == ["Base.id", "Child.name"]


def test_a_dataclass_field_is_exactly_what_it_was():
    """#355's behaviour is a subset of the new rule and must not move."""
    source = (
        "from dataclasses import dataclass, field\n"
        "@dataclass\n"
        "class T:\n"
        "    key: str\n"
        "    cols: list[str] = field(default_factory=list)\n"
    )
    cols = [s for s in parse_file(source, "m.py", "python") if s.name == "cols"]
    assert [(s.kind, s.signature, s.line) for s in cols] == [
        ("field", "cols: list[str] = field(default_factory=list)", 5)
    ]


def test_module_level_bindings_are_untouched():
    """The constant channel's file-scope rule is a different question."""
    source = "MAX = 1\nlower = 2\nclass C:\n    pass\n"
    assert [(s.kind, s.name) for s in parse_file(source, "m.py", "python")] == [
        ("constant", "MAX"), ("class", "C"),
    ]
