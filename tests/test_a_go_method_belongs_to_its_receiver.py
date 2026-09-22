"""A Go method belongs to its receiver, and a struct's fields are indexed (#778).

Go was the one member-ownership issue left out of #788's family, and for a
reason: the other five qualified their members correctly and lost only the
`parent`, while Go's method was not qualified AT ALL. `func (a *Audit) RunIt()`
came back as `RunIt`, owner unknown, because Go attaches a method to a RECEIVER
rather than nesting it inside the type, and the spec walk never reads the
receiver. There is no enclosing node to be a parent.

Its struct fields were absent outright, the same second mechanism #774, #776,
#779 and #782 carried.

⚠⚠ **This needs a SECOND PASS and that is the whole difficulty.** Go lets a
method be declared before its type, in the same file or a different one, so a
single walk that resolves a receiver as it meets it answers `unknown` for every
method that comes first. The fix resolves receivers after the walk, against the
types the walk found.

⚠⚠ **Ids MOVE for every Go method**, because `make_symbol_id` is keyed on the
qualified name and `RunIt` becomes `Audit.RunIt`. That is the cost of the fix
and it is stated in the CHANGELOG; the other five languages moved no id,
because they were already qualified.

⚠ **What is deliberately NOT here: a receiver whose type lives in another
file.** Go permits it across a package, this parser sees one file, and a method
whose type it cannot find keeps today's answer rather than guessing. That is
absence, not fabrication, and it is pinned below.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.symbols import STATE_KINDS


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """This box disables some languages in its own config; the gate is function-local."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


_AUDIT = (
    "package a\n"
    "type Audit struct {\n"
    "\tTally int\n"
    "\tlimit int\n"
    "}\n"
    "func (a *Audit) RunIt() int { return 1 }\n"
    "func (a Audit) Peek() int { return 2 }\n"
    "func Free() int { return 3 }\n"
)


def _syms(source: str, filename: str = "a.go"):
    return parse_file(source, filename, "go")


def _by_name(source: str, filename: str = "a.go"):
    out: dict[str, list] = {}
    for symbol in _syms(source, filename):
        out.setdefault(symbol.name, []).append(symbol)
    return out


def test_a_struct_field_is_indexed_and_owned():
    """The absent half. Both fields, exported and not."""
    found = _by_name(_AUDIT)
    owner = found["Audit"][0]
    assert owner.kind == "type"
    for name in ("Tally", "limit"):
        hits = found.get(name) or []
        assert hits, f"{name} was not extracted"
        assert len(hits) == 1, [(s.name, s.kind) for s in hits]
        assert hits[0].kind == "field", hits[0].kind
        assert hits[0].parent == owner.id, (name, hits[0].parent)
        assert hits[0].qualified_name == f"Audit.{name}"


@pytest.mark.parametrize("method", ["RunIt", "Peek"])
def test_a_method_is_qualified_by_its_receiver_and_owned_by_it(method):
    """⚠ Both receiver forms. A pointer receiver and a value receiver name the
    same type, and a fix that read only `type_identifier` would miss the
    pointer form, which is the commoner of the two in real Go."""
    found = _by_name(_AUDIT)
    owner = found["Audit"][0]
    hit = found[method][0]
    assert hit.kind == "method", hit.kind
    assert hit.qualified_name == f"Audit.{method}", hit.qualified_name
    assert hit.parent == owner.id, (method, hit.parent, owner.id)


def test_a_function_with_no_receiver_is_untouched():
    """The boundary. A plain `func` belongs to no type and must keep a bare
    name and no parent -- the asymmetry #780/#783 kept for constants."""
    hit = _by_name(_AUDIT)["Free"][0]
    assert hit.kind == "function"
    assert hit.qualified_name == "Free"
    assert hit.parent is None


def test_a_method_declared_before_its_type_still_resolves():
    """⚠⚠ The reason this cannot be a single-pass fix, asserted rather than
    left to the implementation.

    Go does not require a type to be declared before a method on it. A walk
    that resolved receivers as it met them would answer `unknown` here and
    look correct on every fixture written in the other order.
    """
    source = (
        "package a\n"
        "func (a *Later) RunIt() int { return 1 }\n"
        "type Later struct {\n"
        "\tN int\n"
        "}\n"
    )
    found = _by_name(source, "later.go")
    owner = found["Later"][0]
    assert found["RunIt"][0].qualified_name == "Later.RunIt"
    assert found["RunIt"][0].parent == owner.id
    assert found["N"][0].parent == owner.id


def test_a_non_struct_type_still_owns_its_methods():
    """⚠ A receiver is not always a struct. `type ID int` has no fields and
    still has methods, and the type_spec carries TWO `type_identifier`
    children -- the name and the underlying type -- so a fix that took the
    wrong one would name the owner `int`."""
    source = (
        "package a\n"
        "type ID int\n"
        "func (i ID) S() string { return \"\" }\n"
    )
    found = _by_name(source, "id.go")
    owner = found["ID"][0]
    assert found["S"][0].qualified_name == "ID.S"
    assert found["S"][0].parent == owner.id
    assert "int" not in found, "the underlying type was indexed as a symbol"


