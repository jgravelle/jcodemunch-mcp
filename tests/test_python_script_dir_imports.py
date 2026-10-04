"""`from checks import x` in a script names the `checks.py` beside it.

A script's own directory is `sys.path[0]`, so a directory of scripts imports
itself by bare module name. `resolve_specifier` tried such a name against the
repo root only, so no edge was built between the scripts.

Each expectation below is what Python itself does: the script directory is the
first directory at or above the importer with no `__init__.py`, it beats the
root, and nothing above it is on `sys.path`.
"""

import pytest

from jcodemunch_mcp.parser.imports import resolve_specifier
from jcodemunch_mcp.tools.find_importers import find_importers
from jcodemunch_mcp.tools.index_folder import index_folder

_FILES = {
    "tools/run.py",
    "tools/checks.py",
    "tools/dup.py",
    "tools/dup/__init__.py",
    "tools/steps/__init__.py",
    "tools/steps/plan.py",
    "tools/steps/cycle.py",
    "pkg/__init__.py",
    "pkg/logging.py",
    "pkg/mod.py",
    "top.py",
    "other/top.py",
    "other/user.py",
    "scripts/helpers.py",
    "scripts/sub/run.py",
}


@pytest.mark.parametrize("specifier, importer, expected", [
    # the module beside the script
    ("checks", "tools/run.py", "tools/checks.py"),
    # a package directory beside the script (`from steps import plan`)
    ("steps", "tools/run.py", "tools/steps/__init__.py"),
    # a package wins over a module of the same name, as in Python's finder
    ("dup", "tools/run.py", "tools/dup/__init__.py"),
    # a subpackage module loaded by the script climbs to the script directory
    ("checks", "tools/steps/plan.py", "tools/checks.py"),
    # inside a package a bare name is absolute: `pkg/logging.py` is not it
    ("logging", "pkg/mod.py", None),
    ("cycle", "tools/steps/plan.py", None),
    # only the script's own directory is on sys.path, not its parent
    ("helpers", "scripts/sub/run.py", None),
    # the script directory beats the root; the root answers when it misses
    ("top", "other/user.py", "other/top.py"),
    ("top", "tools/run.py", "top.py"),
    ("top", "pkg/mod.py", "top.py"),
    # a name with no file, and a non-Python importer
    ("yaml", "tools/run.py", None),
    ("checks", "tools/run.js", None),
])
def test_a_bare_name_resolves_where_python_finds_it(specifier, importer, expected):
    assert resolve_specifier(specifier, importer, _FILES | {importer}) == expected


def test_find_importers_sees_a_script_directory_import(tmp_path):
    src = tmp_path / "src"
    store = tmp_path / "store"
    (src / "tools" / "steps").mkdir(parents=True)
    (src / "scripts" / "sub").mkdir(parents=True)
    store.mkdir()
    (src / "tools" / "checks.py").write_text("def verify():\n    return 1\n")
    (src / "tools" / "run.py").write_text("from checks import verify\n\nverify()\n")
    (src / "tools" / "steps" / "__init__.py").write_text("")
    (src / "tools" / "steps" / "plan.py").write_text("import checks\n")
    (src / "scripts" / "helpers.py").write_text("def h():\n    return 1\n")
    (src / "scripts" / "sub" / "run.py").write_text("import helpers\n")
    result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert result["success"] is True

    def importers(path):
        r = find_importers(repo=result["repo"], file_path=path, storage_path=str(store))
        return {i["file"] for i in r["importers"]}

    assert importers("tools/checks.py") == {"tools/run.py", "tools/steps/plan.py"}
    assert importers("scripts/helpers.py") == set()
