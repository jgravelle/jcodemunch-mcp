"""A Java or Kotlin wildcard import is an edge to each class the file uses (#1375, @randyhbh).

`_JAVA_IMPORT` matched `([\\w.]+)`, so `import com.acme.app.util.*;` matched
nothing and the file had no edge to the package it imports. On a JVM repo that
writes wildcards, `find_importers` and `check_delete_safe` read the package's
classes as unused.

A wildcard names a package, and every resolver consumer maps one specifier to
one file. So the extractor turns `import a.b.*` into one `a.b.Name` edge per
type name the file uses, marked `wildcard: "a.b.*"`. Resolution then keeps the
names that `a/b/` declares and drops the rest (`String`, `Override`), against
the file set at query time, so a class added to the package later resolves
without re-parsing the importer.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.imports import _extract_java_imports, resolve_specifier

FILES = frozenset(
    {
        "app/src/main/java/com/acme/app/OrderService.java",
        "app/src/main/java/com/acme/app/util/Hosts.java",
        "app/src/main/java/com/acme/app/util/Ports.java",
        "app/src/main/java/com/acme/app/util/Unused.java",
        "lib/src/main/kotlin/com/acme/lib/Money.kt",
        "lib/src/main/kotlin/com/acme/lib/Currency.kt",
    }
)
ORDER = "app/src/main/java/com/acme/app/OrderService.java"
MAPPER = "app/src/main/kotlin/com/acme/app/Mapper.kt"

JAVA = """package com.acme.app;

import com.acme.app.util.*;
import java.util.List;

/** Uses Ports in a comment only: Ports. */
public class OrderService {
    private final Hosts hosts = new Hosts();
    String label = "Unused";

