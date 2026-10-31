"""Callers in the same Go package are found (LEDGER L-262).

Go files in one directory form one package and call each other with no import
statement. `get_call_hierarchy`'s callers pass only looked at files the import
graph linked to the symbol's file, so a same-package caller was never
considered: gin's who-calls found 1 of 4 callers on a real index.
"""

from __future__ import annotations

from jcodemunch_mcp.tools.get_call_hierarchy import get_call_hierarchy
from jcodemunch_mcp.tools.index_folder import index_folder


def _index(tmp_path, files: dict[str, str]):
    src = tmp_path / "src"
    for rel, text in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    store = tmp_path / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"], r
    return r["repo"], str(store)


def _callers(repo, store, symbol_id):
    h = get_call_hierarchy(
        repo=repo, symbol_id=symbol_id, direction="callers", storage_path=store
    )
    assert "error" not in h, h
    return h


SAME_PACKAGE = {
    "gin/a.go": "package gin\n\nfunc helper() int { return 1 }\n",
    "gin/b.go": "package gin\n\nfunc Use() int { return helper() }\n",
    "gin/c.go": "package gin\n\nfunc Other() int { return helper() + 1 }\n",
}


def test_a_same_package_caller_is_found(tmp_path):
    repo, store = _index(tmp_path, SAME_PACKAGE)
    h = _callers(repo, store, "gin/a.go::helper#function")
    assert sorted(c["name"] for c in h["callers"]) == ["Other", "Use"], h


def test_found_callers_are_no_longer_disclosed_as_unreached(tmp_path):
    repo, store = _index(tmp_path, SAME_PACKAGE)
    h = _callers(repo, store, "gin/a.go::helper#function")
    gap = (h.get("_meta") or {}).get("caller_graph_incomplete") or {}
    assert "gin/b.go" not in gap.get("excluded_files", []), gap
    assert "gin/b.go" in {c["file"] for c in h["callers"]}, h


def test_a_root_package_caller_is_found(tmp_path):
    repo, store = _index(
        tmp_path,
        {
            "a.go": "package main\n\nfunc helper() int { return 1 }\n",
            "main.go": "package main\n\nfunc main() { helper() }\n",
        },
    )
    h = _callers(repo, store, "a.go::helper#function")
    assert [c["name"] for c in h["callers"]] == ["main"], h


def test_another_package_with_its_own_helper_is_not_a_caller(tmp_path):
    """A file in another directory calls its own `helper`, not this one."""
    files = dict(SAME_PACKAGE)
    files["other/x.go"] = (
        "package other\n\nfunc helper() int { return 2 }\n\n"
        "func Run() int { return helper() }\n"
    )
    repo, store = _index(tmp_path, files)
    h = _callers(repo, store, "gin/a.go::helper#function")
    assert sorted(c["name"] for c in h["callers"]) == ["Other", "Use"], h


def test_a_python_file_in_the_same_directory_is_still_gated(tmp_path):
    """Only a package-directory language shares scope by directory: a Python
    file beside the symbol's file still needs an import to call it."""
    repo, store = _index(
        tmp_path,
        {
            "pkg/a.py": "def helper():\n    return 1\n",
            "pkg/b.py": "def helper():\n    return 2\n\n\ndef use():\n    return helper()\n",
        },
    )
    h = _callers(repo, store, "pkg/a.py::helper#function")
    assert h["callers"] == [], h
