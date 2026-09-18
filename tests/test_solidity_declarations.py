"""#736 and #737: a Solidity constructor and a custom error yield no symbols.

```
parse_file('contract T {\\n  uint256 public total;\\n  error Unauthorized(address who);\\n'
           '  constructor(uint256 x) { total = x; }\\n  function f() public {}\\n}\\n',
           'T.sol', 'solidity')
-> [('T','class'), ('total','constant'), ('f','function')]
```

The contract, its state variable and its function extract. The constructor and
the error are absent, for two DIFFERENT reasons that look identical from
outside.

⚠⚠ **#737 is #722's shape: the extractor matches a node type the grammar never
emits.** `_parse_solidity_symbols` lists `error_definition` in `_MEMBER_TYPES`
and the Solidity grammar spells the form `error_declaration` (verified against
the compiled grammar, not inferred from the node name). The literal matches
nothing, so the form is silently unextractable and no test anywhere fails --
exactly `HASKELL_SPEC`'s `type_synon` against the grammar's `type_synomym`.
Recorded in `_INLINE_GHOSTS_FOUND` by #724's scan.

⚠⚠ **#736 is an omission, and it needs a BUILT name.** `constructor_definition`
is simply absent from `_MEMBER_TYPES`. Adding it is not enough: the grammar
gives a constructor NO identifier child at all -- its named children are
`parameter` and `function_body` -- so `_first_identifier` returns None and the
member is dropped in silence even once the node type is listed. The name has to
be constructed, the way C# operators, conversions and indexers were in #714
(`operator +`, `this[]`).

⚠ **Solidity is DISABLED in this maintainer's local config**, so an unpatched
`parse_file` returns `[]` for a reason that is not the defect -- and a red test
written without the patch would "reproduce" every Solidity bug ever filed. Every
test here patches `jcodemunch_mcp.config.is_language_enabled`, which is where
`parse_file`'s function-local import resolves; patching the parser module would
silently do nothing (the `cli/policy.py` monkeypatch trap).
"""

from unittest import mock

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.grammar_pack import get_parser


SOURCE = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Vault {
    uint256 public total;

    error Unauthorized(address who);
    error InsufficientBalance(uint256 wanted, uint256 held);

    event Deposited(address indexed who, uint256 amount);

    constructor(uint256 seed) {
        total = seed;
    }

    modifier onlyPositive(uint256 n) {
        require(n > 0);
        _;
    }

    function deposit(uint256 n) public {
        total += n;
    }
}

interface IVault {
    error NotAllowed();
    function deposit(uint256 n) external;
}

// ⚠ The shapes `test_the_constructor_knows_its_owner` and
// `test_no_declaration_is_emitted_twice` ARGUE for, which the first version of
// this fixture did not contain -- it had exactly one constructor, so neither
// test could fail on the collision it named
// ([[a-fixture-that-cannot-express-the-reported-shape-cannot-fail-on-it]]).
// Found in review.
error TopLevelErr(bytes32 what);

library Math {
    error DivByZero();

    function half(uint256 n) internal pure returns (uint256) {
        return n / 2;
    }
}

abstract contract Base {
    constructor(uint256 a) {}
}

contract Derived is Base {
    constructor(uint256 a) Base(a) {}
}

