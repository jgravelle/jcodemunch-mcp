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


def test_the_withheld_count_names_only_what_the_boundary_withheld(tmp_path, monkeypatch):
    from jcodemunch_mcp.tools import find_dead_code as fdc
    from jcodemunch_mcp.tools._corpus_adequacy import CorpusAdequacy

    repo, storage = _index(tmp_path, SCOPED)
    assert find_dead_code(repo, storage_path=storage)["dynamic_import_boundary_withheld"] == 1
    # A thin corpus already caps every file under the default threshold, so the
    # boundary withholds nothing that would otherwise have been published.
    monkeypatch.setattr(fdc, "assess_corpus", lambda *a, **k: CorpusAdequacy("stale", {}, None, ["stale_index"]))
    thin = find_dead_code(repo, storage_path=storage)
    assert "dynamic_import_boundary_withheld" not in thin, thin.get("dynamic_import_boundary_withheld")


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


def test_dead_code_v2_leaves_signal_one_undecided_and_keeps_the_others(tmp_path):
    repo, storage = _index(tmp_path, SCOPED)
    result = get_dead_code_v2(repo, min_confidence=0.0, storage_path=storage)
    rows = {r["name"]: r for r in result["dead_symbols"]}
    # ⚠ Index the row, never `.get(name, [])`: a symbol dropped whole passes a
    # "signal absent" check, and dropping it whole was round 1's defect.
    build = rows["build"]
    assert "unreachable_file" not in build["signals"]
    assert "no_callers" in build["signals"], "the boundary silenced a signal it says nothing about"
    assert build["undecided_signals"] == ["unreachable_file"]
    assert "unreachable_file" in rows["alone"]["signals"], "control: an unreached file still fires signal 1"
    assert "undecided_signals" not in rows["alone"]
    assert result["dynamic_import_boundary"]["sites"] == ["run.py"]
    assert result["_meta"]["signal_diagnostics"]["undecided"] == {"unreachable_file": 1}


# A loader at the repo root reaches every file. Round 1 made each one an entry
# point, v2 analysed nothing, and get_repo_health published a grade main withheld.
ROOT_LOADER = {
    "loader.py": "import importlib\n\ndef load(name):\n    return importlib.import_module('.' + name, __package__)\n",
    **{f"m{i}.py": f"def f{i}():\n    return {i}\n\ndef g{i}():\n    return f{i}()\n" for i in range(15)},
}


def test_a_root_level_loader_does_not_turn_a_withheld_grade_into_a_published_one(tmp_path):
    from jcodemunch_mcp.tools.get_repo_health import get_repo_health

    repo, storage = _index(tmp_path, ROOT_LOADER)
    v2 = get_dead_code_v2(repo, storage_path=storage)
    diag = v2["_meta"]["signal_diagnostics"]
    assert diag["analysed"] > 0, "the boundary removed symbols from analysis"
    assert diag["fire_rate"]["unreachable_file"] is None
    assert "unreachable_file" not in diag["informative"]
    assert v2.get("signal_warning")
    health = get_repo_health(repo, storage_path=storage)
    assert health["dead_code_measurable"] is False
    assert "dead_code" in health["radar"]["unmeasurable_axes"]


# No entry point, so signal 1 fires on every decidable symbol, which is a
# constant. Counting the undecided plugins as "did not fire" drags the rate into
# the informative band and hands signal 1 a vote on every lib/ symbol.
PLUGINS_NO_ENTRY = {
    "plugins/loader.py": "import importlib\n\ndef load(name):\n    return importlib.import_module(f'{__package__}.{name}')\n",
    **{f"plugins/p{i}.py": f"def run{i}():\n    return {i}\n" for i in range(12)},
    **{f"lib/l{i}.py": f"def lib{i}():\n    return {i}\n" for i in range(12)},
}


def test_signal_one_is_measured_only_where_it_could_be_decided(tmp_path):
    repo, storage = _index(tmp_path, PLUGINS_NO_ENTRY)
    diag = get_dead_code_v2(repo, min_confidence=0.0, storage_path=storage)["_meta"]["signal_diagnostics"]
    assert diag["undecided"]["unreachable_file"] >= 12
    assert diag["fire_rate"]["unreachable_file"] == 1.0
    assert "unreachable_file" not in diag["informative"]


def test_check_delete_safe_does_not_certify_a_delete_past_the_boundary(tmp_path):
    repo, storage = _index(tmp_path, SCOPED)
    reached = check_delete_safe(repo, "build", storage_path=storage)
    assert reached["verdict"] == "dynamic_import_boundary", reached["verdict"]
    assert reached["confidence"] <= UNPROVEN_CEILING
    assert any(b.get("kind") == "dynamic_import_boundary" and b.get("files") == ["run.py"] for b in reached["blockers"])
    assert reached["stop_rule"]["terminal"] is False
    assert any("loader" in g["action"] for g in reached["stop_rule"]["would_change_verdict"])
    unreached = check_delete_safe(repo, "alone", storage_path=storage)
    assert unreached["verdict"] != "dynamic_import_boundary"


def test_the_verdict_is_not_terminal_with_every_other_channel_closed(tmp_path, monkeypatch):
    from jcodemunch_mcp.tools import check_delete_safe as cds

    monkeypatch.setattr(cds, "_runtime_data_present", lambda *a, **k: True)
    repo, storage = _index(tmp_path, SCOPED)
    r = check_delete_safe(repo, "build", cross_repo=True, include_runtime=True, storage_path=storage)
    assert r["verdict"] == "dynamic_import_boundary"
    assert r["stop_rule"]["terminal"] is False, r["stop_rule"]


def test_the_investigator_does_not_call_a_reachable_file_static_clear(tmp_path):
    from jcodemunch_mcp.investigator import investigate_deletion_safety

    repo, storage = _index(tmp_path, SCOPED)
    reached = investigate_deletion_safety(repo, "build", storage_path=storage)
    assert reached["verdict"] == "not_established", reached["verdict"]
    assert "no_dynamic_loader" in reached["unresolved_obligations"]
    unreached = investigate_deletion_safety(repo, "alone", storage_path=storage)
    assert "no_dynamic_loader" not in [o["obligation"] for o in unreached["obligations"]]


def test_the_new_verdict_is_bounded_never_terminal():
    from jcodemunch_mcp.tools import _stop_rule

    assert "dynamic_import_boundary" in _stop_rule.known_verdicts("check_delete_safe")
    assert "dynamic_import_boundary" in _stop_rule._BOUNDED["check_delete_safe"]
