"""One class, four kinds of member, every class-bearing language, one table.

"Members of kind X are wrong or missing in language Y" arrived as #733, #735,
#743, #755, #759, #769 and #770: one defect class, found one language per fix,
each time by a reviewer looking at something else. This file enumerates it.

Each sample is one class holding up to four ROLES:

    method     a callable member
    mutable    state that can be reassigned
    immutable  state that cannot
    property   an accessor the language distinguishes from plain state

`_TABLE` pins what `parse_file` answers for every (language, role) cell: the
kind, or ABSENT, plus whether the member is OWNED by the class. `_RULE` says
what a right answer is. Every cell that breaks the rule must be in `_GAPS` with
the issue that tracks it, and every `_GAPS` entry must still be broken -- so a
parser fix FAILS this file until the gap entry and the table cell are updated,
which is the notification that a gap closed.

⚠ The roles are about what the LANGUAGE says, never what the parser can see.
A role is omitted only where the language has no such concept (Go has no
property; a JS class field is always reassignable, so JS has no immutable
role), never because the answer is inconvenient. Two labels are looser than
they read and are graded correctly anyway: Python's `LIMIT` is immutable by
the language's documented naming convention only, and a Rust struct field is
mutable only through a `mut` binding.

⚠ What this file CANNOT see, stated: `immutable` accepts every state kind, so a
language that collapses `val` and `var` into one kind grades clean (Kotlin does,
deliberately, #732). And a symbol's `line` is not pinned: a wrong span moves
nothing here.
"""

import re
from collections import Counter

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.symbols import STATE_KINDS

ABSENT = "ABSENT"

#: Ownership. `parent` is what consumers filter on (the file summary's member
#: count, #760), so a member that carries the class only in its qualified name
#: is NOT owned: it is invisible to every parent-keyed reader.
OWNED = "owned"          # parent is the class's id
QUALIFIED = "qualified"  # parent is None, qualified name starts with the class
NO_OWNER = "none"        # neither, or the member is ABSENT

#: What a right answer is. `constant` for a member the language lets you
#: reassign is #741's lesson (a JS `let` is not a constant) in member position.
_RULE: dict[str, frozenset[str]] = {
    "method": frozenset({"method"}),
    "mutable": frozenset(STATE_KINDS) - {"constant"},
    "immutable": frozenset(STATE_KINDS),
    # `method` because a getter IS a method to Python, JS and Dart. Not `field`:
    # where the language declares a property distinctly (C#, Apex, Swift), `field`
    # is the answer #770 rejects, and accepting it would close the cell wrongly.
    "property": frozenset({"property", "method"}),
}

