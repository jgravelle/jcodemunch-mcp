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


def test_a_withheld_score_stays_withheld_and_says_why(tmp_path, monkeypatch):
    """#879: an unmeasurable axis withholds `risk_score` as None. The entry must
    keep the None and the disclosure, never turn it into a 0."""
    import jcodemunch_mcp.tools.get_pr_risk_profile as producer

    repo_id, storage = _repo_with_one_change(tmp_path)
    real = producer.get_pr_risk_profile(repo=repo_id, storage_path=storage)
    withheld = dict(
        real, risk_score=None, risk_level=None, unmeasurable_axes=["blast_radius"]
    )
    monkeypatch.setattr(producer, "get_pr_risk_profile", lambda **kw: withheld)
    entry = _risk_entry(repo_id, storage)
    assert entry["risk_score"] is None
    assert entry["risk_level"] is None
    assert entry["unmeasurable_axes"] == ["blast_radius"]


def test_a_lower_bound_score_says_so(tmp_path, monkeypatch):
    import jcodemunch_mcp.tools.get_pr_risk_profile as producer

    repo_id, storage = _repo_with_one_change(tmp_path)
    real = producer.get_pr_risk_profile(repo=repo_id, storage_path=storage)
    bounded = dict(real, risk_score_is_lower_bound=True)
    monkeypatch.setattr(producer, "get_pr_risk_profile", lambda **kw: bounded)
    entry = _risk_entry(repo_id, storage)
    assert entry["risk_score_is_lower_bound"] is True
