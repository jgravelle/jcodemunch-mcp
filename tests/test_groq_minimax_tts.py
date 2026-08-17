"""Tests for the MiniMax T2A text-to-speech backend."""

import io
import wave
from unittest.mock import MagicMock, patch

import pytest

from jcodemunch_mcp.groq import tts


def _make_wav_bytes(duration_s: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Create minimal WAV bytes for testing."""
    n_frames = int(sample_rate * duration_s)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


def _ok_response(audio: bytes) -> dict:
    return {
        "data": {"audio": audio.hex(), "status": tts.MINIMAX_T2A_STATUS_DONE},
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }


@pytest.fixture(autouse=True)
def _clear_tts_env(monkeypatch):
    """Keep the developer's own environment out of every case here."""
    for name in (
        tts.TTS_PROVIDER_ENV,
        tts.MINIMAX_API_KEY_ENV,
        tts.MINIMAX_REGION_ENV,
        tts.MINIMAX_MODEL_ENV,
        tts.MINIMAX_VOICE_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


class TestRegionalEndpoints:
    def test_both_regions_have_distinct_endpoints(self):
        assert set(tts.MINIMAX_T2A_ENDPOINTS) == {
            tts.MINIMAX_REGION_GLOBAL,
            tts.MINIMAX_REGION_CN,
        }
        global_url = tts.MINIMAX_T2A_ENDPOINTS[tts.MINIMAX_REGION_GLOBAL]
        cn_url = tts.MINIMAX_T2A_ENDPOINTS[tts.MINIMAX_REGION_CN]
        assert global_url != cn_url
        assert global_url.endswith("/v1/t2a_v2")
        assert cn_url.endswith("/v1/t2a_v2")

    def test_default_region_is_used_when_unset(self):
        assert tts.minimax_t2a_url() == tts.MINIMAX_T2A_ENDPOINTS[tts.MINIMAX_DEFAULT_REGION]

    def test_region_can_be_selected_by_env_var(self, monkeypatch):
        monkeypatch.setenv(tts.MINIMAX_REGION_ENV, tts.MINIMAX_REGION_CN)
        assert tts.minimax_t2a_url() == tts.MINIMAX_T2A_ENDPOINTS[tts.MINIMAX_REGION_CN]

    def test_explicit_region_beats_env_var(self, monkeypatch):
        monkeypatch.setenv(tts.MINIMAX_REGION_ENV, tts.MINIMAX_REGION_CN)
        assert tts.minimax_t2a_url(tts.MINIMAX_REGION_GLOBAL) == (
            tts.MINIMAX_T2A_ENDPOINTS[tts.MINIMAX_REGION_GLOBAL]
        )

    def test_unknown_region_raises_rather_than_falling_back(self):
        # A silent fallback would send a region-scoped key to the wrong region.
        with pytest.raises(ValueError, match="Unknown T2A region"):
            tts.minimax_t2a_url("mars")


class TestModelFamily:
    def test_speech_28_family_is_available(self):
        assert "speech-2.8-hd" in tts.MINIMAX_T2A_MODELS
        assert "speech-2.8-turbo" in tts.MINIMAX_T2A_MODELS

    def test_default_model_is_a_known_model(self):
        assert tts.MINIMAX_DEFAULT_T2A_MODEL in tts.MINIMAX_T2A_MODELS

    def test_model_can_be_selected_by_env_var(self, monkeypatch):
        monkeypatch.setenv(tts.MINIMAX_MODEL_ENV, "speech-2.6-turbo")
        assert tts.build_minimax_t2a_payload("hi")["model"] == "speech-2.6-turbo"

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError, match="Unknown T2A model"):
            tts.build_minimax_t2a_payload("hi", model="speech-9000")


class TestRequestPayload:
    def test_required_fields_are_always_present(self):
        payload = tts.build_minimax_t2a_payload("hello world")
        for field in tts.MINIMAX_T2A_REQUIRED_FIELDS:
            assert field in payload
        assert payload["text"] == "hello world"
        assert payload["model"] == tts.MINIMAX_DEFAULT_T2A_MODEL

    def test_payload_only_uses_documented_request_fields(self):
        payload = tts.build_minimax_t2a_payload(
            "hello",
            language_boost="English",
            pronunciation_dict={"tone": ["read/(ri:d)"]},
            voice_modify={"pitch": 1},
            stream=False,
            subtitle_enable=True,
            voice_id="some_voice",
        )
        assert set(payload) <= set(tts.MINIMAX_T2A_REQUEST_FIELDS)

    def test_container_format_lives_in_audio_setting(self):
        # `output_format` is the response encoding, not the container format.
        payload = tts.build_minimax_t2a_payload("hi", audio_format="wav")
        assert payload["audio_setting"]["format"] == "wav"
        assert payload["output_format"] == tts.MINIMAX_OUTPUT_FORMAT_HEX

    def test_caller_audio_setting_is_preserved(self):
        payload = tts.build_minimax_t2a_payload(
            "hi", audio_setting={"sample_rate": 32000}, audio_format="mp3"
        )
        assert payload["audio_setting"]["sample_rate"] == 32000
        assert payload["audio_setting"]["format"] == "mp3"

    def test_unsupported_audio_format_raises(self):
        with pytest.raises(ValueError, match="Unsupported T2A audio format"):
            tts.build_minimax_t2a_payload("hi", audio_format="ogg")

    def test_unsupported_output_format_raises(self):
        with pytest.raises(ValueError, match="Unsupported T2A output format"):
            tts.build_minimax_t2a_payload("hi", output_format="base64")

    def test_voice_setting_is_omitted_when_no_voice_is_configured(self):
        assert "voice_setting" not in tts.build_minimax_t2a_payload("hi")

    def test_voice_id_from_env_var_populates_voice_setting(self, monkeypatch):
        monkeypatch.setenv(tts.MINIMAX_VOICE_ENV, "some_voice")
        payload = tts.build_minimax_t2a_payload("hi")
        assert payload["voice_setting"]["voice_id"] == "some_voice"

    def test_voice_setting_without_voice_id_raises(self):
        with pytest.raises(ValueError, match="requires a voice_id"):
            tts.build_minimax_t2a_payload("hi", voice_setting={"speed": 1.2})

    def test_voice_setting_keeps_other_subfields(self):
        payload = tts.build_minimax_t2a_payload(
            "hi", voice_setting={"speed": 1.2}, voice_id="some_voice"
        )
        assert payload["voice_setting"] == {"speed": 1.2, "voice_id": "some_voice"}

    def test_empty_text_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            tts.build_minimax_t2a_payload("   ")


class TestResponseParsing:
    def test_hex_audio_is_decoded(self):
        wav = _make_wav_bytes(0.25)
        assert tts.parse_minimax_t2a_response(_ok_response(wav)) == wav

    def test_decoded_wav_is_readable_by_the_wave_module(self):
        # Both call sites read the sample rate out of the WAV header.
        wav = _make_wav_bytes(0.5, sample_rate=24000)
        decoded = tts.parse_minimax_t2a_response(_ok_response(wav))
        with wave.open(io.BytesIO(decoded), "rb") as wf:
            assert wf.getframerate() == 24000

    def test_envelope_failure_raises_even_on_a_200(self):
        payload = {
            "data": None,
            "base_resp": {"status_code": 1004, "status_msg": "auth failed"},
        }
        with pytest.raises(RuntimeError, match="status_code=1004"):
            tts.parse_minimax_t2a_response(payload)

    def test_incomplete_synthesis_raises(self):
        payload = {
            "data": {"audio": "00", "status": tts.MINIMAX_T2A_STATUS_SYNTHESIZING},
            "base_resp": {"status_code": 0},
        }
        with pytest.raises(RuntimeError, match="not complete"):
            tts.parse_minimax_t2a_response(payload)

    def test_null_data_raises_rather_than_returning_empty_audio(self):
        payload = {"data": None, "base_resp": {"status_code": 0}}
        with pytest.raises(RuntimeError, match="no audio"):
            tts.parse_minimax_t2a_response(payload)

    def test_non_hex_audio_raises(self):
        payload = {
            "data": {"audio": "not-hex", "status": tts.MINIMAX_T2A_STATUS_DONE},
            "base_resp": {"status_code": 0},
        }
        with pytest.raises(RuntimeError, match="not hex-encoded"):
            tts.parse_minimax_t2a_response(payload)


class TestOperationTable:
    def test_http_operation_targets_the_documented_path(self):
        assert tts.MINIMAX_T2A_OPERATIONS["textToAudioHttp"] == ("POST", "/v1/t2a_v2")

    def test_async_and_socket_operations_are_declared(self):
        assert tts.MINIMAX_T2A_OPERATIONS["textToAudioAsyncCreate"][1] == "/v1/t2a_async_v2"
        assert tts.MINIMAX_T2A_OPERATIONS["textToAudioAsyncQuery"][1] == (
            "/v1/query/t2a_async_query_v2"
        )
        assert tts.MINIMAX_T2A_OPERATIONS["textToAudioWebSocket"] == ("WSS", "/ws/v1/t2a_v2")

    def test_every_declared_endpoint_path_matches_the_http_operation(self):
        path = tts.MINIMAX_T2A_OPERATIONS["textToAudioHttp"][1]
        for url in tts.MINIMAX_T2A_ENDPOINTS.values():
            assert url.endswith(path)


class TestSynthesizeMinimax:
    def _mock_post(self, wav: bytes):
        mock_response = MagicMock()
        mock_response.json.return_value = _ok_response(wav)
        mock_post = MagicMock(return_value=mock_response)
        return mock_post

    def test_posts_to_the_regional_endpoint_with_bearer_auth(self, monkeypatch):
        monkeypatch.setenv(tts.MINIMAX_API_KEY_ENV, "unit-test-key")
        wav = _make_wav_bytes(0.25)
        mock_post = self._mock_post(wav)

        with patch("httpx.post", mock_post):
            assert tts.synthesize_minimax("hello", region=tts.MINIMAX_REGION_CN) == wav

        url = mock_post.call_args[0][0]
        assert url == tts.MINIMAX_T2A_ENDPOINTS[tts.MINIMAX_REGION_CN]
        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"].startswith("Bearer ")
        assert headers["Content-Type"] == "application/json"

    def test_sends_model_and_text(self, monkeypatch):
        monkeypatch.setenv(tts.MINIMAX_API_KEY_ENV, "unit-test-key")
        mock_post = self._mock_post(_make_wav_bytes(0.1))

        with patch("httpx.post", mock_post):
            tts.synthesize_minimax("hello there", model="speech-2.8-turbo")

        sent = mock_post.call_args[1]["json"]
        assert sent["model"] == "speech-2.8-turbo"
        assert sent["text"] == "hello there"
        assert sent["audio_setting"]["format"] == "wav"

    def test_missing_api_key_raises_before_any_request(self):
        mock_post = MagicMock()
        with patch("httpx.post", mock_post):
            with pytest.raises(RuntimeError, match=tts.MINIMAX_API_KEY_ENV):
                tts.synthesize_minimax("hello")
        mock_post.assert_not_called()

    def test_transport_error_is_propagated(self, monkeypatch):
        monkeypatch.setenv(tts.MINIMAX_API_KEY_ENV, "unit-test-key")
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = RuntimeError("boom")
        with patch("httpx.post", MagicMock(return_value=mock_response)):
            with pytest.raises(RuntimeError, match="boom"):
                tts.synthesize_minimax("hello")


class TestBackendSelection:
    def test_default_backend_returns_none(self):
        assert tts.synthesize("hello") is None

    def test_unrelated_provider_value_returns_none(self, monkeypatch):
        monkeypatch.setenv(tts.TTS_PROVIDER_ENV, "something-else")
        assert tts.synthesize("hello") is None

    def test_provider_env_var_routes_to_the_t2a_backend(self, monkeypatch):
        monkeypatch.setenv(tts.TTS_PROVIDER_ENV, tts.PROVIDER_MINIMAX)
        monkeypatch.setenv(tts.MINIMAX_API_KEY_ENV, "unit-test-key")
        wav = _make_wav_bytes(0.25)
        mock_response = MagicMock()
        mock_response.json.return_value = _ok_response(wav)

        with patch("httpx.post", MagicMock(return_value=mock_response)):
            assert tts.synthesize("hello") == wav

    def test_provider_name_is_case_and_space_insensitive(self, monkeypatch):
        monkeypatch.setenv(tts.TTS_PROVIDER_ENV, "  MiniMax  ")
        assert tts.resolve_tts_provider() == tts.PROVIDER_MINIMAX


class TestCallSiteWiring:
    """The seam has to be reached from both existing TTS call sites."""

    def test_explainer_render_tts_uses_the_backend_audio(self, tmp_path):
        from jcodemunch_mcp.groq.config import GcmConfig
        from jcodemunch_mcp.groq.explainer import _render_tts

        wav = _make_wav_bytes(2.0, sample_rate=16000)
        out = tmp_path / "segment.wav"

        with patch("jcodemunch_mcp.groq.tts.synthesize", return_value=wav):
            duration = _render_tts(GcmConfig(groq_api_key="test-key"), "hello", str(out))

        assert out.read_bytes() == wav
        assert duration == pytest.approx(2.0, abs=0.01)

    def test_explainer_render_tts_falls_back_when_no_backend_configured(self, tmp_path):
        from jcodemunch_mcp.groq.config import GcmConfig
        from jcodemunch_mcp.groq.explainer import _render_tts

        wav = _make_wav_bytes(1.0)
        out = tmp_path / "segment.wav"

        mock_response = MagicMock()
        mock_response.content = wav
        mock_client = MagicMock()
        mock_client.audio.speech.create.return_value = mock_response
        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = mock_client

        with patch("jcodemunch_mcp.groq.tts.synthesize", return_value=None), \
             patch.dict("sys.modules", {"openai": mock_openai}):
            duration = _render_tts(GcmConfig(groq_api_key="test-key"), "hello", str(out))

        mock_client.audio.speech.create.assert_called_once()
        assert out.read_bytes() == wav
        assert duration == pytest.approx(1.0, abs=0.01)

    def test_speak_plays_backend_audio_without_the_default_client(self):
        from jcodemunch_mcp.groq.config import GcmConfig
        from jcodemunch_mcp.groq.voice import speak

        np = pytest.importorskip("numpy")
        wav = _make_wav_bytes(0.25)
        mock_sd = MagicMock()
        mock_get_client = MagicMock()

        with patch("jcodemunch_mcp.groq.tts.synthesize", return_value=wav), \
             patch("jcodemunch_mcp.groq.voice._get_client", mock_get_client), \
             patch.dict("sys.modules", {"sounddevice": mock_sd, "numpy": np}):
            speak(GcmConfig(groq_api_key="test-key"), "hello")

        # The default endpoint must not be contacted once a backend answers.
        mock_get_client.assert_not_called()
        mock_sd.play.assert_called_once()
