"""Keyring-backed credential resolution for jcodemunch-mcp (P1.3).

The recommended install pattern places API keys and tokens directly in
``claude_desktop_config.json``, ``~/.claude/settings.json``, ``.mcp.json``,
or ``~/.code-index/config.jsonc``. That keeps the user experience simple
but means every secret named in an MCP env block is recoverable on a
compromised endpoint with a single file read.

This module is the opt-in alternative: any of the recognised credential
env vars may be set to ``"keyring:<name>"``, and the resolver rewrites
that value to the secret stored under that name in the system keyring at
server startup. After the rewrite, every existing ``os.environ.get(...)``
site reads the resolved value without code change.

Storage backend is whatever ``keyring`` selects: macOS Keychain,
Windows Credential Manager, the freedesktop Secret Service on Linux, or
any of the alternative backends documented in the keyring package.

Service name is ``"jcodemunch-mcp"``. Keep your secret names stable so
they survive reinstalls.

Public surface:

    resolve_credentials_in_env() -> None
        Walks the recognised credential env vars and rewrites
        ``keyring:NAME`` references in-place via ``os.environ``.
        Safe to call multiple times. Soft-fails (logs at WARNING and
        leaves the env var unchanged) if the keyring package isn't
        installed or the named secret is missing.

    list_recognised_env_vars() -> list[str]
        Returns the canonical list of env vars this module touches.

    get_keyring_source_for(env_var) -> str | None
        For diagnostic use (``config --check``): returns the keyring
        name a given env var was resolved from, or ``None`` if the env
        var was not keyring-sourced.

    keyring_set / keyring_get / keyring_delete / keyring_list
        Thin wrappers over the keyring library so the CLI doesn't need
        a direct dependency. Each raises ImportError if keyring isn't
        installed.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Service name used for all jcodemunch-mcp keyring entries. Don't change without
# a migration plan — existing user secrets are looked up under this exact name.
SERVICE_NAME = "jcodemunch-mcp"

# Prefix that marks an env-var value as a keyring lookup. The string after the
# prefix is the entry name under SERVICE_NAME.
KEYRING_PREFIX = "keyring:"

# Canonical list of env vars this module touches. Add to this list (and the
# keyring CLI's documented names) when introducing a new credential surface.
CREDENTIAL_ENV_VARS: tuple[str, ...] = (
    "GITHUB_TOKEN",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "MINIMAX_API_KEY",
    "ZHIPUAI_API_KEY",
    "OPENROUTER_API_KEY",
    "ORCAROUTER_API_KEY",
    "GROQ_API_KEY",
    "JCODEMUNCH_HTTP_TOKEN",
)

# Maps env var name -> keyring entry name it was resolved from. Populated by
# resolve_credentials_in_env(). Used by config --check diagnostics so the
# operator can see which env vars came from the keyring vs. plain env.
_resolution_source: dict[str, str] = {}


def list_recognised_env_vars() -> list[str]:
    """Return the canonical list of credential env vars this module touches."""
    return list(CREDENTIAL_ENV_VARS)


def get_keyring_source_for(env_var: str) -> Optional[str]:
    """Return the keyring entry name an env var was resolved from, or None.

    Used by ``config --check`` to surface which env vars came from the keyring
    so the operator can confirm the chokepoint is firing without having to
    inspect the actual secret value.
    """
    return _resolution_source.get(env_var)


def _import_keyring():
    """Import the keyring library, raising ImportError with an actionable hint."""
    try:
        import keyring  # noqa: F401  (returned by reference)
        return __import__("keyring")
    except ImportError as e:
        raise ImportError(
            "Keyring resolution requires the 'keyring' package. "
            "Install with: pip install \"jcodemunch-mcp[keyring]\""
        ) from e


def keyring_set(name: str, value: str) -> None:
    """Store a secret in the system keyring under SERVICE_NAME/<name>."""
    k = _import_keyring()
    k.set_password(SERVICE_NAME, name, value)


def keyring_get(name: str) -> Optional[str]:
    """Fetch a secret from the system keyring. Returns None if absent."""
    k = _import_keyring()
    return k.get_password(SERVICE_NAME, name)


def keyring_delete(name: str) -> bool:
    """Remove a secret from the keyring. Returns True if a secret was removed."""
    k = _import_keyring()
    try:
        k.delete_password(SERVICE_NAME, name)
        return True
    except Exception as e:
        # keyring raises PasswordDeleteError or backend-specific exceptions
        # when the password doesn't exist. Treat both as "nothing to delete."
        logger.debug("keyring_delete(%s) raised %s", name, e)
        return False


def keyring_list() -> list[str]:
    """Return the list of recognised env-var names users can store secrets under.

    The keyring library doesn't offer cross-backend enumeration of stored
    entries (macOS Keychain does, freedesktop Secret Service partially does,
    Windows Credential Manager doesn't), so we surface the documented set of
    env-var names rather than probing the backend. The caller can then run
    ``keyring_get(name)`` on each to see which are actually populated.
    """
    return list(CREDENTIAL_ENV_VARS)


def resolve_credentials_in_env() -> None:
    """Rewrite ``keyring:NAME`` references in credential env vars in-place.

    For every env var in :data:`CREDENTIAL_ENV_VARS`: if the current value
    starts with :data:`KEYRING_PREFIX`, look up the keyring entry named after
    the prefix and replace the env var's value with the resolved secret.

    Soft-fails: if the keyring package isn't installed, or if the named entry
    doesn't exist, or if backend access raises, the env var is left at its
    original ``keyring:NAME`` value and a warning is logged. Callers downstream
    will then see the literal ``keyring:NAME`` string and treat it as missing
    credentials, which is the correct behavior — fail closed, never silently
    leak the literal prefix to an HTTP request.

    Safe to call multiple times: env vars without the prefix are skipped, and
    already-resolved env vars no longer match the prefix.
    """
    keyring_module = None
    for env_var in CREDENTIAL_ENV_VARS:
        raw = os.environ.get(env_var, "")
        if not raw.startswith(KEYRING_PREFIX):
            continue

        entry_name = raw[len(KEYRING_PREFIX):].strip()
        if not entry_name:
            logger.warning(
                "%s set to bare '%s' with no entry name; leaving unchanged",
                env_var,
                KEYRING_PREFIX,
            )
            continue

        # Lazy-import keyring so callers without the [keyring] extra aren't
        # forced to install it just to start the server.
        if keyring_module is None:
            try:
                keyring_module = _import_keyring()
            except ImportError as e:
                logger.warning(
                    "%s requested keyring resolution but the keyring package "
                    "is not installed (%s); leaving env var at literal "
                    "'%s' so downstream code fails closed",
                    env_var,
                    e,
                    raw,
                )
                return

        try:
            secret = keyring_module.get_password(SERVICE_NAME, entry_name)
        except Exception as e:
            logger.warning(
                "%s keyring lookup failed for entry '%s': %s; leaving env var "
                "at literal '%s'",
                env_var,
                entry_name,
                e,
                raw,
            )
            continue

        if secret is None:
            logger.warning(
                "%s requested keyring entry '%s' but no such entry exists "
                "under service '%s'; leaving env var at literal '%s'. "
                "Store it with: jcodemunch-mcp keyring set %s",
                env_var,
                entry_name,
                SERVICE_NAME,
                raw,
                entry_name,
            )
            continue

        os.environ[env_var] = secret
        _resolution_source[env_var] = entry_name
        logger.debug(
            "resolved %s from keyring entry '%s' under service '%s'",
            env_var,
            entry_name,
            SERVICE_NAME,
        )
