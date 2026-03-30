"""Tests for summarizer module."""

import pytest
from unittest.mock import MagicMock, patch
from jcodemunch_mcp.parser import Symbol
from jcodemunch_mcp.summarizer import (
    extract_summary_from_docstring,
    get_provider_name,
    signature_fallback,
    summarize_symbols_simple,
    GeminiBatchSummarizer,
    OpenAIBatchSummarizer,
)
from jcodemunch_mcp.summarizer.batch_summarize import _create_summarizer, get_model_name


def test_extract_summary_from_docstring_simple():
    """Test extracting first sentence from docstring."""
    doc = "Do something cool.\n\nMore details here."
    assert extract_summary_from_docstring(doc) == "Do something cool."


def test_extract_summary_from_docstring_no_period():
    """Test extracting summary without period."""
    doc = "Do something cool"
    assert extract_summary_from_docstring(doc) == "Do something cool"


def test_extract_summary_from_docstring_empty():
    """Test extracting from empty docstring."""
    assert extract_summary_from_docstring("") == ""
    assert extract_summary_from_docstring("   ") == ""


def test_signature_fallback_function():
    """Test signature fallback for functions."""
    sym = Symbol(
        id="test::foo",
        file="test.py",
        name="foo",
        qualified_name="foo",
        kind="function",
        language="python",
        signature="def foo(x: int) -> str:",
    )
    assert signature_fallback(sym) == "def foo(x: int) -> str:"


def test_signature_fallback_class():
    """Test signature fallback for classes."""
    sym = Symbol(
        id="test::MyClass",
        file="test.py",
        name="MyClass",
        qualified_name="MyClass",
        kind="class",
        language="python",
        signature="class MyClass(Base):",
    )
    assert signature_fallback(sym) == "Class MyClass"


def test_signature_fallback_constant():
    """Test signature fallback for constants."""
    sym = Symbol(
        id="test::MAX_SIZE",
        file="test.py",
        name="MAX_SIZE",
        qualified_name="MAX_SIZE",
        kind="constant",
        language="python",
        signature="MAX_SIZE = 100",
    )
    assert signature_fallback(sym) == "Constant MAX_SIZE"


def test_simple_summarize_uses_docstring():
    """Test that summarize uses docstring when available."""
    symbols = [
        Symbol(
            id="test::foo",
            file="test.py",
            name="foo",
            qualified_name="foo",
            kind="function",
            language="python",
            signature="def foo():",
            docstring="Does something useful.",
        )
    ]

    result = summarize_symbols_simple(symbols)
    assert result[0].summary == "Does something useful."


def test_simple_summarize_fallback_to_signature():
    """Test fallback to signature when no docstring."""
    symbols = [
        Symbol(
            id="test::foo",
            file="test.py",
            name="foo",
            qualified_name="foo",
            kind="function",
            language="python",
            signature="def foo(x: int) -> str:",
            docstring="",
        )
    ]

    result = summarize_symbols_simple(symbols)
    assert "def foo" in result[0].summary


def test_anthropic_summarizer_base_url():
    """BatchSummarizer passes ANTHROPIC_BASE_URL to Anthropic client when set."""
    import sys

    mock_anthropic_module = MagicMock()
    mock_client = MagicMock()
    mock_anthropic_module.Anthropic.return_value = mock_client

    with patch.dict(sys.modules, {"anthropic": mock_anthropic_module}):
        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "sk-test-key",
                "ANTHROPIC_BASE_URL": "https://proxy.example.com/v1",
                "JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER": "1",
            },
            clear=True,
        ):
            # Set config value directly (module already imported)
            from jcodemunch_mcp import config as _cfg_module
            _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = True
            from jcodemunch_mcp.summarizer.batch_summarize import BatchSummarizer

            summarizer = BatchSummarizer()

    mock_anthropic_module.Anthropic.assert_called_once_with(
        api_key="sk-test-key",
        base_url="https://proxy.example.com/v1",
    )
    assert summarizer.client is mock_client


