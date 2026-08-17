"""Three-tier summarization: docstring > AI provider > signature fallback."""

import json
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
    ("ORCAROUTER_API_KEY", "orcarouter"),
]
_VALID_PROVIDERS = {"anthropic", "gemini", "openai", "minimax", "glm", "openrouter", "orcarouter", "none"}

# Providers that bill a remote cloud account per call. A bare env key for any of
# these must NEVER auto-enable summarization — that silently spends real money
# during indexing (a stray ANTHROPIC_API_KEY in the environment was doing exactly
# this). Paid-cloud auto-selection requires explicit opt-in; see
# _paid_summaries_allowed. "openai" is handled separately because OPENAI_API_BASE
# may point at a free local endpoint (Ollama / LM Studio).
_PAID_CLOUD_PROVIDERS = frozenset({"anthropic", "gemini", "minimax", "glm", "openrouter", "orcarouter"})
_WARNED_SUPPRESSED_PAID: set[str] = set()


def _paid_summaries_allowed(repo: Optional[str] = None) -> bool:
    """Whether the user has explicitly opted in to paid-cloud auto-summaries.

    Off by default: an ambient cloud API key never bills on its own. Turn on with
    JCODEMUNCH_ALLOW_PAID_SUMMARIES=1 or the `allow_paid_summaries` config key.
    Naming a provider explicitly (`summarizer_provider`) is always honored and does
    not need this flag — that IS the opt-in.
    """
    env = os.environ.get("JCODEMUNCH_ALLOW_PAID_SUMMARIES", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    try:
        return bool(_config.get("allow_paid_summaries", False, repo=repo))
    except Exception:
        return False


def _auto_entry_is_paid_cloud(env_var: str, name: str) -> bool:
    """True if auto-selecting this provider from a bare env key would bill a
    remote cloud account. 'openai' bills only when OPENAI_API_BASE is a
    non-localhost endpoint (default https://api.openai.com/v1 is remote)."""
    if name in _PAID_CLOUD_PROVIDERS:
        return True
    if name == "openai":
        base = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
        return not _is_localhost_url(base)
    return False

# When a summarization run finishes and at least this fraction of graded
# symbols fell back to generic signatures DESPITE successful responses,
# emit a degradation warning (#323 — thinking models burning the output
# budget on reasoning tokens return 200 OK with no usable summary text).
_DEGRADED_FALLBACK_WARN_RATIO = 0.5


def _is_localhost_url(url: str) -> bool:
    """Return True if url points to a loopback address."""
    try:
        parsed = urlparse(url)
        return parsed.hostname in _LOCALHOST_HOSTS
    except Exception:
        return False


def _resolve_extra_body(repo: Optional[str] = None) -> dict:
    """Resolve the optional OpenAI-compatible extra request body (#323).

    Merges the ``openai_extra_body`` config key (project-overridable via
    ``.jcodemunch.jsonc``) over the ``JCODEMUNCH_OPENAI_EXTRA_BODY`` env var
    (a JSON object); config keys win per top-level key. Returns {} when
    neither is set or either fails to parse.

    Primary use: disabling thinking on a local reasoning model so the output
    budget isn't spent on reasoning tokens, e.g.
    ``JCODEMUNCH_OPENAI_EXTRA_BODY='{"chat_template_kwargs":{"enable_thinking":false}}'``
    for llama.cpp / Qwen.
    """
    merged: dict = {}

    env_raw = os.environ.get("JCODEMUNCH_OPENAI_EXTRA_BODY")
    if env_raw:
        try:
            parsed = json.loads(env_raw)
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning(
                "JCODEMUNCH_OPENAI_EXTRA_BODY is not valid JSON; ignoring: %s", e
            )
        else:
            if isinstance(parsed, dict):
                merged.update(parsed)
            else:
                logger.warning(
                    "JCODEMUNCH_OPENAI_EXTRA_BODY must be a JSON object; ignoring"
                )

    cfg = _config.get("openai_extra_body", None, repo=repo)
    if isinstance(cfg, dict):
        merged.update(cfg)
    elif cfg:
        logger.warning("openai_extra_body config must be a JSON object; ignoring")

    return merged


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
    """Base class for AI batch summarizers with shared prompt/parse logic.

    The optional `repo` field carries the active source_root for the index
    being summarized, so per-call `_config.get("...", repo=self.repo)` reads
    honor project-level overrides in `.jcodemunch.jsonc`. Without this, the
    summarizer was global-only at runtime — surfaced by @slazarov on #300
    and tracked as #304.
    """

    model: str = ""
    max_tokens_per_batch: int = 500
    client: object = None
    repo: Optional[str] = None
    _consecutive_failures: int = field(default=0, init=False, repr=False)
    _circuit_broken: bool = field(default=False, init=False, repr=False)
    _ai_summary_count: int = field(default=0, init=False, repr=False)
    _post_response_fallbacks: int = field(default=0, init=False, repr=False)
    _failure_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def _apply_summaries(self, batch: list[Symbol], summaries: list[str]) -> None:
        """Assign parsed summaries to symbols; signature-fallback the blanks.

        Tracks how many symbols got a real AI summary vs. fell back to a
        generic signature DESPITE a successful response, so summarize_batch
        can report silent degradation at the end of the run (#323). A thinking
        model that spends its whole output budget on reasoning returns 200 OK
        with empty content — no exception fires, so the circuit breaker never
        sees it and the run looks successful while producing useless summaries.
        """
        ai = 0
        fallback = 0
        for sym, summary in zip(batch, summaries):
            if summary:
                sym.summary = summary
                ai += 1
            else:
                sym.summary = signature_fallback(sym)
                fallback += 1
        with self._failure_lock:
            self._ai_summary_count += ai
            self._post_response_fallbacks += fallback

    def _degradation_hint(self) -> str:
        """Provider-specific remedy appended to the degradation warning (#323)."""
        return (
            "If the model emits reasoning/thinking tokens, they may be consuming "
            "the output budget; raise the token budget or disable thinking."
        )

    def _warn_if_degraded(self) -> None:
        """Warn when successful responses still yielded mostly generic summaries.

        Distinct from the circuit breaker (which counts HTTP/exception
        failures): this catches the silent case where responses return 200 OK
        but carry no usable summary text — typically a thinking/reasoning model
        spending its entire output budget on reasoning tokens (#323).
        """
        with self._failure_lock:
            ai = self._ai_summary_count
            fallback = self._post_response_fallbacks
        graded = ai + fallback
        if fallback == 0 or graded == 0:
            return
        ratio = fallback / graded
        if ratio >= _DEGRADED_FALLBACK_WARN_RATIO:
            logger.warning(
                "AI summarizer produced usable summaries for only %d/%d symbols; "
                "%d (%d%%) fell back to generic signatures despite successful "
                "responses. %s",
                ai, graded, fallback, round(ratio * 100), self._degradation_hint(),
            )

    def _record_success(self) -> None:
        """Reset consecutive failure counter on a successful batch."""
        with self._failure_lock:
            self._consecutive_failures = 0

    def _record_failure(self) -> None:
        """Increment failure counter; trip circuit breaker if threshold reached."""
        max_failures = _config.get("summarizer_max_failures", 3, repo=self.repo)
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
        self._warn_if_degraded()
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
                cfg_model = (_config.get("summarizer_model", "", repo=self.repo) or "").strip()
                self.model = cfg_model or os.environ.get("ANTHROPIC_MODEL", self.model)
                base_url = os.environ.get("ANTHROPIC_BASE_URL")
                kwargs = {"api_key": api_key}
                if base_url:
                    allow_remote = _config.get("allow_remote_summarizer", False, repo=self.repo)
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
                    'Install it with: pip install "jcodemunch-mcp[anthropic]"',
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
            self._apply_summaries(batch, summaries)

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
                cfg_model = (_config.get("summarizer_model", "", repo=self.repo) or "").strip()
                self.model = cfg_model or os.environ.get("GOOGLE_MODEL", self.model)
                genai.configure(api_key=api_key)
                self.client = genai.GenerativeModel(self.model)
        except ImportError:
            if os.environ.get("GOOGLE_API_KEY"):
                import warnings

                warnings.warn(
                    "GOOGLE_API_KEY is set but the 'google-generativeai' package is not installed. "
                    'Install it with: pip install "jcodemunch-mcp[gemini]"',
                    stacklevel=2,
                )
            self.client = None

    def _summarize_one_batch(self, batch: list[Symbol]):
        """Summarize one batch of symbols."""
        prompt = self._build_prompt(batch)

        try:
            response = self.client.generate_content(prompt)
            summaries = self._parse_response(response.text, len(batch))
            self._apply_summaries(batch, summaries)

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
        self.extra_body = {}
        self.wire_api = (
            os.environ.get("OPENAI_WIRE_API", "chat").strip().lower() or "chat"
        )
        api_base = self.api_base or os.environ.get("OPENAI_API_BASE")
        self.api_base = api_base.rstrip("/") if api_base else None
        if self.api_base:
            # Strip trailing slash if present
            # Security: restrict to localhost unless explicitly overridden
            allow_remote = _config.get("allow_remote_summarizer", False, repo=self.repo)
            if not _is_localhost_url(self.api_base) and not allow_remote:
                logger.warning(
                    "OPENAI_API_BASE points to non-localhost URL (%s). "
                    "Ignoring for security. Set JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER=1 to allow.",
                    urlparse(self.api_base).hostname,
                )
                self.api_base = None
                return
            cfg_model = (_config.get("summarizer_model", "", repo=self.repo) or "").strip()
            if cfg_model:
                self.model = cfg_model
            elif not self.api_base or self.api_base == os.environ.get("OPENAI_API_BASE", "").rstrip("/"):
                self.model = os.environ.get("OPENAI_MODEL", self.model)
            self.max_tokens_per_batch = int(
                os.environ.get("OPENAI_MAX_TOKENS", str(self.max_tokens_per_batch))
            )
            self.extra_body = _resolve_extra_body(self.repo)
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
            # Submit via _run_batch (not _summarize_one_batch) so the circuit
            # breaker is honored: once summarizer_max_failures consecutive
            # batches fail, remaining batches short-circuit to signature
            # fallback instead of hammering a dead endpoint.
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
        self._warn_if_degraded()

        return symbols

    def _degradation_hint(self) -> str:
        """OpenAI-compatible remedy for the degradation warning (#323)."""
        return (
            "If you're using a thinking/reasoning model, the output budget is "
            "likely spent on reasoning tokens. Disable thinking (e.g. set "
            "JCODEMUNCH_OPENAI_EXTRA_BODY="
            "'{\"chat_template_kwargs\":{\"enable_thinking\":false}}') "
            "or raise OPENAI_MAX_TOKENS."
        )

    def _request_spec(self, prompt: str) -> tuple[str, dict]:
        """Build request path and payload for the configured wire API.

        Merges ``self.extra_body`` (from openai_extra_body config /
        JCODEMUNCH_OPENAI_EXTRA_BODY) into the payload so callers can pass
        provider-specific knobs such as disabling a thinking model's
        reasoning (#323).
        """
        if self.wire_api == "responses":
            payload = {
                "model": self.model,
                "input": prompt,
                "max_output_tokens": self.max_tokens_per_batch,
                "temperature": 0.0,
            }
            if self.extra_body:
                payload.update(self.extra_body)
            return "/responses", payload

        if self.wire_api != "chat":
            raise ValueError(f"Unsupported OPENAI_WIRE_API: {self.wire_api}")

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.max_tokens_per_batch,
            "temperature": 0.0,
        }
        if self.extra_body:
            payload.update(self.extra_body)
        return "/chat/completions", payload

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
            self._apply_summaries(batch, summaries)

            self._record_success()

        except Exception as e:
            logger.warning("AI summarization failed, falling back to signature: %s", e)
            self._record_failure()
            for sym in batch:
                if not sym.summary:
                    sym.summary = signature_fallback(sym)


