"""One rule says whether a path is a test file (LEDGER L-101).

Six rules answered the question under `src/` and no two agreed.
`find_dead_code` and `get_dead_code_v2` tested `"/tests/" in path`, which
needs a leading slash, so a test directory at the repository root was not
one: `tests/helpers.py` was reported dead at confidence 1.0, and the deletion
investigator, which asks `find_dead_code` since L-94, then called a name that
file imports "imported by nothing live". `check_delete_safe` and
`find_similar_symbols` saw the root directory and missed `a_test.py` and
`__tests__/`.

What is checked here:

- the cases, pinned against the shared rule, in both directions;
- no module binds the predicate's names, or a TEST regex constant, at any
  depth by one of these forms: def, class, assignment, walrus, loop or
  `with` target, argument, import alias, `match` capture, `except ... as`.
  Each module that imports the name holds the shared function. A write
  through `globals()` or onto another module's attribute is not a form the
  scan reads. Nor is a function-local import of the same name from
  another module: the identity check reads module attributes. ⚠ A copy under ANOTHER name, or a rule written inline, is
  not seen by that scan. Only the tools this file runs are covered by what
  they answer; `get_pr_risk_profile`, `find_similar_symbols`, the reuse
  audit, `get_blast_radius`, `get_untested_symbols`, `find_unused_paths` and
  `get_parity_map` are not among them;
- the root-level shapes through `find_dead_code`, `get_dead_code_v2`, the
  deletion investigator and `get_file_risk`;
- `check_delete_safe` and `check_edit_safe` on a PRODUCTION file whose name
  looks like a test (`models/pod_spec.py`). They read this rule to call a use
  a test use, which downgrades a blocking verdict, so a false positive in the
  rule is a delete certified over a real consumer. The spellings kept
  although a production file can carry them (`ab_test.py`, `tests.py`,
  `ab_tests/`) are pinned to the verdict each preflight gives.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

from jcodemunch_mcp.investigator import REFUTED, investigate_deletion_safety
from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe
from jcodemunch_mcp.tools.check_edit_safe import check_edit_safe
from jcodemunch_mcp.tools.find_dead_code import find_dead_code
from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2
from jcodemunch_mcp.tools.get_file_risk import get_file_risk
from jcodemunch_mcp.tools.index_folder import index_folder

SRC = Path(__file__).resolve().parent.parent / "src" / "jcodemunch_mcp"
AUTHORITY = "tools/_test_paths.py"

TEST_PATHS = [
    "tests/helpers.py",
    "test/helpers.py",
    "__tests__/shapes.js",
    "__test__/shapes.js",
    "Tests/Unit/Foo.php",
    "pkg/tests/helpers.py",
    "pkg/__tests__/a.js",
    "pkg\\tests\\helpers.py",
    "src/unit_tests/a.py",
    "src/integration_tests/a.py",
    "src/test_utils/x.py",
    "src/test/java/Foo.java",
    "test_a.py",
    "pkg/test_a.py",
    "pkg/a_test.py",
    "pkg/a_test.go",
    "src/protest_test.py",
    "conftest.py",
    "pkg/conftest.py",
    "src/a.spec.ts",
    "src/a.spec.jsx",
    "src/a.test.js",
    "src/a.test.mjs",
    "src/a_spec.rb",
    "app/tests.py",
    "experiments/ab_tests/variants.py",
    "experiments/ab_test.py",
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
    "models/pod_spec.py",
    "client/v1_pod_spec.go",
    "docs/api_spec.yaml",
    "docs/openapi_spec.json",
    "src/api.spec.yaml",
    "src/load.test.sh",
    "src/ab_test/variants.py",
    "src/tests.js",
]

_PREDICATE_NAME = re.compile(r"^_?is_test_(file|path)$")
_RULE_CONSTANT = re.compile(r"^_?\w*TEST\w*_RE$|^_?TEST_(FILE|FILENAME|PATH|DIR)\w*$")


# Every place the name is bound today. A module that starts or stops importing it changes this list.
BINDINGS = [
    "investigator/reuse_audit.py:_is_test_path",
    "tools/_test_paths.py:is_test_file",
    "tools/check_delete_safe.py:_is_test_file",
    "tools/check_edit_safe.py:_is_test_file",
    "tools/find_dead_code.py:_is_test_file",
    "tools/find_similar_symbols.py:_is_test_file",
    "tools/find_unused_paths.py:_is_test_file",
    "tools/get_blast_radius.py:_is_test_file",
    "tools/get_dead_code_v2.py:_is_test_file",
    "tools/get_file_risk.py:is_test_file",
    "tools/get_parity_map.py:_is_test_file",
    "tools/get_pr_risk_profile.py:_is_test_file",
    "tools/get_untested_symbols.py:_is_test_file",
]


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


def _refused(name: str) -> bool:
    """A name the copies used: the predicate's, or a TEST regex constant's."""
    return bool(_PREDICATE_NAME.match(name) or _RULE_CONSTANT.match(name))


def test_no_module_writes_its_own_rule():
    """Under the names the copies used. A rule under another name is not seen here."""
    found = []
    for rel, tree in _modules():
        if rel == AUTHORITY:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and _refused(node.name):
                found.append(f"{rel}:{node.lineno} def {node.name}")
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in (n for target in targets for n in ast.walk(target)):
                    if isinstance(t, ast.Name) and _refused(t.id):
                        found.append(f"{rel}:{node.lineno} {t.id}")
            # Every other statement that binds a name: walrus, loop and `with` targets, `del`.
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load) and _refused(node.id):
                found.append(f"{rel}:{node.lineno} name {node.id}")
            if isinstance(node, ast.arg) and _refused(node.arg):
                found.append(f"{rel}:{node.lineno} arg {node.arg}")
            # Bindings the AST holds as a plain string: a `match` capture and `except ... as`.
            for attr in ("name", "rest"):
                captured = getattr(node, attr, None)
                if (
                    isinstance(node, (ast.MatchAs, ast.MatchStar, ast.MatchMapping, ast.ExceptHandler))
                    and isinstance(captured, str)
                    and _refused(captured)
                ):
                    found.append(f"{rel}:{node.lineno} {type(node).__name__} {captured}")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    bound = a.asname or a.name
                    origin = a.name.rsplit(".", 1)[-1]
                    if _refused(bound) and not (_refused(origin) or origin == "is_test_file"):
                        found.append(f"{rel}:{node.lineno} import {a.name} as {bound}")
    assert sorted(set(found)) == [], sorted(set(found))
    assert found == [], found


def test_every_module_that_names_the_predicate_gives_the_shared_answer():
    """Each module that imports the name, at any depth, holds the shared function."""
    shared = _shared()
    seen = []
    for rel, tree in _modules():
        bound = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                bound |= {a.asname or a.name for a in node.names}
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bound.add(node.name)
            elif isinstance(node, ast.Assign):
                bound |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        names = sorted(n for n in bound if _PREDICATE_NAME.match(n) or n == "is_test_file")
        if not names:
            continue
        module = importlib.import_module("jcodemunch_mcp." + rel[:-3].replace("/", "."))
        for name in names:
            fn = getattr(module, name)
            seen.append(f"{rel}:{name}")
            wrong = [p for p in TEST_PATHS if not fn(p)] + [p for p in SOURCE_PATHS if fn(p)]
            assert wrong == [], (rel, name, wrong)
            assert fn is shared, (rel, name)
    assert len(seen) >= 8, seen
    assert sorted(seen) == sorted(BINDINGS), sorted(set(seen) ^ set(BINDINGS))


def _index(root: Path, files: dict[str, str]) -> tuple[str, str]:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    storage = str(root / ".index")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    return res.get("repo", str(root)), storage


_PY_TEST = "from shapes import area\n\n\ndef check():\n    return area()\n"
ROOT_DIRS = {
    "python-tests": (
        {"shapes.py": "def area():\n    return 1\n", "main.py": "print(1)\n", "tests/helpers.py": _PY_TEST},
        "shapes.py::area#function",
        "tests/helpers.py",
    ),
    "python-test": (
        {"shapes.py": "def area():\n    return 1\n", "main.py": "print(1)\n", "test/helpers.py": _PY_TEST},
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
def test_the_second_dead_code_tool_skips_a_root_level_test_and_reports_it_when_asked(tmp_path, case):
    files, _symbol, test_file = ROOT_DIRS[case]
    repo, storage = _index(tmp_path, files)

    def files_reported(include_tests: bool) -> set[str]:
        res = get_dead_code_v2(repo, min_confidence=0.0, include_tests=include_tests, storage_path=storage)
        assert "error" not in res, res
        return {d["file"] for d in res.get("dead_symbols") or []}

    assert test_file not in files_reported(False)
    assert test_file in files_reported(True), "the fixture must be able to report the file"


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
    files, symbol, _test_file = ROOT_DIRS[case]
    repo, storage = _index(tmp_path, files)
    res = get_file_risk(repo, symbol.split("::", 1)[0], storage_path=storage)
    assert "error" not in res, res
    assert res["file_metrics"]["has_tests"] is True, res["file_metrics"]


def test_a_production_importer_is_not_a_test_of_the_file_it_imports(tmp_path):
    files = {
        "shapes.py": "def area():\n    return 1\n",
        "models/pod_spec.py": "from shapes import area\n\n\ndef run():\n    return area()\n",
        "main.py": "from models.pod_spec import run\n\nprint(run())\n",
    }
    repo, storage = _index(tmp_path, files)
    res = get_file_risk(repo, "shapes.py", storage_path=storage)
    assert res["file_metrics"]["has_tests"] is False, res["file_metrics"]


def _consumer_repo(root: Path, importer: str) -> tuple[str, str]:
    module = importer[:-3].replace("/", ".")
    return _index(
        root,
        {
            "core.py": "def helper():\n    return 1\n",
            importer: "from core import helper\n\n\ndef run():\n    return helper()\n",
            "main.py": f"from {module} import run\n\nprint(run())\n",
        },
    )


LOOKS_LIKE_A_TEST = ["models/pod_spec.py", "models/api_spec.py"]


@pytest.mark.parametrize("tool", [check_delete_safe, check_edit_safe], ids=["delete", "edit"])
@pytest.mark.parametrize("importer", LOOKS_LIKE_A_TEST)
def test_a_production_consumer_named_like_a_spec_blocks_as_any_other(tmp_path, tool, importer):
    """The verdict for a `*_spec.py` consumer is the verdict for `models/pod.py`."""
    control_repo, control_storage = _consumer_repo(tmp_path / "control", "models/pod.py")
    control = tool(control_repo, "core.py::helper#function", cross_repo=False, storage_path=control_storage)
    repo, storage = _consumer_repo(tmp_path / "case", importer)
    res = tool(repo, "core.py::helper#function", cross_repo=False, storage_path=storage)
    assert "error" not in control and "error" not in res, (control, res)
    assert res["verdict"] == control["verdict"], (res["verdict"], control["verdict"])
    assert res["verdict"] not in ("test_coverage_only", "safe_to_delete", "safe_to_edit"), res["verdict"]
    assert res["signals"].get("test_import_count", 0) == 0, res["signals"]


def test_a_test_consumer_is_counted_as_a_test_by_the_delete_preflight(tmp_path):
    """The other direction, so the case above cannot pass on a rule that sees no tests."""
    repo, storage = _index(
        tmp_path,
        {
            "core.py": "def helper():\n    return 1\n",
            "main.py": "print(1)\n",
            "__tests__/check_core.py": "from core import helper\n\n\ndef check():\n    return helper()\n",
        },
    )
    res = check_delete_safe(repo, "core.py::helper#function", cross_repo=False, storage_path=storage)
    assert "error" not in res, res
    assert res["verdict"] == "test_coverage_only", res


AMBIGUOUS = ["experiments/ab_test.py", "certs/tests.py", "experiments/ab_tests/variants.py"]


@pytest.mark.parametrize("importer", AMBIGUOUS)
def test_a_consumer_under_an_ambiguous_test_name_is_never_a_safe_delete(tmp_path, importer):
    """The disclosed trade: these read as tests, and the delete preflight stays short of safe.

    A production file can carry each name. The verdict is `test_coverage_only`,
    which is not terminal and names the file as a blocker.
    """
    repo, storage = _consumer_repo(tmp_path, importer)
    res = check_delete_safe(repo, "core.py::helper#function", cross_repo=False, storage_path=storage)
    assert "error" not in res, res
    assert res["verdict"] == "test_coverage_only", res["verdict"]
    assert res["stop_rule"]["terminal"] is False, res["stop_rule"]
    assert importer in str(res["blockers"]), res["blockers"]


@pytest.mark.parametrize("importer", AMBIGUOUS + ["__tests__/check_core.py", "tests/check_core.py"])
def test_the_edit_preflight_counts_a_test_consumer_as_a_test(tmp_path, importer):
    """The edit side in the positive direction, the ambiguous spellings included.

    ⚠ For `experiments/ab_test.py` this pins the disclosed trade: the edit
    preflight says `safe_to_edit` over a file that may be production code
    (LEDGER L-104). Narrowing the rule changes this test on purpose.
    """
    repo, storage = _consumer_repo(tmp_path, importer)
    res = check_edit_safe(repo, "core.py::helper#function", cross_repo=False, storage_path=storage)
    assert "error" not in res, res
    assert res["signals"]["test_import_count"] == 1, res["signals"]
    assert res["signals"]["has_test_coverage"] is True, res["signals"]
    assert res["verdict"] == "safe_to_edit", res["verdict"]
