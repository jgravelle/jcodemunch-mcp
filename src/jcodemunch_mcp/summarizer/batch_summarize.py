"""Three-tier summarization: docstring > AI provider > signature fallback."""

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from .. import config as _config
from ..parser.symbols import Symbol

logger = logging.getLogger(__name__)

_LOCALHOST_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_AUTO_DETECT_ORDER = [
    ("ANTHROPIC_API_KEY", "anthropic"),
    ("GOOGLE_API_KEY", "gemini"),
    ("OPENAI_API_BASE", "openai"),
    ("MINIMAX_API_KEY", "minimax"),
    ("ZHIPUAI_API_KEY", "glm"),
    ("OPENROUTER_API_KEY", "openrouter"),
]
_VALID_PROVIDERS = {"anthropic", "gemini", "openai", "minimax", "glm", "openrouter", "none"}


def _is_localhost_url(url: str) -> bool:
    """Return True if url points to a loopback address."""
    try:
        parsed = urlparse(url)
        return parsed.hostname in _LOCALHOST_HOSTS
    except Exception:
        return False


def extract_summary_from_docstring(docstring: str) -> str:
    """Extract first sentence from docstring (Tier 1).

    Takes the first line and truncates at first period.
    Costs zero tokens.
    """
    if not docstring:
        return ""

    # Take first line, strip whitespace
    first_line = docstring.strip().split("\n")[0].strip()

    # Truncate at first period if present
    if "." in first_line:
        first_line = first_line[: first_line.index(".") + 1]

    return first_line[:120]


def signature_fallback(symbol: Symbol) -> str:
    """Generate summary from signature when all else fails (Tier 3).

    Always produces something, even without API keys.
    """
    kind = symbol.kind
    name = symbol.name
    sig = symbol.signature

    if kind == "class":
        return f"Class {name}"
    elif kind == "constant":
        return f"Constant {name}"
    elif kind == "type":
        return f"Type definition {name}"
    else:
        # For functions/methods, include parameter hint
        return sig[:120] if sig else f"{kind} {name}"


@dataclass
class BaseSummarizer:
    """Base class for AI batch summarizers with shared prompt/parse logic."""

    model: str = ""
    max_tokens_per_batch: int = 500
    client: object = None
    _consecutive_failures: int = field(default=0, init=False, repr=False)
    _circuit_broken: bool = field(default=False, init=False, repr=False)
    _failure_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def _record_success(self) -> None:
        """Reset consecutive failure counter on a successful batch."""
        with self._failure_lock:
            self._consecutive_failures = 0

    def _record_failure(self) -> None:
        """Increment failure counter; trip circuit breaker if threshold reached."""
        max_failures = _config.get("summarizer_max_failures", 3)
        with self._failure_lock:
            self._consecutive_failures += 1
            if max_failures > 0 and self._consecutive_failures >= max_failures:
                if not self._circuit_broken:
                    logger.warning(
                        "AI summarizer failed %d consecutive batches — "
                        "disabling for remaining symbols (signature fallback)",
                        self._consecutive_failures,
                    )
                self._circuit_broken = True

    def summarize_batch(
        self, symbols: list[Symbol], batch_size: int = 10
    ) -> list[Symbol]:
        """Summarize a batch of symbols using AI.

        Only processes symbols that don't already have summaries.
        Uses concurrent requests for throughput (configurable via
        JCODEMUNCH_SUMMARIZER_CONCURRENCY, default 4).
        Trips a circuit breaker after summarizer_max_failures (default 3)
        consecutive failures, falling back to signature for all remaining.
        Returns updated symbols.
        """
        if not self.client:
            for sym in symbols:
                if not sym.summary:
                    sym.summary = signature_fallback(sym)
            return symbols

        to_summarize = [s for s in symbols if not s.summary and not s.docstring]

        if not to_summarize:
            return symbols

        total = len(to_summarize)
        logger.info("AI summarization starting: %d symbols to process", total)

        max_workers = _config.get("summarizer_concurrency", 4)
        batches = [
            to_summarize[i : i + batch_size]
            for i in range(0, len(to_summarize), batch_size)
        ]
        log_every = max(1, len(batches) // 10)  # log ~10 progress updates

        if max_workers <= 1 or len(batches) <= 1:
            for i, batch in enumerate(batches):
                self._run_batch(batch)
                if (i + 1) % log_every == 0 or (i + 1) == len(batches):
                    processed = min((i + 1) * batch_size, total)
                    logger.info(
                        "AI summarization: %d/%d symbols (%d%%)",
                        processed, total, 100 * processed // total,
                    )
        else:
            completed_count = 0
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(self._run_batch, batch): batch
                    for batch in batches
                }
                for future in as_completed(futures):
                    future.result()
                    completed_count += 1
                    if completed_count % log_every == 0 or completed_count == len(batches):
                        processed = min(completed_count * batch_size, total)
                        logger.info(
                            "AI summarization: ~%d/%d symbols (%d%%)",
                            processed, total, 100 * processed // total,
                        )

        logger.info("AI summarization complete: %d symbols processed", total)
        return symbols

    def _run_batch(self, batch: list[Symbol]) -> None:
        """Run a single batch with circuit breaker check."""
        if self._circuit_broken:
            for sym in batch:
                if not sym.summary:
                    sym.summary = signature_fallback(sym)
            return
        self._summarize_one_batch(batch)

    def _summarize_one_batch(self, batch: list[Symbol]):
        """Summarize one batch of symbols. Override in subclasses."""
        raise NotImplementedError

    def _build_prompt(self, symbols: list[Symbol]) -> str:
        """Build summarization prompt for a batch."""
        lines = [
            "Summarize each code symbol in ONE short sentence (max 15 words).",
            "Focus on what it does, not how. Use business context when available.",
            "",
        ]

        # Inject ecosystem context if any symbol has it
        context_lines = set()
        for sym in symbols:
            if sym.ecosystem_context:
                context_lines.add(sym.ecosystem_context)
        if context_lines:
            lines.append("Context:")
            for ctx in context_lines:
                lines.append(ctx)
            lines.append("")

        lines.append("Input:")
        for i, sym in enumerate(symbols, 1):
            lines.append(f"{i}. {sym.kind}: {sym.signature}")

        lines.extend(
            [
                "",
                "Output format: NUMBER. SUMMARY",
                "Example: 1. Authenticates users with username and password.",
                "",
                "Summaries:",
            ]
        )

        return "\n".join(lines)

    def _parse_response(self, text: str, expected_count: int) -> list[str]:
        """Parse numbered summaries from response."""
        summaries = [""] * expected_count

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue

            if "." in line:
                parts = line.split(".", 1)
                try:
                    num = int(parts[0].strip())
                    if 1 <= num <= expected_count:
                        summary = parts[1].strip()
                        if summary:
                            summaries[num - 1] = summary
                except ValueError:
                    continue

        return summaries


