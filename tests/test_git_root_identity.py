"""Tests for v1.95.0 git-root-aware index identity (#288 phase 1).

When a `.git/` is found above the indexed path and `git_root_identity`
is on (default), the storage identity comes from `git remote get-url
origin` so a clone of `elastic/kibana` indexes as `elastic/kibana`
regardless of the local folder name.  Repos without a usable origin
keep the v1.94 `local/<basename>-<hash>` identity but still record
the git_root for v1.96 merge logic.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage.git_root import (
    GitRootIdentity,
    detect_git_root,
    _parse_owner_repo,
)
from jcodemunch_mcp.tools.index_folder import (
    _resolve_repo_identity,
    index_folder,
)


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _set_origin(path: Path, url: str) -> None:
    _git("remote", "add", "origin", url, cwd=path)


# ---------------------------------------------------------------------------
# detect_git_root unit tests
# ---------------------------------------------------------------------------


class TestDetectGitRoot:
    def test_no_git_returns_none(self, tmp_path):
        assert detect_git_root(str(tmp_path)) is None

    def test_git_root_with_origin_returns_owner_repo(self, tmp_path):
        repo = tmp_path / "kibana"
        repo.mkdir()
        _git("init", cwd=repo)
        _set_origin(repo, "https://github.com/elastic/kibana.git")

        ident = detect_git_root(str(repo))
        assert ident == GitRootIdentity(
            git_root=str(repo.resolve()),
            owner="elastic",
            name="kibana",
        )

    def test_git_root_without_origin_returns_local_basename(self, tmp_path):
        repo = tmp_path / "myproject"
        repo.mkdir()
        _git("init", cwd=repo)

        ident = detect_git_root(str(repo))
        assert ident is not None
        assert ident.owner == "local"
        assert ident.name == "myproject"
        assert ident.git_root == str(repo.resolve())

    def test_subdir_walks_up_to_git_root(self, tmp_path):
        repo = tmp_path / "kibana"
        repo.mkdir()
        _git("init", cwd=repo)
        _set_origin(repo, "git@github.com:elastic/kibana.git")
        deep = repo / "src" / "plugins" / "discover"
        deep.mkdir(parents=True)

        ident = detect_git_root(str(deep))
        assert ident is not None
        assert ident.owner == "elastic"
        assert ident.name == "kibana"
        assert ident.git_root == str(repo.resolve())


# ---------------------------------------------------------------------------
# Owner/repo URL parsing
# ---------------------------------------------------------------------------


class TestParseOwnerRepo:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://github.com/elastic/kibana", ("elastic", "kibana")),
            ("https://github.com/elastic/kibana.git", ("elastic", "kibana")),
            ("https://github.com/elastic/kibana/", ("elastic", "kibana")),
            ("git@github.com:elastic/kibana.git", ("elastic", "kibana")),
            ("ssh://git@github.com/elastic/kibana.git", ("elastic", "kibana")),
            ("https://gitlab.com/group/project.git", ("group", "project")),
            ("https://bitbucket.org/team/repo", ("team", "repo")),
        ],
    )
    def test_parse(self, url, expected):
        assert _parse_owner_repo(url) == expected

    def test_returns_none_for_unparseable(self):
        assert _parse_owner_repo("") is None
        assert _parse_owner_repo("not-a-url") is None


# ---------------------------------------------------------------------------
# _resolve_repo_identity end-to-end
# ---------------------------------------------------------------------------


class TestResolveRepoIdentity:
    def test_clone_with_origin_uses_owner_repo(self, tmp_path, monkeypatch):
        repo = tmp_path / "weirdly-named-clone"
        repo.mkdir()
        _git("init", cwd=repo)
        _set_origin(repo, "https://github.com/elastic/kibana.git")

        # Force config default in case a project config near the test cwd
        # has overridden it.
        monkeypatch.setattr(
            config_module, "get",
            lambda key, default=None, repo=None:
                True if key == "git_root_identity" else default,
        )

        owner, name, git_root = _resolve_repo_identity(repo)
        assert owner == "elastic"
        assert name == "kibana"
        assert git_root == str(repo.resolve())

    def test_clone_without_origin_git_mode_uses_git_root_basename(self, tmp_path, monkeypatch):
        repo = tmp_path / "internal-tool"
        repo.mkdir()
        _git("init", cwd=repo)

        monkeypatch.setattr(
            config_module, "get",
            lambda key, default=None, repo=None:
                True if key == "git_root_identity" else default,
        )

        owner, name, git_root = _resolve_repo_identity(repo)
        assert owner == "local"
        assert name == "internal-tool"
        assert git_root == str(repo.resolve())

    def test_no_git_uses_basename_hash(self, tmp_path, monkeypatch):
        plain = tmp_path / "plain-folder"
        plain.mkdir()

        monkeypatch.setattr(
            config_module, "get",
            lambda key, default=None, repo=None:
                True if key == "git_root_identity" else default,
        )

        owner, name, git_root = _resolve_repo_identity(plain)
        assert owner == "local"
        assert name.startswith("plain-folder-")
        assert git_root == ""

    def test_knob_off_uses_basename_hash_even_with_origin(self, tmp_path, monkeypatch):
        repo = tmp_path / "kibana-clone"
        repo.mkdir()
        _git("init", cwd=repo)
        _set_origin(repo, "https://github.com/elastic/kibana.git")

        monkeypatch.setattr(
            config_module, "get",
            lambda key, default=None, repo=None:
                False if key == "git_root_identity" else default,
        )

        owner, name, git_root = _resolve_repo_identity(repo)
        assert owner == "local"
        assert name.startswith("kibana-clone-")
        assert git_root == ""


# ---------------------------------------------------------------------------
# index_folder integration
# ---------------------------------------------------------------------------


class TestIndexFolderIdentity:
    def test_knob_off_skips_retarget_git_root_probe(self, tmp_path, monkeypatch):
        repo = tmp_path / "kibana"
        repo.mkdir()
        _git("init", cwd=repo)
        _set_origin(repo, "https://github.com/elastic/kibana.git")
        sub = repo / "packages"
        sub.mkdir()
        (sub / "p.py").write_text("def p(): pass\n", encoding="utf-8")

        monkeypatch.setattr(
            config_module, "get",
            lambda key, default=None, repo=None:
                False if key == "git_root_identity" else default,
        )

        with patch("jcodemunch_mcp.storage.git_root.detect_git_root", return_value=None) as mock_detect:
            result = index_folder(
                str(sub),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
                context_providers=False,
            )

        assert result["success"] is True
        mock_detect.assert_not_called()

    def test_full_clone_index_uses_owner_repo(self, tmp_path):
        repo = tmp_path / "clone-named-anything"
        repo.mkdir()
        _git("init", cwd=repo)
        _set_origin(repo, "https://github.com/elastic/kibana.git")
        (repo / "main.py").write_text("def hello(): pass\n", encoding="utf-8")

        store = tmp_path / "store"
        result = index_folder(str(repo), use_ai_summaries=False, storage_path=str(store), identity_mode="git")
        assert result["success"] is True
        assert result["repo"] == "elastic/kibana"

    def test_git_root_field_round_trips(self, tmp_path):
        repo = tmp_path / "kibana"
        repo.mkdir()
        _git("init", cwd=repo)
        _set_origin(repo, "https://github.com/elastic/kibana.git")
        (repo / "main.py").write_text("def hello(): pass\n", encoding="utf-8")

        store_path = tmp_path / "store"
        index_folder(str(repo), use_ai_summaries=False, storage_path=str(store_path), identity_mode="git")

        store = IndexStore(base_path=str(store_path))
        loaded = store.load_index("elastic", "kibana")
        assert loaded is not None
        assert loaded.git_root == str(repo.resolve())

    def test_subdir_index_after_root_index_re_walks_under_git_root(self, tmp_path):
        # v1.96: previously v1.95.1 refused; v1.96 merges. After indexing
        # the whole repo then `index ./packages/thing`, the result is one
        # `elastic/kibana` index whose source_files include the originally
        # walked root files plus the subdir files (paths git-root-relative).
        repo = tmp_path / "kibana"
        repo.mkdir()
        _git("init", cwd=repo)
        _set_origin(repo, "https://github.com/elastic/kibana.git")
        (repo / "main.py").write_text("def hello(): pass\n", encoding="utf-8")
        sub = repo / "packages" / "thing"
        sub.mkdir(parents=True)
        (sub / "x.py").write_text("def x(): pass\n", encoding="utf-8")

        store_path = tmp_path / "store"
        first = index_folder(str(repo), use_ai_summaries=False, storage_path=str(store_path), identity_mode="git")
        assert first["success"] is True

        second = index_folder(str(sub), use_ai_summaries=False, storage_path=str(store_path), identity_mode="git")
        assert second["success"] is True
        assert second["repo"] == "elastic/kibana"

        store = IndexStore(base_path=str(store_path))
        loaded = store.load_index("elastic", "kibana")
        assert loaded is not None
        # Both files present, paths git-root-relative.
        assert "main.py" in loaded.source_files
        assert "packages/thing/x.py" in loaded.source_files

    def test_two_subdirs_of_same_clone_merge(self, tmp_path):
        # Bamieh's exact workflow: `index ./packages` then `index ./scripts`
        # from the same clone now coalesce into one `elastic/kibana` index.
        repo = tmp_path / "kibana"
        repo.mkdir()
        _git("init", cwd=repo)
        _set_origin(repo, "https://github.com/elastic/kibana.git")
        packages = repo / "packages"
        packages.mkdir()
        (packages / "p.py").write_text("def p(): pass\n", encoding="utf-8")
        scripts = repo / "scripts"
        scripts.mkdir()
        (scripts / "s.py").write_text("def s(): pass\n", encoding="utf-8")

        store_path = tmp_path / "store"
        first = index_folder(str(packages), use_ai_summaries=False, storage_path=str(store_path), identity_mode="git")
        assert first["success"] is True
        assert first["repo"] == "elastic/kibana"

        second = index_folder(str(scripts), use_ai_summaries=False, storage_path=str(store_path), identity_mode="git")
        assert second["success"] is True
        assert second["repo"] == "elastic/kibana"

        store = IndexStore(base_path=str(store_path))
        loaded = store.load_index("elastic", "kibana")
        assert loaded is not None
        # Both subdirs' files present, git-root-relative.
        assert "packages/p.py" in loaded.source_files
        assert "scripts/s.py" in loaded.source_files
        # source_roots records both walks.
        assert sorted(loaded.source_roots) == ["packages", "scripts"]

    def test_reindex_same_subdir_succeeds(self, tmp_path):
        # The refuse must NOT fire when the user re-indexes the same subdir
        # — that's the normal incremental re-index path.
        repo = tmp_path / "kibana"
        repo.mkdir()
        _git("init", cwd=repo)
        _set_origin(repo, "https://github.com/elastic/kibana.git")
        (repo / "main.py").write_text("def a(): pass\n", encoding="utf-8")

        store = tmp_path / "store"
        first = index_folder(str(repo), use_ai_summaries=False, storage_path=str(store), identity_mode="git")
        assert first["success"] is True

        # Re-index the same path: must succeed.
        second = index_folder(str(repo), use_ai_summaries=False, storage_path=str(store), identity_mode="git")
        assert second["success"] is True

    def test_subdir_under_no_origin_repo_merges_via_git_root(self, tmp_path):
        # v1.96: even without an origin remote, two subdirs of the same git
        # working tree share identity (`local/<git_root_basename>`)
        # and merge.  v1.95 gave them different per-subdir identities; that
        # behavior is no longer accessible without `git_root_identity: false`.
        repo = tmp_path / "internal-tool"
        repo.mkdir()
        _git("init", cwd=repo)  # no origin set
        a = repo / "a"
        a.mkdir()
        (a / "f.py").write_text("def a(): pass\n", encoding="utf-8")
        b = repo / "b"
        b.mkdir()
        (b / "g.py").write_text("def b(): pass\n", encoding="utf-8")

        store_path = tmp_path / "store"
        first = index_folder(str(a), use_ai_summaries=False, storage_path=str(store_path), identity_mode="git")
        assert first["success"] is True
        second = index_folder(str(b), use_ai_summaries=False, storage_path=str(store_path), identity_mode="git")
        assert second["success"] is True
        # Same identity (git_root-derived).
        assert first["repo"] == second["repo"]

        owner, name = first["repo"].split("/", 1)
        store = IndexStore(base_path=str(store_path))
        loaded = store.load_index(owner, name)
        assert loaded is not None
        assert "a/f.py" in loaded.source_files
        assert "b/g.py" in loaded.source_files

    def test_collision_detection_blocks_second_working_tree(self, tmp_path):
        repo_a = tmp_path / "kibana-a"
        repo_a.mkdir()
        _git("init", cwd=repo_a)
        _set_origin(repo_a, "https://github.com/elastic/kibana.git")
        (repo_a / "a.py").write_text("def a(): pass\n", encoding="utf-8")

        repo_b = tmp_path / "kibana-b"
        repo_b.mkdir()
        _git("init", cwd=repo_b)
        _set_origin(repo_b, "https://github.com/elastic/kibana.git")
        (repo_b / "b.py").write_text("def b(): pass\n", encoding="utf-8")

        store = tmp_path / "store"
        first = index_folder(str(repo_a), use_ai_summaries=False, storage_path=str(store), identity_mode="git")
        assert first["success"] is True

        second = index_folder(str(repo_b), use_ai_summaries=False, storage_path=str(store), identity_mode="git")
        assert second["success"] is False
        assert "already exists" in second["error"]
        assert str(repo_a.resolve()) in second["error"]


class TestSubdirMerge:
    """v1.96: merge logic for `index <subdir>` against an existing index
    of the same git working tree.  Files outside `walk_prefix` carry over;
    files inside it are replaced by the fresh walk."""

    def _set_origin(self, repo: Path, url: str) -> None:
        _set_origin(repo, url)

    def _make_repo(self, tmp_path: Path, name: str, origin: str) -> Path:
        repo = tmp_path / name
        repo.mkdir()
        _git("init", cwd=repo)
        _set_origin(repo, origin)
        return repo

    def test_reindex_subdir_replaces_only_that_prefix(self, tmp_path):
        # First walk indexes both packages and scripts via `index .`.
        # Then re-index `./packages` after editing one of its files;
        # the scripts files must remain untouched in the merged index.
        repo = self._make_repo(tmp_path, "kibana", "https://github.com/elastic/kibana.git")
        packages = repo / "packages"
        packages.mkdir()
        (packages / "p1.py").write_text("def p1(): pass\n", encoding="utf-8")
        (packages / "p2.py").write_text("def p2(): pass\n", encoding="utf-8")
        scripts = repo / "scripts"
        scripts.mkdir()
        (scripts / "s.py").write_text("def s(): pass\n", encoding="utf-8")

        store_path = tmp_path / "store"
        index_folder(str(repo), use_ai_summaries=False, storage_path=str(store_path), identity_mode="git")

        # Edit one packages file, drop the other.
        (packages / "p1.py").write_text("def p1_new(): pass\n", encoding="utf-8")
        (packages / "p2.py").unlink()

        result = index_folder(str(packages), use_ai_summaries=False, storage_path=str(store_path), identity_mode="git")
        assert result["success"] is True

        store = IndexStore(base_path=str(store_path))
        loaded = store.load_index("elastic", "kibana")
        assert loaded is not None
        # scripts/s.py carried over despite not being walked this time.
        assert "scripts/s.py" in loaded.source_files
        # packages/p1.py replaced (still present); p2.py gone (dropped from walk).
        assert "packages/p1.py" in loaded.source_files
        assert "packages/p2.py" not in loaded.source_files
        # New symbol from edited p1.py picked up.
        new_p1_symbol = next(
            (s for s in loaded.symbols if s.get("file") == "packages/p1.py"),
            None,
        )
        assert new_p1_symbol is not None
        assert new_p1_symbol.get("name") == "p1_new"

    def test_full_root_walk_after_subdir_replaces_everything(self, tmp_path):
        # Index ./packages first, then index . (the git root). The full
        # walk replaces everything (no carryover, walk_prefix == "").
        repo = self._make_repo(tmp_path, "kibana", "https://github.com/elastic/kibana.git")
        packages = repo / "packages"
        packages.mkdir()
        (packages / "p.py").write_text("def p(): pass\n", encoding="utf-8")
        (repo / "main.py").write_text("def main(): pass\n", encoding="utf-8")

        store_path = tmp_path / "store"
        index_folder(str(packages), use_ai_summaries=False, storage_path=str(store_path), identity_mode="git")
        index_folder(str(repo), use_ai_summaries=False, storage_path=str(store_path), identity_mode="git")

        store = IndexStore(base_path=str(store_path))
        loaded = store.load_index("elastic", "kibana")
        assert loaded is not None
        # Full walk picks up both files.
        assert "main.py" in loaded.source_files
        assert "packages/p.py" in loaded.source_files
        # source_roots is the full-walk marker.
        assert loaded.source_roots == [""]

    def test_repeat_full_root_walk_takes_the_incremental_no_change_path(self, tmp_path):
        """#504: a full-root RE-walk must not be treated as a subdir merge.

        The merge carries over files outside `walk_prefix`; a full-root walk has
        nothing outside it. Assigning `_merge_with_existing` there makes the
        incremental branch (gated on `_merge_with_existing is None`) unreachable,
        so every repeat index rebuilt the whole corpus instead of returning
        "No changes detected".

        `performed_incremental` is #413's field: it reported the substitution
        correctly the whole time, which is what made this visible at all.
        """
        repo = self._make_repo(tmp_path, "kibana", "https://github.com/elastic/kibana.git")
        (repo / "main.py").write_text("def main(): pass\n", encoding="utf-8")
        store_path = tmp_path / "store"

        first = index_folder(
            str(repo), use_ai_summaries=False,
            storage_path=str(store_path), identity_mode="git",
        )
        assert first["success"] is True
        # Nothing to diff against yet, so the first walk is a full build.
        assert first["performed_incremental"] is False

        second = index_folder(
            str(repo), use_ai_summaries=False,
            storage_path=str(store_path), identity_mode="git",
        )
        assert second["success"] is True
        assert second["message"] == "No changes detected"
        assert second["performed_incremental"] is True

        # Idempotent: still no-change on the third run, not just the second.
        third = index_folder(
            str(repo), use_ai_summaries=False,
            storage_path=str(store_path), identity_mode="git",
        )
        assert third["message"] == "No changes detected"
        assert third["performed_incremental"] is True

    def test_full_root_walk_after_subdir_rebuilds_once_then_goes_incremental(self, tmp_path):
        """#504's disclosed migration: one rebuild to establish `source_roots == [""]`.

        A full-corpus incremental diff cannot be layered onto a `source_roots`
        marker that is still partial, so the first full-root walk over a
        subdir-only index rebuilds. That is once per index, not once per run —
        this asserts the run after it is incremental.
        """
        repo = self._make_repo(tmp_path, "kibana", "https://github.com/elastic/kibana.git")
        packages = repo / "packages"
        packages.mkdir()
        (packages / "p.py").write_text("def p(): pass\n", encoding="utf-8")
        (repo / "main.py").write_text("def main(): pass\n", encoding="utf-8")
        store_path = tmp_path / "store"

        index_folder(
            str(packages), use_ai_summaries=False,
            storage_path=str(store_path), identity_mode="git",
        )
        store = IndexStore(base_path=str(store_path))
        assert store.load_index("elastic", "kibana").source_roots == ["packages"]

        promoted = index_folder(
            str(repo), use_ai_summaries=False,
            storage_path=str(store_path), identity_mode="git",
        )
        assert promoted["success"] is True
        # The migration run itself: a rebuild, and it says so.
        assert promoted["performed_incremental"] is False
        assert store.load_index("elastic", "kibana").source_roots == [""]

        settled = index_folder(
            str(repo), use_ai_summaries=False,
            storage_path=str(store_path), identity_mode="git",
        )
        assert settled["message"] == "No changes detected"
        assert settled["performed_incremental"] is True

    def test_disjoint_subdirs_both_present_after_merge(self, tmp_path):
        repo = self._make_repo(tmp_path, "kibana", "https://github.com/elastic/kibana.git")
        a = repo / "packages" / "alpha"
        a.mkdir(parents=True)
        (a / "ax.py").write_text("def ax(): pass\n", encoding="utf-8")
        b = repo / "scripts" / "build"
        b.mkdir(parents=True)
        (b / "bx.py").write_text("def bx(): pass\n", encoding="utf-8")

        store_path = tmp_path / "store"
        index_folder(str(a), use_ai_summaries=False, storage_path=str(store_path), identity_mode="git")
        index_folder(str(b), use_ai_summaries=False, storage_path=str(store_path), identity_mode="git")

        store = IndexStore(base_path=str(store_path))
        loaded = store.load_index("elastic", "kibana")
        assert loaded is not None
        assert "packages/alpha/ax.py" in loaded.source_files
        assert "scripts/build/bx.py" in loaded.source_files
        # source_roots tracks both walks (deeper-than-one paths preserved).
        assert sorted(loaded.source_roots) == ["packages/alpha", "scripts/build"]

    def test_v195_legacy_index_is_rebuilt(self, tmp_path):
        # Simulate a v1.95-format index where source_root is a subdir
        # (not the git root). v1.96 should detect the mismatch, log a
        # warning, and rebuild fresh against the current walk rather than
        # producing a corrupted merge.
        from jcodemunch_mcp.parser.symbols import Symbol

        repo = self._make_repo(tmp_path, "kibana", "https://github.com/elastic/kibana.git")
        scripts = repo / "scripts"
        scripts.mkdir()
        (scripts / "s.py").write_text("def s(): pass\n", encoding="utf-8")

        store_path = tmp_path / "store"
        store = IndexStore(base_path=str(store_path))
        # Hand-craft a v1.95-format manifest: source_root = the SUBDIR,
        # not the git root.  File path "p.py" is subdir-relative.
        store.save_index(
            owner="elastic", name="kibana",
            source_files=["p.py"],
            symbols=[Symbol(
                id="p.py::p#function", file="p.py", name="p",
                qualified_name="p", kind="function", language="python",
                signature="def p()", line=1, end_line=1,
                byte_offset=0, byte_length=12, content_hash="x",
            )],
            raw_files={}, languages={"python": 1},
            source_root=str(repo / "packages"),  # legacy: subdir, not git root
            git_root=str(repo),
        )

        # Re-run with v1.96 logic — should rebuild rather than merge.
        result = index_folder(str(scripts), use_ai_summaries=False, storage_path=str(store_path), identity_mode="git")
        assert result["success"] is True
        warnings_text = " ".join(result.get("warnings", []))
        assert "v1.95" in warnings_text or "subdir-relative" in warnings_text

        loaded = store.load_index("elastic", "kibana")
        assert loaded is not None
        # The legacy `p.py` is gone (rebuilt fresh under git-root paths).
        assert "p.py" not in loaded.source_files
        # The fresh walk's file is present at the git-root-relative path.
        assert "scripts/s.py" in loaded.source_files

    def test_opt_out_preserves_v194_per_subdir_indexes(self, tmp_path, monkeypatch):
        # `git_root_identity: false` keeps each subdir its own
        # local/<basename>-<hash> index — no merge, v1.94 behavior.
        from jcodemunch_mcp import config as config_module
        monkeypatch.setattr(
            config_module, "get",
            lambda key, default=None, repo=None:
                False if key == "git_root_identity" else default,
        )

        repo = self._make_repo(tmp_path, "kibana", "https://github.com/elastic/kibana.git")
        a = repo / "a"
        a.mkdir()
        (a / "f.py").write_text("def a(): pass\n", encoding="utf-8")
        b = repo / "b"
        b.mkdir()
        (b / "g.py").write_text("def b(): pass\n", encoding="utf-8")

        store_path = tmp_path / "store"
        first = index_folder(str(a), use_ai_summaries=False, storage_path=str(store_path))
        second = index_folder(str(b), use_ai_summaries=False, storage_path=str(store_path))
        assert first["success"] is True
        assert second["success"] is True
        # Different per-subdir identities (no merge).
        assert first["repo"] != second["repo"]
        assert first["repo"].startswith("local/a-")
        assert second["repo"].startswith("local/b-")
