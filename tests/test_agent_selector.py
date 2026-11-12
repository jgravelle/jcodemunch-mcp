"""Tests for the Agent Selector — ComplexityScorer, ModelRouter, and config."""

import pytest

from jcodemunch_mcp.agent_selector import (
    AgentSelectorConfig,
    ComplexityAssessment,
    ComplexitySignals,
    DEFAULT_PROVIDERS,
    DEFAULT_THRESHOLDS,
    DEFAULT_WEIGHTS,
    RoutingDecision,
    _classify_language_mix,
    _resolve_tier_model,
    extract_signals_from_index,
    route,
    score_complexity,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _default_config(**overrides) -> AgentSelectorConfig:
    """Build a default config with optional overrides."""
    return AgentSelectorConfig(
        mode=overrides.get("mode", "auto"),
        providers=overrides.get("providers", dict(DEFAULT_PROVIDERS)),
        activeProvider=overrides.get("activeProvider", "anthropic"),
        thresholds=overrides.get("thresholds", dict(DEFAULT_THRESHOLDS)),
        weights=overrides.get("weights", dict(DEFAULT_WEIGHTS)),
        verbosePrompts=overrides.get("verbosePrompts", False),
    )


# ── ComplexityScorer Tests ───────────────────────────────────────────────────

class TestComplexityScorer:
    """Tests for score_complexity()."""

    def test_minimal_signals_score_low(self):
        """A trivial request with minimal signals should score low."""
        signals = ComplexitySignals(
            retrievalSetSize=1,
            symbolCount=5,
            crossFileReferences=0,
            crossProjectReferences=False,
            languageComplexity="simple",
            requestTokenEstimate=200,
        )
        config = _default_config()
        assessment = score_complexity(signals, config)
        assert assessment.score <= 25
        assert assessment.tier == "low"

    def test_complex_signals_score_high(self):
        """A complex multi-project request should score high."""
        signals = ComplexitySignals(
            retrievalSetSize=20,
            symbolCount=100,
            crossFileReferences=30,
            crossProjectReferences=True,
            languageComplexity="mixed-legacy",
            requestTokenEstimate=25000,
        )
        config = _default_config()
        assessment = score_complexity(signals, config)
        assert assessment.score >= 70
        assert assessment.tier == "high"

    def test_medium_signals_score_medium(self):
        """A moderately complex request should score medium."""
        signals = ComplexitySignals(
            retrievalSetSize=8,
            symbolCount=30,
            crossFileReferences=10,
            crossProjectReferences=False,
            languageComplexity="standard",
            requestTokenEstimate=5000,
        )
        config = _default_config()
        assessment = score_complexity(signals, config)
        assert 25 < assessment.score < 70
        assert assessment.tier == "medium"

    def test_score_clamped_to_0_100(self):
        """Score should always be in [0, 100]."""
        # Extreme signals
        signals = ComplexitySignals(
            retrievalSetSize=1000,
            symbolCount=10000,
            crossFileReferences=5000,
            crossProjectReferences=True,
            languageComplexity="mixed-legacy",
            requestTokenEstimate=500000,
        )
        config = _default_config()
        assessment = score_complexity(signals, config)
        assert 0 <= assessment.score <= 100

        # Zero signals
        signals = ComplexitySignals()
        assessment = score_complexity(signals, config)
        assert 0 <= assessment.score <= 100

    def test_threshold_boundary_low_ceiling(self):
        """Score exactly at lowCeiling should be 'low'."""
        config = _default_config(thresholds={"lowCeiling": 50, "highFloor": 70})
        # Craft signals to hit exactly 50
        signals = ComplexitySignals(
            retrievalSetSize=10,
            symbolCount=20,
            crossFileReferences=10,
            crossProjectReferences=False,
            languageComplexity="standard",
            requestTokenEstimate=0,
        )
        assessment = score_complexity(signals, config)
        # We can't guarantee exact score=50 but test the boundary logic
        if assessment.score <= 50:
            assert assessment.tier == "low"
        else:
            assert assessment.tier in ("medium", "high")

    def test_threshold_boundary_high_floor(self):
        """Score at or above highFloor should be 'high'."""
        config = _default_config(thresholds={"lowCeiling": 25, "highFloor": 70})
        signals = ComplexitySignals(
            retrievalSetSize=25,
            symbolCount=100,
            crossFileReferences=30,
            crossProjectReferences=True,
            languageComplexity="complex",
            requestTokenEstimate=20000,
        )
        assessment = score_complexity(signals, config)
        if assessment.score >= 70:
            assert assessment.tier == "high"

    def test_custom_weights(self):
        """Custom weights should affect scoring."""
        signals = ComplexitySignals(
            retrievalSetSize=5,
            symbolCount=10,
            crossFileReferences=5,
            crossProjectReferences=False,
            languageComplexity="standard",
            requestTokenEstimate=1000,
        )
        # Default weights
        config1 = _default_config()
        score1 = score_complexity(signals, config1).score

        # Cranked up retrieval weight
        heavy_weights = dict(DEFAULT_WEIGHTS)
        heavy_weights["retrievalSetSize"] = 20.0
        config2 = _default_config(weights=heavy_weights)
        score2 = score_complexity(signals, config2).score

        assert score2 > score1

    def test_recommended_model_matches_tier(self):
        """The recommended model should match the scored tier."""
        signals = ComplexitySignals(
            retrievalSetSize=1,
            symbolCount=3,
            requestTokenEstimate=100,
        )
        config = _default_config()
        assessment = score_complexity(signals, config)
        tier = assessment.tier
        expected_model = DEFAULT_PROVIDERS["anthropic"][tier]
        assert assessment.recommendedModel == expected_model

    def test_signals_in_assessment(self):
        """Assessment should include all signal values."""
        signals = ComplexitySignals(
            retrievalSetSize=5,
            symbolCount=20,
            crossFileReferences=8,
            crossProjectReferences=True,
            languageComplexity="mixed",
            requestTokenEstimate=3000,
        )
        config = _default_config()
        assessment = score_complexity(signals, config)
        assert assessment.signals["retrievalSetSize"] == 5
        assert assessment.signals["symbolCount"] == 20
        assert assessment.signals["crossFileReferences"] == 8
        assert assessment.signals["crossProjectReferences"] is True
        assert assessment.signals["languageComplexity"] == "mixed"
        assert assessment.signals["requestTokenEstimate"] == 3000


# ── ModelRouter Tests ────────────────────────────────────────────────────────

class TestModelRouter:
    """Tests for route()."""

    def _make_assessment(self, score: int, tier: str, recommended: str) -> ComplexityAssessment:
        return ComplexityAssessment(
            score=score,
            tier=tier,
            signals={},
            recommendedModel=recommended,
        )

    def test_mode_off(self):
        """Mode=off should return action='off' with no prompts."""
        config = _default_config(mode="off")
        assessment = self._make_assessment(80, "high", "claude-opus-5-5")
        decision = route(assessment, config, current_model="claude-sonnet-5-5")
        assert decision.action == "off"
        assert decision.prompt_text is None
        assert decision.metadata_text is None

    def test_mode_auto_routes(self):
        """Mode=auto should auto-switch and provide metadata."""
        config = _default_config(mode="auto")
        assessment = self._make_assessment(80, "high", "claude-opus-5-5")
        decision = route(assessment, config, current_model="claude-sonnet-5-5")
        assert decision.action == "auto-switch"
        assert decision.selectedModel == "claude-opus-5-5"
        assert decision.metadata_text is not None
        assert "claude-opus-5-5" in decision.metadata_text
        assert "80/100" in decision.metadata_text

    def test_manual_step_up_prompt(self):
        """Manual mode should prompt on step-up."""
        config = _default_config(mode="manual")
        assessment = self._make_assessment(80, "high", "claude-opus-5-5")
        assessment.signals = {"symbolCount": 87, "crossFileReferences": 23, "crossProjectReferences": True}
        decision = route(assessment, config, current_model="claude-sonnet-5-5")
        assert decision.action == "prompt"
        assert decision.prompt_text is not None
        assert "complex" in decision.prompt_text
        assert "switch" in decision.prompt_text.lower()

    def test_manual_step_down_suppressed_by_default(self):
        """Manual mode with verbosePrompts=false should suppress step-down."""
        config = _default_config(mode="manual", verbosePrompts=False)
        assessment = self._make_assessment(15, "low", "claude-haiku-5-5")
        decision = route(assessment, config, current_model="claude-opus-5-5")
        assert decision.action == "proceed"
        assert decision.prompt_text is None

    def test_manual_step_down_verbose(self):
        """Manual mode with verbosePrompts=true should prompt on step-down."""
        config = _default_config(mode="manual", verbosePrompts=True)
        assessment = self._make_assessment(15, "low", "claude-haiku-5-5")
        assessment.signals = {"retrievalSetSize": 1, "symbolCount": 5}
        decision = route(assessment, config, current_model="claude-opus-5-5")
        assert decision.action == "prompt"
        assert decision.prompt_text is not None
        assert "straightforward" in decision.prompt_text

    def test_manual_same_tier_silent(self):
        """Manual mode should proceed silently when tiers match."""
        config = _default_config(mode="manual")
        assessment = self._make_assessment(50, "medium", "claude-sonnet-5-5")
        decision = route(assessment, config, current_model="claude-sonnet-5-5")
        assert decision.action == "proceed"
        assert decision.prompt_text is None

    def test_manual_no_current_model(self):
        """Manual mode without a current model should proceed without prompt."""
        config = _default_config(mode="manual")
        assessment = self._make_assessment(80, "high", "claude-opus-5-5")
        decision = route(assessment, config, current_model=None)
        assert decision.action == "proceed"

    def test_single_model_provider_no_routing(self):
        """Single-model provider should proceed silently (no routing possible)."""
        config = _default_config(
            mode="manual",
            providers={"anthropic": {"low": "claude-sonnet-5-5", "medium": "claude-sonnet-5-5", "high": "claude-sonnet-5-5"}},
        )
        assessment = self._make_assessment(80, "high", "claude-sonnet-5-5")
        decision = route(assessment, config, current_model="claude-sonnet-5-5")
        assert decision.action == "proceed"

    def test_missing_provider_falls_back_to_off(self):
        """Missing active provider should fall back to mode=off."""
        config = _default_config(mode="auto", activeProvider="nonexistent", providers={})
        assessment = self._make_assessment(80, "high", "unknown")
        decision = route(assessment, config, current_model="some-model")
        assert decision.action == "off"


# ── Tier Resolution Tests ────────────────────────────────────────────────────

class TestTierResolution:
    """Tests for _resolve_tier_model edge cases."""

    def test_exact_tier_match(self):
        models = {"low": "haiku", "medium": "sonnet", "high": "opus"}
        assert _resolve_tier_model(models, "low") == "haiku"
        assert _resolve_tier_model(models, "medium") == "sonnet"
        assert _resolve_tier_model(models, "high") == "opus"

    def test_missing_low_falls_to_medium(self):
        models = {"medium": "sonnet", "high": "opus"}
        assert _resolve_tier_model(models, "low") == "sonnet"

    def test_missing_high_falls_to_medium(self):
        models = {"low": "haiku", "medium": "sonnet"}
        assert _resolve_tier_model(models, "high") == "sonnet"

    def test_only_one_model(self):
        models = {"medium": "sonnet"}
        assert _resolve_tier_model(models, "low") == "sonnet"
        assert _resolve_tier_model(models, "high") == "sonnet"


# ── Config Tests ─────────────────────────────────────────────────────────────

class TestAgentSelectorConfig:
    """Tests for AgentSelectorConfig construction and overrides."""

    def test_defaults(self):
        config = AgentSelectorConfig.from_config({})
        assert config.mode == "off"
        assert config.activeProvider == "anthropic"
        assert config.verbosePrompts is False
        assert config.thresholds == DEFAULT_THRESHOLDS

    def test_custom_config(self):
        raw = {
            "mode": "auto",
            "activeProvider": "openai",
            "verbosePrompts": True,
            "thresholds": {"lowCeiling": 30, "highFloor": 80},
        }
        config = AgentSelectorConfig.from_config(raw)
        assert config.mode == "auto"
        assert config.activeProvider == "openai"
        assert config.verbosePrompts is True
        assert config.thresholds["lowCeiling"] == 30
        assert config.thresholds["highFloor"] == 80

    def test_init_overrides(self):
        raw = {"mode": "off", "activeProvider": "anthropic"}
        overrides = {
            "agentSelector.mode": "manual",
            "agentSelector.activeProvider": "openai",
            "agentSelector.verbosePrompts": True,
        }
        config = AgentSelectorConfig.from_config(raw, init_overrides=overrides)
        assert config.mode == "manual"
        assert config.activeProvider == "openai"
        assert config.verbosePrompts is True

    def test_invalid_mode_falls_back_to_off(self):
        config = AgentSelectorConfig.from_config({"mode": "invalid"})
        assert config.mode == "off"

    def test_resolve_provider_from_defaults(self):
        """Provider not in user config should fall back to DEFAULT_PROVIDERS."""
        config = AgentSelectorConfig.from_config({"activeProvider": "google"})
        models = config.resolve_provider_models()
        assert models is not None
        assert models["low"] == "gemini-3.6-flash"

    def test_resolve_unknown_provider_returns_none(self):
        config = AgentSelectorConfig.from_config({"activeProvider": "nonexistent"})
        assert config.resolve_provider_models() is None

    def test_custom_provider_overrides_default(self):
        raw = {
            "activeProvider": "anthropic",
            "providers": {
                "anthropic": {
                    "low": "my-haiku",
                    "medium": "my-sonnet",
                    "high": "my-opus",
                }
            },
        }
        config = AgentSelectorConfig.from_config(raw)
        models = config.resolve_provider_models()
        assert models["low"] == "my-haiku"


# ── Language Classification Tests ────────────────────────────────────────────

class TestLanguageClassification:
    """Tests for _classify_language_mix."""

    def test_empty(self):
        assert _classify_language_mix(set()) == "standard"

    def test_single_simple(self):
        assert _classify_language_mix({"python"}) == "simple"

    def test_legacy_single(self):
        assert _classify_language_mix({"vb_net"}) == "legacy"

    def test_legacy_mixed(self):
        assert _classify_language_mix({"vb_net", "csharp"}) == "mixed-legacy"

    def test_complex_single(self):
        assert _classify_language_mix({"rust"}) == "complex"

    def test_complex_mixed(self):
        assert _classify_language_mix({"rust", "cpp", "python"}) == "mixed"

    def test_standard_multi(self):
        assert _classify_language_mix({"python", "javascript"}) == "standard"


# ── Default Providers Tests ──────────────────────────────────────────────────

class TestDefaultProviders:
    """Tests for default batting orders."""

    def test_all_providers_have_three_tiers(self):
        for provider, models in DEFAULT_PROVIDERS.items():
            for tier in ("low", "medium", "high"):
                assert tier in models, f"{provider} missing {tier} tier"

    def test_anthropic_defaults(self):
        assert DEFAULT_PROVIDERS["anthropic"]["low"] == "claude-haiku-5-5"
        assert DEFAULT_PROVIDERS["anthropic"]["medium"] == "claude-sonnet-5-5"
        assert DEFAULT_PROVIDERS["anthropic"]["high"] == "claude-opus-5-5"

    def test_openai_defaults(self):
        assert DEFAULT_PROVIDERS["openai"]["low"] == "gpt-4o-mini"
        assert DEFAULT_PROVIDERS["openai"]["high"] == "o3"
