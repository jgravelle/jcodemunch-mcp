"""Tests for index_dependency (dependency-docs P1, docs/prd-dependency-docs.md).

Fixtures build fake node_modules / virtualenv site-packages layouts under
tmp_path — no real package manager or network involved. The snapshot-then-
index design is exercised end-to-end: resolution, version truth from package
metadata, dist-only npm packages (default skip-dir dodge), identity safety,
and every honest-failure path.
"""

import json

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.index_dependency import (
    index_dependency,
    _pep503_normalize,
    _safe_snapshot_name,
)


def _index_host(src, store):
    result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert result["success"] is True
    return result["repo"], str(store)


def _host_repo(tmp_path):
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    store.mkdir()
    (src / "app.py").write_text("def main():\n    return 0\n")
    return src, store


def _dep_symbols(dep_repo: str, store: str) -> list:
    owner, name = dep_repo.split("/", 1)
    index = IndexStore(base_path=store).load_index(owner, name)
    assert index is not None
    return list(index.symbols)


def _write_npm_pkg(src, name, version, files, pkg_json_extra=None):
    parts = name.split("/")
    pkg_dir = src / "node_modules"
    for p in parts:
        pkg_dir = pkg_dir / p
    pkg_dir.mkdir(parents=True)
    meta = {"name": name, "version": version}
    if pkg_json_extra:
        meta.update(pkg_json_extra)
    (pkg_dir / "package.json").write_text(json.dumps(meta))
    for rel, content in files.items():
        f = pkg_dir / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    return pkg_dir


def _write_pypi_pkg(src, dist_name, version, top_level, files, long_desc=""):
    sp = src / ".venv" / "Lib" / "site-packages"
    di = sp / f"{dist_name.replace('-', '_')}-{version}.dist-info"
    di.mkdir(parents=True)
    body = f"\n\n{long_desc}" if long_desc else ""
    (di / "METADATA").write_text(f"Name: {dist_name}\nVersion: {version}{body}")
    (di / "top_level.txt").write_text(top_level + "\n")
    pkg_dir = sp / top_level
    for rel, content in files.items():
        f = pkg_dir / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    return pkg_dir


# --- pure helpers -------------------------------------------------------------

def test_pep503_normalize():
    assert _pep503_normalize("Typing_Extensions") == "typing-extensions"
    assert _pep503_normalize("zope.interface") == "zope-interface"


def test_safe_snapshot_name():
    # The readable part only. What the digest after it must do (distinct,
    # stable, safe) is tested in test_dependency_snapshot_names_are_distinct.py.
    assert _safe_snapshot_name("@acme/util", "2.0.0", ecosystem="npm").startswith("acme__util@2.0.0-")
    assert _safe_snapshot_name("requests", "", ecosystem="pypi").startswith("requests@unknown-")


# --- npm ----------------------------------------------------------------------

def test_npm_flat_package(tmp_path):
    src, store = _host_repo(tmp_path)
    _write_npm_pkg(src, "leftpad", "1.3.0", {
        "index.js": "function leftPad(s, n) {\n  return s.padStart(n);\n}\nmodule.exports = leftPad;\n",
        "README.md": "# leftpad\n",
    })
    repo, store = _index_host(src, store)
    res = index_dependency(repo, "leftpad", storage_path=store)
    assert "error" not in res, res
    assert res["resolved"]["ecosystem"] == "npm"
    assert res["resolved"]["version"] == "1.3.0"
    assert "leftpad@1.3.0" in res["indexed_repo"]
    assert res["docs"]["readme"] == "README.md"
    assert res["symbol_count"] > 0
    names = {s.get("name") for s in _dep_symbols(res["indexed_repo"], store)}
    assert "leftPad" in names


