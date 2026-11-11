"""A Kotlin import of a top-level declaration resolves to the file that declares it (#1376, @randyhbh).

`import com.acme.app.mapper.buildSnapshot` imports a top-level function
declared in `SnapshotMapper.kt`. JVM import resolution matched the dotted name
against file paths only (`com/acme/app/mapper/buildSnapshot.kt`, then
`com/acme/app/mapper.kt`), so the edge resolved to nothing. The same was true
of a top-level `val`, `const val`, `object`, `typealias`, and of a second class
declared in a file named after another one. On a Kotlin repository
`find_importers` then reported the declaring file as unimported, and
`check_delete_safe` had no import to block a delete on.

The fix keeps the resolver path-only and ADDS an edge at the index, beside the
one the import wrote, the Racket collection-path shape: the package (the
specifier minus its last segment) names a directory under a source root, and
the last segment names a top-level declaration in a file there.
"""

from __future__ import annotations

import pytest

ROOT_FILES = {
    "app/src/main/kotlin/com/acme/app/mapper/SnapshotMapper.kt": (
        "package com.acme.app.mapper\n\n"
        "const val LIMIT = 3\n"
        "val defaultName: String = \"x\"\n"
        "fun buildSnapshot(): Int = 1\n"
        "class Extra\n"
        "object Registry\n"
        "typealias Snap = Int\n"
    ),
    "app/src/main/kotlin/com/acme/app/mapper/Other.kt": (
        "package com.acme.app.mapper\n\nclass Other { fun buildSnapshot(): Int = 2 }\n"
    ),
    "app/src/main/kotlin/com/acme/app/Service.kt": (
        "package com.acme.app\n\n"
        "import com.acme.app.mapper.buildSnapshot\n"
        "import com.acme.app.mapper.Extra\n"
        "import com.acme.app.mapper.LIMIT as CAP\n"
        "import com.acme.app.mapper.missingName\n"
        "import kotlin.collections.listOf\n\n"
        "class Service { fun run() = buildSnapshot() + CAP + Extra().hashCode() }\n"
    ),
    "app/src/main/java/com/acme/app/Caller.java": (
        "package com.acme.app;\n\n"
        "import com.acme.app.mapper.Registry;\n\n"
        "public class Caller { Object r = Registry.INSTANCE; }\n"
    ),
}
MAPPER = "app/src/main/kotlin/com/acme/app/mapper/SnapshotMapper.kt"
OTHER = "app/src/main/kotlin/com/acme/app/mapper/Other.kt"
SERVICE = "app/src/main/kotlin/com/acme/app/Service.kt"
CALLER = "app/src/main/java/com/acme/app/Caller.java"


def _write(root, files):
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body.encode("utf-8"))


def _index(tmp_path, files=ROOT_FILES):
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "proj"
    _write(root, files)
    store = tmp_path / "store"
    store.mkdir(exist_ok=True)
    r = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True, r
    return root, store, r["repo"]


def _importers(repo, store, path):
    from jcodemunch_mcp.tools.find_importers import find_importers

    out = find_importers(repo=repo, file_path=path, storage_path=str(store))
    return {row["file"] for row in out["importers"]}


def test_the_reporters_top_level_function_import_finds_its_file(tmp_path):
    _, store, repo = _index(tmp_path)
    assert _importers(repo, store, MAPPER) == {SERVICE, CALLER}


def test_a_member_of_the_same_name_in_another_file_is_not_the_target(tmp_path):
    """`Other.buildSnapshot` is a method, not a top-level declaration."""
    _, store, repo = _index(tmp_path)
    assert _importers(repo, store, OTHER) == set()


@pytest.mark.parametrize("name", ["LIMIT", "defaultName", "buildSnapshot", "Extra", "Registry", "Snap"])
def test_every_top_level_kind_resolves(tmp_path, name):
    from jcodemunch_mcp.parser.imports import resolve_specifier
    from jcodemunch_mcp.storage import IndexStore

    files = dict(ROOT_FILES)
    files[SERVICE] = f"package com.acme.app\n\nimport com.acme.app.mapper.{name}\n\nclass Service\n"
    _, store, repo = _index(tmp_path, files)
    owner, rname = repo.split("/", 1)
    index = IndexStore(base_path=str(store)).load_index(owner, rname)
    targets = {
        resolve_specifier(e["specifier"], SERVICE, frozenset(index.source_files))
        for e in index.imports[SERVICE]
    }
    assert MAPPER in targets