def test_anthropic_summarizer_no_base_url():
    """BatchSummarizer omits base_url when ANTHROPIC_BASE_URL is not set."""
    import sys

    mock_anthropic_module = MagicMock()
    mock_client = MagicMock()
    mock_anthropic_module.Anthropic.return_value = mock_client

    with patch.dict(sys.modules, {"anthropic": mock_anthropic_module}):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test-key"}, clear=True):
            from jcodemunch_mcp.summarizer.batch_summarize import BatchSummarizer

            summarizer = BatchSummarizer()

    mock_anthropic_module.Anthropic.assert_called_once_with(api_key="sk-test-key")
    assert summarizer.client is mock_client


def test_gemini_summarizer_no_api_key():
    """GeminiBatchSummarizer falls back to signature when no API key is set."""
    with patch.dict("os.environ", {}, clear=True):
        summarizer = GeminiBatchSummarizer()
        assert summarizer.client is None

    symbols = [
        Symbol(
            id="test::bar",
            file="test.py",
            name="bar",
            qualified_name="bar",
            kind="function",
            language="python",
            signature="def bar():",
        )
    ]
    summarizer.summarize_batch(symbols)
    assert symbols[0].summary == "def bar():"


def test_gemini_summarizer_with_mock_client():
    """GeminiBatchSummarizer uses Gemini response when client is available."""
    mock_response = MagicMock()
    mock_response.text = "1. Computes the sum of two integers."

    mock_client = MagicMock()
    mock_client.generate_content.return_value = mock_response

    summarizer = GeminiBatchSummarizer()
    summarizer.client = mock_client

    symbols = [
        Symbol(
            id="test::add",
            file="test.py",
            name="add",
            qualified_name="add",
            kind="function",
            language="python",
            signature="def add(a: int, b: int) -> int:",
        )
    ]
    summarizer.summarize_batch(symbols)
    assert symbols[0].summary == "Computes the sum of two integers."


def test_get_provider_name_explicit_values(monkeypatch):
    """Explicit provider selection should win over auto-detect."""
    for provider in ("anthropic", "gemini", "openai", "minimax", "glm"):
        monkeypatch.setenv("JCODEMUNCH_SUMMARIZER_PROVIDER", provider)
        assert get_provider_name() == provider


def test_get_provider_name_none_disables(monkeypatch):
    """Explicit none should disable AI providers."""
    monkeypatch.setenv("JCODEMUNCH_SUMMARIZER_PROVIDER", "none")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert get_provider_name() is None


def test_get_provider_name_unknown_falls_back_to_auto(monkeypatch):
    """Unknown explicit values should fall back to auto-detection."""
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_BASE", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JCODEMUNCH_SUMMARIZER_PROVIDER", "unknown-provider")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    assert get_provider_name() == "minimax"


def test_get_provider_name_auto_detect_priority(monkeypatch):
    """Auto-detect should follow Anthropic -> Gemini -> OpenAI -> MiniMax -> GLM."""
    for key in (
        "JCODEMUNCH_SUMMARIZER_PROVIDER",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_BASE",
        "MINIMAX_API_KEY",
        "ZHIPUAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_BASE", "http://localhost:11434/v1")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    assert get_provider_name() == "openai"


