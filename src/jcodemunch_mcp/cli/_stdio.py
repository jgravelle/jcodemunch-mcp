"""UTF-8 CLI output, shared by both console entry paths (`cli_entry`, `server.main`)."""

import logging
import os
import sys

logger = logging.getLogger(__name__)


def _force_utf8_stdio() -> None:
    """Make CLI output UTF-8 regardless of the platform locale (v1.108.262).

    ⚠⚠ On Windows, `sys.stdout` is the **console** stream (already UTF-8) when
    attached to a terminal and the **locale** stream (cp1252) when piped or
    redirected. So a command that prints any character cp1252 cannot encode
    works interactively and dies the moment anyone consumes it:

        jcodemunch-mcp receipt --explain            # fine
        jcodemunch-mcp receipt --explain | more     # UnicodeEncodeError, no output

    Measured: `receipt --explain` writes U+2212 MINUS SIGN and `render_diagram`
    writes U+2713 CHECK MARK. That is a hard traceback out of a shipped command,
    in the one configuration nobody exercises by hand and every script uses.
    Sibling of the cp1252 DECODE class swept in v1.108.230; that sweep covered
    subprocess input and left output alone.

    Fixed at the entry point rather than per string, because the next non-ASCII
    character someone adds must not reintroduce this.

    ⚠ The MCP stdio transport is unaffected: it wraps `sys.stdout.buffer` in its
    own TextIOWrapper, so it never reads the text layer being reconfigured here.
    Verified before this shipped.

    ⚠ `errors="replace"` is deliberate. Filesystem paths can carry surrogates
    from a `surrogateescape` decode, and those raise even under UTF-8. Mangling
    one character in a display string beats killing the command.

    ⚠ `PYTHONIOENCODING` is honoured as an explicit opt-out: if the operator
    named an encoding, that is a decision, not an accident.
    """
    if os.environ.get("PYTHONIOENCODING"):
        return
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # replaced by a test harness or a captured buffer
            continue
        current = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if current in ("utf8", "utf8mb4"):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # A stream that refuses reconfiguration is not a reason to refuse to
            # run; the command simply keeps the behaviour it had before.
            logger.debug("Could not force UTF-8 on sys.%s", stream_name, exc_info=True)
