"""The full tier shows its progress while it runs, and names a test that hangs.

A CI job cancelled at `timeout-minutes` used to leave nothing in its step log
between the env block and "The operation was canceled.": `_run` captured all
of pytest's output and printed only after pytest exited. A hung test and a slow
runner read the same, and the only remedy was a re-run (two windows legs on
2026-10-29, each green on re-run). These tests pin the two halves of the fix:
`_run` echoes the lines it is told to while the child runs, and the full tier
asks pytest's own faulthandler plugin to dump the stacks of a test that runs
too long, then echoes that dump.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from harness import __main__ as harness_main


def test_run_echoes_a_line_before_the_child_exits(tmp_path, capsys):
    """The child waits for a file the echo creates; a buffered `_run` never creates it."""
    flag = tmp_path / "seen"
    child = textwrap.dedent(
        f"""
        import sys, time, pathlib
        print("first [ 10%]", flush=True)
        flag = pathlib.Path({str(flag)!r})
        deadline = time.monotonic() + 20
        while not flag.exists():
            if time.monotonic() > deadline:
                sys.exit(3)
            time.sleep(0.05)
        print("second line")
        """
    )

    def echo(line: str) -> bool:
        if "first" in line:
            flag.write_text("x", encoding="utf-8")
        return True

    rc, out, secs = harness_main._run([sys.executable, "-c", child], echo=echo)
    assert rc == 0, out
    assert "first [ 10%]" in out and "second line" in out
    printed = capsys.readouterr().out
    assert printed.index("first [ 10%]") < printed.index("second line")


def test_run_without_echo_prints_nothing(capsys):
    rc, out, _ = harness_main._run([sys.executable, "-c", "print('quiet')"])
    assert rc == 0 and "quiet" in out
    assert "quiet" not in capsys.readouterr().out


def test_the_echo_keeps_progress_and_a_hang_dump_only():
    echo = harness_main._ProgressEcho()
    lines = [
        ("........................................................ [ 30%]", True),
        ("..F..s................................................. [ 31%]", True),
        ("src/jcodemunch_mcp/server.py      4000    300    92%", False),
        ("TOTAL                             61604   9308    85%", False),
        ("Timeout (0:05:00)!", True),
        ("Thread 0x00001234 (most recent call first):", True),
        ('  File "tests/test_x.py", line 9 in test_hangs', True),
        ("", True),
        ("tests/test_y.py::test_ok PASSED", False),
        ("18846 passed, 25 skipped in 869.66s (0:14:29)", False),
    ]
    for line, expected in lines:
        assert echo(line) is expected, line


def _fake_full(monkeypatch):
    captured = {}

    def fake_run(cmd, *, env=None, echo=None):
        captured["cmd"] = list(cmd)
        captured["echo"] = echo
        return 0, "1 passed in 1.0s\nTOTAL 1 0 100%\n", 1.0

    monkeypatch.setattr(harness_main, "_run", fake_run)
    monkeypatch.setattr(harness_main, "warm_assets", lambda: True)
    harness_main.tier_full({"tiers": {}})
    return captured


def test_the_full_tier_streams_and_sets_a_faulthandler_bound(monkeypatch):
    captured = _fake_full(monkeypatch)
    assert isinstance(captured["echo"], harness_main._ProgressEcho)
    cmd = captured["cmd"]
    opt = f"faulthandler_timeout={harness_main.FAULTHANDLER_TIMEOUT_SECONDS}"
    assert opt in cmd and cmd[cmd.index(opt) - 1] == "-o"


def test_the_faulthandler_bound_fires_before_the_job_is_cancelled():
    from harness import thresholds as T

    job_seconds = T.floor("ci.test_job_timeout_minutes") * 60
    # A hang has to be dumped while the job still runs, with the rest of the
    # suite (about 15 min on a windows runner) still to fit.
    assert 0 < harness_main.FAULTHANDLER_TIMEOUT_SECONDS <= job_seconds / 3


def test_a_hung_test_is_named_in_the_echoed_output(tmp_path, capsys):
    """Through pytest and an xdist worker, as the full tier runs it."""
    t = tmp_path / "test_hangs.py"
    t.write_text(
        "import time\n\ndef test_sleeps_past_the_bound():\n    time.sleep(4)\n",
        encoding="utf-8",
    )
    rc, out, _ = harness_main._run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(t),
            "-q",
            "-p",
            "no:cacheprovider",
            "-n",
            "1",
            "-o",
            "faulthandler_timeout=1",
        ],
        echo=harness_main._ProgressEcho(),
    )
    assert rc == 0, out
    printed = capsys.readouterr().out
    assert "Timeout" in printed
    assert "test_sleeps_past_the_bound" in printed
    assert Path(t).name in printed
