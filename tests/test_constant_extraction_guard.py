"""Every language that declares a constant surface must actually extract one (#428).

The defect this guards is not any single missing branch. It is that a declared
pattern with no implementation is **indistinguishable from a language that has no
constants**: `constant_patterns` looks like coverage, `search_symbols` returns
nothing, and the caller concludes the symbol does not exist. @mussonking lost a
day to it on a Rust file holding 935 constants, and only caught it because he
knew the exact count.

So membership in `constant_patterns` predicts nothing in either direction today,
which is the complaint restated as a mechanism. This file makes the claim
enforceable: declare the pattern, extract the constant, or be exempt BY NAME.

⚠ Config is isolated deliberately. `parse_file` consults `is_language_enabled`,
so without the fixture this suite reports the developer's `~/.code-index/
config.jsonc` rather than the parser. A local run with `arduino` disabled shows
it extracting zero symbols and looks exactly like a real defect -- the same
failure mode as #411, where a test read the developer's real config and the
large-file guard was broken by using the large-file escape hatch.
"""
import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY

# One minimal sample per language that declares `constant_patterns`. Each holds
# a constant AND an ordinary function, so a sample that stops parsing entirely is
# distinguishable from one whose constants alone are missing.
SAMPLES: dict[str, tuple[str, str]] = {
    "arduino": ("probe.ino", "#define TOP_LEVEL 1\nvoid setup() {}\n"),
    "bash": ("probe.sh", 'readonly TOP_LEVEL="a"\nvisible() { :; }\n'),
    "c": ("probe.c", "#define TOP_LEVEL 1\nvoid visible(void) {}\n"),
    "cpp": ("probe.cpp", "#define TOP_LEVEL 1\nvoid visible() {}\n"),
    "gdscript": ("probe.gd", "const TOP_LEVEL := 1\nfunc visible():\n\tpass\n"),
    "gleam": ("probe.gleam", "const top_level = 1\npub fn visible() { 1 }\n"),
    "go": ("probe.go", 'package p\n\nconst TOP_LEVEL = "a"\n\nfunc Visible() {}\n'),
    "java": ("Probe.java", "class Probe { static final int TOP_LEVEL = 1; void visible() {} }\n"),
    "javascript": ("probe.js", 'const TOP_LEVEL = "a";\nfunction visible() {}\n'),
    "kotlin": ("probe.kt", 'const val TOP_LEVEL: String = "a"\nfun visible() {}\n'),
    "perl": ("probe.pl", "use constant TOP_LEVEL => 1;\nsub visible { }\n"),
    "php": ("probe.php", "<?php\nconst TOP_LEVEL = 'a';\nfunction visible() {}\n"),
    "python": ("probe.py", 'TOP_LEVEL = "a"\n\n\ndef visible():\n    pass\n'),
    "rust": (
        "probe.rs",
        'const TOP_LEVEL: &str = "a";\nstatic ALSO_THIS: &str = "b";\npub fn visible() {}\n',
    ),
    "scala": ("probe.scala", 'object O { val TOP_LEVEL = "a"\n  def visible() = 1 }\n'),
    "tsx": ("probe.tsx", 'const TOP_LEVEL = "a";\nfunction visible() {}\n'),
    "typescript": ("probe.ts", 'const TOP_LEVEL = "a";\nfunction visible() {}\n'),
}

# Known-broken, each named individually with its reason. Never a skipped
# directory and never a category -- an exemption that covers a class hides the
# next member of it.
#
# EMPTY, and that is the end state this ratchet was built to reach. The four
# entries here (rust, go, java, php) were @mussonking's half of #428; they were
# implemented on 2026-08-15 and `test_exemptions_are_not_stale` below failed
# until they were deleted, which is the mechanism working rather than a chore.
#
# ⚠ An empty set means `test_exemptions_are_not_stale` parametrizes over nothing
# and pytest reports it as a SKIP ("got empty parameter set"). That skip is the
# ratchet at rest, not a lost test -- it re-arms the moment anyone adds an
# exemption. Same shape as `_JS_VARIANT_EXEMPT` in the v1.108.273 sweep.
EXEMPT: dict[str, str] = {}

