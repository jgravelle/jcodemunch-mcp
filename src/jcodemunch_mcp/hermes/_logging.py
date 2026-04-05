"""Dedicated file-based logging for the jcodemunch Hermes plugin.

By default, Python's :mod:`logging` routes plugin messages to whatever
handler the host (Hermes Agent) has configured — usually stderr or a
central Hermes log. That makes it hard to answer the question "is my
plugin actually firing?" without digging through the host's logs.

This module attaches a dedicated :class:`RotatingFileHandler` to the
``jcodemunch_mcp.hermes`` logger at plugin registration time, so every
log line emitted from the plugin (including all six hooks and the
tools/register code paths) lands in a predictable, dedicated file at::

    ~/.hermes/plugins/jcodemunch/logs/debug.log

Override the directory with ``JCODEMUNCH_HERMES_LOG_DIR`` for testing.

Set ``JCODEMUNCH_HERMES_DEBUG=1`` to drop the logger level from
``INFO`` down to ``DEBUG`` for verbose per-decision tracing. Without
the debug flag, only INFO and above (hook fires, registration events,
warnings, errors) are written — which is already enough to confirm
whether hooks are running in a given session.

The configure function is idempotent: calling it multiple times (e.g.,
after Hermes reloads the plugin for any reason) will not add duplicate
handlers.
"""

from __future__ import annotations

import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# Marker attached to our FileHandler instance so idempotency checks
# can recognise the handler we installed and skip re-installation.
_HANDLER_MARKER = "_jcm_hermes_plugin_handler"

# The directory shim at ~/.hermes/plugins/jcodemunch/__init__.py may
# attach its OWN RotatingFileHandler to the same logger before this
# module runs (so that ImportError paths are visible in debug.log
# even when the in-package module can't be imported). If we see that
# shim-installed handler, we skip our own setup — otherwise every
# log line gets written twice, once per handler.
_SHIM_HANDLER_MARKER = "_jcm_shim_handler"

# The logger name we configure. Every other module in this subpackage
# uses ``logging.getLogger(__name__)`` which resolves to a child of
# this logger, so the handler is inherited automatically.
_PLUGIN_LOGGER_NAME = "jcodemunch_mcp.hermes"

# Default log location. Sits alongside the dev-shim directory at
# ``~/.hermes/plugins/jcodemunch/`` so operators have a single obvious
# place to look regardless of how the plugin was installed (entry
# point vs. directory shim).
_DEFAULT_LOG_DIR = Path.home() / ".hermes" / "logs" / "jcodemunch"
_LOG_FILENAME = "debug.log"

_lock = threading.Lock()
_configured = False


def _resolve_log_dir() -> Path:
    """Return the log directory, honouring ``JCODEMUNCH_HERMES_LOG_DIR``."""
    override = os.environ.get("JCODEMUNCH_HERMES_LOG_DIR")
    if override:
        return Path(override).expanduser()
    return _DEFAULT_LOG_DIR


def get_log_file_path() -> Path:
    """Public helper that returns the resolved debug.log path.

    Useful for tests, docs, and any code that wants to display the
    file location to the user.
    """
    return _resolve_log_dir() / _LOG_FILENAME


def _has_marker_handler(logger: logging.Logger) -> bool:
    """True if ANY handler writing to our debug.log is already
    attached — whether installed by this module or by the directory
    shim at ~/.hermes/plugins/jcodemunch/__init__.py."""
    for handler in logger.handlers:
        if getattr(handler, _HANDLER_MARKER, False):
            return True
        if getattr(handler, _SHIM_HANDLER_MARKER, False):
            return True
    return False


def configure_plugin_logging(force: bool = False) -> Optional[Path]:
    """Attach a dedicated rotating FileHandler to the plugin logger.

    Safe to call multiple times — the second and later calls are
    no-ops unless ``force=True`` is passed (used by tests that want a
    clean slate).

    Returns the resolved log file path on success, or ``None`` if the
    logging setup failed for any reason (missing permissions, disk
    full, etc.). A failure here must never propagate: plugin loading
    continues without file logging.
    """
    global _configured

    with _lock:
        if _configured and not force:
            return get_log_file_path()

        try:
            log_dir = _resolve_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / _LOG_FILENAME

            logger = logging.getLogger(_PLUGIN_LOGGER_NAME)

            # Remove any previously installed marker handler so a
            # forced reconfigure can swap log directories cleanly.
            if force:
                logger.handlers = [
                    h for h in logger.handlers
                    if not getattr(h, _HANDLER_MARKER, False)
                ]

            if not _has_marker_handler(logger):
                handler = RotatingFileHandler(
                    log_file,
                    mode="a",
                    maxBytes=5 * 1024 * 1024,  # 5 MB
                    backupCount=3,
                    encoding="utf-8",
                    delay=False,
                )
                handler.setFormatter(
                    logging.Formatter(
                        fmt="[%(asctime)s] %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S",
                    )
                )
                # Tag so idempotency check recognises our handler.
                setattr(handler, _HANDLER_MARKER, True)
                logger.addHandler(handler)

            # Lower the logger level based on the debug env flag. We
            # do NOT disable propagation — Hermes' own handlers still
            # receive the messages, which is usually what operators
            # want when debugging a plugin.
            debug = os.environ.get("JCODEMUNCH_HERMES_DEBUG", "").strip().lower()
            if debug in {"1", "true", "yes", "on"}:
                logger.setLevel(logging.DEBUG)
            else:
                logger.setLevel(logging.INFO)

            _configured = True
            return log_file
        except Exception:
            # Last-resort safety: never raise from logging setup.
            # Fall back to whatever handlers are already on the logger
            # (inherited from Hermes) so log lines still go somewhere.
            return None
