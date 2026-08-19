"""#488: an explicit local model outranks the zero-config default.

`_detect_provider` returned the bundled ONNX encoder at priority 0, so once
`[local-embed]` was installed every lower branch became unreachable *by
configuration*. `embed_model` / `JCODEMUNCH_EMBED_MODEL` was read after the
early return and changed nothing — no warning, no log line, no field in any
response. The config comment documented the opposite precedence, which is how it
stayed invisible.

⚠⚠ Only the FREE, on-machine option was promoted. Gemini and OpenAI stay below
the bundled encoder, and that is the load-bearing half of this change: see
`tests/test_paid_embeddings_optin.py`, which exists because jdocmunch's resolver
auto-selected OpenAI from an ambient key and began billing a remote account and
shipping the indexed corpus off the machine. `embed_model` is free and local, so
promoting it costs a re-embed; promoting a cloud provider costs money and
exfiltrates the corpus. Not the same decision, not the same answer.
"""

from unittest import mock

import pytest

# ⚠ Module reference, not a from-import of `_detect_provider_detailed`. That
# symbol does not exist pre-fix, so importing it by name turns the non-vacuity
# pass into a COLLECTION ERROR — which proves the function is new and nothing
# about the behaviour. Referencing through the module lets each test fail on its
# own terms.
from jcodemunch_mcp.tools import embed_repo as er
from jcodemunch_mcp.tools.embed_repo import _detect_provider


def _detect_provider_detailed():
    return er._detect_provider_detailed()

