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
