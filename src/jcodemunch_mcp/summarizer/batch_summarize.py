"""Three-tier summarization: docstring > AI (Haiku or Gemini) > signature fallback."""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

from ..parser.symbols import Symbol


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
        first_line = first_line[:first_line.index(".") + 1]
    
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
class BatchSummarizer:
    """AI-based batch summarization using Claude Haiku (Tier 2)."""
    
    model: str = "claude-haiku-4-5-20251001"
    max_tokens_per_batch: int = 500
    
    def __post_init__(self):
        self.client = None
        self._init_client()
    
    def _init_client(self):
        """Initialize Anthropic client if API key is available."""
        try:
            from anthropic import Anthropic
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if api_key:
                self.model = os.environ.get("ANTHROPIC_MODEL", self.model)
                base_url = os.environ.get("ANTHROPIC_BASE_URL")
                kwargs = {"api_key": api_key}
                if base_url:
                    kwargs["base_url"] = base_url
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

    def summarize_batch(self, symbols: list[Symbol], batch_size: int = 10) -> list[Symbol]:
        """Summarize a batch of symbols using AI.
        
        Only processes symbols that don't already have summaries.
        Returns updated symbols.
        """
        if not self.client:
            # Fall back to signature fallback for all
            for sym in symbols:
                if not sym.summary:
                    sym.summary = signature_fallback(sym)
            return symbols
        
        # Filter symbols needing summarization
        to_summarize = [s for s in symbols if not s.summary and not s.docstring]
        
        if not to_summarize:
            return symbols
        
        # Process in batches
        for i in range(0, len(to_summarize), batch_size):
            batch = to_summarize[i:i + batch_size]
            self._summarize_one_batch(batch)
        
        return symbols
    
    def _summarize_one_batch(self, batch: list[Symbol]):
        """Summarize one batch of symbols."""
        # Build prompt
        prompt = self._build_prompt(batch)
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens_per_batch,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Parse response
            summaries = self._parse_response(response.content[0].text, len(batch))
            
            # Update symbols
            for sym, summary in zip(batch, summaries):
                if summary:
                    sym.summary = summary
                else:
                    sym.summary = signature_fallback(sym)
        
        except Exception:
            # On any error, fall back to signature
            for sym in batch:
                if not sym.summary:
                    sym.summary = signature_fallback(sym)
    
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

        lines.extend([
            "",
            "Output format: NUMBER. SUMMARY",
            "Example: 1. Authenticates users with username and password.",
            "",
            "Summaries:",
        ])

        return "\n".join(lines)

    def _parse_response(self, text: str, expected_count: int) -> list[str]:
        """Parse numbered summaries from response."""
        summaries = [""] * expected_count

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Look for "N. summary" format
            if "." in line:
                parts = line.split(".", 1)
                try:
                    num = int(parts[0].strip())
                    if 1 <= num <= expected_count:
                        summary = parts[1].strip()
                        summaries[num - 1] = summary
                except ValueError:
                    continue

        return summaries


@dataclass
class GeminiBatchSummarizer:
    """AI-based batch summarization using Google Gemini Flash (Tier 2)."""

    model: str = "gemini-2.5-flash-lite"
    max_tokens_per_batch: int = 500

    def __post_init__(self):
        self.client = None
        self._init_client()

    def _init_client(self):
        """Initialize Gemini client if API key is available."""
        try:
            import google.generativeai as genai
            api_key = os.environ.get("GOOGLE_API_KEY")
            if api_key:
                self.model = os.environ.get("GOOGLE_MODEL", self.model)
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

    def summarize_batch(self, symbols: list[Symbol], batch_size: int = 10) -> list[Symbol]:
        """Summarize a batch of symbols using Gemini."""
        if not self.client:
            for sym in symbols:
                if not sym.summary:
                    sym.summary = signature_fallback(sym)
            return symbols

        to_summarize = [s for s in symbols if not s.summary and not s.docstring]

        if not to_summarize:
            return symbols

        for i in range(0, len(to_summarize), batch_size):
            batch = to_summarize[i:i + batch_size]
            self._summarize_one_batch(batch)

        return symbols

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

        except Exception:
            for sym in batch:
                if not sym.summary:
                    sym.summary = signature_fallback(sym)

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

        lines.extend([
            "",
            "Output format: NUMBER. SUMMARY",
            "Example: 1. Authenticates users with username and password.",
            "",
            "Summaries:",
        ])

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
                        summaries[num - 1] = parts[1].strip()
                except ValueError:
                    continue

        return summaries


