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
        ("extract", {"new_file": "repository/extracted.go"}),
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
    assert out["risk_score_is_lower_bound"] is True  # beside the number it qualifies (review)
    assert isinstance(out["risk_score"], float)


def test_an_unmeasured_blast_axis_withholds_the_composite(fixture_repo):
    """Only the Go file changed, so no probed file resolved and nothing was
    found: the axis is not measured, and a composite built on a 0.0 there is
    LOWER than the truth. It is withheld, never graded (review of #879;
    Standing lesson 2026-08-28)."""
    from jcodemunch_mcp.tools.get_pr_risk_profile import get_pr_risk_profile

    root, repo, store = fixture_repo
    first = _git(["rev-parse", "HEAD"], root)
    go = root / "repository" / "order_repo.go"
    go.write_text(go.read_text().replace("return nil", "return nil // changed"))
    _git(["commit", "-qam", "edit"], root)
    out = get_pr_risk_profile(repo, base_ref=first, head_ref="HEAD", storage_path=store)
    assert out["risk_score"] is None and out["risk_level"] is None, out
    assert out["unmeasurable_axes"] == ["blast_radius"]
    blast = out["signal_breakdown"]["blast_radius"]
    assert blast["score"] is None and blast["measurable"] is False


def test_a_new_file_is_probed_by_its_package_and_a_new_leaf_is_measured(fixture_repo):
    """A new Go file joins a package an unchanged file imports, so it has a
    dependent the file graph cannot see: probed by its directory, never
    skipped as unindexed (review of #879). A new Python file nothing can yet
    import is a measured zero, and nothing is flagged."""
    from jcodemunch_mcp.tools.get_pr_risk_profile import get_pr_risk_profile

    root, repo, store = fixture_repo
    first = _git(["rev-parse", "HEAD"], root)
    (root / "repository" / "extra.go").write_text("package repository\n\nfunc Extra() int { return 1 }\n")
    _git(["add", "-A"], root)
    _git(["commit", "-qm", "go"], root)
    go_only = get_pr_risk_profile(repo, base_ref=first, head_ref="HEAD", storage_path=store)
    assert go_only["signal_breakdown"]["blast_radius"]["unresolvable_files"] == ["repository/extra.go"]
    assert go_only["risk_score"] is None

    second = _git(["rev-parse", "HEAD"], root)
    (root / "services" / "fresh.py").write_text("def fresh():\n    return 1\n")
    _git(["add", "-A"], root)
    _git(["commit", "-qm", "py"], root)
    py_only = get_pr_risk_profile(repo, base_ref=second, head_ref="HEAD", storage_path=store)
    assert isinstance(py_only["risk_score"], float), py_only
    assert "unresolvable_files" not in py_only["signal_breakdown"]["blast_radius"]
    assert "risk_score_is_lower_bound" not in py_only and "unmeasurable_axes" not in py_only


# --- the ratchet -----------------------------------------------------------
#
# Found by NAME, per FUNCTION, through import aliases (review of #879: a
# module-level scan passed with `_check_collision` walking on its own beside a
# helper that asked, and an aliased `_bfs_importers as walk` passed too). A
# call is resolved through the module's `from ... import X as Y` lines, and
# every function that calls a walker must itself (nested bodies included)
# call `blast_verdict` or `importers_with_verdict`. ⚠ Still a check of CALLS,
# not of control flow: a verdict call under `if False:` satisfies it. And only
# FUNCTIONS are inspected: a walk at module level, in a class body or in a
# lambda is not seen (review of #879; none exists today).