def test_get_provider_name_auto_detect_minimax(monkeypatch):
    """MiniMax should be detected when higher-priority providers are absent."""
    for key in (
        "JCODEMUNCH_SUMMARIZER_PROVIDER",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_BASE",
        "ZHIPUAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    assert get_provider_name() == "minimax"


def test_get_provider_name_auto_detect_glm(monkeypatch):
    """GLM should be detected when it is the only configured provider."""
    for key in (
        "JCODEMUNCH_SUMMARIZER_PROVIDER",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_BASE",
        "MINIMAX_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    assert get_provider_name() == "glm"


def test_get_provider_name_auto_detect_openrouter(monkeypatch):
    """OpenRouter should be detected when it is the only configured provider."""
    for key in (
        "JCODEMUNCH_SUMMARIZER_PROVIDER",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_BASE",
        "MINIMAX_API_KEY",
        "ZHIPUAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    assert get_provider_name() == "openrouter"


def test_create_summarizer_explicit_provider_missing_key_returns_none(monkeypatch):
    """Explicit minimax/glm/openrouter provider selection should degrade gracefully without keys."""
    monkeypatch.setenv("JCODEMUNCH_SUMMARIZER_PROVIDER", "minimax")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    assert _create_summarizer() is None

    monkeypatch.setenv("JCODEMUNCH_SUMMARIZER_PROVIDER", "glm")
    monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)
    assert _create_summarizer() is None

    monkeypatch.setenv("JCODEMUNCH_SUMMARIZER_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert _create_summarizer() is None


def test_openai_summarizer_no_api_base():
    """OpenAIBatchSummarizer falls back to signature when no API base is set."""
    with patch.dict("os.environ", {}, clear=True):
        summarizer = OpenAIBatchSummarizer()
        assert summarizer.client is None

    symbols = [
        Symbol(
            id="test::bar",
            file="test.py",
            name="bar",
            qualified_name="bar",
            kind="function",
            language="python",
            signature="def bar():",
        )
    ]
    summarizer.summarize_batch(symbols)
    assert symbols[0].summary == "def bar():"


def test_openai_summarizer_with_mock_client():
    """OpenAIBatchSummarizer parses the response from OpenAI compatible endpoints."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "1. Multiplies two integers together."}}]
    }

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch.dict(
        "os.environ",
        {"OPENAI_API_BASE": "http://localhost:11434/v1", "OPENAI_MODEL": "qwen3-coder"},
        clear=True,
    ), patch.object(OpenAIBatchSummarizer, "_init_client"):
        summarizer = OpenAIBatchSummarizer()
        summarizer.client = mock_client

    symbols = [
        Symbol(
            id="test::multiply",
            file="test.py",
            name="multiply",
            qualified_name="multiply",
            kind="function",
            language="python",
            signature="def multiply(a: int, b: int) -> int:",
        )
    ]
    summarizer.summarize_batch(symbols)

    # Verify the endpoint URL used
    mock_client.post.assert_called_once()
    assert (
        mock_client.post.call_args[0][0] == "http://localhost:11434/v1/chat/completions"
    )
    assert symbols[0].summary == "Multiplies two integers together."


def test_openai_summarizer_responses_api_mode():
    """OpenAIBatchSummarizer supports the Responses API when configured."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "output": [
            {
                "content": [
                    {
                        "type": "output_text",
                        "text": "1. Multiplies two integers together.",
                    }
                ]
            }
        ]
    }

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch.dict(
        "os.environ",
        {
            "OPENAI_API_BASE": "http://localhost:11434/v1",
            "OPENAI_MODEL": "gpt-5.4-mini",
            "OPENAI_WIRE_API": "responses",
        },
        clear=True,
    ):
        with patch.object(OpenAIBatchSummarizer, "_init_client"):
            summarizer = OpenAIBatchSummarizer()
        summarizer.client = mock_client

    symbols = [
        Symbol(
            id="test::multiply",
            file="test.py",
            name="multiply",
            qualified_name="multiply",
            kind="function",
            language="python",
            signature="def multiply(a: int, b: int) -> int:",
        )
    ]
    summarizer.summarize_batch(symbols)

    mock_client.post.assert_called_once()
    assert mock_client.post.call_args[0][0] == "http://localhost:11434/v1/responses"
    assert mock_client.post.call_args[1]["json"] == {
        "model": "gpt-5.4-mini",
        "input": mock_client.post.call_args[1]["json"]["input"],
        "max_output_tokens": 500,
        "temperature": 0.0,
    }
    assert (
        "Summarize each code symbol" in mock_client.post.call_args[1]["json"]["input"]
    )
    assert symbols[0].summary == "Multiplies two integers together."


def test_openai_summarizer_invalid_wire_api_falls_back():
    """OpenAIBatchSummarizer falls back safely for unsupported wire APIs."""
    mock_client = MagicMock()

    with patch.dict(
        "os.environ",
        {
            "OPENAI_API_BASE": "http://localhost:11434/v1",
            "OPENAI_WIRE_API": "bogus",
        },
        clear=True,
    ):
        with patch.object(OpenAIBatchSummarizer, "_init_client"):
            summarizer = OpenAIBatchSummarizer()
        summarizer.client = mock_client

    symbols = [
        Symbol(
            id="test::fallback",
            file="test.py",
            name="fallback",
            qualified_name="fallback",
            kind="function",
            language="python",
            signature="def fallback():",
        )
    ]
    summarizer.summarize_batch(symbols)

    mock_client.post.assert_not_called()
    assert symbols[0].summary == "def fallback():"


def test_openai_summarizer_responses_http_error_falls_back():
    """Responses mode falls back to signature summaries on HTTP errors."""
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = RuntimeError("400 Bad Request")

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch.dict(
        "os.environ",
        {
            "OPENAI_API_BASE": "http://localhost:11434/v1",
            "OPENAI_WIRE_API": "responses",
        },
        clear=True,
    ):
        with patch.object(OpenAIBatchSummarizer, "_init_client"):
            summarizer = OpenAIBatchSummarizer()
        summarizer.client = mock_client

    symbols = [
        Symbol(
            id="test::http_error",
            file="test.py",
            name="http_error",
            qualified_name="http_error",
            kind="function",
            language="python",
            signature="def http_error():",
        )
    ]
    summarizer.summarize_batch(symbols)

    mock_client.post.assert_called_once()
    assert symbols[0].summary == "def http_error():"


def test_openai_summarizer_responses_missing_text_falls_back():
    """Responses mode falls back when the response contains no text output."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"output": [{"content": [{"type": "tool_call"}]}]}

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch.dict(
        "os.environ",
        {
            "OPENAI_API_BASE": "http://localhost:11434/v1",
            "OPENAI_WIRE_API": "responses",
        },
        clear=True,
    ):
        with patch.object(OpenAIBatchSummarizer, "_init_client"):
            summarizer = OpenAIBatchSummarizer()
        summarizer.client = mock_client

    symbols = [
        Symbol(
            id="test::missing_text",
            file="test.py",
            name="missing_text",
            qualified_name="missing_text",
            kind="function",
            language="python",
            signature="def missing_text():",
        )
    ]
    summarizer.summarize_batch(symbols)

    mock_client.post.assert_called_once()
    assert symbols[0].summary == "def missing_text():"


def test_openai_summarizer_responses_partial_parse_falls_back_per_symbol():
    """Responses mode preserves per-symbol fallback when fewer summaries are returned."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "output_text": "1. Handles the first function only."
    }

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch.dict(
        "os.environ",
        {
            "OPENAI_API_BASE": "http://localhost:11434/v1",
            "OPENAI_WIRE_API": "responses",
        },
        clear=True,
    ):
        with patch.object(OpenAIBatchSummarizer, "_init_client"):
            summarizer = OpenAIBatchSummarizer()
        summarizer.client = mock_client

    symbols = [
        Symbol(
            id="test::first",
            file="test.py",
            name="first",
            qualified_name="first",
            kind="function",
            language="python",
            signature="def first():",
        ),
        Symbol(
            id="test::second",
            file="test.py",
            name="second",
            qualified_name="second",
            kind="function",
            language="python",
            signature="def second():",
        ),
    ]
    summarizer.summarize_batch(symbols, batch_size=2)

    mock_client.post.assert_called_once()
    assert symbols[0].summary == "Handles the first function only."
    assert symbols[1].summary == "def second():"


