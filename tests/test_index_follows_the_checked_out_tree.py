"""L-196: an index follows the checked-out tree; branch delta layers are dormant.

1.50.0 announced per-branch delta layers: one base index, plus a small delta
per git branch, so a branch switch would not re-index. The writer enters delta
mode only when the base index records a branch different from the checked-out
one, but no full save records the base's branch, so it never has. And no tool
reads a branch view: every reader loads the base index. jjg ruled on 2026-10-15
to keep the behaviour readers rely on and correct the record instead of
enabling the writer, which alone would have served a stale base on every
feature branch.

This pins that behaviour. A pass on another branch updates the base index that
readers load, and writes no delta. Enabling delta layers must change this test
and the read path together.
"""

from __future__ import annotations

import subprocess

from jcodemunch_mcp.storage.index_store import IndexStore
from jcodemunch_mcp.tools.index_folder import index_folder


def _git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=str(cwd), check=True, capture_output=True,
    )


def test_a_pass_on_another_branch_updates_the_index_readers_load(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    _git("init", "-b", "main", cwd=repo)
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)
    store_path = tmp_path / "store"

    first = index_folder(str(repo), use_ai_summaries=False, storage_path=str(store_path), identity_mode="git")
    assert first["success"] is True, first
    owner, name = first["repo"].split("/", 1)

    _git("checkout", "-b", "feature", cwd=repo)
    (repo / "a.py").write_text("def alpha():\n    return 1\n\n\ndef on_feature():\n    return 2\n", encoding="utf-8")
    second = index_folder(str(repo), use_ai_summaries=False, storage_path=str(store_path), identity_mode="git")
    assert second["success"] is True, second

    store = IndexStore(base_path=str(store_path))
    base = store.load_index(owner, name)
    assert "on_feature" in {s["name"] for s in base.symbols}, "readers load the base index; it must match the checkout"
    assert store.list_branches(owner, name) == [], "no branch delta is written"
    assert "branch" not in second, second.get("branch")
