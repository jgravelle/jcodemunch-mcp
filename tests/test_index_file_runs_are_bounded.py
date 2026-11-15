"""At most `MAX_CONCURRENT` `index-file` runs work at once on a machine.

The PostToolUse hook starts a detached `index-file` for every Edit or Write and
nothing counted them: eight edits in a row held eight runs and 800 MB at once on
a 50,000-symbol index, and a machine running many agent sessions had no upper
limit (2026-11-15, after an overnight run froze the development laptop with
python.exe processes). The slots are `process_locks` locks, so the holders here
are real child processes and a dead holder frees its slot.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from unittest import mock

import pytest

from jcodemunch_mcp.storage import index_file_slots as slots
from jcodemunch_mcp.storage import process_locks


def _hold_in_child(storage: str, target: str) -> subprocess.Popen:
    """A separate process that holds one slot until it is ended."""
    code = textwrap.dedent(
        f"""
        import sys, time
        from jcodemunch_mcp.storage import process_locks
        assert process_locks.acquire({slots.SCOPE!r}, {target!r}, {storage!r})
        print("held", flush=True)
        time.sleep(120)
        """
    )
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "held"
    return child


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def every_slot_held(storage):
    children = [_hold_in_child(storage, t) for t in slots._targets()]
    yield children
    for c in children:
        c.kill()
        c.wait()


def test_no_slot_is_free_while_each_is_held_by_a_live_process(storage, every_slot_held):
    assert all(process_locks.inspect(slots.SCOPE, t, storage) is not None for t in slots._targets())
    assert slots.claim(storage) is None


def test_a_slot_whose_holder_died_is_free_again(storage, every_slot_held):
    every_slot_held[0].kill()
    every_slot_held[0].wait()
    assert process_locks.inspect(slots.SCOPE, slots._targets()[0], storage) is None
    got = slots.claim(storage)
    assert got == slots._targets()[0]
    slots.release(got, storage)


def test_index_file_does_no_work_when_every_slot_is_held(storage, every_slot_held, tmp_path, capsys):
    from jcodemunch_mcp import server

    target = tmp_path / "a.py"
    target.write_text("def f():\n    return 1\n")
    with mock.patch("jcodemunch_mcp.tools.index_file.index_file") as work:
        server.main(["index-file", str(target)])
    work.assert_not_called()
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] == "busy"
    assert out["max_concurrent"] == slots.MAX_CONCURRENT


def test_index_file_works_and_gives_its_slot_back(storage, tmp_path, capsys):
    from jcodemunch_mcp import server

    target = tmp_path / "a.py"
    target.write_text("def f():\n    return 1\n")
    seen = {}

    def fake_index_file(**kwargs):
        seen["free_during_run"] = [
            process_locks.inspect(slots.SCOPE, t, storage) is None for t in slots._targets()
        ]
        return {"success": True}

    with mock.patch("jcodemunch_mcp.tools.index_file.index_file", side_effect=fake_index_file):
        server.main(["index-file", str(target)])
    assert seen["free_during_run"].count(False) == 1, "the run did not hold exactly one slot"
    assert all(process_locks.inspect(slots.SCOPE, t, storage) is None for t in slots._targets())


def test_the_slot_is_given_back_when_the_run_fails(storage, tmp_path):
    from jcodemunch_mcp import server

    target = tmp_path / "a.py"
    target.write_text("x = 1\n")
    with mock.patch("jcodemunch_mcp.tools.index_file.index_file", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            server.main(["index-file", str(target)])
    assert all(process_locks.inspect(slots.SCOPE, t, storage) is None for t in slots._targets())


def test_the_hook_still_starts_a_run_when_every_slot_is_held(storage, every_slot_held, tmp_path):
    """The hook does not read the slots: importing the lock layer costs every hook
    0.66 s, and the command that it starts enforces the bound itself."""
    from jcodemunch_mcp.cli.hooks import reindex

    with mock.patch("jcodemunch_mcp.cli.hooks.reindex.subprocess.Popen") as popen:
        reindex._spawn_index_file(str(tmp_path / "a.py"))
    popen.assert_called_once()


def test_a_burst_of_real_runs_never_works_more_than_the_bound_at_once(storage, tmp_path):
    """Real processes, each claiming a slot, holding it briefly, then exiting."""
    code = textwrap.dedent(
        f"""
        import json, sys, time
        from jcodemunch_mcp.storage import index_file_slots as s
        got = s.claim({storage!r})
        if got is not None:
            time.sleep(5)
            s.release(got, {storage!r})
        print(json.dumps(got))
        """
    )
    burst = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
             for _ in range(slots.MAX_CONCURRENT + 4)]
    started = time.monotonic()
    claimed = [json.loads(p.communicate(timeout=60)[0]) for p in burst]
    assert time.monotonic() - started < 60
    worked = [c for c in claimed if c is not None]
    assert 1 <= len(worked) <= slots.MAX_CONCURRENT
    assert len(set(worked)) == len(worked), "two live processes held one slot"