def get_model_name(repo: Optional[str] = None) -> Optional[str]:
    """Return the configured summarizer_model override, or None if unset.

    Reads the summarizer_model config key, honoring project-level overrides
    in `.jcodemunch.jsonc` when `repo` is given (#304). Returns the stripped
    value, or None if the key is empty or not set.
    """
    val = _config.get("summarizer_model", "", repo=repo)
    if not val:
        return None
    return str(val).strip() or None


def _create_summarizer(repo: Optional[str] = None) -> Optional[BaseSummarizer]:
    """Return the appropriate summarizer based on tri-state use_ai_summaries + provider config.

    Tri-state semantics for use_ai_summaries:
    - False / "false" / "0" / "no" / "off": AI disabled — returns None immediately.
    - True (bool, explicit): use summarizer_provider + summarizer_model from config;
      falls back to auto-detect if provider is empty/unset.
    - "auto" / "true" / anything else truthy: auto-detect by env vars (legacy behavior).

    `repo` (when given) routes all config reads through the project-aware path
    so `.jcodemunch.jsonc` overrides for `summarizer_model`, `summarizer_provider`,
    and `use_ai_summaries` are honored at runtime (#304).
    """
    raw = _config.get("use_ai_summaries", "auto", repo=repo)

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

    model_override = get_model_name(repo=repo)

    if explicit_mode:
        # Use summarizer_provider from config; fall back to auto-detect if unset
        explicit_provider = (_config.get("summarizer_provider", "", repo=repo) or "").lower().strip()
        if explicit_provider == "":
            logger.warning(
                "use_ai_summaries is 'true' but summarizer_provider is not set; falling back to auto-detect"
            )
            name = get_provider_name(repo=repo)
        elif explicit_provider not in _VALID_PROVIDERS:
            logger.warning(
                "summarizer_provider '%s' is not a valid provider; falling back to auto-detect. "
                "Valid values: %s",
                explicit_provider,
                ", ".join(sorted(_VALID_PROVIDERS - {"none"})),
            )
            name = get_provider_name(repo=repo)
        else:
            name = None if explicit_provider == "none" else explicit_provider
    else:
        name = get_provider_name(repo=repo)

    if name == "anthropic":
        s = BatchSummarizer(repo=repo)
        return s if s.client else None
    if name == "gemini":
        s = GeminiBatchSummarizer(repo=repo)
        return s if s.client else None
    if name == "openai":
        s = _make_openai_compat(
            api_key=os.environ.get("OPENAI_API_KEY", "local-llm"),
            base_url=os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1"),
            model=model_override or os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            repo=repo,
        )
        return s if s.client else None
    if name == "minimax":
        try:
            s = _make_openai_compat(
                api_key=os.environ.get("MINIMAX_API_KEY"),
                base_url="https://api.minimax.io/v1",
                model=model_override or "minimax-m2.7",
                repo=repo,
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
                repo=repo,
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
                repo=repo,
            )
        except ValueError:
            return None
        return s if s.client else None
    if name == "orcarouter":
        try:
            s = _make_openai_compat(
                api_key=os.environ.get("ORCAROUTER_API_KEY"),
                base_url="https://api.orcarouter.ai/v1",
                model=model_override or "openai/gpt-4o-mini",
                repo=repo,
            )
        except ValueError:
            return None
        return s if s.client else None
    return None


