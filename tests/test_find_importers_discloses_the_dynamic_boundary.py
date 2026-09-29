"""`find_importers` names the dynamic import that can load a file (LEDGER L-73).

#876 records a Python dynamic import it cannot resolve as a site with a scope,
and L-70 made every absence tool read it through `tools/_dynamic_boundary.py`.
`find_importers` was left out: for a module `import_module(f"adapters.{name}")`
can load it answered `importer_count: 0` with nothing beside it, which reads
as "nothing imports this" one call away from `get_blast_radius` refusing to
say so.

The property: a package- or prefix-scoped site that can reach the file is
named in a `dynamic_import_boundary` block, whatever the static count, in
singular and batch mode and through the compact encoding. An opaque site is
disclosed as `dynamic_imports_unfollowed` beside an EMPTY answer only, the rule
`get_blast_radius` applies (#876, jjg 2026-09-29). A file no site reaches is
answered exactly as before.
"""

from __future__ import annotations

from pathlib import Path

from jcodemunch_mcp.encoding.schemas import find_importers as fi_schema
from jcodemunch_mcp.tools.find_importers import find_importers
from jcodemunch_mcp.tools.index_folder import index_folder

SCOPED = {
    "run.py": "import importlib\n\ndef make(name):\n    return importlib.import_module(f'adapters.{name}')\n",
    "adapters/__init__.py": "",
    "adapters/alpha.py": "def build():\n    return 1\n",
    "adapters/beta.py": "def other():\n    return 2\n",
    "user.py": "from adapters.beta import other\n\ndef use():\n    return other()\n",
    "lonely.py": "def alone():\n    return 0\n",
}
OPAQUE = {
    "plugins.py": "import os\n\ndef load():\n    return __import__(os.environ['PLUGIN'])\n",
    "lonely.py": "def alone():\n    return 0\n",
    "core.py": "def used():\n    return 1\n",
    "main.py": "from core import used\n\ndef main():\n    return used()\n",
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


def test_an_empty_answer_names_the_loader_that_can_reach_the_file(tmp_path):
    repo, storage = _index(tmp_path, SCOPED)
    r = find_importers(repo, file_path="adapters/alpha.py", storage_path=storage)
    assert r["importer_count"] == 0
    block = r["dynamic_import_boundary"]
    assert block["files"] == ["run.py"]
    assert block["files_total"] == 1
    assert "not" in block["note"].lower()


def test_a_static_importer_does_not_hide_the_loader(tmp_path):
    repo, storage = _index(tmp_path, SCOPED)
    r = find_importers(repo, file_path="adapters/beta.py", storage_path=storage)
    assert [i["file"] for i in r["importers"]] == ["user.py"]
    assert r["dynamic_import_boundary"]["files"] == ["run.py"]


def test_control_a_file_no_site_reaches_is_answered_as_before(tmp_path):
    repo, storage = _index(tmp_path, SCOPED)
    r = find_importers(repo, file_path="lonely.py", storage_path=storage)
    assert r["importer_count"] == 0
    assert "dynamic_import_boundary" not in r
    assert "dynamic_imports_unfollowed" not in r


def test_an_opaque_site_is_disclosed_beside_an_empty_answer_only(tmp_path):
    repo, storage = _index(tmp_path, OPAQUE)
    empty = find_importers(repo, file_path="lonely.py", storage_path=storage)
    assert "dynamic_import_boundary" not in empty, "an opaque site is disclosed, never a boundary"
    assert empty["dynamic_imports_unfollowed"]["files"] == ["plugins.py"]
    found = find_importers(repo, file_path="core.py", storage_path=storage)
    assert found["importer_count"] == 1
    assert "dynamic_imports_unfollowed" not in found


def test_a_scoped_block_supersedes_the_opaque_disclosure_as_in_the_blast_radius(tmp_path):
    """`get_blast_radius` discloses an opaque site only when it did not already
    refuse (review, L-73): the scoped block says the empty answer is not
    evidence, and a second disclosure beside it says nothing new."""
    files = dict(SCOPED)
    files["plug.py"] = OPAQUE["plugins.py"]
    repo, storage = _index(tmp_path, files)
    reached = find_importers(repo, file_path="adapters/alpha.py", storage_path=storage)
    assert reached["dynamic_import_boundary"]["files"] == ["run.py"]
    assert "dynamic_imports_unfollowed" not in reached
    assert find_importers(repo, file_path="lonely.py", storage_path=storage)["dynamic_imports_unfollowed"]["files"] == ["plug.py"]
    batch = find_importers(repo, file_paths=["adapters/alpha.py"], storage_path=storage)
    assert "dynamic_imports_unfollowed" not in batch
    mixed = find_importers(repo, file_paths=["adapters/alpha.py", "lonely.py"], storage_path=storage)
    assert mixed["dynamic_imports_unfollowed"]["files"] == ["plug.py"]


def test_batch_mode_carries_the_same_answer_per_file(tmp_path):
    repo, storage = _index(tmp_path, SCOPED)
    r = find_importers(repo, file_paths=["adapters/alpha.py", "lonely.py"], storage_path=storage)
    by_file = {e["file_path"]: e for e in r["results"]}
    assert by_file["adapters/alpha.py"]["dynamic_import_boundary"]["files"] == ["run.py"]
    assert "dynamic_import_boundary" not in by_file["lonely.py"]


def test_batch_mode_discloses_an_opaque_site_once(tmp_path):
    repo, storage = _index(tmp_path, OPAQUE)
    r = find_importers(repo, file_paths=["lonely.py", "core.py"], storage_path=storage)
    assert r["dynamic_imports_unfollowed"]["files"] == ["plugins.py"]
    assert all("dynamic_imports_unfollowed" not in e for e in r["results"])


def test_the_investigator_does_not_call_a_loadable_importer_dead(tmp_path):
    """The consumer that read `importer_count == 0` as "unreachable": a helper
    imported only by a module a loader can reach was offered for deletion as
    part of a dead cluster."""
    from jcodemunch_mcp.investigator import investigate_deletion_safety

    files = dict(SCOPED)
    files["helpers.py"] = "def assist():\n    return 1\n"
    files["adapters/alpha.py"] = "from helpers import assist\n\ndef build():\n    return assist()\n"
    repo, storage = _index(tmp_path, files)
    r = investigate_deletion_safety(repo, "assist", storage_path=storage)
    assert "deletion_cluster" not in r, r.get("deletion_cluster")
    assert r["verdict"] == "unsafe", r["verdict"]
    assert "export_not_imported" in r["refuted_obligations"]


def test_the_disclosure_survives_the_compact_encoding(tmp_path):
    repo, storage = _index(tmp_path, SCOPED)
    r = find_importers(repo, file_path="adapters/alpha.py", storage_path=storage)
    payload, _encoding_id = fi_schema.encode("find_importers", r)
    back = fi_schema.decode(payload)
    assert back["dynamic_import_boundary"] == r["dynamic_import_boundary"]

    repo2, storage2 = _index(tmp_path / "o", OPAQUE)
    o = find_importers(repo2, file_path="lonely.py", storage_path=storage2)
    assert fi_schema.decode(fi_schema.encode("find_importers", o)[0])["dynamic_imports_unfollowed"] == o["dynamic_imports_unfollowed"]
