"""#738: Julia short-form function definitions are not indexed.

```
parse_file('f(x) = x + 1\\nfunction g(y)\\n  return y\\nend\\n', 'a.jl', 'julia')
-> [('g', 'function')]
```

`f` is absent. Short-form definitions are idiomatic Julia and dense in numerical
code, so this is most of the surface of some files.

⚠⚠ **#722's shape again: the extractor matches a node type the grammar never
emits.** `_parse_julia_symbols` tests
`node.type in ("function_definition", "short_function_definition")`, and the
Julia grammar has no `short_function_definition` at all. The literal matches
nothing and nothing fails. Recorded in `_INLINE_GHOSTS_FOUND` by #724's scan,
which is the only instrument in the tree that could see it.

⚠⚠ **The spelling was UNESTABLISHED when the issue was filed, and establishing
it is the fix's first step.** Asked of the compiled grammar over eleven shapes:
a short form is an **`assignment` whose first named child is a
`call_expression`** -- `f(x) = x + 1` is
`assignment > call_expression > identifier`. That predicate is exact against
every neighbour, which is why it is the one used:

| source | first child | is a function? |
|---|---|---|
| `f(x) = x + 1` | `call_expression` | yes |
| `k(x::T) where T = x` | `where_expression` wrapping one | yes |
| `x = 1` | `identifier` | no |
| `h = z -> z*2` | `identifier` | no |
| `a[i] = 1` | `index_expression` | no |
| `a.b = 1` | `field_expression` | no |
| `a, b = 1, 2` | `open_tuple` | no |

⚠ `h = z -> z*2` binds a function to a name and is deliberately NOT one of
these: the grammar calls its right side an `arrow_function_expression` and its
left side a plain `identifier`, so it is a variable holding a lambda, the shape
`_extract_variable_function` handles for JS and which Julia has never claimed to
index. Widening to it is a separate decision with its own blast radius.

⚠⚠ **Two more things were found in this function while building the fixture, and
neither is this issue.** (1) A Julia MACRO yields no symbol: `macro_definition`
is in the grammar and is matched, but `_direct_name` takes the first direct
identifier child while the name sits one level deeper, under
`signature > call_expression`. Real defect, filed separately, and
`test_a_macro_is_a_known_separate_gap` fails when it is fixed so the record
cannot outlive it. (2) `mutable_struct_definition` is a DEAD literal -- the
grammar has no such node and spells `mutable struct X` as an ordinary
`struct_definition`, which IS matched, so the form extracts correctly and the
dead alternative costs nothing. Recorded because a reader who checks the grammar
will find it and wonder; it is cosmetic, not a defect, and is deliberately not
filed as one. (3) **Julia TYPES are not indexed unless the name is bare** --
`struct Box{T}`, `struct S <: Super` and `abstract type B <: A` all yield
nothing, because `_struct_name` reads `type_head > identifier` while the grammar
nests the name under `parametrized_type_expression` or the `<:`
`binary_expression`. Seven of nine shapes, and the two that work are the least
common. Filed as #749, found while checking that the dead literal in (2) lost
nothing on removal — which it did not.

⚠⚠ **(1), (3) and #738 itself are ONE shape three times: the node type is right
and matched, and the name helper looks at the wrong DEPTH.** That is why #738's
fix is `_callable_name`, a single resolver asked by both function forms, rather
than a fourth bespoke helper — and why #749 argues for the same treatment of
type heads instead of a fifth.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.grammar_pack import get_parser


SOURCE = """module Demo

f(x) = x + 1
g(x::Int) = x * 2
k(x::T) where T = x
square(x) = x^2

function longform(y)
    return y
end

struct Point
    x::Float64
end

macro sayhello(name)
    return :(println("hi"))
end

# Not function definitions.
counter = 0
CAP = 10
lambda_holder = z -> z * 2
table = Dict()
table["k"] = 1