#: language -> (filename, class name, {role: member name}, source)
_SAMPLES: dict[str, tuple[str, str, dict[str, str], str]] = {
    "python": ("a.py", "Audit", {
        "method": "run_it", "mutable": "tally", "immutable": "LIMIT", "property": "view",
    }, (
        "class Audit:\n"
        "    LIMIT = 3\n"
        "    tally: int = 0\n"
        "    def run_it(self):\n"
        "        return 1\n"
        "    @property\n"
        "    def view(self):\n"
        "        return 2\n"
    )),
    "javascript": ("a.js", "Audit", {
        "method": "runIt", "mutable": "tally", "property": "view",
    }, (
        "class Audit {\n"
        "  tally = 0;\n"
        "  runIt() { return 1; }\n"
        "  get view() { return 2; }\n"
        "}\n"
    )),
    "typescript": ("a.ts", "Audit", {
        "method": "runIt", "mutable": "tally", "immutable": "limit", "property": "view",
    }, (
        "class Audit {\n"
        "  readonly limit: number = 3;\n"
        "  tally: number = 0;\n"
        "  runIt(): number { return 1; }\n"
        "  get view(): number { return 2; }\n"
        "}\n"
    )),
    "tsx": ("a.tsx", "Audit", {
        "method": "runIt", "mutable": "tally", "immutable": "limit", "property": "view",
    }, (
        "class Audit {\n"
        "  readonly limit: number = 3;\n"
        "  tally: number = 0;\n"
        "  runIt(): number { return 1; }\n"
        "  get view(): number { return 2; }\n"
        "}\n"
    )),
    "java": ("Audit.java", "Audit", {
        "method": "runIt", "mutable": "tally", "immutable": "LIMIT",
    }, (
        "class Audit {\n"
        "    static final int LIMIT = 3;\n"
        "    int tally = 0;\n"
        "    int runIt() { return 1; }\n"
        "}\n"
    )),
    "csharp": ("Audit.cs", "Audit", {
        "method": "RunIt", "mutable": "tally", "immutable": "Limit", "property": "View",
    }, (
        "class Audit {\n"
        "    const int Limit = 3;\n"
        "    private int tally;\n"
        "    public int View { get; set; }\n"
        "    int RunIt() { return 1; }\n"
        "}\n"
    )),
    "cpp": ("a.cpp", "Audit", {
        "method": "runIt", "mutable": "tally", "immutable": "limit",
    }, (
        "class Audit {\n"
        "public:\n"
        "    const int limit = 3;\n"
        "    int tally = 0;\n"
        "    int runIt() { return 1; }\n"
        "};\n"
    )),
    "arduino": ("a.ino", "Audit", {
        "method": "runIt", "mutable": "tally", "immutable": "limit",
    }, (
        "class Audit {\n"
        "public:\n"
        "    const int limit = 3;\n"
        "    int tally = 0;\n"
        "    int runIt() { return 1; }\n"
        "};\n"
    )),
    "php": ("a.php", "Audit", {
        "method": "runIt", "mutable": "tally", "immutable": "LIMIT",
    }, (
        "<?php\n"
        "class Audit {\n"
        "    const LIMIT = 3;\n"
        "    public $tally = 0;\n"
        "    function runIt() { return 1; }\n"
        "}\n"
    )),
    "ruby": ("a.rb", "Audit", {
        "method": "run_it", "immutable": "LIMIT", "property": "view",
    }, (
        "class Audit\n"
        "  LIMIT = 3\n"
        "  attr_accessor :view\n"
        "  def run_it\n"
        "    1\n"
        "  end\n"
        "end\n"
    )),
    "kotlin": ("a.kt", "Audit", {
        "method": "runIt", "mutable": "tally", "immutable": "limit", "property": "view",
    }, (
        "class Audit {\n"
        "    val limit: Int = 3\n"
        "    var tally: Int = 0\n"
        "    val view: Int\n"
        "        get() = 2\n"
        "    fun runIt(): Int { return 1 }\n"
        "}\n"
    )),
    "swift": ("a.swift", "Audit", {
        "method": "runIt", "mutable": "tally", "immutable": "limit", "property": "view",
    }, (
        "class Audit {\n"
        "    let limit: Int = 3\n"
        "    var tally: Int = 0\n"
        "    var view: Int { return 2 }\n"
        "    func runIt() -> Int { return 1 }\n"
        "}\n"
    )),
    "scala": ("a.scala", "Audit", {
        "method": "runIt", "mutable": "tally", "immutable": "limit",
    }, (
        "class Audit {\n"
        "  val limit: Int = 3\n"
        "  var tally: Int = 0\n"
        "  def runIt(): Int = 1\n"
        "}\n"
    )),
    "dart": ("a.dart", "Audit", {
        "method": "runIt", "mutable": "tally", "immutable": "limit", "property": "view",
    }, (
        "class Audit {\n"
        "  final int limit = 3;\n"
        "  int tally = 0;\n"
        "  int get view => 2;\n"
        "  int runIt() { return 1; }\n"
        "}\n"
    )),
    "groovy": ("a.groovy", "Audit", {
        "method": "runIt", "mutable": "tally", "immutable": "LIMIT",
    }, (
        "class Audit {\n"
        "    static final int LIMIT = 3\n"
        "    int tally = 0\n"
        "    int runIt() { return 1 }\n"
        "}\n"
    )),
    "apex": ("Audit.cls", "Audit", {
        "method": "runIt", "mutable": "tally", "immutable": "LIMIT_N", "property": "View",
    }, (
        "public class Audit {\n"
        "    static final Integer LIMIT_N = 3;\n"
        "    Integer tally = 0;\n"
        "    public Integer View { get; set; }\n"
        "    Integer runIt() { return 1; }\n"
        "}\n"
    )),
    "objc": ("a.m", "Audit", {
        "method": "runIt", "mutable": "tally", "property": "view",
    }, (
        "@interface Audit : NSObject {\n"
        "    int tally;\n"
        "}\n"
        "@property int view;\n"
        "- (int)runIt;\n"
        "@end\n"
    )),
    "gdscript": ("a.gd", "Audit", {
        "method": "run_it", "mutable": "tally", "immutable": "LIMIT",
    }, (
        "class Audit:\n"
        "\tconst LIMIT = 3\n"
        "\tvar tally = 0\n"
        "\tfunc run_it():\n"
        "\t\treturn 1\n"
    )),
    "dlang": ("a.d", "Audit", {
        "method": "runIt", "mutable": "tally", "immutable": "limit",
    }, (
        "class Audit {\n"
        "    immutable int limit = 3;\n"
        "    int tally = 0;\n"
        "    int runIt() { return 1; }\n"
        "}\n"
    )),
    "solidity": ("a.sol", "Audit", {
        "method": "runIt", "mutable": "tally", "immutable": "LIMIT",
    }, (
        "contract Audit {\n"
        "    uint constant LIMIT = 3;\n"
        "    uint tally = 0;\n"
        "    function runIt() public returns (uint) { return 1; }\n"
        "}\n"
    )),
    "go": ("a.go", "Audit", {
        "method": "RunIt", "mutable": "Tally",
    }, (
        "package a\n"
        "type Audit struct {\n"
        "\tTally int\n"
        "}\n"
        "func (a *Audit) RunIt() int { return 1 }\n"
    )),
    "rust": ("a.rs", "Audit", {
        "method": "run_it", "mutable": "tally", "immutable": "LIMIT",
    }, (
        "struct Audit {\n"
        "    tally: i32,\n"
        "}\n"
        "impl Audit {\n"
        "    const LIMIT: i32 = 3;\n"
        "    fn run_it(&self) -> i32 { 1 }\n"
        "}\n"
    )),
}


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """`parse_file` consults the config gate first; this box disables several
    languages, and a disabled one reads as ABSENT across its whole row. Patch
    `jcodemunch_mcp.config`: the parser's import of the gate is function-local.
    """
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


