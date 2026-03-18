"""Tests for watch-claude v2: hook-event, manifest, git worktree parsing, integration."""

import asyncio
import hashlib
import json
import textwrap
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from jcodemunch_mcp.hook_event import handle_hook_event, read_manifest
from jcodemunch_mcp.server import main
from jcodemunch_mcp.watcher import (
    _local_repo_id,
    parse_git_worktrees,
    watch_claude_worktrees,
)


# ---------------------------------------------------------------------------
# hook-event tests
# ---------------------------------------------------------------------------


class TestHookEvent:
    def test_create_appends_to_manifest(self, tmp_path):
        manifest = tmp_path / "manifest.jsonl"
        payload = json.dumps({"worktreePath": "/tmp/my-wt"})
        with patch("sys.stdin", StringIO(payload)):
            handle_hook_event("create", manifest_path=manifest)
        lines = manifest.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "create"
        assert entry["path"] == str(Path("/tmp/my-wt").resolve())
        assert "ts" in entry

    def test_remove_appends_to_manifest(self, tmp_path):
        manifest = tmp_path / "manifest.jsonl"
        payload = json.dumps({"worktreePath": "/tmp/my-wt"})
        with patch("sys.stdin", StringIO(payload)):
            handle_hook_event("create", manifest_path=manifest)
        with patch("sys.stdin", StringIO(payload)):
            handle_hook_event("remove", manifest_path=manifest)
        lines = manifest.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1])["event"] == "remove"

    def test_creates_manifest_if_missing(self, tmp_path):
        manifest = tmp_path / "subdir" / "manifest.jsonl"
        payload = json.dumps({"worktreePath": "/tmp/wt"})
        with patch("sys.stdin", StringIO(payload)):
            handle_hook_event("create", manifest_path=manifest)
        assert manifest.is_file()

    def test_reads_worktree_path_snake_case(self, tmp_path):
        """Also accept worktree_path (snake_case) for robustness."""
        manifest = tmp_path / "manifest.jsonl"
        payload = json.dumps({"worktree_path": "/tmp/wt2"})
        with patch("sys.stdin", StringIO(payload)):
            handle_hook_event("create", manifest_path=manifest)
        entry = json.loads(manifest.read_text().strip())
        assert entry["path"] == str(Path("/tmp/wt2").resolve())

    def test_exits_on_missing_path(self, tmp_path):
        manifest = tmp_path / "manifest.jsonl"
        payload = json.dumps({"unrelated": "data"})
        with patch("sys.stdin", StringIO(payload)):
            with pytest.raises(SystemExit):
                handle_hook_event("create", manifest_path=manifest)


# ---------------------------------------------------------------------------
# Manifest parsing tests
# ---------------------------------------------------------------------------


class TestReadManifest:
    def test_empty_file(self, tmp_path):
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text("")
        assert read_manifest(manifest) == set()

    def test_missing_file(self, tmp_path):
        assert read_manifest(tmp_path / "nope.jsonl") == set()

    def test_create_then_remove(self, tmp_path):
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps({"event": "create", "path": "/a"}) + "\n"
            + json.dumps({"event": "remove", "path": "/a"}) + "\n"
        )
        assert read_manifest(manifest) == set()

    def test_multiple_active(self, tmp_path):
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps({"event": "create", "path": "/a"}) + "\n"
            + json.dumps({"event": "create", "path": "/b"}) + "\n"
            + json.dumps({"event": "create", "path": "/c"}) + "\n"
        )
        assert read_manifest(manifest) == {"/a", "/b", "/c"}

    def test_skips_malformed_lines(self, tmp_path):
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            "not json\n"
            + json.dumps({"event": "create", "path": "/ok"}) + "\n"
            + "\n"
        )
        assert read_manifest(manifest) == {"/ok"}


# ---------------------------------------------------------------------------
# Git worktree parsing tests
# ---------------------------------------------------------------------------
PORCELAIN_OUTPUT = textwrap.dedent("""\
    worktree /home/user/project
    HEAD abc123
    branch refs/heads/main

    worktree /home/user/.claude-worktrees/project/dreamy-fox
    HEAD def456
    branch refs/heads/agent/dreamy-fox

    worktree /home/user/.claude/worktrees/feature-auth
    HEAD 789abc
    branch refs/heads/worktree-feature-auth

    worktree /home/user/.claude/worktrees/manual-branch
    HEAD aaa111
    branch refs/heads/feature/manual

    worktree /home/user/.claude-worktrees/project/old-session
    HEAD bbb222
    branch refs/heads/agent/old-session
    prunable gitdir file points to non-existent location

    """)


class TestParseGitWorktrees:
    def _run_with_output(self, stdout):
        import subprocess

        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=stdout, stderr=""
        )
        with patch("jcodemunch_mcp.watcher.subprocess.run", return_value=fake_result):
            return parse_git_worktrees("/fake/repo")

    def test_filters_by_branch(self):
        result = self._run_with_output(PORCELAIN_OUTPUT)
        # Should include agent/* and worktree-* but not main or feature/manual
        assert "/home/user/.claude-worktrees/project/dreamy-fox" in result
        assert "/home/user/.claude/worktrees/feature-auth" in result
        assert "/home/user/.claude/worktrees/manual-branch" not in result

    def test_skips_main_worktree(self):
        result = self._run_with_output(PORCELAIN_OUTPUT)
        assert "/home/user/project" not in result

    def test_skips_prunable(self):
        result = self._run_with_output(PORCELAIN_OUTPUT)
        assert "/home/user/.claude-worktrees/project/old-session" not in result

    def test_handles_git_failure(self):
        import subprocess

        fake_result = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr="not a git repo"
        )
        with patch("jcodemunch_mcp.watcher.subprocess.run", return_value=fake_result):
            assert parse_git_worktrees("/fake/repo") == set()

    def test_handles_empty_output(self):
        result = self._run_with_output("")
        assert result == set()


