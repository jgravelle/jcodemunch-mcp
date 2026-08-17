"""Text-to-speech backends for the voice loop and the narrated explainer.

Two backends are available:

  - The default backend, which keeps using the audio endpoint already
    configured for the ``gcm`` CLI through :class:`GcmConfig`.
  - The MiniMax T2A HTTP backend, selected with
    ``JCODEMUNCH_TTS_PROVIDER=minimax``.

Both backends hand the caller the raw bytes of a WAV container, because
``voice.speak`` reads the sample rate, channel count and sample width straight
out of the WAV header and ``explainer._render_tts`` derives slide timing from
the WAV frame count. The MiniMax backend therefore asks for
``audio_setting.format = "wav"`` and hex response encoding, then decodes
``data.audio`` back into those bytes.

Limitations: only the synchronous ``textToAudioHttp`` operation is wired up
here, and it is the only entry in ``MINIMAX_T2A_IMPLEMENTED_OPERATIONS``. The
async and WebSocket operations are recorded separately in
``MINIMAX_T2A_UNIMPLEMENTED_OPERATIONS``: this module never polls a task id and
never opens a socket, so a text longer than the endpoint's per-request ceiling
has to be split by the caller.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# --- Backend selection -----------------------------------------------------

TTS_PROVIDER_ENV = "JCODEMUNCH_TTS_PROVIDER"
PROVIDER_MINIMAX = "minimax"


# --- MiniMax T2A -----------------------------------------------------------

MINIMAX_API_KEY_ENV = "MINIMAX_API_KEY"
MINIMAX_REGION_ENV = "JCODEMUNCH_MINIMAX_T2A_REGION"
MINIMAX_MODEL_ENV = "JCODEMUNCH_MINIMAX_T2A_MODEL"
MINIMAX_VOICE_ENV = "JCODEMUNCH_MINIMAX_T2A_VOICE"

MINIMAX_REGION_GLOBAL = "global_en"
MINIMAX_REGION_CN = "cn_zh"
MINIMAX_DEFAULT_REGION = MINIMAX_REGION_GLOBAL

# The regions are separate deployments and a key issued for one is not valid on
# the other, so the region is chosen explicitly rather than derived from the key.
MINIMAX_T2A_ENDPOINTS: Dict[str, str] = {
    MINIMAX_REGION_GLOBAL: "https://api.minimax.io/v1/t2a_v2",
    MINIMAX_REGION_CN: "https://api.minimaxi.com/v1/t2a_v2",
}

MINIMAX_T2A_MODELS: Tuple[str, ...] = (
    "speech-2.8-hd",
    "speech-2.8-turbo",
    "speech-2.6-hd",
    "speech-2.6-turbo",
    "speech-02-hd",
    "speech-02-turbo",
    "speech-01-hd",
    "speech-01-turbo",
)
MINIMAX_DEFAULT_T2A_MODEL = "speech-2.8-hd"

# Container formats the endpoint can encode the audio into.
MINIMAX_T2A_AUDIO_FORMATS: Tuple[str, ...] = ("mp3", "wav", "flac", "pcm")

# operation_id -> (method, path)
#
# The status is in the constant names rather than in a docstring caveat: a
# caller reading the table at the point of use must not mistake a declared
# operation for an implemented one.
MINIMAX_T2A_IMPLEMENTED_OPERATIONS: Dict[str, Tuple[str, str]] = {
    "textToAudioHttp": ("POST", "/v1/t2a_v2"),
}

# Documented by the endpoint but deliberately not wired up here: this module
# never polls a task id and never opens a socket. Recorded so the surface is
# discoverable, and kept out of the table above so nothing can dispatch to it.
MINIMAX_T2A_UNIMPLEMENTED_OPERATIONS: Dict[str, Tuple[str, str]] = {
    "textToAudioAsyncCreate": ("POST", "/v1/t2a_async_v2"),
    "textToAudioAsyncQuery": ("POST", "/v1/query/t2a_async_query_v2"),
    "textToAudioWebSocket": ("WSS", "/ws/v1/t2a_v2"),
}

MINIMAX_T2A_REQUIRED_FIELDS: Tuple[str, ...] = ("model", "text")
MINIMAX_T2A_REQUEST_FIELDS: Tuple[str, ...] = (
    "model",
    "text",
    "stream",
    "language_boost",
    "output_format",
    "voice_setting",
    "pronunciation_dict",
    "audio_setting",
    "voice_modify",
    "subtitle_enable",
)

# `output_format` selects how the response carries the audio, NOT the container
# format: `hex` inlines the bytes in `data.audio`, `url` returns a link that
# expires. The container format lives in `audio_setting.format`, and conflating
# the two is why this pair is named rather than passed as a literal.
MINIMAX_OUTPUT_FORMAT_HEX = "hex"
MINIMAX_OUTPUT_FORMAT_URL = "url"
MINIMAX_OUTPUT_FORMATS: Tuple[str, ...] = (
    MINIMAX_OUTPUT_FORMAT_HEX,
    MINIMAX_OUTPUT_FORMAT_URL,
)

# `data.status`: 1 while still synthesizing, 2 once the audio is complete.
MINIMAX_T2A_STATUS_SYNTHESIZING = 1
MINIMAX_T2A_STATUS_DONE = 2

# `base_resp.status_code` is 0 on success; anything else describes a failure.
MINIMAX_BASE_RESP_OK = 0

MINIMAX_T2A_TIMEOUT = 60.0


def resolve_tts_provider() -> str:
    """Return the configured TTS backend name, or "" for the default one."""
    return os.environ.get(TTS_PROVIDER_ENV, "").strip().lower()


def minimax_t2a_url(region: Optional[str] = None) -> str:
    """Return the T2A endpoint for a region.

    Raises ValueError on an unknown region rather than falling back, so a typo
    surfaces as an error instead of silently sending a key to the wrong region.
    """
    name = (region or os.environ.get(MINIMAX_REGION_ENV) or MINIMAX_DEFAULT_REGION)
    name = name.strip().lower()
    try:
        return MINIMAX_T2A_ENDPOINTS[name]
    except KeyError:
        raise ValueError(
            f"Unknown T2A region {name!r}. "
            f"Valid regions: {', '.join(sorted(MINIMAX_T2A_ENDPOINTS))}"
        ) from None


def build_minimax_t2a_payload(
    text: str,
    *,
    model: Optional[str] = None,
    audio_format: str = "wav",
    voice_id: Optional[str] = None,
    output_format: str = MINIMAX_OUTPUT_FORMAT_HEX,
    voice_setting: Optional[Dict[str, Any]] = None,
    audio_setting: Optional[Dict[str, Any]] = None,
    language_boost: Optional[str] = None,
    pronunciation_dict: Optional[Dict[str, Any]] = None,
    voice_modify: Optional[Dict[str, Any]] = None,
    stream: Optional[bool] = None,
    subtitle_enable: Optional[bool] = None,
) -> Dict[str, Any]:
    """Build a T2A request body.

    `model` and `text` are the two required fields. Every optional field is
    omitted entirely when not supplied, so the endpoint's own defaults apply
    rather than a copy of them drifting in this module.
    """
    if not text or not text.strip():
        raise ValueError("T2A request text must not be empty")

    chosen_model = (model or os.environ.get(MINIMAX_MODEL_ENV) or MINIMAX_DEFAULT_T2A_MODEL)
    if chosen_model not in MINIMAX_T2A_MODELS:
        raise ValueError(
            f"Unknown T2A model {chosen_model!r}. "
            f"Valid models: {', '.join(MINIMAX_T2A_MODELS)}"
        )
    if audio_format not in MINIMAX_T2A_AUDIO_FORMATS:
        raise ValueError(
            f"Unsupported T2A audio format {audio_format!r}. "
            f"Valid formats: {', '.join(MINIMAX_T2A_AUDIO_FORMATS)}"
        )
    if output_format not in MINIMAX_OUTPUT_FORMATS:
        raise ValueError(
            f"Unsupported T2A output format {output_format!r}. "
            f"Valid values: {', '.join(MINIMAX_OUTPUT_FORMATS)}"
        )

    payload: Dict[str, Any] = {
        "model": chosen_model,
        "text": text,
        "output_format": output_format,
    }

    settings = dict(audio_setting or {})
    settings["format"] = audio_format
    payload["audio_setting"] = settings

    voice = dict(voice_setting or {})
    resolved_voice_id = voice_id or os.environ.get(MINIMAX_VOICE_ENV) or voice.get("voice_id")
    if resolved_voice_id:
        voice["voice_id"] = resolved_voice_id
    if voice:
        # `voice_id` is required whenever a voice setting is sent at all.
        if not voice.get("voice_id"):
            raise ValueError("voice_setting requires a voice_id")
        payload["voice_setting"] = voice

    if language_boost is not None:
        payload["language_boost"] = language_boost
    if pronunciation_dict is not None:
        payload["pronunciation_dict"] = pronunciation_dict
    if voice_modify is not None:
        payload["voice_modify"] = voice_modify
    if stream is not None:
        payload["stream"] = stream
    if subtitle_enable is not None:
        payload["subtitle_enable"] = subtitle_enable

    # Drift guard, not validation of caller input: every key above is set by
    # this function from a fixed set, so this cannot fire today. It exists to
    # catch a later edit that adds a field the endpoint does not accept.
    unknown = set(payload) - set(MINIMAX_T2A_REQUEST_FIELDS)
    if unknown:
        raise ValueError(f"Unknown T2A request fields: {', '.join(sorted(unknown))}")
    return payload


def parse_minimax_t2a_response(payload: Dict[str, Any]) -> bytes:
    """Decode the audio bytes out of a T2A response body.

    The transport can return HTTP 200 with a failure in `base_resp`, so the
    envelope is checked before the audio is read.
    """
    base_resp = payload.get("base_resp")
    if not isinstance(base_resp, dict) or "status_code" not in base_resp:
        # An unreadable envelope must not resolve to the confident answer: with
        # no `status_code` the outcome cannot be established, so it is a failure
        # rather than a default of OK.
        raise RuntimeError(
            "T2A response carried no base_resp.status_code, "
            "so the request outcome could not be established"
        )
    status_code = base_resp["status_code"]
    if status_code != MINIMAX_BASE_RESP_OK:
        status_msg = base_resp.get("status_msg") or "no status message"
        raise RuntimeError(f"T2A request failed (status_code={status_code}): {status_msg}")

    # `data` is nullable even on an otherwise successful envelope.
    data = payload.get("data") or {}
    status = data.get("status")
    if status is not None and status != MINIMAX_T2A_STATUS_DONE:
        raise RuntimeError(f"T2A synthesis is not complete (data.status={status})")

    audio_hex = data.get("audio")
    if not audio_hex:
        raise RuntimeError(
            "T2A response carried no audio; "
            f"request {MINIMAX_OUTPUT_FORMAT_HEX!r} output to inline it in data.audio"
        )
    try:
        return bytes.fromhex(audio_hex)
    except ValueError as exc:
        raise RuntimeError("T2A returned audio that is not hex-encoded") from exc


def synthesize_minimax(
    text: str,
    *,
    api_key: Optional[str] = None,
    region: Optional[str] = None,
    model: Optional[str] = None,
    audio_format: str = "wav",
    voice_id: Optional[str] = None,
    timeout: float = MINIMAX_T2A_TIMEOUT,
    **payload_kwargs: Any,
) -> bytes:
    """Synthesize `text` over the T2A HTTP operation and return audio bytes."""
    import httpx

    key = api_key or os.environ.get(MINIMAX_API_KEY_ENV, "")
    if not key:
        raise RuntimeError(
            f"{MINIMAX_API_KEY_ENV} not set. Export it to use the "
            f"{PROVIDER_MINIMAX} TTS backend."
        )

    url = minimax_t2a_url(region)
    payload = build_minimax_t2a_payload(
        text,
        model=model,
        audio_format=audio_format,
        voice_id=voice_id,
        **payload_kwargs,
    )
    response = httpx.post(
        url,
        json=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_minimax_t2a_response(response.json())


def synthesize(
    text: str,
    *,
    audio_format: str = "wav",
    provider: Optional[str] = None,
) -> Optional[bytes]:
    """Synthesize `text` with the configured backend.

    Returns None when no alternative backend is configured, which is the signal
    for the caller to keep using its existing default path.
    """
    name = provider if provider is not None else resolve_tts_provider()
    if name != PROVIDER_MINIMAX:
        return None
    logger.debug("routing TTS through the %s backend", name)
    return synthesize_minimax(text, audio_format=audio_format)
