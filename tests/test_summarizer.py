"""Tests for summarizer module."""

import pytest
from unittest.mock import MagicMock, patch
from jcodemunch_mcp.parser import Symbol
from jcodemunch_mcp.summarizer import (
    extract_summary_from_docstring,
    signature_fallback,
    summarize_symbols_simple,
    GeminiBatchSummarizer,
    OpenAIBatchSummarizer,
)


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
        with patch.dict("os.environ", {
            "ANTHROPIC_API_KEY": "sk-test-key",
            "ANTHROPIC_BASE_URL": "https://proxy.example.com/v1",
            "JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER": "1",
        }, clear=True):
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
        "choices": [
            {
                "message": {"content": "1. Multiplies two integers together."}
            }
        ]
    }

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch.dict("os.environ", {"OPENAI_API_BASE": "http://localhost:11434/v1", "OPENAI_MODEL": "qwen3-coder"}, clear=True):
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
    assert mock_client.post.call_args[0][0] == "http://localhost:11434/v1/chat/completions"
    assert symbols[0].summary == "Multiplies two integers together."

def test_openai_summarizer_timeout_config():
    """OpenAIBatchSummarizer configures custom timeouts via OPENAI_TIMEOUT."""
    # Test valid float parsing
    with patch.dict("os.environ", {"OPENAI_API_BASE": "http://test", "OPENAI_TIMEOUT": "120.5", "JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER": "1"}, clear=True):
        summarizer = OpenAIBatchSummarizer()
        assert summarizer.client is not None
        assert summarizer.client.timeout.read == 120.5

    # Test invalid string fallback
    with patch.dict("os.environ", {"OPENAI_API_BASE": "http://test", "OPENAI_TIMEOUT": "invalid", "JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER": "1"}, clear=True):
        summarizer = OpenAIBatchSummarizer()
        assert summarizer.client is not None
        assert summarizer.client.timeout.read == 60.0

