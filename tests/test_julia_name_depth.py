"""#748 + #749: a Julia name helper that looks at the wrong DEPTH.

Both issues are the same defect in the same function, one node type apart, and
neither is a ghost: `macro_definition`, `struct_definition` and
`abstract_definition` are all in the compiled grammar and all matched by
`_parse_julia_symbols`. What fails is the NAME lookup, which reads a depth the
grammar only uses for the simplest spelling of each form.

    macro sayhello(x)          signature > call_expression > identifier
    struct Box{T}              type_head > parametrized_type_expression > identifier
    struct S <: Super          type_head > binary_expression > identifier
    struct Q{T} <: Sup{T}      type_head > binary_expression >
                                   parametrized_type_expression > identifier

`_direct_name` (macros) took the first DIRECT identifier child and `_struct_name`
read `type_head > identifier`, so every shape above yielded nothing while the
bare form worked.

⚠⚠ **This is the THIRD and FOURTH instance of one shape in one function**, after
short-form functions (#738: the name is under `call_expression`, the helper
wanted a `signature`). #738's answer was `_callable_name`, ONE resolver asked by
both the long and short forms rather than a second bespoke helper. These two
issues are fixed the same way -- `_type_head_name`, asked by every type form --
because a fifth bespoke helper is how the first four happened.

⚠ Julia is the language where this is expensive rather than cosmetic. A macro is
a first-class part of a public API (`@test`, `@inbounds`, `@view`), and a
parametric or subtyped type head is the ORDINARY case in numerical and
interface-style code -- so the shapes that failed were the common ones and the
two that worked were the rare ones.
"""

import ast
import inspect
import pathlib

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """⚠ Patch `jcodemunch_mcp.config`, never the parser module.

    `parse_file`'s import of the language gate is function-local, so patching
    the name in `extractor` resolves against the wrong module and does nothing
    (the `cli/policy.py` trap). This box disables several languages in config,
    which is why a bare run here reports zero symbols and looks like a grammar
    defect.
    """
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _symbols(source: str):
    return {(s.name, s.kind) for s in parse_file(source, "a.jl", "julia")}


# --------------------------------------------------------------------------
# #748: macros
# --------------------------------------------------------------------------

#: Every macro spelling, with the name each one declares.
#:
#: ⚠ The no-argument form is here because it is the shape a bespoke fix is most
#: likely to miss: `macro probe() ... end` still nests its name under
#: `signature > call_expression`, so a helper that special-cased "a macro with
#: arguments" would pass the motivating case and fail this one.
_MACRO_SHAPES = [
    ("macro probe(name)\n  return 1\nend\n", "an argument"),
    ("macro probe()\n  return 1\nend\n", "no arguments"),
    ("macro probe(a, b)\n  return 1\nend\n", "two arguments"),
    ("macro probe(args...)\n  return 1\nend\n", "a splat"),
]


@pytest.mark.parametrize("source,shape", _MACRO_SHAPES, ids=[s for _src, s in _MACRO_SHAPES])
def test_a_macro_is_a_symbol(source, shape):
    """#748: a macro yields a symbol, whatever its argument list looks like."""
    assert ("probe", "function") in _symbols(source), (
        f"a Julia macro with {shape} yields {sorted(_symbols(source))}. The name "
        f"is at `signature > call_expression > identifier`, one level below a "
        f"direct child (#748)."
    )


def test_a_macro_and_a_function_are_named_by_the_same_path():
    """⚠⚠ The reason this is not a fourth helper: the shapes are IDENTICAL.

    A macro's `signature` nests its name exactly where a function's does, so the
    fix is to ask the existing resolver rather than to write a macro-shaped one.
    Asserted on the PRODUCT, because "they look the same in the grammar" is a
    claim about a tree I read once and this is a claim that keeps being true.
    """
    macro = _symbols("macro probe(x)\n  return 1\nend\n")
    function = _symbols("function probe(x)\n  return 1\nend\n")

    assert ("probe", "function") in macro
    assert ("probe", "function") in function


# --------------------------------------------------------------------------
# #749: type heads
# --------------------------------------------------------------------------