@dataclass
class BatchSummarizer(BaseSummarizer):
    """AI-based batch summarization using Claude Haiku (Tier 2)."""

    model: str = "claude-haiku-4-5-20251001"

    def __post_init__(self):
        self.client = None
        self._init_client()

    def _init_client(self):
        """Initialize Anthropic client if API key is available."""
        try:
            from anthropic import Anthropic

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if api_key:
                cfg_model = (_config.get("summarizer_model", "") or "").strip()
                self.model = cfg_model or os.environ.get("ANTHROPIC_MODEL", self.model)
                base_url = os.environ.get("ANTHROPIC_BASE_URL")
                kwargs = {"api_key": api_key}
                if base_url:
                    allow_remote = _config.get("allow_remote_summarizer", False)
                    if _is_localhost_url(base_url) or allow_remote:
                        kwargs["base_url"] = base_url
                    else:
                        logger.warning(
                            "ANTHROPIC_BASE_URL points to non-localhost URL (%s). "
                            "Ignoring for security. Set JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER=1 to allow.",
                            urlparse(base_url).hostname,
                        )
                self.client = Anthropic(**kwargs)
        except ImportError:
            if os.environ.get("ANTHROPIC_API_KEY"):
                import warnings

                warnings.warn(
                    "ANTHROPIC_API_KEY is set but the 'anthropic' package is not installed. "
                    "Install it with: pip install jcodemunch-mcp[anthropic]",
                    stacklevel=2,
                )
            self.client = None

    def _summarize_one_batch(self, batch: list[Symbol]):
        """Summarize one batch of symbols."""
        prompt = self._build_prompt(batch)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens_per_batch,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )

            summaries = self._parse_response(response.content[0].text, len(batch))

            for sym, summary in zip(batch, summaries):
                if summary:
                    sym.summary = summary
                else:
                    sym.summary = signature_fallback(sym)

            self._record_success()

        except Exception as e:
            logger.warning("AI summarization failed, falling back to signature: %s", e)
            self._record_failure()
            for sym in batch:
                if not sym.summary:
                    sym.summary = signature_fallback(sym)


