"""Voice-to-Codebase — speak a question, hear the answer.

Uses Groq's full audio stack:
  - Whisper large-v3-turbo for speech-to-text
  - LLM for code Q&A (via existing inference module)
  - Orpheus (playht) for text-to-speech

Requires: pip install "jcodemunch-mcp[groq-voice]"
"""

import io
import sys
import time
import wave
from typing import Optional

from .config import GcmConfig

# Audio recording parameters
SAMPLE_RATE = 16000  # Whisper expects 16kHz
CHANNELS = 1
DTYPE = "int16"

# Voice answer length cap — TTS is slow on long text
VOICE_MAX_WORDS = 100
VOICE_SYSTEM_PROMPT = (
    "You are a senior software engineer answering questions about a codebase. "
    "Use ONLY the provided code context to answer. "
    "Cite file paths and symbol names when relevant. "
    "If the context is insufficient, say so — do not guess. "
    "Keep your answer under 100 words — this will be spoken aloud."
)

# Groq audio endpoints (OpenAI-compatible)
WHISPER_MODEL = "whisper-large-v3-turbo"
TTS_MODEL = "playht-tts"
TTS_VOICE = "Fritz-PlayAI"


def _check_audio_deps() -> Optional[str]:
    """Return error message if audio deps are missing, else None."""
    missing = []
    try:
        import sounddevice  # noqa: F401
    except ImportError:
        missing.append("sounddevice")
    try:
        import numpy  # noqa: F401
    except ImportError:
        missing.append("numpy")
    if missing:
        return (
            f"Missing audio dependencies: {', '.join(missing)}\n"
            'Install with: pip install "jcodemunch-mcp[groq-voice]"'
        )
    return None


def _get_client(cfg: GcmConfig):
    """Get OpenAI client pointed at Groq."""
    from openai import OpenAI
    return OpenAI(api_key=cfg.groq_api_key, base_url=cfg.base_url)


def record_audio(verbose: bool = False) -> bytes:
    """Record audio via push-to-talk (press Enter to stop). Returns WAV bytes."""
    import numpy as np
    import sounddevice as sd

    print("  Press ENTER to stop recording...", file=sys.stderr)

    frames: list = []
    recording = True

    def callback(indata, frame_count, time_info, status):
        if status and verbose:
            print(f"  [audio] {status}", file=sys.stderr)
        if recording:
            frames.append(indata.copy())

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        callback=callback,
        blocksize=1024,
    )

    stream.start()
    try:
        input()  # Block until Enter
    except EOFError:
        pass
    recording = False
    stream.stop()
    stream.close()

    if not frames:
        return b""

    audio_data = np.concatenate(frames, axis=0)

    # Encode as WAV in memory
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # int16 = 2 bytes
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_data.tobytes())

    return buf.getvalue()


def transcribe(cfg: GcmConfig, audio_wav: bytes, verbose: bool = False) -> str:
    """Transcribe WAV audio to text via Groq Whisper."""
    client = _get_client(cfg)

    t0 = time.perf_counter()
    # Groq's transcription endpoint expects a file-like object
    audio_file = io.BytesIO(audio_wav)
    audio_file.name = "recording.wav"

    response = client.audio.transcriptions.create(
        model=WHISPER_MODEL,
        file=audio_file,
        response_format="text",
    )

    text = response.strip() if isinstance(response, str) else str(response).strip()

    if verbose:
        elapsed = time.perf_counter() - t0
        print(f"  [stt] \"{text}\" ({elapsed:.2f}s)", file=sys.stderr)

    return text