def test_openai_summarizer_explicit_openai_provider_uses_default_api_base():
    """Explicit openai provider should default to the hosted OpenAI base URL."""
    from jcodemunch_mcp import config as _cfg_module

    _sentinel = object()
    _orig = _cfg_module._GLOBAL_CONFIG.get("allow_remote_summarizer", _sentinel)
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "1. Handles hosted OpenAI requests."}}]
    }

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch.dict(
        "os.environ",
        {
            "JCODEMUNCH_SUMMARIZER_PROVIDER": "openai",
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_MODEL": "gpt-4o-mini",
            "JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER": "1",
        },
        clear=True,
    ):
        try:
            _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = True
            with patch.object(OpenAIBatchSummarizer, "_init_client"):
                summarizer = OpenAIBatchSummarizer(
                    model="gpt-4o-mini",
                    api_base="https://api.openai.com/v1",
                    api_key="sk-test",
                )
            summarizer.client = mock_client
        finally:
            if _orig is _sentinel:
                _cfg_module._GLOBAL_CONFIG.pop("allow_remote_summarizer", None)
            else:
                _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = _orig

    symbols = [
        Symbol(
            id="test::hosted",
            file="test.py",
            name="hosted",
            qualified_name="hosted",
            kind="function",
            language="python",
            signature="def hosted():",
        )
    ]
    summarizer.summarize_batch(symbols)

    mock_client.post.assert_called_once()
    assert mock_client.post.call_args[0][0] == "https://api.openai.com/v1/chat/completions"
    assert symbols[0].summary == "Handles hosted OpenAI requests."


