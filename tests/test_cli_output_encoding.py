"""CLI output is UTF-8 even when piped (v1.108.262).

On Windows `sys.stdout` is the CONSOLE stream (already UTF-8) when attached to a
terminal and the LOCALE stream (cp1252) when piped or redirected. So a command
printing any character cp1252 cannot encode worked interactively and died the
moment anything consumed it:

    jcodemunch-mcp receipt --explain            # fine
    jcodemunch-mcp receipt --explain | more     # UnicodeEncodeError, no output

`receipt --explain` writes U+2212 MINUS SIGN; `render_diagram` writes U+2713
CHECK MARK. A hard traceback out of a shipped command, in the one configuration
nobody exercises by hand and every script uses.

⚠ Sibling of the cp1252 DECODE class swept in v1.108.230. That sweep hardened
subprocess INPUT and left our own OUTPUT alone, which is the shape a fix leaves
when it is scoped to the symptom that was reported rather than to the hazard.
See tests/test_subprocess_encoding_guard.py for the other half.

⚠ These tests run the CLI in a SUBPROCESS with a pipe, because that is the only
configuration that reproduces it. Calling the formatter in-process cannot fail:
pytest's capture layer accepts any string.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")

# Characters cp1252 cannot encode at all, which is what makes them fatal rather
# than merely mangled. U+2212 is live in `receipt --explain` today.
UNENCODABLE = "−✓→"


def _run(args, env_extra=None):
    """Run the CLI with stdout as a PIPE, decoding nothing for us."""
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env.pop("PYTHONIOENCODING", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "jcodemunch_mcp.server", *args],
        capture_output=True, env=env, timeout=300,
    )


class TestTheReportedCrash:
    def test_receipt_explain_survives_a_pipe(self):
        out = _run(["receipt", "--explain"])
        assert out.returncode == 0, out.stderr.decode("utf-8", "replace")[-2000:]
        assert b"UnicodeEncodeError" not in out.stderr

    def test_its_output_is_valid_utf8(self):
        """Not crashing is not the same as being right. The bytes must decode."""
        out = _run(["receipt", "--explain"])
        out.stdout.decode("utf-8")  # raises if we emitted cp1252

    def test_the_unencodable_character_survives_round_trip(self):
        """U+2212 is the exact character that used to kill this command."""
        out = _run(["receipt", "--explain"])
        assert "−" in out.stdout.decode("utf-8"), (
            "the character that caused the crash is gone from the output; "
            "it must round-trip, not be dropped"
        )

    def test_cp1252_cannot_represent_that_character(self):
        """Non-vacuity floor: if this ever encodes, the test above proves
        nothing, because the crash it guards could not happen."""
        with pytest.raises(UnicodeEncodeError):
            "−".encode("cp1252")


class TestOtherCommands:
    """One crash is a bug; the fix is at the entry point, so spot-check that
    other commands come through the same door."""

    @pytest.mark.parametrize("args", [
        ["receipt", "--rates"],
        ["surface", "--json"],
        ["--help"],
    ])
    def test_output_is_valid_utf8(self, args):
        out = _run(args)
        out.stdout.decode("utf-8")
        assert b"UnicodeEncodeError" not in out.stderr


class TestTheKnob:
    def test_pythonioencoding_is_honoured_as_an_opt_out(self):
        """An operator who named an encoding made a decision. We do not override
        it -- but the command must still not crash, so the value has to be one
        that can carry the payload."""
        out = _run(["receipt", "--rates"], {"PYTHONIOENCODING": "utf-8"})
        assert out.returncode == 0
        out.stdout.decode("utf-8")

    def test_the_opt_out_is_checked_before_reconfiguring(self):
        import inspect
        from jcodemunch_mcp import server as S
        src = inspect.getsource(S._force_utf8_stdio)
        assert src.index("PYTHONIOENCODING") < src.index("reconfigure("), (
            "the opt-out must be honoured before we touch the stream"
        )


class TestTheHelperItself:
    def test_it_does_not_raise_without_reconfigure(self, monkeypatch):
        """Captured buffers and test harnesses replace sys.stdout with objects
        that have no reconfigure(). The CLI must still start."""
        import io
        from jcodemunch_mcp import server as S
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        S._force_utf8_stdio()  # must not raise

    def test_a_refusing_stream_is_survivable(self, monkeypatch):
        """A stream that refuses reconfiguration is not a reason to refuse to
        run. It keeps whatever behaviour it had."""
        from jcodemunch_mcp import server as S

        class Stubborn:
            encoding = "cp1252"

            def reconfigure(self, **kw):
                raise OSError("no")

        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        monkeypatch.setattr(sys, "stdout", Stubborn())
        monkeypatch.setattr(sys, "stderr", Stubborn())
        S._force_utf8_stdio()

    def test_an_already_utf8_stream_is_left_alone(self, monkeypatch):
        """Reconfiguring a stream that is already correct is a needless side
        effect on a shared global."""
        from jcodemunch_mcp import server as S
        calls = []

        class Fine:
            encoding = "UTF-8"

            def reconfigure(self, **kw):
                calls.append(kw)

        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        monkeypatch.setattr(sys, "stdout", Fine())
        monkeypatch.setattr(sys, "stderr", Fine())
        S._force_utf8_stdio()
        assert calls == []

    def test_a_cp1252_stream_is_reconfigured_with_replace(self, monkeypatch):
        """errors='replace' matters: filesystem paths can carry surrogates from
        a surrogateescape decode, and those raise even under UTF-8."""
        from jcodemunch_mcp import server as S
        calls = []

        class Locale:
            encoding = "cp1252"

            def reconfigure(self, **kw):
                calls.append(kw)

        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        monkeypatch.setattr(sys, "stdout", Locale())
        monkeypatch.setattr(sys, "stderr", Locale())
        S._force_utf8_stdio()
        assert len(calls) == 2
        assert all(c["encoding"] == "utf-8" and c["errors"] == "replace" for c in calls)

    def test_main_calls_it_before_anything_prints(self):
        import inspect
        from jcodemunch_mcp import server as S
        src = inspect.getsource(S.main)
        assert "_force_utf8_stdio()" in src
        body = src[src.index('"""Main entry point."""'):]
        assert body.index("_force_utf8_stdio()") < 200, (
            "must run before any subcommand can write"
        )


class TestTheMcpTransportIsNotAffected:
    """⚠ The reason this fix is safe to apply unconditionally in main().

    `_force_utf8_stdio()` reconfigures the *text* layer of `sys.stdin`/`sys.stdout`.
    The MCP stdio transport must therefore never read that layer for the JSON-RPC
    wire. In `mcp` 1.x it wrapped `sys.stdout.buffer`; in 2.x it claims the file
    descriptor instead (`_claim_fd(1, ...)`): the wire becomes a private duplicate,
    fd 1 is diverted to stderr while serving, and the transport builds its own
    `TextIOWrapper(encoding="utf-8")` over the duplicate. Either way the wire is
    UTF-8 and independent of the process's locale-configured text layer, which is
    why forcing that layer stays safe. Asserted, not remembered: if a future SDK
    routes the wire through `sys.stdout`, this fails before a corrupted stream does.
    """

    def test_stdio_transport_owns_the_wire_encoding(self):
        import inspect
        import mcp.server.stdio as stdio_mod
        src = inspect.getsource(stdio_mod)
        assert "_claim_fd(1" in src, (
            "the MCP stdio transport no longer claims fd 1 for the wire; "
            "re-check whether _force_utf8_stdio can still run for `serve`"
        )
        assert 'encoding="utf-8"' in src, (
            "the transport no longer sets the wire encoding itself, so the "
            "process's text layer would decide it"
        )
        # The writer must target the claimed stream, never sys.stdout.
        writer = src[src.index("async def stdout_writer"):]
        writer = writer[: writer.index("async with anyio.create_task_group")]
        assert "sys.stdout" not in writer, (
            "the wire writer reaches for sys.stdout, the text layer _force_utf8_stdio "
            "reconfigures; that would put the JSON-RPC stream on the locale encoding"
        )
