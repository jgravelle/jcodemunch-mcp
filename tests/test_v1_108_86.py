"""Tests for v1.108.86 — `watch-all` announces its steady state (#357 follow-up).

`watch-all` is a long-lived foreground daemon: it indexes, then blocks watching
for edits. The post-index silence was read as a hang twice (@zakblacki). It now
prints a one-time guidance line naming the Ctrl+C / `watch-install` options.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp import watch_all as mod


class _FakeManager:
    """Minimal WatcherManager stand-in: run() returns immediately so the
    `asyncio.wait(..., FIRST_COMPLETED)` in watch_all unblocks at once."""

    def __init__(self, **kwargs):
        self._folders: list[str] = []

    async def add_folder(self, folder):
        self._folders.append(folder)

    def list_folders(self):
        return list(self._folders)

    async def remove_folder(self, folder):
        if folder in self._folders:
            self._folders.remove(folder)

    async def run(self):
        return None

    def stop(self):
        pass


def _wire(monkeypatch, repos, captured):
    monkeypatch.setattr(mod, "WatcherManager", _FakeManager)
    monkeypatch.setattr(mod, "_is_wsl", lambda: False)
    monkeypatch.setattr(mod, "watch_all_scope", lambda storage_path=None, **kw: (list(repos), {}))
    monkeypatch.setattr(
        mod, "_watcher_output",
        lambda msg, **kw: captured.append(msg),
    )


@pytest.mark.asyncio
async def test_announces_steady_state_with_repos(monkeypatch):
    captured: list[str] = []
    _wire(monkeypatch, ["/fake/repo-a"], captured)

    await mod.watch_all(rediscover_interval_s=0.01)

    hits = [m for m in captured if "watch-all: watching" in m]
    assert len(hits) == 1, captured
    line = hits[0]
    assert "watching 1 repo(s)" in line
    assert "Ctrl+C" in line
    # Points at the background-service alternative.
    assert "watch-install" in line


@pytest.mark.asyncio
async def test_count_reflects_discovered_repos(monkeypatch):
    captured: list[str] = []
    _wire(monkeypatch, ["/fake/a", "/fake/b", "/fake/c"], captured)

    await mod.watch_all(rediscover_interval_s=0.01)

    hits = [m for m in captured if "watch-all: watching" in m]
    assert hits and "watching 3 repo(s)" in hits[0]


@pytest.mark.asyncio
async def test_no_announcement_when_nothing_indexed(monkeypatch):
    captured: list[str] = []
    _wire(monkeypatch, [], captured)

    await mod.watch_all(rediscover_interval_s=0.01)

    assert not [m for m in captured if "watch-all: watching" in m]