#: The kind of the CONTAINER, pinned so that a class regressing to some other
#: kind cannot hide behind a lookup by name.
_CONTAINER_KIND: dict[str, str] = {"go": "type", "rust": "type"}


def _observe(language: str) -> dict[str, tuple[str, str]]:
    """{role: (kind or ABSENT, ownership)} for one sample."""
    filename, class_name, members, source = _SAMPLES[language]
    symbols = parse_file(source, filename, language)
    container = _CONTAINER_KIND.get(language, "class")
    owners = {s.id for s in symbols if s.name == class_name and s.kind == container}
    row = {}
    for role, member in members.items():
        hits = [s for s in symbols if s.name == member]
        if not hits:
            row[role] = (ABSENT, NO_OWNER)
            continue
        assert len(hits) == 1, (language, role, [(s.name, s.kind) for s in hits])
        hit = hits[0]
        if hit.parent in owners:
            ownership = OWNED
        elif (hit.qualified_name or "").startswith(class_name + "."):
            ownership = QUALIFIED
        else:
            ownership = NO_OWNER
        row[role] = (hit.kind, ownership)
    return row


#: Class-bearing by spec, deliberately without a row, with the reason.
_NOT_SAMPLED: dict[str, str] = {
    "perl": "a Perl class is a `package`; the language has no member declarations to audit",
    "haskell": (
        "a typeclass declares methods and no state, so three of the four roles cannot "
        "be written; the method's kind and owner are pinned in test_haskell_declared_forms.py"
    ),
}