def test_a_name_the_package_does_not_declare_resolves_to_nothing(tmp_path):
    from jcodemunch_mcp.parser.imports import resolve_specifier
    from jcodemunch_mcp.storage import IndexStore

    _, store, repo = _index(tmp_path)
    owner, rname = repo.split("/", 1)
    index = IndexStore(base_path=str(store)).load_index(owner, rname)
    fs = frozenset(index.source_files)
    for e in index.imports[SERVICE]:
        target = resolve_specifier(e["specifier"], SERVICE, fs)
        if "missingName" in e["specifier"] or e["specifier"].startswith("kotlin."):
            assert target is None, e


def test_a_moved_declaration_moves_its_edge_on_reindex(tmp_path):
    """The added edge is derived again, so a stale one does not survive."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    root, store, repo = _index(tmp_path)
    (root / MAPPER).write_bytes(
        ROOT_FILES[MAPPER].replace("fun buildSnapshot(): Int = 1\n", "").encode("utf-8")
    )
    (root / OTHER).write_bytes(
        "package com.acme.app.mapper\n\nclass Other\nfun buildSnapshot(): Int = 2\n".encode("utf-8")
    )
    r = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True, r
    assert SERVICE in _importers(repo, store, OTHER)
    # Service still imports Extra and LIMIT from SnapshotMapper.kt.
    assert SERVICE in _importers(repo, store, MAPPER)

    (root / SERVICE).write_bytes(
        "package com.acme.app\n\nimport com.acme.app.mapper.buildSnapshot\n\nclass Service\n".encode("utf-8")
    )
    r = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True, r
    assert SERVICE not in _importers(repo, store, MAPPER)
    assert SERVICE in _importers(repo, store, OTHER)


def test_check_delete_safe_blocks_on_the_top_level_import(tmp_path):
    from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe

    _, store, repo = _index(tmp_path)
    verdict = check_delete_safe(
        repo=repo,
        symbol="buildSnapshot",
        cross_repo=False,
        include_runtime=False,
        storage_path=str(store),
    )
    assert verdict.get("verdict") != "safe_to_delete", verdict


@pytest.mark.parametrize(
    "line",
    [
        "import com.acme.app.mapper.SnapshotMapperKt;",
        "import static com.acme.app.mapper.SnapshotMapperKt.buildSnapshot;",
    ],
)
def test_java_reaches_the_kotlin_file_through_its_generated_class(tmp_path, line):
    files = dict(ROOT_FILES)
    files[CALLER] = f"package com.acme.app;\n\n{line}\n\npublic class Caller {{ }}\n"
    _, store, repo = _index(tmp_path, files)
    assert CALLER in _importers(repo, store, MAPPER)


def test_a_stale_added_edge_is_dropped_when_the_declaration_is_gone():
    """Unit form: an edge added earlier and no longer backed by a symbol is removed."""
    from jcodemunch_mcp.parser.imports import JVM_DECLARED_EDGE_KEY, augment_jvm_declaration_edges

    fs = frozenset({MAPPER, OTHER, SERVICE})
    imports = {
        SERVICE: [
            {"specifier": "com.acme.app.mapper.buildSnapshot", "names": ["buildSnapshot"]},
            {
                "specifier": "com.acme.app.mapper.SnapshotMapper",
                "names": ["buildSnapshot"],
                JVM_DECLARED_EDGE_KEY: "com.acme.app.mapper.buildSnapshot",
            },
        ]
    }
    symbols = [{"file": OTHER, "name": "buildSnapshot", "parent": None}]
    assert augment_jvm_declaration_edges(imports, symbols, fs) == 1
    assert [e["specifier"] for e in imports[SERVICE]] == [
        "com.acme.app.mapper.buildSnapshot",
        "com.acme.app.mapper.Other",
    ]
    # A second run adds nothing new and keeps one derived edge.
    assert augment_jvm_declaration_edges(imports, symbols, fs) == 1
    assert len(imports[SERVICE]) == 2


def test_find_references_counts_the_importer_once(tmp_path):
    from jcodemunch_mcp.tools.find_references import find_references

    _, store, repo = _index(tmp_path)
    out = find_references(repo=repo, identifier="buildSnapshot", storage_path=str(store))
    files = [r["file"] for r in out.get("references", [])]
    assert files.count(SERVICE) == 1, out
