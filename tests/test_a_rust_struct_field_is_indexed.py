"""A Rust struct's fields are indexed and owned (#786).

The last cell of the member-kind audit, and the one held back from #818 on
purpose. The other three spec-driven languages needed a channel wired up;
Rust needed its ORACLE taught first.

⚠⚠ **`fidelity.rust.extra` gates at 0 and is computed by NAME over every
symbol we emit, with no kind filter** (`benchmarks/rust_fidelity/run_fidelity.py`
`extra = sorted(n for n in jcm_by_name if n not in known)`). The `syn` oracle
carried no `field` def at all, so emitting struct fields would have failed the
fast tier on correct extraction. The alternatives were to exempt the kind --
shipping the extraction unscored in BOTH directions, which is the macro ceiling
`benchmarks/rust_fidelity/README.md` already warns about -- or to teach the
oracle. The oracle was taught.

⚠ The oracle's own note said fields are omitted because none of them "binds a
name another module can reach". That was written when we emitted none, and it
was never quite right: a `pub` field is reachable as `s.field`. The
justification is replaced rather than deleted.

What this covers and what it deliberately does not:

- A NAMED struct field and a NAMED union field are members. The grammar spells
  them identically (`field_declaration_list > field_declaration >
  field_identifier`) and so does `syn`.
- A TUPLE struct's fields have no identifier at all
  (`ordered_field_declaration_list`), so there is no name to index. Both sides
  skip them by construction rather than by a rule.
- An ENUM VARIANT's fields are pinned as absent. A variant is not a struct, the
  oracle omits variants deliberately, and adopting their fields without
  adopting the variants would be a half-answer.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """This box disables some languages in its own config; the gate is function-local."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


_AUDIT = (
    "struct Audit {\n"
    "    tally: i32,\n"
    "    pub limit: u8,\n"
    "}\n"
    "impl Audit {\n"
    "    const LIMIT: i32 = 3;\n"
    "    fn run_it(&self) -> i32 { 1 }\n"
    "}\n"
)


def _by_name(source: str, filename: str = "p.rs") -> dict[str, list]:
    out: dict[str, list] = {}
    for symbol in parse_file(source, filename, "rust"):
        out.setdefault(symbol.name, []).append(symbol)
    return out


@pytest.mark.parametrize("name", ["tally", "limit"])
def test_a_rust_struct_field_is_indexed_and_owned(name):
    """⚠ Both visibilities. `pub limit` carries a `visibility_modifier` child
    that `tally` does not, and a reader that took the first child as the name
    would answer `pub` for one of them."""
    found = _by_name(_AUDIT)
    owner = found["Audit"][0]
    hits = found.get(name) or []
    assert hits, f"{name} was not extracted"
    assert len(hits) == 1, [(s.name, s.kind) for s in hits]
    assert hits[0].kind == "field", hits[0].kind
    assert hits[0].parent == owner.id, (name, hits[0].parent)
    assert hits[0].qualified_name == f"Audit.{name}"


def test_the_impl_members_are_unchanged():
    """Non-regression: the two cells Rust already answered correctly. An
    `impl`'s `const` and `fn` reach their owner by a different path, and the
    field channel must not disturb them."""
    found = _by_name(_AUDIT)
    owner = found["Audit"][0]
    assert found["LIMIT"][0].kind == "constant"
    assert found["LIMIT"][0].parent == owner.id
    assert found["run_it"][0].kind == "method"
    assert found["run_it"][0].parent == owner.id


def test_a_named_union_field_is_a_member_too():
    """⚠ The grammar spells a union's members with the SAME nodes as a
    struct's, and so does `syn`. Excluding them would need an extra condition
    written against the spelling `union_item`, for no reason anyone could
    state."""
    source = "union U {\n    a: u8,\n    b: i32,\n}\n"
    found = _by_name(source, "u.rs")
    owner = found["U"][0]
    for name in ("a", "b"):
        assert found[name][0].kind == "field", name
        assert found[name][0].parent == owner.id, name
        assert found[name][0].qualified_name == f"U.{name}"


def test_a_tuple_struct_contributes_no_member():
    """⚠ Not a rule, a consequence: a tuple struct's fields are an
    `ordered_field_declaration_list` with no `field_identifier` anywhere, so
    there is no name to index. `syn` reports `ident: None` for the same reason,
    which is why both sides agree without either being told to."""
    found = _by_name("struct Wrap(u8, i32);\n", "w.rs")
    assert "Wrap" in found
    assert [n for n in found if n != "Wrap"] == []


def test_an_enum_variants_fields_are_absent_and_that_is_a_limit():
    """⚠⚠ Pinned, because the grammar makes this the easy mistake: an enum
    variant holds a `field_declaration_list` exactly as a struct does, so a
    channel gated on the NODE TYPE alone adopts `inner` as a member of `E`.

    It is excluded because a variant is not a struct and the oracle omits
    variants deliberately — indexing a variant's fields while the variant
    itself is absent would be a half-answer, and the two decisions belong
    together. A later change moves this line.
    """
    source = "enum E {\n    A,\n    B { inner: u8 },\n}\n"
    found = _by_name(source, "e.rs")
    assert "E" in found
    assert "inner" not in found, [(s.kind, s.parent) for s in found.get("inner", [])]


def test_a_struct_declared_inside_a_function_owns_its_own_fields():
    """⚠ Rust allows a struct in a function body and we already index it,
    qualified by the function (`outer.Inner`). Its field must follow the same
    qualification rather than being published bare or attributed to `outer`."""
    source = "fn outer() {\n    struct Inner { x: u8 }\n}\n"
    found = _by_name(source, "n.rs")
    inner = found["Inner"][0]
    assert inner.qualified_name == "outer.Inner"
    assert found["x"][0].kind == "field"
    assert found["x"][0].parent == inner.id
    assert found["x"][0].qualified_name == "outer.Inner.x"


def test_a_generic_struct_field_is_owned_by_the_base_name():
    """`struct G<T> { item: T }` -- the type parameter list sits between the
    name and the body, so a walk that took the node after the name would find
    `<T>` rather than the fields."""
    found = _by_name("struct G<T> {\n    item: T,\n}\n", "g.rs")
    assert found["item"][0].parent == found["G"][0].id
    assert found["item"][0].qualified_name == "G.item"


def test_a_local_binding_is_not_a_member():
    """The channel next door. A `let` inside a function is not a field, and
    Rust spells it `let_declaration` -- stated rather than assumed, because
    every other language in this family needed the scope gate."""
    source = "fn f() {\n    let local = 1;\n    let _ = local;\n}\n"
    for hit in _by_name(source, "l.rs").get("local", []):
        assert hit.kind != "field", hit.kind
        assert hit.parent is None, hit.parent