WALKERS = {"_bfs_importers", "_build_reverse_adjacency"}
ASKS = {"blast_verdict", "importers_with_verdict"}
# Modules that walk the graph without asking, and the LEDGER row tracking
# them; `scripts/gap_ledgers.py` requires the row to exist and read OPEN.
NOT_YET_ASKING = {
    "src/jcodemunch_mcp/tools/find_dead_code.py": "L-59",
    "src/jcodemunch_mcp/tools/get_dead_code_v2.py": "L-59",
    "src/jcodemunch_mcp/tools/get_untested_symbols.py": "L-59",
    "src/jcodemunch_mcp/tools/get_call_hierarchy.py": "L-59",
    "src/jcodemunch_mcp/tools/get_impact_preview.py": "L-59",
}
# The authority: it defines the walk and the verdict.
AUTHORITY = {"src/jcodemunch_mcp/tools/get_blast_radius.py"}
# Walks written by hand (a `resolve_specifier` loop compared against the
# target file), which no scan for a walker's NAME can see. The KNOWN
# population, recorded because a hand-kept list is only as good as its
# census: each either asks or is tracked. A census by SHAPE (a loop over
# `index.imports` calling `resolve_specifier`) matched 23 functions on
# 2026-09-28 (recorded in LEDGER L-59), most of them forward graphs and
# centrality, so it is not a gate.
HAND_ROLLED = {"src/jcodemunch_mcp/tools/check_rename_safe.py"}
HAND_ROLLED_NOT_YET = {
    "src/jcodemunch_mcp/tools/get_file_risk.py": "L-59",
    "src/jcodemunch_mcp/tools/find_importers.py": "L-59",
}


def _aliases(tree) -> dict[str, str]:
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                out[a.asname or a.name] = a.name
    return out


def _called(node, aliases) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            out.add(aliases.get(name, name))
    return out


def _outer_silent(path: Path) -> list[str]:
    """Silent functions not nested in a function that asks: a nested helper
    that walks for an outer function that asks is that function's walk."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases = _aliases(tree)
    asking_bodies = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and _called(fn, aliases) & ASKS:
            asking_bodies |= {id(n) for n in ast.walk(fn) if n is not fn}
    return [
        fn.name for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and id(fn) not in asking_bodies
        and _called(fn, aliases) & WALKERS
        and not _called(fn, aliases) & ASKS
    ]


def test_every_importer_walk_asks_blast_verdict():
    silent: dict[str, list[str]] = {}
    for path in sorted((REPO / "src").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel in AUTHORITY:
            continue
        found = _outer_silent(path)
        if found:
            silent[rel] = found
    assert set(silent) == set(NOT_YET_ASKING), (
        f"walk the importer graph without asking blast_verdict: "
        f"{ {k: v for k, v in silent.items() if k not in NOT_YET_ASKING} }; "
        f"listed but no longer silent (remove them): {sorted(set(NOT_YET_ASKING) - set(silent))}"
    )
    for rel in sorted(HAND_ROLLED):
        assert _called(ast.parse((REPO / rel).read_text(encoding="utf-8")), {}) & ASKS, (
            f"{rel} walks importers by hand without blast_verdict"
        )
    for rel in sorted(HAND_ROLLED_NOT_YET):
        assert not _called(ast.parse((REPO / rel).read_text(encoding="utf-8")), {}) & ASKS, (
            f"{rel} asks now: move it from HAND_ROLLED_NOT_YET to HAND_ROLLED"
        )


def test_the_ratchet_sees_a_walk_beside_a_helper_that_asks(tmp_path):
    """Non-vacuity, in-suite: the two shapes review planted past the
    module-level draft -- a function that walks on its own in a module whose
    helper asks, and a walker imported under another name."""
    beside = tmp_path / "beside.py"
    beside.write_text(
        "from .get_blast_radius import importers_with_verdict, _bfs_importers, _build_reverse_adjacency\n"
        "def helper(index, f):\n    return importers_with_verdict(index, f, 1)\n"
        "def check(index, f):\n"
        "    rev = _build_reverse_adjacency(index.imports, set(), {}, None)\n"
        "    return _bfs_importers(f, rev, 1)\n",
        encoding="utf-8",
    )
    aliased = tmp_path / "aliased.py"
    aliased.write_text(
        "from .get_blast_radius import _bfs_importers as walk\n"
        "def check(rev, f):\n    return walk(f, rev, 1)\n",
        encoding="utf-8",
    )
    nested = tmp_path / "nested.py"
    nested.write_text(
        "from .get_blast_radius import blast_verdict, _bfs_importers\n"
        "def outer(index, rev, f):\n"
        "    def inner():\n        return _bfs_importers(f, rev, 1)\n"
        "    found, _ = inner()\n"
        "    return found or blast_verdict(index, set(), f, 0)\n",
        encoding="utf-8",
    )
    assert _outer_silent(beside) == ["check"]
    assert _outer_silent(aliased) == ["check"]
    assert _outer_silent(nested) == []
