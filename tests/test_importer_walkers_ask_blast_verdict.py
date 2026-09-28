"""A tool that walks the importer graph asks `blast_verdict` before it reads
an empty walk as "nothing depends on this" (#879).

#718 made `get_blast_radius.blast_verdict` the one authority on an importer
walk, because the walk alone cannot tell "nothing depends on this" from "the
graph cannot reach this" (#415: Go imports a PACKAGE, so the file-level graph
lands on no member file). Using it was opt-in, and three callers never did:
- `plan_refactoring`'s collision check answered `safe: True` from an empty walk;
- its rename, move and signature plans listed no affected files;
- `get_pr_risk_profile`'s blast signal read zero dependents.
And `check_rename_safe`, the public tool for the same collision question,
walks by hand and said `safe: True` the same way (found while fixing it).

The property: an empty walk from a file the graph cannot reach is disclosed
as unresolvable, never answered as a zero, and a walk the graph CAN answer is
unchanged. The ratchet at the bottom fails when a module walks the graph
without asking, unless it is listed with the LEDGER row that tracks it.
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

from jcodemunch_mcp.tools.index_folder import index_folder

REPO = Path(__file__).resolve().parents[1]

GO_MOD = "module fixture\n\ngo 1.22\n"
ORDER_REPO_GO = """package repository

type OrderRepo struct{ db string }

func (r *OrderRepo) DeleteItem(id string) error { return nil }
"""
PICK_SERVICE_GO = """package pkg

import "fixture/repository"

type PickService struct{ orderRepo *repository.OrderRepo }

