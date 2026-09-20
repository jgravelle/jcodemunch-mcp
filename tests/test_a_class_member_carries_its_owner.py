"""A class member carries its owner's id, not just its owner's name (#788).

Five languages are parsed by a CUSTOM extractor rather than the spec walk:
Apex, D, Groovy, Objective-C and Solidity (`extractor.py`'s `parse_file`
dispatch). Each threads a `scope: str` -- the enclosing class's qualified name
-- down its own `_walk` and builds `qualified = f"{scope}.{name}"` by string
concatenation. None of them passes the owner's ID, **which each has already
computed** one frame up as `make_symbol_id(filename, qualified, kind)`.

So a member comes back qualified by its class and carrying `parent=None`, which
makes it invisible to every parent-keyed reader: the file summary counts members
by `parent` (#760), `get_class_hierarchy` cannot place them, and a class reports
as having no members at all.

⚠⚠ **This is the 08-19 standing lesson at scale -- a second generator, five
times over.** Every one of these parsers reproduces ownership instead of asking
for it, and all five are wrong in the same way. The fix is one helper
(`_member_of`) that answers both halves, asked at every site.

⚠ **The ratchets at the end of this file govern those five functions and
nothing else.** They stop the rule being re-transcribed INSIDE them; they say
nothing about a sixth parser, and Zig, PowerShell and MATLAB carry this defect
today under #809. The parametrized tests above are what grade the five.

⚠⚠ **Objective-C gets the qualified-name half only, and the reason is #771.**
`@interface Audit` and `@implementation Audit` are two symbols with one id, so
`_disambiguate_overloads` renumbers the CLASS to `~1`/`~2` while the member's
`parent` still names the un-suffixed id -- an id no symbol has. `build_symbol_tree`
requires `symbol.parent in node_map`, so `get_class_hierarchy` still leaves ObjC
members unplaced; `_heuristic_summary` strips the ordinal and does benefit. C#
`partial class` and Swift `extension` have shipped in that state since #771.
This change does not fix it and does not make it worse.

⚠ **Go (#778) is NOT here.** Same symptom, different cause: Go's method is not
qualified at ALL (`RunIt`, not `Audit.RunIt`), because Go attaches a method to a
receiver type the spec walk never reads. There is no enclosing node to be a
parent in the first place, so fixing it means resolving a receiver to a declared
type -- a different question with its own failure modes. It gets its own PR.

⚠ **What is NOT here: the members these four parsers never extract at all.**
Apex, D, Groovy and Objective-C each drop their class STATE (#774, #776, #779,
#782 all say "some class members are not indexed"), and that is a second
mechanism in the same functions. It ships next, on top of this helper, so those
four issues close there; #788 is the one this closes, because Solidity already
extracts its state and only ownership was left.
"""