def get_provider_name(repo: Optional[str] = None) -> Optional[str]:
    """Return the active summarizer provider name, or None if disabled/unset.

    Priority: summarizer_provider config key > JCODEMUNCH_SUMMARIZER_PROVIDER env var > auto-detect by key.
    Auto-detect order: Anthropic > Gemini > OpenAI-compatible > MiniMax > GLM-5 > OpenRouter > OrcaRouter.

    `repo` routes the config read through the project-aware path so a
    `summarizer_provider` set in `.jcodemunch.jsonc` is honored (#304).
    """
    explicit = (_config.get("summarizer_provider", "", repo=repo) or os.environ.get("JCODEMUNCH_SUMMARIZER_PROVIDER", "")).lower().strip()
    if explicit in _VALID_PROVIDERS:
        return None if explicit == "none" else explicit

    # Auto-detect by env key — but NEVER auto-bill a paid cloud provider from a
    # bare key. A stray ANTHROPIC_API_KEY / GOOGLE_API_KEY / etc. in the
    # environment must not silently spend during indexing. Paid-cloud
    # auto-selection requires explicit opt-in; naming the provider bypasses this
    # (handled above). Free/local endpoints still auto-enable.
    allow_paid = _paid_summaries_allowed(repo)
    for env_var, name in _AUTO_DETECT_ORDER:
        if not os.environ.get(env_var):
            continue
        if not allow_paid and _auto_entry_is_paid_cloud(env_var, name):
            if name not in _WARNED_SUPPRESSED_PAID:
                _WARNED_SUPPRESSED_PAID.add(name)
                logger.warning(
                    "%s is set but paid-cloud AI summaries are opt-in — NOT billing "
                    "%s automatically. To enable, set summarizer_provider=%s (or "
                    "JCODEMUNCH_ALLOW_PAID_SUMMARIES=1). Indexing continues with "
                    "signature/docstring summaries.",
                    env_var, name, name,
                )
            continue
        return name
    return None