def speak(cfg: GcmConfig, text: str, verbose: bool = False) -> None:
    """Convert text to speech via Groq Orpheus TTS and play through speakers."""
    import numpy as np
    import sounddevice as sd

    from . import tts

    t0 = time.perf_counter()

    # A configured alternative backend returns the WAV bytes directly; None
    # means none is configured, so the default endpoint below is used.
    audio_bytes = tts.synthesize(text, audio_format="wav")
    if audio_bytes is None:
        client = _get_client(cfg)
        response = client.audio.speech.create(
            model=TTS_MODEL,
            voice=TTS_VOICE,
            input=text,
            response_format="wav",
        )
        audio_bytes = response.content if hasattr(response, "content") else response.read()

    if verbose:
        elapsed = time.perf_counter() - t0
        print(f"  [tts] audio generated ({elapsed:.2f}s)", file=sys.stderr)

    # Read WAV response and play
    buf = io.BytesIO(audio_bytes)

    with wave.open(buf, "rb") as wf:
        sr = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())

    # Convert to numpy array
    if sampwidth == 2:
        audio = np.frombuffer(raw, dtype=np.int16)
    elif sampwidth == 4:
        audio = np.frombuffer(raw, dtype=np.int32)
    else:
        audio = np.frombuffer(raw, dtype=np.uint8)

    if n_channels > 1:
        audio = audio.reshape(-1, n_channels)

    sd.play(audio, samplerate=sr)
    sd.wait()


def run_voice_loop(
    cfg: GcmConfig,
    repo_id: str,
    verbose: bool = False,
) -> None:
    """Main voice conversation loop — record → transcribe → retrieve → answer → speak."""
    from .retriever import retrieve_context
    from .inference import ask

    # Check dependencies first
    err = _check_audio_deps()
    if err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

    # Check microphone
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        default_in = sd.query_devices(kind="input")
        if verbose:
            print(f"  [mic] {default_in['name']}", file=sys.stderr)
    except Exception as e:
        print(f"Error: No microphone detected — {e}", file=sys.stderr)
        sys.exit(1)

    # Override system prompt for voice brevity
    voice_cfg = GcmConfig(
        groq_api_key=cfg.groq_api_key,
        model=cfg.model,
        base_url=cfg.base_url,
        token_budget=cfg.token_budget,
        max_answer_tokens=512,  # shorter for voice
        storage_path=cfg.storage_path,
        github_token=cfg.github_token,
        system_prompt=VOICE_SYSTEM_PROMPT,
    )

    print(f"Voice mode — talking to {repo_id}")
    print("Press ENTER to start recording, ENTER again to stop. Type 'exit' to quit.\n")

    history: list[dict] = []

    while True:
        try:
            prompt = input("[Press ENTER to speak, or type 'exit'] ")
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if prompt.strip().lower() in ("exit", "quit", "q"):
            print("Bye!")
            break

        # If they typed a text question instead of pressing Enter, use it directly
        if prompt.strip():
            question = prompt.strip()
            if verbose:
                print(f"  [text] \"{question}\"", file=sys.stderr)
        else:
            # Record audio
            print("  Recording...", file=sys.stderr)
            audio = record_audio(verbose=verbose)
            if not audio:
                print("  No audio captured.", file=sys.stderr)
                continue

            # Transcribe
            question = transcribe(cfg, audio, verbose=verbose)
            if not question:
                print("  Could not transcribe audio.", file=sys.stderr)
                continue

            print(f"  You: {question}")

        # Retrieve context
        t0 = time.perf_counter()
        context, raw = retrieve_context(repo_id, question, voice_cfg.token_budget, voice_cfg.storage_path)

        if "error" in raw:
            print(f"  Retrieval error: {raw['error']}", file=sys.stderr)
            continue

        if verbose:
            n_items = len(raw.get("context_items", []))
            t_ret = time.perf_counter() - t0
            print(f"  [retrieval] {n_items} symbols in {t_ret:.2f}s", file=sys.stderr)

        # Get answer from LLM
        t1 = time.perf_counter()
        answer = ask(voice_cfg, context, question, history)

        if verbose:
            t_llm = time.perf_counter() - t1
            print(f"  [llm] {t_llm:.2f}s", file=sys.stderr)

        print(f"  Answer: {answer}\n")

        # Speak the answer
        try:
            speak(cfg, answer, verbose=verbose)
        except Exception as e:
            if verbose:
                print(f"  [tts] playback failed: {e}", file=sys.stderr)
            # Still useful even if TTS fails — text answer was printed

        # Update history
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})

        if verbose:
            t_total = time.perf_counter() - t0
            print(f"  [total] {t_total:.2f}s", file=sys.stderr)

        print()