#: Every type-head shape, with the name it declares.
#:
#: ⚠⚠ Seven of these nine yielded NOTHING before the fix, and the two that
#: worked (`struct P`, `abstract type A end`) are the least common spellings in
#: real Julia. A fixture holding only those two would have reported a healthy
#: language, which is the [[a-fixture-that-cannot-express-the-reported-shape-cannot-fail-on-it]]
#: rule in the shape it actually takes: the fixture was not wrong, it was narrow.
_TYPE_SHAPES = [
    ("struct P\nend\n", "P", "bare"),
    ("struct Box{T}\n  x::T\nend\n", "Box", "parametric"),
    ("struct S <: Super\nend\n", "S", "subtyped"),
    ("struct Q{T} <: Sup{T}\nend\n", "Q", "parametric and subtyped"),
    ("mutable struct M{T}\n  x::T\nend\n", "M", "mutable parametric"),
    ("mutable struct N <: Super\nend\n", "N", "mutable subtyped"),
    ("abstract type A end\n", "A", "abstract bare"),
    ("abstract type B <: A end\n", "B", "abstract subtyped"),
    ("abstract type C{T} <: A end\n", "C", "abstract parametric and subtyped"),
]


@pytest.mark.parametrize(
    "source,name,shape",
    _TYPE_SHAPES,
    ids=[shape for _s, _n, shape in _TYPE_SHAPES],
)
def test_a_type_head_is_named_at_any_depth(source, name, shape):
    """#749: the declared type is a symbol whether or not its head is wrapped."""
    assert (name, "type") in _symbols(source), (
        f"a {shape} Julia type head yields {sorted(_symbols(source))}, not "
        f"{name!r}. The name is nested under `parametrized_type_expression` "
        f"and/or the `<:` `binary_expression` (#749)."
    )


def test_the_supertype_is_not_indexed_as_a_declaration():
    """⚠⚠ The failure mode a `<:` unwrap invites, and the one that would be silent.

    `struct S <: Super` mentions TWO identifiers and declares ONE. A resolver
    that collected identifiers instead of taking the left operand would index
    `Super` as a type declared here -- a symbol pointing at a definition that
    lives in another file, which is worse than the absence it replaced because
    nothing about it looks wrong in a result list.
    """
    symbols = _symbols("struct S <: Super\nend\n")

    assert ("S", "type") in symbols
    assert not [s for s in symbols if s[0] == "Super"], (
        f"`Super` is indexed as a declaration by `struct S <: Super`, where it "
        f"is a REFERENCE: {sorted(symbols)}"
    )


def test_a_combined_head_names_the_type_and_not_its_supertype():
    """⚠⚠ The row a mutation pass added, because the two above CANNOT see the
    failure they were written for.

    Planting the obvious wrong fix -- "take the first identifier anywhere under
    the head", breadth-first -- left both of them GREEN. On `struct S <: Super`
    and `struct Box{T}` a breadth-first walk reaches the left operand first and
    happens to be right, so the resolver fabricates nothing and the rows pass.

    It is only the COMBINED head that separates the two rules. In
    `abstract type C{T} <: A end` the name is one level deeper than the
    supertype -- `binary_expression > parametrized_type_expression > identifier`
    against `binary_expression > identifier` -- so a breadth-first walk reaches
    `A` first and publishes the SUPERTYPE as the declaration. Measured: the
    planted fix failed one row, and it was this shape's positive assertion, not
    either guard named after the hazard.

    ⚠ That is [[a-fixture-that-cannot-express-the-reported-shape-cannot-fail-on-it]]
    inside the guard written against the hazard: both rows describe the right
    property and neither can be answered wrongly by the shapes they use.
    """
    symbols = _symbols("abstract type C{T} <: A end\n")

    assert ("C", "type") in symbols, (
        f"the combined head `C{{T}} <: A` yields {sorted(symbols)}, not `C`"
    )
    assert not [s for s in symbols if s[0] == "A"], (
        f"the SUPERTYPE `A` is indexed as the declaration of "
        f"`abstract type C{{T}} <: A end`: {sorted(symbols)}. A resolver that "
        f"searches for an identifier instead of taking the left operand reaches "
        f"`A` first here, and is correct on every simpler shape."
    )


def test_a_type_parameter_is_not_indexed_as_a_declaration():
    """The same asymmetry one wrapper over: `{T}` binds T for the head, and a
    walk that took every identifier would publish `T` as a type of its own.
    """
    symbols = _symbols("struct Box{T}\n  x::T\nend\n")

    assert ("Box", "type") in symbols
    assert not [s for s in symbols if s[0] == "T"], (
        f"the type PARAMETER `T` is indexed as a declaration: {sorted(symbols)}"
    )


# --------------------------------------------------------------------------
# Blast radius: what must NOT start extracting
# --------------------------------------------------------------------------

#: Ordinary Julia that must be unaffected by a deeper name walk.
#:
#: ⚠ #738's note records the shape of this risk from the other side: matching
#: `assignment` alone would have indexed every variable in every Julia file as a
#: function. A name resolver that descends further is the same hazard pointing
#: down instead of out.
_UNCHANGED = [
    ("x = 1\n", "an ordinary assignment"),
    ("a.b = 1\n", "a field assignment"),
    ("v[i] = 1\n", "an index assignment"),
    ("a, b = 1, 2\n", "a tuple assignment"),
    ("x::Int = 5\n", "a typed variable"),
]


