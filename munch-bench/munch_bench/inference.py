"""Inference layer — calls LLM providers to answer codebase questions."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


SYSTEM_PROMPT = (
    "You are a senior software engineer answering questions about a codebase. "
    "Use the provided code context to give accurate, concise answers. "
    "If the context doesn't contain enough information, say so."
)


@dataclass
class InferenceResult:
    """Result of a single inference call."""

    answer: str
    model: str
    provider: str
    wall_time_s: float
    input_tokens: int
    output_tokens: int
    cost_usd: float


# Approximate costs per 1M tokens (input, output)
COST_TABLE: dict[str, tuple[float, float]] = {
    # Groq
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "llama3-70b-8192": (0.59, 0.79),
    "llama3-8b-8192": (0.05, 0.08),
    "gemma2-9b-it": (0.20, 0.20),
    "mixtral-8x7b-32768": (0.24, 0.24),
    "deepseek-r1-distill-llama-70b": (0.75, 0.99),
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    # Anthropic
    # Sonnet 5 carries an introductory rate of (2.00, 10.00) through 2026-08-31;
    # the standard rate is listed so a benchmark run does not read cheap after it lapses.
    "claude-fable-5": (10.00, 50.00),
    "claude-mythos-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}


# Anthropic models that still accept `temperature`. Sampling parameters were
# REMOVED on Opus 4.7 and later and on Sonnet 5 — sending one returns a 400, so
# the benchmark cannot pass temperature unconditionally.
#
# ⚠ This is an allowlist, not a blocklist, and the default (omit) is the modern
# behaviour: an unrecognised model id is far more likely to be a NEW model that
# rejects sampling than an old one that accepts it. The cost of that default is
# that a genuinely old, unlisted model silently samples at the API default of
# 1.0 instead of 0.0 — visible as run-to-run variance, where the other way round
# is a hard 400 that stops the run.
#
# ⚠⚠ EVERY ID HERE MUST HAVE A `COST_TABLE` ROW. `_price_for` falls back to
# (1.00, 3.00) for anything unlisted, so a model this set says we can benchmark
# but the table cannot price reports a cost several times under the real rate,
# silently. `claude-sonnet-4-5` was in exactly that state and was removed rather
# than priced — it is a legacy model nothing here benchmarks, and the fallback
# would have made a run of it read cheap.
_SAMPLING_SUPPORTED: frozenset[str] = frozenset({
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-haiku-4-5-20251001",
})

# Anthropic models that run adaptive thinking when `thinking` is OMITTED, so the
# benchmark must disable it explicitly to keep pre-Claude-5 behaviour. `max_tokens`
# caps thinking and response TOGETHER, so leaving it on truncates a 2048-token
# budget and bills reasoning the benchmark never reads.
#
# ⚠ Fable 5 is deliberately absent: its thinking is always on and an explicit
# `{"type": "disabled"}` returns a 400. Opus 4.8/4.7 are absent because omitting
# `thinking` already means no thinking there.
_THINKING_ON_BY_DEFAULT: frozenset[str] = frozenset({
    "claude-opus-5",
    "claude-sonnet-5",
})

# Models whose thinking CANNOT be turned off, so `max_tokens` is always shared
# between reasoning and answer and 2048 truncates.
_THINKING_ALWAYS_ON: frozenset[str] = frozenset({
    "claude-fable-5",
    "claude-mythos-5",
})

# ⚠ The default stays 2048 rather than rising for everyone: every run in
# `results/` was produced under it, and a larger budget lets answers grow, which
# would silently break comparability with the committed corpus. Only the models
# that cannot fit thinking inside it get more.
_MAX_TOKENS = 2048
# ~16k is the practical ceiling for a NON-STREAMING request — the SDK refuses one
# it estimates will exceed its HTTP timeout. Going above this means streaming.
_MAX_TOKENS_THINKING_ALWAYS_ON = 16000


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD from token counts."""
    costs = COST_TABLE.get(model, (1.0, 3.0))  # conservative default
    return (input_tokens * costs[0] + output_tokens * costs[1]) / 1_000_000


