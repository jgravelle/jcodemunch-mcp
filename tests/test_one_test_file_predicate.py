"""One rule says whether a path is a test file (LEDGER L-101).

Six modules under `src/` each kept their own rule and no two agreed.
`find_dead_code` and `get_dead_code_v2` tested `"/tests/" in path`, which
needs a leading slash, so a test directory at the repository root was not
one: `tests/helpers.py` was reported dead at confidence 1.0, and the deletion
investigator, which asks `find_dead_code` since L-94, then called a name that
file imports "imported by nothing live". `check_delete_safe` and
`find_similar_symbols` saw the root directory and missed `a_test.py` and
`__tests__/`.

The cases are pinned here against the shared rule, every module-level
predicate under `src/` is checked against the same cases, and the two
reported shapes run through the product.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

from jcodemunch_mcp.investigator import REFUTED, investigate_deletion_safety
from jcodemunch_mcp.tools.find_dead_code import find_dead_code
from jcodemunch_mcp.tools.index_folder import index_folder

SRC = Path(__file__).resolve().parent.parent / "src" / "jcodemunch_mcp"
AUTHORITY = "tools/_test_paths.py"

TEST_PATHS = [
    "tests/helpers.py",
    "test/helpers.py",
    "__tests__/shapes.js",
    "Tests/Unit/Foo.php",
    "pkg/tests/helpers.py",
    "pkg/__tests__/a.js",
    "pkg\\tests\\helpers.py",
    "test_a.py",
    "pkg/test_a.py",
    "pkg/a_test.py",
    "pkg/a_test.go",
    "conftest.py",
    "pkg/conftest.py",
    "src/a.spec.ts",
    "src/a.test.js",
    "src/a_spec.rb",
    "src/test/java/Foo.java",
    "src/test_utils/x.py",
    "app/tests.py",
    "src/protest_test.py",
]
SOURCE_PATHS = [
    "",
    "src/a.py",
    "src/a.ts",
    "src/latest.py",
    "src/contest.py",
    "src/attestation.py",
    "src/testing/util.py",
    "src/testimonials.tsx",
    "src/pytest_plugin.py",
    "testdata/x.go",
    "spec/openapi.yaml",
    ".github/workflows/test.yml",
    "src/main/java/Foo.java",
]

_PREDICATE_NAME = re.compile(r"^_?is_test_(file|path)$")
_RULE_CONSTANT = re.compile(r"^_?TEST_(FILE|FILENAME|PATH|DIR)\w*$")


def _shared():
    return importlib.import_module("jcodemunch_mcp.tools._test_paths").is_test_file


@pytest.mark.parametrize("path", TEST_PATHS)
def test_a_test_path_is_a_test(path):
    assert _shared()(path) is True


@pytest.mark.parametrize("path", SOURCE_PATHS)
def test_a_source_path_is_not_a_test(path):
    assert _shared()(path) is False


def _modules():
    for p in sorted(SRC.rglob("*.py")):
        yield p.relative_to(SRC).as_posix(), ast.parse(p.read_text(encoding="utf-8"))


def test_no_module_writes_its_own_rule():
    """A definition or a rule constant outside the authority is a second answer."""
    found = []
    for rel, tree in _modules():
        if rel == AUTHORITY:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _PREDICATE_NAME.match(node.name):
                found.append(f"{rel}:{node.lineno} def {node.name}")
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and _RULE_CONSTANT.match(t.id):
                        found.append(f"{rel}:{node.lineno} {t.id}")
    assert found == [], found


def test_every_module_that_names_the_predicate_gives_the_shared_answer():
    """The name each tool exports is the shared rule, checked by what it answers."""
    shared = _shared()
    seen = []
    for rel, tree in _modules():
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names |= {a.asname or a.name for a in node.names}
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
        for name in sorted(n for n in names if _PREDICATE_NAME.match(n) or n == "is_test_file"):
            module = importlib.import_module("jcodemunch_mcp." + rel[:-3].replace("/", "."))
            fn = getattr(module, name, None)
            if fn is None:
                continue  # imported inside a function
            seen.append(f"{rel}:{name}")
            wrong = [p for p in TEST_PATHS if not fn(p)] + [p for p in SOURCE_PATHS if fn(p)]
            assert wrong == [], (rel, name, wrong)
            assert fn is shared, (rel, name)
    assert len(seen) >= 8, seen


def _index(root: Path, files: dict[str, str]) -> tuple[str, str]:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    storage = str(root / ".index")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    return res.get("repo", str(root)), storage


ROOT_DIRS = {
    "python-tests": (
        {
            "shapes.py": "def area():\n    return 1\n",
            "main.py": "print(1)\n",
            "tests/helpers.py": "from shapes import area\n\n\ndef check():\n    return area()\n",
        },
        "shapes.py::area#function",
        "tests/helpers.py",
    ),
    "python-test": (
        {
            "shapes.py": "def area():\n    return 1\n",
            "main.py": "print(1)\n",
            "test/helpers.py": "from shapes import area\n\n\ndef check():\n    return area()\n",
        },
        "shapes.py::area#function",
        "test/helpers.py",
    ),
    "js-dunder-tests": (
        {
            "package.json": '{"name":"fx","version":"1.0.0","type":"module","main":"src/main.js"}',
            "src/main.js": "export function boot() { return 1; }\n",
            "src/shapes.js": "export function area() { return 1; }\n",
            "__tests__/shapes.js": "import { area } from '../src/shapes.js';\nexport function check() { return area(); }\n",
        },
        "src/shapes.js::area#function",
        "__tests__/shapes.js",
    ),
}


@pytest.mark.parametrize("case", sorted(ROOT_DIRS))
def test_a_root_level_test_directory_is_not_reported_dead(tmp_path, case):
    files, _symbol, test_file = ROOT_DIRS[case]
    repo, storage = _index(tmp_path, files)
    res = find_dead_code(repo, granularity="file", storage_path=storage)
    assert "error" not in res, res
    assert test_file not in [d["file"] for d in res.get("dead_files") or []], res


@pytest.mark.parametrize("case", sorted(ROOT_DIRS))
def test_a_name_a_root_level_test_imports_is_imported(tmp_path, case):
    files, symbol, test_file = ROOT_DIRS[case]
    repo, storage = _index(tmp_path, files)
    result = investigate_deletion_safety(repo, symbol, storage_path=storage)
    assert "error" not in result, result
    ob = next(o for o in result["obligations"] if o["obligation"] == "export_not_imported")
    assert ob["status"] == REFUTED, ob
    assert test_file in str(ob), ob


@pytest.mark.parametrize("case", sorted(ROOT_DIRS))
def test_a_root_level_test_counts_as_a_test_of_the_file_it_imports(tmp_path, case):
    """`get_file_risk` reads the rule inline, with no predicate name to scan for."""
    from jcodemunch_mcp.tools.get_file_risk import get_file_risk

    files, symbol, _test_file = ROOT_DIRS[case]
    repo, storage = _index(tmp_path, files)
    res = get_file_risk(repo, symbol.split("::", 1)[0], storage_path=storage)
    assert "error" not in res, res
    assert res["file_metrics"]["has_tests"] is True, res["file_metrics"]