def _make_openai_compat(
    api_key: Optional[str],
    base_url: str,
    model: str,
    repo: Optional[str] = None,
) -> OpenAIBatchSummarizer:
    """Factory helper for OpenAI-compatible providers."""
    if not api_key:
        raise ValueError("Missing API key for OpenAI-compatible summarizer")
    return OpenAIBatchSummarizer(model=model, api_base=base_url, api_key=api_key, repo=repo)


def _docstring_summaries_enabled(repo: Optional[str] = None) -> bool:
    """Return True if the docstring-extraction tier is enabled (P1.5).

    Defaults to True for back-compat. When set to False via the
    ``summarize_from_docstrings`` config key, the docstring-extraction tier
    is skipped entirely and summaries fall through to AI summarization (if
    configured) or to the signature_fallback. Recommended for security-conscious
    deployments that want to eliminate the indirect-prompt-injection surface
    that docstring-extracted content introduces (F-04).
    """
    try:
        from .. import config as _cfg
        return bool(_cfg.get("summarize_from_docstrings", True, repo=repo))
    except Exception:
        return True  # config unavailable, preserve default behavior


def summarize_symbols_simple(symbols: list[Symbol]) -> list[Symbol]:
    """Tier 1 + Tier 3: Docstring extraction + signature fallback.

    No AI required. Fast and deterministic. Docstring tier honors the
    ``summarize_from_docstrings`` config key (P1.5); when disabled, symbols
    without an existing summary fall straight through to signature_fallback.
    """
    use_docstrings = _docstring_summaries_enabled()
    for sym in symbols:
        if sym.summary:
            continue

        # Try docstring (gated by P1.5 config)
        if use_docstrings and sym.docstring:
            sym.summary = extract_summary_from_docstring(sym.docstring)

        # Fall back to signature
        if not sym.summary:
            sym.summary = signature_fallback(sym)

    return symbols


