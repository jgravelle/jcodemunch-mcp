"""jcodemunch Hermes plugin — registration entry point.

This subpackage is what Hermes Agent loads when
``jcodemunch-mcp`` is installed alongside Hermes. Registration happens
via an entry point declared in ``pyproject.toml``::

    [project.entry-points."hermes_agent.plugins"]
    jcodemunch = "jcodemunch_mcp.hermes"

On Hermes startup the plugin loader imports this module and calls
:func:`register`. That function:

1. Discovers every jcodemunch tool by calling
   :func:`jcodemunch_mcp.server.list_tools` and registers each one
   under the ``jcm_`` prefix.
2. Registers the lifecycle hooks from :mod:`.hooks` (read guard,
   edit guard, index hook, session context injector, session
   start/end).

Both steps are wrapped in defensive try/except so a misbehaving plugin
can never crash the agent — matching the Hermes contract documented at
https://hermes-agent.nousresearch.com/docs/guides/build-a-hermes-plugin.
"""

from __future__ import annotations

import logging

from . import _logging as _plugin_logging
from . import hooks as _hooks
from . import tools as _tools

try:
    from .. import __version__ as _parent_version
except Exception:  # pragma: no cover — extremely defensive
    _parent_version = "unknown"

logger = logging.getLogger(__name__)

# Exposed for ``/plugins`` banner and test harnesses.
__plugin_name__ = "jcodemunch"
__plugin_version__ = _parent_version


def register(ctx) -> None:
    """Called once at startup by the Hermes plugin loader.

    Responsibilities:

    1. Discover every jcodemunch tool via ``list_tools()`` on a
       background event loop thread.
    2. Register each discovered tool under the ``jcm_`` prefix,
       pointing at a sync handler that dispatches to jcodemunch's
       async ``call_tool()`` through the bridge.
    3. Register lifecycle hooks from :mod:`jcodemunch_mcp.hermes.hooks`.

    If ``register()`` raises, the whole plugin is disabled by Hermes.
    We therefore catch everything and log — never let exceptions
    escape.
    """
    try:
        # Set up dedicated file logging BEFORE anything else so we get
        # a visible audit trail of the registration itself, including
        # any failures below. Failure of the logging setup is
        # non-fatal — we fall back to whatever handlers Hermes has
        # already configured on the root logger.
        log_file = _plugin_logging.configure_plugin_logging()
        if log_file:
            logger.info(
                "register() entered — plugin v%s, log file: %s",
                _parent_version, log_file,
            )
        else:
            logger.info(
                "register() entered — plugin v%s (file logging unavailable)",
                _parent_version,
            )

        entries = _tools.build_tool_entries()
        if not entries:
            logger.warning(
                "jcodemunch hermes plugin: no tools discovered "
                "(jcodemunch %s) — plugin will be empty.",
                _parent_version,
            )

        registered_tools = 0
        for schema, handler in entries:
            try:
                ctx.register_tool(
                    name=schema["name"],
                    toolset="jcodemunch",
                    schema=schema,
                    handler=handler,
                )
                registered_tools += 1
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning(
                    "jcodemunch hermes plugin: failed to register %s: %s",
                    schema.get("name"),
                    exc,
                )

        registered_hooks = _hooks.register_hooks(ctx)

        logger.info(
            "jcodemunch hermes plugin: registered %d/%d tools and %d hooks "
            "(jcodemunch %s)",
            registered_tools,
            len(entries),
            registered_hooks,
            _parent_version,
        )
    except Exception as exc:  # pragma: no cover — last-resort safety net
        logger.exception("jcodemunch hermes plugin: register() failed: %s", exc)
