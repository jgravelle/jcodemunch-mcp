"""The capsule's risk entry carries what `get_pr_risk_profile` publishes (LEDGER L-61).

`_stage_risk` read `composite_risk`, `verdict` and `signals`, keys the producer
has never published (it publishes `risk_score`, `risk_level` and
`signal_breakdown`), so the entry carried `0`, `""` and `{}` for every PR.
The guard runs the REAL producer: a mock that returns a dict would supply
whatever keys the consumer expects and could not see this defect.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from jcodemunch_mcp.tools.assemble_task_context import assemble_task_context
from jcodemunch_mcp.tools.get_pr_risk_profile import get_pr_risk_profile
from jcodemunch_mcp.tools.index_folder import index_folder


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo_with_one_change(tmp_path: Path) -> tuple[str, str]:
    """Indexed at the first commit; HEAD changes a function another file imports."""
    root = tmp_path / "riskfix"
    root.mkdir()
    (root / "core.py").write_text(
        "def load(name):\n    return name\n\n\ndef save(name, value):\n    return value\n",
        encoding="utf-8",
    )
    (root / "consumer.py").write_text(
        "from core import load\n\n\ndef serve():\n    return load('a')\n",
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
        "def load(name):\n    if not name:\n        raise ValueError(name)\n"
        "    for _ in range(3):\n        name = name.strip()\n    return name\n\n\n"
        "def save(name, value):\n    return value\n",
        encoding="utf-8",
    )
    _git(["commit", "-q", "-am", "change load"], root)
    return repo_id, storage


def _risk_entry(repo_id: str, storage: str) -> dict:
    out = assemble_task_context(
        repo_id,
        "review the risk of this change",
        intent="review",
        include=["risk"],
        token_budget=20000,
        storage_path=storage,
    )
    entries = [e for e in out.get("entries", []) if e.get("stage") == "risk"]
    assert len(entries) == 1, out
    return entries[0]


def test_the_risk_entry_carries_the_producers_score_level_and_signals(tmp_path):
    repo_id, storage = _repo_with_one_change(tmp_path)
    profile = get_pr_risk_profile(repo=repo_id, storage_path=storage)
    assert "error" not in profile, profile
    assert profile["changed_symbols_count"] > 0, (
        "the fixture must produce a real change"
    )
    entry = _risk_entry(repo_id, storage)
    assert entry["risk_score"] == profile["risk_score"]
    assert entry["risk_level"] == profile["risk_level"]
    assert entry["signal_breakdown"] == profile["signal_breakdown"]
    assert entry["signal_breakdown"], "a real change has signals"
    assert entry["recommendations"] == (profile.get("recommendations") or [])[:3]


def test_the_risk_entry_no_longer_carries_keys_nobody_publishes(tmp_path):
    repo_id, storage = _repo_with_one_change(tmp_path)
    entry = _risk_entry(repo_id, storage)
    for invented in ("composite_risk", "verdict", "signals"):
        assert invented not in entry, invented


def test_every_key_the_stage_reads_is_one_the_producer_publishes(tmp_path):
    """The contract, read off a real response, so a renamed producer key fails here."""
    repo_id, storage = _repo_with_one_change(tmp_path)
    profile = get_pr_risk_profile(repo=repo_id, storage_path=storage)
    entry = _risk_entry(repo_id, storage)
    carried = set(entry) - {"stage", "source_tool", "tokens", "label"}
    assert carried <= set(profile), carried - set(profile)


def _go_package_repo(tmp_path: Path, *, edit_python: bool) -> tuple[str, str]:
    """The #879 fixture: a Go file the file-level import graph cannot walk (Go
    imports a PACKAGE), beside a Python pair it can. Indexed at the first
    commit; HEAD edits the Go file, and the Python file when asked."""
    from tests.test_importer_walkers_ask_blast_verdict import (
        BILLING_PY,
        CONTROL_PY,
        GO_MOD,
        ORDER_REPO_GO,
        PICK_SERVICE_GO,
    )

    root = tmp_path / "fx"
    for d in ("repository", "pkg", "services"):
        (root / d).mkdir(parents=True)
    (root / "go.mod").write_text(GO_MOD)
    (root / "repository" / "order_repo.go").write_text(ORDER_REPO_GO)
    (root / "pkg" / "pick_service.go").write_text(PICK_SERVICE_GO)
    for init in ("__init__.py", "services/__init__.py", "pkg/__init__.py"):
        (root / init).write_text("")
    (root / "services" / "billing.py").write_text(BILLING_PY)
    (root / "pkg" / "control.py").write_text(CONTROL_PY)
    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@t.test"], root)
    _git(["config", "user.name", "T"], root)
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "initial"], root)
    storage = str(tmp_path / "store")
    res = index_folder(
        path=str(root),
        identity_mode="local",
        use_ai_summaries=False,
        storage_path=storage,
    )
    assert res["success"], res
    go = root / "repository" / "order_repo.go"
    go.write_text(go.read_text().replace("return nil", "return nil // changed"))
    if edit_python:
        py = root / "services" / "billing.py"
        py.write_text(py.read_text().replace("* 100", "* 101"))
    _git(["commit", "-qam", "edit"], root)
    return res["repo"], storage


def test_a_withheld_score_stays_withheld_and_says_why(tmp_path):
    """#879: only the Go file changed, so the blast axis is unmeasured and the
    real profile withholds `risk_score`. The entry keeps the None and the
    disclosure, never a 0."""
    repo_id, storage = _go_package_repo(tmp_path, edit_python=False)
    profile = get_pr_risk_profile(repo=repo_id, storage_path=storage)
    assert profile["risk_score"] is None and profile["unmeasurable_axes"] == [
        "blast_radius"
    ]
    entry = _risk_entry(repo_id, storage)
    assert entry["risk_score"] is None
    assert entry["risk_level"] is None
    assert entry["unmeasurable_axes"] == profile["unmeasurable_axes"]
    assert entry["signal_breakdown"] == profile["signal_breakdown"]
    carried = set(entry) - {"stage", "source_tool", "tokens", "label"}
    assert carried <= set(profile), carried - set(profile)


def test_a_lower_bound_score_says_so(tmp_path):
    """The Go file and a Python file changed: the score is measured but is a
    lower bound, and the real profile says so beside it."""
    repo_id, storage = _go_package_repo(tmp_path, edit_python=True)
    profile = get_pr_risk_profile(repo=repo_id, storage_path=storage)
    assert profile["risk_score_is_lower_bound"] is True
    entry = _risk_entry(repo_id, storage)
    assert entry["risk_score_is_lower_bound"] is True
    assert entry["risk_score"] == profile["risk_score"]
    carried = set(entry) - {"stage", "source_tool", "tokens", "label"}
    assert carried <= set(profile), carried - set(profile)


def test_a_long_unresolvable_list_is_capped_with_its_total():
    from jcodemunch_mcp.tools import assemble_task_context as atc

    cap = atc._RISK_UNRESOLVABLE_FILES_CAP
    files = [f"pkg{i}/f.go" for i in range(cap + 5)]
    breakdown = {
        "blast_radius": {"score": 0.4, "unresolvable_files": files},
        "churn": {"score": 0.1},
    }
    capped = atc._capped_signal_breakdown(breakdown)
    blast = capped["blast_radius"]
    assert blast["unresolvable_files"] == files[:cap]
    assert blast["unresolvable_files_total"] == cap + 5
    assert blast["unresolvable_files_truncated"] is True
    assert capped["churn"] == breakdown["churn"]
    assert breakdown["blast_radius"]["unresolvable_files"] == files, (
        "the input is not edited"
    )
    short = {"blast_radius": {"unresolvable_files": files[:cap]}}
    assert atc._capped_signal_breakdown(short) == short


def test_the_stage_applies_the_cap(tmp_path, monkeypatch):
    """The helper alone is not the path that ships the list (review): the
    stage must call it. The producer's KEYS are guarded by the real-producer
    tests above; this one only lengthens the real response's list."""
    import jcodemunch_mcp.tools.get_pr_risk_profile as producer
    from jcodemunch_mcp.tools import assemble_task_context as atc

    repo_id, storage = _go_package_repo(tmp_path, edit_python=True)
    real = producer.get_pr_risk_profile(repo=repo_id, storage_path=storage)
    cap = atc._RISK_UNRESOLVABLE_FILES_CAP
    files = [f"pkg{i}/f.go" for i in range(cap + 3)]
    blast = dict(real["signal_breakdown"]["blast_radius"], unresolvable_files=files)
    long = dict(
        real, signal_breakdown=dict(real["signal_breakdown"], blast_radius=blast)
    )
    monkeypatch.setattr(producer, "get_pr_risk_profile", lambda **kw: long)
    entry = _risk_entry(repo_id, storage)
    shipped = entry["signal_breakdown"]["blast_radius"]
    assert shipped["unresolvable_files"] == files[:cap]
    assert shipped["unresolvable_files_total"] == cap + 3
    assert shipped["unresolvable_files_truncated"] is True
