"""The capsule's changed entry carries what `get_changed_symbols` publishes (LEDGER L-272).

`_stage_changed` read `added`, `modified`, `removed` and `renamed`, keys the
producer has never published (it publishes `added_symbols`, `removed_symbols`
and `changed_symbols`, each changed row naming its `change_type`), so all four
lists were empty for every diff and a review capsule said nothing changed.
The guard runs the REAL producer, the L-61 rule: a mock that returns a dict
would supply whatever keys the consumer expects and could not see this defect.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from jcodemunch_mcp.tools import assemble_task_context as atc
from jcodemunch_mcp.tools.assemble_task_context import assemble_task_context
from jcodemunch_mcp.tools.get_changed_symbols import get_changed_symbols
from jcodemunch_mcp.tools.index_folder import index_folder

_ENVELOPE = {"stage", "source_tool", "tokens", "label"}


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git"] + args, cwd=str(cwd), check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo_with_every_change_type(tmp_path: Path) -> tuple[str, str]:
    """Indexed at the first commit; HEAD adds, removes and modifies a function."""
    root = tmp_path / "changedfix"
    root.mkdir()
    (root / "core.py").write_text(
        "def load(name):\n    return name\n\n\ndef drop(name):\n    return None\n",
        encoding="utf-8",
    )
    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@t.test"], root)
    _git(["config", "user.name", "T"], root)
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "initial"], root)
    storage = str(tmp_path / ".index")
    repo_id = index_folder(str(root), use_ai_summaries=False, storage_path=storage)[
        "repo"
    ]
    (root / "core.py").write_text(
        "def load(name):\n    return name.strip()\n\n\ndef keep(name):\n    return name\n",
        encoding="utf-8",
    )
    _git(["commit", "-q", "-am", "change"], root)
    return repo_id, storage


def _changed_entry(repo_id: str, storage: str) -> dict:
    out = assemble_task_context(
        repo_id,
        "review what changed in this pr",
        intent="review",
        include=["changed"],
        token_budget=20000,
        storage_path=storage,
    )
    entries = [e for e in out.get("entries", []) if e.get("stage") == "changed"]
    assert len(entries) == 1, out
    return entries[0]


def _ids(rows: list[dict]) -> list[str]:
    return [r["symbol_id"] for r in rows]


def test_the_changed_entry_names_what_the_producer_found(tmp_path):
    repo_id, storage = _repo_with_every_change_type(tmp_path)
    diff = get_changed_symbols(
        repo=repo_id, include_blast_radius=False, storage_path=storage
    )
    assert "error" not in diff, diff
    assert diff["added_count"] and diff["removed_count"] and diff["changed_count"], (
        "the fixture must produce every change type"
    )
    entry = _changed_entry(repo_id, storage)
    assert _ids(entry["added_symbols"]) == _ids(diff["added_symbols"])
    assert _ids(entry["removed_symbols"]) == _ids(diff["removed_symbols"])
    assert _ids(entry["changed_symbols"]) == _ids(diff["changed_symbols"])
    assert [r["change_type"] for r in entry["changed_symbols"]] == [
        r["change_type"] for r in diff["changed_symbols"]
    ]
    for key in (
        "added_count",
        "removed_count",
        "changed_count",
        "symbol_diff_complete",
    ):
        assert entry[key] == diff[key], key


def test_every_key_the_stage_carries_is_one_the_producer_publishes(tmp_path):
    """The contract, read off a real response, so a renamed producer key fails here."""
    repo_id, storage = _repo_with_every_change_type(tmp_path)
    diff = get_changed_symbols(
        repo=repo_id, include_blast_radius=False, storage_path=storage
    )
    entry = _changed_entry(repo_id, storage)
    carried = set(entry) - _ENVELOPE
    assert carried <= set(diff), carried - set(diff)
    for rows in ("added_symbols", "removed_symbols", "changed_symbols"):
        for row in entry[rows]:
            src = next(r for r in diff[rows] if r["symbol_id"] == row["symbol_id"])
            assert set(row) <= set(src), (rows, set(row) - set(src))


def test_a_long_list_is_cut_and_its_count_is_the_whole(tmp_path, monkeypatch):
    """A page of rows beside the producer's own count, never `len()` of the page (#559)."""
    import jcodemunch_mcp.tools.get_changed_symbols as producer

    repo_id, storage = _repo_with_every_change_type(tmp_path)
    real = producer.get_changed_symbols(
        repo=repo_id, include_blast_radius=False, storage_path=storage
    )
    cap = atc._CHANGED_ROWS_CAP
    row = dict(real["added_symbols"][0])
    many = [dict(row, symbol_id=f"core.py::f{i}#function") for i in range(cap + 4)]
    long = dict(real, added_symbols=many, added_count=len(many))
    monkeypatch.setattr(producer, "get_changed_symbols", lambda **kw: long)
    entry = _changed_entry(repo_id, storage)
    assert _ids(entry["added_symbols"]) == _ids(many[:cap])
    assert entry["added_count"] == cap + 4