end
"""


@pytest.fixture(scope="module")
def parsed():
    """A LIST, never a name-keyed dict [[a-set-cannot-count]]."""
    return list(parse_file(SOURCE, "demo.jl", "julia"))


def _named(parsed, name):
    return [s for s in parsed if s.name == name]


def test_the_fixture_parses_without_error():
    """Separate "not extracted" from "never parsed"."""
    tree = get_parser("julia").parse(SOURCE.encode("utf-8"))

    assert not tree.root_node.has_error, "the fixture is not valid Julia"


def test_the_controls_extract(parsed):
    """The long form, the struct and the module already worked.

    ⚠ `sayhello` is NOT in this list, and the first draft of this test put it
    there and failed against a correct tree. Julia macros are not indexed at
    all -- see the known-gap test below -- so asserting them as a control would
    have made this file red for a reason that is not #738.
    """
    names = {s.name for s in parsed}

    assert {"Demo", "longform", "Point"} <= names, sorted(names)


def test_a_macro_is_a_known_separate_gap(parsed):
    """⚠⚠ Found while building this fixture, and it is NOT this issue.

    `macro sayhello(x) ... end` yields no symbol. `macro_definition` IS in the
    grammar and IS matched, so this is not a ghost -- the extractor reads the
    name with `_direct_name`, which takes the first DIRECT identifier child,
    while a macro's name sits at `signature > call_expression > identifier`,
    exactly one level deeper. The same "the helper looks in the wrong place"
    shape as the short form, in the same function, needing a different fix.

    Filed separately (policy 1: one issue, one verdict). This asserts the gap so
    the record cannot outlive it -- when macros start extracting, this test
    fails and names the line to delete.
    """
    assert not [s for s in parsed if s.name == "sayhello"], (
        "a Julia macro now yields a symbol, so the separate macro gap is fixed "
        "-- DELETE this test rather than adjusting it, and remove the note from "
        "the module docstring"
    )


# ---------------------------------------------------------------------------
# The ghost
# ---------------------------------------------------------------------------

def test_the_grammar_does_not_spell_short_function_definition():
    """The root cause, against the COMPILED grammar's own symbol table.

    ⚠ This is what makes #738 a fact rather than a guess from the node name,
    and it is the same source #724's property A reads -- deliberately not
    `node-types.json`, which the grammar pack does not ship.
    """
    language = get_parser("julia").language
    kinds = {language.node_kind_for_id(i) for i in range(language.node_kind_count)}

    assert "short_function_definition" not in kinds, (
        "the grammar now emits `short_function_definition`, so #738's premise "
        "has changed: prefer the grammar's own node type over the assignment "
        "predicate and re-derive the fix"
    )
    assert "assignment" in kinds and "call_expression" in kinds


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------

def test_a_short_form_definition_is_indexed(parsed):
    """The reported case."""
    assert _named(parsed, "f"), sorted(s.name for s in parsed)


@pytest.mark.parametrize(
    "name",
    ["f", "g", "k", "square"],
    ids=["plain", "typed param", "where clause", "operator body"],
)
def test_every_short_form_shape_is_indexed(parsed, name):
    """⚠ `k` is the near-miss and the reason the predicate is not just
    `first child is a call_expression`.

    `k(x::T) where T = x` puts a `where_expression` between the assignment and
    the call, so a one-level predicate finds no `call_expression`, indexes
    nothing, and passes every other case in this list. A `where` clause is
    ordinary in generic numerical code.
    """
    assert _named(parsed, name), f"{name} is absent"


def test_a_short_form_definition_is_a_function(parsed):
    """Not `constant`, which is what an assignment-shaped node invites."""
    assert _named(parsed, "f")[0].kind == "function"


def test_a_short_form_definition_knows_its_module(parsed):
    """`Demo.f` -- the scope is already threaded for the long form."""
    assert _named(parsed, "f")[0].qualified_name == "Demo.f"


# ---------------------------------------------------------------------------
# What must NOT become a function
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["counter", "CAP", "table"],
    ids=["plain variable", "screaming variable", "dict variable"],
)
def test_an_ordinary_assignment_is_not_a_function(parsed, name):
    """The blast radius this fix must not take.

    An `assignment` is the single most common statement in Julia, and the
    predicate keys on the SHAPE of its left side. A fix that matched
    `assignment` alone would index every variable in every Julia file as a
    function -- the widening #732 took by accident in Kotlin and had to undo
    across three review rounds.
    """
    assert not [s for s in _named(parsed, name) if s.kind == "function"], (
        f"{name} is an ordinary assignment and was indexed as a function"
    )


def test_an_indexed_assignment_is_not_a_function(parsed):
    """`table["k"] = 1` is a mutation, and its left side is an index_expression."""
    assert not _named(parsed, "table[\"k\"]")
    assert not [s for s in parsed if s.kind == "function" and s.name.startswith("table")]


def test_a_lambda_bound_to_a_name_is_out_of_scope(parsed):
    """Named so the boundary is not read as an omission.

    `lambda_holder = z -> z*2` is an `assignment` with an `identifier` left side
    and an `arrow_function_expression` right side -- a variable holding a
    function, not a function definition. Julia has never indexed it, and
    widening to it is a separate decision. This asserts the CURRENT boundary so
    a future change to it is deliberate rather than incidental.
    """
    assert not [s for s in _named(parsed, "lambda_holder") if s.kind == "function"]


def test_no_declaration_is_emitted_twice(parsed):
    """Keyed on (name, line).

    The short-form predicate runs over `assignment`, which `_walk` also
    descends into, so a fix that both matched the node and recursed would emit
    twice.
    """
    seen = [(s.name, s.line) for s in parsed]

    assert len(seen) == len(set(seen)), sorted(n for n in seen if seen.count(n) > 1)


# ---------------------------------------------------------------------------
# Out of scope, stated rather than left silent
# ---------------------------------------------------------------------------

QUALIFIED = "Base.length(x) = 1\n"
CALLABLE_OBJECT = "(m::Model)(x) = x\n"


@pytest.mark.parametrize(
    "source,label",
    [(QUALIFIED, "qualified method"), (CALLABLE_OBJECT, "callable object")],
)
def test_the_unnameable_short_forms_are_declined_not_mis_named(source, label):
    """Both are short forms whose call has no simple identifier.

    `Base.length(x) = 1` puts a `field_expression` where the name would be, and
    `(m::Model)(x) = x` a `parenthesized_expression`. ⚠⚠ **The LONG form drops
    both too** -- `_func_name` looks for an `identifier` directly under the
    signature's `call_expression` and finds neither -- so extracting them here
    would make the short form index names the long form cannot, which is a new
    inconsistency rather than a fix. Declined in both, filed separately, and
    asserted here so the boundary is recorded rather than discovered.
    """
    symbols = parse_file(source, "q.jl", "julia")

    assert not [s for s in symbols if s.kind == "function"], (
        f"{label} now yields a function; if that is intended, the LONG form "
        f"must gain it in the same change or the two disagree"
    )


@pytest.mark.parametrize(
    "source,name",
    [
        ("struct Box{T} end", "Box"),
        ("struct S <: Super end", "S"),
        ("struct Q{T} <: Sup{T} end", "Q"),
        ("mutable struct M{T} end", "M"),
        ("abstract type B <: A end", "B"),
        ("abstract type C{T} <: A end", "C"),
        ("primitive type Bits 8 end", "Bits"),
    ],
    ids=["parametric", "subtyped", "both", "mutable parametric",
         "abstract subtyped", "abstract parametric", "primitive"],
)
def test_a_parametric_or_subtyped_type_head_is_a_known_gap(source, name):
    """⚠⚠ #749, found in review of this batch and deliberately NOT fixed here.

    `_struct_name` reads `type_head > identifier`, which is only the bare form.
    The grammar nests the name as soon as the head is parametric or subtyped --
    `type_head > parametrized_type_expression > identifier`, or
    `type_head > binary_expression > ...` for `<:` -- so seven of nine type
    shapes yield nothing, and the two that work are the least common in real
    Julia. `abstract_definition` shares the helper and the gap; `primitive type`
    is unnamed in the extractor entirely and is julia's one remaining row in
    #724's inventory.

    **The third instance of one shape in this function**, after short-form
    functions (#738, fixed here) and macros (#748): the node type is right and
    matched, and the NAME HELPER LOOKS AT THE WRONG DEPTH. That is why #738's
    fix is `_callable_name`, one shared resolver, rather than a fourth bespoke
    helper -- and why #749 argues for the same treatment of type heads instead
    of a fifth.

    ⚠ The sources are single-line because an empty Julia type body is valid,
    which keeps newline escapes out of a parametrize list. An earlier draft used
    `\\n` and the escapes were processed twice on the way into the file, leaving
    an unterminated string literal that failed at COLLECTION -- the documented
    hazard, and the reason code with escapes goes through an editor and an
    `ast.parse`, never a heredoc.

    This asserts the gap so the record cannot outlive it: when #749 is fixed
    these fail and name the line to delete.
    """
    symbols = parse_file(source, "t.jl", "julia")

    assert not [s for s in symbols if s.name == name], (
        f"{name} now extracts, so #749 is fixed -- DELETE this test rather than "
        f"adjusting it, and remove the note from the module docstring"
    )


def test_the_bare_type_head_control_still_extracts():
    """Non-vacuity for the seven above: the helper is not simply broken.

    If `_struct_name` returned None for everything, every absence assertion in
    the parametrized test would pass while saying nothing about the type head.
    """
    assert [(s.name, s.kind) for s in parse_file("struct P end", "t.jl", "julia")] == [("P", "type")]


def test_a_mutable_struct_still_extracts():
    """The removal of the `mutable_struct_definition` literal, proven safe.

    ⚠ That literal was the third dead one in the same tuple as #738's, and
    unlike #737's and #738's it cost nothing: the grammar has no such node kind
    and spells `mutable struct X` as an ordinary `struct_definition`, which the
    branch already matched. Removing a redundant alternative is only safe if
    something asserts the form it appeared to handle -- otherwise this is a
    cleanup that silently drops a language feature.
    """
    symbols = parse_file("mutable struct Counter\n    n::Int\nend\n", "c.jl", "julia")

    assert [(s.name, s.kind) for s in symbols] == [("Counter", "type")], symbols
