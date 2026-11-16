"""A Java or Kotlin class used by same-package code is not dead (#1377, @randyhbh).

Classes in one Java or Kotlin package use each other with no `import` line, so
the import graph held no edge for that use. `check_references` and
`check_delete_safe` saw it through their content scan, but `find_dead_code`
reads only the import graph and reported a used class as dead at confidence
1.0, reason `zero_importers`.

A file's own package is an implicit `import <package>.*`, so the extractor
emits it through the #1375 wildcard path: one `<package>.Name` edge per type
name the file uses, marked `wildcard` and `same_package`. Resolution keeps the
names the package declares and drops the rest (`String`, `Override`).
"""

from __future__ import annotations

from jcodemunch_mcp.parser.imports import _extract_java_imports, resolve_specifier
from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe
from jcodemunch_mcp.tools.find_dead_code import find_dead_code
from jcodemunch_mcp.tools.find_references import find_references
from jcodemunch_mcp.tools.index_folder import index_folder

FILES = {
    "src/main/java/com/acme/JavaUser.java": (
        "package com.acme;\n\npublic class JavaUser {\n"
        "    public int run() { JavaHelper h = new JavaHelper(); return h.value(); }\n}\n"
    ),
    "src/main/java/com/acme/JavaHelper.java": (
        "package com.acme;\n\npublic class JavaHelper {\n    public int value() { return 1; }\n}\n"
    ),
    "src/main/java/com/acme/JavaOrphan.java": (
        "package com.acme;\n\npublic class JavaOrphan {\n    public int value() { return 2; }\n}\n"
    ),
    # Same simple name as a used class, other package, used by nobody.
    "src/main/java/com/other/JavaHelper.java": (
        "package com.other;\n\npublic class JavaHelper {\n    public int value() { return 3; }\n}\n"
    ),
    "src/main/kotlin/com/acme/kt/KtUser.kt": (
        "package com.acme.kt\n\nclass KtUser {\n    fun run(): Int = KtHelper().value() + Shared().n\n}\n"
    ),
    "src/main/kotlin/com/acme/kt/KtHelper.kt": (
        "package com.acme.kt\n\nclass KtHelper {\n    fun value(): Int = 1\n}\n"
    ),
    # A Kotlin file whose class is not named after the file (#1376's shape).
    "src/main/kotlin/com/acme/kt/Models.kt": (
        "package com.acme.kt\n\nclass Shared {\n    val n: Int = 1\n}\n"
    ),
}


def _index(tmp_path):
    proj = tmp_path / "proj"
    for rel, text in FILES.items():
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    store = tmp_path / "store"
    r = index_folder(str(proj), use_ai_summaries=False, storage_path=str(store))
    assert r["success"], r
    return r["repo"], str(store)


# The using classes stand for live code: nothing in this fixture uses them,
# so without a root they are dead and so, by cascade, is all they use.
ROOTS = [
    "src/main/java/com/acme/JavaUser.java",
    "src/main/kotlin/com/acme/kt/KtUser.kt",
]


def _dead_files(repo, store):
    d = find_dead_code(
        repo, storage_path=store, min_confidence=0.0, entry_point_patterns=ROOTS
    )
    return {s["file"] for s in d["dead_symbols"] if s["kind"] == "class"}


def test_a_class_used_only_by_same_package_code_is_not_dead(tmp_path):
    repo, store = _index(tmp_path)
    dead = _dead_files(repo, store)
    assert "src/main/java/com/acme/JavaHelper.java" not in dead
    assert "src/main/kotlin/com/acme/kt/KtHelper.kt" not in dead
    assert "src/main/kotlin/com/acme/kt/Models.kt" not in dead


def test_an_unused_class_in_the_same_package_is_still_dead(tmp_path):
    repo, store = _index(tmp_path)
    dead = _dead_files(repo, store)
    assert "src/main/java/com/acme/JavaOrphan.java" in dead
    # The same simple name in another package gains nothing from com.acme's use.
    assert "src/main/java/com/other/JavaHelper.java" in dead


def test_find_references_and_check_delete_safe_see_the_same_package_use(tmp_path):
    repo, store = _index(tmp_path)
    fr = find_references(repo, identifier="KtHelper", storage_path=store)
    assert fr["reference_count"] >= 1
    assert any(
        r.get("file") == "src/main/kotlin/com/acme/kt/KtUser.kt"
        for r in fr["references"]
    )
    ds = check_delete_safe(
        repo,
        symbol="JavaHelper",
        cross_repo=False,
        include_runtime=False,
        storage_path=store,
    )
    assert ds["verdict"] != "safe_to_delete"