# ---------------------------------------------------------------------------
# _local_repo_id
# ---------------------------------------------------------------------------


class TestLocalRepoId:
    def test_matches_index_folder_convention(self, tmp_path):
        folder = tmp_path / "my-worktree"
        folder.mkdir()
        repo_id = _local_repo_id(str(folder))
        resolved = str(folder.resolve())
        digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:8]
        assert repo_id == f"local/my-worktree-{digest}"


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCLI:
    def test_watch_claude_help(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["watch-claude", "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "--repos" in out

    def test_hook_event_help(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["hook-event", "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "create" in out
        assert "remove" in out


# ---------------------------------------------------------------------------
# Integration tests (mocked _watch_single)
# ---------------------------------------------------------------------------


class TestWatchClaudeIntegration:
    @pytest.mark.asyncio
    async def test_manifest_mode_starts_existing_worktrees(self, tmp_path):
        """Worktrees listed in manifest should be watched on startup."""
        wt = tmp_path / "wt-1"
        wt.mkdir()
        (wt / "main.py").write_text("x = 1\n")

        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps({"event": "create", "path": str(wt)}) + "\n"
        )

        started = []

        async def fake_watch_single(folder_path, **kwargs):
            started.append(folder_path)
            await asyncio.Event().wait()

        with (
            patch("jcodemunch_mcp.watcher._watch_single", side_effect=fake_watch_single),
            patch("jcodemunch_mcp.watcher.DEFAULT_MANIFEST_PATH", manifest),
        ):
            task = asyncio.create_task(
                watch_claude_worktrees(use_ai_summaries=False)
            )
            await asyncio.sleep(0.3)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(started) == 1
        assert started[0] == str(wt)

    @pytest.mark.asyncio
    async def test_manifest_mode_reacts_to_new_event(self, tmp_path):
        """A create event appended to the manifest should trigger a new watcher."""
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text("")  # empty initially

        wt = tmp_path / "new-wt"
        wt.mkdir()

        started = []

        async def fake_watch_single(folder_path, **kwargs):
            started.append(folder_path)
            await asyncio.Event().wait()

        with (
            patch("jcodemunch_mcp.watcher._watch_single", side_effect=fake_watch_single),
            patch("jcodemunch_mcp.watcher.DEFAULT_MANIFEST_PATH", manifest),
        ):
            task = asyncio.create_task(
                watch_claude_worktrees(use_ai_summaries=False)
            )
            await asyncio.sleep(0.3)
            assert len(started) == 0

            # Simulate hook appending a create event
            with open(manifest, "a") as f:
                f.write(json.dumps({"event": "create", "path": str(wt)}) + "\n")

            # Wait for watchfiles to pick it up
            await asyncio.sleep(1.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(started) == 1
        assert started[0] == str(wt)

    @pytest.mark.asyncio
    async def test_repos_mode_discovers_worktrees(self, tmp_path):
        """--repos mode should discover worktrees via git worktree list."""
        started = []

        async def fake_watch_single(folder_path, **kwargs):
            started.append(folder_path)
            await asyncio.Event().wait()

        wt_path = str(tmp_path / "wt-from-git")
        (tmp_path / "wt-from-git").mkdir()

        def fake_parse(repo_path):
            return {wt_path}

        manifest = tmp_path / "no-manifest.jsonl"  # nonexistent

        with (
            patch("jcodemunch_mcp.watcher._watch_single", side_effect=fake_watch_single),
            patch("jcodemunch_mcp.watcher.parse_git_worktrees", side_effect=fake_parse),
            patch("jcodemunch_mcp.watcher.DEFAULT_MANIFEST_PATH", manifest),
        ):
            task = asyncio.create_task(
                watch_claude_worktrees(
                    repos=["/fake/repo"],
                    poll_interval=0.1,
                    use_ai_summaries=False,
                )
            )
            await asyncio.sleep(0.3)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(started) == 1
        assert started[0] == wt_path

    @pytest.mark.asyncio
    async def test_repos_mode_cleans_up_removed(self, tmp_path):
        """When a worktree disappears from git, it should be stopped and cache invalidated."""
        started = []
        invalidated = []

        async def fake_watch_single(folder_path, **kwargs):
            started.append(folder_path)
            await asyncio.Event().wait()

        def fake_invalidate(repo, storage_path=None):
            invalidated.append(repo)
            return {"success": True}

        wt_path = str(tmp_path / "wt-gone")
        (tmp_path / "wt-gone").mkdir()

        call_count = 0

        def fake_parse(repo_path):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return {wt_path}
            return set()  # worktree gone

        manifest = tmp_path / "no-manifest.jsonl"

        with (
            patch("jcodemunch_mcp.watcher._watch_single", side_effect=fake_watch_single),
            patch("jcodemunch_mcp.watcher.parse_git_worktrees", side_effect=fake_parse),
            patch("jcodemunch_mcp.watcher.invalidate_cache", side_effect=fake_invalidate),
            patch("jcodemunch_mcp.watcher.DEFAULT_MANIFEST_PATH", manifest),
        ):
            task = asyncio.create_task(
                watch_claude_worktrees(
                    repos=["/fake/repo"],
                    poll_interval=0.1,
                    use_ai_summaries=False,
                )
            )
            await asyncio.sleep(0.8)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(started) == 1
        assert len(invalidated) == 1
        assert invalidated[0].startswith("local/wt-gone-")
