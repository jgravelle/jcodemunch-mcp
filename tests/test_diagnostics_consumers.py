"""Compiler diagnostics mapped to symbols — the consumer half.

PRD: docs/prd-compiler-diagnostics.md. No new MCP tool (catalog moratorium):
the ingested snapshot is read by four tools that already exist.

Covers, with NO producer mocked (the rows come from a real ingest of the
captured fixtures over a real index):
- check_edit_safe: an `error` on the target is a `pre_existing_diagnostics`
  blocker naming tool and code; warnings alone do not block; no data means
  no block and `diagnostics_data_present: False`, never `errors: 0`
- get_changed_symbols: a changed entry carries its diagnostics; the response
  carries `diagnostics_as_of` and a tri-state `diagnostics_current`
- get_pr_risk_profile: a `diagnostics` block that is REPORTED, NOT SCORED —
  the risk score is identical with and without the ingest
- get_symbol_provenance: a `diagnostics` section beside `stack_frequency`
- `diagnostics_currency` is tri-state and None when either side is unknown
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures" / "diagnostics"


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git"] + args, cwd=str(cwd), check=True, capture_output=True, text=True,
    ).stdout.strip()


def _make_git_repo(tmp_path: Path) -> tuple[Path, str, str, Path]:
    """A git repo holding the fixture sources, indexed, first commit returned."""
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

    root = tmp_path / "diagfix"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "mod.py").write_text((FIX / "sample_mod.py").read_text(encoding="utf-8"), encoding="utf-8")
    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@t.test"], root)
    _git(["config", "user.name", "T"], root)
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "initial"], root)
    storage = str(tmp_path / ".index")
    result = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    repo_id = result["repo"]
    owner, name = repo_id.split("/", 1)
    db_path = SQLiteIndexStore(base_path=storage)._db_path(owner, name)
    return root, repo_id, storage, db_path


def _ingest(db_path: Path, fixture: str) -> dict:
    from jcodemunch_mcp.runtime.diagnostics_ingest import ingest_diagnostics_file

    return ingest_diagnostics_file(db_path=str(db_path), file_path=str(FIX / fixture))


# ──────────────────────────────────────────────────────────────────────
# currency helper
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "as_of, live, expected",
    [
        ("abc123", "abc123", True),
        ("abc123", "def456", False),
        (None, "abc123", None),
        ("abc123", None, None),
        (None, None, None),
        ("", "abc123", None),
    ],
)
def test_diagnostics_currency_is_tri_state(as_of, live, expected):
    from jcodemunch_mcp.tools._diagnostics_consume import diagnostics_currency

    assert diagnostics_currency(as_of, live) is expected


# ──────────────────────────────────────────────────────────────────────
# check_edit_safe
# ──────────────────────────────────────────────────────────────────────


def test_check_edit_safe_without_any_ingest_discloses_absence_not_zero(tmp_path):
    from jcodemunch_mcp.tools.check_edit_safe import check_edit_safe

    _, repo_id, storage, _ = _make_git_repo(tmp_path)
    r = check_edit_safe(repo_id, "bad_call", storage_path=storage)
    assert r["signals"]["diagnostics_data_present"] is False
    assert "diagnostics" not in r["signals"]
    assert not [b for b in r["blockers"] if b["kind"] == "pre_existing_diagnostics"]


def test_check_edit_safe_blocks_on_a_pre_existing_error_and_names_it(tmp_path):
    from jcodemunch_mcp.tools.check_edit_safe import check_edit_safe

    root, repo_id, storage, db_path = _make_git_repo(tmp_path)
    ingest = _ingest(db_path, "mypy.jsonl")
    assert ingest["mapped"] == 5
    head = _git(["rev-parse", "HEAD"], root)

    r = check_edit_safe(repo_id, "bad_call", storage_path=storage)
    blockers = [b for b in r["blockers"] if b["kind"] == "pre_existing_diagnostics"]
    assert len(blockers) == 1, r["blockers"]
    b = blockers[0]
    assert b["severity"] == 4
    assert "mypy" in b["detail"] and "arg-type" in b["detail"]
    sig = r["signals"]["diagnostics"]
    assert sig["errors"] == 1 and sig["warnings"] == 0
    assert sig["tools"] == ["mypy"]
    assert sig["as_of"] == head
    assert sig["current"] is True
    assert r["signals"]["diagnostics_data_present"] is True
    assert "mypy" in r["recommended_action"] and "arg-type" in r["recommended_action"]


def test_check_edit_safe_on_a_clean_symbol_has_no_block_and_no_zero_row(tmp_path):
    from jcodemunch_mcp.tools.check_edit_safe import check_edit_safe

    _, repo_id, storage, db_path = _make_git_repo(tmp_path)
    _ingest(db_path, "mypy.jsonl")
    r = check_edit_safe(repo_id, "add", storage_path=storage)
    assert r["signals"]["diagnostics_data_present"] is True
    # The checker RAN and found nothing on `add`: that is a real zero, and it
    # is stated as one. Compare the no-ingest test above, where the key is absent.
    assert r["signals"]["diagnostics"]["errors"] == 0
    assert not [b for b in r["blockers"] if b["kind"] == "pre_existing_diagnostics"]


def test_check_edit_safe_warnings_alone_do_not_block(tmp_path):
    from jcodemunch_mcp.tools.check_edit_safe import check_edit_safe

    _, repo_id, storage, db_path = _make_git_repo(tmp_path)
    ingest = _ingest(db_path, "ruff.json")
    assert ingest["tool"] == "ruff"
    r = check_edit_safe(repo_id, "unused_var", storage_path=storage)
    sig = r["signals"]["diagnostics"]
    assert sig["warnings"] >= 1 and sig["errors"] == 0, sig
    assert not [b for b in r["blockers"] if b["kind"] == "pre_existing_diagnostics"]


def test_check_edit_safe_currency_goes_false_after_a_new_commit(tmp_path):
    from jcodemunch_mcp.tools.check_edit_safe import check_edit_safe

    root, repo_id, storage, db_path = _make_git_repo(tmp_path)
    _ingest(db_path, "mypy.jsonl")
    (root / "README.md").write_text("moved on\n", encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "later"], root)
    r = check_edit_safe(repo_id, "bad_call", storage_path=storage)
    assert r["signals"]["diagnostics"]["current"] is False
    # Still reported: the reader decides what a stale snapshot is worth.
    assert r["signals"]["diagnostics"]["errors"] == 1


# ──────────────────────────────────────────────────────────────────────
# get_changed_symbols
# ──────────────────────────────────────────────────────────────────────


def _commit_edit_to_bad_call(root: Path) -> str:
    p = root / "pkg" / "mod.py"
    src = p.read_text(encoding="utf-8").replace('return add("x", 2)', 'return add("y", 2)')
    p.write_text(src, encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "touch bad_call"], root)
    return _git(["rev-parse", "HEAD"], root)


def test_get_changed_symbols_annotates_changed_entries_and_reports_currency(tmp_path):
    from jcodemunch_mcp.tools.get_changed_symbols import get_changed_symbols

    root, repo_id, storage, db_path = _make_git_repo(tmp_path)
    first = _git(["rev-parse", "HEAD"], root)
    _ingest(db_path, "mypy.jsonl")
    second = _commit_edit_to_bad_call(root)

    r = get_changed_symbols(repo_id, since_sha=first, until_sha=second, storage_path=storage)
    changed = {e["name"]: e for e in r["changed_symbols"]}
    assert "bad_call" in changed, r
    assert changed["bad_call"]["diagnostics"] == {"errors": 1, "warnings": 0, "tools": ["mypy"]}
    assert r["diagnostics_as_of"] == first
    assert r["diagnostics_current"] is False  # snapshot is one commit behind until_sha


def test_get_changed_symbols_without_ingest_carries_no_diagnostics_keys(tmp_path):
    from jcodemunch_mcp.tools.get_changed_symbols import get_changed_symbols

    root, repo_id, storage, _ = _make_git_repo(tmp_path)
    first = _git(["rev-parse", "HEAD"], root)
    second = _commit_edit_to_bad_call(root)
    r = get_changed_symbols(repo_id, since_sha=first, until_sha=second, storage_path=storage)
    assert "diagnostics_as_of" not in r and "diagnostics_current" not in r
    assert all("diagnostics" not in e for e in r["changed_symbols"])


# ──────────────────────────────────────────────────────────────────────
# get_pr_risk_profile
# ──────────────────────────────────────────────────────────────────────


def test_get_pr_risk_profile_reports_diagnostics_without_moving_the_score(tmp_path):
    from jcodemunch_mcp.tools.get_pr_risk_profile import get_pr_risk_profile

    root, repo_id, storage, db_path = _make_git_repo(tmp_path)
    first = _git(["rev-parse", "HEAD"], root)
    second = _commit_edit_to_bad_call(root)

    before = get_pr_risk_profile(repo_id, base_ref=first, head_ref=second, storage_path=storage)
    assert "diagnostics" not in before

    _ingest(db_path, "mypy.jsonl")
    after = get_pr_risk_profile(repo_id, base_ref=first, head_ref=second, storage_path=storage)
    assert after["risk_score"] == before["risk_score"]
    assert "diagnostics" not in after.get("signal_breakdown", {})
    block = after["diagnostics"]
    assert block["basis"] == "reported_not_scored"
    assert block["errors"] == 1 and block["warnings"] == 0
    assert [s["name"] for s in block["changed_symbols_with_errors"]] == ["bad_call"]
    assert block["as_of"] == _git(["rev-parse", "HEAD"], root)
    assert block["current"] is True


# ──────────────────────────────────────────────────────────────────────
# get_symbol_provenance
# ──────────────────────────────────────────────────────────────────────


def test_get_symbol_provenance_carries_a_diagnostics_section_only_when_ingested(tmp_path):
    from jcodemunch_mcp.tools.get_symbol_provenance import get_symbol_provenance

    _, repo_id, storage, db_path = _make_git_repo(tmp_path)
    r0 = get_symbol_provenance(repo_id, "bad_call", storage_path=storage)
    assert "diagnostics" not in r0

    _ingest(db_path, "mypy.jsonl")
    r1 = get_symbol_provenance(repo_id, "bad_call", storage_path=storage)
    d = r1["diagnostics"]
    assert d["errors"] == 1 and d["warnings"] == 0
    assert d["codes"][0]["tool"] == "mypy" and d["codes"][0]["code"] == "arg-type"
    assert d["current"] is True