def summarize_symbols(
    symbols: list[Symbol],
    use_ai: bool = True,
    repo: Optional[str] = None,
) -> list[Symbol]:
    """Full three-tier summarization.

    Tier 1: Docstring extraction (free)
    Tier 2: AI batch summarization (Claude Haiku, Gemini Flash, OpenAI, MiniMax, GLM-5, OpenRouter, OrcaRouter)
    Tier 3: Signature fallback (always works)

    Provider selection (Tier 2 priority):
      1. ANTHROPIC_API_KEY set or provider=anthropic → Claude Haiku
      2. GOOGLE_API_KEY set or provider=gemini       → Gemini Flash
      3. OPENAI provider/base                         → OpenAI-compatible endpoint
      4. MINIMAX_API_KEY set or provider=minimax     → MiniMax M2.7
      5. ZHIPUAI_API_KEY set or provider=glm         → GLM-5
      6. OPENROUTER_API_KEY set or provider=openrouter → OpenRouter
      7. ORCAROUTER_API_KEY set or provider=orcarouter → OrcaRouter
      - None set               → skip to Tier 3

    `repo` (absolute path of the index source_root) routes config reads
    through the project-aware path so `summarizer_provider` /
    `summarizer_model` / `use_ai_summaries` set in `.jcodemunch.jsonc` are
    honored at runtime (#304). Defaults to None for callers that don't have
    a repo context, preserving the global-only behavior.
    """
    # Tier 1: Extract from docstrings (gated by summarize_from_docstrings, P1.5)
    if _docstring_summaries_enabled(repo=repo):
        for sym in symbols:
            if sym.docstring and not sym.summary:
                sym.summary = extract_summary_from_docstring(sym.docstring)

    # Tier 2: AI summarization for remaining symbols
    if use_ai:
        summarizer = _create_summarizer(repo=repo)
        if summarizer:
            symbols = summarizer.summarize_batch(symbols)

    # Tier 3: Signature fallback for any still missing
    for sym in symbols:
        if not sym.summary:
            sym.summary = signature_fallback(sym)

    return symbols