    @Override
    public String toString() { return hosts.toString(); }
}
"""


def _resolved(src, importer, lang="java"):
    out = {}
    for e in _extract_java_imports(src, lang):
        out[e["specifier"]] = resolve_specifier(e["specifier"], importer, FILES)
    return out


def test_the_reporters_wildcard_resolves_to_the_class_it_uses():
    got = _resolved(JAVA, ORDER)
    hits = {spec: path for spec, path in got.items() if path}
    assert hits == {"com.acme.app.util.Hosts": "app/src/main/java/com/acme/app/util/Hosts.java"}


def test_a_name_only_in_a_comment_or_a_string_is_not_an_edge():
    specs = {e["specifier"] for e in _extract_java_imports(JAVA, "java")}
    assert "com.acme.app.util.Ports" not in specs
    assert "com.acme.app.util.Unused" not in specs


def test_every_wildcard_edge_names_the_line_it_came_from():
    edges = [e for e in _extract_java_imports(JAVA, "java") if e.get("wildcard")]
    assert edges
    assert {e["wildcard"] for e in edges} == {"com.acme.app.util.*"}
    for e in edges:
        assert e["names"] == [e["specifier"].rsplit(".", 1)[1]]


def test_an_explicit_import_is_not_doubled_by_a_wildcard():
    src = "import com.acme.app.util.*;\nimport com.acme.lib.Hosts;\nclass X { Hosts h; Ports p; }\n"
    edges = _extract_java_imports(src, "java")
    specs = [e["specifier"] for e in edges]
    assert "com.acme.app.util.Hosts" not in specs
    assert "com.acme.lib.Hosts" in specs
    assert "com.acme.app.util.Ports" in specs


def test_a_type_the_file_declares_is_not_a_wildcard_edge():
    src = "import com.acme.app.util.*;\nclass Hosts { Ports p; }\n"
    specs = {e["specifier"] for e in _extract_java_imports(src, "java")}
    assert "com.acme.app.util.Hosts" not in specs
    assert "com.acme.app.util.Ports" in specs


def test_kotlin_wildcard_resolves_across_modules():
    src = "package com.acme.app\n\nimport com.acme.lib.*\n\nfun total(m: Money): Money = m\n"
    hits = {s: p for s, p in _resolved(src, MAPPER, "kotlin").items() if p}
    assert hits == {"com.acme.lib.Money": "lib/src/main/kotlin/com/acme/lib/Money.kt"}


@pytest.mark.parametrize("terminator", [";", ";\r", " ;  ", ""])
def test_the_line_ending_spellings_all_extract(terminator):
    src = f"import com.acme.app.util.*{terminator}\nclass X {{ Hosts h; }}\n"
    specs = {e["specifier"] for e in _extract_java_imports(src, "java")}
    assert "com.acme.app.util.Hosts" in specs


def test_a_static_wildcard_is_an_edge_to_its_class():
    src = "import static com.acme.app.util.Hosts.*;\nclass X { int p = DEFAULT; }\n"
    edges = _extract_java_imports(src, "java")
    assert [e["specifier"] for e in edges] == ["com.acme.app.util.Hosts"]
    assert resolve_specifier("com.acme.app.util.Hosts", ORDER, FILES) == (
        "app/src/main/java/com/acme/app/util/Hosts.java"
    )


def test_a_file_with_no_wildcard_extracts_exactly_as_before():
    src = "import com.acme.app.util.Hosts;\nimport static a.b.C.m;\nclass X { Ports p; }\n"
    assert _extract_java_imports(src, "java") == [
        {"specifier": "com.acme.app.util.Hosts", "names": ["Hosts"]},
        {"specifier": "a.b.C.m", "names": ["m"]},
    ]


# --- Review round 1: spellings that dropped or invented an edge ---------------


def _specs(src, lang="java"):
    # The written imports only: since #1377 a file with a `package` line also
    # carries one same-package marker edge, which `tests/test_jvm_same_package_use.py` covers.
    from jcodemunch_mcp.parser.imports import JVM_SAME_PACKAGE_KEY

    return [e["specifier"] for e in _extract_java_imports(src, lang) if JVM_SAME_PACKAGE_KEY not in e]


def test_a_class_literal_at_a_line_end_does_not_hide_the_next_name():
    """`Hosts::class` then a newline once read as a declaration of `Ports`, so
    the used `Ports` lost its importer: the direction this fix closes."""
    src = "package p\nimport a.b.*\nfun f() {\n  val k = Hosts::class\n  Ports.init()\n}\n"
    assert set(_specs(src, "kotlin")) == {"a.b.Hosts", "a.b.Ports"}


def test_a_constant_or_a_member_is_not_a_class_name():
    src = "import a.b.*;\nclass X { int v = Hosts.MAX_VALUE; int w = MAX_SIZE; Hosts.Inner i; }\n"
    assert _specs(src) == ["a.b.Hosts"]


def test_a_fully_qualified_use_is_not_supplied_by_the_wildcard():
    assert _specs("import a.b.*;\nclass X { com.other.Hosts h; }\n") == []


def test_the_generation_moves_so_old_indexes_gain_the_edges():
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 31


def test_an_unresolved_wildcard_edge_is_marked_dead():
    from jcodemunch_mcp.parser.imports import is_unresolved_wildcard_edge

    edges = {e["specifier"]: e for e in _extract_java_imports(JAVA, "java")}
    assert not is_unresolved_wildcard_edge(edges["com.acme.app.util.Hosts"], ORDER, FILES)
    assert is_unresolved_wildcard_edge(edges["com.acme.app.util.String"], ORDER, FILES)
    explicit = {"specifier": "java.util.List", "names": ["List"]}
    assert not is_unresolved_wildcard_edge(explicit, ORDER, FILES)


# --- The user's entry points ---------------------------------------------------


def _index_wildcard_tree(tmp_path):
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "proj"
    files = {
        "app/src/main/java/com/acme/app/OrderService.java": (
            "package com.acme.app;\n\nimport com.acme.app.util.*;\n\n"
            "public class OrderService {\n  private final Hosts hosts = new Hosts();\n"
            "  @Override public String toString() { return hosts.toString(); }\n}\n"
        ),
        "app/src/main/java/com/acme/app/util/Hosts.java": (
            "package com.acme.app.util;\n\npublic class Hosts {\n  public String name() { return null; }\n}\n"
        ),
    }
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    r = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True, r
    return r["repo"], str(store)


def test_index_folder_then_find_importers_sees_a_wildcard_importer(tmp_path):
    from jcodemunch_mcp.tools.find_importers import find_importers

    repo, store = _index_wildcard_tree(tmp_path)
    out = find_importers(
        repo=repo, file_path="app/src/main/java/com/acme/app/util/Hosts.java", storage_path=store
    )
    assert {row["file"] for row in out["importers"]} == {
        "app/src/main/java/com/acme/app/OrderService.java"
    }, out


def test_check_delete_safe_does_not_clear_a_class_used_through_a_wildcard(tmp_path):
    from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe

    repo, store = _index_wildcard_tree(tmp_path)
    verdict = check_delete_safe(
        repo=repo, symbol="Hosts", cross_repo=False, include_runtime=False, storage_path=store
    )
    kinds = {b.get("kind") for b in verdict.get("blockers", [])}
    assert "external_import" in kinds, verdict
    assert verdict.get("verdict") != "safe_to_delete", verdict


def test_the_name_matching_tools_do_not_list_a_class_the_package_lacks(tmp_path):
    """`a.b.String` resolves to nothing: no tool reports the file as importing
    `String`, while `Hosts`, which the package declares, is reported."""
    from jcodemunch_mcp.tools.check_references import check_references
    from jcodemunch_mcp.tools.find_references import find_references

    repo, store = _index_wildcard_tree(tmp_path)
    order = "app/src/main/java/com/acme/app/OrderService.java"

    def files(out):
        return {row["file"] for row in out.get("references", [])}

    assert order in files(find_references(repo=repo, identifier="Hosts", storage_path=store))
    assert order not in files(find_references(repo=repo, identifier="String", storage_path=store))
    assert order not in files(find_references(repo=repo, identifier="Override", storage_path=store))

    def imported(out):
        return {row["file"] for row in out.get("import_references", [])}

    hosts = check_references(repo=repo, identifier="Hosts", search_content=False, storage_path=store)
    string = check_references(repo=repo, identifier="String", search_content=False, storage_path=store)
    assert order in imported(hosts), hosts
    assert order not in imported(string), string


def test_a_moved_symbol_needs_no_import_of_a_class_that_does_not_exist(monkeypatch):
    from types import SimpleNamespace

    from jcodemunch_mcp.tools import plan_refactoring as pr

    src = JAVA
    monkeypatch.setattr(pr, "_get_file_content_safe", lambda *a: (src, None))
    index = SimpleNamespace(
        imports={ORDER: _extract_java_imports(src, "java")}, source_files=sorted(FILES)
    )
    sym = {"file": ORDER, "line": 7, "end_line": 13}
    _, needed = pr._extract_symbol_with_deps(None, "o", "n", index, sym)
    assert {e["specifier"] for e in needed if e.get("wildcard")} == {"com.acme.app.util.Hosts"}


def test_an_index_with_no_file_set_keeps_every_wildcard_edge():
    """Unknown is not dead: without a file set no edge can be shown to resolve
    to nothing, so the name-matching tools keep it (they over-report, never drop)."""
    from types import SimpleNamespace

    from jcodemunch_mcp.parser.imports import wildcard_edge_filter

    edge = {"specifier": "a.b.String", "names": ["String"], "wildcard": "a.b.*"}
    assert wildcard_edge_filter(SimpleNamespace())(edge, ORDER) is False
    assert wildcard_edge_filter(SimpleNamespace(source_files=sorted(FILES)))(edge, ORDER) is True
