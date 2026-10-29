"""Two different packages never share a snapshot directory.

`index_dependency` copies a package into `<store>/deps/<name>` and indexes it
there. A second package that maps to the same name deletes and rebuilds the
first one's snapshot, so the first package's index silently becomes the
second package. The name used to be a lossy cleanup of the package name:
`@a/b` and `a__b` both became `a__b`, names that differ only in case share a
directory on Windows and macOS, and an npm and a PyPI package with the same
name and version shared one too.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from jcodemunch_mcp.tools.index_dependency import _safe_snapshot_name, index_dependency
from jcodemunch_mcp.tools.index_folder import index_folder

# Pairs that the old cleanup sent to one directory, plus the case and
# cross-ecosystem shapes.
DISTINCT = [
    ("npm", "@a/b", "1.0.0"),
    ("npm", "a__b", "1.0.0"),
    ("npm", "a/b", "1.0.0"),
    ("npm", "Foo", "1.0.0"),
    ("npm", "foo", "1.0.0"),
    ("pypi", "foo", "1.0.0"),
    ("pypi", "a b", "1.0.0"),
    ("pypi", "a_b", "1.0.0"),
    ("pypi", "a..b", "1.0.0"),
    ("pypi", "a_b", "1/0"),
    ("pypi", "a_b", "1_0"),
    ("npm", "x", ""),
    ("npm", "x", "unknown"),
]


@pytest.mark.parametrize(("left", "right"), list(itertools.combinations(DISTINCT, 2)))
def test_distinct_packages_get_distinct_directories_even_ignoring_case(left, right):
    a = _safe_snapshot_name(left[1], left[2], ecosystem=left[0])
    b = _safe_snapshot_name(right[1], right[2], ecosystem=right[0])
    assert a.casefold() != b.casefold(), (left, right, a)


def test_the_same_package_gets_the_same_directory():
    # A re-run must find (and rebuild) its own earlier snapshot.
    for eco, name, ver in DISTINCT:
        assert _safe_snapshot_name(name, ver, ecosystem=eco) == _safe_snapshot_name(
            name, ver, ecosystem=eco
        )


def test_the_directory_name_is_one_safe_path_component():
    for eco, name, ver in DISTINCT + [
        ("npm", "../../etc", "../1"),
        ("pypi", "a\\b", "1"),
    ]:
        snap = _safe_snapshot_name(name, ver, ecosystem=eco)
        assert "/" not in snap and "\\" not in snap and ".." not in snap, snap
        assert len(snap) <= 200


def test_the_directory_name_keeps_the_readable_name_and_version():
    assert _safe_snapshot_name("@acme/util", "2.0.0", ecosystem="npm").startswith(
        "acme__util@2.0.0"
    )


def _write_npm_pkg(root, name, version, body):
    pkg = root / "node_modules"
    for part in name.split("/"):
        pkg = pkg / part
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"name": name, "version": version}))
    (pkg / "index.js").write_text(body)


def test_indexing_a_second_colliding_package_keeps_the_first(tmp_path):
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    store.mkdir()
    (src / "app.py").write_text("def main():\n    return 0\n")
    _write_npm_pkg(src, "@a/b", "1.0.0", "export function fromScoped() { return 1; }\n")
    _write_npm_pkg(
        src, "a__b", "1.0.0", "export function fromUnscoped() { return 2; }\n"
    )
    host = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert host["success"] is True

    first = index_dependency(host["repo"], "@a/b", storage_path=str(store))
    second = index_dependency(host["repo"], "a__b", storage_path=str(store))
    assert "error" not in first and "error" not in second, (first, second)
    assert first["snapshot_path"] != second["snapshot_path"]
    assert first["indexed_repo"] != second["indexed_repo"]

    first_js = (Path(first["snapshot_path"]) / "index.js").read_text()
    assert "fromScoped" in first_js, (
        "the second package overwrote the first one's snapshot"
    )
