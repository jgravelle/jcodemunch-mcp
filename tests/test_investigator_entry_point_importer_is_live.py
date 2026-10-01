"""An importer nothing imports is not thereby dead (LEDGER L-94).

The deletion investigator split a name's importers into live and dead by one
question: does anything import the importer? An entry point has no importer by
construction, so a name imported ONLY by `main.py`, by the file `package.json`
names as `main`, by a package's `__init__.py` or by a test read
`export_not_imported` SATISFIED with the evidence "Imported only by main.py,
which is itself unreachable".

`find_dead_code` already answers "is this file dead?" with every root this
project knows (entry-point filenames, `__init__`, `package.json` entries, the
framework profile, render edges, dynamic imports). The investigator asks it
and keeps no second answer. The other direction is tested too: an importer
that really is dead still lets the obligation through, with its cluster
named, and one the tool is unsure of or cannot answer for stays live.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.investigator import REFUTED, SATISFIED, investigate_deletion_safety
from jcodemunch_mcp.investigator.deletion_safety import UNSAFE
from jcodemunch_mcp.tools.index_folder import index_folder

PKG = '{"name":"fx","version":"1.0.0","type":"module","main":"src/main.js"}'


def _index(root: Path, files: dict[str, str]) -> tuple[str, str]:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    storage = str(root / ".index")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    return res.get("repo", str(root)), storage


def _ob(result: dict, name: str) -> dict:
    assert "error" not in result, result
    for o in result["obligations"]:
        if o["obligation"] == name:
            return o
    raise AssertionError(f"obligation {name!r} missing from {result['obligations']}")


CASES = {
    "python-main": (
        {
            "shapes.py": "def area():\n    return 1\n",
            "main.py": "from shapes import area\n\nprint(area())\n",
        },
        "shapes.py::area#function",
        "main.py",
    ),
    "package-json-main": (
        {
            "package.json": PKG,
            "src/shapes.js": "export function area() { return 1; }\n",
            "src/main.js": "import { area } from './shapes.js';\nexport function boot() { return area(); }\n",
        },
        "src/shapes.js::area#function",
        "src/main.js",
    ),
    "package-init": (
        {
            "pkg/shapes.py": "def area():\n    return 1\n",
            "pkg/__init__.py": "from .shapes import area\n\n__all__ = ['area']\n",
        },
        "pkg/shapes.py::area#function",
        "pkg/__init__.py",
    ),
    "test-file": (
        {
            "shapes.py": "def area():\n    return 1\n",
            "tests/test_shapes.py": "from shapes import area\n\n\ndef test_area():\n    assert area() == 1\n",
        },
        "shapes.py::area#function",
        "tests/test_shapes.py",
    ),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_name_imported_only_by_a_root_is_imported(tmp_path, case):
    files, symbol, importer = CASES[case]
    repo, storage = _index(tmp_path, files)
    result = investigate_deletion_safety(repo, symbol, storage_path=storage)
    ob = _ob(result, "export_not_imported")
    assert ob["status"] == REFUTED, ob
    assert any(importer in e for e in ob["evidence"]), ob
    # The text sweep asks the same liveness question of the same file.
    text = _ob(result, "no_textual_use")
    assert text["status"] == REFUTED and importer in text["detail"]["files"], text
    assert result["verdict"] == UNSAFE


def test_a_name_imported_only_by_a_dead_file_is_still_removable_with_it(tmp_path):
    """Gap 3, kept: `orphan.py` is no root and nothing imports it."""
    files = {
        "shapes.py": "def area():\n    return 1\n\n\ndef side():\n    return 2\n",
        "orphan.py": "from shapes import area\n\n\ndef unused():\n    return area()\n",
        "main.py": "from shapes import side\n\nprint(side())\n",
    }
    repo, storage = _index(tmp_path, files)
    result = investigate_deletion_safety(
        repo, "shapes.py::area#function", storage_path=storage
    )
    ob = _ob(result, "export_not_imported")
    assert ob["status"] == SATISFIED, ob
    assert ob["detail"]["deletion_cluster"] == ["orphan.py"], ob
    text = _ob(result, "no_textual_use")
    assert text["status"] == SATISFIED and text["detail"]["in_unreachable_files"] == [
        "orphan.py"
    ], text
    assert result["verdict"] != UNSAFE


def test_an_importer_the_dead_code_tool_is_unsure_of_counts_as_live(tmp_path):
    """`loader.py` is imported, but only by a dead file: `find_dead_code` reports it below
    its default confidence. Unsure is not dead, and an unclassified importer blocks."""
    files = {
        "shapes.py": "def area():\n    return 1\n\n\ndef side():\n    return 2\n",
        "loader.py": "from shapes import area\n\n\ndef load():\n    return area()\n",
        "orphan.py": "from loader import load\n\n\ndef unused():\n    return load()\n",
        "main.py": "from shapes import side\n\nprint(side())\n",
    }
    repo, storage = _index(tmp_path, files)
    result = investigate_deletion_safety(
        repo, "shapes.py::area#function", storage_path=storage
    )
    ob = _ob(result, "export_not_imported")
    assert ob["status"] == REFUTED, ob
    assert ob["detail"]["live_importers"] == ["loader.py"], ob


@pytest.mark.parametrize("how", ["error", "raise"])
def test_a_dead_code_tool_that_cannot_answer_leaves_every_importer_live(
    tmp_path, monkeypatch, how
):
    """UNKNOWN blocks: `orphan.py` really is dead here, and with no answer it must not be called so."""
    import jcodemunch_mcp.tools.find_dead_code as fdc

    files = {
        "shapes.py": "def area():\n    return 1\n",
        "orphan.py": "from shapes import area\n\n\ndef unused():\n    return area()\n",
    }
    repo, storage = _index(tmp_path, files)

    def broken(*args, **kwargs):
        if how == "raise":
            raise RuntimeError("index unreadable")
        return {"error": "index unreadable"}

    monkeypatch.setattr(fdc, "find_dead_code", broken)
    ob = _ob(
        investigate_deletion_safety(
            repo, "shapes.py::area#function", storage_path=storage
        ),
        "export_not_imported",
    )
    assert ob["status"] == REFUTED, ob
    assert ob["detail"]["live_importers"] == ["orphan.py"], ob
