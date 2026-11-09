"""A Java or Kotlin import resolves to the file that declares the class (#1374, @randyhbh).

`_extract_java_imports` read `import com.acme.app.util.Hosts` from `.java` and
`.kt` files, and `resolve_specifier` never turned it into a path, for two
reasons: `.java` and `.kt` were not candidate extensions, and the only source
roots tried were the repo root and the Python ones (keyed on `__init__.py`),
while Maven and Gradle sources live under `<module>/src/<sourceSet>/{java,kotlin}`.
So every JVM import edge was dropped, and on a JVM repository `find_importers`,
`get_dependency_graph`, `get_repo_health` and `check_delete_safe` read an empty
graph, the last two with confident answers.

The JVM step is gated on the IMPORTER's extension, so a Python, JavaScript or
Ruby importer resolves exactly as before; the gate is tested below.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser import imports as imp
from jcodemunch_mcp.parser.imports import _extract_java_imports, resolve_specifier

# The reporter's layout: two Gradle modules, Java and Kotlin in each.
GRADLE = frozenset(
    {
        "app/src/main/java/com/acme/app/OrderService.java",
        "app/src/main/java/com/acme/app/util/Hosts.java",
        "app/src/main/kotlin/com/acme/app/Mapper.kt",
        "app/src/test/java/com/acme/app/OrderServiceTest.java",
        "lib/src/main/kotlin/com/acme/lib/Money.kt",
        "lib/src/main/java/com/acme/lib/Ledger.java",
        "subprojects/core/billing/src/main/java/com/acme/billing/Invoice.java",
        "app/build.gradle.kts",
    }
)

ORDER_SERVICE = "app/src/main/java/com/acme/app/OrderService.java"
MAPPER = "app/src/main/kotlin/com/acme/app/Mapper.kt"


def _resolve(spec, importer, files=GRADLE):
    return resolve_specifier(spec, importer, files)


def test_the_reporters_repro_resolves_every_edge():
    src = "package com.acme.app;\n\nimport com.acme.app.util.Hosts;\nimport com.acme.lib.Money;\n"
    got = {
        e["specifier"]: _resolve(e["specifier"], ORDER_SERVICE)
        for e in _extract_java_imports(src, "java")
    }
    assert got == {
        "com.acme.app.util.Hosts": "app/src/main/java/com/acme/app/util/Hosts.java",
        "com.acme.lib.Money": "lib/src/main/kotlin/com/acme/lib/Money.kt",
    }


def test_the_repo_root_layout_resolves():
    """The issue's control: the class at the root, no source-root prefix."""
    assert _resolve(
        "com.acme.app.util.Hosts", "x.java", frozenset({"com/acme/app/util/Hosts.java"})
    ) == ("com/acme/app/util/Hosts.java")


@pytest.mark.parametrize(
    "spec, importer, expected",
    [
        # Java to Java, same module
        (
            "com.acme.app.util.Hosts",
            ORDER_SERVICE,
            "app/src/main/java/com/acme/app/util/Hosts.java",
        ),
        # Java to Kotlin, another module
        (
            "com.acme.lib.Money",
            ORDER_SERVICE,
            "lib/src/main/kotlin/com/acme/lib/Money.kt",
        ),
        # Kotlin to Java, another module
        ("com.acme.lib.Ledger", MAPPER, "lib/src/main/java/com/acme/lib/Ledger.java"),
        # Kotlin to Kotlin
        ("com.acme.lib.Money", MAPPER, "lib/src/main/kotlin/com/acme/lib/Money.kt"),
        # A module several directories deep
        (
            "com.acme.billing.Invoice",
            MAPPER,
            "subprojects/core/billing/src/main/java/com/acme/billing/Invoice.java",
        ),
        # A test source set reaching into main
        (
            "com.acme.app.OrderService",
            "app/src/test/java/com/acme/app/OrderServiceTest.java",
            ORDER_SERVICE,
        ),
        # A Gradle Kotlin script is an importer too
        (
            "com.acme.lib.Money",
            "app/build.gradle.kts",
            "lib/src/main/kotlin/com/acme/lib/Money.kt",
        ),
    ],
)
def test_a_jvm_import_resolves_across_languages_and_modules(spec, importer, expected):
    assert _resolve(spec, importer) == expected


def test_a_static_import_resolves_to_the_class():
    src = "import static com.acme.app.util.Hosts.localHost;\n"
    (edge,) = _extract_java_imports(src, "java")
    assert (
        _resolve(edge["specifier"], ORDER_SERVICE)
        == "app/src/main/java/com/acme/app/util/Hosts.java"
    )


def test_a_nested_class_import_resolves_to_the_outer_file():
    assert (
        _resolve("com.acme.lib.Ledger.Entry", MAPPER)
        == "lib/src/main/java/com/acme/lib/Ledger.java"
    )


