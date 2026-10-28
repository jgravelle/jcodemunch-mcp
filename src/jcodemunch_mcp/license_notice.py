"""The license notice for installs that never run `init` (jjg, DECISIONS 2026-10-28, option B).

A `uvx` or plugin install starts the server directly, so it never prints
`init`'s license line and the person using it is never told that work use
needs a license. The first successful tool answer on a machine carries one
plain `license_notice` field instead.

⚠⚠ A STATEMENT, NEVER AN INSTRUCTION TO THE MODEL. The surface and policy
notices ask the agent to relay them; this one does not, by jjg's ruling.
`tests/test_license_notice.py` checks the text for relay wording.

⚠ Once per machine: a marker under `CODE_INDEX_PATH` (default `~/.code-index`),
so a persistent volume, such as the Docker image's index volume, keeps it.
`init` writes the same marker, because it prints its own license line.

⚠ Never shown when a license key is configured (read locally, never
validated: jjg, DECISIONS 2026-10-20, paid clients are not marketed to), when
`JCODEMUNCH_LICENSE_NOTICE` is off, or on a CI or remote runner (`CI` or
`CLAUDE_CODE_REMOTE` true), where a throwaway machine would repeat it every
run. ⚠⚠ `CLAUDECODE=1` is NOT a CI marker: every local Claude Code session
sets it.

⚠ The stderr copy is a debug courtesy. No client shows a server's stderr to
the person by default; stdout is the JSON-RPC stream and is never written.

⚠ Fail-safe like its siblings: a check that fails says nothing; a marker that
cannot be written still says it, once in this process.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

logger = logging.getLogger(__name__)

#: The plugin README's license line, word for word.
NOTICE_TEXT = (
    "License: free for personal and other non-commercial use. Work use needs a commercial "
    "license, paid once, that includes every 1.x update: https://jcodemunch.com/buying.php"
)
ENV_VAR = "JCODEMUNCH_LICENSE_NOTICE"
MARKER_FILE = "license_notice_state.json"
#: A CI or remote runner: a fresh machine each run, so a once-per-machine
#: notice would be said every run. Not CLAUDECODE (see the module docstring).
CI_MARKERS = ("CI", "CLAUDE_CODE_REMOTE")
#: A process decides once; conftest turns it off for every test.
NOTICE_STATE = {"checked": False}

# The first release whose `init` printed the license line (#1131). An `init`
# stamp from an earlier release proves nothing was shown.
FIRST_INIT_WITH_LICENSE_LINE = (1, 108, 376)

_OFF = frozenset({"0", "false", "no", "off"})
_ON = frozenset({"1", "true", "yes", "on"})


def marker_path() -> Path:
    """The once-per-machine marker, under the same store every in-answer notice uses."""
    from .surface_offer import notice_marker_path

    return notice_marker_path(MARKER_FILE)


def skip_reason(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Why the notice is not said here, or None when it is due. Cheapest checks first."""
    env = os.environ if env is None else env
    if (env.get(ENV_VAR) or "").strip().lower() in _OFF:
        return "disabled"
    if any((env.get(name) or "").strip().lower() in _ON for name in CI_MARKERS):
        return "ci"
    from .org.license import licence_key_configured

    if licence_key_configured():
        return "licensed"
    if _init_showed_the_line():
        return "init_ran"
    if marker_path().is_file():
        return "already_shown"
    return None


def _init_showed_the_line() -> bool:
    """Did an `init` from before this marker existed print the license line?

    Such an `init` left only its version stamp. The stamp counts only from the
    first release whose `init` printed the line; an unreadable version counts
    as not shown.
    """
    from .cli.init import read_install_version

    stamped = read_install_version()
    if not stamped:
        return False
    try:
        parts = tuple(int(x) for x in stamped.split(".")[:3])
    except ValueError:
        return False
    return parts >= FIRST_INIT_WITH_LICENSE_LINE


def mark_shown(source: str, version: str = "") -> None:
    """Write the marker. `init` calls this too: it prints its own license line."""
    path = marker_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "shown_at": datetime.now(timezone.utc).isoformat(),
                    "source": source,
                    "version": version,
                }
            ),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        logger.debug("license notice marker not written", exc_info=True)


def notice_for(name: str, result: object, *, version: str = "") -> Optional[str]:
    """The notice text for this answer, or None. Said once per machine.

    Only a successful dict answer can carry it; anything else leaves it for
    the next answer instead of using it up.
    """
    if not isinstance(result, dict) or "error" in result:
        return None
    if NOTICE_STATE["checked"]:
        return None
    NOTICE_STATE["checked"] = True
    try:
        if skip_reason() is not None:
            return None
    except Exception:
        logger.debug("license notice skipped", exc_info=True)
        return None
    mark_shown(name, version)
    try:
        print(NOTICE_TEXT, file=sys.stderr, flush=True)
    except Exception:
        logger.debug("license notice stderr write failed", exc_info=True)
    return NOTICE_TEXT
