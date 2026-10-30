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
import time
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
        # Another worker reported while this one dumped: the dump goes on.
        ("........................................................ [ 32%]", True),
        ("Thread 0x00005678 (most recent call first):", True),
        ('  File "tests/test_z.py", line 3 in test_stuck', True),
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
    # A hang has to be dumped while the job still runs, with most of the
    # job's time left for the rest of the suite.
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


def test_a_failing_run_parses_the_same_on_both_paths(tmp_path, capsys):
    """The summary counts and the failed ids read the same with and without echo.

    The streamed path reads stdout and stderr as one stream where the old path
    joined them stdout-then-stderr, so stderr text (a warning) can sit in a
    different place in the failure report. Everything the tier PARSES from the
    output must not move.
    """
    t = tmp_path / "test_mixed.py"
    t.write_text(
        "import pytest, sys\n\n"
        "def test_ok():\n    assert True\n\n"
        "def test_bad():\n    print('noise', file=sys.stderr)\n    assert 1 == 2\n\n"
        "@pytest.fixture\ndef broken():\n    raise RuntimeError('setup')\n\n"
        "def test_err(broken):\n    pass\n",
        encoding="utf-8",
    )
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(t),
        "-q",
        "-p",
        "no:cacheprovider",
        "-n",
        "2",
        "--tb=short",
    ]
    rc_a, out_a, _ = harness_main._run(cmd)
    rc_b, out_b, _ = harness_main._run(cmd, echo=harness_main._ProgressEcho())
    capsys.readouterr()
    assert rc_a == rc_b != 0
    sa, sb = harness_main._pytest_summary(out_a), harness_main._pytest_summary(out_b)
    assert {k: sa[k] for k in ("passed", "failed", "skipped")} == {
        k: sb[k] for k in ("passed", "failed", "skipped")
    }
    assert sa["failed"] >= 1
    assert sorted(harness_main._failed_id_lines(out_a)) == sorted(
        harness_main._failed_id_lines(out_b)
    )


def test_a_raising_echo_kills_the_child(tmp_path):
    """subprocess.run kills its child on an exception; the streamed path must too."""
    pidfile = tmp_path / "pid"
    child = (
        "import os, sys, time, pathlib\n"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
        "print('go', flush=True)\n"
        "time.sleep(60)\n"
    )

    def boom(line):
        raise RuntimeError("echo failed")

    t0 = time.monotonic()
    try:
        harness_main._run([sys.executable, "-c", child], echo=boom)
    except RuntimeError:
        pass
    else:
        raise AssertionError("the echo's exception was swallowed")
    assert time.monotonic() - t0 < 30, "the child was waited on instead of killed"
