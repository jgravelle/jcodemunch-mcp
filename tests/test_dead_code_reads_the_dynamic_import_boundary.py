"""The dead-code tools read #876's dynamic-import boundary (LEDGER L-70).

#876 made a Python dynamic import an edge or a recorded site with a scope, and
only `get_blast_radius` read the site: an empty blast refuses when a
`package`/`prefix:` scope reaches the file (an `opaque` one is disclosed, jjg
2026-09-29). `find_dead_code`, `get_dead_code_v2` and `check_delete_safe` got
the edges and not the boundary, so a module `import_module(f"adapters.{name}")`
can load still read as zero importers there, at confidence 1.0, and
`check_delete_safe` certified it `safe_to_delete`.

The property: an absence claim about a file a scoped dynamic import can reach
is not published as proven, by any of the three; an opaque site is disclosed
and caps nothing; a file no site reaches keeps its verdict.
"""

from __future__ import annotations

from pathlib import Path

from jcodemunch_mcp.tools._corpus_adequacy import UNPROVEN_CEILING
from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe
from jcodemunch_mcp.tools.find_dead_code import find_dead_code
from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2
from jcodemunch_mcp.tools.index_folder import index_folder

SCOPED = {
    "run.py": "import importlib\n\ndef make(name):\n    return importlib.import_module(f'adapters.{name}')\n",
    "adapters/__init__.py": "",
    "adapters/alpha.py": "def build():\n    return 1\n",
    "lonely.py": "def alone():\n    return 0\n",
}
OPAQUE = {
    "plugins.py": "import os\n\ndef load():\n    return __import__(os.environ['PLUGIN'])\n",
    "lonely.py": "def alone():\n    return 0\n",
}


def _index(tmp_path: Path, files: dict[str, str]) -> tuple[str, str]:
    root = tmp_path / "r"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    storage = str(tmp_path / "store")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=storage, identity_mode="local")
    return res["repo"], storage


def _rows(result: dict) -> dict[str, dict]:
    return {row["file"]: row for row in result["dead_files"]}


def test_find_dead_code_does_not_prove_dead_a_file_a_scoped_site_can_load(tmp_path):
    repo, storage = _index(tmp_path, SCOPED)
    default = _rows(find_dead_code(repo, storage_path=storage))
    assert "adapters/alpha.py" not in default, "published as dead past a loader that can import it"
    assert default["lonely.py"]["confidence"] == 1.0, "a file no site reaches keeps its proof"

    every = _rows(find_dead_code(repo, min_confidence=0.0, storage_path=storage))
    row = every["adapters/alpha.py"]
    assert row["confidence"] <= UNPROVEN_CEILING
    assert row["uncapped_confidence"] == 1.0
    assert "dynamic_import_boundary" in row["confidence_capped_by"]
    assert row["dynamic_import_sites"] == ["run.py"]


def test_find_dead_code_discloses_an_opaque_site_and_caps_nothing(tmp_path):
    repo, storage = _index(tmp_path, OPAQUE)
    result = find_dead_code(repo, storage_path=storage)
    assert _rows(result)["lonely.py"]["confidence"] == 1.0
    assert result["dynamic_imports_unfollowed"]["files"] == ["plugins.py"]


def test_control_no_dynamic_import_no_disclosure(tmp_path):
    repo, storage = _index(tmp_path, {"lonely.py": "def alone():\n    return 0\n"})
    result = find_dead_code(repo, storage_path=storage)
    assert "dynamic_imports_unfollowed" not in result
    assert "dynamic_import_sites" not in _rows(result)["lonely.py"]


def test_dead_code_v2_does_not_call_a_reachable_file_unreachable(tmp_path):
    repo, storage = _index(tmp_path, SCOPED)
    rows = get_dead_code_v2(repo, min_confidence=0.0, storage_path=storage)["dead_symbols"]
    signals = {r["name"]: r["signals"] for r in rows}
    assert "unreachable_file" not in signals.get("build", []), signals
    assert "unreachable_file" in signals.get("alone", []), "control: an unreached file still fires signal 1"


def test_check_delete_safe_does_not_certify_a_delete_past_the_boundary(tmp_path):
    repo, storage = _index(tmp_path, SCOPED)
    reached = check_delete_safe(repo, "build", storage_path=storage)
    assert reached["verdict"] == "dynamic_import_boundary", reached["verdict"]
    assert reached["confidence"] <= UNPROVEN_CEILING
    assert any(b.get("kind") == "dynamic_import_boundary" and b.get("files") == ["run.py"] for b in reached["blockers"])
    unreached = check_delete_safe(repo, "alone", storage_path=storage)
    assert unreached["verdict"] != "dynamic_import_boundary"


def test_the_new_verdict_is_bounded_never_terminal():
    from jcodemunch_mcp.tools import _stop_rule

    assert "dynamic_import_boundary" in _stop_rule.known_verdicts("check_delete_safe")
    assert "dynamic_import_boundary" in _stop_rule._BOUNDED["check_delete_safe"]
