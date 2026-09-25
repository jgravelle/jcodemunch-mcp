"""`get_changed_symbols(include_blast_radius=True)` keeps blast radius's verdict (#718).

Reported by @Torolosko: on a real commit delta, every changed symbol's embedded
blast came back as a bare `"blast_radius": []` -- the shape a consumer reads as
"no downstream impact". Standalone `get_blast_radius` already refuses that zero
when the import graph cannot reach the symbol (#415: a package-granular Go
import lands on no file), but the embedded path called `_bfs_importers` directly
and never asked. A second call site reproducing the authority's walk without
its verdict: Standing lesson 08-19.

⚠⚠ The embedded path has a case the standalone one does not: the importer graph
is the INDEX's, and `since_sha` defaults to the indexed commit, so a file ADDED
in the diff is not in the graph at all. Its symbols' empty blast is a question
the graph was never asked, and it must not read as absence either.

The controls matter as much as the refusals: a symbol something imports keeps
its importers, and a symbol nothing imports gets a verdict that CAN prove it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from jcodemunch_mcp.tools.get_blast_radius import get_blast_radius
from jcodemunch_mcp.tools.get_changed_symbols import get_changed_symbols
from jcodemunch_mcp.tools.index_folder import index_folder

GO_MOD = "module fixture\n\ngo 1.22\n"
ORDER_REPO_GO = """package repository

type OrderRepo struct{ db string }

func (r *OrderRepo) DeleteItem(id string) error { return nil }
"""
PICK_SERVICE_GO = """package pkg

import "fixture/repository"

