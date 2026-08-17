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
    for provider in ("anthropic", "gemini", "openai", "minimax", "glm", "orcarouter"):
        monkeypatch.setenv("JCODEMUNCH_SUMMARIZER_PROVIDER", provider)
        assert get_provider_name() == provider


def test_get_provider_name_none_disables(monkeypatch):
    """Explicit none should disable AI providers."""
    monkeypatch.setenv("JCODEMUNCH_SUMMARIZER_PROVIDER", "none")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert get_provider_name() is None


def test_get_provider_name_unknown_falls_back_to_auto(monkeypatch):
    """Unknown explicit values should fall back to auto-detection."""
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_BASE", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY", "ORCAROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JCODEMUNCH_SUMMARIZER_PROVIDER", "unknown-provider")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setenv("JCODEMUNCH_ALLOW_PAID_SUMMARIES", "1")  # paid auto-detect is opt-in (v1.108.128)
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
    monkeypatch.setenv("JCODEMUNCH_ALLOW_PAID_SUMMARIES", "1")  # paid auto-detect is opt-in (v1.108.128)
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
    monkeypatch.setenv("JCODEMUNCH_ALLOW_PAID_SUMMARIES", "1")  # paid auto-detect is opt-in (v1.108.128)
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
        "ORCAROUTER_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("JCODEMUNCH_ALLOW_PAID_SUMMARIES", "1")  # paid auto-detect is opt-in (v1.108.128)
    assert get_provider_name() == "openrouter"


def test_get_provider_name_auto_detect_orcarouter(monkeypatch):
    """OrcaRouter should be detected when it is the only configured provider."""
    for key in (
        "JCODEMUNCH_SUMMARIZER_PROVIDER",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_BASE",
        "MINIMAX_API_KEY",
        "ZHIPUAI_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test")
    monkeypatch.setenv("JCODEMUNCH_ALLOW_PAID_SUMMARIES", "1")  # paid auto-detect is opt-in (v1.108.128)
    assert get_provider_name() == "orcarouter"


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

    monkeypatch.setenv("JCODEMUNCH_SUMMARIZER_PROVIDER", "orcarouter")
    monkeypatch.delenv("ORCAROUTER_API_KEY", raising=False)
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


def test_openai_summarizer_orcarouter_provider_defaults():
    """OrcaRouter should use its fixed API base and default model."""
    from jcodemunch_mcp import config as _cfg_module

    _sentinel = object()
    _orig = _cfg_module._GLOBAL_CONFIG.get("allow_remote_summarizer", _sentinel)
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "1. Uses the OrcaRouter endpoint."}}]
    }

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch.dict(
        "os.environ",
        {
            "ORCAROUTER_API_KEY": "sk-orca-test",
            "JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER": "1",
        },
        clear=True,
    ):
        try:
            _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = True
            with patch.object(OpenAIBatchSummarizer, "_init_client"):
                summarizer = OpenAIBatchSummarizer(
                    model="openai/gpt-4o-mini",
                    api_base="https://api.orcarouter.ai/v1",
                    api_key="sk-orca-test",
                )
            summarizer.client = mock_client
        finally:
            if _orig is _sentinel:
                _cfg_module._GLOBAL_CONFIG.pop("allow_remote_summarizer", None)
            else:
                _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = _orig

    symbols = [
        Symbol(
            id="test::orcarouter",
            file="test.py",
            name="orcarouter",
            qualified_name="orcarouter",
            kind="function",
            language="python",
            signature="def orcarouter():",
        )
    ]
    summarizer.summarize_batch(symbols)

    mock_client.post.assert_called_once()
    assert mock_client.post.call_args[0][0] == "https://api.orcarouter.ai/v1/chat/completions"
    assert mock_client.post.call_args[1]["json"]["model"] == "openai/gpt-4o-mini"
    assert symbols[0].summary == "Uses the OrcaRouter endpoint."


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
             side_effect=lambda k, d=None, **kwargs: True if k == "allow_remote_summarizer" else d), \
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
             side_effect=lambda k, d=None, **kwargs: True if k == "allow_remote_summarizer" else d), \
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
        side_effect=lambda k, d=None, **kwargs: "" if k == "summarizer_model" else d,
    ):
        assert get_model_name() is None