from typing import Optional

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """This box disables some languages in its own config; the gate is function-local."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


_APEX = (
    "public class Audit {\n"
    "    public Integer runIt() { return 1; }\n"
    "}\n"
)
_DLANG = (
    "class Audit {\n"
    "    int runIt() { return 1; }\n"
    "}\n"
)
_GROOVY = (
    "class Audit {\n"
    "    int runIt() { return 1 }\n"
    "}\n"
)
_OBJC = (
    "@interface Audit\n"
    "- (int)runIt;\n"
    "@end\n"
    "@implementation Audit\n"
    "- (int)runIt { return 1; }\n"
    "@end\n"
)
_SOLIDITY = (
    "contract Audit {\n"
    "    uint constant LIMIT = 3;\n"
    "    uint tally = 0;\n"
    "    function runIt() public returns (uint) { return 1; }\n"
    "}\n"
)

#: (language, filename, source, container name, [member names]). The container
#: is looked up by NAME and its id is what every member must carry.
_OWNED: list[tuple[str, str, str, str, list[str]]] = [
    ("apex", "Audit.cls", _APEX, "Audit", ["runIt"]),
    ("dlang", "audit.d", _DLANG, "Audit", ["runIt"]),
    ("groovy", "Audit.groovy", _GROOVY, "Audit", ["runIt"]),
    ("objc", "Audit.m", _OBJC, "Audit", ["runIt"]),
    ("solidity", "Audit.sol", _SOLIDITY, "Audit", ["LIMIT", "tally", "runIt"]),
]

_IDS = [row[0] for row in _OWNED]


def _by_name(language, filename, source):
    out: dict[str, list] = {}
    for symbol in parse_file(source, filename, language):
        out.setdefault(symbol.name, []).append(symbol)
    return out


def _without_ordinal(symbol_id: Optional[str]) -> Optional[str]:
    """An id with `_disambiguate_overloads`' `~N` suffix removed.

    ⚠⚠ Needed for ObjC and ONLY ObjC here, and it is #771's residue, not a
    weakening of the assertion below. `@interface Audit` and
    `@implementation Audit` are two symbols with the same `(file, qualified,
    kind)`, so the post-parse disambiguator renumbers the CLASS to `~1`/`~2`
    while every child's `parent` still names the id the parser computed -- an
    id no symbol has any more. A C# `partial class` and a Swift `extension`
    have shipped that way since #771, and `_heuristic_summary` matches on the
    stripped id for exactly this reason. Reproducing that rule here rather
    than inventing a second one is deliberate; re-pointing children at a
    chosen ordinal is a different fix in a different layer.
    """
    if symbol_id is None:
        return None
    return symbol_id.rsplit("~", 1)[0] if "~" in symbol_id else symbol_id


@pytest.mark.parametrize("language,filename,source,container,members", _OWNED, ids=_IDS)
def test_every_member_carries_its_owners_id(
    language, filename, source, container, members
):
    """The defect, stated as the property all five share."""
    found = _by_name(language, filename, source)
    owners = {_without_ordinal(s.id) for s in found.get(container, [])}
    assert owners, f"{language}: the container {container!r} was not extracted"
    for member in members:
        hits = found.get(member) or []
        assert hits, f"{language}: {member!r} was not extracted"
        for hit in hits:
            assert hit.parent is not None, f"{language}: {member} carries no parent"
            assert _without_ordinal(hit.parent) in owners, (
                f"{language}: {member} parent={hit.parent!r} is not the id of {container}"
            )


def test_objc_is_the_only_language_here_that_needs_the_ordinal_stripped():
    """Non-vacuity for the helper above: it must not be quietly load-bearing
    for the other four.

    ⚠ If a future change gave, say, Solidity two same-named contracts in one
    file, this fails and the question gets asked again rather than absorbed.
    """
    needs_stripping = set()
    for language, filename, source, container, _members in _OWNED:
        ids = [s.id for s in _by_name(language, filename, source).get(container, [])]
        if any("~" in i for i in ids):
            needs_stripping.add(language)
    assert needs_stripping == {"objc"}, needs_stripping


@pytest.mark.parametrize("language,filename,source,container,members", _OWNED, ids=_IDS)
def test_the_qualified_name_does_not_move(
    language, filename, source, container, members
):
    """⚠⚠ The id-stability half, asserted rather than assumed.

    `make_symbol_id` is keyed on `(filename, qualified_name, kind)`, so a fix
    that rebuilt the qualified name differently would silently re-id every
    member of five languages -- a far larger change than the one being made,
    and one nothing else here would notice. Populating `parent` alone must
    leave this byte-identical to what the parsers already emit.
    """
    found = _by_name(language, filename, source)
    for member in members:
        for hit in found[member]:
            assert hit.qualified_name == f"{container}.{member}", (
                language, member, hit.qualified_name
            )


#: (language, filename, source, {name: kind}) at FILE scope, where nothing owns
#: the declaration. ⚠ Groovy is absent: its top-level `def` already takes the
#: `function` kind from `scope` being empty, and it is pinned in
#: `test_groovy_parser.py`. Objective-C is absent for the same reason -- a C
#: function outside any `@interface` never reaches the member branch.
_TOP_LEVEL: list[tuple[str, str, str, dict[str, str]]] = [
    ("dlang", "free.d", "int freeFn() { return 1; }\n", {"freeFn": "function"}),
    (
        "solidity",
        "free.sol",
        "function freeFn() pure returns (uint) { return 1; }\n",
        {"freeFn": "function"},
    ),
]


@pytest.mark.parametrize(
    "language,filename,source,expected", _TOP_LEVEL,
    ids=[r[0] for r in _TOP_LEVEL],
)
def test_a_declaration_with_no_owner_keeps_a_bare_name_and_no_parent(
    language, filename, source, expected
):
    """The boundary, and the half a careless fix takes with it.

    ⚠⚠ A free function is not a method and has no owner. #780/#783 kept exactly
    this asymmetry for constants, and a helper that defaulted to *some* parent
    would erase it here without failing anything above.
    """
    found = _by_name(language, filename, source)
    for name, kind in expected.items():
        hits = found.get(name) or []
        assert len(hits) == 1, (language, name, [(s.name, s.kind) for s in hits])
        hit = hits[0]
        assert hit.parent is None, (language, name, hit.parent)
        assert hit.qualified_name == name, (language, name, hit.qualified_name)
        assert hit.kind == kind, (language, name, hit.kind)


def test_a_contract_method_is_a_method_and_a_free_function_is_not():
    """#788's other half: Solidity called every `function_definition` a
    `function`, including the ones inside a contract.

    ⚠ The kind is decided by whether there IS an owner, which is the same
    question `_member_of` answers -- so the two halves of this issue are one
    change, not a change plus a special case. Groovy already does it this way
    (`kind = "method" if scope else "function"`), which is why it needs no row
    here.
    """
    inside = _by_name("solidity", "Audit.sol", _SOLIDITY)["runIt"]
    assert [s.kind for s in inside] == ["method"]
    outside = _by_name(
        "solidity", "free.sol",
        "function freeFn() pure returns (uint) { return 1; }\n",
    )["freeFn"]
    assert [s.kind for s in outside] == ["function"]


def test_a_d_class_method_is_a_method():
    """#776's kind half. D shares Solidity's defect and its fix."""
    hits = _by_name("dlang", "audit.d", _DLANG)["runIt"]
    assert [s.kind for s in hits] == ["method"]


