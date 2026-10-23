"""index_dependency only reads what is installed inside the repo, and copies safely.

`package` is joined onto `node_modules` or a virtualenv's site-packages, and a
`top_level.txt` line from the installed distribution is joined onto
site-packages too. Each is validated as a package or module name, and every
directory it resolves to (after symlinks) must be inside the repo and be one
package: never a directory that is or contains a store root, and for npm a
directory with its own package.json. The snapshot copy applies the same
per-file checks the indexer applies to a walked file: no secret or credential
file, nothing over the size cap, nothing binary. And `index_folder(paths=...)`
honors .gitignore like the walk does; only index_dependency's own filtered
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
    skipped = res["skipped_on_copy"]  # in the body: _meta is stripped by default
    assert sum(skipped.values()) == 3, skipped


def test_explicit_paths_honor_gitignore(tmp_path):
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


def test_a_listed_folder_honors_a_gitignore_above_it(tmp_path):
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
    deps = os.path.join(host["store"], "deps")
    for entry in os.listdir(deps):
        # Every snapshot is one directory directly under deps/; nothing is
        # written into deps/ itself.
        assert os.path.isdir(os.path.join(deps, entry)), entry
        assert ".." not in entry and "/" not in entry


def test_a_link_to_the_repo_root_is_refused(host):
    node_modules = host["src"] / "node_modules"
    node_modules.mkdir()
    try:
        os.symlink(host["src"], node_modules / "self", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "self", ecosystem="npm", storage_path=host["store"])
    assert "error" in res and "repo root" in res["error"], res


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


@pytest.mark.parametrize("folder", ["env", "venv", "tools/node_modules/ws"])
def test_a_workspace_named_like_a_store_keeps_its_gitignore(host, folder):
    """Only the repo's top-level node_modules and the site-packages actually
    found are exempt; a folder that merely has one of those names is not."""
    (host["src"] / ".gitignore").write_text(f"{folder}/local.js\n")
    target = host["src"] / folder
    target.mkdir(parents=True)
    (target / "package.json").write_text(json.dumps({"name": "ws", "version": "0.1.0"}))
    (target / "index.js").write_text("export function ws() { return 1; }\n")
    (target / "local.js").write_text("export const local = 'internal';\n")
    node_modules = host["src"] / "node_modules"
    node_modules.mkdir(exist_ok=True)
    try:
        os.symlink(target, node_modules / "ws", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "ws", ecosystem="npm", storage_path=host["store"])
    assert "error" not in res, res
    copied = set(os.listdir(res["snapshot_path"]))
    assert "index.js" in copied and "local.js" not in copied, copied


def test_a_virtualenv_linked_out_of_the_repo_says_so(host):
    real_sp = host["outside"] / "site-packages"
    (real_sp / "pkg").mkdir(parents=True)
    (real_sp / "pkg" / "__init__.py").write_text(MARKER)
    lib = host["src"] / ".venv" / "Lib"
    lib.mkdir(parents=True)
    try:
        os.symlink(real_sp, lib / "site-packages", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "pkg", ecosystem="pypi", storage_path=host["store"])
    assert "error" in res and "outside the repo" in res["error"], res


def test_a_site_packages_link_inside_the_repo_still_resolves(host):
    target = host["src"] / "lib" / "mypkg"
    target.mkdir(parents=True)
    (target / "__init__.py").write_text("def mypkg_fn():\n    return 1\n")
    sp = host["src"] / ".venv" / "Lib" / "site-packages"
    di = sp / "mypkg-1.0.dist-info"
    di.mkdir(parents=True)
    (di / "METADATA").write_text("Name: mypkg\nVersion: 1.0")
    (di / "top_level.txt").write_text("mypkg\n")
    try:
        os.symlink(target, sp / "mypkg", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "mypkg", ecosystem="pypi", storage_path=host["store"])
    assert "error" not in res, res


def test_one_virtualenv_linked_out_does_not_hide_another(host):
    lib = host["src"] / ".venv" / "Lib"
    lib.mkdir(parents=True)
    try:
        os.symlink(host["outside"], lib / "site-packages", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    real = host["src"] / "venv" / "Lib" / "site-packages" / "goodpkg"
    real.mkdir(parents=True)
    (real / "__init__.py").write_text("def goodpkg_fn():\n    return 1\n")
    res = index_dependency(host["repo"], "goodpkg", ecosystem="pypi", storage_path=host["store"])
    assert "error" not in res, res
    assert not _store_has_marker(host["store"])


@pytest.mark.parametrize("how", ["top_level", "bare"])
def test_a_pypi_name_linked_to_the_repo_root_is_refused(host, how):
    sp = host["src"] / ".venv" / "Lib" / "site-packages"
    sp.mkdir(parents=True)
    if how == "top_level":
        di = sp / "rootdist-1.0.dist-info"
        di.mkdir()
        (di / "METADATA").write_text("Name: rootdist\nVersion: 1.0")
        (di / "top_level.txt").write_text("rootlink\n")
        link, package = sp / "rootlink", "rootdist"
    else:
        link, package = sp / "selfpkg", "selfpkg"
    try:
        os.symlink(host["src"], link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], package, ecosystem="pypi", storage_path=host["store"])
    assert "error" in res, res


@pytest.mark.parametrize("target", ["node_modules", "node_modules/@types"])
def test_a_link_to_node_modules_or_a_scope_is_refused(host, target):
    """A real package sits next to the link, so only the store refusal (not
    "no indexable code") can produce the error."""
    node_modules = host["src"] / "node_modules"
    real = node_modules / "@types" / "real"
    real.mkdir(parents=True)
    (real / "package.json").write_text(json.dumps({"name": "@types/real", "version": "1.0.0"}))
    (real / "index.js").write_text("function realPkg() { return 1; }\n")
    try:
        os.symlink(host["src"] / target, node_modules / "everything", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "everything", ecosystem="npm", storage_path=host["store"])
    assert "error" in res and "only a package directory" in res["error"], res
    assert not os.path.isdir(os.path.join(host["store"], "deps")) or not os.listdir(
        os.path.join(host["store"], "deps")
    )


@pytest.mark.parametrize("target", [".venv/Lib", ".venv"])
def test_a_pypi_link_to_part_of_a_virtualenv_is_refused(host, target):
    sp = host["src"] / ".venv" / "Lib" / "site-packages"
    (sp / "realpkg").mkdir(parents=True)
    (sp / "realpkg" / "__init__.py").write_text("def real_fn():\n    return 1\n")
    try:
        os.symlink(host["src"] / target, sp / "allpkgs", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "allpkgs", ecosystem="pypi", storage_path=host["store"])
    assert "error" in res and "only a package directory" in res["error"], res


def test_a_link_to_a_multi_package_store_is_refused(host):
    """yarn's node_modules/.store (or any directory holding several packages)
    has no package.json of its own, so it is not one package."""
    store = host["src"] / "node_modules" / ".store"
    for name in ("a-npm-1.0.0-x", "b-npm-1.0.0-y"):
        pkg = store / name / "package"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text(json.dumps({"name": name, "version": "1.0.0"}))
        (pkg / "index.js").write_text("function storePkg() { return 1; }\n")
    try:
        os.symlink(store, host["src"] / "node_modules" / "everything", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "everything", ecosystem="npm", storage_path=host["store"])
    assert "error" in res and "only a package directory" in res["error"], res


def _link_file(target, link):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a file symlink here")


def test_a_package_json_linked_out_of_the_repo_is_not_read(host):
    (host["outside"] / "package.json").write_text(json.dumps({"name": "LEAKNAME", "version": "9.9.9"}))
    pkg = host["src"] / "node_modules" / "metalink"
    pkg.mkdir(parents=True)
    (pkg / "index.js").write_text("function metalink() { return 1; }")
    _link_file(host["outside"] / "package.json", pkg / "package.json")
    res = index_dependency(host["repo"], "metalink", ecosystem="npm", storage_path=host["store"])
    assert "error" in res, res
    assert "LEAKNAME" not in json.dumps(res)


@pytest.mark.parametrize("linked", ["METADATA", "dist-info"])
def test_pypi_metadata_linked_out_of_the_repo_is_not_read(host, linked):
    out_di = host["outside"] / "leak-6.6.6.dist-info"
    out_di.mkdir()
    (out_di / "METADATA").write_text("Name: OUTNAME\nVersion: 6.6.6")
    sp = host["src"] / ".venv" / "Lib" / "site-packages"
    (sp / "leak").mkdir(parents=True)
    (sp / "leak" / "__init__.py").write_text("def leak_fn():\n    return 1\n")
    if linked == "METADATA":
        di = sp / "leak-1.0.dist-info"
        di.mkdir()
        _link_file(out_di / "METADATA", di / "METADATA")
    else:
        try:
            os.symlink(out_di, sp / "leak-1.0.dist-info", target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "leak", ecosystem="pypi", storage_path=host["store"])
    # Refused by name, not served from the bare directory without a version.
    assert "error" in res and "outside the repo" in res["error"], res
    assert "OUTNAME" not in json.dumps(res), res
    assert "6.6.6" not in json.dumps(res), res


def test_a_pypi_package_dir_linked_out_says_so(host):
    (host["outside"] / "evilpkg").mkdir()
    (host["outside"] / "evilpkg" / "__init__.py").write_text(MARKER)
    sp = host["src"] / ".venv" / "Lib" / "site-packages"
    sp.mkdir(parents=True)
    try:
        os.symlink(host["outside"] / "evilpkg", sp / "evilpkg", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "evilpkg", ecosystem="pypi", storage_path=host["store"])
    assert "error" in res and "outside the repo" in res["error"], res
    assert not _store_has_marker(host["store"])


def test_pnpm_package_json_linked_inside_the_repo_still_resolves(host):
    real = host["src"] / "node_modules" / ".pnpm" / "foo@1.0.0" / "node_modules" / "foo"
    real.mkdir(parents=True)
    (real / "package.json").write_text(json.dumps({"name": "foo", "version": "1.0.0"}))
    (real / "index.js").write_text("export function foo() { return 1; }")
    pkg = host["src"] / "node_modules" / "foo"
    pkg.mkdir()
    (pkg / "index.js").write_text("export function foo() { return 1; }")
    _link_file(real / "package.json", pkg / "package.json")
    res = index_dependency(host["repo"], "foo", ecosystem="npm", storage_path=host["store"])
    assert "error" not in res, res
    assert res["resolved"]["version"] == "1.0.0"


def test_a_linked_out_dist_info_matched_by_import_name_is_withheld(host):
    out_di = host["outside"] / "PyYAML-6.0.dist-info"
    out_di.mkdir()
    (out_di / "METADATA").write_text("Name: PyYAML\nVersion: 6.0")
    (out_di / "top_level.txt").write_text("yaml\n")
    sp = host["src"] / ".venv" / "Lib" / "site-packages"
    (sp / "yaml").mkdir(parents=True)
    (sp / "yaml" / "__init__.py").write_text("def load():\n    return 1\n")
    try:
        os.symlink(out_di, sp / "PyYAML-6.0.dist-info", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "yaml", ecosystem="pypi", storage_path=host["store"])
    # The linked-out dist-info cannot be read, so it is not used: the directory
    # is served, the version left unknown, and the withheld metadata named.
    assert "error" not in res, res
    assert res["resolved"]["version"] == ""
    assert res["resolved"]["withheld_metadata"] == [".venv/Lib/site-packages/PyYAML-6.0.dist-info"]


def test_an_unrelated_linked_out_dist_info_does_not_block_a_package(host):
    out_di = host["outside"] / "requests-2.0.dist-info"
    out_di.mkdir()
    (out_di / "METADATA").write_text("Name: requests\nVersion: 2.0")
    sp = host["src"] / ".venv" / "Lib" / "site-packages"
    (sp / "foo").mkdir(parents=True)
    (sp / "foo" / "__init__.py").write_text("def foo_fn():\n    return 1\n")
    try:
        os.symlink(out_di, sp / "requests-2.0.dist-info", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "foo", ecosystem="pypi", storage_path=host["store"])
    assert "error" not in res, res
    assert res["resolved"]["withheld_metadata"] == [".venv/Lib/site-packages/requests-2.0.dist-info"]
    assert res["resolved"]["withheld_metadata_total"] == 1
    assert res["resolved"]["withheld_metadata_truncated"] is False


def test_a_dist_info_whose_in_repo_top_level_names_the_package_is_refused(host):
    out = host["outside"] / "METADATA"
    out.write_text("Name: OUTNAME\nVersion: 6.6.6")
    sp = host["src"] / ".venv" / "Lib" / "site-packages"
    (sp / "yaml").mkdir(parents=True)
    (sp / "yaml" / "__init__.py").write_text("def load():\n    return 1\n")
    di = sp / "PyYAML-6.0.dist-info"
    di.mkdir()
    (di / "top_level.txt").write_text("yaml\n")
    _link_file(out, di / "METADATA")
    res = index_dependency(host["repo"], "yaml", ecosystem="pypi", storage_path=host["store"])
    assert "error" in res and "outside the repo" in res["error"], res
    assert "OUTNAME" not in json.dumps(res)


def test_the_requested_top_level_linked_out_is_refused_not_swapped(host):
    (host["outside"] / "foo").mkdir()
    (host["outside"] / "foo" / "__init__.py").write_text(MARKER)
    sp = host["src"] / ".venv" / "Lib" / "site-packages"
    di = sp / "foo-1.0.dist-info"
    di.mkdir(parents=True)
    (di / "METADATA").write_text("Name: foo\nVersion: 1.0")
    (di / "top_level.txt").write_text("foo\nbar\n")
    (sp / "bar").mkdir()
    (sp / "bar" / "__init__.py").write_text("def bar_fn():\n    return 1\n")
    try:
        os.symlink(host["outside"] / "foo", sp / "foo", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "foo", ecosystem="pypi", storage_path=host["store"])
    assert "error" in res and "outside the repo" in res["error"], res
    assert not _store_has_marker(host["store"])


def test_a_symlinked_file_in_a_package_is_counted_as_skipped(host):
    pkg = host["src"] / "node_modules" / "withlink"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"name": "withlink", "version": "1.0.0"}))
    (pkg / "index.js").write_text("function withLink() { return 1; }")
    _link_file(host["outside"] / "index.js", pkg / "extra.js")
    res = index_dependency(host["repo"], "withlink", ecosystem="npm", storage_path=host["store"])
    assert "error" not in res, res
    assert res["skipped_on_copy"].get("symlink") == 1, res.get("skipped_on_copy")
    assert not _store_has_marker(host["store"])


def test_the_withheld_list_is_capped_with_its_total(host):
    from jcodemunch_mcp.tools import index_dependency as dep_module

    sp = host["src"] / ".venv" / "Lib" / "site-packages"
    (sp / "foo").mkdir(parents=True)
    (sp / "foo" / "__init__.py").write_text("def foo_fn():\n    return 1\n")
    n = dep_module._WITHHELD_LIST_CAP + 5
    for i in range(n):
        out_di = host["outside"] / f"pkg{i}-1.0.dist-info"
        out_di.mkdir()
        (out_di / "METADATA").write_text(f"Name: pkg{i}\nVersion: 1.0")
        try:
            os.symlink(out_di, sp / f"pkg{i}-1.0.dist-info", target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("this platform cannot create a directory symlink here")
    res = index_dependency(host["repo"], "foo", ecosystem="pypi", storage_path=host["store"])
    resolved = res["resolved"]
    assert len(resolved["withheld_metadata"]) == dep_module._WITHHELD_LIST_CAP
    assert resolved["withheld_metadata_total"] == n
    assert resolved["withheld_metadata_truncated"] is True
