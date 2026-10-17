"""A watched repo's index follows a HEAD move that changes no working-tree file (#1040, @randyhbh).

`FreshnessProbe.repo_freshness` compares the index's stored `git_head` with the
live `HEAD`. The stored value was refreshed only inside `index_folder`, which
the watcher called only on a file event, and a commit, a `git switch` to a
branch with the same tree, or a fast-forward that touches nothing indexed
writes under `.git/` only. No event, no run: the repo read `stale` and
`search_text` refused to certify absence over an index whose every file was
current, until some unrelated file changed. A pre-commit hook that touches the
tree made it worse: the reindex it provoked ran DURING the commit and stored
the parent's SHA, and the ref was written seconds later, unseen.

The watcher now reads a cheap signature of where HEAD points on every idle
wake-up, and when it has moved and settled, compares the live HEAD with the
stored one and runs the ordinary incremental index when they differ.
"""

from __future__ import annotations

import asyncio
import os
import time
import subprocess
from pathlib import Path

import pytest

from jcodemunch_mcp import watcher
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.index_folder import index_folder

_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _git(cwd, *args) -> str:
    done = subprocess.run(
        ["git", *args], cwd=str(cwd), env=_GIT_ENV, capture_output=True, text=True,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=60,
    )
    assert done.returncode == 0, (args, done.stdout, done.stderr)
    return done.stdout.strip()


def _repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "code.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    _git(root, "add", "code.py")
    _git(root, "commit", "-q", "-m", "one")
    return root


def _indexed(tmp_path):
    """A committed repo, indexed. Returns (root, storage, owner, name)."""
    root = _repo(tmp_path)
    storage = str(tmp_path / "store")
    result = index_folder(str(root), use_ai_summaries=False, storage_path=storage, context_providers=False)
    assert result["success"] is True, result
    owner, name = result["repo"].split("/", 1)
    return root, storage, owner, name


def _stored_head(storage, owner, name) -> str:
    index = IndexStore(base_path=storage).load_index(owner, name)
    return (getattr(index, "git_head", "") or "") if index is not None else ""


def _symbol_names(storage, owner, name) -> set[str]:
    index = IndexStore(base_path=storage).load_index(owner, name)
    return {s.get("name") for s in index.symbols}


# --------------------------------------------------------------------------- the signature


def test_the_signature_moves_with_a_commit(tmp_path):
    root = _repo(tmp_path)
    before = watcher._git_head_signature(str(root))
    assert before is not None
    _git(root, "commit", "-q", "--allow-empty", "-m", "two")
    assert watcher._git_head_signature(str(root)) != before


def test_the_signature_moves_with_a_switch(tmp_path):
    root = _repo(tmp_path)
    before = watcher._git_head_signature(str(root))
    _git(root, "switch", "-q", "-c", "other")
    assert watcher._git_head_signature(str(root)) != before


def test_the_signature_moves_with_a_commit_after_refs_are_packed(tmp_path):
    root = _repo(tmp_path)
    _git(root, "pack-refs", "--all")
    before = watcher._git_head_signature(str(root))
    assert before is not None
    _git(root, "commit", "-q", "--allow-empty", "-m", "two")
    assert watcher._git_head_signature(str(root)) != before


def test_the_signature_moves_when_a_detached_head_moves(tmp_path):
    root = _repo(tmp_path)
    _git(root, "commit", "-q", "--allow-empty", "-m", "two")
    _git(root, "switch", "-q", "--detach", "HEAD")
    before = watcher._git_head_signature(str(root))
    _git(root, "switch", "-q", "--detach", "HEAD~1")
    assert watcher._git_head_signature(str(root)) != before


def test_a_linked_worktree_has_its_own_signature(tmp_path):
    """A worktree's `.git` is a FILE naming its git dir; its branch ref is in the common dir."""
    root = _repo(tmp_path)
    tree = tmp_path / "tree"
    _git(root, "worktree", "add", "-q", "-b", "side", str(tree))
    main_before = watcher._git_head_signature(str(root))
    tree_before = watcher._git_head_signature(str(tree))
    assert tree_before is not None
    _git(tree, "commit", "-q", "--allow-empty", "-m", "side")
    assert watcher._git_head_signature(str(tree)) != tree_before
    assert watcher._git_head_signature(str(root)) == main_before