def infer_groq(
    context: str,
    question: str,
    model: str = "llama-3.3-70b-versatile",
    api_key: Optional[str] = None,
) -> InferenceResult:
    """Call Groq API (OpenAI-compatible) for inference."""
    import openai
    import os

    client = openai.OpenAI(
        api_key=api_key or os.environ.get("GROQ_API_KEY", ""),
        base_url="https://api.groq.com/openai/v1",
    )

    t0 = time.perf_counter()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"## Code Context\n\n{context}\n\n## Question\n\n{question}"},
        ],
        temperature=0.0,
        max_tokens=2048,
    )
    elapsed = time.perf_counter() - t0

    usage = response.usage
    input_tok = usage.prompt_tokens if usage else 0
    output_tok = usage.completion_tokens if usage else 0

    return InferenceResult(
        answer=response.choices[0].message.content or "",
        model=model,
        provider="groq",
        wall_time_s=elapsed,
        input_tokens=input_tok,
        output_tokens=output_tok,
        cost_usd=_estimate_cost(model, input_tok, output_tok),
    )


def infer_openai(
    context: str,
    question: str,
    model: str = "gpt-4o-mini",
    api_key: Optional[str] = None,
) -> InferenceResult:
    """Call OpenAI API for inference."""
    import openai
    import os

    client = openai.OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY", ""))

    t0 = time.perf_counter()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"## Code Context\n\n{context}\n\n## Question\n\n{question}"},
        ],
        temperature=0.0,
        max_tokens=2048,
    )
    elapsed = time.perf_counter() - t0

    usage = response.usage
    input_tok = usage.prompt_tokens if usage else 0
    output_tok = usage.completion_tokens if usage else 0

    return InferenceResult(
        answer=response.choices[0].message.content or "",
        model=model,
        provider="openai",
        wall_time_s=elapsed,
        input_tokens=input_tok,
        output_tokens=output_tok,
        cost_usd=_estimate_cost(model, input_tok, output_tok),
    )


def infer_anthropic(
    context: str,
    question: str,
    model: str = "claude-sonnet-5",
    api_key: Optional[str] = None,
) -> InferenceResult:
    """Call Anthropic API for inference."""
    import anthropic
    import os

    client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""))

    kwargs: dict = {}
    if model in _SAMPLING_SUPPORTED:
        kwargs["temperature"] = 0.0
    if model in _THINKING_ON_BY_DEFAULT:
        kwargs["thinking"] = {"type": "disabled"}

    t0 = time.perf_counter()
    response = client.messages.create(
        model=model,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": f"## Code Context\n\n{context}\n\n## Question\n\n{question}"},
        ],
        max_tokens=(
            _MAX_TOKENS_THINKING_ALWAYS_ON if model in _THINKING_ALWAYS_ON else _MAX_TOKENS
        ),
        **kwargs,
    )
    elapsed = time.perf_counter() - t0

    input_tok = response.usage.input_tokens
    output_tok = response.usage.output_tokens
    # Take the first TEXT block rather than content[0]: on a model whose thinking
    # cannot be disabled (Fable 5) the first block is a thinking block, which has
    # no `.text` at all.
    answer = next((b.text for b in response.content if getattr(b, "type", None) == "text"), "")

    return InferenceResult(
        answer=answer,
        model=model,
        provider="anthropic",
        wall_time_s=elapsed,
        input_tokens=input_tok,
        output_tokens=output_tok,
        cost_usd=_estimate_cost(model, input_tok, output_tok),
    )


PROVIDER_MAP = {
    "groq": infer_groq,
    "openai": infer_openai,
    "anthropic": infer_anthropic,
}


def infer(
    context: str,
    question: str,
    provider: str = "groq",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> InferenceResult:
    """Dispatch inference to the appropriate provider."""
    defaults = {
        "groq": "llama-3.3-70b-versatile",
        "openai": "gpt-4o-mini",
        "anthropic": "claude-sonnet-5",
    }
    fn = PROVIDER_MAP.get(provider)
    if fn is None:
        raise ValueError(f"Unknown provider: {provider}. Choose from: {', '.join(PROVIDER_MAP)}")
    model = model or defaults.get(provider, "")
    return fn(context, question, model=model, api_key=api_key)
