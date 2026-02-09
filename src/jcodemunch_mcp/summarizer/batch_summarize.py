"""Three-tier summarization: docstring > AI (Haiku) > signature fallback."""

import os
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
                self.client = Anthropic(api_key=api_key)
        except ImportError:
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
            "Focus on what it does, not how.",
            "",
            "Input:",
        ]
        
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
    Tier 2: AI batch summarization (Haiku)
    Tier 3: Signature fallback (always works)
    """
    # Tier 1: Extract from docstrings
    for sym in symbols:
        if sym.docstring and not sym.summary:
            sym.summary = extract_summary_from_docstring(sym.docstring)
    
    # Tier 2: AI summarization for remaining symbols
    if use_ai:
        summarizer = BatchSummarizer()
        symbols = summarizer.summarize_batch(symbols)
    
    # Tier 3: Signature fallback for any still missing
    for sym in symbols:
        if not sym.summary:
            sym.summary = signature_fallback(sym)
    
    return symbols
