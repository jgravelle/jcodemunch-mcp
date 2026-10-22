"""index_dependency only reads what is installed inside the repo, and copies safely.

`package` is joined onto `node_modules` or a virtualenv's site-packages, and a
`top_level.txt` line from the installed distribution is joined onto
site-packages too. Each is validated as a package or module name, and every
directory it resolves to (after symlinks) must stay inside the folder it was
looked up in and inside the repo. The snapshot copy applies the same
per-file checks the indexer applies to a walked file: no secret or credential
file, nothing over the size cap, nothing binary. And `index_folder(paths=...)`
honours .gitignore like the walk does; only index_dependency's own filtered
snapshot opts out.

Each test plants a marker outside the repo (or a file that must not be copied)
and asserts it never reaches the index store.
"""

from __future__ import annotations

import json
import os

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.index_dependency import index_dependency
from jcodemunch_mcp.tools.index_folder import index_folder

MARKER = "def outside_marker():\n    return 'should never be indexed'\n"


@pytest.fixture
def host(tmp_path):
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    store.mkdir()
    (src / "app.py").write_text("def main():\n    return 0\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "mod.py").write_text(MARKER)
    (outside / "index.js").write_text("function outsideMarker() { return 1; }\n")
    result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert result["success"] is True
    return {"src": src, "store": str(store), "repo": result["repo"], "outside": outside}


def _store_has_marker(store: str) -> bool:
    for dirpath, _dirs, files in os.walk(store):
        for name in files:
            try:
                with open(os.path.join(dirpath, name), "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            if b"outside_marker" in data or b"outsideMarker" in data:
                return True
    return False


@pytest.mark.parametrize(
    "package_of,ecosystem",
    [
        pytest.param(lambda h: "../../outside", "npm", id="npm-dotdot"),
        pytest.param(lambda h: str(h["outside"]), "npm", id="npm-absolute"),
        pytest.param(lambda h: "@scope/../../../outside", "npm", id="npm-scoped-dotdot"),
        pytest.param(lambda h: "../../../../outside", "pypi", id="pypi-dotdot"),
        pytest.param(lambda h: str(h["outside"]), "pypi", id="pypi-absolute"),
        pytest.param(lambda h: "..\\..\\outside", "auto", id="backslash"),
    ],
)
def test_a_path_shaped_package_is_refused(host, package_of, ecosystem):
    # Make the lookup folders exist so a join would otherwise reach outside.
    (host["src"] / "node_modules").mkdir(exist_ok=True)
    (host["src"] / ".venv" / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
    res = index_dependency(host["repo"], package_of(host), ecosystem=ecosystem, storage_path=host["store"])
    assert "error" in res, res
    assert not _store_has_marker(host["store"])


def test_a_top_level_entry_that_is_a_path_is_ignored(host):
    sp = host["src"] / ".venv" / "Lib" / "site-packages"
    di = sp / "evil-1.0.dist-info"
    di.mkdir(parents=True)
    (di / "METADATA").write_text("Name: evil\nVersion: 1.0")
    (di / "top_level.txt").write_text("../../../../outside\n")
    res = index_dependency(host["repo"], "evil", ecosystem="pypi", storage_path=host["store"])
    assert "error" in res, res
    assert not _store_has_marker(host["store"])


def test_a_package_symlinked_outside_the_repo_is_refused(host):
    node_modules = host["src"] / "node_modules"
    node_modules.mkdir()
    try:
        os.symlink(host["outside"], node_modules / "linked", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "linked", ecosystem="npm", storage_path=host["store"])
    assert "error" in res, res
    assert not _store_has_marker(host["store"])


def test_valid_names_still_resolve(host):
    pkg = host["src"] / "node_modules" / "@acme" / "util"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"name": "@acme/util", "version": "2.0.0"}))
    (pkg / "index.js").write_text("export function acmeUtil() { return 1; }\n")
    res = index_dependency(host["repo"], "@acme/util", storage_path=host["store"])
    assert "error" not in res, res


def test_the_copy_skips_secret_oversize_and_binary_files(host, monkeypatch):
    from jcodemunch_mcp.tools import index_dependency as dep_module

    monkeypatch.setattr(dep_module, "get_max_file_size", lambda *a, **k: 2000)
    pkg = host["src"] / "node_modules" / "leaky"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"name": "leaky", "version": "1.0.0"}))
    (pkg / "index.js").write_text("function leaky() { return 1; }\n")
    (pkg / "credentials.json").write_text('{"token": "s3cr3t"}')
    (pkg / "huge.js").write_text("// " + "x" * 5000 + "\n")
    (pkg / "blob.js").write_bytes(b"\x00\x01\x02\x03" * 100)
    res = index_dependency(host["repo"], "leaky", storage_path=host["store"])
    assert "error" not in res, res
    snapshot = res["snapshot_path"]
    copied = set(os.listdir(snapshot))
    assert "index.js" in copied
    assert {"credentials.json", "huge.js", "blob.js"}.isdisjoint(copied), copied
    skipped = res["_meta"]["skipped_on_copy"]
    assert sum(skipped.values()) == 3, skipped