@dataclass
class GeminiBatchSummarizer(BaseSummarizer):
    """AI-based batch summarization using Google Gemini Flash (Tier 2)."""

    model: str = "gemini-2.5-flash-lite"

    def __post_init__(self):
        self.client = None
        self._init_client()

    def _init_client(self):
        """Initialize Gemini client if API key is available."""
        try:
            import google.generativeai as genai

            api_key = os.environ.get("GOOGLE_API_KEY")
            if api_key:
                cfg_model = (_config.get("summarizer_model", "") or "").strip()
                self.model = cfg_model or os.environ.get("GOOGLE_MODEL", self.model)
                genai.configure(api_key=api_key)
                self.client = genai.GenerativeModel(self.model)
        except ImportError:
            if os.environ.get("GOOGLE_API_KEY"):
                import warnings

                warnings.warn(
                    "GOOGLE_API_KEY is set but the 'google-generativeai' package is not installed. "
                    "Install it with: pip install jcodemunch-mcp[gemini]",
                    stacklevel=2,
                )
            self.client = None

    def _summarize_one_batch(self, batch: list[Symbol]):
        """Summarize one batch of symbols."""
        prompt = self._build_prompt(batch)

        try:
            response = self.client.generate_content(prompt)
            summaries = self._parse_response(response.text, len(batch))

            for sym, summary in zip(batch, summaries):
                if summary:
                    sym.summary = summary
                else:
                    sym.summary = signature_fallback(sym)

            self._record_success()

        except Exception as e:
            logger.warning("AI summarization failed, falling back to signature: %s", e)
            self._record_failure()
            for sym in batch:
                if not sym.summary:
                    sym.summary = signature_fallback(sym)


@dataclass
class OpenAIBatchSummarizer(BaseSummarizer):
    """AI-based batch summarization using OpenAI-compatible endpoints (Tier 2).

    Supports OpenAI-hosted APIs, local LLMs, and compatible providers like MiniMax
    and GLM-5.
    """

    model: str = "qwen3-coder"
    api_base: Optional[str] = None
    api_key: str = "local-llm"

    def __post_init__(self):
        self.client = None
        self.wire_api = (
            os.environ.get("OPENAI_WIRE_API", "chat").strip().lower() or "chat"
        )
        api_base = self.api_base or os.environ.get("OPENAI_API_BASE")
        self.api_base = api_base.rstrip("/") if api_base else None
        if self.api_base:
            # Strip trailing slash if present
            # Security: restrict to localhost unless explicitly overridden
            allow_remote = _config.get("allow_remote_summarizer", False)
            if not _is_localhost_url(self.api_base) and not allow_remote:
                logger.warning(
                    "OPENAI_API_BASE points to non-localhost URL (%s). "
                    "Ignoring for security. Set JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER=1 to allow.",
                    urlparse(self.api_base).hostname,
                )
                self.api_base = None
                return
            cfg_model = (_config.get("summarizer_model", "") or "").strip()
            if cfg_model:
                self.model = cfg_model
            elif not self.api_base or self.api_base == os.environ.get("OPENAI_API_BASE", "").rstrip("/"):
                self.model = os.environ.get("OPENAI_MODEL", self.model)
            self.max_tokens_per_batch = int(
                os.environ.get("OPENAI_MAX_TOKENS", str(self.max_tokens_per_batch))
            )
            self._init_client()

    @property
    def wire_api(self) -> str:
        return getattr(self, "_wire_api", "chat")

    @wire_api.setter
    def wire_api(self, value: str):
        normalized = (value or "chat").strip().lower()
        self._wire_api = normalized or "chat"

    def _init_client(self):
        """Initialize HTTP client for OpenAI requests."""
        try:
            import httpx

            timeout_str = os.environ.get("OPENAI_TIMEOUT", "60.0")
            try:
                timeout = float(timeout_str)
            except ValueError:
                timeout = 60.0

            headers = {"Authorization": f"Bearer {self.api_key}"}
            self.client = httpx.Client(timeout=timeout, headers=headers)
        except ImportError:
            self.client = None

    def summarize_batch(
        self, symbols: list[Symbol], batch_size: int = 10
    ) -> list[Symbol]:
        """Summarize a batch of symbols using OpenAI compatible endpoint."""
        if not self.client or not self.api_base:
            for sym in symbols:
                if not sym.summary:
                    sym.summary = signature_fallback(sym)
            return symbols

        batch_size = int(os.environ.get("OPENAI_BATCH_SIZE", str(batch_size)))
        to_summarize = [s for s in symbols if not s.summary and not s.docstring]

        if not to_summarize:
            return symbols

        total = len(to_summarize)
        logger.info("AI summarization starting: %d symbols to process (provider=openai model=%s)", total, self.model)

        max_workers = int(os.environ.get("OPENAI_CONCURRENCY", str(_config.get("summarizer_concurrency", 4))))
        batches = [
            to_summarize[i : i + batch_size]
            for i in range(0, len(to_summarize), batch_size)
        ]
        log_every = max(1, len(batches) // 10)

        completed_count = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._summarize_one_batch, batch): batch
                for batch in batches
            }
            for future in as_completed(futures):
                future.result()
                completed_count += 1
                if completed_count % log_every == 0 or completed_count == len(batches):
                    processed = min(completed_count * batch_size, total)
                    logger.info(
                        "AI summarization: ~%d/%d symbols (%d%%)",
                        processed, total, 100 * processed // total,
                    )

        logger.info("AI summarization complete: %d symbols processed", total)

        return symbols

    def _request_spec(self, prompt: str) -> tuple[str, dict]:
        """Build request path and payload for the configured wire API."""
        if self.wire_api == "responses":
            return "/responses", {
                "model": self.model,
                "input": prompt,
                "max_output_tokens": self.max_tokens_per_batch,
                "temperature": 0.0,
            }

        if self.wire_api != "chat":
            raise ValueError(f"Unsupported OPENAI_WIRE_API: {self.wire_api}")

        return "/chat/completions", {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.max_tokens_per_batch,
            "temperature": 0.0,
        }

    def _extract_response_text(self, data: dict) -> str:
        """Extract response text for the configured wire API."""
        if self.wire_api == "responses":
            output_text = data.get("output_text")
            if isinstance(output_text, str) and output_text.strip():
                return output_text

            text_parts = []
            for output in data.get("output", []):
                for content in output.get("content", []):
                    if content.get("type") == "output_text":
                        text = content.get("text", "")
                        if text:
                            text_parts.append(text)

            if text_parts:
                return "\n".join(text_parts)

            raise KeyError("Responses API payload did not contain output text")

        return data["choices"][0]["message"]["content"]

    def _summarize_one_batch(self, batch: list[Symbol]):
        """Summarize one batch of symbols via HTTP POST."""
        prompt = self._build_prompt(batch)

        try:
            path, payload = self._request_spec(prompt)

            response = self.client.post(f"{self.api_base}{path}", json=payload)
            response.raise_for_status()

            data = response.json()
            text = self._extract_response_text(data)
            summaries = self._parse_response(text, len(batch))

            for sym, summary in zip(batch, summaries):
                if summary:
                    sym.summary = summary
                else:
                    sym.summary = signature_fallback(sym)

            self._record_success()

        except Exception as e:
            logger.warning("AI summarization failed, falling back to signature: %s", e)
            self._record_failure()
            for sym in batch:
                if not sym.summary:
                    sym.summary = signature_fallback(sym)


