"""A session sees the current workspace's indexes by default (small-repo plan step 6).

Every session sharing an index store used to see every repository indexed on
the machine: `list_repos` returned them all and a bare repo name resolved across
all of them. jjg's ruling 5 (2026-10-13) makes the workspace the default scope,
with `repo_visibility: "all"` to widen it. The edge rulings (2026-10-15): an
exact `owner/name` or a path still resolves anywhere; a remote index (no
folder) is left off the list; a workspace that holds no index lists everything
and says so, which is what a Claude Desktop launch (cwd = home or a system
folder) gets.

Indexes here are built by the real `index_folder` into a scratch store, so the
listing's `source_root` is the producer's. The one hand-written row is the remote
case, which `index_folder` cannot produce.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp.storage.sqlite_store import _cache_clear
from jcodemunch_mcp.tools import _utils
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.list_repos import list_repos


def _project(root: Path, name: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "mod.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    return folder


def _index(folder: Path, store: Path) -> str:
    result = index_folder(str(folder), use_ai_summaries=False, storage_path=str(store))
    assert result["success"] is True, result
    return result["repo"]


@pytest.fixture
def visibility(monkeypatch):
    """Set `repo_visibility` for the test; None leaves the shipped default."""
    real_get = config_module.get
    state = {"value": None}

    def fake_get(key, *a, **k):
        if key == "repo_visibility" and state["value"] is not None:
            return state["value"]
        return real_get(key, *a, **k)

    monkeypatch.setattr(config_module, "get", fake_get)

    def set_(value):
        state["value"] = value

    return set_


@pytest.fixture
def store_with_three(tmp_path, monkeypatch):
    """A workspace with one indexed project, and one indexed project outside it."""
    _cache_clear()
    _utils._bare_name_cache.clear()
    store = tmp_path / "store"
    ws = tmp_path / "work"
    inside = _project(ws, "inside")
    outside = _project(tmp_path / "elsewhere", "outside")
    ids = {"inside": _index(inside, store), "outside": _index(outside, store)}
    monkeypatch.chdir(ws)
    return store, ws, ids


def _listed(result: dict) -> set[str]:
    return {r["repo"] for r in result["repos"]}


def test_the_default_lists_only_the_workspace_and_says_what_it_hid(store_with_three, visibility):
    store, ws, ids = store_with_three
    out = list_repos(storage_path=str(store))
    assert _listed(out) == {ids["inside"]}
    assert out["count"] == 1
    vis = out["visibility"]
    assert vis["mode"] == "workspace"
    assert vis["hidden_count"] == 1
    assert "repo_visibility" in vis["hint"]
    assert ids["outside"] not in str(vis), "the hidden index is counted, never named"


def test_all_restores_the_full_listing_with_no_visibility_block(store_with_three, visibility):
    store, ws, ids = store_with_three
    visibility("all")
    out = list_repos(storage_path=str(store))
    assert _listed(out) == {ids["inside"], ids["outside"]}
    assert "visibility" not in out


def test_a_session_in_a_subfolder_sees_the_repo_that_contains_it(store_with_three, visibility, monkeypatch):
    store, ws, ids = store_with_three
    sub = ws / "inside" / "pkg"
    sub.mkdir()
    monkeypatch.chdir(sub)
    out = list_repos(storage_path=str(store))
    assert _listed(out) == {ids["inside"]}


def test_a_workspace_with_no_index_lists_everything_and_says_it_is_unscoped(store_with_three, visibility, tmp_path, monkeypatch):
    store, ws, ids = store_with_three
    empty = tmp_path / "unrelated"
    empty.mkdir()
    monkeypatch.chdir(empty)
    out = list_repos(storage_path=str(store))
    assert _listed(out) == {ids["inside"], ids["outside"]}
    vis = out["visibility"]
    assert vis["mode"] == "all"
    assert vis["reason"] == "no_index_in_workspace"


def test_an_empty_store_keeps_its_empty_hint(tmp_path, visibility, monkeypatch):
    _cache_clear()
    store = tmp_path / "empty_store"
    store.mkdir()
    monkeypatch.chdir(tmp_path)
    out = list_repos(storage_path=str(store))
    assert out["repos"] == [] and out.get("empty") is True
    assert "visibility" not in out


def test_an_index_with_no_folder_is_hidden_under_the_default():
    from jcodemunch_mcp.tools._visibility import scope_repos

    ws = Path.cwd().resolve()
    remote = {"repo": "octo/remote", "source_root": ""}
    local = {"repo": "local/here", "source_root": str(ws)}
    visible, info = scope_repos([remote, local], mode="workspace", workspace=ws)
    assert [r["repo"] for r in visible] == ["local/here"]
    assert info["hidden_count"] == 1


def test_an_exact_id_outside_the_workspace_still_resolves(store_with_three, visibility):
    store, ws, ids = store_with_three
    owner, name = _utils.resolve_repo(ids["outside"], storage_path=str(store))
    assert f"{owner}/{name}" == ids["outside"]


def test_an_ambiguous_bare_name_resolves_to_the_workspace_copy(tmp_path, visibility, monkeypatch):
    _cache_clear()
    _utils._bare_name_cache.clear()
    store = tmp_path / "store"
    ws = tmp_path / "work"
    mine = _index(_project(ws, "app"), store)
    theirs = _index(_project(tmp_path / "elsewhere", "app"), store)
    assert mine != theirs
    monkeypatch.chdir(ws)
    owner, name = _utils.resolve_repo("app", storage_path=str(store))
    assert f"{owner}/{name}" == mine
    visibility("all")
    with pytest.raises(ValueError, match="Ambiguous"):
        _utils.resolve_repo("app", storage_path=str(store))


def test_the_key_is_declared_with_workspace_as_the_default():
    assert config_module.DEFAULTS["repo_visibility"] == "workspace"
    assert config_module.CONFIG_TYPES["repo_visibility"] is str
    assert config_module.ENV_VAR_MAPPING["JCODEMUNCH_REPO_VISIBILITY"] == "repo_visibility"


def test_a_home_folder_launch_lists_everything_and_says_it_is_unscoped(store_with_three, visibility, monkeypatch):
    """Claude Desktop starts in the home folder; containment alone would scope the
    list to every index under home and read as scoped (review of this change)."""
    store, ws, ids = store_with_three
    home = ws.parent
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(home)
    out = list_repos(storage_path=str(store))
    assert _listed(out) == {ids["inside"], ids["outside"]}
    assert out["visibility"]["mode"] == "all"
    assert out["visibility"]["reason"] == "workspace_is_not_a_project"


def test_a_filesystem_root_or_a_folder_above_home_is_not_a_project(tmp_path, monkeypatch):
    from jcodemunch_mcp.tools._visibility import is_project_folder

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home" / "me"))
    assert is_project_folder(Path(tmp_path.anchor)) is False
    assert is_project_folder(tmp_path / "home") is False
    assert is_project_folder(tmp_path / "home" / "me") is False
    assert is_project_folder(tmp_path / "home" / "me" / "proj") is True


def test_an_unknown_repo_visibility_value_reads_as_the_default(store_with_three, visibility):
    store, ws, ids = store_with_three
    visibility("everything-please")
    out = list_repos(storage_path=str(store))
    assert _listed(out) == {ids["inside"]}
    assert out["visibility"]["mode"] == "workspace"


def test_a_bare_name_with_two_copies_in_the_workspace_stays_ambiguous(tmp_path, visibility, monkeypatch):
    _cache_clear()
    _utils._bare_name_cache.clear()
    store = tmp_path / "store"
    ws = tmp_path / "work"
    _index(_project(ws / "a", "app"), store)
    _index(_project(ws / "b", "app"), store)
    _index(_project(tmp_path / "elsewhere", "app"), store)
    monkeypatch.chdir(ws)
    with pytest.raises(ValueError, match="Ambiguous"):
        _utils.resolve_repo("app", storage_path=str(store))
