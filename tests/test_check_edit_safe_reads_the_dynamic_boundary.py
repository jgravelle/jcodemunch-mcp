"""`check_edit_safe` does not certify an edit past a dynamic import (LEDGER L-75).

#876 records a Python dynamic import it cannot resolve as a site with a scope,
and every absence tool reads it through `tools/_dynamic_boundary.py` (L-70,
L-73). `check_edit_safe` reads `find_importers`' list for signature impact and
did not read the boundary, so a function in a module
`import_module(f"adapters.{name}")` can load graded `safe_to_edit` ("no
external callers") though the loader calls it at runtime.

The property: a file a package- or prefix-scoped site can reach never grades
`safe_to_edit`; it grades the bounded `dynamic_import_boundary`, names the
loader, and is never terminal. Only that absence verdict is replaced: a verdict
backed by positive evidence keeps its name and gains the loader as a blocker.
An opaque site blocks nothing (#876, jjg 2026-09-29), and a file no site
reaches is graded exactly as before.
"""

from __future__ import annotations

from pathlib import Path

from jcodemunch_mcp.tools._corpus_adequacy import UNPROVEN_CEILING
from jcodemunch_mcp.tools.check_edit_safe import check_edit_safe
from jcodemunch_mcp.tools.index_folder import index_folder

SCOPED = {
    "run.py": "import importlib\n\ndef make(name):\n    return importlib.import_module(f'adapters.{name}')\n",
    "adapters/__init__.py": "",
    "adapters/alpha.py": "def build():\n    return 1\n",
    "adapters/beta.py": (
        "def branchy(x):\n"
        + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(12))
        + "    return -1\n"
    ),
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


def _loader_blocker(result: dict) -> dict | None:
    return next((b for b in result["blockers"] if b.get("kind") == "dynamic_import_boundary"), None)


def test_a_loadable_function_is_not_safe_to_edit(tmp_path):
    repo, storage = _index(tmp_path, SCOPED)
    r = check_edit_safe(repo, "build", storage_path=storage)
    assert r["verdict"] == "dynamic_import_boundary", r["verdict"]
    assert r["confidence"] <= UNPROVEN_CEILING, "an unproven absence published at safe_to_edit's confidence"
    assert _loader_blocker(r)["files"] == ["run.py"]
    assert r["stop_rule"]["terminal"] is False
    assert any("loader" in g["action"] for g in r["stop_rule"]["would_change_verdict"])
    assert "run.py" in r["recommended_action"]


def test_the_verdict_is_not_terminal_with_every_other_channel_closed(tmp_path, monkeypatch):
    from jcodemunch_mcp.tools import check_edit_safe as ces

    monkeypatch.setattr(ces, "_runtime_data_present", lambda *a, **k: True)
    repo, storage = _index(tmp_path, SCOPED)
    r = check_edit_safe(repo, "build", cross_repo=True, include_runtime=True, storage_path=storage)
    assert r["verdict"] == "dynamic_import_boundary"
    assert r["stop_rule"]["terminal"] is False, r["stop_rule"]


def test_untested_keeps_its_name_but_is_never_terminal_past_a_loader(tmp_path, monkeypatch):
    """`untested` rests on the same "no external caller" claim, because
    signature_impact outranks it. Reading the loader can move it (review, L-75),
    so it keeps its name and is not terminal, even with every other channel
    closed. The loader calling `.build()` is what makes it referenced."""
    from jcodemunch_mcp.tools import check_edit_safe as ces

    monkeypatch.setattr(ces, "_runtime_data_present", lambda *a, **k: True)
    files = dict(SCOPED)
    files["run.py"] = "import importlib\n\ndef make(name):\n    return importlib.import_module(f'adapters.{name}').build()\n"
    repo, storage = _index(tmp_path, files)
    r = check_edit_safe(repo, "build", cross_repo=True, include_runtime=True, storage_path=storage)
    assert r["verdict"] == "untested", r["verdict"]
    assert _loader_blocker(r)["files"] == ["run.py"]
    assert r["stop_rule"]["terminal"] is False, r["stop_rule"]


def test_a_capped_loader_list_says_how_many_it_left_out(tmp_path):
    files = {k: v for k, v in SCOPED.items() if k != "run.py"}
    for i in range(13):
        files[f"load{i:02d}.py"] = "import importlib\n\ndef make(name):\n    return importlib.import_module(f'adapters.{name}')\n"
    repo, storage = _index(tmp_path, files)
    r = check_edit_safe(repo, "build", storage_path=storage)
    assert r["verdict"] == "dynamic_import_boundary"
    assert r["signals"]["dynamic_loader_count"] == 13
    assert len(_loader_blocker(r)["files"]) < 13
    assert "and 3 more" in r["recommended_action"], r["recommended_action"]


def test_the_unproven_verdict_never_reads_safer_than_untested(tmp_path):
    repo, storage = _index(tmp_path, SCOPED)
    r = check_edit_safe(repo, "build", storage_path=storage)
    assert r["verdict"] == "dynamic_import_boundary"
    assert r["confidence"] <= 0.55, "higher = safer: an unseen caller outranked a seen use with no test"
    assert check_edit_safe(repo, "alone", storage_path=storage)["signals"]["dynamic_loader_count"] == 0


def test_a_verdict_backed_by_evidence_keeps_its_name_and_gains_the_loader(tmp_path):
    repo, storage = _index(tmp_path, SCOPED)
    r = check_edit_safe(repo, "branchy", storage_path=storage)
    assert r["verdict"] == "complexity_risk", r["verdict"]
    assert _loader_blocker(r)["files"] == ["run.py"]


def test_control_a_file_no_site_reaches_is_graded_as_before(tmp_path):
    repo, storage = _index(tmp_path, SCOPED)
    r = check_edit_safe(repo, "alone", storage_path=storage)
    assert r["verdict"] == "safe_to_edit"
    assert _loader_blocker(r) is None


def test_an_opaque_site_blocks_nothing(tmp_path):
    repo, storage = _index(tmp_path, OPAQUE)
    r = check_edit_safe(repo, "alone", storage_path=storage)
    assert r["verdict"] == "safe_to_edit"
    assert _loader_blocker(r) is None


def test_the_new_verdict_is_bounded_never_terminal():
    from jcodemunch_mcp.tools import _stop_rule

    assert "dynamic_import_boundary" in _stop_rule._BOUNDED["check_edit_safe"]