def test_a_generic_receiver_resolves_to_its_base_type():
    """⚠ `func (b *Box[T]) Get()` wraps the name in `pointer_type >
    generic_type >  type_identifier`, two levels deeper than the plain form,
    and the type's own spec carries a `type_parameter_list` before its body."""
    source = (
        "package a\n"
        "type Box[T any] struct {\n"
        "\tv T\n"
        "}\n"
        "func (b *Box[T]) Get() T { return b.v }\n"
    )
    found = _by_name(source, "box.go")
    owner = found["Box"][0]
    assert found["Get"][0].qualified_name == "Box.Get"
    assert found["Get"][0].parent == owner.id
    assert found["v"][0].parent == owner.id


def test_one_declaration_naming_two_fields_yields_two():
    """`X, Y int` is two members. Reading only the first indexes half a line --
    the rule the Apex branch states and Groovy had to be fixed for."""
    source = "package a\ntype A struct {\n\tX, Y int\n}\n"
    found = _by_name(source, "two.go")
    owner = found["A"][0]
    for name in ("X", "Y"):
        assert found[name][0].kind == "field", name
        assert found[name][0].parent == owner.id, name


def test_a_receiver_whose_type_is_not_in_this_file_keeps_todays_answer():
    """⚠⚠ The limit, pinned because the alternative is fabrication.

    Go allows a method's receiver type to be declared in another file of the
    same package. This parser sees one file, so the type is genuinely not
    found, and inventing an owner id for it would be worse than leaving the
    method unqualified. Absence is the safe direction.

    ⚠ A later cross-file resolution must move this line.
    """
    source = "package a\nfunc (o *Elsewhere) RunIt() int { return 1 }\n"
    hit = _by_name(source, "orphan.go")["RunIt"][0]
    assert hit.kind == "method"
    assert hit.qualified_name == "RunIt"
    assert hit.parent is None


def test_an_embedded_field_is_named_after_its_type():
    """⚠ An embedded field has NO `field_identifier` -- the grammar gives only
    the type -- and Go's own selector for it is the type's base name, so
    `a.Reader` is how it is read. Naming it that way is what makes the member
    findable; skipping it would report the struct as having one member when it
    has two.
    """
    source = (
        "package a\n"
        "type A struct {\n"
        "\tio.Reader\n"
        "\tN int\n"
        "}\n"
    )
    found = _by_name(source, "emb.go")
    owner = found["A"][0]
    assert found["Reader"][0].kind == "field"
    assert found["Reader"][0].parent == owner.id
    assert found["Reader"][0].qualified_name == "A.Reader"
    assert found["N"][0].parent == owner.id


def test_a_function_local_type_does_not_steal_the_package_types_members():
    """⚠⚠ The shadowing case, and it is why nothing here joins on a NAME.

    A `type` inside a function body is a different type that happens to share
    a name. An owner table keyed on the bare name gave the function-local
    `Config` the package-level `Config`'s method and field: `Use` came back as
    `helper.Config.Use` with a wrong owner, a wrong qualified name and a wrong
    id, the local type gained a `Real` it does not declare, and the real type
    was left reporting ZERO members -- the exact symptom this issue fixes,
    reintroduced one scope over.

    ⚠ It is worse than the defect it replaced: before #778 the same file
    answered `Use` unqualified with no parent, which is an honest absence. A
    wrong owner is fabrication, and this file's own rule is absence over
    fabrication.
    """
    source = (
        "package a\n"
        "type Config struct {\n\tReal int\n}\n"
        "func (c *Config) Use() int { return c.Real }\n"
        "func helper() {\n"
        "\ttype Config struct{ Fake int }\n"
        "\t_ = Config{}\n"
        "}\n"
    )
    found = _by_name(source, "shadow.go")
    package_type = next(s for s in found["Config"] if s.parent is None)
    assert found["Use"][0].qualified_name == "Config.Use"
    assert found["Use"][0].parent == package_type.id
    assert found["Real"][0].qualified_name == "Config.Real"
    assert found["Real"][0].parent == package_type.id
    # The local type declares `Fake` and owns nothing of the package type's.
    assert "Fake" not in found, "a function-local struct's fields were adopted"


def test_two_methods_declared_on_one_line_both_resolve():
    """⚠⚠ A LINE IS NOT AN IDENTITY. Keying methods on the start LINE
    collapsed these two: the second write won, `Y` resolved and `X` stayed
    bare. gofmt splits the line, which is precisely why such a defect survives
    review and reaches the one file nobody formatted."""
    source = "package a\ntype A int\nfunc (a A) X() {}; func (a A) Y() {}\n"
    found = _by_name(source, "oneline.go")
    owner = found["A"][0]
    for name in ("X", "Y"):
        assert found[name][0].qualified_name == f"A.{name}", name
        assert found[name][0].parent == owner.id, name