_ENV = (
    "JCODEMUNCH_EMBED_MODEL",
    "GOOGLE_API_KEY", "GOOGLE_EMBED_MODEL",
    "OPENAI_API_KEY", "OPENAI_EMBED_MODEL",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _with_onnx(available=True):
    return (
        mock.patch(
            "jcodemunch_mcp.embeddings.local_encoder.is_onnxruntime_available",
            return_value=available,
        ),
        mock.patch(
            "jcodemunch_mcp.embeddings.local_encoder.is_model_available",
            return_value=available,
        ),
    )


def _backend(available):
    return mock.patch(
        "jcodemunch_mcp.tools.embed_repo._backend_available", return_value=available
    )


class TestAnExplicitLocalModelWins:
    def test_embed_model_outranks_the_bundled_encoder(self, clean_env):
        clean_env.setenv("JCODEMUNCH_EMBED_MODEL", "BAAI/bge-base-en-v1.5")
        onnx_rt, onnx_model = _with_onnx(True)
        with onnx_rt, onnx_model, _backend(True):
            provider, model = _detect_provider()
        assert provider == "sentence_transformers"
        assert model == "BAAI/bge-base-en-v1.5"

    def test_the_reason_names_why(self, clean_env):
        clean_env.setenv("JCODEMUNCH_EMBED_MODEL", "BAAI/bge-base-en-v1.5")
        onnx_rt, onnx_model = _with_onnx(True)
        with onnx_rt, onnx_model, _backend(True):
            selected, reason, skipped = _detect_provider_detailed()
        assert selected[0] == "sentence_transformers"
        assert reason == "embed_model"
        assert skipped == []

    def test_nothing_configured_still_gets_the_default(self, clean_env):
        """A zero-config install must behave exactly as before."""
        onnx_rt, onnx_model = _with_onnx(True)
        with onnx_rt, onnx_model:
            selected, reason, _ = _detect_provider_detailed()
        assert selected == ("local_onnx", "all-MiniLM-L6-v2")
        assert reason == "bundled_default"


class TestPaidRemoteProvidersStayBelowTheDefault:
    """⚠⚠ The half that must not move. Reversing these bills the user and sends
    their corpus off the machine."""

    @pytest.mark.parametrize(
        "key,model_var,value",
        [
            ("GOOGLE_API_KEY", "GOOGLE_EMBED_MODEL", "models/embedding-001"),
            ("OPENAI_API_KEY", "OPENAI_EMBED_MODEL", "text-embedding-3-small"),
        ],
    )
    def test_a_cloud_provider_does_not_outrank_onnx(
        self, clean_env, key, model_var, value
    ):
        clean_env.setenv(key, "not-a-real-key")
        clean_env.setenv(model_var, value)
        onnx_rt, onnx_model = _with_onnx(True)
        with onnx_rt, onnx_model:
            provider, _ = _detect_provider()
        assert provider == "local_onnx", (
            "a configured cloud provider was selected over the free on-machine "
            "encoder — this bills the user and ships the corpus off the box"
        )

    def test_a_cloud_provider_is_still_reachable_without_onnx(self, clean_env):
        """Below the default is not the same as removed."""
        clean_env.setenv("OPENAI_API_KEY", "not-a-real-key")
        clean_env.setenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
        onnx_rt, onnx_model = _with_onnx(False)
        with onnx_rt, onnx_model:
            provider, model = _detect_provider()
        assert provider == "openai"
        assert model == "text-embedding-3-small"

    def test_embed_model_still_beats_a_cloud_provider(self, clean_env):
        """The pre-existing ordering among the explicit providers is unchanged."""
        clean_env.setenv("JCODEMUNCH_EMBED_MODEL", "all-MiniLM-L6-v2")
        clean_env.setenv("GOOGLE_API_KEY", "fake-key")
        clean_env.setenv("GOOGLE_EMBED_MODEL", "models/embedding-001")
        onnx_rt, onnx_model = _with_onnx(False)
        with onnx_rt, onnx_model:
            provider, _ = _detect_provider()
        assert provider == "sentence_transformers"


class TestAnUnusableSettingIsSkippedNotHonoured:
    """⚠ The probe decides PRECEDENCE, never selection."""

    def test_an_uninstalled_backend_does_not_displace_a_working_onnx(self, clean_env):
        """Promoting it would trade a silently-ignored setting for an
        ImportError at embed time — the same defect, louder."""
        clean_env.setenv("JCODEMUNCH_EMBED_MODEL", "BAAI/bge-base-en-v1.5")
        onnx_rt, onnx_model = _with_onnx(True)
        with onnx_rt, onnx_model, _backend(False):
            selected, reason, skipped = _detect_provider_detailed()
        assert selected == ("local_onnx", "all-MiniLM-L6-v2")
        assert reason == "bundled_default"
        assert len(skipped) == 1
        assert "BAAI/bge-base-en-v1.5" in skipped[0]
        assert "jcodemunch-mcp[semantic]" in skipped[0], (
            "the skip must name the remedy, or it is the original silent "
            "discard with extra steps"
        )

    def test_without_onnx_the_setting_is_still_selected(self, clean_env):
        """⚠ Probing unconditionally broke this on every machine without the
        package. With no ONNX there is nothing to protect, and returning the
        provider is what hands the caller the actionable pip-install error
        instead of a bare None."""
        clean_env.setenv("JCODEMUNCH_EMBED_MODEL", "BAAI/bge-base-en-v1.5")
        onnx_rt, onnx_model = _with_onnx(False)
        with onnx_rt, onnx_model, _backend(False):
            selected, reason, skipped = _detect_provider_detailed()
        assert selected == ("sentence_transformers", "BAAI/bge-base-en-v1.5")
        assert reason == "embed_model"
        assert skipped == []


class TestTheWrapperIsUnchangedForCallers:
    def test_detect_provider_still_returns_a_two_tuple(self, clean_env):
        clean_env.setenv("JCODEMUNCH_EMBED_MODEL", "all-MiniLM-L6-v2")
        onnx_rt, onnx_model = _with_onnx(False)
        with onnx_rt, onnx_model:
            result = _detect_provider()
        assert isinstance(result, tuple) and len(result) == 2

    def test_none_when_nothing_is_available(self, clean_env):
        onnx_rt, onnx_model = _with_onnx(False)
        with onnx_rt, onnx_model:
            assert _detect_provider() is None


class TestTheConfigCommentMatchesTheCode:
    """The comment documenting the OPPOSITE precedence is why this stayed
    invisible: it was the only place `embed_model` was documented."""

    def test_the_comment_states_the_shipped_precedence(self):
        import pathlib

        from jcodemunch_mcp import config as config_module

        text = pathlib.Path(config_module.__file__).read_text(encoding="utf-8")
        block = text.split('// "embed_model": ""', 1)[1][:800]
        assert "ONNX" in block, (
            "the embed_model comment does not mention the encoder it now "
            "outranks, which is the omission #488 reported"
        )