def test_openai_summarizer_minimax_provider_defaults():
    """MiniMax should use its fixed API base and model."""
    from jcodemunch_mcp import config as _cfg_module

    _sentinel = object()
    _orig = _cfg_module._GLOBAL_CONFIG.get("allow_remote_summarizer", _sentinel)
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "1. Uses the MiniMax endpoint."}}]
    }

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch.dict(
        "os.environ",
        {
            "MINIMAX_API_KEY": "test-key",
            "JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER": "1",
        },
        clear=True,
    ):
        try:
            _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = True
            with patch.object(OpenAIBatchSummarizer, "_init_client"):
                summarizer = OpenAIBatchSummarizer(
                    model="minimax-m2.7",
                    api_base="https://api.minimax.io/v1",
                    api_key="test-key",
                )
            summarizer.client = mock_client
        finally:
            if _orig is _sentinel:
                _cfg_module._GLOBAL_CONFIG.pop("allow_remote_summarizer", None)
            else:
                _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = _orig

    symbols = [
        Symbol(
            id="test::minimax",
            file="test.py",
            name="minimax",
            qualified_name="minimax",
            kind="function",
            language="python",
            signature="def minimax():",
        )
    ]
    summarizer.summarize_batch(symbols)

    mock_client.post.assert_called_once()
    assert mock_client.post.call_args[0][0] == "https://api.minimax.io/v1/chat/completions"
    assert mock_client.post.call_args[1]["json"]["model"] == "minimax-m2.7"
    assert symbols[0].summary == "Uses the MiniMax endpoint."


def test_openai_summarizer_glm_provider_defaults():
    """GLM should use its fixed API base and model."""
    from jcodemunch_mcp import config as _cfg_module

    _sentinel = object()
    _orig = _cfg_module._GLOBAL_CONFIG.get("allow_remote_summarizer", _sentinel)
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "1. Uses the GLM endpoint."}}]
    }

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch.dict(
        "os.environ",
        {
            "ZHIPUAI_API_KEY": "test-key",
            "JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER": "1",
        },
        clear=True,
    ):
        try:
            _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = True
            with patch.object(OpenAIBatchSummarizer, "_init_client"):
                summarizer = OpenAIBatchSummarizer(
                    model="glm-5",
                    api_base="https://api.z.ai/api/paas/v4/",
                    api_key="test-key",
                )
            summarizer.client = mock_client
        finally:
            if _orig is _sentinel:
                _cfg_module._GLOBAL_CONFIG.pop("allow_remote_summarizer", None)
            else:
                _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = _orig

    symbols = [
        Symbol(
            id="test::glm",
            file="test.py",
            name="glm",
            qualified_name="glm",
            kind="function",
            language="python",
            signature="def glm():",
        )
    ]
    summarizer.summarize_batch(symbols)

    mock_client.post.assert_called_once()
    assert mock_client.post.call_args[0][0] == "https://api.z.ai/api/paas/v4/chat/completions"
    assert mock_client.post.call_args[1]["json"]["model"] == "glm-5"
    assert symbols[0].summary == "Uses the GLM endpoint."


