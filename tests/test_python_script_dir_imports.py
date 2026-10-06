"""`from checks import x` in a script names the `checks.py` beside it.

A script's own directory is `sys.path[0]`, so a directory of scripts imports
itself by bare module name. `resolve_specifier` tried such a name against the
repo root only, so no edge was built between the scripts.

Each expectation below is what Python itself does: the script directory is the
first directory at or above the importer with no `__init__.py`, it beats the
root, and nothing above it is treated as being on `sys.path`.

Two shapes build NO edge (maintainer follow-up on #972): a standard-library
name, and a directory with no `__init__.py` under a package at any level. A false
edge there gives a file an importer it does not have, and `find_dead_code`
then stops reporting it.
"""

import pytest

from jcodemunch_mcp.parser.imports import resolve_specifier
from jcodemunch_mcp.tools.find_dead_code import find_dead_code
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
    "tools/types.py",
    "tools/a/b.py",
    "app/__init__.py",
    "app/files/main.py",
    "app/files/helper.py",
    "app/files/types.py",
    "app/tests/test_x.py",
    "app/tests/util.py",
    "app/types/llms/common.py",
    "app/types/llms/openai.py",
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
    # a standard-library name is the standard library, not the file beside it
    ("types", "tools/run.py", None),
    ("types", "app/files/main.py", None),
    # a directory with no `__init__.py` inside a package is a namespace
    # sub-package: a bare name there is absolute
    ("helper", "app/files/main.py", None),
    ("util", "app/tests/test_x.py", None),
    # the same shape one directory deeper: the package is two levels up
    ("openai", "app/types/llms/common.py", None),
    # a path is not a module name
    ("a/b", "tools/run.py", None),
])
def test_a_bare_name_resolves_where_python_finds_it(specifier, importer, expected):
    assert resolve_specifier(specifier, importer, _FILES | {importer}) == expected


def test_a_repo_whose_root_is_a_package_has_no_script_directory():
    files = {"__init__.py", "tools/run.py", "tools/checks.py"}
    assert resolve_specifier("checks", "tools/run.py", files) is None


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


def test_a_namespace_sub_package_gets_no_false_importer(tmp_path):
    """The litellm shape: `app/llms/openai/` has no `__init__.py` and holds an
    `openai.py` nothing imports; `import openai` beside it is the third-party
    package. `app/files/types.py` is the standard-library twin of that."""
    src = tmp_path / "src"
    store = tmp_path / "store"
    (src / "app" / "llms" / "openai").mkdir(parents=True)
    (src / "app" / "files").mkdir(parents=True)
    store.mkdir()
    (src / "main.py").write_text(
        "from app.llms.openai.common_utils import client\n"
        "from app.files.main import run\n\nclient()\nrun()\n"
    )
    (src / "app" / "__init__.py").write_text("")
    (src / "app" / "llms" / "__init__.py").write_text("")
    (src / "app" / "llms" / "openai" / "common_utils.py").write_text(
        "import openai\n\n\ndef client():\n    return openai.OpenAI()\n"
    )
    (src / "app" / "llms" / "openai" / "openai.py").write_text("def orphan():\n    return 1\n")
    (src / "app" / "files" / "main.py").write_text(
        "from types import MappingProxyType\n\n\ndef run():\n    return MappingProxyType({})\n"
    )
    (src / "app" / "files" / "types.py").write_text("class Orphan:\n    pass\n")
    (src / "app" / "types" / "llms").mkdir(parents=True)
    (src / "app" / "types" / "llms" / "common.py").write_text("import openai\n")
    (src / "app" / "types" / "llms" / "openai.py").write_text("def orphan2():\n    return 2\n")
    result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert result["success"] is True

    orphans = ("app/llms/openai/openai.py", "app/files/types.py", "app/types/llms/openai.py")
    for path in orphans:
        r = find_importers(repo=result["repo"], file_path=path, storage_path=str(store))
        assert [i["file"] for i in r["importers"]] == [], path
    dead = find_dead_code(repo=result["repo"], storage_path=str(store), min_confidence=0.0)
    assert {d["file"] for d in dead["dead_files"]} >= set(orphans)
