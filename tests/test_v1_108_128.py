"""v1.108.128 — paid-cloud summarizer auto-billing guard.

A bare cloud API key in the environment (e.g. ANTHROPIC_API_KEY) used to
auto-enable AI summarization, silently billing the account on every index.
The guard: auto-detect never selects a PAID cloud provider from a bare env key
unless the user explicitly opts in (names the provider, or sets
JCODEMUNCH_ALLOW_PAID_SUMMARIES / allow_paid_summaries). Free/local endpoints
still auto-enable.
"""

import pytest

from jcodemunch_mcp.summarizer import batch_summarize as bs


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in (
        "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_BASE", "OPENAI_API_KEY",
        "ATLASCLOUD_API_KEY", "ATLAS_CLOUD_API_KEY",
        "MINIMAX_API_KEY", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY",
        "JCODEMUNCH_SUMMARIZER_PROVIDER", "JCODEMUNCH_ALLOW_PAID_SUMMARIES",
    ):
        monkeypatch.delenv(k, raising=False)
    # Ensure config provider/opt-in are unset for the auto path.
    monkeypatch.setattr(bs._config, "get", _cfg_default(raising_keys={}))
    bs._WARNED_SUPPRESSED_PAID.clear()


def _cfg_default(raising_keys):
    def _get(key, default=None, repo=None):
        return raising_keys.get(key, default)
    return _get


@pytest.mark.parametrize("env_var", ["ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "ATLASCLOUD_API_KEY", "ATLAS_CLOUD_API_KEY", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY"])
def test_bare_paid_key_does_not_auto_select(monkeypatch, env_var):
    """A bare paid-cloud key must NOT auto-enable that provider."""
    monkeypatch.setenv(env_var, "sk-fake-key")
    assert bs.get_provider_name() is None


def test_remote_openai_base_suppressed(monkeypatch):
    """Default (remote) OpenAI base must not auto-bill."""
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    assert bs.get_provider_name() is None


def test_local_openai_base_still_auto_selects(monkeypatch):
    """A local OpenAI-compatible endpoint (Ollama/LM Studio) is free — still auto."""
    monkeypatch.setenv("OPENAI_API_BASE", "http://localhost:11434/v1")
    assert bs.get_provider_name() == "openai"


def test_explicit_provider_still_honored(monkeypatch):
    """Naming the provider IS the opt-in and must still work with a bare key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key")
    monkeypatch.setenv("JCODEMUNCH_SUMMARIZER_PROVIDER", "anthropic")
    assert bs.get_provider_name() == "anthropic"


def test_env_opt_in_re_enables_auto(monkeypatch):
    """JCODEMUNCH_ALLOW_PAID_SUMMARIES=1 restores legacy auto-select."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key")
    monkeypatch.setenv("JCODEMUNCH_ALLOW_PAID_SUMMARIES", "1")
    assert bs.get_provider_name() == "anthropic"


def test_env_opt_in_enables_atlascloud_auto_detect(monkeypatch):
    """Atlas Cloud follows the same paid-cloud opt-in guard as hosted providers."""
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "atlas-test-key")
    monkeypatch.setenv("JCODEMUNCH_ALLOW_PAID_SUMMARIES", "1")
    assert bs.get_provider_name() == "atlascloud"


def test_config_opt_in_re_enables_auto(monkeypatch):
    """allow_paid_summaries config key restores legacy auto-select."""
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")
    monkeypatch.setattr(bs._config, "get", _cfg_default({"allow_paid_summaries": True}))
    assert bs.get_provider_name() == "gemini"


def test_create_summarizer_returns_none_on_bare_paid_key(monkeypatch):
    """End-to-end: no summarizer object is built from a bare paid key in auto mode."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key")
    # use_ai_summaries default is "auto" via config.get default.
    assert bs._create_summarizer() is None