@pytest.mark.parametrize("source,shape", _UNCHANGED, ids=[s for _src, s in _UNCHANGED])
def test_an_ordinary_binding_is_not_a_type_or_a_macro(source, shape):
    """Nothing here declares a type or a callable, so nothing here is one."""
    kinds = {k for _n, k in _symbols(source)}

    assert not (kinds & {"type", "function"}), (
        f"{shape} yields {sorted(_symbols(source))}, which includes a type or a "
        f"function. A deeper name walk must not widen WHICH nodes are matched."
    )


# --------------------------------------------------------------------------
# The mechanism: one resolver, not a fifth bespoke helper
# --------------------------------------------------------------------------

def _julia_source() -> str:
    from jcodemunch_mcp.parser import extractor

    return inspect.getsource(extractor._parse_julia_symbols)


def _julia_tree() -> ast.FunctionDef:
    # ⚠ NOT `inspect.cleandoc`: it re-indents relative to the second line and
    # turns a top-level function into an IndentationError. `_parse_julia_symbols`
    # is module-level, so its source parses as written.
    return ast.parse(_julia_source()).body[0]


def _name_helpers() -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in _julia_tree().body
        if isinstance(node, ast.FunctionDef)
    }


def _calls_in_branch_for(node_type: str) -> set[str]:
    """Every function called inside the walker branch that handles `node_type`.

    ⚠⚠ **BRANCH-scoped, because function-scoped could not see the defect.**
    The first version of this file asked "is `_type_head_name` defined, and does
    `_walk` call it anywhere" -- which stays GREEN when one branch keeps the
    shared resolver and the other is given a fifth bespoke helper, i.e. green
    against exactly the duplication the fix exists to end. Measured by the
    reviewer against that mutant, not inferred.
    """
    calls: set[str] = set()
    for node in ast.walk(_julia_tree()):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        constants = []
        for comparator in node.test.comparators:
            if isinstance(comparator, ast.Constant):
                constants.append(comparator.value)
            elif isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                # ⚠ `elif node.type in ("struct_definition", "abstract_definition")`
                # is a legal spelling of the same branch, and reading only
                # `ast.Constant` reported "no branch found" for a branch sitting
                # in front of the reader. Fail-closed, but with a message that
                # sends them hunting. Found in review.
                constants.extend(
                    e.value for e in comparator.elts if isinstance(e, ast.Constant)
                )
        if node_type not in constants:
            continue
        for statement in node.body:
            for inner in ast.walk(statement):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
                    calls.add(inner.func.id)
    return calls


#: What a type branch is allowed to call, beyond the shared resolver.
#:
#: ⚠ `_direct_name` is here because `abstract_definition` has fallen back to it
#: since before #749 and nothing establishes that every spelling of every type
#: form builds a `type_head`. It is a FALLBACK -- second in the or-chain, after
#: the resolver -- which is the difference between a safety net and a bypass.
#:
#: ⚠⚠ An allowlist grows, and this project has watched one become an escape
#: hatch (`_HELPER_LITERAL_EXCEPTIONS`, deleted by the test written to keep it
#: honest). Adding a name here is the decision this guard exists to force.
_NAME_HELPERS_A_TYPE_BRANCH_MAY_ASK = frozenset({"_type_head_name", "_direct_name"})