def test_a_solidity_modifier_is_not_swept_into_the_method_change():
    """⚠ The neighbour that must NOT move, pinned because the fix above widens
    a rule that reads as "callable member of a contract".

    A modifier is not a method in Solidity's own vocabulary and its kind is
    load-bearing elsewhere; changing it would move ids in a released language
    for a question nobody asked. If that is ever revisited it should be a
    decision, not a side effect of this one.
    """
    source = (
        "contract Audit {\n"
        "    modifier onlyOwner() { _; }\n"
        "}\n"
    )
    hits = _by_name("solidity", "Mod.sol", source)["onlyOwner"]
    assert [s.kind for s in hits] == ["function"]


#: The five parsers this file governs. ⚠⚠ **A hard-coded list, and saying so is
#: half the point.** A scan over every `_parse_*_symbols` would fire on 22 other
#: parsers that legitimately build a dotted name from a module path, an arity or
#: a namespace, and none of those is member ownership. The cost is that this
#: guard is silent about a SIXTH parser: Zig, PowerShell and MATLAB carry the
#: same defect today and are tracked by #809, not by this test.
_GOVERNED = (
    "_parse_apex_symbols",
    "_parse_dlang_symbols",
    "_parse_groovy_symbols",
    "_parse_objc_symbols",
    "_parse_solidity_symbols",
)


def _source_of(target: str):
    import ast
    import inspect

    from jcodemunch_mcp.parser import extractor

    return ast.parse(inspect.getsource(getattr(extractor, target)))


def _hand_built_dotted_names(tree) -> int:
    """Every spelling of "join two names with a dot" in one function's AST."""
    import ast

    hits = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            literal = "".join(
                p.value for p in node.values if isinstance(p, ast.Constant)
            )
            if literal == ".":
                hits += 1
        # `scope + "." + name`, the spelling an f-string scan walks past.
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            for side in (node.left, node.right):
                if isinstance(side, ast.Constant) and side.value == ".":
                    hits += 1
    return hits


def test_no_governed_parser_builds_a_qualified_member_name_by_hand():
    """⚠⚠ The ratchet, and the reason this is one helper rather than five edits.

    The alternative fix -- adding `parent=owner.id` at each of the ~15
    `Symbol(...)` constructions across these five parsers -- is a sixth,
    seventh and eighth transcription of the same rule. It works on the day it
    is written and drifts into a gap later, which is what
    `java_field_is_constant`'s docstring already says happened once.

    ⚠⚠ **What this does NOT catch, measured rather than assumed**: it sees only
    the five functions in `_GOVERNED`, so a new parser inherits nothing from it,
    and it is a scan for a SPELLING -- the 09-01 standing lesson names exactly
    that weakness. The parametrized tests at the top of this file are what
    actually grade the five; this stops the rule being re-transcribed inside
    them, and the test below covers the other half of a re-transcription.
    """
    offenders = [
        f"{t}: {n} hand-built dotted name(s)"
        for t in _GOVERNED
        if (n := _hand_built_dotted_names(_source_of(t)))
    ]
    assert offenders == [], offenders


def test_every_governed_parser_asks_the_helper():
    """The half an absence scan cannot see.

    ⚠⚠ A parser can stop building the name by hand and still drop the owner --
    take the qualified name from `_member_of` and never pass `parent=` to
    `Symbol(...)`. The scan above stays green through that, because the
    offending spelling is GONE. So assert the positive: every governed parser
    calls the helper, and every one passes a `parent` keyword.
    """
    import ast

    missing = []
    for target in _GOVERNED:
        tree = _source_of(target)
        calls = {
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        if "_member_of" not in calls:
            missing.append(f"{target}: never calls _member_of")
        parents = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.keyword) and n.arg == "parent"
        ]
        if not parents:
            missing.append(f"{target}: never passes parent= to Symbol(...)")
    assert missing == [], missing