def test_openai_summarizer_openrouter_provider_defaults():
    """OpenRouter should use its fixed API base and default free model."""
    from jcodemunch_mcp import config as _cfg_module

    _sentinel = object()
    _orig = _cfg_module._GLOBAL_CONFIG.get("allow_remote_summarizer", _sentinel)
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "1. Uses the OpenRouter endpoint."}}]
    }

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch.dict(
        "os.environ",
        {
            "OPENROUTER_API_KEY": "test-key",
            "JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER": "1",
        },
        clear=True,
    ):
        try:
            _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = True
            with patch.object(OpenAIBatchSummarizer, "_init_client"):
                summarizer = OpenAIBatchSummarizer(
                    model="meta-llama/llama-3.3-70b-instruct:free",
                    api_base="https://openrouter.ai/api/v1",
                    api_key="test-key",
                )
            summarizer.client = mock_client
        finally:
            if _orig is _sentinel:
                _cfg_module._GLOBAL_CONFIG.pop("allow_remote_summarizer", None)
            else:
                _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = _orig

    symbols = [
        Symbol(
            id="test::openrouter",
            file="test.py",
            name="openrouter",
            qualified_name="openrouter",
            kind="function",
            language="python",
            signature="def openrouter():",
        )
    ]
    summarizer.summarize_batch(symbols)

    mock_client.post.assert_called_once()
    assert mock_client.post.call_args[0][0] == "https://openrouter.ai/api/v1/chat/completions"
    assert mock_client.post.call_args[1]["json"]["model"] == "meta-llama/llama-3.3-70b-instruct:free"
    assert symbols[0].summary == "Uses the OpenRouter endpoint."


def test_openai_summarizer_remote_endpoint_requires_allow_flag():
    """Non-localhost OpenAI endpoints are ignored without the allow flag."""
    from jcodemunch_mcp import config as _cfg_module
    _sentinel = object()
    _orig = _cfg_module._GLOBAL_CONFIG.get("allow_remote_summarizer", _sentinel)
    try:
        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_BASE": "https://example.openai.azure.com/openai/v1",
                "OPENAI_WIRE_API": "responses",
            },
            clear=True,
        ):
            _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = False
            summarizer = OpenAIBatchSummarizer()
    finally:
        if _orig is _sentinel:
            _cfg_module._GLOBAL_CONFIG.pop("allow_remote_summarizer", None)
        else:
            _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = _orig

    assert summarizer.api_base is None
    assert summarizer.client is None

    symbols = [
        Symbol(
            id="test::remote",
            file="test.py",
            name="remote",
            qualified_name="remote",
            kind="function",
            language="python",
            signature="def remote():",
        )
    ]
    summarizer.summarize_batch(symbols)
    assert symbols[0].summary == "def remote():"


def test_openai_summarizer_timeout_config():
    """OpenAIBatchSummarizer configures custom timeouts via OPENAI_TIMEOUT."""
    # Test valid float parsing
    # The summarizer reads config.get("allow_remote_summarizer") — patch it
    # alongside the env vars so the non-localhost URL is accepted.
    # Mock httpx.Client to capture the timeout kwarg without creating a real SSL context.
    with patch.dict(
        "os.environ",
        {
            "OPENAI_API_BASE": "http://test",
            "OPENAI_TIMEOUT": "120.5",
        },
        clear=True,
    ), patch("jcodemunch_mcp.summarizer.batch_summarize._config.get",
             side_effect=lambda k, d=None: True if k == "allow_remote_summarizer" else d), \
         patch("httpx.Client") as mock_httpx:
        summarizer = OpenAIBatchSummarizer()
        assert summarizer.client is not None
        call_kwargs = mock_httpx.call_args
        assert call_kwargs[1]["timeout"] == 120.5

    # Test invalid string fallback
    with patch.dict(
        "os.environ",
        {
            "OPENAI_API_BASE": "http://test",
            "OPENAI_TIMEOUT": "invalid",
        },
        clear=True,
    ), patch("jcodemunch_mcp.summarizer.batch_summarize._config.get",
             side_effect=lambda k, d=None: True if k == "allow_remote_summarizer" else d), \
         patch("httpx.Client") as mock_httpx:
        summarizer = OpenAIBatchSummarizer()
        assert summarizer.client is not None
        call_kwargs = mock_httpx.call_args
        assert call_kwargs[1]["timeout"] == 60.0


# ---------------------------------------------------------------------------
# Tests for get_model_name() and tri-state use_ai_summaries
# ---------------------------------------------------------------------------


def test_get_model_name_returns_none_when_empty():
    """get_model_name() returns None when summarizer_model config is empty."""
    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None: "" if k == "summarizer_model" else d,
    ):
        assert get_model_name() is None