def test_every_type_form_asks_the_same_resolver():
    """⚠⚠ The point of the fix, asserted as a PROPERTY rather than as prose.

    `abstract_definition` and `struct_definition` had a shared helper before
    this change and shared its defect, which is the good failure mode: one fix
    reached both. A future `primitive_definition` (julia's one remaining
    unnamed form in #724's inventory) must join them rather than arrive with a
    sixth helper, and this names the resolver it has to ask.

    ⚠ A guard over the SOURCE, not over behaviour, because the behavioural
    tests above pass equally well against five copies of the same walk -- and
    five copies is exactly how this function reached four name helpers.

    ⚠⚠ **Asserted PER BRANCH, and the first version was not.** Asking whether
    `_type_head_name` exists and whether `_walk` calls it somewhere is satisfied
    by one branch keeping the resolver while the other gets a fifth bespoke
    helper -- the reviewer planted `_abstract_head_name` on `abstract_definition`
    and this test stayed green, with every behavioural row green too, because a
    correct duplicate is still correct. A guard that cannot fail on the
    duplication it names is the [[a-guard-covered-only-by-positive-tests-can-be-deleted]]
    shape, and it is the second instance of it in this file (`mutation.md` row 4
    was the first).
    """
    helpers = _name_helpers()
    assert "_type_head_name" in helpers, (
        f"no `_type_head_name` resolver in `_parse_julia_symbols`; its inner "
        f"helpers are {sorted(helpers)}. #749's fix is one resolver for every "
        f"type form, not a fifth bespoke helper."
    )

    for node_type in ("struct_definition", "abstract_definition"):
        calls = _calls_in_branch_for(node_type)
        assert calls, f"no `{node_type}` branch found in `_parse_julia_symbols`"
        assert "_type_head_name" in calls, (
            f"the `{node_type}` branch names its type with {sorted(calls)} and "
            f"does not ask `_type_head_name`. Every type form asks ONE resolver "
            f"(#749); a second helper that happens to be correct today is how "
            f"this function reached four of them."
        )
        # ⚠⚠ An ALLOWLIST, because "calls the resolver" is not "is named by
        # it": `_abstract_head_name(node) or _type_head_name(node)` satisfies the
        # assertion above while the bespoke helper decides every name and the
        # resolver is dead in the or-chain -- measured in review, and it is ONE
        # reordering away from the real code, which is already an or-chain.
        unexpected = calls - _NAME_HELPERS_A_TYPE_BRANCH_MAY_ASK
        assert not unexpected, (
            f"the `{node_type}` branch also calls {sorted(unexpected)}. A helper "
            f"ahead of `_type_head_name` in an or-chain takes every name and "
            f"leaves the resolver unreachable, so the shared-resolver property "
            f"is a spelling rather than a fact. Add the name here only with the "
            f"reason it is not a fifth bespoke helper."
        )


def test_the_macro_branch_stopped_asking_for_a_direct_child():
    """The #748 half of the same property.

    ⚠ Asserted POSITIVELY -- the branch must ask `_func_name` -- and the first
    version did not match this docstring: it banned the name `_direct_name`,
    which a bespoke `_macro_name` helper would have satisfied while reinstating
    the defect. Banning a name is the thing the sentence above says to avoid,
    written one line below it. Found in review.
    """
    macro_calls = _calls_in_branch_for("macro_definition")

    assert macro_calls, "no `macro_definition` branch found in `_parse_julia_symbols`"
    assert "_func_name" in macro_calls, (
        f"the macro branch names its symbol with {sorted(macro_calls)} rather "
        f"than `_func_name`. A macro's `signature` nests its name exactly where "
        f"a function's does, so the two forms are ONE question (#748); "
        f"`_direct_name` asks for a direct identifier child, which a macro does "
        f"not have."
    )


def test_a_primitive_type_is_a_known_separate_gap():
    """⚠⚠ NOT fixed here, and the reason is the defect CLASS, not the effort.

    `primitive type Bits 8 end` is `primitive_definition`, a node type
    `_parse_julia_symbols` does not match AT ALL. That is #698's class -- a form
    no extractor names -- while #748 and #749 are the name-depth class, where
    the node is matched and the lookup is too shallow. Fixing it here would
    close an inventory row inside a PR about a different mechanism, and the
    inventory is the tracker: julia's row stays until a change whose subject it
    is removes it.

    ⚠ Asserts the gap so the record cannot outlive the defect -- when
    `primitive_definition` starts extracting, this test fails and names the line
    to delete.
    """
    assert not [s for s in _symbols("primitive type Bits 8 end\n") if s[0] == "Bits"], (
        "a Julia `primitive type` now yields a symbol: delete this test, and "
        "remove julia's `primitive_definition` row from "
        "tests/fixtures/grammar_declaration_inventory.json"
    )


def test_the_retired_gap_tests_are_gone_from_the_short_function_file():
    """⚠ The lifecycle both issues' gap tests promised, asserted rather than trusted.

    `test_julia_short_functions.py` carried `test_a_macro_is_a_known_separate_gap`
    and `test_a_parametric_or_subtyped_type_head_is_a_known_gap`, each written to
    FAIL when its defect was fixed and to name the line to delete. They are
    deleted, with `harness/retired.json` entries naming these replacements --
    and this check exists because a gap test that outlives its gap is the exact
    record-outlives-its-subject shape those tests were written to prevent.
    """
    path = pathlib.Path(__file__).with_name("test_julia_short_functions.py")
    text = path.read_text(encoding="utf-8")

    for retired in (
        "def test_a_macro_is_a_known_separate_gap",
        "def test_a_parametric_or_subtyped_type_head_is_a_known_gap",
    ):
        assert retired not in text, (
            f"{retired} still exists in {path.name}, asserting a gap that is "
            f"fixed. Delete it (its `harness/retired.json` entry is already "
            f"written)."
        )