def test_get_model_name_returns_value_when_set():
    """get_model_name() returns the model string when summarizer_model is configured."""
    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None, **kwargs: "my-custom-model" if k == "summarizer_model" else d,
    ):
        assert get_model_name() == "my-custom-model"


# ---------------------------------------------------------------------------
# Tests for openai_extra_body / JCODEMUNCH_OPENAI_EXTRA_BODY (#323)
# ---------------------------------------------------------------------------


def _one_symbol(name="multiply", sig="def multiply(a: int, b: int) -> int:"):
    return Symbol(
        id=f"test::{name}",
        file="test.py",
        name=name,
        qualified_name=name,
        kind="function",
        language="python",
        signature=sig,
    )


def test_openai_extra_body_from_env_merged_into_chat_payload():
    """JCODEMUNCH_OPENAI_EXTRA_BODY is merged into the /chat/completions payload."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "1. Multiplies two integers together."}}]
    }
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch.dict(
        "os.environ",
        {
            "OPENAI_API_BASE": "http://localhost:11434/v1",
            "JCODEMUNCH_OPENAI_EXTRA_BODY": '{"chat_template_kwargs":{"enable_thinking":false}}',
        },
        clear=True,
    ), patch.object(OpenAIBatchSummarizer, "_init_client"):
        summarizer = OpenAIBatchSummarizer()
        summarizer.client = mock_client

    summarizer.summarize_batch([_one_symbol()])

    payload = mock_client.post.call_args[1]["json"]
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    # Standard payload keys are preserved.
    assert payload["messages"][0]["content"].startswith("Summarize each code symbol")


def test_openai_extra_body_from_config_merged_into_responses_payload():
    """openai_extra_body config is merged into the /responses payload too."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "output": [{"content": [{"type": "output_text", "text": "1. Does a thing."}]}]
    }
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    def fake_get(k, d=None, **kwargs):
        if k == "openai_extra_body":
            return {"reasoning": {"effort": "low"}}
        return d

    with patch.dict(
        "os.environ",
        {"OPENAI_API_BASE": "http://localhost:11434/v1", "OPENAI_WIRE_API": "responses"},
        clear=True,
    ), patch("jcodemunch_mcp.summarizer.batch_summarize._config.get", side_effect=fake_get):
        with patch.object(OpenAIBatchSummarizer, "_init_client"):
            summarizer = OpenAIBatchSummarizer()
        summarizer.client = mock_client

    summarizer.summarize_batch([_one_symbol()])

    payload = mock_client.post.call_args[1]["json"]
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["input"].startswith("Summarize each code symbol")


def test_openai_extra_body_config_wins_over_env_per_key():
    """When both env and config set the same key, the config value wins."""
    def fake_get(k, d=None, **kwargs):
        if k == "openai_extra_body":
            return {"chat_template_kwargs": {"enable_thinking": True}}
        return d

    with patch.dict(
        "os.environ",
        {"JCODEMUNCH_OPENAI_EXTRA_BODY": '{"chat_template_kwargs":{"enable_thinking":false},"foo":1}'},
        clear=True,
    ), patch("jcodemunch_mcp.summarizer.batch_summarize._config.get", side_effect=fake_get):
        from jcodemunch_mcp.summarizer.batch_summarize import _resolve_extra_body

        resolved = _resolve_extra_body()

    assert resolved == {"chat_template_kwargs": {"enable_thinking": True}, "foo": 1}


def test_openai_extra_body_invalid_json_ignored(caplog):
    """A malformed JCODEMUNCH_OPENAI_EXTRA_BODY is ignored, not fatal."""
    with patch.dict(
        "os.environ", {"JCODEMUNCH_OPENAI_EXTRA_BODY": "{not json"}, clear=True
    ), patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None, **kwargs: d,
    ):
        from jcodemunch_mcp.summarizer.batch_summarize import _resolve_extra_body

        with caplog.at_level("WARNING"):
            resolved = _resolve_extra_body()

    assert resolved == {}
    assert "not valid JSON" in caplog.text


