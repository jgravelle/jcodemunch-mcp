"""Same-package use in Java and Kotlin is an edge (#1377, @randyhbh, split from #1374).

Classes in one Java or Kotlin package use each other with no `import` line,
so the import graph held no edge for that use. `check_references` and
`check_delete_safe` saw it through the name in the text, but `find_importers`
listed nothing and `find_dead_code` reported a class used only by its own
package as dead at confidence 1.0.

A file's own package is now read as an implicit `import pkg.*`: one marker
edge stores the type names the file uses, and the index derives an edge to
each one the package declares, the #1376 shape.
"""

from __future__ import annotations

FILES = {
    "app/src/main/java/com/acme/app/Hosts.java": (
        "package com.acme.app;\n\npublic class Hosts {\n  public static String name() { return \"h\"; }\n}\n"
    ),
    "app/src/main/java/com/acme/app/OrderService.java": (
        "package com.acme.app;\n\npublic class OrderService {\n  String run() { return Hosts.name(); }\n}\n"
    ),
    "app/src/main/kotlin/com/acme/app/Money.kt": "package com.acme.app\n\nclass Money(val cents: Long)\n",
    "app/src/main/kotlin/com/acme/app/Ledger.kt": (
        "package com.acme.app\n\nclass Ledger { fun total(): Money = Money(1) }\n"
    ),
    # Same simple name in another package: not reached by same-package use.
    "app/src/main/java/com/acme/other/Hosts.java": "package com.acme.other;\n\npublic class Hosts {}\n",
    # An explicit import shadows the own-package class of the same name.
    "app/src/main/java/com/acme/app/Shadowed.java": (
        "package com.acme.app;\n\nimport com.acme.other.Hosts;\n\npublic class Shadowed { Hosts h; }\n"
    ),
}
HOSTS = "app/src/main/java/com/acme/app/Hosts.java"
OTHER_HOSTS = "app/src/main/java/com/acme/other/Hosts.java"
ORDER = "app/src/main/java/com/acme/app/OrderService.java"
MONEY = "app/src/main/kotlin/com/acme/app/Money.kt"
LEDGER = "app/src/main/kotlin/com/acme/app/Ledger.kt"
SHADOWED = "app/src/main/java/com/acme/app/Shadowed.java"


def _index(tmp_path):
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "proj"
    for rel, body in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body.encode("utf-8"))
    store = tmp_path / "store"
    store.mkdir()
    r = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True, r
    return store, r["repo"]


def _importers(repo, store, path):
    from jcodemunch_mcp.tools.find_importers import find_importers

    out = find_importers(repo=repo, file_path=path, storage_path=str(store))
    return {row["file"] for row in out["importers"]}


def test_same_package_use_is_an_importer(tmp_path):
    store, repo = _index(tmp_path)
    assert ORDER in _importers(repo, store, HOSTS)
    assert LEDGER in _importers(repo, store, MONEY)


def test_another_packages_class_of_the_same_name_is_not_reached(tmp_path):
    store, repo = _index(tmp_path)
    assert ORDER not in _importers(repo, store, OTHER_HOSTS)
    # The explicit import binds the other package's class, and the own-package
    # one is shadowed.
    assert SHADOWED in _importers(repo, store, OTHER_HOSTS)
    assert SHADOWED not in _importers(repo, store, HOSTS)


def test_a_class_used_only_by_its_package_is_not_dead(tmp_path):
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    store, repo = _index(tmp_path)
    out = find_dead_code(repo=repo, storage_path=str(store), min_confidence=0.0)
    # The fixture has no entry point, so its importers are dead too and the
    # classes they use may still be reported as reached only from dead code.
    # What must not happen is the 1.0 "nothing imports this" verdict.
    rows = [r for r in out.get("dead_symbols", []) if r.get("file") in (HOSTS, MONEY)]
    assert rows, out
    assert all(r.get("reason") != "zero_importers" for r in rows), rows
    assert all(r.get("confidence", 1.0) < 1.0 for r in rows), rows


def test_the_stored_row_is_one_marker_edge(tmp_path):
    import json
    import sqlite3

    from jcodemunch_mcp.parser.imports import JVM_SAME_PACKAGE_KEY

    store, _ = _index(tmp_path)
    rows = {}
    for db in store.glob("**/*.db"):
        con = sqlite3.connect(str(db))
        try:
            for path, raw in con.execute("SELECT path, imports FROM files"):
                rows[path] = json.loads(raw) if raw else []
        except sqlite3.OperationalError:
            continue
        finally:
            con.close()
    markers = [e for e in rows[ORDER] if JVM_SAME_PACKAGE_KEY in e]
    assert len(markers) == 1
    assert markers[0]["specifier"] == "com.acme.app.*"
    assert "Hosts" in markers[0][JVM_SAME_PACKAGE_KEY]
    assert markers[0]["names"] == []


def test_the_marker_resolves_to_nothing():
    from jcodemunch_mcp.parser.imports import resolve_specifier

    fs = frozenset(FILES)
    assert resolve_specifier("com.acme.app.*", ORDER, fs) is None


def test_the_generation_moved():
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 32
