"""A constant declared inside a container is owned by it (#780, #783).

``_constant_symbol`` hardcodes ``qualified_name = name`` and takes no parent, so
every constant it mints is bare. ``_walk_tree`` repaired that at the call site
for ONE language -- ``if language == "rust"`` -- which is a guard written against
a spelling: Java's ``static final`` field, PHP's class ``const`` and Kotlin's
``const val`` all reach the same channel through the same gate and all came out
with ``parent=None``.

⚠⚠ **A member with no parent is invisible to every parent-keyed reader**, which
is what makes this a defect rather than a cosmetic naming difference: the file
summary (#760) counts members by ``parent``, so a PHP class whose only members
are constants reported as having none.

⚠ **The gate above this decides WHICH constants are extracted; this decides who
owns the ones that are.** They are different questions and the fix belongs to
the second: no constant becomes a symbol here that was not one before, so the
languages whose constants are file-scope only are untouched by construction --
their ``parent_symbol`` is None and nothing fires.

⚠ Kotlin carries no issue of its own because the audit's Kotlin sample reaches
the ``property`` channel, so the audit could not see this cell at all. It is
here because the property is asserted over
``_CLASS_SCOPED_CONSTANT_LANGUAGES``, not over the two languages that were
reported -- a language added to that set later inherits the assertion.
"""

import pytest