def test_degradation_warning_fires_on_silent_fallback(caplog):
    """A successful response with no usable summaries triggers a degradation warning."""
    # 200 OK, but the model returned only reasoning prose — no numbered summaries.
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "<think>reasoning reasoning</think>"}}]
    }
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch.dict(
        "os.environ", {"OPENAI_API_BASE": "http://localhost:11434/v1"}, clear=True
    ), patch.object(OpenAIBatchSummarizer, "_init_client"):
        summarizer = OpenAIBatchSummarizer()
        summarizer.client = mock_client

    symbols = [_one_symbol(name=f"f{i}", sig=f"def f{i}():") for i in range(4)]
    with caplog.at_level("WARNING", logger="jcodemunch_mcp.summarizer.batch_summarize"):
        summarizer.summarize_batch(symbols)

    # Every symbol fell back to its signature ...
    assert [s.summary for s in symbols] == [f"def f{i}():" for i in range(4)]
    # ... and the run reported the silent degradation with the remedy hint.
    assert "fell back to generic signatures despite successful responses" in caplog.text
    assert "JCODEMUNCH_OPENAI_EXTRA_BODY" in caplog.text


def test_no_degradation_warning_when_summaries_usable(caplog):
    """Good responses do not trigger the degradation warning."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "1. Does thing one.\n2. Does thing two."}}]
    }
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch.dict(
        "os.environ", {"OPENAI_API_BASE": "http://localhost:11434/v1"}, clear=True
    ), patch.object(OpenAIBatchSummarizer, "_init_client"):
        summarizer = OpenAIBatchSummarizer()
        summarizer.client = mock_client

    symbols = [_one_symbol(name="a"), _one_symbol(name="b")]
    with caplog.at_level("WARNING", logger="jcodemunch_mcp.summarizer.batch_summarize"):
        summarizer.summarize_batch(symbols)

    assert symbols[0].summary == "Does thing one."
    assert symbols[1].summary == "Does thing two."
    assert "fell back to generic signatures" not in caplog.text


def test_openai_summarizer_circuit_breaker_trips():
    """OpenAI provider honors the circuit breaker.

    Regression: the OpenAIBatchSummarizer.summarize_batch override submitted
    _summarize_one_batch directly, bypassing _run_batch and its
    _circuit_broken check, so summarizer_max_failures never short-circuited
    the OpenAI provider the way it does for Anthropic/Gemini. After the
    default 3 consecutive failures, the remaining batches must short-circuit
    to signature fallback without further HTTP calls.
    """
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = RuntimeError("500 Server Error")
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    # OPENAI_CONCURRENCY=1 forces sequential batch execution so the breaker
    # trip point is deterministic; OPENAI_BATCH_SIZE=1 gives one symbol/batch.
    # Both are read inside summarize_batch(), so the call must run while the
    # patched environment is still in scope.
    symbols = [_one_symbol(name=f"f{i}", sig=f"def f{i}():") for i in range(10)]
    with patch.dict(
        "os.environ",
        {
            "OPENAI_API_BASE": "http://localhost:11434/v1",
            "OPENAI_BATCH_SIZE": "1",
            "OPENAI_CONCURRENCY": "1",
        },
        clear=True,
    ), patch.object(OpenAIBatchSummarizer, "_init_client"):
        summarizer = OpenAIBatchSummarizer()
        summarizer.client = mock_client
        summarizer.summarize_batch(symbols)

    # Breaker trips after 3 consecutive failures; the other 7 batches never
    # reach the endpoint.
    assert mock_client.post.call_count == 3
    assert summarizer._circuit_broken is True
    # Every symbol still received a signature fallback.
    assert [s.summary for s in symbols] == [f"def f{i}():" for i in range(10)]


def test_get_model_name_strips_whitespace():
    """get_model_name() strips surrounding whitespace from the model value."""
    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None, **kwargs: "  claude-haiku  " if k == "summarizer_model" else d,
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
        side_effect=lambda k, d=None, **kwargs: False if k == "use_ai_summaries" else d,
    ):
        assert _create_summarizer() is None


@pytest.mark.parametrize("falsy_val", ["false", "0", "no", "off"])
def test_create_summarizer_disabled_when_string_false(falsy_val):
    """_create_summarizer() returns None for each falsy string value of use_ai_summaries."""
    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None, **kwargs: falsy_val if k == "use_ai_summaries" else d,
    ):
        assert _create_summarizer() is None


def test_create_summarizer_auto_mode_no_providers(monkeypatch):
    """_create_summarizer() with use_ai_summaries='auto' returns None when no providers configured."""
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_BASE", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY", "ORCAROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None, **kwargs: "auto" if k == "use_ai_summaries" else d,
    ):
        assert _create_summarizer() is None


def test_create_summarizer_auto_mode_detects_provider(monkeypatch):
    """_create_summarizer() with use_ai_summaries='auto' picks up auto-detected provider."""
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_BASE", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY", "ORCAROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    monkeypatch.setenv("JCODEMUNCH_ALLOW_PAID_SUMMARIES", "1")  # paid auto-detect is opt-in (v1.108.128)
    from jcodemunch_mcp import config as _cfg_module
    _sentinel = object()
    _orig = _cfg_module._GLOBAL_CONFIG.get("allow_remote_summarizer", _sentinel)
    try:
        _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = True
        with patch(
            "jcodemunch_mcp.summarizer.batch_summarize._config.get",
            side_effect=lambda k, d=None, **kwargs: (
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
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_BASE", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY", "ORCAROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    monkeypatch.setenv("JCODEMUNCH_ALLOW_PAID_SUMMARIES", "1")  # paid auto-detect is opt-in (v1.108.128)
    from jcodemunch_mcp import config as _cfg_module
    _sentinel = object()
    _orig = _cfg_module._GLOBAL_CONFIG.get("allow_remote_summarizer", _sentinel)
    try:
        _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = True
        with patch(
            "jcodemunch_mcp.summarizer.batch_summarize._config.get",
            side_effect=lambda k, d=None, **kwargs: (
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
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_BASE", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY", "ORCAROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None, **kwargs: (
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
                side_effect=lambda k, d=None, **kwargs: "claude-override-model" if k == "summarizer_model" else d,
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
                side_effect=lambda k, d=None, **kwargs: "gemini-override-model" if k == "summarizer_model" else d,
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
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY", "ORCAROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None, **kwargs: (
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
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_BASE", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY", "ORCAROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JCODEMUNCH_ALLOW_PAID_SUMMARIES", "1")  # paid auto-detect is opt-in (v1.108.128)

    from jcodemunch_mcp import config as _cfg_module

    _sentinel = object()
    _orig = _cfg_module._GLOBAL_CONFIG.get("allow_remote_summarizer", _sentinel)
    try:
        _cfg_module._GLOBAL_CONFIG["allow_remote_summarizer"] = True
        with patch(
            "jcodemunch_mcp.summarizer.batch_summarize._config.get",
            side_effect=lambda k, d=None, **kwargs: (
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


def test_summarizer_model_config_beats_openai_model_env(monkeypatch):
    """summarizer_model config takes priority over OPENAI_MODEL env var in OpenAI provider."""
    monkeypatch.setenv("OPENAI_API_BASE", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_MODEL", "env-model-should-lose")
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY", "ORCAROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None, **kwargs: (
            "auto" if k == "use_ai_summaries"
            else "config-model-wins" if k == "summarizer_model"
            else d
        ),
    ):
        s = _create_summarizer()

    assert s is not None
    assert isinstance(s, OpenAIBatchSummarizer)
    assert s.model == "config-model-wins", (
        f"Expected summarizer_model config to win over OPENAI_MODEL env var, got {s.model!r}"
    )


# ---------------------------------------------------------------------------
# Circuit breaker tests
# ---------------------------------------------------------------------------


def _make_symbols(n: int) -> list[Symbol]:
    """Create n test symbols without summaries."""
    return [
        Symbol(
            id=f"test::sym{i}",
            file="test.py",
            name=f"sym{i}",
            qualified_name=f"sym{i}",
            kind="function",
            language="python",
            signature=f"def sym{i}():",
        )
        for i in range(n)
    ]


def test_circuit_breaker_trips_after_consecutive_failures():
    """After 3 consecutive failures, remaining batches get signature fallback without API calls."""
    from jcodemunch_mcp.summarizer.batch_summarize import BaseSummarizer

    class FailingSummarizer(BaseSummarizer):
        call_count = 0

        def _summarize_one_batch(self, batch):
            self.call_count += 1
            try:
                raise RuntimeError("API is down")
            except Exception:
                self._record_failure()
                for sym in batch:
                    if not sym.summary:
                        sym.summary = signature_fallback(sym)

    summarizer = FailingSummarizer(client=object())  # non-None client
    symbols = _make_symbols(50)  # 50 symbols / batch_size=10 = 5 batches

    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None, **kwargs: 3 if k == "summarizer_max_failures" else 1 if k == "summarizer_concurrency" else d,
    ):
        summarizer.summarize_batch(symbols, batch_size=10)

    # Circuit should trip after 3 failures — batches 4 and 5 are skipped
    assert summarizer.call_count == 3
    assert summarizer._circuit_broken is True
    # All symbols should still have summaries (signature fallback)
    assert all(sym.summary for sym in symbols)


def test_circuit_breaker_resets_on_success():
    """A successful batch resets the failure counter."""
    from jcodemunch_mcp.summarizer.batch_summarize import BaseSummarizer

    class FlakySummarizer(BaseSummarizer):
        call_count = 0

        def _summarize_one_batch(self, batch):
            self.call_count += 1
            if self.call_count in (1, 2, 4, 5):
                # Fail batches 1, 2, 4, 5 — but succeed on 3 (resets counter)
                self._record_failure()
                for sym in batch:
                    if not sym.summary:
                        sym.summary = signature_fallback(sym)
                return
            for sym in batch:
                sym.summary = "AI summary"
            self._record_success()

    summarizer = FlakySummarizer(client=object())
    symbols = _make_symbols(60)  # 6 batches

    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None, **kwargs: 3 if k == "summarizer_max_failures" else 1 if k == "summarizer_concurrency" else d,
    ):
        summarizer.summarize_batch(symbols, batch_size=10)

    # All 6 batches attempted because batch 3 resets the counter
    # Failures: 1,2 (count=2), success 3 (reset), failures 4,5 (count=2), batch 6 runs
    assert summarizer.call_count == 6
    assert summarizer._circuit_broken is False


def test_circuit_breaker_disabled_when_zero():
    """Setting summarizer_max_failures=0 disables the circuit breaker."""
    from jcodemunch_mcp.summarizer.batch_summarize import BaseSummarizer

    class AlwaysFailSummarizer(BaseSummarizer):
        call_count = 0

        def _summarize_one_batch(self, batch):
            self.call_count += 1
            self._record_failure()
            for sym in batch:
                if not sym.summary:
                    sym.summary = signature_fallback(sym)

    summarizer = AlwaysFailSummarizer(client=object())
    symbols = _make_symbols(50)  # 5 batches

    with patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get",
        side_effect=lambda k, d=None, **kwargs: 0 if k == "summarizer_max_failures" else 1 if k == "summarizer_concurrency" else d,
    ):
        summarizer.summarize_batch(symbols, batch_size=10)

    # All 5 batches attempted — circuit never trips
    assert summarizer.call_count == 5
    assert summarizer._circuit_broken is False


# ---------------------------------------------------------------------------
# test_summarizer diagnostic tool
# ---------------------------------------------------------------------------


def test_test_summarizer_disabled():
    """test_summarizer returns disabled status when use_ai_summaries is false."""
    from jcodemunch_mcp.tools.test_summarizer import test_summarizer as run_test

    with patch(
        "jcodemunch_mcp.tools.test_summarizer._config.get",
        side_effect=lambda k, d=None, **kwargs: "false" if k == "use_ai_summaries" else d,
    ):
        result = run_test()

    assert result["status"] == "disabled"
    assert result["error"] is not None


def test_test_summarizer_no_provider():
    """test_summarizer returns no_provider when no API keys are set."""
    from jcodemunch_mcp.tools.test_summarizer import test_summarizer as run_test

    with patch(
        "jcodemunch_mcp.tools.test_summarizer._config.get",
        side_effect=lambda k, d=None, **kwargs: "auto" if k == "use_ai_summaries" else d,
    ), patch(
        "jcodemunch_mcp.tools.test_summarizer.get_provider_name",
        return_value=None,
    ):
        result = run_test()

    assert result["status"] == "no_provider"


def test_test_summarizer_ok():
    """test_summarizer returns ok when AI produces a real summary."""
    from jcodemunch_mcp.tools.test_summarizer import test_summarizer as run_test

    mock_summarizer = MagicMock()
    mock_summarizer.model = "test-model"

    def fake_summarize(symbols, batch_size=1):
        for sym in symbols:
            sym.summary = "Greets the user by name."
        return symbols

    mock_summarizer.summarize_batch.side_effect = fake_summarize

    with patch(
        "jcodemunch_mcp.tools.test_summarizer._config.get",
        side_effect=lambda k, d=None, **kwargs: "auto" if k == "use_ai_summaries" else d,
    ), patch(
        "jcodemunch_mcp.tools.test_summarizer.get_provider_name",
        return_value="anthropic",
    ), patch(
        "jcodemunch_mcp.tools.test_summarizer._create_summarizer",
        return_value=mock_summarizer,
    ):
        result = run_test()

    assert result["status"] == "ok"
    assert result["provider"] == "anthropic"
    assert result["summary"] == "Greets the user by name."
    assert result["elapsed_ms"] is not None


def test_test_summarizer_fallback():
    """test_summarizer detects when AI fell back to signature."""
    from jcodemunch_mcp.tools.test_summarizer import test_summarizer as run_test

    mock_summarizer = MagicMock()
    mock_summarizer.model = "test-model"

    def fake_fallback(symbols, batch_size=1):
        for sym in symbols:
            sym.summary = "def greet(name: str) -> str:"  # signature = fallback
        return symbols

    mock_summarizer.summarize_batch.side_effect = fake_fallback

    with patch(
        "jcodemunch_mcp.tools.test_summarizer._config.get",
        side_effect=lambda k, d=None, **kwargs: "auto" if k == "use_ai_summaries" else d,
    ), patch(
        "jcodemunch_mcp.tools.test_summarizer.get_provider_name",
        return_value="openrouter",
    ), patch(
        "jcodemunch_mcp.tools.test_summarizer._create_summarizer",
        return_value=mock_summarizer,
    ):
        result = run_test()

    assert result["status"] == "fallback"
    assert "signature" in result["error"].lower()


# ─── #304: project-aware runtime config reads ──────────────────────────────


class TestProjectAwareSummarizer:
    """Regression: jcm#304. batch_summarize.py read summarizer_model and
    summarizer_provider from _GLOBAL_CONFIG only (no repo= passed), so
    project-level overrides in .jcodemunch.jsonc were silently dropped at
    runtime. The fix threads `repo` through summarize_symbols ->
    _create_summarizer -> provider __init__ -> _config.get(..., repo=).
    """

    def _isolated_configs(self, monkeypatch, tmp_path, project_config: dict):
        """Set up an isolated config environment: empty global config, a
        project config at <tmp>/project/.jcodemunch.jsonc, and Path.cwd /
        Path.home redirected so config loading uses the temp dir."""
        from jcodemunch_mcp.config import _GLOBAL_CONFIG, _PROJECT_CONFIGS
        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()

        storage = tmp_path / "storage"
        storage.mkdir()
        (storage / "config.jsonc").write_text("{}", encoding="utf-8")

        project = tmp_path / "project"
        project.mkdir()
        if project_config:
            import json
            (project / ".jcodemunch.jsonc").write_text(
                json.dumps(project_config), encoding="utf-8"
            )

        monkeypatch.setenv("CODE_INDEX_PATH", str(storage))
        from pathlib import Path as _P
        monkeypatch.setattr(_P, "home", classmethod(lambda cls: tmp_path))
        for k in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_BASE",
                  "MINIMAX_API_KEY", "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY",
                  "ORCAROUTER_API_KEY",
                  "OPENAI_MODEL", "ANTHROPIC_MODEL", "GOOGLE_MODEL",
                  "JCODEMUNCH_SUMMARIZER_PROVIDER",
                  "JCODEMUNCH_SUMMARIZER_MODEL"):
            monkeypatch.delenv(k, raising=False)

        # Force load_config to pick up the empty config.jsonc.
        from jcodemunch_mcp import config as _cfg
        _cfg.load_config(str(storage))
        # Load the project config so _PROJECT_CONFIGS is populated.
        _cfg.load_project_config(str(project))

        return project

    def test_get_model_name_returns_project_value(self, tmp_path, monkeypatch):
        project = self._isolated_configs(
            monkeypatch, tmp_path,
            project_config={"summarizer_model": "Qwen3.6-Plus"},
        )
        from jcodemunch_mcp.summarizer.batch_summarize import get_model_name
        # Without repo: still sees global only (which is empty here).
        assert get_model_name() is None
        # With repo: project-aware lookup returns the project value.
        assert get_model_name(repo=str(project)) == "Qwen3.6-Plus"

    def test_get_provider_name_returns_project_value(self, tmp_path, monkeypatch):
        project = self._isolated_configs(
            monkeypatch, tmp_path,
            project_config={"summarizer_provider": "anthropic"},
        )
        from jcodemunch_mcp.summarizer.batch_summarize import get_provider_name
        # Without repo: empty global means None.
        assert get_provider_name() is None
        # With repo: project override is honored.
        assert get_provider_name(repo=str(project)) == "anthropic"

    def test_anthropic_summarizer_picks_up_project_model(self, tmp_path, monkeypatch):
        """The runtime path that was broken: when ANTHROPIC_API_KEY is set
        and the project config overrides summarizer_model, the resulting
        BatchSummarizer.model reflects the project value (not the env
        ANTHROPIC_MODEL or the dataclass default)."""
        project = self._isolated_configs(
            monkeypatch, tmp_path,
            project_config={"summarizer_model": "claude-haiku-from-project"},
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        # Stub the anthropic SDK so we don't need it installed.
        import sys
        import types as _types
        stub = _types.ModuleType("anthropic")
        stub.Anthropic = lambda **kwargs: object()  # any non-None
        monkeypatch.setitem(sys.modules, "anthropic", stub)

        from jcodemunch_mcp.summarizer.batch_summarize import BatchSummarizer
        s = BatchSummarizer(repo=str(project))
        assert s.model == "claude-haiku-from-project"

    def test_create_summarizer_threads_repo_through(self, tmp_path, monkeypatch):
        """End-to-end: _create_summarizer(repo=X) selects the provider
        per X's project config AND constructs an instance carrying repo=X."""
        project = self._isolated_configs(
            monkeypatch, tmp_path,
            project_config={
                "summarizer_provider": "anthropic",
                "summarizer_model": "claude-from-project",
                "use_ai_summaries": True,
            },
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        import sys, types as _types
        stub = _types.ModuleType("anthropic")
        stub.Anthropic = lambda **kwargs: object()
        monkeypatch.setitem(sys.modules, "anthropic", stub)

        from jcodemunch_mcp.summarizer.batch_summarize import (
            _create_summarizer,
            BatchSummarizer,
        )
        s = _create_summarizer(repo=str(project))
        assert isinstance(s, BatchSummarizer)
        assert s.repo == str(project)
        assert s.model == "claude-from-project"

    def test_create_summarizer_without_repo_keeps_global_only(self, tmp_path, monkeypatch):
        """Pre-#304 behavior preserved when no repo is passed: global config
        only, project overrides are not consulted."""
        project = self._isolated_configs(
            monkeypatch, tmp_path,
            project_config={"summarizer_provider": "anthropic"},
        )
        # No ANTHROPIC_API_KEY in env; no global summarizer_provider; no repo
        # → should return None (auto-detect finds nothing).
        from jcodemunch_mcp.summarizer.batch_summarize import _create_summarizer
        assert _create_summarizer() is None
