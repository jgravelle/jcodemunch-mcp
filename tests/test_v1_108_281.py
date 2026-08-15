"""Rust, Go, Java and PHP constants (#428, second half).

`tests/test_constant_extraction_guard.py` asserts the CLAIM -- a language that
declares `constant_patterns` extracts at least one constant -- against one
minimal sample each. That is the right shape for a ratchet and it is deliberately
thin: one constant proves the branch exists and nothing about what it accepts.

This file covers what the minimal samples cannot, one test per decision that was
actually made while implementing:

* the reporter's own shape (constants nested inside `pub mod`), which is what
  read as zero symbols,
* the N-names-per-declaration forms that made `_extract_constants` plural,
* and the three exclusions, each of which is a thing that is NOT a constant and
  would otherwise arrive as one.

⚠ The exclusions matter more than the inclusions here. A missing constant is a
recall bug the reporter could see; `notStatic` or a function-local arriving as
`kind="constant"` is a precision bug nobody would look for.
"""
import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """Answer the parser, not the developer's config file (#411)."""
    monkeypatch.setattr(
        "jcodemunch_mcp.config.is_language_enabled",
        lambda language, repo=None: True,
    )


def _constants(source: str, filename: str, language: str) -> list[str]:
    return [s.name for s in parse_file(source, filename, language) if s.kind == "constant"]


def _symbols(source: str, filename: str, language: str) -> list[tuple[str, str]]:
    return [(s.kind, s.name) for s in parse_file(source, filename, language)]


# ── Rust ────────────────────────────────────────────────────────────────────

def test_rust_constants_inside_nested_modules_are_extracted():
    """The reporter's file: `pub const` inside `pub mod`, indexed as zero symbols.

    935 constants in one generated contract file, and `index_folder` reported it
    in `no_symbols_files`. The nesting was his first hypothesis and the wrong
    one -- no Rust const was extracted anywhere -- but the nested shape is what
    the real file looks like, so it is what the regression test uses.
    """
    source = (
        "pub mod invoice {\n"
        '    pub const DOC_TYPE: &str = "invoice";\n'
        "    pub mod line {\n"
        '        pub const UNIT: &str = "each";\n'
        "    }\n"
        "}\n"
        'const TOP_LEVEL: &str = "a";\n'
    )
    assert _constants(source, "contract.rs", "rust") == ["DOC_TYPE", "UNIT", "TOP_LEVEL"]


def test_rust_static_mut_is_not_a_constant():
    """`static mut` says in its own declaration that it can change.

    The discriminator is the grammar's `mutable_specifier`, not a naming rule --
    the same evidence-over-heuristic choice Bash makes by accepting `readonly`
    and rejecting a bare `declare`.
    """
    source = (
        'const KEPT: &str = "a";\n'
        'static ALSO_KEPT: &str = "b";\n'
        "static mut COUNTER: i32 = 0;\n"
    )
    assert _constants(source, "probe.rs", "rust") == ["KEPT", "ALSO_KEPT"]


def test_rust_lowercase_constants_are_not_filtered_out():
    """No UPPER_CASE test on a language where `const` IS the declaration.

    Python needs the case heuristic because an assignment is a constant only by
    convention. Applying it to Rust could only delete correct results.
    """
    source = 'const lowercase_but_still_const: u8 = 1;\n'
    assert _constants(source, "probe.rs", "rust") == ["lowercase_but_still_const"]


# ── Go ──────────────────────────────────────────────────────────────────────

def test_go_grouped_and_multi_name_declarations_yield_every_name():
    """Two nestings bind N names: `const ( ... )` blocks and `const A, B = 1, 2`.

    This is why `_extract_constants` is plural. A single-symbol return would
    have reported the first name of a 935-line grouped block and dropped the
    rest, which looks like success.
    """
    source = (
        "package p\n\n"
        'const SINGLE = "x"\n\n'
        "const (\n\tGROUPED_A = 1\n\tGROUPED_B = 2\n)\n\n"
        "const MULTI_A, MULTI_B = 1, 2\n"
    )
    assert _constants(source, "probe.go", "go") == [
        "SINGLE",
        "GROUPED_A",
        "GROUPED_B",
        "MULTI_A",
        "MULTI_B",
    ]


def test_go_unexported_lowercase_constants_are_kept():
    """Lowercase is Go's visibility rule, not an accident to filter on."""
    source = 'package p\n\nconst unexported = "x"\n'
    assert _constants(source, "probe.go", "go") == ["unexported"]


# ── PHP ─────────────────────────────────────────────────────────────────────

def test_php_comma_separated_constants_yield_every_name():
    source = "<?php\nconst FIRST = 'a', SECOND = 'b';\nfunction visible() {}\n"
    assert _constants(source, "probe.php", "php") == ["FIRST", "SECOND"]


# ── Java ────────────────────────────────────────────────────────────────────

def test_java_requires_both_static_and_final():
    """A Java constant is `static final`. Neither modifier alone qualifies.

    ⚠ This is the precision half. `final int` is per-instance and `static int`
    is mutable shared state; admitting either would reclassify ordinary fields
    as constants in every Java class in an index.
    """
    source = (
        "class Probe {\n"
        "  static final int KEPT = 1, ALSO_KEPT = 2;\n"
        "  final int instanceFinal = 3;\n"
        "  static int mutableShared = 4;\n"
        "  int plain = 5;\n"
        "  void visible() {}\n"
        "}\n"
    )
    assert _constants(source, "Probe.java", "java") == ["KEPT", "ALSO_KEPT"]


def test_java_function_locals_never_become_constants():
    """The widened gate accepts a CONTAINER parent, never a function parent.

    `parent_symbol is None` is what kept locals out before #428, and widening it
    to reach class fields must not also reach method bodies -- so this asserts
    the boundary of the widening rather than the widening itself.
    """
    source = (
        "class Probe {\n"
        "  void visible() {\n"
        "    final int localFinal = 9;\n"
        "    int local = 10;\n"
        "  }\n"
        "}\n"
    )
    assert _constants(source, "Probe.java", "java") == []


def test_java_class_and_method_symbols_are_unchanged():
    """The widening must add constants without disturbing the existing walk."""
    source = "class Probe {\n  static final int K = 1;\n  void visible() {}\n}\n"
    # Source order: the constant is declared before the method, and the walk
    # emits in the order it meets nodes.
    assert _symbols(source, "Probe.java", "java") == [
        ("class", "Probe"),
        ("constant", "K"),
        ("method", "visible"),
    ]


# ── The widening is named, not general ──────────────────────────────────────

def test_only_named_languages_reach_constants_through_a_container():
    """A container parent unlocks the constant walk for Java and nothing else.

    ⚠⚠ The general version of this fix -- accepting `parent_is_container` for
    every language -- was declined. Python class bodies would start emitting
    constants they have never emitted, moving symbol counts in every index and
    every published dead-code grade. This test is what makes the narrow choice
    durable: widen the set deliberately, with a sample, or not at all.
    """
    from jcodemunch_mcp.parser.extractor import _CLASS_SCOPED_CONSTANT_LANGUAGES

    assert _CLASS_SCOPED_CONSTANT_LANGUAGES == frozenset({"java"})

    # A Python class body holds an UPPER_CASE assignment, which is the exact
    # shape the general widening would have admitted.
    source = "class Config:\n    TIMEOUT = 30\n\n\nMODULE_LEVEL = 1\n"
    assert _constants(source, "conf.py", "python") == ["MODULE_LEVEL"]