def test_explicit_paths_honour_gitignore(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text("local_settings.py\n")
    (root / "app.py").write_text("def app():\n    return 1\n")
    (root / "local_settings.py").write_text("def local_setting():\n    return 'internal'\n")
    store = tmp_path / "store"
    res = index_folder(
        str(root), use_ai_summaries=False, storage_path=str(store),
        paths=["app.py", "local_settings.py"],
    )
    assert res["success"] is True
    assert any("gitignored" in w for w in res.get("warnings", [])), res.get("warnings")
    owner, name = res["repo"].split("/", 1)
    names = {s.get("name") for s in IndexStore(base_path=str(store)).load_index(owner, name).symbols}
    assert "app" in names and "local_setting" not in names


def test_a_package_whose_gitignore_lists_dist_still_indexes(host):
    pkg = host["src"] / "node_modules" / "distonly"
    (pkg / "dist").mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"name": "distonly", "version": "1.0.0"}))
    (pkg / ".gitignore").write_text("dist/\n")
    (pkg / "dist" / "index.js").write_text("export function fromDist() { return 1; }\n")
    res = index_dependency(host["repo"], "distonly", storage_path=host["store"])
    assert "error" not in res, res
    assert res["symbol_count"] > 0


def test_a_virtualenv_linked_out_of_the_repo_is_not_followed(host):
    real_sp = host["outside"] / "site-packages"
    (real_sp / "secretmod").mkdir(parents=True)
    (real_sp / "secretmod" / "__init__.py").write_text(MARKER)
    lib = host["src"] / ".venv" / "Lib"
    lib.mkdir(parents=True)
    try:
        os.symlink(real_sp, lib / "site-packages", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "secretmod", ecosystem="pypi", storage_path=host["store"])
    assert "error" in res, res
    assert not _store_has_marker(host["store"])


def test_a_workspace_link_inside_the_repo_still_resolves(host):
    target = host["src"] / "packages" / "util"
    target.mkdir(parents=True)
    (target / "package.json").write_text(json.dumps({"name": "@acme/util", "version": "0.1.0"}))
    (target / "index.js").write_text("export function workspaceUtil() { return 1; }\n")
    scope = host["src"] / "node_modules" / "@acme"
    scope.mkdir(parents=True)
    try:
        os.symlink(target, scope / "util", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "@acme/util", storage_path=host["store"])
    assert "error" not in res, res


def test_a_snapshot_from_an_older_copy_is_rebuilt(host):
    pkg = host["src"] / "node_modules" / "leftover"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"name": "leftover", "version": "1.0.0"}))
    (pkg / "index.js").write_text("function leftover() { return 1; }\n")
    stale = os.path.join(host["store"], "deps", "leftover@1.0.0")
    os.makedirs(stale)
    with open(os.path.join(stale, "credentials.json"), "w") as fh:
        fh.write('{"token": "old"}')
    res = index_dependency(host["repo"], "leftover", storage_path=host["store"])
    assert "error" not in res, res
    assert not os.path.exists(os.path.join(stale, "credentials.json"))


def test_a_name_with_a_trailing_newline_is_refused(host):
    res = index_dependency(host["repo"], "lodash\n", storage_path=host["store"])
    assert "error" in res and "not a package name" in res["error"], res


def test_a_listed_folder_honours_a_gitignore_above_it(tmp_path):
    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    (root / ".gitignore").write_text("sub/local_settings.py\n")
    (root / "sub" / "a.py").write_text("def a():\n    return 1\n")
    (root / "sub" / "local_settings.py").write_text("def local_setting():\n    return 'internal'\n")
    store = tmp_path / "store"
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store), paths=["sub"])
    assert res["success"] is True
    owner, name = res["repo"].split("/", 1)
    names = {s.get("name") for s in IndexStore(base_path=str(store)).load_index(owner, name).symbols}
    assert "a" in names and "local_setting" not in names


@pytest.mark.parametrize("version", ["1.0.0/..", "1.0.0/../../../victim", "..", "../x"])
def test_a_crafted_version_cannot_move_the_snapshot(host, tmp_path, version):
    """The version comes from the installed package.json. It must not steer the
    rebuild onto deps/ itself, or the copy outside the store."""
    other = os.path.join(host["store"], "deps", "keepme@1.0.0")
    os.makedirs(other)
    with open(os.path.join(other, "keep.js"), "w") as fh:
        fh.write("function keep() { return 1; }\n")
    pkg = host["src"] / "node_modules" / "crafted"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"name": "crafted", "version": version}))
    (pkg / "index.js").write_text("function crafted() { return 1; }\n")
    index_dependency(host["repo"], "crafted", storage_path=host["store"])
    assert os.path.exists(os.path.join(other, "keep.js"))  # no other snapshot removed
    assert not (tmp_path / "victim").exists()
    for entry in os.listdir(os.path.join(host["store"], "deps")):
        assert ".." not in entry and "/" not in entry


def test_a_link_to_the_repo_root_is_refused(host):
    node_modules = host["src"] / "node_modules"
    node_modules.mkdir()
    try:
        os.symlink(host["src"], node_modules / "self", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "self", ecosystem="npm", storage_path=host["store"])
    assert "error" in res and "outside the repo" in res["error"], res


def test_a_workspace_package_keeps_its_gitignored_files_out(host):
    (host["src"] / ".gitignore").write_text("packages/util/local_settings.js\n")
    target = host["src"] / "packages" / "util"
    target.mkdir(parents=True)
    (target / "package.json").write_text(json.dumps({"name": "@acme/util", "version": "0.1.0"}))
    (target / "index.js").write_text("export function workspaceUtil() { return 1; }\n")
    (target / "local_settings.js").write_text("export const localSetting = 'internal';\n")
    scope = host["src"] / "node_modules" / "@acme"
    scope.mkdir(parents=True)
    try:
        os.symlink(target, scope / "util", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "@acme/util", storage_path=host["store"])
    assert "error" not in res, res
    copied = set(os.listdir(res["snapshot_path"]))
    assert "index.js" in copied and "local_settings.js" not in copied, copied
