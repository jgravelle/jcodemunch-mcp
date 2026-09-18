"""#731: a Go package-level `var` yields no symbol, while `const` beside it does.

`http.DefaultClient` is a package-level `var`. So is every sentinel error a Go
codebase exports (`io.EOF`, `sql.ErrNoRows`) once it is declared with `var`
rather than a function. None of them were findable by name.

⚠⚠ **The asymmetry is the whole defect and it is invisible from the extractor.**
`const_declaration` reaches the index through `constant_patterns` and
`var_declaration` reaches nothing, so a reader who finds Go's constant binder
sees a language that handles grouped, multi-name declarations properly and stops
looking -- #735's Java case and #732's Kotlin case wearing a third costume.

⚠⚠ **A local `var` is the SAME node type**, nested under a function's block, so
this needs a scope gate that #735's Java fix did not: Java spells a local
`local_variable_declaration`. The gate is an ALLOWLIST of parents that mean
module level, for #732's reason -- an allowlist that misses a spelling leaves
that form unindexed, which is the pre-fix status quo, while a denylist that
misses one publishes a local as package state.

⚠ **Go's grammar nests the two forms DIFFERENTLY, and copying the constant
binder would miss half of them**: a grouped `const ( ... )` holds its
`const_spec` children directly, while a grouped `var ( ... )` wraps them in a
`var_spec_list`. `test_a_grouped_var_block_binds_every_name` is that case, and
it fails against a binder written by analogy.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """⚠ Patch `jcodemunch_mcp.config`, never the parser module: `parse_file`
    imports the gate function-locally, so patching the parser does nothing
    (the `cli/policy.py` trap).
    """
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def pairs(source: str, filename: str = "a.go") -> set[tuple[str, str]]:
    return {(s.name, s.kind) for s in parse_file(source, filename, "go")}


PACKAGE_LEVEL = """package m

var Client = 1
"""


def test_a_package_level_var_is_a_symbol():
    """The reported case, through the product."""
    assert ("Client", "variable") in pairs(PACKAGE_LEVEL)


def test_the_constant_beside_it_keeps_its_own_kind():
    """⚠⚠ A `const` must not become a `variable`, and must not be emitted twice.

    Go spells the two forms as different node types, so unlike Java (#735) and
    Kotlin (#732) no shared predicate is needed to split them -- but a channel
    pointed at the wrong node type would show up exactly here, and asserting it
    costs one line.
    """
    source = """package m

const Limit = 10
var Client = 1
"""
    assert pairs(source) == {("Limit", "constant"), ("Client", "variable")}


def test_a_grouped_var_block_binds_every_name():
    """⚠⚠ The case a binder copied from the constant one gets wrong.

    Grouped `const ( ... )` holds its specs directly under the declaration;
    grouped `var ( ... )` wraps them in a `var_spec_list`. A walk over the
    declaration's direct children finds every constant and NO variable.
    """
    source = """package m

var (
\tA = 1
\tB int
\tC, D = 3, 4
)
"""
    assert pairs(source) == {
        ("A", "variable"),
        ("B", "variable"),
        ("C", "variable"),
        ("D", "variable"),
    }


def test_one_var_spec_can_bind_several_names():
    """`var C, D = 3, 4` is one node and two declarations.

    ⚠ This is why the form needs a CHANNEL rather than a `symbol_node_types`
    entry: `_extract_symbol` returns at most one `Optional[Symbol]` per node,
    so the map cannot express it (#735's argument, inherited).
    """
    assert pairs("package m\n\nvar C, D = 3, 4\n") == {
        ("C", "variable"),
        ("D", "variable"),
    }


def test_a_var_with_a_type_and_no_value_is_a_symbol():
    """`var ErrNotFound error` declares a name as surely as one with a value."""
    assert pairs("package m\n\nvar ErrNotFound error\n") == {("ErrNotFound", "variable")}


def test_an_unexported_var_is_a_symbol():
    """⚠ No case heuristic, for `_extract_go_constants`' stated reason: `var` IS
    the declaration, so filtering on capitalisation would drop exactly the
    unexported package state that Go's visibility rule spells in lowercase.
    """
    assert pairs("package m\n\nvar cache = 1\n") == {("cache", "variable")}


LOCAL_SCOPES = {
    "function body": """package m

func F() {
\tvar local = 1
\t_ = local
}
""",
    "method body": """package m

type T struct{}

func (t T) M() {
\tvar local = 1
\t_ = local
}
""",
    "if block": """package m

func F() {
\tif true {
\t\tvar local = 1
\t\t_ = local
\t}
}
""",
    "for block": """package m

func F() {
\tfor {
\t\tvar local = 1
\t\t_ = local
\t}
}
""",
    "grouped inside a function": """package m

func F() {
\tvar (
\t\tlocal = 1
\t)
\t_ = local
}
""",
}


@pytest.mark.parametrize("scope", sorted(LOCAL_SCOPES), ids=sorted(LOCAL_SCOPES))
def test_a_local_var_is_not_package_state(scope):
    """⚠⚠ The gate #735 did not need and this one does.

    Java spells a local `local_variable_declaration`, a different node type from
    the field form, so Java's fix needed no scope test. Go spells a local with
    the SAME `var_declaration`, so without the allowlist every local in the
    corpus becomes a package-level symbol -- #732's Kotlin defect, which is the
    reason the gate is an allowlist of module-level parents rather than a list
    of places to exclude.
    """
    assert "local" not in {name for name, _kind in pairs(LOCAL_SCOPES[scope])}


def test_a_short_var_declaration_is_not_a_declaration_channel():
    """`x := 1` is `short_var_declaration`, a different node type, and local by
    construction -- it cannot appear at package level. Asserted so a fix routed
    through it fails rather than quietly widening the channel.
    """
    source = """package m

func F() {
\tx := 1
\t_ = x
}
"""
    assert "x" not in {name for name, _kind in pairs(source)}


def test_the_blank_identifier_is_not_a_symbol():
    """⚠ `var _ = mustCompile(...)` is Go's DISCARD, not a declaration of a name.

    It cannot be referenced, several can sit in one file, and each would be a
    symbol called `_` competing in every ranking -- noise that looks like data.
    Found by probing the fix rather than by the report.

    ⚠ `const _ = iota` has the SAME hole in the constant channel and is
    deliberately NOT fixed here: it is a separate finding, filed as #763 rather
    than folded into a change about `var`, and this test asserts only the half
    this PR owns.
    """
    source = """package m

var _ = 1
var Client = 2
"""
    assert pairs(source) == {("Client", "variable")}


def test_no_declaration_is_emitted_twice():
    """⚠ Keyed on `(name, line)`, not on the name: two package-level vars with
    the same name are illegal in Go, but a name repeated across scopes is not,
    and a set of names cannot tell a double-emit from a legal repeat (#735).
    """
    source = """package m

const Limit = 10
var Client = 1
var (
\tA = 1
\tB = 2
)
"""
    seen = [(s.name, s.line) for s in parse_file(source, "a.go", "go")]
    assert len(seen) == len(set(seen)), f"a declaration was emitted twice: {seen}"


def test_functions_and_types_beside_the_vars_are_unaffected():
    """The blast radius, asserted rather than argued."""
    source = """package m

var Client = 1

type T struct{}

func F() {}

func (t T) M() {}
"""
    assert pairs(source) == {
        ("Client", "variable"),
        ("T", "type"),
        ("F", "function"),
        ("M", "method"),
    }