def test_a_grouped_type_declaration_gives_each_types_fields_to_that_type():
    """Each spec in a grouped block owns its own members (#817).

    ⚠⚠ **This test asserted the opposite until #817, and it was the OLD gap's
    witness rather than a guard on this pass** (Practice 9). A grouped
    `type ( A ...; B ... )` yielded ONE symbol for the whole declaration, so
    this pass had to refuse the second spec by name -- B stayed unindexed,
    which is what it already was, and what must not happen was A growing a
    member it does not declare. Both halves are live now: B is a type, and
    `Theirs` is B's.

    The refusal it pinned is gone from the source, so restoring the assertion
    would pin a state nothing produces. `harness/retired.json` carries the
    lesson and names this as the replacement.
    """
    source = (
        "package a\n"
        "type (\n"
        "\tA struct{ Mine int }\n"
        "\tB struct{ Theirs int }\n"
        ")\n"
    )
    found = _by_name(source, "grouped.go")
    assert found["Mine"][0].parent == found["A"][0].id
    assert found["Theirs"][0].parent == found["B"][0].id
    assert found["Theirs"][0].qualified_name == "B.Theirs"


def test_a_nested_anonymous_struct_is_not_descended_and_that_is_a_limit():
    """⚠ The limit, pinned rather than claimed. Only the outer
    `field_declaration_list` is read, so an anonymous struct inside a field
    contributes the field and not its own members. That under-reports in the
    same direction the pre-#778 tree did; a later change must move this line
    rather than discover it."""
    source = "package a\ntype A struct {\n\tInner struct{ Deep int }\n}\n"
    found = _by_name(source, "nested.go")
    assert found["Inner"][0].parent == found["A"][0].id
    assert "Deep" not in found


def test_a_local_variable_is_not_a_member():
    """The channel next door. A `var` inside a function belongs to no type, and
    a fix reaching too widely would give it one."""
    source = (
        "package a\n"
        "func f() {\n"
        "\tvar local int = 1\n"
        "\t_ = local\n"
        "}\n"
    )
    hits = [s for s in _syms(source, "loc.go") if s.name == "local"]
    for hit in hits:
        assert hit.parent is None, hit.parent
        assert hit.kind not in ("field", "property"), hit.kind


def test_a_package_level_var_and_const_are_not_members():
    """⚠ Go's `var` and `const` channels already emit at package scope (#731,
    #763). They belong to no type and this change must not adopt them."""
    source = (
        "package a\n"
        "var Total int = 0\n"
        "const Limit = 3\n"
        "type A struct {\n\tN int\n}\n"
    )
    found = _by_name(source, "pkg.go")
    for name in ("Total", "Limit"):
        for hit in found.get(name, []):
            assert hit.parent is None, (name, hit.parent)
            assert hit.kind not in ("field", "property"), (name, hit.kind)
    assert found["N"][0].parent == found["A"][0].id


def test_a_kotlin_extension_function_is_not_swept_into_this_and_that_is_a_ruling():
    """⚠⚠ The boundary of the receiver mechanism, pinned so crossing it is
    deliberate.

    Scanning for other languages that attach a callable to a type declared
    elsewhere found exactly one more: Kotlin's `fun Audit.r()` comes back as a
    top-level `function` named `r`, with no qualification and no parent.

    It is deliberately left alone, because the two are not the same thing. A Go
    method IS the type's method -- it can reach unexported state and is part of
    the type's interface. A Kotlin extension is a top-level function resolved
    statically, cannot see private members, and is not inherited; calling it a
    member would claim more than the language does.

    ⚠ Swift disagrees with Kotlin here and already ships that way: `extension
    Audit { func r() }` nests in the grammar, so it is owned. That
    inconsistency is real, predates this change, and is a language-semantics
    decision rather than a parser defect -- which is why it is pinned rather
    than silently harmonised in a PR about Go.
    """
    kotlin = parse_file(
        "class Audit { var t = 0 }\nfun Audit.r(): Int = 1\n", "A.kt", "kotlin"
    )
    extension = [s for s in kotlin if s.name == "r"]
    assert len(extension) == 1
    assert extension[0].kind == "function"
    assert extension[0].qualified_name == "r"
    assert extension[0].parent is None


def test_the_struct_fields_are_the_only_new_state_kinds():
    """Non-vacuity against over-reach: exactly the declared members, nothing
    the walk picked up on the way."""
    state = sorted(
        (s.name, s.kind) for s in _syms(_AUDIT) if s.kind in STATE_KINDS
    )
    assert state == [("Tally", "field"), ("limit", "field")], state