def test_get_model_name_returns_value_when_set():
    """get_model_name() returns the model string when summarizer_model is configured."""
    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None: "my-custom-model" if k == "summarizer_model" else d,
    ):
        assert get_model_name() == "my-custom-model"


def test_get_model_name_strips_whitespace():
    """get_model_name() strips surrounding whitespace from the model value."""
    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None: "  claude-haiku  " if k == "summarizer_model" else d,
    ):
        assert get_model_name() == "claude-haiku"


def test_get_model_name_returns_none_for_whitespace_only():
    """get_model_name returns None for whitespace-only config value."""
    from jcodemunch_mcp import config as _cfg_module
    with patch.object(_cfg_module, "_GLOBAL_CONFIG", {"summarizer_model": "   "}):
        assert get_model_name() is None


def test_create_summarizer_disabled_when_false():
    """_create_summarizer() returns None when use_ai_summaries is False (bool)."""
    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None: False if k == "use_ai_summaries" else d,
    ):
        assert _create_summarizer() is None


@pytest.mark.parametrize("falsy_val", ["false", "0", "no", "off"])
def test_create_summarizer_disabled_when_string_false(falsy_val):
    """_create_summarizer() returns None for each falsy string value of use_ai_summaries."""
    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None: falsy_val if k == "use_ai_summaries" else d,
    ):
        assert _create_summarizer() is None


def test_create_summarizer_auto_mode_no_providers(monkeypatch):
    """_create_summarizer() with use_ai_summaries='auto' returns None when no providers configured."""
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_BASE", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None: "auto" if k == "use_ai_summaries" else d,
    ):
        assert _create_summarizer() is None


def test_create_summarizer_auto_mode_detects_provider(monkeypatch):
    """_create_summarizer() with use_ai_summaries='auto' picks up auto-detected provider."""
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_BASE", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    from jcodemunch_mcp import config as _cfg_module
    _sentinel = object()
    _orig = _cfg_module._GLOBAL_CONFIG.get("allow_remote_summarizer", _sentinel)
    try:
        _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = True
        with patch(
            "jcodemunch_mcp.summarizer.batch_summarize._config.get",
            side_effect=lambda k, d=None: (
                "auto" if k == "use_ai_summaries"
                else "" if k == "summarizer_model"
                else True if k == "allow_remote_summarizer"
                else d
            ),
        ):
            s = _create_summarizer()
    finally:
        if _orig is _sentinel:
            _cfg_module._GLOBAL_CONFIG.pop("allow_remote_summarizer", None)
        else:
            _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = _orig
    # GLM provider — OpenAIBatchSummarizer with the glm endpoint
    assert s is not None
    assert isinstance(s, OpenAIBatchSummarizer)
    assert s.model == "glm-5"


def test_create_summarizer_model_override_applied_to_glm(monkeypatch):
    """summarizer_model config override is applied to the created GLM summarizer."""
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_BASE", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    from jcodemunch_mcp import config as _cfg_module
    _sentinel = object()
    _orig = _cfg_module._GLOBAL_CONFIG.get("allow_remote_summarizer", _sentinel)
    try:
        _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = True
        with patch(
            "jcodemunch_mcp.summarizer.batch_summarize._config.get",
            side_effect=lambda k, d=None: (
                "auto" if k == "use_ai_summaries"
                else "glm-6-turbo" if k == "summarizer_model"
                else True if k == "allow_remote_summarizer"
                else d
            ),
        ):
            s = _create_summarizer()
    finally:
        if _orig is _sentinel:
            _cfg_module._GLOBAL_CONFIG.pop("allow_remote_summarizer", None)
        else:
            _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = _orig
    assert s is not None
    assert s.model == "glm-6-turbo"


def test_create_summarizer_explicit_true_no_provider_warns_and_autodetects(monkeypatch, caplog):
    """use_ai_summaries=True with no summarizer_provider logs warning and falls back to auto-detect."""
    import logging
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_BASE", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None: (
            True if k == "use_ai_summaries"
            else "" if k in ("summarizer_provider", "summarizer_model")
            else d
        ),
    ), caplog.at_level(logging.WARNING, logger="jcodemunch_mcp.summarizer.batch_summarize"):
        result = _create_summarizer()
    assert result is None
    assert "summarizer_provider is not set" in caplog.text