def test_npm_scoped_package(tmp_path):
    src, store = _host_repo(tmp_path)
    _write_npm_pkg(src, "@acme/util", "2.0.0", {
        "index.js": "export function acmeUtil() { return 1; }\n",
    })
    repo, store = _index_host(src, store)
    res = index_dependency(repo, "@acme/util", storage_path=store)
    assert "error" not in res, res
    assert res["resolved"]["name"] == "@acme/util"
    assert "acme__util@2.0.0" in res["snapshot_path"]
    assert res["symbol_count"] > 0


def test_npm_dist_only_package_indexes(tmp_path):
    """Compiled packages ship only dist/ — a default skip dir. The snapshot
    plus explicit-paths indexing must surface their symbols anyway."""
    src, store = _host_repo(tmp_path)
    _write_npm_pkg(src, "compiled-lib", "3.1.4", {
        "dist/index.js": "function compiledEntry() { return 42; }\nmodule.exports = compiledEntry;\n",
        "dist/index.d.ts": "export declare function compiledEntry(): number;\n",
    })
    repo, store = _index_host(src, store)
    res = index_dependency(repo, "compiled-lib", storage_path=store)
    assert "error" not in res, res
    assert res["symbol_count"] > 0, res
    names = {s.get("name") for s in _dep_symbols(res["indexed_repo"], store)}
    assert "compiledEntry" in names


def test_min_js_not_copied(tmp_path):
    src, store = _host_repo(tmp_path)
    _write_npm_pkg(src, "shiny", "1.0.0", {
        "index.js": "function shiny() { return 1; }\n",
        "shiny.min.js": "function s(){return 1}\n",
    })
    repo, store = _index_host(src, store)
    res = index_dependency(repo, "shiny", storage_path=store)
    assert "error" not in res, res
    from pathlib import Path
    snapshot = Path(res["snapshot_path"])
    assert (snapshot / "index.js").is_file()
    assert not (snapshot / "shiny.min.js").exists()


# --- pypi ---------------------------------------------------------------------

def test_pypi_dist_info_resolution(tmp_path):
    src, store = _host_repo(tmp_path)
    _write_pypi_pkg(src, "requests", "2.32.3", "requests", {
        "__init__.py": "from .api import get\n",
        "api.py": "def get(url, **kwargs):\n    \"\"\"Send a GET request.\"\"\"\n    return url\n",
    }, long_desc="Requests is an elegant HTTP library. " * 10)
    repo, store = _index_host(src, store)
    res = index_dependency(repo, "requests", storage_path=store)
    assert "error" not in res, res
    assert res["resolved"]["ecosystem"] == "pypi"
    assert res["resolved"]["version"] == "2.32.3"
    assert res["docs"]["readme_in_metadata"] is True
    assert "requests@2.32.3" in res["indexed_repo"]
    names = {s.get("name") for s in _dep_symbols(res["indexed_repo"], store)}
    assert "get" in names


def test_pypi_import_name_via_top_level(tmp_path):
    src, store = _host_repo(tmp_path)
    _write_pypi_pkg(src, "PyYAML", "6.0.2", "yaml", {
        "__init__.py": "def safe_load(stream):\n    return stream\n",
    })
    repo, store = _index_host(src, store)
    res = index_dependency(repo, "yaml", storage_path=store)
    assert "error" not in res, res
    assert res["resolved"]["name"] == "PyYAML"
    assert res["resolved"]["version"] == "6.0.2"


def test_pypi_name_normalization(tmp_path):
    src, store = _host_repo(tmp_path)
    _write_pypi_pkg(src, "typing-extensions", "4.12.0", "typing_extensions_pkg", {
        "__init__.py": "def override(f):\n    return f\n",
    })
    repo, store = _index_host(src, store)
    res = index_dependency(repo, "Typing_Extensions", storage_path=store)
    assert "error" not in res, res
    assert res["resolved"]["version"] == "4.12.0"