def get_model_name() -> Optional[str]:
    """Return the configured summarizer_model override, or None if unset.

    Reads the summarizer_model config key. Returns the stripped value, or None
    if the key is empty or not set.
    """
    val = _config.get("summarizer_model", "")
    if not val:
        return None
    return str(val).strip() or None


def _create_summarizer() -> Optional[BaseSummarizer]:
    """Return the appropriate summarizer based on tri-state use_ai_summaries + provider config.

    Tri-state semantics for use_ai_summaries:
    - False / "false" / "0" / "no" / "off": AI disabled — returns None immediately.
    - True (bool, explicit): use summarizer_provider + summarizer_model from config;
      falls back to auto-detect if provider is empty/unset.
    - "auto" / "true" / anything else truthy: auto-detect by env vars (legacy behavior).
    """
    raw = _config.get("use_ai_summaries", "auto")

    # Normalize to disabled / explicit / auto
    if isinstance(raw, bool):
        disabled = not raw
        explicit_mode = raw  # True → explicit, False → disabled
    else:
        s = str(raw).strip().lower()
        disabled = s in ("false", "0", "no", "off")
        explicit_mode = False  # string "true"/"auto" → auto-detect

    if disabled:
        return None

    model_override = get_model_name()

    if explicit_mode:
        # Use summarizer_provider from config; fall back to auto-detect if unset
        explicit_provider = (_config.get("summarizer_provider", "") or "").lower().strip()
        if explicit_provider == "":
            logger.warning(
                "use_ai_summaries is 'true' but summarizer_provider is not set; falling back to auto-detect"
            )
            name = get_provider_name()
        elif explicit_provider not in _VALID_PROVIDERS:
            logger.warning(
                "summarizer_provider '%s' is not a valid provider; falling back to auto-detect. "
                "Valid values: %s",
                explicit_provider,
                ", ".join(sorted(_VALID_PROVIDERS - {"none"})),
            )
            name = get_provider_name()
        else:
            name = None if explicit_provider == "none" else explicit_provider
    else:
        name = get_provider_name()

    if name == "anthropic":
        s = BatchSummarizer()
        return s if s.client else None
    if name == "gemini":
        s = GeminiBatchSummarizer()
        return s if s.client else None
    if name == "openai":
        s = _make_openai_compat(
            api_key=os.environ.get("OPENAI_API_KEY", "local-llm"),
            base_url=os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1"),
            model=model_override or os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        )
        return s if s.client else None
    if name == "minimax":
        try:
            s = _make_openai_compat(
                api_key=os.environ.get("MINIMAX_API_KEY"),
                base_url="https://api.minimax.io/v1",
                model=model_override or "minimax-m2.7",
            )
        except ValueError:
            return None
        return s if s.client else None
    if name == "glm":
        try:
            s = _make_openai_compat(
                api_key=os.environ.get("ZHIPUAI_API_KEY"),
                base_url="https://api.z.ai/api/paas/v4/",
                model=model_override or "glm-5",
            )
        except ValueError:
            return None
        return s if s.client else None
    if name == "openrouter":
        try:
            s = _make_openai_compat(
                api_key=os.environ.get("OPENROUTER_API_KEY"),
                base_url="https://openrouter.ai/api/v1",
                model=model_override or "meta-llama/llama-3.3-70b-instruct:free",
            )
        except ValueError:
            return None
        return s if s.client else None
    return None