# ---------------------------------------------------------------------------
# Per-provider model override tests
# ---------------------------------------------------------------------------


def test_anthropic_model_override_via_config():
    """summarizer_model config takes priority over ANTHROPIC_MODEL env var for Anthropic."""
    import sys

    mock_anthropic_module = MagicMock()
    mock_client = MagicMock()
    mock_anthropic_module.Anthropic.return_value = mock_client

    with patch.dict(sys.modules, {"anthropic": mock_anthropic_module}):
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_MODEL": "claude-haiku-fallback"},
            clear=True,
        ):
            with patch(
                "jcodemunch_mcp.summarizer.batch_summarize._config.get",
                side_effect=lambda k, d=None: "claude-override-model" if k == "summarizer_model" else d,
            ):
                from jcodemunch_mcp.summarizer.batch_summarize import BatchSummarizer

                summarizer = BatchSummarizer()

    assert summarizer.model == "claude-override-model"
    assert summarizer.client is mock_client


def test_gemini_model_override_via_config():
    """summarizer_model config takes priority over GOOGLE_MODEL env var for Gemini."""
    import sys

    mock_genai_module = MagicMock()
    mock_genai_model_instance = MagicMock()
    mock_genai_module.GenerativeModel.return_value = mock_genai_model_instance

    # Build a google package mock that exposes generativeai as an attribute
    mock_google_pkg = MagicMock()
    mock_google_pkg.generativeai = mock_genai_module

    with patch.dict(
        sys.modules,
        {"google": mock_google_pkg, "google.generativeai": mock_genai_module},
    ):
        with patch.dict(
            "os.environ",
            {"GOOGLE_API_KEY": "gkey-test", "GOOGLE_MODEL": "gemini-fallback"},
            clear=True,
        ):
            with patch(
                "jcodemunch_mcp.summarizer.batch_summarize._config.get",
                side_effect=lambda k, d=None: "gemini-override-model" if k == "summarizer_model" else d,
            ):
                from jcodemunch_mcp.summarizer.batch_summarize import GeminiBatchSummarizer

                summarizer = GeminiBatchSummarizer()

    # The client must be the instance created with the override model
    assert summarizer.model == "gemini-override-model"
    mock_genai_module.GenerativeModel.assert_called_once_with("gemini-override-model")
    assert summarizer.client is mock_genai_model_instance


def test_openai_model_override_via_config(monkeypatch):
    """summarizer_model config is applied to _create_summarizer() for the OpenAI provider."""
    monkeypatch.setenv("OPENAI_API_BASE", "http://localhost:11434/v1")
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None: (
            "auto" if k == "use_ai_summaries"
            else "my-openai-override" if k == "summarizer_model"
            else d
        ),
    ):
        s = _create_summarizer()

    assert s is not None
    assert isinstance(s, OpenAIBatchSummarizer)
    assert s.model == "my-openai-override"


def test_minimax_model_override_via_config(monkeypatch):
    """summarizer_model config is applied to _create_summarizer() for the MiniMax provider."""
    monkeypatch.setenv("MINIMAX_API_KEY", "mm-test-key")
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_BASE", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    from jcodemunch_mcp import config as _cfg_module

    _sentinel = object()
    _orig = _cfg_module._GLOBAL_CONFIG.get("allow_remote_summarizer", _sentinel)
    try:
        _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = True
        with patch(
            "jcodemunch_mcp.summarizer.batch_summarize._config.get",
            side_effect=lambda k, d=None: (
                "auto" if k == "use_ai_summaries"
                else "minimax-m3" if k == "summarizer_model"
                else True if k == "allow_remote_summarizer"
                else d
            ),
        ):
            s = _create_summarizer()
    finally:
        if _orig is _sentinel:
            _cfg_module._GLOBAL_CONFIG.pop("allow_remote_summarizer", None)
        else:
            _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = _orig

    assert s is not None
    assert isinstance(s, OpenAIBatchSummarizer)
    assert s.model == "minimax-m3"
