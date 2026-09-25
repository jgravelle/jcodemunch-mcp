"""#745: a declared node type must actually EXTRACT, not merely be nameable.

`tests/test_language_spec_maps_agree.py` (#712) asks whether a node type in
`symbol_node_types` has a way to get its name: an entry in `name_fields`, or a
branch in `_extract_name`. That is a question about the MAPS. This file asks
the question about the PRODUCT — parse a sample and see whether the symbol
comes out — and the two differ by exactly one indirection.

⚠⚠ **PHP is the instance that proves the difference.**
`php.symbol_node_types["property_declaration"] = "property"` and
`php.name_fields["property_declaration"] = "name"`, so #712's guard passes; but
the grammar sets no `name` field on that node (its named children are
`visibility_modifier` and `property_element`), so `child_by_field_name("name")`
returns `None` and no PHP class property has ever been indexed (#743).

⚠⚠ **A static scan cannot close this, which is why the check is behavioural.**
The compiled grammar's symbol table (`Language.node_kind_count` /
`node_kind_for_id`) enumerates node KINDS, so it can prove a declared node type
is never emitted — #724's property A, which reports clean across every declared
pair — but it cannot enumerate which FIELDS a grammar sets on a node. The PHP
case is invisible to it.

⚠ The samples are the point of maintenance and they are deliberately
unavoidable: `test_every_declared_node_type_has_a_sample` fails BY NAME for a
declared node type with no sample, so a spec cannot grow a form that nothing
exercises. Adding two lines of sample source is the cost of declaring a form.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY

#: `(filename, source)` per declared node type, per language.
#:
#: ⚠ The sample is the SMALLEST source that spells the form. It is not a
#: fixture of realistic code: a bigger sample makes a failure harder to read
#: and invites a second form sneaking in under the first one's row.
_SAMPLES: dict[str, dict[str, tuple[str, str]]] = {
    "arduino": {
        "alias_declaration": ("a.ino", "using Alias = int;\n"),
        "class_specifier": ("a.ino", "class Probe { int x; };\n"),
        "declaration": ("a.ino", "int probe(int a);\n"),
        "enum_specifier": ("a.ino", "enum Probe { A, B };\n"),
        "field_declaration": ("a.ino", "class Holder { void probe(); };\n"),
        "function_definition": ("a.ino", "void probe() {}\n"),
        "struct_specifier": ("a.ino", "struct Probe { int a; };\n"),
        "type_definition": ("a.ino", "typedef int probe_t;\n"),
        "union_specifier": ("a.ino", "union Probe { int a; float b; };\n"),
    },
    "bash": {
        "function_definition": ("a.sh", "probe() {\n  echo hi\n}\n"),
    },
    "c": {
        "declaration": ("a.c", "int probe(int a);\n"),
        "enum_specifier": ("a.c", "enum Probe { A, B };\n"),
        "function_definition": ("a.c", "int probe(void) { return 0; }\n"),
        "struct_specifier": ("a.c", "struct Probe { int a; };\n"),
        "type_definition": ("a.c", "typedef int probe_t;\n"),
        "union_specifier": ("a.c", "union Probe { int a; float b; };\n"),
    },
    "cpp": {
        "alias_declaration": ("a.cpp", "using Alias = int;\n"),
        "class_specifier": ("a.cpp", "class Probe { int x; };\n"),
        "declaration": ("a.cpp", "int probe(int a);\n"),
        "enum_specifier": ("a.cpp", "enum Probe { A, B };\n"),
        "field_declaration": ("a.cpp", "class Holder { void probe(); };\n"),
        "function_definition": ("a.cpp", "void probe() {}\n"),
        "struct_specifier": ("a.cpp", "struct Probe { int a; };\n"),
        "type_definition": ("a.cpp", "typedef int probe_t;\n"),
        "union_specifier": ("a.cpp", "union Probe { int a; float b; };\n"),
    },
    "csharp": {
        "class_declaration": ("a.cs", "class Probe { }\n"),
        "constructor_declaration": ("a.cs", "class Holder { public Holder() { } }\n"),
        "conversion_operator_declaration": (
            "a.cs",
            "class Holder { public static explicit operator string(Holder h) => \"\"; }\n",
        ),
        "delegate_declaration": ("a.cs", "delegate void Probe(int a);\n"),
        "destructor_declaration": ("a.cs", "class Holder { ~Holder() { } }\n"),
        "enum_declaration": ("a.cs", "enum Probe { A, B }\n"),
        "event_declaration": (
            "a.cs",
            "class Holder { public event System.EventHandler Probe { add { } remove { } } }\n",
        ),
        "event_field_declaration": (
            "a.cs",
            "class Holder { public event System.EventHandler Probe; }\n",
        ),
        "field_declaration": ("a.cs", "class Holder { private int probe; }\n"),
        "indexer_declaration": (
            "a.cs",
            "class Holder { public int this[int i] => i; }\n",
        ),
        "interface_declaration": ("a.cs", "interface IProbe { }\n"),
        "method_declaration": ("a.cs", "class Holder { void Probe() { } }\n"),
        "operator_declaration": (
            "a.cs",
            "class Holder { public static Holder operator +(Holder a, Holder b) => a; }\n",
        ),
        "property_declaration": ("a.cs", "class Holder { public int Probe { get; set; } }\n"),
        "record_declaration": ("a.cs", "record Probe(int A);\n"),
        "struct_declaration": ("a.cs", "struct Probe { }\n"),
    },
    "dart": {
        "class_definition": ("a.dart", "class Probe {}\n"),
        "enum_declaration": ("a.dart", "enum Probe { a, b }\n"),
        "extension_declaration": ("a.dart", "extension Probe on String {}\n"),
        "extension_type_declaration": ("a.dart", "extension type Probe(int v) {}\n"),
        "function_signature": ("a.dart", "void probe() {}\n"),
        "method_signature": ("a.dart", "class Holder { void probe() {} }\n"),
        "mixin_declaration": ("a.dart", "mixin Probe {}\n"),
        "type_alias": ("a.dart", "typedef Probe = int Function(int);\n"),
    },
    "gdscript": {
        "class_definition": ("a.gd", "class Probe:\n\tvar x = 1\n"),
        "enum_definition": ("a.gd", "enum Probe { A, B }\n"),
        "function_definition": ("a.gd", "func probe():\n\tpass\n"),
        "signal_statement": ("a.gd", "signal probe\n"),
    },
    "gleam": {
        "constant": ("a.gleam", "const probe = 1\n"),
        "function": ("a.gleam", "fn probe() {\n  1\n}\n"),
        "type_alias": ("a.gleam", "type Probe = String\n"),
        "type_definition": ("a.gleam", "type Probe {\n  Probe(name: String)\n}\n"),
    },
    "go": {
        "function_declaration": ("a.go", "package p\n\nfunc Probe() {}\n"),
        "method_declaration": ("a.go", "package p\n\ntype H struct{}\n\nfunc (h H) Probe() {}\n"),
        # ⚠ The SPEC since #817: one `type_declaration` wraps every spec of a
        # grouped `type ( ... )`, and a row here binds ONE name, so this table
        # could not have seen the block form for any language. The N-binding
        # property is asserted per spelling in
        # `tests/test_a_grouped_go_type_block_binds_every_name.py`.
        "type_spec": ("a.go", "package p\n\ntype Probe struct{}\n"),
    },
    "haskell": {
        "bind": ("a.hs", "probe :: Int\nprobe = 1\n"),
        "class": ("a.hs", "class Probe a where\n  probe :: a -> Int\n"),
        "instance": ("a.hs", "data T = T\ninstance Probe T where\n  probe _ = 1\n"),
        "data_type": ("a.hs", "data Probe = Probe Int\n"),
        "function": ("a.hs", "probe :: Int -> Int\nprobe x = x\n"),
        "newtype": ("a.hs", "newtype Probe = Probe Int\n"),
        "type_synomym": ("a.hs", "type Probe = Int\n"),
    },
    "java": {
        "annotation_type_declaration": ("A.java", "public @interface Probe { }\n"),
        "annotation_type_element_declaration": (
            "A.java",
            "public @interface Holder { String probe(); }\n",
        ),
        "class_declaration": ("A.java", "class Probe { }\n"),
        "compact_constructor_declaration": (
            "A.java",
            "record Holder(int a) {\n  Holder {\n  }\n}\n",
        ),
        "constructor_declaration": ("A.java", "class Holder { Holder() { } }\n"),
        "enum_declaration": ("A.java", "enum Probe { A, B }\n"),
        "interface_declaration": ("A.java", "interface Probe { }\n"),
        "method_declaration": ("A.java", "class Holder { void probe() { } }\n"),
        "record_declaration": ("A.java", "record Probe(int a) { }\n"),
    },
    "javascript": {
        "class_declaration": ("a.js", "class Probe {}\n"),
        "function_declaration": ("a.js", "function probe() {}\n"),
        "generator_function_declaration": ("a.js", "function* probe() {}\n"),
        "method_definition": ("a.js", "class Holder { probe() {} }\n"),
    },
    "kotlin": {
        "class_declaration": ("a.kt", "class Probe\n"),
        "function_declaration": ("a.kt", "fun probe() {}\n"),
        "object_declaration": ("a.kt", "object Probe\n"),
        "property_declaration": ("a.kt", "class Holder {\n    val probe = 1\n}\n"),
        "type_alias": ("a.kt", "typealias Probe = String\n"),
    },
    "perl": {
        "package_statement": ("a.pl", "package Probe;\n"),
        "subroutine_declaration_statement": ("a.pl", "sub probe {\n    return 1;\n}\n"),
    },
    "php": {
        "class_declaration": ("a.php", "<?php\nclass Probe { }\n"),
        "enum_declaration": ("a.php", "<?php\nenum Probe { case A; }\n"),
        "function_definition": ("a.php", "<?php\nfunction probe() { }\n"),
        "interface_declaration": ("a.php", "<?php\ninterface Probe { }\n"),
        "method_declaration": ("a.php", "<?php\nclass Holder { public function probe() { } }\n"),
        "trait_declaration": ("a.php", "<?php\ntrait Probe { }\n"),
    },
    "python": {
        "class_definition": ("a.py", "class Probe:\n    pass\n"),
        "function_definition": ("a.py", "def probe():\n    pass\n"),
        "type_alias_statement": ("a.py", "type Probe = int\n"),
    },
    "ruby": {
        "class": ("a.rb", "class Probe\nend\n"),
        "method": ("a.rb", "def probe\nend\n"),
        "module": ("a.rb", "module Probe\nend\n"),
        "singleton_method": ("a.rb", "def self.probe\nend\n"),
    },
    "rust": {
        "associated_type": ("a.rs", "trait Holder {\n    type Probe;\n}\n"),
        "enum_item": ("a.rs", "enum Probe { A, B }\n"),
        "function_item": ("a.rs", "fn probe() {}\n"),
        "function_signature_item": ("a.rs", "trait Holder {\n    fn probe(&self);\n}\n"),
        "struct_item": ("a.rs", "struct Probe { a: u8 }\n"),
        "trait_item": ("a.rs", "trait Probe {}\n"),
        "type_item": ("a.rs", "type Probe = u8;\n"),
        "union_item": ("a.rs", "union Probe { a: u8 }\n"),
    },
    "scala": {
        "class_definition": ("a.scala", "class Probe\n"),
        "enum_definition": ("a.scala", "enum Probe:\n  case A\n"),
        "function_declaration": ("a.scala", "trait Holder:\n  def probe: Int\n"),
        "function_definition": ("a.scala", "def probe: Int = 1\n"),
        "given_definition": ("a.scala", "given probe: Int = 1\n"),
        "object_definition": ("a.scala", "object Probe\n"),
        "trait_definition": ("a.scala", "trait Probe\n"),
        "type_definition": ("a.scala", "type Probe = String\n"),
        "val_definition": ("a.scala", "val probe = 1\n"),
        # ⚠ IN A CLASS, unlike `val_definition` above, and the difference is the
        # declared kind. `var_definition` is a `field`, and a field asserts
        # membership of a type -- at module scope the same declaration is a
        # `variable` (#769/#787). A file-scope sample would test the spec's row
        # against a kind that row does not claim there.
        "var_definition": ("a.scala", "class Holder {\n  var probe = 1\n}\n"),
    },
    "swift": {
        "class_declaration": ("a.swift", "class Probe {}\n"),
        "deinit_declaration": ("a.swift", "class Holder {\n    deinit {}\n}\n"),
        "function_declaration": ("a.swift", "func probe() {}\n"),
        "init_declaration": ("a.swift", "class Holder {\n    init() {}\n}\n"),
        "property_declaration": ("a.swift", "class Holder {\n    var probe = 1\n}\n"),
        "protocol_declaration": ("a.swift", "protocol Probe {}\n"),
        "protocol_function_declaration": ("a.swift", "protocol Holder {\n    func probe()\n}\n"),
        "protocol_property_declaration": ("a.swift", "protocol Holder {\n    var probe: Int { get }\n}\n"),
        "subscript_declaration": ("a.swift", "struct Holder {\n    subscript(i: Int) -> Int { return i }\n}\n"),
        "typealias_declaration": ("a.swift", "typealias Probe = String\n"),
    },
    "tsx": {
        "abstract_class_declaration": ("a.tsx", "abstract class Probe {}\n"),
        "abstract_method_signature": (
            "a.tsx",
            "abstract class Holder {\n  abstract probe(): void;\n}\n",
        ),
        "class_declaration": ("a.tsx", "class Probe {}\n"),
        "enum_declaration": ("a.tsx", "enum Probe { A }\n"),
        "function_declaration": ("a.tsx", "function probe() {}\n"),
        "generator_function_declaration": ("a.tsx", "function* probe() {}\n"),
        "interface_declaration": ("a.tsx", "interface Probe { a: number }\n"),
        "method_definition": ("a.tsx", "class Holder { probe() {} }\n"),
        "type_alias_declaration": ("a.tsx", "type Probe = string;\n"),
    },
    "typescript": {
        "abstract_class_declaration": ("a.ts", "abstract class Probe {}\n"),
        "abstract_method_signature": (
            "a.ts",
            "abstract class Holder {\n  abstract probe(): void;\n}\n",
        ),
        "class_declaration": ("a.ts", "class Probe {}\n"),
        "enum_declaration": ("a.ts", "enum Probe { A }\n"),
        "function_declaration": ("a.ts", "function probe() {}\n"),
        "generator_function_declaration": ("a.ts", "function* probe() {}\n"),
        "interface_declaration": ("a.ts", "interface Probe { a: number }\n"),
        "method_definition": ("a.ts", "class Holder { probe() {} }\n"),
        "type_alias_declaration": ("a.ts", "type Probe = string;\n"),
    },
}

#: Declared forms that do NOT extract, each with the issue that tracks it.
#:
#: ⚠⚠ A TRACKED gap, never a tolerated one, and every entry FAILS WHEN FIXED —
#: `test_a_known_gap_is_still_a_gap` asserts the form still yields nothing, so
#: an entry cannot outlive the defect it names. That is the sibling #724's
#: `_CONFIRMED_GAPS` has and #712's `_KNOWN_GAPS` has; an excuse list without
#: it is the record that survives its own repair.
_KNOWN_GAPS: dict[str, dict[str, str]] = {
    # #722 (the whole Haskell spec, which extracted nothing) left by the usual
    # route: the forms extract, this file's guard said so, the entry went.
    # ⚠⚠ #743 was the motivating instance and its entry is GONE, but NOT by the
    # usual route. `test_a_known_gap_is_still_a_gap` fails when a gap closes
    # while the form stays declared; this form stopped being DECLARED at all --
    # #744 moved `php.property_declaration` out of `symbol_node_types` into
    # `field_patterns`, so it left `DECLARED_FORMS` and the partition check is
    # what named it. Both exits are correct and they fail in different tests,
    # which is worth knowing before reading either failure.
    # #754 left by the usual route too: found by THIS file on its first run
    # (the grammar gives `deinit_declaration` no identifier child), fixed by
    # building the name `deinit`, and this guard said so.
}

#: Forms the grammar can only spell INSIDE a container, where `_walk_tree`
#: promotes `function` to `method` and attributes it to the owner.
#:
#: ⚠⚠ **An allowance, so it is asserted in the STRICT direction**: the row below
#: requires the PROMOTED kind, not "declared or promoted". A form that started
#: extracting under its declared kind fails here and the entry is deleted --
#: otherwise this table would quietly widen into "any kind will do", which is
#: the one thing this file must not become.
#:
#: ⚠ Each entry's declared kind must be `function`;
#: `test_every_promotion_entry_is_a_function_form` refuses anything else, so an
#: entry cannot be used to excuse a genuinely wrong kind.
_PROMOTED_IN_A_CONTAINER = {
    ("arduino", "field_declaration"): "a member function prototype is a field_declaration (#755)",
    ("cpp", "field_declaration"): "a member function prototype is a field_declaration (#755)",
    ("rust", "function_signature_item"): "a bodiless fn exists only inside a trait",
    ("scala", "function_declaration"): "a bodiless def exists only inside a trait or abstract class",
    ("swift", "protocol_function_declaration"): "a protocol's method requirement exists only inside the protocol (#733)",
    ("swift", "subscript_declaration"): "a subscript exists only inside a type (#733)",
}


def _declared_forms() -> list[tuple[str, str, str]]:
    """Every `(language, node_type, kind)` a spec advertises."""
    out = []
    for language, spec in sorted(LANGUAGE_REGISTRY.items()):
        for node_type, kind in sorted((getattr(spec, "symbol_node_types", None) or {}).items()):
            out.append((language, node_type, kind))
    return out


DECLARED_FORMS = _declared_forms()

#: The forms the extraction check runs over: every declared form MINUS the
#: tracked gaps, which `test_a_known_gap_is_still_a_gap` owns instead.
#:
#: ⚠⚠ **Excluded from the parametrization rather than skipped inside it, and
#: that is a Floor decision, not a style one.** Seven `pytest.skip`s would take
#: `ci.skips_windows` from 24 to 31 against a ceiling of 25 -- a tracked gap
#: would be spending the suite's skip budget to say something a passing test
#: already says. ⚠ The project's own rule is to READ the skip count rather than
#: the exit code (the 105-tests-never-ran near-miss), so a file that raises it
#: by seven for bookkeeping is working against the instrument.
CHECKED_FORMS = [
    (language, node_type, kind)
    for language, node_type, kind in DECLARED_FORMS
    if node_type not in _KNOWN_GAPS.get(language, {})
]


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """`parse_file` consults the config gate before parsing anything.

    ⚠⚠ Patch `jcodemunch_mcp.config`, never the parser module: the parser's
    import of the gate is function-local, so patching its namespace resolves
    nothing (the `cli/policy.py` trap). This box disables several languages,
    and without this every one of them would report a spec defect that is
    really a developer config.
    """
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _expected_pairs(pairs, kind):
    """The pairs of one kind, for a failure message that names them."""
    return sorted(name for name, k in pairs if k == kind)


def _pairs_extracted(language: str, node_type: str) -> set[tuple[str, str]]:
    filename, source = _SAMPLES[language][node_type]
    return {(s.name, s.kind) for s in parse_file(source, filename, language)}


def _kinds_extracted(language: str, node_type: str) -> set[str]:
    return {kind for _name, kind in _pairs_extracted(language, node_type)}


def _pairs_without_the_form(language: str, node_type: str) -> set[tuple[str, str]]:
    """The same sample, parsed with this node type REMOVED from the spec.

    ⚠⚠ **This is what makes a row assert something about the form it names.**
    A sample needs a container to be legal source -- a Rust associated type
    needs its `trait`, a C# indexer needs its `class` -- and the container is
    itself a declared form, so a row that asserts only "the expected kind
    appears somewhere" can be satisfied by the WRAPPER. `rust.associated_type`
    was exactly that: `trait_item` is also mapped to `type`, so deleting
    `associated_type` from the spec left the row green. Found in review.

    Removing the entry and re-parsing answers the question directly: what does
    this declaration contribute that nothing else does?
    """
    import dataclasses

    import jcodemunch_mcp.parser.extractor as extractor

    spec = LANGUAGE_REGISTRY[language]
    without = dataclasses.replace(
        spec,
        symbol_node_types={
            nt: k for nt, k in spec.symbol_node_types.items() if nt != node_type
        },
    )
    filename, source = _SAMPLES[language][node_type]
    original = extractor.LANGUAGE_REGISTRY
    extractor.LANGUAGE_REGISTRY = {**LANGUAGE_REGISTRY, language: without}
    try:
        return {(s.name, s.kind) for s in parse_file(source, filename, language)}
    finally:
        extractor.LANGUAGE_REGISTRY = original


def test_every_declared_node_type_has_a_sample():
    """A form nobody exercises is a form nobody has checked.

    ⚠⚠ This is what stops the table falling behind the specs. The alternative —
    iterating the samples instead of the declarations — passes by DELETION: drop
    a sample and its form stops being checked, in silence. Same direction as
    #724's inventory gate, which is why both halves are asserted.
    """
    missing = [
        f"{language}.{node_type}"
        for language, node_type, _kind in DECLARED_FORMS
        if node_type not in _SAMPLES.get(language, {})
        and node_type not in _KNOWN_GAPS.get(language, {})
    ]
    assert not missing, (
        f"{len(missing)} declared node type(s) have no sample and no tracked "
        f"gap: {missing}. Declaring a form obliges two lines of sample source "
        f"here -- without one, nothing proves the declaration produces a symbol "
        f"(#745)."
    )


def test_no_sample_describes_a_form_that_is_not_declared():
    """The mirror of the sample obligation, and the direction nothing asked.

    ⚠⚠ The file had a one-way check: a declared form with no sample fails,
    a sample for a form nothing declares sat green forever. That is how
    `php.property_declaration`'s sample survived #744 moving the form out of
    `symbol_node_types` into `field_patterns` -- the row read as evidence for a
    declaration that no longer existed, which is the record-outlives-its-subject
    shape this whole file is about, one direction over.

    ⚠ A sample is not wasted work when it goes stale -- the PHP one moved to
    `tests/test_inventory_reads_every_channel.py`, where the form is now
    recognised. The rule is that it lives where the form is declared, not in
    both places.
    """
    declared = {(lang, nt) for lang, nt, _k in DECLARED_FORMS}
    stale = sorted(
        f"{language}.{node_type}"
        for language, samples in _SAMPLES.items()
        for node_type in samples
        if (language, node_type) not in declared
    )
    assert not stale, (
        f"{stale} have samples here and are declared in no spec's "
        f"`symbol_node_types`. If the form moved to another channel, its "
        f"sample belongs in that channel's file; if the declaration was "
        f"dropped, so is the sample."
    )


def test_the_two_lists_partition_every_declared_form():
    """Checked + tracked = declared, with nothing in both and nothing in neither.

    ⚠⚠ The exclusion above is what keeps the skip count flat, and it is also
    the way this file could go quietly blind: a form moved into `_KNOWN_GAPS`
    stops being checked, so a bad entry buys silence rather than a failure.
    Both halves are asserted here, and `test_a_known_gap_is_still_a_gap` fails
    the moment such an entry stops describing a real gap.
    """
    declared = {(lang, nt) for lang, nt, _k in DECLARED_FORMS}
    checked = {(lang, nt) for lang, nt, _k in CHECKED_FORMS}
    tracked = {(lang, nt) for lang, gaps in _KNOWN_GAPS.items() for nt in gaps}

    assert tracked <= declared, sorted(tracked - declared)
    assert not (checked & tracked), sorted(checked & tracked)
    assert checked | tracked == declared, sorted(declared - (checked | tracked))


@pytest.mark.parametrize(
    "language,node_type,kind",
    CHECKED_FORMS,
    ids=[f"{lang}.{nt}" for lang, nt, _k in CHECKED_FORMS],
)
def test_a_declared_node_type_extracts_its_kind(language, node_type, kind):
    """The property: what a spec advertises, the product emits -- FROM THIS FORM.

    Two halves, and the second is what makes the first mean anything:

    1. a symbol of the declared kind comes out of the sample;
    2. it stops coming out when this node type is removed from the spec.

    ⚠⚠ **Half 2 was missing and one row was hollow because of it.** A sample
    needs a container to be legal source, containers are declared forms too, and
    `rust.associated_type`'s container (`trait_item`) carries the SAME kind --
    so the row passed with `associated_type` deleted from the spec entirely. The
    deletion is the assertion now, on every row, which also means a future
    sample cannot be written carelessly enough to reintroduce the hole. Found in
    review; #745's own defect class, inside the file written to find it.
    """
    if node_type not in _SAMPLES.get(language, {}):
        pytest.fail(
            f"{language}.{node_type} has no sample; "
            f"test_every_declared_node_type_has_a_sample names them all at once"
        )

    expected = "method" if (language, node_type) in _PROMOTED_IN_A_CONTAINER else kind
    with_form = _pairs_extracted(language, node_type)
    kinds = {k for _n, k in with_form}
    assert expected in kinds, (
        f"{language} declares symbol_node_types[{node_type!r}] = {kind!r} and "
        f"its sample yields {sorted(kinds) or 'NOTHING'}. The declaration is "
        f"advertised and unreachable -- #712's shape one indirection down "
        f"(#745)."
    )

    contributed = with_form - _pairs_without_the_form(language, node_type)
    assert any(k == expected for _n, k in contributed), (
        f"{language}.{node_type} is declared {kind!r} and its sample still "
        f"yields {sorted(_expected_pairs(with_form, expected))} with the entry "
        f"REMOVED from the spec, so this row proves nothing about the form it "
        f"names -- the container is supplying the kind. Narrow the sample, or "
        f"name the form that is really under test."
    )


@pytest.mark.parametrize(
    "language,node_type",
    sorted((lang, nt) for lang, gaps in _KNOWN_GAPS.items() for nt in gaps),
)
def test_a_known_gap_is_still_a_gap(language, node_type):
    """⚠⚠ FAILS when the gap closes, which is the whole point of the entry.

    A record that outlives its defect is #724's shape: the list says the form
    is broken while the form works, and the next reader trusts the list. When
    this fails, DELETE the entry and add a sample -- never adjust it.

    ⚠⚠ Asks what the form CONTRIBUTES, for the mirror of the reason the row
    test does. "The declared kind is absent" would be wrong in the other
    direction: a broken form inside a container of the same kind could never be
    recorded as a gap at all, because the container's symbol would make this
    test fail on a genuine defect.
    """
    sample = _SAMPLES.get(language, {}).get(node_type)
    assert sample is not None, (
        f"{language}.{node_type} is a tracked gap with no sample, so nothing "
        f"can tell whether it is still broken. Every gap entry carries the "
        f"source that fails."
    )
    contributed = _pairs_extracted(language, node_type) - _pairs_without_the_form(
        language, node_type
    )
    assert not contributed, (
        f"{language}.{node_type} now contributes {sorted(contributed)}: the gap "
        f"is FIXED. Delete its `_KNOWN_GAPS` entry -- the sample is already "
        f"here ({_KNOWN_GAPS[language][node_type]})."
    )


def test_every_promotion_entry_is_a_function_form():
    """The promotion allowance may only cover `function` becoming `method`.

    ⚠⚠ Without this, an entry here could excuse a form extracting under any
    kind at all -- an exception list turning into an escape hatch, which is what
    `test_every_exception_has_a_real_special_case` was written for one file
    over.
    """
    for (language, node_type), reason in sorted(_PROMOTED_IN_A_CONTAINER.items()):
        declared = LANGUAGE_REGISTRY[language].symbol_node_types[node_type]
        assert declared == "function", (
            f"{language}.{node_type} is declared {declared!r}, not 'function', so "
            f"the container promotion cannot explain it ({reason})"
        )


def test_the_container_promotion_is_real():
    """The allowance describes `_walk_tree`, not a convenient belief.

    ⚠ ONE node type, TWO positions: Ruby's `method` extracts as a `function` at
    top level and as a `method` inside a class. If that stopped being true the
    promotion entries above would be covering something else, and every one of
    them would keep passing on the wrong grounds.
    """
    top = {(s.name, s.kind) for s in parse_file("def probe\nend\n", "a.rb", "ruby")}
    nested = {
        (s.name, s.kind)
        for s in parse_file("class H\n  def probe\n  end\nend\n", "a.rb", "ruby")
    }
    assert ("probe", "function") in top
    assert ("probe", "method") in nested


def test_the_check_fires_on_a_name_field_the_grammar_never_sets():
    """Non-vacuity, and it plants #743's defect on a language that WORKS.

    ⚠⚠ The whole file is worth nothing if this check cannot fail, and "it
    reports 3 known gaps today" does not prove that -- those could be the only
    three cases it is capable of seeing. So Python's `function_definition` is
    re-pointed at a field the grammar does not set, which is exactly PHP's
    shape, and the extraction must go from working to nothing.
    """
    import dataclasses

    spec = LANGUAGE_REGISTRY["python"]
    before = {s.kind for s in parse_file("def probe():\n    pass\n", "a.py", "python")}
    assert "function" in before, before

    broken = dataclasses.replace(
        spec, name_fields={**spec.name_fields, "function_definition": "no_such_field"}
    )
    registry_patch = {**LANGUAGE_REGISTRY, "python": broken}

    import jcodemunch_mcp.parser.extractor as extractor

    original = extractor.LANGUAGE_REGISTRY
    extractor.LANGUAGE_REGISTRY = registry_patch
    try:
        after = {s.kind for s in parse_file("def probe():\n    pass\n", "a.py", "python")}
    finally:
        extractor.LANGUAGE_REGISTRY = original

    assert "function" not in after, (
        "re-pointing name_fields at a field the grammar never sets still "
        "extracted the function, so this file cannot see #743's defect class"
    )