func (s *PickService) StartSession(id string) error {
\treturn s.orderRepo.DeleteItem(id)
}
"""
BILLING_PY = "def charge_card(amount):\n    return amount * 100\n"
CONTROL_PY = (
    "from services.billing import charge_card\n\n\n"
    "def run_charge(amount):\n    return charge_card(amount)\n"
)


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def fixture_repo(tmp_path):
    root = tmp_path / "fx"
    for d in ("repository", "pkg", "services"):
        (root / d).mkdir(parents=True)
    (root / "go.mod").write_text(GO_MOD)
    (root / "repository" / "order_repo.go").write_text(ORDER_REPO_GO)
    (root / "pkg" / "pick_service.go").write_text(PICK_SERVICE_GO)
    (root / "__init__.py").write_text("")
    (root / "services" / "__init__.py").write_text("")
    (root / "pkg" / "__init__.py").write_text("")
    (root / "services" / "billing.py").write_text(BILLING_PY)
    (root / "pkg" / "control.py").write_text(CONTROL_PY)
    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@t.test"], root)
    _git(["config", "user.name", "T"], root)
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "initial"], root)
    store = str(tmp_path / "store")
    res = index_folder(path=str(root), identity_mode="local", use_ai_summaries=False, storage_path=store)
    assert res["success"], res
    return root, res["repo"], store


def _plan(repo, store, symbol, refactor_type, **kw):
    from jcodemunch_mcp.tools.plan_refactoring import plan_refactoring

    out = plan_refactoring(repo=repo, symbol=symbol, refactor_type=refactor_type, storage_path=store, **kw)
    assert "error" not in out, out
    return out


def test_a_collision_the_graph_cannot_see_is_not_safe(fixture_repo):
    """`StartSession` already exists in `pkg/pick_service.go`, which calls
    `DeleteItem` through a package import the file graph cannot follow. The
    collision check walked nothing, checked only the defining file, and said
    `safe: True`."""
    _, repo, store = fixture_repo
    out = _plan(repo, store, "DeleteItem", "rename", new_name="StartSession")
    check = out["collision_check"]
    assert check["safe"] is not True, check
    assert check["unresolvable"]["reason"], check


@pytest.mark.parametrize(
    "refactor_type,kw",
    [
        ("rename", {"new_name": "RemoveItem"}),
        ("signature", {"new_signature": "func (r *OrderRepo) DeleteItem(id string, force bool) error"}),
        ("move", {"new_file": "repository/moved.go"}),
    ],
)
def test_a_plan_says_its_affected_files_are_unresolvable(fixture_repo, refactor_type, kw):
    _, repo, store = fixture_repo
    out = _plan(repo, store, "DeleteItem", refactor_type, **kw)
    assert out["affected_files_unresolvable"]["reason"], out


def test_a_walk_the_graph_can_answer_is_unchanged(fixture_repo):
    """The control: `charge_card` is imported by name, so the walk reaches
    `pkg/control.py`, the collision with `run_charge` is found, and nothing is
    marked unresolvable."""
    _, repo, store = fixture_repo
    out = _plan(repo, store, "charge_card", "rename", new_name="run_charge")
    assert out["collision_check"]["safe"] is False
    assert "unresolvable" not in out["collision_check"]
    assert "affected_files_unresolvable" not in out
    clean = _plan(repo, store, "charge_card", "rename", new_name="bill_card")
    assert clean["collision_check"]["safe"] is True
    assert {e["file"] for e in clean["edits"]} == {"services/billing.py", "pkg/control.py"}


def test_check_rename_safe_does_not_certify_files_it_could_not_reach(fixture_repo):
    """The public tool answers the same question with its own walk, and said
    `safe: True` for the same Go collision (found while fixing #879)."""
    from jcodemunch_mcp.tools.check_rename_safe import check_rename_safe

    _, repo, store = fixture_repo
    out = check_rename_safe(repo, "DeleteItem", "StartSession", storage_path=store)
    assert out["safe"] is None, out
    assert out["unresolvable"]["reason"], out
    control = check_rename_safe(repo, "charge_card", "run_charge", storage_path=store)
    assert control["safe"] is False and "unresolvable" not in control
    clean = check_rename_safe(repo, "charge_card", "bill_card", storage_path=store)
    assert clean["safe"] is True and clean["checked_files"] == 2


def test_the_pr_risk_blast_signal_names_what_it_could_not_walk(fixture_repo):
    from jcodemunch_mcp.tools.get_pr_risk_profile import get_pr_risk_profile

    root, repo, store = fixture_repo
    first = _git(["rev-parse", "HEAD"], root)
    go = root / "repository" / "order_repo.go"
    go.write_text(go.read_text().replace("return nil", "return nil // changed"))
    py = root / "services" / "billing.py"
    py.write_text(py.read_text().replace("* 100", "* 101"))
    _git(["commit", "-qam", "edit"], root)
    out = get_pr_risk_profile(repo, base_ref=first, head_ref="HEAD", storage_path=store)
    blast = out["signal_breakdown"]["blast_radius"]
    assert blast["unresolvable_files"] == ["repository/order_repo.go"], blast
    assert blast["score_is_lower_bound"] is True
    assert blast["affected_files"] >= 1  # the Python control's importer is still counted


# --- the ratchet -----------------------------------------------------------

WALKERS = ("_bfs_importers", "_build_reverse_adjacency")
# A module that walks the graph without asking, and the LEDGER row tracking it.
NOT_YET_ASKING = {
    "src/jcodemunch_mcp/tools/find_dead_code.py": "L-59",
    "src/jcodemunch_mcp/tools/get_dead_code_v2.py": "L-59",
    "src/jcodemunch_mcp/tools/get_untested_symbols.py": "L-59",
    "src/jcodemunch_mcp/tools/get_call_hierarchy.py": "L-59",
    "src/jcodemunch_mcp/tools/get_impact_preview.py": "L-59",
}
# The authority itself and the walk's own definitions.
AUTHORITY = {"src/jcodemunch_mcp/tools/get_blast_radius.py"}
# Walks written by hand (a `resolve_specifier` loop compared against the
# target file), which no scan for the walker's NAME can see. Each must ask.
HAND_ROLLED = {"src/jcodemunch_mcp/tools/check_rename_safe.py"}


def _names_called(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            out.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return out


def test_every_importer_walk_asks_blast_verdict():
    walkers, asking = set(), set()
    for path in sorted((REPO / "src").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        called = _names_called(path)
        if called & set(WALKERS):
            walkers.add(rel)
            if "blast_verdict" in called:
                asking.add(rel)
    silent = walkers - asking - AUTHORITY
    assert silent == set(NOT_YET_ASKING), (
        f"walk the importer graph without asking blast_verdict: {sorted(silent - set(NOT_YET_ASKING))}; "
        f"listed but no longer silent (remove them): {sorted(set(NOT_YET_ASKING) - silent)}"
    )
    for rel in sorted(HAND_ROLLED):
        assert "blast_verdict" in _names_called(REPO / rel), f"{rel} walks importers by hand without blast_verdict"
    ledger = (REPO / "docs" / "workflows" / "LEDGER.md").read_text(encoding="utf-8")
    for rel, row in NOT_YET_ASKING.items():
        m = re.search(rf"^\| {re.escape(row)} \|.*$", ledger, re.M)
        assert m and "OPEN" in m.group(0), f"{rel} names {row}, which is not an OPEN LEDGER row"
