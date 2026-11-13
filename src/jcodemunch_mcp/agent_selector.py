"""Agent Selector — complexity-based model routing for jCodeMunch.

Assesses request complexity using signals from pre-processing and routes
to appropriate model tiers (low/medium/high). Supports off/manual/auto modes.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Default batting orders ───────────────────────────────────────────────────

DEFAULT_PROVIDERS: dict[str, dict[str, str]] = {
    "anthropic": {
        "low": "claude-haiku-4-5-20251001",
        "medium": "claude-sonnet-4-20250514",
        "high": "claude-opus-4-20250115",
    },
    # gpt-4o-mini and gpt-4o retire 2027-01-14 (OpenAI deprecations, notice of
    # 2026-10-14), both replaced by gpt-6-luna. medium is gpt-6-sol, which OpenAI
    # names as the replacement for gpt-5.1, so the three tiers stay distinct.
    "openai": {
        "low": "gpt-6-luna",
        "medium": "gpt-6-sol",
        "high": "o3",
    },
    "google": {
        "low": "gemini-2.0-flash",
        "medium": "gemini-2.5-pro",
        "high": "gemini-2.5-pro",
    },
}

# ── Default scoring weights ──────────────────────────────────────────────────

DEFAULT_WEIGHTS: dict[str, float] = {
    "retrievalSetSize": 3.0,
    "symbolCount": 0.5,
    "crossFileReferences": 4.0,
    "crossProjectReferences": 25.0,  # binary bonus
    "languageComplexity": 5.0,
    "requestTokenEstimate": 0.001,
}

# ── Language complexity tiers ────────────────────────────────────────────────

_LANG_COMPLEXITY: dict[str, float] = {
    "mixed-legacy": 8.0,
    "mixed": 5.0,
    "legacy": 6.0,
    "complex": 4.0,
    "standard": 2.0,
    "simple": 1.0,
}

TIERS = ("low", "medium", "high")

# ── Default thresholds ───────────────────────────────────────────────────────

DEFAULT_THRESHOLDS = {"lowCeiling": 25, "highFloor": 70}


@dataclass
class ComplexitySignals:
    """Raw signals consumed by the scorer."""

    retrievalSetSize: int = 0
    symbolCount: int = 0
    crossFileReferences: int = 0
    crossProjectReferences: bool = False
    languageComplexity: str = "standard"
    requestTokenEstimate: int = 0


@dataclass
class ComplexityAssessment:
    """Result of complexity scoring."""

    score: int
    tier: str
    signals: dict[str, Any]
    recommendedModel: str
    currentModel: Optional[str] = None


@dataclass
class AgentSelectorConfig:
    """Resolved agent selector configuration."""

    mode: str = "off"  # "off" | "manual" | "auto"
    providers: dict[str, dict[str, str]] = field(default_factory=dict)
    activeProvider: str = "anthropic"
    thresholds: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    verbosePrompts: bool = False

    @classmethod
    def from_config(cls, raw: dict[str, Any], init_overrides: Optional[dict[str, Any]] = None) -> "AgentSelectorConfig":
        """Build config from the agentSelector block + optional init overrides."""
        cfg = cls(
            mode=raw.get("mode", "off"),
            providers=raw.get("providers", {}),
            activeProvider=raw.get("activeProvider", "anthropic"),
            thresholds={**DEFAULT_THRESHOLDS, **raw.get("thresholds", {})},
            weights={**DEFAULT_WEIGHTS, **raw.get("weights", {})},
            verbosePrompts=raw.get("verbosePrompts", False),
        )
        # Apply init overrides (session-level, do not persist)
        if init_overrides:
            if "agentSelector.mode" in init_overrides:
                cfg.mode = init_overrides["agentSelector.mode"]
            if "agentSelector.activeProvider" in init_overrides:
                cfg.activeProvider = init_overrides["agentSelector.activeProvider"]
            if "agentSelector.verbosePrompts" in init_overrides:
                cfg.verbosePrompts = init_overrides["agentSelector.verbosePrompts"]
        # Validate mode
        if cfg.mode not in ("off", "manual", "auto"):
            logger.warning("Invalid agentSelector.mode '%s', falling back to 'off'", cfg.mode)
            cfg.mode = "off"
        return cfg

    def resolve_provider_models(self) -> Optional[dict[str, str]]:
        """Get the model map for the active provider.

        Falls back to DEFAULT_PROVIDERS if user didn't supply a custom block.
        Returns None if provider is not found anywhere (error state).
        """
        if self.activeProvider in self.providers:
            return self.providers[self.activeProvider]
        if self.activeProvider in DEFAULT_PROVIDERS:
            return DEFAULT_PROVIDERS[self.activeProvider]
        return None


# ── ComplexityScorer ─────────────────────────────────────────────────────────

def score_complexity(
    signals: ComplexitySignals,
    config: AgentSelectorConfig,
) -> ComplexityAssessment:
    """Score request complexity using weighted linear heuristics.

    Returns a ComplexityAssessment with score (0-100), tier, and recommended model.
    """
    w = config.weights

    raw = (
        signals.retrievalSetSize * w.get("retrievalSetSize", 3.0)
        + signals.symbolCount * w.get("symbolCount", 0.5)
        + signals.crossFileReferences * w.get("crossFileReferences", 4.0)
        + (1 if signals.crossProjectReferences else 0) * w.get("crossProjectReferences", 25.0)
        + _LANG_COMPLEXITY.get(signals.languageComplexity, 2.0) * w.get("languageComplexity", 5.0)
        + signals.requestTokenEstimate * w.get("requestTokenEstimate", 0.001)
    )

    # Normalize: empirically, raw scores tend to range 0-200 for typical requests.
    # We map 0 → 0, 200 → 100 with clamping.
    score = max(0, min(100, int(raw * 0.5)))

    # Determine tier
    low_ceiling = config.thresholds.get("lowCeiling", 25)
    high_floor = config.thresholds.get("highFloor", 70)
    if score <= low_ceiling:
        tier = "low"
    elif score >= high_floor:
        tier = "high"
    else:
        tier = "medium"

    # Resolve recommended model
    models = config.resolve_provider_models()
    if models is None:
        logger.error(
            "Active provider '%s' not found in providers config or defaults. "
            "Falling back to no recommendation.",
            config.activeProvider,
        )
        recommended = "unknown"
    else:
        recommended = _resolve_tier_model(models, tier)

    return ComplexityAssessment(
        score=score,
        tier=tier,
        signals={
            "retrievalSetSize": signals.retrievalSetSize,
            "symbolCount": signals.symbolCount,
            "crossFileReferences": signals.crossFileReferences,
            "crossProjectReferences": signals.crossProjectReferences,
            "languageComplexity": signals.languageComplexity,
            "requestTokenEstimate": signals.requestTokenEstimate,
        },
        recommendedModel=recommended,
    )


def _resolve_tier_model(models: dict[str, str], tier: str) -> str:
    """Resolve the model for a tier, falling to next-highest if tier is missing."""
    if tier in models:
        return models[tier]
    # Fall to next-highest available tier
    tier_order = list(TIERS)
    idx = tier_order.index(tier)
    # Search upward
    for i in range(idx + 1, len(tier_order)):
        if tier_order[i] in models:
            logger.warning(
                "No '%s' tier model configured; falling back to '%s'",
                tier, tier_order[i],
            )
            return models[tier_order[i]]
    # Search downward
    for i in range(idx - 1, -1, -1):
        if tier_order[i] in models:
            logger.warning(
                "No '%s' tier model configured; falling back to '%s'",
                tier, tier_order[i],
            )
            return models[tier_order[i]]
    # Should not happen if models dict is non-empty
    return next(iter(models.values()))


# ── ModelRouter ──────────────────────────────────────────────────────────────

@dataclass
class RoutingDecision:
    """What the router decided."""

    action: str  # "proceed" | "prompt" | "auto-switch" | "off"
    selectedModel: str
    assessment: ComplexityAssessment
    prompt_text: Optional[str] = None
    metadata_text: Optional[str] = None


def _model_tier(model: str, models: dict[str, str]) -> Optional[str]:
    """Determine which tier a model belongs to."""
    for tier, m in models.items():
        if m == model:
            return tier
    return None


def _tier_rank(tier: str) -> int:
    """Numeric rank for tier comparison."""
    return TIERS.index(tier) if tier in TIERS else 1


def route(
    assessment: ComplexityAssessment,
    config: AgentSelectorConfig,
    current_model: Optional[str] = None,
) -> RoutingDecision:
    """Route a scored request based on mode and config.

    Args:
        assessment: The complexity assessment from score_complexity().
        config: Agent selector configuration.
        current_model: The model currently in use (if known).

    Returns:
        RoutingDecision with action, selected model, and optional prompt/metadata.
    """
    assessment.currentModel = current_model
    models = config.resolve_provider_models()

    # ── Mode: off ────────────────────────────────────────────────────────
    if config.mode == "off" or models is None:
        if models is None and config.mode != "off":
            logger.error(
                "Active provider '%s' not found — falling back to mode=off for this session",
                config.activeProvider,
            )
        return RoutingDecision(
            action="off",
            selectedModel=current_model or "unknown",
            assessment=assessment,
        )

    recommended = assessment.recommendedModel

    # ── Mode: auto ───────────────────────────────────────────────────────
    if config.mode == "auto":
        meta = (
            f"\U0001f916 Agent Selector: Routed to {recommended} "
            f"(score: {assessment.score}/100, tier: {assessment.tier})"
        )
        return RoutingDecision(
            action="auto-switch",
            selectedModel=recommended,
            assessment=assessment,
            metadata_text=meta,
        )

    # ── Mode: manual ─────────────────────────────────────────────────────
    if not current_model:
        # No current model known — just advise, don't prompt
        return RoutingDecision(
            action="proceed",
            selectedModel=recommended,
            assessment=assessment,
        )

    current_tier = _model_tier(current_model, models)
    recommended_tier = assessment.tier

    # Same tier or unknown current model tier → proceed silently
    if current_tier is None or current_tier == recommended_tier:
        return RoutingDecision(
            action="proceed",
            selectedModel=current_model,
            assessment=assessment,
        )

    current_rank = _tier_rank(current_tier)
    recommended_rank = _tier_rank(recommended_tier)

    # Only one model configured → no routing possible
    unique_models = set(models.values())
    if len(unique_models) <= 1:
        return RoutingDecision(
            action="proceed",
            selectedModel=current_model,
            assessment=assessment,
        )

    # Step-up: recommended tier is higher than current
    if recommended_rank > current_rank:
        sig = assessment.signals
        details = []
        if sig.get("crossProjectReferences"):
            details.append("multi-project")
        details.append(f"{sig.get('symbolCount', 0)} symbols")
        if sig.get("crossFileReferences", 0) > 0:
            details.append(f"{sig['crossFileReferences']} cross-file refs")
        detail_str = ", ".join(details)

        prompt = (
            f"\u26a1 Agent Selector: This request scores as complex "
            f"({assessment.score}/100 \u2014 {detail_str}). "
            f"Recommended model: {recommended}. "
            f"Current model: {current_model}.\n\n"
            f"Proceed with current model, or switch? [proceed / switch]"
        )
        return RoutingDecision(
            action="prompt",
            selectedModel=current_model,
            assessment=assessment,
            prompt_text=prompt,
        )

    # Step-down: recommended tier is lower than current
    if recommended_rank < current_rank:
        if config.verbosePrompts:
            sig = assessment.signals
            details = []
            if sig.get("retrievalSetSize", 0) <= 3:
                details.append("single file" if sig["retrievalSetSize"] <= 1 else f"{sig['retrievalSetSize']} files")
            details.append(f"{sig.get('symbolCount', 0)} symbols")
            detail_str = ", ".join(details)

            prompt = (
                f"\U0001f4a1 Agent Selector: This request scores as straightforward "
                f"({assessment.score}/100 \u2014 {detail_str}). "
                f"A lighter model like {recommended} could handle this.\n\n"
                f"Proceed with current model, or switch? [proceed / switch]"
            )
            return RoutingDecision(
                action="prompt",
                selectedModel=current_model,
                assessment=assessment,
                prompt_text=prompt,
            )
        # Suppress step-down by default
        return RoutingDecision(
            action="proceed",
            selectedModel=current_model,
            assessment=assessment,
        )

    # Fallthrough (should not reach here)
    return RoutingDecision(
        action="proceed",
        selectedModel=current_model,
        assessment=assessment,
    )


# ── Extract signals from a CodeIndex ─────────────────────────────────────────

def extract_signals_from_index(index: Any, file_paths: Optional[list[str]] = None) -> ComplexitySignals:
    """Extract complexity signals from a loaded CodeIndex and optional retrieval set.

    Args:
        index: A CodeIndex object from jcodemunch storage.
        file_paths: Optional list of file paths in the retrieval set.

    Returns:
        ComplexitySignals populated from the index data.
    """
    symbols = getattr(index, "symbols", [])
    imports = getattr(index, "imports", {})
    source_files = set(getattr(index, "source_files", []))

    # Retrieval set size
    retrieval_size = len(file_paths) if file_paths else len(source_files)

    # Symbol count
    if file_paths:
        file_set = set(file_paths)
        symbol_count = sum(1 for s in symbols if s.file in file_set)
    else:
        symbol_count = len(symbols)

    # Cross-file references
    cross_refs = 0
    if file_paths:
        file_set = set(file_paths)
        for f in file_paths:
            if f in imports:
                cross_refs += len(imports[f])
    else:
        for imp_list in imports.values():
            cross_refs += len(imp_list)

    # Cross-project: check if index spans multiple source roots
    cross_project = False
    source_root = getattr(index, "source_root", None)
    if source_root and file_paths:
        roots = set()
        for fp in file_paths:
            # Simple heuristic: if path doesn't start with source_root, it's cross-project
            if not fp.startswith(str(source_root)):
                cross_project = True
                break

    # Language complexity
    languages = set()
    if file_paths:
        for s in symbols:
            if s.file in set(file_paths):
                lang = getattr(s, "language", None)
                if lang:
                    languages.add(lang)
    else:
        for s in symbols:
            lang = getattr(s, "language", None)
            if lang:
                languages.add(lang)

    lang_complexity = _classify_language_mix(languages)

    # Token estimate
    total_bytes = 0
    for s in symbols:
        if file_paths and s.file not in set(file_paths):
            continue
        src = getattr(s, "source", None)
        if src:
            total_bytes += len(src)
    token_estimate = total_bytes // 4  # ~4 bytes per token

    return ComplexitySignals(
        retrievalSetSize=retrieval_size,
        symbolCount=symbol_count,
        crossFileReferences=cross_refs,
        crossProjectReferences=cross_project,
        languageComplexity=lang_complexity,
        requestTokenEstimate=token_estimate,
    )


_LEGACY_LANGS = {"vb", "vb_net", "fortran", "cobol", "pascal", "delphi"}
_COMPLEX_LANGS = {"cpp", "c", "arduino", "vhdl", "verilog", "rust", "haskell", "scala"}


def _classify_language_mix(languages: set[str]) -> str:
    """Classify language mix complexity."""
    if not languages:
        return "standard"

    has_legacy = bool(languages & _LEGACY_LANGS)
    has_complex = bool(languages & _COMPLEX_LANGS)

    if has_legacy and len(languages) > 1:
        return "mixed-legacy"
    if has_legacy:
        return "legacy"
    if len(languages) > 2 and has_complex:
        return "mixed"
    if has_complex:
        return "complex"
    if len(languages) <= 1:
        return "simple"
    return "standard"