#: Written by observation, then frozen. A cell is (kind or ABSENT, ownership).
_TABLE: dict[str, dict[str, tuple[str, str]]] = {
    "python": {"method": ("method", OWNED), "mutable": ("field", OWNED), "immutable": ("constant", OWNED), "property": ("method", OWNED)},
    "javascript": {"method": ("method", OWNED), "mutable": ("field", OWNED), "property": ("method", OWNED)},
    "typescript": {"method": ("method", OWNED), "mutable": ("field", OWNED), "immutable": ("constant", OWNED), "property": ("method", OWNED)},
    "tsx": {"method": ("method", OWNED), "mutable": ("field", OWNED), "immutable": ("constant", OWNED), "property": ("method", OWNED)},
    "java": {"method": ("method", OWNED), "mutable": ("field", OWNED), "immutable": ("constant", OWNED)},
    "csharp": {"method": ("method", OWNED), "mutable": ("field", OWNED), "immutable": ("constant", OWNED), "property": ("property", OWNED)},
    "cpp": {"method": ("method", OWNED), "mutable": ("field", OWNED), "immutable": ("field", OWNED)},
    "arduino": {"method": ("method", OWNED), "mutable": ("field", OWNED), "immutable": ("field", OWNED)},
    "php": {"method": ("method", OWNED), "mutable": ("property", OWNED), "immutable": ("constant", OWNED)},
    "ruby": {"method": ("method", OWNED), "immutable": ("constant", OWNED), "property": ("property", OWNED)},
    "kotlin": {"method": ("method", OWNED), "mutable": ("property", OWNED), "immutable": ("property", OWNED), "property": ("property", OWNED)},
    "swift": {"method": ("method", OWNED), "mutable": ("property", OWNED), "immutable": ("constant", OWNED), "property": ("property", OWNED)},
    "scala": {"method": ("method", OWNED), "mutable": ("field", OWNED), "immutable": ("constant", OWNED)},
    "dart": {"method": ("method", OWNED), "mutable": ("field", OWNED), "immutable": ("field", OWNED), "property": ("method", OWNED)},
    "groovy": {"method": ("method", OWNED), "mutable": ("field", OWNED), "immutable": ("constant", OWNED)},
    "apex": {"method": ("method", OWNED), "mutable": ("field", OWNED), "immutable": ("constant", OWNED), "property": ("property", OWNED)},
    "objc": {"method": ("method", OWNED), "mutable": ("field", OWNED), "property": ("property", OWNED)},
    "gdscript": {"method": ("method", OWNED), "mutable": ("field", OWNED), "immutable": ("constant", OWNED)},
    "dlang": {"method": ("method", OWNED), "mutable": ("field", OWNED), "immutable": ("constant", OWNED)},
    "solidity": {"method": ("method", OWNED), "mutable": ("field", OWNED), "immutable": ("constant", OWNED)},
    "go": {"method": ("method", OWNED), "mutable": ("field", OWNED)},
    "rust": {"method": ("method", OWNED), "mutable": ("field", OWNED), "immutable": ("constant", OWNED)},
}

#: Every cell of `_TABLE` that breaks `_RULE`, and what tracks it. ⚠⚠ A TRACKED
#: gap, never a tolerated one: the entry FAILS when the cell is fixed.
_GAPS: dict[tuple[str, str], str] = {
    # ⚠⚠ **EMPTY, and keeping it that way is the point of this file.** Every
    # cell of `_TABLE` satisfies `_RULE`. The burn-down ran in five passes, one
    # MECHANISM each rather than one language each: ownership for five custom
    # parsers (#788, one helper they all ask), the class state four of them
    # never extracted (#774, #776, #779, #782), Go's receiver, which needs a
    # second pass because a method may precede its type (#778), the three
    # spec-driven languages whose channels existed and were not wired up
    # (#775, #777, #785), and Rust, held back to last because its `syn` ORACLE
    # had to learn fields before the extractor could emit any (#786).
    #
    # ⚠⚠ **An empty dict is not the same as a solved problem, and the file says
    # so above**: `_SAMPLES` covers the languages it covers, and
    # `test_every_class_bearing_spec_is_sampled_or_excused` is one-directional
    # by construction for a custom extractor. #809, #811 and #812 are SIX
    # languages this table has never had a row for -- Zig, PowerShell and
    # MATLAB in the first two, Pascal, F# and Nim in the third. ⚠ It read
    # "nine" until review: that is the count of MENTIONS across three issues,
    # and #809 and #811 name the same three languages. **The enumeration built
    # to
    # stop this defect class being found one language per fix cannot see the
    # languages it does not sample.**
}

def _violations(table: dict[str, dict[str, tuple[str, str]]]) -> set[tuple[str, str]]:
    """Every (language, role) cell that is not a right answer."""
    return {
        (language, role)
        for language, row in table.items()
        for role, (kind, ownership) in row.items()
        if kind not in _RULE[role] or ownership != OWNED
    }