def get_provider_name() -> Optional[str]:
    """Return the active summarizer provider name, or None if disabled/unset.

    Priority: summarizer_provider config key > JCODEMUNCH_SUMMARIZER_PROVIDER env var > auto-detect by key.
    Auto-detect order: Anthropic > Gemini > OpenAI-compatible > MiniMax > GLM-5 > OpenRouter.
    """
    explicit = (_config.get("summarizer_provider", "") or os.environ.get("JCODEMUNCH_SUMMARIZER_PROVIDER", "")).lower().strip()
    if explicit in _VALID_PROVIDERS:
        return None if explicit == "none" else explicit

    for env_var, name in _AUTO_DETECT_ORDER:
        if os.environ.get(env_var):
            return name
    return None


def _make_openai_compat(
    api_key: Optional[str],
    base_url: str,
    model: str,
) -> OpenAIBatchSummarizer:
    """Factory helper for OpenAI-compatible providers."""
    if not api_key:
        raise ValueError("Missing API key for OpenAI-compatible summarizer")
    return OpenAIBatchSummarizer(model=model, api_base=base_url, api_key=api_key)


def summarize_symbols_simple(symbols: list[Symbol]) -> list[Symbol]:
    """Tier 1 + Tier 3: Docstring extraction + signature fallback.

    No AI required. Fast and deterministic.
    """
    for sym in symbols:
        if sym.summary:
            continue

        # Try docstring
        if sym.docstring:
            sym.summary = extract_summary_from_docstring(sym.docstring)

        # Fall back to signature
        if not sym.summary:
            sym.summary = signature_fallback(sym)

    return symbols


def summarize_symbols(symbols: list[Symbol], use_ai: bool = True) -> list[Symbol]:
    """Full three-tier summarization.

    Tier 1: Docstring extraction (free)
    Tier 2: AI batch summarization (Claude Haiku, Gemini Flash, OpenAI, MiniMax, GLM-5)
    Tier 3: Signature fallback (always works)

    Provider selection (Tier 2 priority):
      1. ANTHROPIC_API_KEY set or provider=anthropic → Claude Haiku
      2. GOOGLE_API_KEY set or provider=gemini       → Gemini Flash
      3. OPENAI provider/base                         → OpenAI-compatible endpoint
      4. MINIMAX_API_KEY set or provider=minimax     → MiniMax M2.7
      5. ZHIPUAI_API_KEY set or provider=glm         → GLM-5
      6. OPENROUTER_API_KEY set or provider=openrouter → OpenRouter
      - None set               → skip to Tier 3
    """
    # Tier 1: Extract from docstrings
    for sym in symbols:
        if sym.docstring and not sym.summary:
            sym.summary = extract_summary_from_docstring(sym.docstring)

    # Tier 2: AI summarization for remaining symbols
    if use_ai:
        summarizer = _create_summarizer()
        if summarizer:
            symbols = summarizer.summarize_batch(symbols)

    # Tier 3: Signature fallback for any still missing
    for sym in symbols:
        if not sym.summary:
            sym.summary = signature_fallback(sym)

    return symbols