def test_the_own_package_edge_is_marked_and_resolves_only_declared_names():
    src = FILES["src/main/java/com/acme/JavaUser.java"]
    edges = [e for e in _extract_java_imports(src, "java") if e.get("same_package")]
    assert {e["specifier"] for e in edges} == {"com.acme.JavaHelper"}
    assert all(e["wildcard"] == "com.acme.*" for e in edges)
    files = frozenset(FILES)
    assert resolve_specifier(
        "com.acme.JavaHelper", "src/main/java/com/acme/JavaUser.java", files
    ) == ("src/main/java/com/acme/JavaHelper.java")


def test_an_explicit_wildcard_of_the_own_package_is_not_doubled():
    src = "package com.acme;\n\nimport com.acme.*;\n\nclass X { JavaHelper h; }\n"
    specs = [e["specifier"] for e in _extract_java_imports(src, "java")]
    assert specs.count("com.acme.JavaHelper") == 1


def test_a_file_with_no_package_line_gains_no_edge():
    src = "class X { JavaHelper h; }\n"
    assert _extract_java_imports(src, "java") == []


def _own(src, lang="java"):
    return {
        e["wildcard"] for e in _extract_java_imports(src, lang) if e.get("same_package")
    }


def test_the_package_line_is_read_past_a_trailing_comment():
    assert _own("package com.acme; // the app\nclass X { Y y; }\n") == {"com.acme.*"}
    assert _own("package com.acme // the app\nclass X { val y = Y() }\n", "kotlin") == {
        "com.acme.*"
    }


def test_a_backticked_kotlin_package_segment_is_read():
    assert _own("package com.`fun`.app\nclass X { val y = Y() }\n", "kotlin") == {
        "com.fun.app.*"
    }


def test_a_package_line_in_a_comment_or_a_string_is_not_the_package():
    src = '/*\npackage old.x\n*/\n@file:JvmName("""\npackage old.y\n""")\npackage com.acme\nclass X { val y = Y() }\n'
    assert _own(src, "kotlin") == {"com.acme.*"}


def test_a_package_edge_to_a_jdk_name_does_not_resolve_to_a_file_named_after_the_package():
    """Review of #1377: dropping `String` from `com.acme.util.String` reached
    `com/acme/util.kt`, a file beside the `util/` package, so every file in the
    package read as importing it. A name below a package lives in the package."""
    files = frozenset(
        {
            "src/main/kotlin/com/acme/util.kt",
            "src/main/kotlin/com/acme/util/U.kt",
        }
    )
    importer = "src/main/kotlin/com/acme/util/U.kt"
    assert resolve_specifier("com.acme.util.String", importer, files) is None
    # A static or nested import of a class that is not a package still drops.
    files2 = frozenset({"src/main/java/a/b/C.java", "src/main/java/a/b/D.java"})
    assert (
        resolve_specifier("a.b.C.member", "src/main/java/a/b/D.java", files2)
        == "src/main/java/a/b/C.java"
    )


def test_the_generation_moves_so_old_indexes_gain_the_edges():
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 32


def test_a_file_named_after_a_package_does_not_hide_a_declaration_in_the_package(
    tmp_path,
):
    """Review of #1377: `augment_jvm_declaration_edges` copied the resolver's
    old rule to decide what already resolves, so with `util.kt` beside `util/`
    a class declared in `util/Models.kt` read as resolved to `util.kt` and its
    users reached nothing. The guard asks the resolver now."""
    from jcodemunch_mcp.tools.find_importers import find_importers

    files = {
        "src/main/kotlin/com/acme/util.kt": "package com.acme\n\nfun top(): Int = 1\n",
        "src/main/kotlin/com/acme/util/Models.kt": (
            "package com.acme.util\n\nclass Shared {\n    val n: Int = 1\n}\n\nfun helper(): Int = 2\n"
        ),
        "src/main/kotlin/com/acme/util/User.kt": "package com.acme.util\n\nclass User {\n    val s = Shared()\n}\n",
        "src/main/kotlin/com/acme/App.kt": (
            "package com.acme\n\nimport com.acme.util.helper\n\nclass App {\n    val h = helper()\n}\n"
        ),
    }
    proj = tmp_path / "proj"
    for rel, text in files.items():
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    store = str(tmp_path / "store")
    repo = index_folder(str(proj), use_ai_summaries=False, storage_path=store)["repo"]
    got = find_importers(
        repo, file_path="src/main/kotlin/com/acme/util/Models.kt", storage_path=store
    )
    importers = {i["file"] for i in got["importers"]}
    assert importers == {
        "src/main/kotlin/com/acme/util/User.kt",
        "src/main/kotlin/com/acme/App.kt",
    }
