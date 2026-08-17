"""Tests for keyring-backed credential resolution (P1.3).

Covers:
- ``resolve_credentials_in_env`` is a no-op when no env var carries the
  ``keyring:`` prefix.
- A ``keyring:NAME`` env var gets rewritten to the secret stored under
  ``NAME`` in the keyring (mocked) at server startup.
- Missing-entry case: env var stays at the literal ``keyring:NAME`` so
  downstream code fails closed rather than silently treating the prefix
  string as a real credential.
- Keyring not installed: env var stays at the literal ``keyring:NAME``;
  no exception bubbles out of ``resolve_credentials_in_env``.

Uses ``unittest.mock`` to fake the keyring backend so the test suite
runs without an actual keyring package or system keyring backend.
"""

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

from jcodemunch_mcp import credentials as _creds


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Each test runs against a clean copy of the credential env vars."""
    for var in _creds.CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    _creds._resolution_source.clear()
    yield
    _creds._resolution_source.clear()


class TestResolveCredentialsInEnv:
    def test_noop_when_no_keyring_prefix(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-plain")
        _creds.resolve_credentials_in_env()
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-plain"
        assert _creds.get_keyring_source_for("ANTHROPIC_API_KEY") is None

    def test_resolves_keyring_prefix_to_secret(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "keyring:my-claude")

        fake_keyring = MagicMock()
        fake_keyring.get_password.return_value = "sk-ant-actual-secret"
        with patch.dict(sys.modules, {"keyring": fake_keyring}):
            _creds.resolve_credentials_in_env()

        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-actual-secret"
        assert _creds.get_keyring_source_for("ANTHROPIC_API_KEY") == "my-claude"
        fake_keyring.get_password.assert_called_with(_creds.SERVICE_NAME, "my-claude")

    def test_missing_entry_leaves_literal_prefix(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "keyring:gh-missing")

        fake_keyring = MagicMock()
        fake_keyring.get_password.return_value = None  # no such entry
        with patch.dict(sys.modules, {"keyring": fake_keyring}):
            _creds.resolve_credentials_in_env()

        # Fail-closed: env var unchanged. Downstream HTTP calls will see the
        # literal "keyring:gh-missing" and treat it as an invalid token.
        assert os.environ["GITHUB_TOKEN"] == "keyring:gh-missing"
        assert _creds.get_keyring_source_for("GITHUB_TOKEN") is None

    def test_keyring_not_installed_leaves_literal_prefix(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "keyring:openai-prod")

        # Force the import to fail by removing keyring from sys.modules and
        # blocking the import.
        with patch.dict(sys.modules, {"keyring": None}):
            _creds.resolve_credentials_in_env()

        # Same fail-closed behavior as the missing-entry case.
        assert os.environ["OPENAI_API_KEY"] == "keyring:openai-prod"
        assert _creds.get_keyring_source_for("OPENAI_API_KEY") is None

    def test_bare_prefix_with_no_entry_name_is_skipped(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "keyring:")

        fake_keyring = MagicMock()
        with patch.dict(sys.modules, {"keyring": fake_keyring}):
            _creds.resolve_credentials_in_env()

        assert os.environ["GROQ_API_KEY"] == "keyring:"
        # And we should not have hit the keyring backend at all.
        fake_keyring.get_password.assert_not_called()

    def test_mixed_env_vars_resolved_independently(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "keyring:claude-prod")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-plain")  # not keyring-sourced
        monkeypatch.setenv("GITHUB_TOKEN", "keyring:gh-bot")

        fake_keyring = MagicMock()
        fake_keyring.get_password.side_effect = lambda service, name: {
            "claude-prod": "sk-ant-real",
            "gh-bot": "ghp-real-token",
        }.get(name)
        with patch.dict(sys.modules, {"keyring": fake_keyring}):
            _creds.resolve_credentials_in_env()

        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-real"
        assert os.environ["OPENAI_API_KEY"] == "sk-openai-plain"  # untouched
        assert os.environ["GITHUB_TOKEN"] == "ghp-real-token"
        assert _creds.get_keyring_source_for("ANTHROPIC_API_KEY") == "claude-prod"
        assert _creds.get_keyring_source_for("OPENAI_API_KEY") is None
        assert _creds.get_keyring_source_for("GITHUB_TOKEN") == "gh-bot"

    def test_idempotent_double_call(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "keyring:claude")

        fake_keyring = MagicMock()
        fake_keyring.get_password.return_value = "sk-ant-secret"
        with patch.dict(sys.modules, {"keyring": fake_keyring}):
            _creds.resolve_credentials_in_env()
            # After first resolve, env var no longer carries the prefix, so a
            # second call must be a no-op (no second keyring lookup).
            _creds.resolve_credentials_in_env()

        assert fake_keyring.get_password.call_count == 1
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-secret"


class TestRecognisedEnvVars:
    def test_canonical_list_matches_documented_surface(self):
        listed = _creds.list_recognised_env_vars()
        # The PRD names these ten env vars as the credential surface. Locking
        # the set here so any future change is forced through CHANGELOG.
        expected = {
            "GITHUB_TOKEN",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "OPENAI_API_KEY",
            "OPENAI_API_BASE",
            "MINIMAX_API_KEY",
            "ZHIPUAI_API_KEY",
            "OPENROUTER_API_KEY",
            "GROQ_API_KEY",
            "JCODEMUNCH_HTTP_TOKEN",
        }
        assert set(listed) == expected