@dataclass
class OpenAIBatchSummarizer:
    """AI-based batch summarization using OpenAI-compatible endpoints (Tier 2).
    
    Ideal for local LLMs like Ollama (http://localhost:11434/v1) or LM Studio.
    """

    model: str = "qwen3-coder"
    max_tokens_per_batch: int = 500

    def __post_init__(self):
        self.client = None
        self.api_base = os.environ.get("OPENAI_API_BASE")
        if self.api_base:
            # Strip trailing slash if present
            self.api_base = self.api_base.rstrip("/")
            self.model = os.environ.get("OPENAI_MODEL", self.model)
            self.max_tokens_per_batch = int(
                os.environ.get("OPENAI_MAX_TOKENS", str(self.max_tokens_per_batch))
            )
            self._init_client()

    def _init_client(self):
        """Initialize HTTP client for OpenAI requests."""
        try:
            import httpx
            
            timeout_str = os.environ.get("OPENAI_TIMEOUT", "60.0")
            try:
                timeout = float(timeout_str)
            except ValueError:
                timeout = 60.0
                
            api_key = os.environ.get("OPENAI_API_KEY", "local-llm")
            headers = {"Authorization": f"Bearer {api_key}"}
            self.client = httpx.Client(timeout=timeout, headers=headers)
        except ImportError:
            self.client = None

    def summarize_batch(self, symbols: list[Symbol], batch_size: int = 10) -> list[Symbol]:
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

        max_workers = int(os.environ.get("OPENAI_CONCURRENCY", "1"))
        batches = [to_summarize[i:i + batch_size] for i in range(0, len(to_summarize), batch_size)]

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._summarize_one_batch, batch): batch for batch in batches}
            for future in as_completed(futures):
                future.result()

        return symbols

    def _summarize_one_batch(self, batch: list[Symbol]):
        """Summarize one batch of symbols via HTTP POST."""
        prompt = self._build_prompt(batch)

        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": self.max_tokens_per_batch,
                "temperature": 0.0,
            }
            
            response = self.client.post(f"{self.api_base}/chat/completions", json=payload)
            response.raise_for_status()
            
            data = response.json()
            # Extract content from the first choice
            text = data["choices"][0]["message"]["content"]
            summaries = self._parse_response(text, len(batch))

            for sym, summary in zip(batch, summaries):
                if summary:
                    sym.summary = summary
                else:
                    sym.summary = signature_fallback(sym)

        except Exception:
            for sym in batch:
                if not sym.summary:
                    sym.summary = signature_fallback(sym)

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

        lines.extend([
            "",
            "Output format: NUMBER. SUMMARY",
            "Example: 1. Authenticates users with username and password.",
            "",
            "Summaries:",
        ])

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
                        summaries[num - 1] = parts[1].strip()
                except ValueError:
                    continue

        return summaries


def _create_summarizer() -> Optional[BatchSummarizer]:
    """Return the appropriate summarizer based on available API keys.

    Priority: Anthropic > Google Gemini > OpenAI/Local.
    Returns None if no API keys are configured.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        s = BatchSummarizer()
        if s.client:
            return s

    if os.environ.get("GOOGLE_API_KEY"):
        s = GeminiBatchSummarizer()
        if s.client:
            return s
            
    if os.environ.get("OPENAI_API_BASE"):
        s = OpenAIBatchSummarizer()
        if s.client:
            return s

    return None


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
    Tier 2: AI batch summarization (Claude Haiku, Gemini Flash, or Local LLM)
    Tier 3: Signature fallback (always works)

    Provider selection (Tier 2 priority):
      1. ANTHROPIC_API_KEY set → Claude Haiku
      2. GOOGLE_API_KEY set    → Gemini Flash
      3. OPENAI_API_BASE set   → Local LLM via OpenAI compatible endpoint
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