contract Sibling {
    constructor() {}

    fallback() external {}
    receive() external payable {}
}
"""


@pytest.fixture
def parsed():
    """A LIST [[a-set-cannot-count]], and the language gate patched ON.

    ⚠ `parse_file` imports `is_language_enabled` from `..config` inside the
    function, so the patch target is the CONFIG module. Patching the parser
    module resolves nothing and the fixture would return `[]`, making every
    absence assertion below pass for the wrong reason.
    """
    with mock.patch("jcodemunch_mcp.config.is_language_enabled", return_value=True):
        return list(parse_file(SOURCE, "Vault.sol", "solidity"))


def _named(parsed, name):
    return [s for s in parsed if s.name == name]


def test_the_language_gate_is_actually_patched(parsed):
    """Non-vacuity floor, and it is not hypothetical on this box.

    Solidity is disabled in the maintainer's local config, so an unpatched run
    yields `[]`. Without this, every "X is absent" assertion in this file would
    pass on a tree where the defect was fixed.
    """
    assert parsed, (
        "no Solidity symbols at all -- the language gate is not patched, or the "
        "grammar is unavailable. Nothing below this line means anything."
    )


def test_the_fixture_parses_without_error():
    """Separate "not extracted" from "never parsed"."""
    tree = get_parser("solidity").parse(SOURCE.encode("utf-8"))

    assert not tree.root_node.has_error, "the fixture is not valid Solidity"


def test_the_controls_extract(parsed):
    """The shapes that already worked, so a total failure is distinguishable."""
    names = {s.name for s in parsed}

    assert {"Vault", "IVault", "deposit", "total", "Deposited", "onlyPositive"} <= names, sorted(names)


# ---------------------------------------------------------------------------
# #737 -- the ghost: a literal the grammar never emits
# ---------------------------------------------------------------------------

def test_the_grammar_spells_error_declaration_not_error_definition():
    """The root cause, asserted against the COMPILED grammar.

    ⚠ This is the assertion that makes #737 a fact rather than a reading of the
    node name. `node_kind_count`/`node_kind_for_id` is the grammar's own symbol
    table -- the same source #724 uses, deliberately not `node-types.json`,
    which the grammar pack does not ship.
    """
    language = get_parser("solidity").language
    kinds = {language.node_kind_for_id(i) for i in range(language.node_kind_count)}

    assert "error_declaration" in kinds
    assert "error_definition" not in kinds, (
        "the grammar now emits `error_definition` too, so #737's premise has "
        "changed and the fix needs re-deriving rather than adjusting"
    )


def test_a_custom_error_is_indexed(parsed):
    """The reported case."""
    assert _named(parsed, "Unauthorized"), sorted(s.name for s in parsed)


def test_every_custom_error_is_indexed(parsed):
    """Not just the first, and not just the one in a contract.

    `NotAllowed` sits in an `interface`, which reaches `_walk` down a different
    branch of `_CONTRACT_TYPES`; a fix keyed on the contract body alone passes
    the test above and fails this one.
    """
    names = {s.name for s in parsed}

    assert {"Unauthorized", "InsufficientBalance", "NotAllowed"} <= names, sorted(names)


def test_an_error_knows_its_owner(parsed):
    """`Vault.Unauthorized`, never a bare name -- the scope is already threaded."""
    err = _named(parsed, "Unauthorized")[0]

    assert err.qualified_name == "Vault.Unauthorized", err.qualified_name


# ---------------------------------------------------------------------------
# #736 -- the omission that also needs a built name
# ---------------------------------------------------------------------------

def test_a_constructor_is_indexed(parsed):
    """The reported case.

    ⚠ A fix that only adds `constructor_definition` to the node-type map still
    fails this: the grammar gives a constructor no identifier, so
    `_first_identifier` returns None and the member is dropped. The name must be
    built.
    """
    ctors = [s for s in parsed if s.name == "constructor"]

    assert ctors, sorted(s.name for s in parsed)


def test_the_constructor_has_no_identifier_in_the_grammar():
    """Why a `_MEMBER_TYPES` entry alone is not the fix, asserted not argued."""
    root = get_parser("solidity").parse(SOURCE.encode("utf-8")).root_node

    def find(node, kind):
        if node.type == kind:
            return node
        for child in node.children:
            hit = find(child, kind)
            if hit is not None:
                return hit
        return None

    ctor = find(root, "constructor_definition")
    assert ctor is not None, "the fixture has no constructor_definition"
    assert not [c for c in ctor.children if c.type == "identifier"], (
        "the grammar now gives a constructor an identifier, so the built name is "
        "no longer necessary -- prefer the grammar's own name"
    )


def test_the_constructor_knows_its_owner(parsed):
    """`Vault.constructor`.

    Two contracts in one file each having a `constructor` is the ordinary case,
    so an unqualified name would collide across owners.
    """
    ctor = [s for s in parsed if s.name == "constructor"][0]

    assert ctor.qualified_name == "Vault.constructor", ctor.qualified_name


def test_the_constructor_is_a_method_shaped_kind(parsed):
    """A constructor is callable; `type` would be wrong.

    Solidity's existing members use `function` for functions and modifiers, so
    this follows the file's own convention rather than importing another
    language's.
    """
    ctor = [s for s in parsed if s.name == "constructor"][0]

    assert ctor.kind == "function", ctor.kind


# ---------------------------------------------------------------------------
# Neither channel may fire twice
# ---------------------------------------------------------------------------

def test_a_file_scope_error_is_indexed(parsed):
    """#737's fix must reach an error declared OUTSIDE any contract.

    ⚠ Asserted by nothing in the first version of this file. `_walk` recurses
    from the root, so a file-scope `error` reaches `_MEMBER_TYPES` with an empty
    scope -- a fix keyed on the contract body would pass every other error test
    here and drop this one.
    """
    top = _named(parsed, "TopLevelErr")

    assert top, sorted(s.name for s in parsed)
    assert top[0].qualified_name == "TopLevelErr", top[0].qualified_name


def test_an_error_in_a_library_is_indexed(parsed):
    """A `library` is a third container spelling beside contract and interface."""
    assert _named(parsed, "DivByZero"), sorted(s.name for s in parsed)


@pytest.mark.parametrize("owner", ["Vault", "Base", "Derived", "Sibling"])
def test_every_constructor_is_owned_by_its_own_contract(parsed, owner):
    """⚠⚠ THE case the owner test argues for, now actually in the fixture.

    Four constructors in one file -- a contract, an abstract contract, a derived
    contract calling `Base(a)`, and one with no parameters. An unqualified name
    would collapse all four, and nothing in the first fixture could have noticed
    because it held exactly one.
    """
    ctors = [s for s in parsed if s.name == "constructor" and s.qualified_name == f"{owner}.constructor"]

    assert len(ctors) == 1, [s.qualified_name for s in parsed if s.name == "constructor"]


def test_every_constructor_has_a_distinct_id(parsed):
    """Ids, not just qualified names -- a collision here would silently merge
    two members in the index even with the names right."""
    ids = [s.id for s in parsed if s.name == "constructor"]

    assert len(ids) == 4, ids
    assert len(set(ids)) == len(ids), ids


def test_fallback_and_receive_stay_absent(parsed):
    """The boundary, recorded rather than discovered.

    `fallback_receive_definition` is still in #724's inventory as an unnamed
    form, so `fallback()` and `receive()` yield nothing. Asserting it keeps the
    constructor fix from being read as covering every nameless member, and this
    test fails when that gap closes.
    """
    assert not [s for s in parsed if s.name in ("fallback", "receive")], (
        "a nameless Solidity member other than the constructor now extracts -- "
        "if that is intended, remove this test and the inventory row together"
    )


def test_no_declaration_is_emitted_twice(parsed):
    """Keyed on (name, line): two same-named members in different contracts are
    not the same thing as one member emitted twice."""
    seen = [(s.name, s.line) for s in parsed]

    assert len(seen) == len(set(seen)), sorted(n for n in seen if seen.count(n) > 1)