def test_pypi_single_module_unsupported(tmp_path):
    src, store = _host_repo(tmp_path)
    sp = src / ".venv" / "Lib" / "site-packages"
    di = sp / "six-1.16.0.dist-info"
    di.mkdir(parents=True)
    (di / "METADATA").write_text("Name: six\nVersion: 1.16.0")
    (di / "top_level.txt").write_text("six\n")
    (sp / "six.py").write_text("PY3 = True\n")
    repo, store = _index_host(src, store)
    res = index_dependency(repo, "six", storage_path=store)
    assert "error" in res
    assert "single-module" in res["error"]


# --- honesty + control paths ---------------------------------------------------

def test_not_installed_lists_looked_in(tmp_path):
    src, store = _host_repo(tmp_path)
    repo, store = _index_host(src, store)
    res = index_dependency(repo, "ghost-package", storage_path=store)
    assert "error" in res
    assert res["looked_in"], res


def test_ecosystem_pin_skips_node_modules(tmp_path):
    src, store = _host_repo(tmp_path)
    _write_npm_pkg(src, "dual", "1.0.0", {"index.js": "function dual() {}\n"})
    repo, store = _index_host(src, store)
    res = index_dependency(repo, "dual", ecosystem="pypi", storage_path=store)
    assert "error" in res  # pinned to pypi; the npm install must not count


def test_cache_hit_second_call(tmp_path):
    src, store = _host_repo(tmp_path)
    _write_npm_pkg(src, "leftpad", "1.3.0", {
        "index.js": "function leftPad(s, n) { return s; }\n",
    })
    repo, store = _index_host(src, store)
    first = index_dependency(repo, "leftpad", storage_path=store)
    second = index_dependency(repo, "leftpad", storage_path=store)
    assert "error" not in second, second
    assert second["_meta"]["cache_hit"] is True
    assert second["indexed_repo"] == first["indexed_repo"]
    assert second["symbol_count"] == first["symbol_count"] > 0


def test_truncation_is_reported(tmp_path):
    src, store = _host_repo(tmp_path)
    files = {f"mod{i}.js": f"function fn{i}() {{ return {i}; }}\n" for i in range(5)}
    _write_npm_pkg(src, "bigpkg", "1.0.0", files)
    repo, store = _index_host(src, store)
    res = index_dependency(repo, "bigpkg", max_files=2, storage_path=store)
    assert "error" not in res, res
    assert res["_meta"]["truncated"] is True
    assert any("max_files" in n for n in res["_meta"]["notes"])


def test_thin_docs_note(tmp_path):
    src, store = _host_repo(tmp_path)
    _write_npm_pkg(src, "bare", "0.1.0", {"index.js": "function bare() {}\n"})
    repo, store = _index_host(src, store)
    res = index_dependency(repo, "bare", storage_path=store)
    assert "error" not in res, res
    assert res["docs"]["readme"] is None
    assert any("minimal doc" in n for n in res["_meta"]["notes"])


def test_host_index_untouched(tmp_path):
    """The dependency must land in its OWN repo, never merge into the host."""
    src, store = _host_repo(tmp_path)
    _write_npm_pkg(src, "leftpad", "1.3.0", {"index.js": "function leftPad() {}\n"})
    repo, store = _index_host(src, store)
    owner, name = repo.split("/", 1)
    before = IndexStore(base_path=store).load_index(owner, name)
    host_files_before = set(before.source_files)
    res = index_dependency(repo, "leftpad", storage_path=store)
    assert "error" not in res, res
    assert res["indexed_repo"] != repo
    after = IndexStore(base_path=store).load_index(owner, name)
    assert set(after.source_files) == host_files_before


def test_bad_args(tmp_path):
    src, store = _host_repo(tmp_path)
    repo, store = _index_host(src, store)
    assert "error" in index_dependency(repo, "x", ecosystem="cargo", storage_path=store)
    assert "error" in index_dependency(repo, "", storage_path=store)
    assert "error" in index_dependency("no/such-repo", "x", storage_path=store)