from jcodemunch_mcp.parser.extractor import (
    _CLASS_SCOPED_CONSTANT_LANGUAGES,
    parse_file,
)


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """This box disables some languages in its own config; the gate is function-local."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


#: language -> (filename, container name, container kind, constant name, source).
#: One sample per class-scoped language, so the set below cannot grow silently.
_CLASS_SCOPED: dict[str, tuple[str, str, str, str, str]] = {
    "java": ("Audit.java", "Audit", "class", "LIMIT", (
        "class Audit {\n"
        "    static final int LIMIT = 3;\n"
        "}\n"
    )),
    "kotlin": ("Audit.kt", "Audit", "class", "LIMIT", (
        "class Audit {\n"
        "    val LIMIT = 3\n"
        "}\n"
    )),
    "php": ("a.php", "Audit", "class", "LIMIT", (
        "<?php\n"
        "class Audit {\n"
        "    const LIMIT = 3;\n"
        "}\n"
    )),
}


def _symbols(language: str, filename: str, source: str):
    return parse_file(source, filename, language)


def _one(symbols, name: str):
    hits = [s for s in symbols if s.name == name]
    assert len(hits) == 1, [(s.name, s.kind) for s in symbols]
    return hits[0]


def test_every_class_scoped_language_has_a_sample():
    """The set is the roster; a language added to it without a sample fails here.

    ⚠ The ratchet, not the instance: #780 and #783 named Java and PHP, and the
    same channel had the same defect in Kotlin, which nobody reported.
    """
    assert set(_CLASS_SCOPED) == set(_CLASS_SCOPED_CONSTANT_LANGUAGES)


@pytest.mark.parametrize("language", sorted(_CLASS_SCOPED))
def test_a_class_constant_is_owned_by_its_class(language):
    filename, container, container_kind, const_name, source = _CLASS_SCOPED[language]
    symbols = _symbols(language, filename, source)
    owner = _one(symbols, container)
    assert owner.kind == container_kind
    member = _one(symbols, const_name)
    assert member.kind == "constant"
    assert member.parent == owner.id
    assert member.qualified_name == f"{container}.{const_name}"
    assert member.id.endswith(f"{container}.{const_name}#constant")


@pytest.mark.parametrize("language", sorted(_CLASS_SCOPED))
def test_the_class_is_not_the_only_member_holder(language):
    """Non-vacuity: the owner id the member claims is the one a reader filters on."""
    filename, container, _kind, const_name, source = _CLASS_SCOPED[language]
    symbols = _symbols(language, filename, source)
    owner = _one(symbols, container)
    members = [s for s in symbols if s.parent == owner.id]
    assert [s.name for s in members] == [const_name]


def test_one_declaration_binding_two_names_owns_both():
    """PHP `const X = 1, Y = 2;` is one node and two declarations (#428)."""
    symbols = _symbols("php", "c.php", "<?php\nclass A { const X = 1, Y = 2; }\n")
    owner = _one(symbols, "A")
    assert {s.name: s.parent for s in symbols if s.kind == "constant"} == {
        "X": owner.id,
        "Y": owner.id,
    }
    assert {s.qualified_name for s in symbols if s.kind == "constant"} == {"A.X", "A.Y"}


def test_a_kotlin_const_val_in_a_companion_object_is_owned_by_the_CLASS():
    """The form the prose names, which the roster sample does not exercise.

    ⚠ `_CLASS_SCOPED` uses a SCREAMING_CASE `val` for Kotlin, and `const val` is
    the spelling every docstring and the CHANGELOG entry reach for. They are
    different declarations and only one of them was under a test; review caught
    that the named one was not.

    ⚠⚠ The owner is `Audit`, not the companion object. A companion object is not
    a symbol here, so `parent_symbol` at that depth is still the class -- which
    is the answer a reader wants (`Audit.LIMIT` is how the constant is written
    at the call site) and is worth pinning rather than leaving to inspection.
    """
    symbols = _symbols("kotlin", "Audit.kt", (
        "class Audit {\n"
        "    companion object { const val LIMIT = 3 }\n"
        "}\n"
    ))
    owner = _one(symbols, "Audit")
    assert owner.kind == "class"
    member = _one(symbols, "LIMIT")
    assert member.kind == "constant"
    assert member.parent == owner.id
    assert member.qualified_name == "Audit.LIMIT"


def test_two_classes_sharing_a_constant_name_no_longer_collide():
    """The second-order consequence, and the one that ADDS symbols to an index.

    ⚠⚠ Before the fix both constants minted the SAME id (`x.php::K#constant`),
    because the id is built from the qualified name and both were bare. Two
    declarations sharing one id is the shape #571 and #741 each paid for: one of
    them is unreachable by lookup. Qualifying separates them, so a file holding
    two same-named class constants gains a distinct symbol rather than merely
    renaming one.
    """
    symbols = _symbols("php", "x.php", (
        "<?php\n"
        "class A { const K = 1; }\n"
        "class B { const K = 2; }\n"
    ))
    ids = [s.id for s in symbols if s.kind == "constant"]
    assert ids == ["x.php::A.K#constant", "x.php::B.K#constant"]
    assert len(set(ids)) == 2


def test_a_file_scope_constant_stays_bare():
    """The other half, and the one a widening would break.

    ⚠ A constant with no container has nothing to be owned by, and giving it a
    qualified name would move the id of every file-scope constant in every
    index. `parent_symbol is None` is the whole guard.
    """
    symbols = _symbols("php", "b.php", "<?php\nconst LIMIT = 3;\n")
    member = _one(symbols, "LIMIT")
    assert member.parent is None
    assert member.qualified_name == "LIMIT"


def test_rust_keeps_the_ownership_it_already_had():
    """Rust was the one language the call site repaired; it must not move."""
    symbols = _symbols("rust", "a.rs", "struct Audit;\nimpl Audit {\n    const LIMIT: i32 = 3;\n}\n")
    owner = _one(symbols, "Audit")
    member = _one(symbols, "LIMIT")
    assert member.parent == owner.id
    assert member.qualified_name == "Audit.LIMIT"


def test_a_rust_function_local_constant_keeps_its_function():
    """Rust reaches this branch through the FUNCTION-scoped gate, not the class
    one, and that half is unchanged: the owner is the `fn`."""
    symbols = _symbols("rust", "c.rs", "fn outer() {\n    const LIMIT: i32 = 3;\n}\n")
    owner = _one(symbols, "outer")
    member = _one(symbols, "LIMIT")
    assert member.parent == owner.id
    assert member.qualified_name == "outer.LIMIT"
