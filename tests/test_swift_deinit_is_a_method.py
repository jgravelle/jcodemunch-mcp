"""A Swift `deinit` is a method of its type, named `deinit` (#754).

`SWIFT_SPEC.symbol_node_types` declared `deinit_declaration` as a `method` and
nothing was emitted: the grammar gives it no identifier child (its only named
child is `function_body`), so `_extract_name` had no name to borrow. The
`init` beside it extracts because the grammar names it. A deinitialiser is
where a class releases resources, so it is the member someone searches for
when chasing a leak.

Rulings:
- The name is BUILT, the way #714 built `this[]` and #736 built `constructor`:
  a type has at most one `deinit`, so `Holder.deinit` is unambiguous. It is
  spelled exactly as the declaration is written.
- ⚠⚠ Swift forbids calling `deinit` explicitly, so the name can appear at NO
  call site. An identifier-shaped name would otherwise earn a confident
  "no references, safe to delete" from `check_delete_safe` for a member the
  runtime calls on every release -- #714's defect, walked around by a name that
  merely looks ordinary (the `subscript[]` note in `_extract_name`). The
  reachability authority learns it per language; `deinit` stays callable, and
  searchable, everywhere else.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _swift_enabled(monkeypatch):
    """Answer the parser, not the developer's config file (#411)."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source: str):
    symbols = parse_file(source, "a.swift", "swift")
    by_id = {s.id: s for s in symbols}
    return [
        (s.id.rsplit("::", 1)[1], s.kind, by_id[s.parent].qualified_name if s.parent in by_id else s.parent)
        for s in symbols
    ]


def test_the_reported_deinit_is_a_method_of_its_class():
    assert _rows("class Holder {\n    init() {}\n    deinit { print(1) }\n}\n") == [
        ("Holder#class", "class", None),
        ("Holder.init#method", "method", "Holder"),
        ("Holder.deinit#method", "method", "Holder"),
    ]


@pytest.mark.parametrize("source,expected", [
    ("actor A {\n    deinit {}\n}\n", ("A.deinit#method", "method", "A")),
    ("struct S: ~Copyable {\n    deinit {}\n}\n", ("S.deinit#method", "method", "S")),
    ("class O {\n    class I {\n        deinit {}\n    }\n}\n", ("O.I.deinit#method", "method", "O.I")),
])
def test_every_type_that_can_declare_a_deinit_owns_it(source, expected):
    assert expected in _rows(source)


def test_the_span_signature_and_docstring_are_the_declarations():
    src = "class D {\n    /// Closes the handle.\n    deinit {\n        close()\n    }\n}\n"
    sym = next(s for s in parse_file(src, "a.swift", "swift") if s.name == "deinit")
    assert (sym.line, sym.end_line) == (3, 5)
    assert sym.signature.startswith("deinit")
    assert "Closes the handle" in sym.docstring


def test_the_grammar_still_names_nothing():
    """The name is built only because the grammar has none to give. If a grammar
    upgrade adds one, this fails and the built name should give way to it."""
    from tree_sitter_language_pack import get_parser

    tree = get_parser("swift").parse(b"class H {\n    deinit {}\n}\n")
    found = []

    def walk(node):
        if node.type == "deinit_declaration":
            found.append(node)
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    (deinit,) = found
    assert deinit.child_by_field_name("name") is None
    assert [c.type for c in deinit.named_children] == ["function_body"]


def test_deinit_is_never_nameable_at_a_swift_call_site_and_is_elsewhere():
    from jcodemunch_mcp.tools._name_reachability import name_can_appear_at_a_call_site

    assert not name_can_appear_at_a_call_site("deinit", "swift")
    assert not name_can_appear_at_a_call_site("Holder.deinit", "swift")
    # A function a PYTHON or Swift-less caller names `deinit` is an ordinary call.
    assert name_can_appear_at_a_call_site("deinit", "python")
    assert name_can_appear_at_a_call_site("deinit")
    # Swift's other members are unaffected.
    assert name_can_appear_at_a_call_site("init", "swift")


@pytest.fixture(scope="module")
def swift_repo(tmp_path_factory):
    """A class whose deinit the runtime calls, used by another file."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path_factory.mktemp("swift_repo")
    (root / "Holder.swift").write_text(
        "class Holder {\n    func ordinary() {}\n    deinit { print(1) }\n}\n", encoding="utf-8"
    )
    (root / "Use.swift").write_text(
        "func go() {\n    var h: Holder? = Holder()\n    h?.ordinary()\n    h = nil\n}\n", encoding="utf-8"
    )
    storage = str(root / "idx")
    result = index_folder(path=str(root), use_ai_summaries=False, storage_path=storage)
    return result["repo"], storage


def test_a_deinit_is_never_certified_deletable(swift_repo, monkeypatch):
    """⚠⚠ The destructive surface this fix would otherwise create: `h = nil`
    runs the deinit and writes no token naming it."""
    import jcodemunch_mcp.config as config
    from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)
    repo, storage = swift_repo
    got = check_delete_safe(repo, "Holder.swift::Holder.deinit#method", storage_path=storage)
    assert got["verdict"] != "safe_to_delete", got
    assert got["verdict"] == "name_not_searchable", got["verdict"]