DECLARING = sorted(
    lang
    for lang, spec in LANGUAGE_REGISTRY.items()
    if getattr(spec, "constant_patterns", None)
)


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """Answer the parser, not the developer's config file."""
    monkeypatch.setattr(
        "jcodemunch_mcp.config.is_language_enabled",
        lambda language, repo=None: True,
    )


def _constants(language: str) -> list:
    filename, source = SAMPLES[language]
    return [s for s in parse_file(source, filename, language) if s.kind == "constant"]


def test_every_declaring_language_has_a_sample():
    """A language added to `constant_patterns` cannot skip this guard by omission."""
    missing = sorted(set(DECLARING) - set(SAMPLES))
    assert not missing, (
        f"{missing} declare constant_patterns but have no sample here. Add one; "
        "an absent sample is an unmeasured claim, which is the defect #428 is about."
    )


def test_no_sample_for_a_language_that_stopped_declaring():
    """Drop the sample when the claim goes away, so the file cannot rot."""
    stale = sorted(set(SAMPLES) - set(DECLARING))
    assert not stale, f"{stale} no longer declare constant_patterns; remove their samples."


@pytest.mark.parametrize("language", [lang for lang in DECLARING if lang not in EXEMPT])
def test_declared_constant_pattern_extracts_a_constant(language):
    found = _constants(language)
    assert found, (
        f"{language} declares constant_patterns="
        f"{LANGUAGE_REGISTRY[language].constant_patterns} but extracted no constant. "
        "A declared-and-unimplemented pattern is indistinguishable from a language "
        "with no constants, which is exactly how #428 reached a user."
    )


@pytest.mark.parametrize("language", sorted(EXEMPT))
def test_exemptions_are_not_stale(language):
    """The ratchet. Fixing a language must delete its exemption in the same change."""
    found = _constants(language)
    assert not found, (
        f"{language} now extracts {[s.name for s in found]}. Remove it from EXEMPT "
        "in this file -- an exemption outliving its defect is how a ratchet rots into "
        "a permanent allowance."
    )


def test_the_guard_can_fail():
    """A guard nobody can see fail is one nobody believes (the .264 lesson).

    Feed a declaring language a sample with no constant in it and confirm the
    measurement returns empty, so a green run above means the samples were read
    rather than the assertion being vacuous.
    """
    symbols = parse_file("def visible():\n    pass\n", "empty.py", "python")
    assert not [s for s in symbols if s.kind == "constant"]
    assert [s for s in symbols if s.kind == "function"], "sample must still parse"


# --- the two fixed here, pinned against their specific failure shapes ---


def test_kotlin_const_val_is_reachable():
    """Regression for an unreachable branch that looked like coverage.

    Kotlin's `property_declaration` routed into a branch written against Swift's
    grammar, which requires a `value_binding_pattern` child with a `mutability`
    field. Kotlin spells it `binding_pattern_kind > val`, so the branch returned
    None on every possible input while reading as handled.
    """
    found = _constants("kotlin")
    assert [s.name for s in found] == ["TOP_LEVEL"]


def test_kotlin_plain_val_needs_a_constant_shaped_name():
    """`const val` is a constant by declaration; a bare `val` is just immutable."""
    source = "val MAX_SPEED = 100\nval userName = \"jjg\"\nvar counter = 0\n"
    found = [s for s in parse_file(source, "p.kt", "kotlin") if s.kind == "constant"]
    assert [s.name for s in found] == ["MAX_SPEED"]


def test_bash_readonly_binds_several_names_in_one_node():
    """`Optional[Symbol]` cannot express this, which is why the plural path exists.

    One `declaration_command` carries N `variable_assignment` children. Returning
    at most one symbol per node would silently keep the first and drop the rest --
    the same shape as the 935-constant collision @mussonking flagged, and worse
    than extracting none because it looks complete.
    """
    source = 'readonly FIRST="x" SECOND="y"\ndeclare -r THIRD="z"\n'
    found = [s for s in parse_file(source, "p.sh", "bash") if s.kind == "constant"]
    assert [s.name for s in found] == ["FIRST", "SECOND", "THIRD"]


def test_bash_mutable_declarations_are_not_constants():
    """The declaration is the evidence. `local` and a bare `declare` are variables."""
    source = 'local scoped=1\ndeclare plain=2\nPLAIN=3\n'
    found = [s for s in parse_file(source, "p.sh", "bash") if s.kind == "constant"]
    assert not found