def test_a_watched_subdirectory_reads_the_repository_above_it(tmp_path):
    root = _repo(tmp_path)
    sub = root / "pkg"
    sub.mkdir()
    before = watcher._git_head_signature(str(sub))
    assert before is not None
    _git(root, "commit", "-q", "--allow-empty", "-m", "two")
    assert watcher._git_head_signature(str(sub)) != before


def test_the_signature_holds_still_while_only_the_working_tree_changes(tmp_path):
    root = _repo(tmp_path)
    before = watcher._git_head_signature(str(root))
    (root / "code.py").write_text("def second():\n    return 2\n", encoding="utf-8")
    (root / "new.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "new.py")  # the git index is not HEAD
    assert watcher._git_head_signature(str(root)) == before


def test_a_folder_under_no_repository_has_no_signature(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert watcher._git_head_signature(str(plain)) is None


def test_a_ref_that_lives_only_in_packed_refs_is_seen_when_that_file_changes(tmp_path):
    """No loose ref to read: the signature has to move with `packed-refs` itself."""
    root = _repo(tmp_path)
    first = _git(root, "rev-parse", "HEAD")
    _git(root, "commit", "-q", "--allow-empty", "-m", "two")
    second = _git(root, "rev-parse", "HEAD")
    _git(root, "pack-refs", "--all")
    packed = root / ".git" / "packed-refs"
    assert not (root / ".git" / "refs" / "heads" / "main").exists()
    before = watcher._git_head_signature(str(root))
    text = packed.read_text(encoding="utf-8")
    assert second in text
    packed.write_text(text.replace(second, first), encoding="utf-8")  # what `git reset` writes loose, written packed
    stamp = packed.stat().st_mtime + 5
    os.utime(packed, (stamp, stamp))
    assert watcher._git_head_signature(str(root)) != before


@pytest.mark.parametrize("ref", [
    "refs/../../outside.txt",
    "refs" + chr(92) + ".." + chr(92) + ".." + chr(92) + "outside.txt",
    "../outside.txt",
    "/outside.txt",
    "refs/heads/./../../../outside.txt",
])
def test_a_ref_name_that_leaves_the_git_directory_is_not_followed(tmp_path, ref):
    """HEAD is a file anyone can write. A name that climbs out of `.git` reads nothing."""
    root = _repo(tmp_path)
    (root / "outside.txt").write_text("SECRET-OUTSIDE", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("SECRET-OUTSIDE", encoding="utf-8")
    (root / ".git" / "HEAD").write_text("ref: " + ref + "\n", encoding="utf-8")
    signature = watcher._git_head_signature(str(root))
    assert signature is not None
    assert "SECRET-OUTSIDE" not in repr(signature)
    assert signature[1] is None and signature[2] is None


def test_an_absolute_ref_name_is_not_followed(tmp_path):
    root = _repo(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET-OUTSIDE", encoding="utf-8")
    (root / ".git" / "HEAD").write_text("ref: " + str(outside) + "\n", encoding="utf-8")
    assert "SECRET-OUTSIDE" not in repr(watcher._git_head_signature(str(root)))


def test_an_absolute_ref_name_written_with_forward_slashes_is_not_followed(tmp_path):
    """`C:/x/outside.txt` on Windows (the colon), `/x/outside.txt` elsewhere (the leading slash)."""
    root = _repo(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET-OUTSIDE", encoding="utf-8")
    (root / ".git" / "HEAD").write_text("ref: " + outside.as_posix() + "\n", encoding="utf-8")
    assert "SECRET-OUTSIDE" not in repr(watcher._git_head_signature(str(root)))


def test_no_read_under_the_git_directory_is_longer_than_its_bound(tmp_path):
    """SECURITY.md states two bounds: 256 bytes of HEAD and a ref, 4096 of a pointer file."""
    assert (watcher._GIT_REF_MAX_BYTES, watcher._GIT_POINTER_MAX_BYTES) == (256, 4096)
    root = _repo(tmp_path)
    ref = root / ".git" / "refs" / "heads" / "main"
    ref.write_text("a" * 300 + "TAIL-OF-A-LONG-REF", encoding="utf-8")
    signature = watcher._git_head_signature(str(root))
    assert signature[1] == "a" * 256

    # A `.git` pointer FILE, written by hand (git hides the one it writes on Windows).
    linked = tmp_path / "linked"
    linked.mkdir()
    pointer = linked / ".git"
    text = "gitdir: " + str(root / ".git")
    pointer.write_text(text + "\n", encoding="utf-8")
    assert watcher._git_dirs(str(linked)) == (root / ".git", root / ".git")
    pointer.write_text(" " * 5000 + text, encoding="utf-8")
    assert watcher._git_dirs(str(linked)) is None  # nothing past the bound is read to find `gitdir:`
    pointer.write_text(text + "\n", encoding="utf-8")
    (root / ".git" / "commondir").write_text(" " * 5000 + "elsewhere", encoding="utf-8")
    assert watcher._git_dirs(str(linked)) == (root / ".git", root / ".git")  # a commondir past the bound names nothing


def test_an_ordinary_branch_name_with_slashes_and_dots_is_followed(tmp_path):
    root = _repo(tmp_path)
    _git(root, "switch", "-q", "-c", "feature/v1.2-x")
    signature = watcher._git_head_signature(str(root))
    assert signature[1] == _git(root, "rev-parse", "HEAD")


# --------------------------------------------------------------------------- the detector


def test_a_read_that_fails_clears_the_candidate():
    """A move seen once, then an unreadable wake-up: the move has to be seen twice again."""
    detector = watcher._HeadMoveDetector()
    detector.observe(("a",))
    detector.observe(("a",))
    assert detector.observe(("b",)) is False
    assert detector.observe(None) is False
    assert detector.observe(("b",)) is False
    assert detector.observe(("b",)) is True


def test_a_move_is_reported_once_it_has_settled():
    """One idle wake-up sees the move; the next, seeing the same thing, reports it."""
    detector = watcher._HeadMoveDetector()
    assert detector.observe(("a",)) is False  # first sight: wait for it to settle
    assert detector.observe(("a",)) is True
    assert detector.observe(("a",)) is False  # reported once
    assert detector.observe(("b",)) is False
    assert detector.observe(("b",)) is True


def test_a_head_that_keeps_moving_is_not_reported_until_it_stops():
    """A rebase moves HEAD once per commit; one reconciliation follows the last."""
    detector = watcher._HeadMoveDetector()
    detector.observe(("a",))
    detector.observe(("a",))
    assert [detector.observe((n,)) for n in ("b", "c", "d", "e")] == [False, False, False, False]
    assert detector.observe(("e",)) is True


def test_a_head_that_moves_and_comes_back_is_not_reported():
    detector = watcher._HeadMoveDetector()
    detector.observe(("a",))
    detector.observe(("a",))
    assert detector.observe(("b",)) is False
    assert detector.observe(("a",)) is False
    assert detector.observe(("a",)) is False


def test_no_signature_is_never_a_move():
    """UNKNOWN is not a change: a folder under no repository reconciles nothing."""
    detector = watcher._HeadMoveDetector()
    assert [detector.observe(None) for _ in range(4)] == [False, False, False, False]
    detector.observe(("a",))
    assert detector.observe(("a",)) is True
    assert detector.observe(None) is False  # the read failed for one wake-up
    assert detector.observe(None) is False
    assert detector.observe(("a",)) is False  # and nothing moved meanwhile


# --------------------------------------------------------------------------- the retry


def test_a_failed_reconciliation_is_due_again_once_and_no_sooner_than_the_delay(monkeypatch):
    """The delay is the whole bound on what a persistent failure costs: an index per wake-up without it."""
    monkeypatch.setattr(watcher, "_HEAD_RECONCILE_RETRY_SECONDS", 60.0)
    retry = watcher._HeadRetry()
    assert retry.due(1000.0) is False  # nothing failed
    retry.schedule(1000.0)
    assert [retry.due(t) for t in (1000.0, 1001.0, 1030.0, 1059.9)] == [False, False, False, False]
    assert retry.due(1060.0) is True
    assert [retry.due(t) for t in (1060.0, 1061.0, 5000.0)] == [False, False, False]  # once per failure
    retry.schedule(5000.0)
    assert retry.due(5059.0) is False
    assert retry.due(5061.0) is True


def test_the_retry_delay_is_at_least_a_minute():
    """A reconciliation is a walk of the tree; a failing one is not repeated faster than this."""
    assert watcher._HEAD_RECONCILE_RETRY_SECONDS >= 60.0


# --------------------------------------------------------------------------- the reconciliation


def _reconcile(root, storage, owner, name, **overrides):
    options = dict(
        folder_path=str(root), repo_id=f"{owner}/{name}", store=IndexStore(base_path=storage),
        use_ai_summaries=False, context_providers=False, storage_path=storage,
        extra_ignore_patterns=None, follow_symlinks=False, on_reindex=None,
        quiet=True, log_file_handle=None, build_hash_cache=lambda: None,
    )
    options.update(overrides)
    return watcher._reconcile_head_move(**options)


async def test_a_head_only_move_refreshes_the_stored_head(tmp_path):
    root, storage, owner, name = _indexed(tmp_path)
    assert _stored_head(storage, owner, name) == _git(root, "rev-parse", "HEAD")
    _git(root, "commit", "-q", "--allow-empty", "-m", "two")
    head = _git(root, "rev-parse", "HEAD")
    assert _stored_head(storage, owner, name) != head
    assert await _reconcile(root, storage, owner, name) == watcher._HEAD_RECONCILED
    assert _stored_head(storage, owner, name) == head


async def test_a_move_whose_file_events_were_lost_is_reindexed(tmp_path):
    """The reconciliation is the ordinary incremental index, so it reads the tree.

    Refreshing the stored SHA alone would certify an index as current at the
    new HEAD while a file the commit changed still held its old symbols.
    """
    root, storage, owner, name = _indexed(tmp_path)
    (root / "code.py").write_text("def second():\n    return 2\n", encoding="utf-8")
    _git(root, "commit", "-q", "-am", "two")
    assert await _reconcile(root, storage, owner, name) == watcher._HEAD_RECONCILED
    assert _stored_head(storage, owner, name) == _git(root, "rev-parse", "HEAD")
    names = _symbol_names(storage, owner, name)
    assert "second" in names and "first" not in names


async def test_a_signature_change_that_leaves_head_where_it_was_runs_no_index(tmp_path, monkeypatch):
    """`git switch -c other` moves the signature and not the commit. Nothing to do."""
    root, storage, owner, name = _indexed(tmp_path)
    _git(root, "switch", "-q", "-c", "other")
    calls = []
    monkeypatch.setattr(watcher, "index_folder", lambda **kwargs: calls.append(kwargs) or {"success": True})
    assert await _reconcile(root, storage, owner, name) == watcher._HEAD_UNCHANGED
    assert calls == []


async def test_a_folder_under_no_repository_runs_no_index(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "code.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    result = index_folder(str(plain), use_ai_summaries=False, storage_path=storage, context_providers=False)
    owner, name = result["repo"].split("/", 1)
    calls = []
    monkeypatch.setattr(watcher, "index_folder", lambda **kwargs: calls.append(kwargs) or {"success": True})
    assert await _reconcile(plain, storage, owner, name) == watcher._HEAD_UNCHANGED
    assert calls == []


def _recording_index(result=None, raises=None):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return result if result is not None else {"success": True, "message": "No changes detected"}

    return calls, fake


async def _moved(tmp_path):
    root, storage, owner, name = _indexed(tmp_path)
    _git(root, "commit", "-q", "--allow-empty", "-m", "two")
    return root, storage, owner, name


async def test_the_index_run_is_the_watchs_own_incremental_index(tmp_path, monkeypatch):
    """The watch's options, passed through: never a second set of them.

    A reconciliation that turned AI summaries on would bill a summarizer once
    per commit; one that dropped the ignore patterns would index what the
    watch was told to leave out.
    """
    root, storage, owner, name = await _moved(tmp_path)
    calls, fake = _recording_index()
    monkeypatch.setattr(watcher, "index_folder", fake)
    outcome = await _reconcile(
        root, storage, owner, name,
        use_ai_summaries=False, context_providers=False, extra_ignore_patterns=["gen/"], follow_symlinks=True,
    )
    assert outcome == watcher._HEAD_RECONCILED
    assert calls == [{
        "path": str(root), "use_ai_summaries": False, "context_providers": False, "storage_path": storage,
        "extra_ignore_patterns": ["gen/"], "follow_symlinks": True, "incremental": True,
    }]
    calls.clear()
    await _reconcile(root, storage, owner, name, use_ai_summaries=True, context_providers=True)
    assert calls[0]["use_ai_summaries"] is True and calls[0]["context_providers"] is True


async def test_a_reconciliation_records_its_start_and_outcome_and_rebuilds_the_hash_cache(tmp_path, monkeypatch):
    root, storage, owner, name = await _moved(tmp_path)
    events = []
    monkeypatch.setattr(watcher, "mark_reindex_start", lambda repo: events.append(("start", repo)))
    monkeypatch.setattr(watcher, "mark_reindex_done", lambda repo, result: events.append(("done", repo)))
    monkeypatch.setattr(watcher, "mark_reindex_failed", lambda repo, error, **kw: events.append(("failed", repo)))
    _, fake = _recording_index()
    monkeypatch.setattr(watcher, "index_folder", fake)
    outcome = await _reconcile(root, storage, owner, name, build_hash_cache=lambda: events.append(("cache", "")))
    repo = f"{owner}/{name}"
    assert outcome == watcher._HEAD_RECONCILED
    assert events == [("start", repo), ("cache", ""), ("done", repo)]


async def test_activity_is_counted_only_when_the_reconciliation_changed_something(tmp_path, monkeypatch):
    """`on_reindex` feeds the idle timeout: a commit that changed no file is not activity."""
    root, storage, owner, name = await _moved(tmp_path)
    counted = []
    _, fake = _recording_index({"success": True, "message": "No changes detected"})
    monkeypatch.setattr(watcher, "index_folder", fake)
    await _reconcile(root, storage, owner, name, on_reindex=lambda: counted.append(1))
    assert counted == []
    _, fake = _recording_index({"success": True, "changed": 1})
    monkeypatch.setattr(watcher, "index_folder", fake)
    await _reconcile(root, storage, owner, name, on_reindex=lambda: counted.append(1))
    assert counted == [1]


@pytest.mark.parametrize("how", ["returns a failure", "raises"])
async def test_an_index_that_fails_is_recorded_and_does_not_stop_the_watch(tmp_path, monkeypatch, how):
    root, storage, owner, name = await _moved(tmp_path)
    events = []
    monkeypatch.setattr(watcher, "mark_reindex_start", lambda repo: events.append("start"))
    monkeypatch.setattr(watcher, "mark_reindex_done", lambda repo, result: events.append("done"))
    monkeypatch.setattr(watcher, "mark_reindex_failed", lambda repo, error, **kw: events.append(("failed", error)))
    if how == "raises":
        _, fake = _recording_index(raises=RuntimeError("store is locked"))
    else:
        _, fake = _recording_index({"success": False, "error": "store is locked"})
    monkeypatch.setattr(watcher, "index_folder", fake)
    cache = []
    outcome = await _reconcile(root, storage, owner, name, build_hash_cache=lambda: cache.append(1))
    assert outcome == watcher._HEAD_FAILED
    assert events == ["start", ("failed", "store is locked")]
    assert cache == []


# --------------------------------------------------------------------------- the watcher, end to end


async def _until(condition, task, what, seconds=30.0):
    async def observe():
        while not condition():
            if task.done():
                task.result()
                pytest.fail("the watcher stopped")
            await asyncio.sleep(0.1)
    try:
        await asyncio.wait_for(observe(), seconds)
    except asyncio.TimeoutError as error:
        raise AssertionError(f"timed out waiting for {what}") from error


async def _stop(task):
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_the_watcher_follows_a_commit_that_changes_no_file(tmp_path):
    """The report: after `git commit` the watched repo stays `stale` until a file changes."""
    pytest.importorskip("watchfiles")
    root, storage, owner, name = _indexed(tmp_path)
    task = asyncio.create_task(watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    ))
    try:
        _git(root, "commit", "-q", "--allow-empty", "-m", "two")
        head = _git(root, "rev-parse", "HEAD")
        await _until(lambda: _stored_head(storage, owner, name) == head, task, "the stored head to follow the commit")
        _git(root, "commit", "-q", "--allow-empty", "-m", "three")
        head = _git(root, "rev-parse", "HEAD")
        await _until(lambda: _stored_head(storage, owner, name) == head, task, "the stored head to follow a second commit")
    finally:
        await _stop(task)


async def test_the_watcher_follows_a_commit_made_before_it_started(tmp_path):
    """HEAD moved between the caller's index and the watcher's first wake-up."""
    pytest.importorskip("watchfiles")
    root, storage, owner, name = _indexed(tmp_path)
    _git(root, "commit", "-q", "--allow-empty", "-m", "two")
    head = _git(root, "rev-parse", "HEAD")
    task = asyncio.create_task(watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    ))
    try:
        await _until(lambda: _stored_head(storage, owner, name) == head, task, "the stored head to reach HEAD")
    finally:
        await _stop(task)


async def test_the_watcher_ends_at_head_after_a_commit_that_changes_a_file(tmp_path):
    """The reported race: the file event's reindex can run before the ref is written."""
    pytest.importorskip("watchfiles")
    root, storage, owner, name = _indexed(tmp_path)
    task = asyncio.create_task(watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    ))
    try:
        (root / "code.py").write_text("def second():\n    return 2\n", encoding="utf-8")
        await _until(lambda: "second" in _symbol_names(storage, owner, name), task, "the edit to be indexed")
        _git(root, "commit", "-q", "-am", "two")  # the ref is written after the reindex
        head = _git(root, "rev-parse", "HEAD")
        await _until(lambda: _stored_head(storage, owner, name) == head, task, "the stored head to follow the commit")
        assert "second" in _symbol_names(storage, owner, name)
    finally:
        await _stop(task)


async def test_the_watcher_tries_a_failed_reconciliation_again_once_and_then_leaves_it(tmp_path, monkeypatch):
    """A move is reported once, so the loop retries a failure itself, after the delay, one time.

    HEAD never moves here: the only reconciliation the detector asks for is
    the one for the first signature it sees. That one is made to report a
    failure, so a SECOND call can only be the retry. It runs for real, finds
    the heads equal and schedules nothing, so there is no third: a retry that
    came due on every wake-up, or was scheduled after every outcome, would
    make one within the wait below.

    An earlier draft failed the index instead and waited for the stored head
    to reach a new commit. It passed against the watcher's own first index run
    storing that head while the commit was made (CI, ubuntu 3.10): the
    interleaving was not pinned.
    """
    pytest.importorskip("watchfiles")
    root, storage, owner, name = _indexed(tmp_path)
    monkeypatch.setattr(watcher, "_HEAD_RECONCILE_RETRY_SECONDS", 2.5)
    real = watcher._reconcile_head_move
    outcomes = []
    called_at = []

    async def first_one_fails(**kwargs):
        called_at.append(time.monotonic())
        if not outcomes:
            outcomes.append(watcher._HEAD_FAILED)
            return watcher._HEAD_FAILED
        outcome = await real(**kwargs)
        outcomes.append(outcome)
        return outcome

    monkeypatch.setattr(watcher, "_reconcile_head_move", first_one_fails)
    task = asyncio.create_task(watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    ))
    try:
        await _until(lambda: len(outcomes) >= 2, task, "the retry of the failed reconciliation")
        assert outcomes[:2] == [watcher._HEAD_FAILED, watcher._HEAD_UNCHANGED]
        # No sooner than the delay. Idle wake-ups are a second apart, so a retry that
        # ignored the delay would come about one second after the failure.
        assert called_at[1] - called_at[0] >= 2.5
        await asyncio.sleep(6.0)  # several idle wake-ups and more than two retry delays
        assert outcomes == [watcher._HEAD_FAILED, watcher._HEAD_UNCHANGED]
    finally:
        await _stop(task)