def test_the_importers_own_module_wins_a_duplicated_class():
    files = frozenset(
        {
            "a/src/main/java/com/acme/Shared.java",
            "b/src/main/java/com/acme/Shared.java",
            "a/src/main/java/com/acme/UseA.java",
            "b/src/main/java/com/acme/UseB.java",
        }
    )
    assert (
        _resolve("com.acme.Shared", "a/src/main/java/com/acme/UseA.java", files)
        == "a/src/main/java/com/acme/Shared.java"
    )
    assert (
        _resolve("com.acme.Shared", "b/src/main/java/com/acme/UseB.java", files)
        == "b/src/main/java/com/acme/Shared.java"
    )


@pytest.mark.parametrize(
    "spec",
    [
        "java.util.List",
        "java.util.Map.Entry",
        "org.springframework.stereotype.Service",
        "kotlin.collections.List",
    ],
)
def test_jdk_and_third_party_imports_resolve_to_nothing(spec):
    assert _resolve(spec, ORDER_SERVICE) is None
    assert _resolve(spec, MAPPER) is None


def test_a_jvm_import_never_binds_a_file_of_another_language():
    """`java.util.List` beside a `java/util/List.py` is still the JDK."""
    files = GRADLE | {"java/util/List.py", "java/util/List.ts", "com/acme/lib/Money.js"}
    assert _resolve("java.util.List", ORDER_SERVICE, files) is None
    assert (
        _resolve("com.acme.lib.Money", ORDER_SERVICE, files)
        == "lib/src/main/kotlin/com/acme/lib/Money.kt"
    )


@pytest.mark.parametrize(
    "spec, importer, files, expected",
    [
        # A Python dotted import does not take a Java class
        ("com.acme.app.util.Hosts", "tool.py", GRADLE, None),
        (
            "app.utils",
            "main.py",
            frozenset({"app/utils.py", "app/utils.java", "app/__init__.py"}),
            "app/utils.py",
        ),
        # A JavaScript or Ruby importer does not take a Kotlin file for a dotted name
        ("com.acme.lib.Money", "web/index.js", GRADLE, None),
        ("com.acme.lib.Money", "scripts/x.rb", GRADLE, None),
    ],
)
def test_the_jvm_step_is_gated_on_the_importers_extension(
    spec, importer, files, expected
):
    assert _resolve(spec, importer, files) == expected


def test_the_source_roots_are_derived_once_per_file_set():
    roots = imp._jvm_source_roots(GRADLE)
    assert set(roots) >= {
        "app/src/main/java",
        "app/src/main/kotlin",
        "app/src/test/java",
        "lib/src/main/kotlin",
        "lib/src/main/java",
        "subprojects/core/billing/src/main/java",
    }
    assert imp._jvm_source_roots(GRADLE) is roots
    assert "src/main/java" in imp._jvm_source_roots(
        frozenset({"src/main/java/com/x/A.java"})
    )


@pytest.mark.parametrize("language", ["java", "kotlin"])
def test_an_import_in_a_crlf_file_is_extracted(language):
    """A Windows checkout: the pattern used to stop at the carriage return."""
    src = (
        "package com.acme.app;"
        + chr(13)
        + chr(10)
        + "import com.acme.lib.Money;"
        + chr(13)
        + chr(10)
    )
    src += "import static com.acme.app.util.Hosts.localHost" + chr(13) + chr(10)
    assert [e["specifier"] for e in _extract_java_imports(src, language)] == [
        "com.acme.lib.Money",
        "com.acme.app.util.Hosts.localHost",
    ]


def test_the_crlf_fix_re_parses_existing_indexes():
    """`files.imports` changes on unchanged content, so the generation moves."""
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 30


@pytest.mark.parametrize("newline", ["lf", "crlf"])
def test_index_folder_then_find_importers_returns_the_edge(tmp_path, newline):
    """The user's entry point: an indexed Gradle tree has its import graph."""
    from jcodemunch_mcp.tools.find_importers import find_importers
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "proj"
    files = {
        "app/src/main/java/com/acme/app/OrderService.java": (
            "package com.acme.app;\n\nimport com.acme.app.util.Hosts;\nimport com.acme.lib.Money;\n\n"
            "public class OrderService {\n  Money total() { return Hosts.money(); }\n}\n"
        ),
        "app/src/main/java/com/acme/app/util/Hosts.java": (
            "package com.acme.app.util;\n\nimport com.acme.lib.Money;\n\n"
            "public class Hosts {\n  public static Money money() { return null; }\n}\n"
        ),
        "lib/src/main/kotlin/com/acme/lib/Money.kt": "package com.acme.lib\n\nclass Money(val cents: Long)\n",
    }
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        eol = chr(13) + chr(10) if newline == "crlf" else chr(10)
        p.write_bytes(body.replace(chr(10), eol).encode("utf-8"))
    store = tmp_path / "store"
    store.mkdir()
    r = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True, r
    out = find_importers(
        repo=r["repo"],
        file_path="lib/src/main/kotlin/com/acme/lib/Money.kt",
        storage_path=str(store),
    )
    got = {row["file"] for row in out["importers"]}
    assert got == {
        "app/src/main/java/com/acme/app/OrderService.java",
        "app/src/main/java/com/acme/app/util/Hosts.java",
    }, out