func Start(r *repository.OrderRepo, id string) error {
\treturn r.DeleteItem(id)
}
"""


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True,
        encoding="utf-8", stdin=subprocess.DEVNULL,
    ).stdout.strip()


def _repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str, str, str]:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.email", "t@t"], tmp_path)
    _git(["config", "user.name", "t"], tmp_path)
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-qm", "base"], tmp_path)
    storage = str(tmp_path / ".index")
    res = index_folder(str(tmp_path), use_ai_summaries=False, storage_path=storage, identity_mode="local")
    return tmp_path, res["repo"], storage, _git(["rev-parse", "HEAD"], tmp_path)


def _commit(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-qm", "change"], root)


def _entries(result: dict) -> dict[str, dict]:
    assert "error" not in result, result
    return {
        e["name"]: e
        for e in result["added_symbols"] + result["removed_symbols"] + result["changed_symbols"]
    }


def test_a_go_symbol_the_graph_cannot_reach_refuses_its_empty_blast(tmp_path):
    """The reported shape: an empty embedded blast with nothing saying it is unproven."""
    root, repo, storage, base = _repo(tmp_path, {
        "go.mod": GO_MOD,
        "repository/order_repo.go": ORDER_REPO_GO,
        "pkg/pick_service.go": PICK_SERVICE_GO,
    })
    _commit(root, {"repository/order_repo.go": ORDER_REPO_GO.replace("return nil", "return fmt.Errorf(id)")})

    result = get_changed_symbols(repo, since_sha=base, include_blast_radius=True, storage_path=storage)
    entry = _entries(result)["DeleteItem"]
    assert entry["blast_radius"] == [], "precondition: the file graph still finds nothing"
    verdict = entry.get("blast_verdict")
    assert verdict, "an empty embedded blast carries no verdict, so it reads as 'no downstream impact'"
    assert verdict == {"state": "degraded", "absence_refused": True, "reason": "package_granular_imports"}
    full = result["blast_verdicts"][entry["file"]]
    assert full["absence_refused"] is True and "package granularity" in full["note"]


def test_the_embedded_verdict_agrees_with_standalone_blast_radius(tmp_path):
    """One authority: the embedded answer is the standalone answer, not a copy of it."""
    root, repo, storage, base = _repo(tmp_path, {
        "go.mod": GO_MOD,
        "repository/order_repo.go": ORDER_REPO_GO,
        "pkg/pick_service.go": PICK_SERVICE_GO,
    })
    _commit(root, {"repository/order_repo.go": ORDER_REPO_GO.replace("return nil", "return fmt.Errorf(id)")})

    result = get_changed_symbols(repo, since_sha=base, include_blast_radius=True, storage_path=storage)
    embedded = result["blast_verdicts"]["repository/order_repo.go"]
    standalone = get_blast_radius(repo=repo, symbol="DeleteItem", storage_path=storage)["_meta"]["verdict"]
    for key in ("state", "absence_refused", "incomplete", "note"):
        assert embedded.get(key) == standalone.get(key), key


def test_a_symbol_in_a_file_the_index_never_saw_refuses_its_empty_blast(tmp_path):
    """The case only the embedded path has: a file ADDED since the indexed commit."""
    root, repo, storage, base = _repo(tmp_path, {
        "app/__init__.py": "",
        "app/main.py": "def main():\n    return 0\n",
    })
    _commit(root, {
        "app/fresh.py": "def brand_new():\n    return 1\n",
        "app/main.py": "from app.fresh import brand_new\n\ndef main():\n    return brand_new()\n",
    })

    entry = _entries(get_changed_symbols(repo, since_sha=base, include_blast_radius=True, storage_path=storage))["brand_new"]
    assert entry["blast_radius"] == []
    assert entry.get("blast_verdict") == {"state": "degraded", "absence_refused": True, "reason": "file_not_in_index"}


def test_controls_a_reached_symbol_keeps_its_importers_and_an_unimported_one_can_prove_it(tmp_path):
    root, repo, storage, base = _repo(tmp_path, {
        "app/__init__.py": "",
        "app/engine.py": "def run():\n    return 0\n",
        "app/main.py": "from app.engine import run\n\ndef main():\n    return run()\n",
        "app/lonely.py": "def alone():\n    return 0\n",
    })
    _commit(root, {
        "app/engine.py": "def run():\n    return 1\n",
        "app/lonely.py": "def alone():\n    return 1\n",
    })

    entries = _entries(get_changed_symbols(repo, since_sha=base, include_blast_radius=True, storage_path=storage))
    assert entries["run"]["blast_radius"] == ["app/main.py"]
    assert "blast_verdict" not in entries["run"], "a non-empty blast is positive evidence and needs no verdict"
    lonely = entries["alone"]
    assert lonely["blast_radius"] == []
    assert lonely["blast_verdict"] == {"state": "absent", "absence_refused": False, "reason": None}, (
        "a symbol nothing imports must still be provable"
    )


def test_one_verdict_per_file_however_many_symbols_changed_in_it(tmp_path):
    """The verdict describes the file's walk, so it is published once per file."""
    body = "".join(f"def f{i}():\n    return 0\n" for i in range(5))
    root, repo, storage, base = _repo(tmp_path, {"app/__init__.py": "", "app/many.py": body})
    _commit(root, {"app/many.py": body.replace("return 0", "return 1")})

    result = get_changed_symbols(repo, since_sha=base, include_blast_radius=True, storage_path=storage)
    assert len(_entries(result)) == 5
    assert list(result["blast_verdicts"]) == ["app/many.py"]


def test_a_blast_that_cannot_be_computed_says_so(tmp_path, monkeypatch):
    """An index with no import graph: the request is answered, not silently dropped."""
    from jcodemunch_mcp.storage import IndexStore

    root, repo, storage, base = _repo(tmp_path, {"app/lonely.py": "def alone():\n    return 0\n"})
    _commit(root, {"app/lonely.py": "def alone():\n    return 1\n"})

    real_load = IndexStore.load_index

    def _no_graph(self, *a, **k):
        index = real_load(self, *a, **k)
        if index is not None:
            index.imports = None
        return index

    monkeypatch.setattr(IndexStore, "load_index", _no_graph)
    result = get_changed_symbols(repo, since_sha=base, include_blast_radius=True, storage_path=storage)
    assert "error" not in result, result
    assert "no import graph" in result["blast_radius_unavailable"]
