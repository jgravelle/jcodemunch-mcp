"""The `jcodemunch-mcp` console entry: a hook starts without the server module.

Claude Code runs a hook command on every matching tool call, and each one is a
fresh process. Every hook went through `server.cli`, so each imported
`server.py` and with it the MCP SDK, httpx, jsonschema and every tool module
before it read its stdin. A hook needs none of that, and the opt-in Grep
answer's time budget starts only after that import, so the import ran on top
of the budget on every Grep.

A bare `jcodemunch-mcp hook-<name>` (the only form `init` writes; no hook takes
an argument) runs its handler directly, after the same steps `server.main`
takes first: UTF-8 output, the package-integrity check, and keyring
resolution of env values. Anything else goes to `server.cli` unchanged, so the
license reminder still follows every command it follows today.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

logger = logging.getLogger(__name__)

# Subcommand -> handler in `cli.hooks`. Each must be dispatched by server.main
# to the same handler with nothing else in between;
# tests/test_hooks_skip_server_import.py checks both directions.
HOOK_RUNNERS = {
    "hook-pretooluse": "run_pretooluse",
    "hook-posttooluse": "run_posttooluse",
    "hook-copilot-posttooluse": "run_copilot_posttooluse",
    "hook-precompact": "run_precompact",
    "hook-taskcomplete": "run_taskcomplete",
    "hook-subagent-start": "run_subagentstart",
    "hook-sessionstart": "run_sessionstart",
}


def cli(argv: Optional[list] = None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 1 and args[0] in HOOK_RUNNERS:
        sys.exit(_run_hook(args[0]))
    from .server import cli as server_cli

    return server_cli(argv)


def _run_hook(command: str) -> int:
    from .cli._stdio import _force_utf8_stdio
    from .security import verify_package_integrity

    _force_utf8_stdio()
    verify_package_integrity()
    try:
        from . import credentials as _creds

        _creds.resolve_credentials_in_env()
    except Exception:
        logger.debug("credential env resolution skipped", exc_info=True)
    from .cli import hooks

    return getattr(hooks, HOOK_RUNNERS[command])()
