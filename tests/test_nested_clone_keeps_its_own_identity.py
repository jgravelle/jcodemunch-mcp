"""A git clone inside an indexed checkout gets its own index.

A benchmark kept its datasets in a gitignored folder of its own checkout.
Each dataset was a full clone with its own ``.git`` and origin, yet
``index_folder`` on one filed it under the outer checkout's index, because
the existing-index lookup matched by path containment. It skipped only a
nested linked worktree (#372), so every dataset became part of one index and
every lookup by the clone's own id found nothing.

The lookup now asks ``checkout_boundary.separate_checkout_between``, the
rule ``resolve_repo`` and the session hooks already use (#1202): a separate
checkout between the path and the indexed root is not part of that root.
"""

import subprocess
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage.git_root import resolve_index_identity
from jcodemunch_mcp.tools.index_folder import index_folder


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _repo(path: Path, origin: str, filename: str, body: str) -> Path:
    path.mkdir(parents=True)
    _git("init", "-q", cwd=path)
    _git("remote", "add", "origin", origin, cwd=path)
    (path / filename).write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def parent_indexed(tmp_path):
    parent = _repo(
        tmp_path / "bench",
        "https://github.com/acme/bench.git",
        "run.py",
        "def run():\n    return 1\n",
    )
    (parent / ".gitignore").write_text("datasets/\n", encoding="utf-8")
    store = tmp_path / "store"
    res = index_folder(
        str(parent),
        use_ai_summaries=False,
        storage_path=str(store),
        identity_mode="git",
    )
    assert res["repo"] == "acme/bench"
    nested = _repo(
        parent / "datasets" / "express",
        "https://github.com/expressjs/express.git",
        "app.js",
        "function createApplication() {\n  return 1;\n}\n",
    )
    return parent, nested, store


@pytest.mark.parametrize("mode", ["config", "git"])
def test_a_nested_clone_is_not_filed_under_the_parent(parent_indexed, mode):
    _, nested, store = parent_indexed
    decision = resolve_index_identity(
        str(nested), mode=mode, store=IndexStore(base_path=str(store))
    )
    assert (decision.owner, decision.name) != ("acme", "bench")
    assert Path(decision.walk_root) == nested.resolve()


def test_indexing_the_nested_clone_builds_its_own_index(parent_indexed):
    parent, nested, store = parent_indexed
    res = index_folder(str(nested), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True
    assert res["repo"] != "acme/bench"
    loaded = IndexStore(base_path=str(store)).load_index("acme", "bench")
    assert loaded is not None
    assert not any("createApplication" == s.get("name") for s in loaded.symbols)


def test_a_plain_subdirectory_still_belongs_to_the_parent(parent_indexed):
    parent, _, store = parent_indexed
    sub = parent / "lib"
    sub.mkdir()
    decision = resolve_index_identity(
        str(sub), mode="config", store=IndexStore(base_path=str(store))
    )
    assert (decision.owner, decision.name) == ("acme", "bench")


def test_local_mode_on_a_nested_clone_is_not_a_conflict_with_the_parent(
    parent_indexed,
):
    """`local` mode refuses a path that an existing git identity already owns.
    The parent's identity used to claim the nested clone, so `local` raised
    IdentityModeConflict naming acme/bench. The clone is not the parent's."""
    _, nested, store = parent_indexed
    decision = resolve_index_identity(
        str(nested), mode="local", store=IndexStore(base_path=str(store))
    )
    assert (decision.owner, decision.name) != ("acme", "bench")
