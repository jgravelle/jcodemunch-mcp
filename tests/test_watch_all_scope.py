"""watch-all leaves temp and idle indexes unwatched, and says so.

Two settings, both on by default: `watch_all_skip_temp` skips a repo under the
system temp dir or a top-level tmp/temp folder; `watch_all_max_idle_days` (14)
skips a repo whose newest indexed file AND last indexed commit are both older.
`indexed_at` is not the idle signal: the watcher's own startup pass rewrites it.
A signal that cannot be read keeps the repo watched.

Every repo here lives under pytest's tmp_path, which is inside the system temp
dir, so the idle tests pass `skip_temp=False` to isolate the idle rule.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from jcodemunch_mcp import watch_all, watch_scope
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

_DAY = 86_400


def _index(tmp_path: Path, *, file_age_days: float, commit_age_days=None):
    """Index a one-file repo whose file (and optional commit) are that old."""
    repo = tmp_path / "repo"
    repo.mkdir()
    f = repo / "a.py"
    f.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    if commit_age_days is not None:
        when = f"@{int(time.time() - commit_age_days * _DAY)} +0000"
        env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
            cwd=repo,
            check=True,
            env=env,
        )
    stamp = time.time() - file_age_days * _DAY
    os.utime(f, (stamp, stamp))
    store_dir = tmp_path / "store"
    from jcodemunch_mcp.tools.index_folder import index_folder

    assert index_folder(
        path=str(repo), use_ai_summaries=False, storage_path=str(store_dir)
    )["success"]
    store = SQLiteIndexStore(base_path=str(store_dir))
    entry = store.list_repos()[0]
    owner, name = entry["repo"].split("/", 1)
    return str(repo.resolve()), entry, store._db_path(owner, name), str(store_dir)


def _reason(folder, entry, db, **kw):
    kw.setdefault("skip_temp", False)
    kw.setdefault("max_idle_days", 14)
    return watch_scope.skip_reason(folder, entry, db, **kw)


class TestTempFolders:
    def test_the_system_temp_dir_counts(self, tmp_path):
        assert watch_scope.is_temp_path(str(tmp_path)) is True

    @pytest.mark.parametrize("top", ["tmp", "temp", "TMP"])
    def test_a_top_level_tmp_folder_counts(self, top):
        anchor = Path(Path.cwd().anchor)
        assert (
            watch_scope.is_temp_path(str(anchor / top / "next.js" / "packages")) is True
        )

    def test_var_tmp_counts(self):
        anchor = Path(Path.cwd().anchor)
        assert watch_scope.is_temp_path(str(anchor / "var" / "tmp" / "x")) is True

    def test_a_nested_folder_named_tmp_does_not(self):
        anchor = Path(Path.cwd().anchor)
        assert watch_scope.is_temp_path(str(anchor / "work" / "tmp" / "repo")) is False

    def test_an_ordinary_folder_does_not(self):
        assert (
            watch_scope.is_temp_path(str(Path(__file__).resolve().parents[1])) is False
        )

    def test_a_temp_repo_is_skipped_only_when_the_setting_is_on(self, tmp_path):
        folder = str(tmp_path)
        assert (
            watch_scope.skip_reason(folder, {}, None, skip_temp=True, max_idle_days=0)
            == "temp"
        )
        assert (
            watch_scope.skip_reason(folder, {}, None, skip_temp=False, max_idle_days=0)
            is None
        )


class TestIdleRepos:
    def test_old_files_and_no_git_is_idle(self, tmp_path):
        folder, entry, db, _ = _index(tmp_path, file_age_days=30)
        assert not (entry.get("git_head") or "")
        assert _reason(folder, entry, db) == "idle"

    def test_a_recent_file_is_not_idle(self, tmp_path):
        folder, entry, db, _ = _index(tmp_path, file_age_days=1)
        assert _reason(folder, entry, db) is None

    def test_old_files_and_an_old_commit_is_idle(self, tmp_path):
        folder, entry, db, _ = _index(tmp_path, file_age_days=30, commit_age_days=30)
        assert entry.get("git_head")
        assert _reason(folder, entry, db) == "idle"

    def test_a_recent_commit_keeps_a_repo_with_old_files_watched(self, tmp_path):
        folder, entry, db, _ = _index(tmp_path, file_age_days=30, commit_age_days=1)
        assert _reason(folder, entry, db) is None

    def test_indexed_at_is_not_the_signal(self, tmp_path):
        """The index was written just now; its contents are a month old."""
        folder, entry, db, _ = _index(tmp_path, file_age_days=30)
        assert entry.get("indexed_at")
        assert _reason(folder, entry, db) == "idle"

    def test_zero_days_watches_everything(self, tmp_path):
        folder, entry, db, _ = _index(tmp_path, file_age_days=30)
        assert _reason(folder, entry, db, max_idle_days=0) is None

    def test_an_unreadable_index_is_unknown_and_stays_watched(self, tmp_path):
        assert _reason(str(tmp_path), {}, tmp_path / "missing.db") is None

    def test_a_commit_git_cannot_date_is_unknown_and_stays_watched(self, tmp_path):
        folder, entry, db, _ = _index(tmp_path, file_age_days=30)
        assert _reason(folder, {**entry, "git_head": "0" * 40}, db) is None


class TestSettings:
    @pytest.mark.parametrize("days", [-1, "14", 2.5, True, None])
    def test_a_bad_day_count_keeps_the_default(self, days):
        with patch.object(
            watch_scope._config,
            "get",
            side_effect=lambda k, d=None, **kw: (
                days if k == "watch_all_max_idle_days" else d
            ),
        ):
            assert watch_scope.settings() == (True, 14)

    @pytest.mark.parametrize(
        "value,expected", [(False, False), (True, True), ("no", True), (0, True)]
    )
    def test_only_an_explicit_false_turns_temp_skipping_off(self, value, expected):
        with patch.object(
            watch_scope._config,
            "get",
            side_effect=lambda k, d=None, **kw: (
                value if k == "watch_all_skip_temp" else d
            ),
        ):
            assert watch_scope.settings()[0] is expected

    def test_the_defaults_are_the_approved_ones(self):
        from jcodemunch_mcp.config import CONFIG_TYPES, DEFAULTS

        assert DEFAULTS["watch_all_skip_temp"] is True
        assert DEFAULTS["watch_all_max_idle_days"] == 14
        assert CONFIG_TYPES["watch_all_skip_temp"] is bool
        assert CONFIG_TYPES["watch_all_max_idle_days"] is int


class TestWatchAllUsesTheScope:
    def test_the_scope_skips_a_temp_index_and_keeps_it_when_told_to(self, tmp_path):
        folder, _entry, _db, store_dir = _index(tmp_path, file_age_days=1)

        kept, skipped = watch_all.watch_all_scope(
            store_dir, skip_temp=True, max_idle_days=0
        )
        assert kept == [] and skipped == {folder: "temp"}

        kept, skipped = watch_all.watch_all_scope(
            store_dir, skip_temp=False, max_idle_days=0
        )
        assert kept == [folder] and skipped == {}

    def test_every_local_index_is_still_discovered(self, tmp_path):
        """`discover_local_repos` answers "what is indexed"; only watch-all narrows it."""
        folder, _entry, _db, store_dir = _index(tmp_path, file_age_days=1)
        assert watch_all.discover_local_repos(store_dir) == [folder]

    def test_the_startup_line_names_both_reasons_and_their_keys(self):
        line = watch_all._skipped_summary({"/a": "temp", "/b": "idle", "/c": "idle"})

        assert "not watching 3" in line
        assert "1 under a temp folder (watch_all_skip_temp)" in line
        assert (
            "2 with no file or commit change" in line
            and "watch_all_max_idle_days" in line
        )
        assert "searchable" in line

    def test_nothing_skipped_prints_nothing(self):
        assert watch_all._skipped_summary({}) is None

    def test_watch_status_names_a_skipped_repo(self, tmp_path, monkeypatch):
        from jcodemunch_mcp.tools import get_watch_status as gws

        folder = str(tmp_path / "r")
        Path(folder).mkdir()
        monkeypatch.setattr(
            gws, "discover_local_repo_entries", lambda sp=None: {folder: {}}
        )
        monkeypatch.setattr(gws, "service_status", lambda: {"active": False})
        monkeypatch.setattr(gws.process_locks, "inspect", lambda *a, **k: None)
        monkeypatch.setattr(watch_scope, "settings", lambda: (True, 14))

        out = gws.get_watch_status(str(tmp_path / "store"), check_freshness=False)

        assert out["repos"][0]["watch_all_skipped"] == "temp"
