"""The OpenAI-compatible summarizer's default model follows the base URL, and
`config --check` reports the model the runtime sends.

`_create_summarizer` sent the hosted default (`OPENAI_DEFAULT_MODEL`) to every
base URL, including a local endpoint, while `config --check` reported
`qwen3-coder` whenever OPENAI_API_BASE was set. A local-endpoint user with no
model configured was shown one model and sent another, and the one sent is a
hosted OpenAI model a local server does not serve. `openai_default_model` is
now the one rule, and both read it.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.summarizer import batch_summarize as bs


@pytest.mark.parametrize(
    "base",
    [
        None,
        "",
        "https://api.openai.com/v1",
        "https://api.openai.com/v1/",
        "HTTPS://API.OPENAI.COM/v1",
        "  https://api.openai.com/v1  ",
        "https://api.openai.com./v1",
        "https://eu.api.openai.com/v1",
    ],
)
def test_the_hosted_api_gets_the_hosted_default(base):
    assert bs.openai_default_model(base) == bs.OPENAI_DEFAULT_MODEL


@pytest.mark.parametrize(
    "base",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:8080/v1",
        "https://llm.internal.example/v1",
        "https://notapi.openai.com.evil.example/v1",
        "https://xapi.openai.com/v1",
    ],
)
def test_any_other_base_gets_the_local_default(base):
    assert bs.openai_default_model(base) == bs.OPENAI_LOCAL_DEFAULT_MODEL


def test_the_local_default_is_not_a_hosted_model():
    assert bs.OPENAI_LOCAL_DEFAULT_MODEL != bs.OPENAI_DEFAULT_MODEL


def test_a_local_endpoint_is_sent_the_local_default(monkeypatch):
    """The runtime half: no OPENAI_MODEL, no summarizer_model, a local base."""
    for k in ("OPENAI_MODEL", "JCODEMUNCH_SUMMARIZER_MODEL", "JCODEMUNCH_SUMMARIZER_PROVIDER",
              "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY",
              "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_BASE", "http://localhost:11434/v1")
    monkeypatch.setattr(bs, "get_model_name", lambda repo=None: "")
    monkeypatch.setattr(bs, "get_provider_name", lambda repo=None: "openai")
    sent = {}

    class _Captured:
        client = object()

    def fake_make(api_key, base_url, model, repo=None):
        sent["model"] = model
        return _Captured()

    monkeypatch.setattr(bs, "_make_openai_compat", fake_make)
    assert bs._create_summarizer() is not None
    assert sent["model"] != bs.OPENAI_DEFAULT_MODEL
    assert sent["model"] == bs.OPENAI_LOCAL_DEFAULT_MODEL


def test_an_explicit_model_still_wins(monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_MODEL", "my-own-model")
    monkeypatch.setattr(bs, "get_model_name", lambda repo=None: "")
    monkeypatch.setattr(bs, "get_provider_name", lambda repo=None: "openai")
    sent = {}

    class _Captured:
        client = object()

    def fake_make(api_key, base_url, model, repo=None):
        sent["model"] = model
        return _Captured()

    monkeypatch.setattr(bs, "_make_openai_compat", fake_make)
    bs._create_summarizer()
    assert sent["model"] == "my-own-model"
