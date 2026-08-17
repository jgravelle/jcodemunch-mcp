"""Diagnostic tool: verify AI summarizer configuration and connectivity."""

import logging
import time
from typing import Any

from .. import config as _config
from ..parser.symbols import Symbol
from ..summarizer.batch_summarize import (
    _create_summarizer,
    get_model_name,
    get_provider_name,
    signature_fallback,
)

logger = logging.getLogger(__name__)

_TEST_SYMBOL = Symbol(
    id="test::greet",
    file="test.py",
    name="greet",
    qualified_name="greet",
    kind="function",
    language="python",
    signature="def greet(name: str) -> str:",
)


def test_summarizer(timeout_ms: int = 15000) -> dict[str, Any]:
    """Send a probe request to the configured AI summarizer and report the result.

    Returns a dict with status, provider info, timing, and any error details.
    """
    result: dict[str, Any] = {
        "status": "unknown",
        "use_ai_summaries": None,
        "provider": None,
        "model": None,
        "summary": None,
        "elapsed_ms": None,
        "error": None,
    }

    # Step 1: Check if AI summaries are enabled.
    # Global-only by design (#301): this is a diagnostic tool probing the
    # server-level summarizer config, not a per-repo behavior gate.
    raw = _config.get("use_ai_summaries", "auto")
    result["use_ai_summaries"] = raw

    if isinstance(raw, bool):
        disabled = not raw
    else:
        disabled = str(raw).strip().lower() in ("false", "0", "no", "off")

    if disabled:
        result["status"] = "disabled"
        result["error"] = (
            "AI summarization is disabled (use_ai_summaries is false). "
            "Set use_ai_summaries to \"auto\" or true to enable."
        )
        return result

    # Step 2: Check provider detection
    provider = get_provider_name()
    result["provider"] = provider

    if not provider:
        result["status"] = "no_provider"
        result["error"] = (
            "No summarizer provider detected. "
            "Set an API key (ANTHROPIC_API_KEY, GOOGLE_API_KEY, OPENAI_API_BASE, "
            "MINIMAX_API_KEY, ZHIPUAI_API_KEY, OPENROUTER_API_KEY, ORCAROUTER_API_KEY) "
            "or configure summarizer_provider in your config file."
        )
        return result

    # Step 3: Check model
    model_override = get_model_name()
    result["model"] = model_override or "(provider default)"

    # Step 4: Try to create the summarizer
    try:
        summarizer = _create_summarizer()
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"Failed to create summarizer: {e}"
        return result

    if not summarizer:
        # Map provider to pip extra for actionable install instructions
        _pip_extras = {
            "anthropic": "anthropic",
            "gemini": "gemini",
            "openai": "openai",
            "minimax": "minimax",
            "glm": "zhipu",
            "openrouter": "openai",
            "orcarouter": "openai",
        }
        extra = _pip_extras.get(provider, provider)
        result["status"] = "misconfigured"
        result["error"] = (
            f"Provider '{provider}' was detected but the summarizer could not be "
            f"initialized. The '{extra}' package may not be installed.\n"
            f"  Run: pip install \"jcodemunch-mcp[{extra}]\"\n"
            "If the package is already installed, check that the API key is valid."
        )
        return result

    result["model"] = summarizer.model

    # Step 5: Send a test request
    test_sym = Symbol(
        id=_TEST_SYMBOL.id,
        file=_TEST_SYMBOL.file,
        name=_TEST_SYMBOL.name,
        qualified_name=_TEST_SYMBOL.qualified_name,
        kind=_TEST_SYMBOL.kind,
        language=_TEST_SYMBOL.language,
        signature=_TEST_SYMBOL.signature,
    )

    start = time.monotonic()
    try:
        summarizer.summarize_batch([test_sym], batch_size=1)
        elapsed_ms = round((time.monotonic() - start) * 1000)
        result["elapsed_ms"] = elapsed_ms
    except Exception as e:
        elapsed_ms = round((time.monotonic() - start) * 1000)
        result["elapsed_ms"] = elapsed_ms
        result["status"] = "error"
        result["error"] = f"Summarization request failed after {elapsed_ms}ms: {e}"
        return result

    # Step 6: Evaluate the result
    summary = test_sym.summary or ""
    result["summary"] = summary

    fallback = signature_fallback(_TEST_SYMBOL)
    if not summary:
        result["status"] = "error"
        result["error"] = "AI returned an empty summary."
    elif summary == fallback:
        # Got signature fallback — AI call likely failed silently
        result["status"] = "fallback"
        result["error"] = (
            f"AI did not produce a summary (fell back to signature: \"{fallback}\"). "
            "This usually means the API returned an error or an unparseable response. "
            "Check the server logs for details."
        )
    else:
        result["status"] = "ok"

    if elapsed_ms > timeout_ms:
        result["status"] = "timeout"
        result["error"] = (
            f"AI responded but took {elapsed_ms}ms (threshold: {timeout_ms}ms). "
            "Consider using a faster model or a closer endpoint."
        )

    return result