@pytest.mark.parametrize("language", sorted(_SAMPLES))
def test_the_table_is_what_the_parser_answers(language):
    """A parser change that moves a cell fails HERE, for better or worse.

    Better: update the cell, and if it now satisfies `_RULE`, delete its `_GAPS`
    entry (the next test fails until you do). Worse: the message is the diff.
    """
    assert _observe(language) == _TABLE[language]


def test_every_broken_cell_is_tracked_and_every_tracked_cell_is_broken():
    """Both directions. An untracked broken cell is a defect nobody owns; a
    tracked cell that is no longer broken is a gap that closed unannounced."""
    broken = _violations(_TABLE)
    tracked = set(_GAPS)
    assert broken - tracked == set(), f"broken and untracked: {sorted(broken - tracked)}"
    assert tracked - broken == set(), f"tracked and fixed: {sorted(tracked - broken)}"


def test_the_table_and_the_samples_cover_the_same_cells():
    assert {(lang, role) for lang, row in _TABLE.items() for role in row} == {
        (lang, role) for lang, (_, _, members, _) in _SAMPLES.items() for role in members
    }


def test_every_sample_yields_its_container_and_nothing_it_did_not_declare():
    """Both directions of the symbol SET, which the table cannot see.

    A missing container makes a whole row ABSENT for a reason unrelated to
    members. And a FABRICATED symbol -- a parameter or a local published as a
    member, the direction #751's row calls worse than an absence -- moves no
    cell: review appended a fake `constant` to every sample and all 22 rows
    stayed green. Every sample declares exactly its container and its members,
    so anything else in the answer is a fabrication.
    """
    for language, (filename, class_name, members, source) in _SAMPLES.items():
        symbols = parse_file(source, filename, language)
        container = _CONTAINER_KIND.get(language, "class")
        assert (class_name, container) in {(s.name, s.kind) for s in symbols}, language
        # A Counter, never a set: review cloned the class symbol as a second
        # `Audit` of kind `constant` and a set comparison stayed green on all 22
        # samples (CLAUDE.md 08-27, "a set cannot count"). ABSENT members are
        # subtracted because the declaration is what is pinned here, not the gap.
        answered = Counter(s.name for s in symbols)
        declared = Counter([class_name, *members.values()])
        assert not answered - declared, (language, dict(answered - declared))


def test_a_gap_names_its_tracker():
    """`#N`, or the literal `unfiled`. Never free text: a gap whose tracker is
    prose cannot be searched for, and #758 is what an unverifiable cite costs."""
    for cell, tracker in _GAPS.items():
        assert re.fullmatch(r"#\d+|unfiled", tracker), (cell, tracker)


def test_every_class_bearing_spec_is_sampled_or_excused():
    """A language whose spec can emit a `class` owes this file a row.

    ⚠ One-directional by construction: languages with a custom extractor
    (groovy, apex, objc, dlang, solidity) declare no `symbol_node_types` and are
    sampled above by hand, so a NEW custom extractor is not caught here.
    """
    from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY

    class_bearing = {
        language for language, spec in LANGUAGE_REGISTRY.items()
        if "class" in set(getattr(spec, "symbol_node_types", {}).values())
    }
    unaccounted = class_bearing - set(_SAMPLES) - set(_NOT_SAMPLED)
    assert not unaccounted, sorted(unaccounted)
    assert not set(_SAMPLES) & set(_NOT_SAMPLED)


@pytest.mark.parametrize("cell, expected", [
    (("mutable", ("constant", OWNED)), True),    # #769 / #770's shape
    (("mutable", ("field", OWNED)), False),
    (("mutable", ("field", QUALIFIED)), True),   # right kind, no owner
    (("immutable", ("constant", OWNED)), False),
    (("immutable", (ABSENT, NO_OWNER)), True),   # #755's shape
    (("method", ("function", OWNED)), True),
    (("property", ("constant", OWNED)), True),
    (("property", ("field", OWNED)), True),      # the answer #770 rejects
])
def test_the_rule_fires(cell, expected):
    """Non-vacuity: the rule is what decides `_GAPS`, so it is tested alone."""
    role, answer = cell
    assert bool(_violations({"probe": {role: answer}})) is expected
