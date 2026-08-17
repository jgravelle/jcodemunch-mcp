"""Auto Repo Explainer — generate a narrated explainer video for any codebase.

Pipeline:
  1. Retrieve repo structure + key symbols via jCodeMunch
  2. Generate narration script via Groq LLM
  3. Render narration audio via Groq Orpheus TTS
  4. Generate static slides (file tree + code snippets) via Pillow
  5. Composite slides + audio into MP4 via FFmpeg

MVP: static slides (no animation). Requires FFmpeg on PATH.
Requires: pip install "jcodemunch-mcp[groq-explain]"
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass
from typing import Optional

from .config import GcmConfig
from .voice import TTS_MODEL, TTS_VOICE


# Slide layout constants
SLIDE_WIDTH = 1920
SLIDE_HEIGHT = 1080
BG_COLOR = (13, 17, 23)       # GitHub dark background
TEXT_COLOR = (230, 237, 243)   # Light text
ACCENT_COLOR = (88, 166, 255)  # Blue accent
CODE_COLOR = (201, 209, 217)   # Code text
HEADER_COLOR = (255, 123, 114) # Red-orange for headers
MARGIN = 80
CODE_FONT_SIZE = 20
TITLE_FONT_SIZE = 48
BODY_FONT_SIZE = 28
FOOTER_FONT_SIZE = 16


@dataclass
class Slide:
    """A single slide in the explainer video."""
    title: str
    content: str  # Plain text or code
    is_code: bool = False
    duration: float = 0.0  # Seconds, set from narration timing


@dataclass
class NarrationSegment:
    """A segment of the narration script tied to a slide."""
    slide_title: str
    text: str
    slide_content: str
    is_code: bool = False


NARRATION_PROMPT = """\
You are creating a 60-second narration script for a codebase explainer video.
You will be given: repo summary, file tree, and key symbols.

Write a narration script as a JSON array of segments. Each segment has:
- "slide_title": short title shown on slide (max 50 chars)
- "text": what the narrator says (conversational, clear)
- "slide_content": what's shown on the slide (file tree excerpt or code snippet)
- "is_code": true if slide_content is code, false for text/tree

Rules:
- Total narration should be ~150 words (60 seconds at speaking pace)
- 4-6 segments total
- First segment: repo name, purpose, languages
- Middle segments: key architecture, main entry points, interesting patterns
- Last segment: wrap-up with where to start exploring
- Keep slide_content concise (max 15 lines)
- Do NOT use markdown in slide_content — plain text only

Return ONLY the JSON array, no other text.
"""


def _check_deps() -> Optional[str]:
    """Check for required dependencies. Returns error message or None."""
    missing = []
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
    except ImportError:
        missing.append("Pillow")

    if not shutil.which("ffmpeg"):
        missing.append("ffmpeg (system binary, not pip)")

    if missing:
        parts = []
        pip_pkgs = [m for m in missing if m != "ffmpeg (system binary, not pip)"]
        if pip_pkgs:
            parts.append('pip install "jcodemunch-mcp[groq-explain]"')
        if "ffmpeg (system binary, not pip)" in missing:
            parts.append("Install FFmpeg: https://ffmpeg.org/download.html")
        return f"Missing dependencies: {', '.join(missing)}\n" + "\n".join(parts)
    return None


def _gather_repo_info(repo_id: str, storage_path: Optional[str] = None) -> dict:
    """Gather repo summary, file tree, and key symbols via jCodeMunch."""
    from ..tools.get_file_tree import get_file_tree
    from ..tools.get_repo_outline import get_repo_outline
    from ..tools.search_symbols import search_symbols

    info: dict = {"repo": repo_id}

    # Repo outline (summary + structure)
    try:
        outline = get_repo_outline(repo=repo_id, storage_path=storage_path)
        info["outline"] = outline
    except Exception as e:
        info["outline"] = {"error": str(e)}

    # File tree
    try:
        tree = get_file_tree(repo=repo_id, storage_path=storage_path)
        info["file_tree"] = tree.get("tree", "")
    except Exception as e:
        info["file_tree"] = f"Error: {e}"

    # Top symbols by centrality (most important entry points)
    try:
        symbols = search_symbols(
            repo=repo_id,
            query="main entry point",
            max_results=10,
            storage_path=storage_path,
        )
        info["key_symbols"] = symbols.get("symbols", [])
    except Exception:
        info["key_symbols"] = []

    return info


def _generate_narration_script(cfg: GcmConfig, repo_info: dict, verbose: bool = False) -> list[NarrationSegment]:
    """Use Groq LLM to generate a structured narration script."""
    from .inference import ask

    # Build context from repo info
    parts = [f"Repository: {repo_info['repo']}"]

    outline = repo_info.get("outline", {})
    if isinstance(outline, dict) and "error" not in outline:
        if outline.get("summary"):
            parts.append(f"\nSummary:\n{outline['summary']}")
        if outline.get("languages"):
            langs = ", ".join(f"{l['language']} ({l['files']} files)" for l in outline["languages"][:5])
            parts.append(f"\nLanguages: {langs}")
        if outline.get("total_files"):
            parts.append(f"Total files: {outline['total_files']}")
        if outline.get("total_symbols"):
            parts.append(f"Total symbols: {outline['total_symbols']}")

    tree = repo_info.get("file_tree", "")
    if tree:
        # Truncate tree to first 30 lines
        tree_lines = tree.split("\n")[:30]
        parts.append("\nFile Tree:\n" + "\n".join(tree_lines))

    symbols = repo_info.get("key_symbols", [])
    if symbols:
        sym_lines = []
        for s in symbols[:10]:
            sym_lines.append(f"  {s.get('kind', '?')} {s.get('id', '?')} in {s.get('file', '?')}")
        parts.append("\nKey Symbols:\n" + "\n".join(sym_lines))

    context = "\n".join(parts)

    # Override config for narration generation
    narration_cfg = GcmConfig(
        groq_api_key=cfg.groq_api_key,
        model=cfg.model,
        base_url=cfg.base_url,
        token_budget=cfg.token_budget,
        max_answer_tokens=2048,
        system_prompt=NARRATION_PROMPT,
    )

    t0 = time.perf_counter()
    raw = ask(narration_cfg, context, "Generate the narration script as JSON.")

    if verbose:
        elapsed = time.perf_counter() - t0
        print(f"  [narration] script generated ({elapsed:.2f}s)", file=sys.stderr)

    # Parse JSON from response
    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        segments_raw = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON array in the response
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            segments_raw = json.loads(text[start:end])
        else:
            raise ValueError(f"Could not parse narration script from LLM response:\n{raw[:200]}")

    segments = []
    for seg in segments_raw:
        segments.append(NarrationSegment(
            slide_title=seg.get("slide_title", ""),
            text=seg.get("text", ""),
            slide_content=seg.get("slide_content", ""),
            is_code=seg.get("is_code", False),
        ))

    return segments


def _render_tts(cfg: GcmConfig, text: str, output_path: str, verbose: bool = False) -> float:
    """Render TTS for a text segment. Returns duration in seconds."""
    from . import tts

    t0 = time.perf_counter()

    # A configured alternative backend returns the WAV bytes directly; None
    # means none is configured, so the default endpoint below is used.
    audio_bytes = tts.synthesize(text, audio_format="wav")
    if audio_bytes is None:
        from openai import OpenAI

        client = OpenAI(api_key=cfg.groq_api_key, base_url=cfg.base_url)

        response = client.audio.speech.create(
            model=TTS_MODEL,
            voice=TTS_VOICE,
            input=text,
            response_format="wav",
        )

        audio_bytes = response.content if hasattr(response, "content") else response.read()

    with open(output_path, "wb") as f:
        f.write(audio_bytes)

    # Get duration from WAV header
    with wave.open(output_path, "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        duration = frames / rate

    if verbose:
        elapsed = time.perf_counter() - t0
        print(f"  [tts] segment rendered ({duration:.1f}s audio, {elapsed:.2f}s wall)", file=sys.stderr)

    return duration


def _render_slide(slide: Slide, slide_num: int, total_slides: int, repo_name: str, output_path: str) -> None:
    """Render a single slide as a PNG image using Pillow."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (SLIDE_WIDTH, SLIDE_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Use default font (monospace-like) — Pillow's built-in
    try:
        title_font = ImageFont.truetype("arial.ttf", TITLE_FONT_SIZE)
        body_font = ImageFont.truetype("consola.ttf" if slide.is_code else "arial.ttf", CODE_FONT_SIZE if slide.is_code else BODY_FONT_SIZE)
        footer_font = ImageFont.truetype("arial.ttf", FOOTER_FONT_SIZE)
    except (OSError, IOError):
        # Fallback to default
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
        footer_font = ImageFont.load_default()

    y = MARGIN

    # Title
    draw.text((MARGIN, y), slide.title, fill=ACCENT_COLOR, font=title_font)
    y += TITLE_FONT_SIZE + 30

    # Divider line
    draw.line([(MARGIN, y), (SLIDE_WIDTH - MARGIN, y)], fill=(48, 54, 61), width=2)
    y += 20

    # Content area
    if slide.is_code:
        # Draw code background
        code_bg = (22, 27, 34)
        draw.rectangle(
            [(MARGIN - 10, y - 5), (SLIDE_WIDTH - MARGIN + 10, SLIDE_HEIGHT - MARGIN - 40)],
            fill=code_bg,
        )

    # Wrap and draw content lines
    content_lines = slide.content.split("\n")
    line_height = (CODE_FONT_SIZE if slide.is_code else BODY_FONT_SIZE) + 6
    color = CODE_COLOR if slide.is_code else TEXT_COLOR

    for line in content_lines:
        if y > SLIDE_HEIGHT - MARGIN - 40:
            break
        draw.text((MARGIN + 10, y), line, fill=color, font=body_font)
        y += line_height

    # Footer
    footer_y = SLIDE_HEIGHT - MARGIN
    footer = f"Powered by jCodeMunch + Groq  |  {repo_name}  |  {slide_num}/{total_slides}"
    draw.text((MARGIN, footer_y), footer, fill=(110, 118, 129), font=footer_font)

    img.save(output_path)


def _composite_video(slides_dir: str, audio_dir: str, segments: list[NarrationSegment], durations: list[float], output_path: str, verbose: bool = False) -> None:
    """Composite slide images + audio segments into final MP4 via FFmpeg."""
    # Build FFmpeg concat file
    concat_entries = []
    filter_parts = []
    input_args = []

    for i, (seg, dur) in enumerate(zip(segments, durations)):
        slide_path = os.path.join(slides_dir, f"slide_{i:02d}.png")
        audio_path = os.path.join(audio_dir, f"audio_{i:02d}.wav")

        # Input: image looped for duration, then audio
        input_args.extend(["-loop", "1", "-t", f"{dur:.2f}", "-i", slide_path])
        input_args.extend(["-i", audio_path])

    # Build filter complex to concat all segments
    n = len(segments)
    filter_parts = []
    for i in range(n):
        v_idx = i * 2
        a_idx = i * 2 + 1
        filter_parts.append(f"[{v_idx}:v]scale={SLIDE_WIDTH}:{SLIDE_HEIGHT},setsar=1[v{i}]")
        filter_parts.append(f"[{a_idx}:a]aformat=sample_rates=44100:channel_layouts=mono[a{i}]")

    v_concat = "".join(f"[v{i}]" for i in range(n))
    a_concat = "".join(f"[a{i}]" for i in range(n))
    filter_parts.append(f"{v_concat}concat=n={n}:v=1:a=0[outv]")
    filter_parts.append(f"{a_concat}concat=n={n}:v=0:a=1[outa]")

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        *input_args,
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output_path,
    ]

    if verbose:
        print(f"  [ffmpeg] compositing {n} segments...", file=sys.stderr)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{result.stderr[-500:]}")

    if verbose:
        print(f"  [ffmpeg] done: {output_path}", file=sys.stderr)


def generate_explainer(
    cfg: GcmConfig,
    repo_id: str,
    output_path: str = "explainer.mp4",
    verbose: bool = False,
) -> str:
    """Generate a narrated explainer video for a codebase.

    Returns the path to the output MP4 file.
    """
    # Check dependencies
    err = _check_deps()
    if err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

    print(f"Generating explainer for {repo_id}...")

    # Step 1: Gather repo information
    if verbose:
        print("  [1/5] Gathering repo info...", file=sys.stderr)
    t0 = time.perf_counter()
    repo_info = _gather_repo_info(repo_id, cfg.storage_path)

    if verbose:
        elapsed = time.perf_counter() - t0
        print(f"  [1/5] done ({elapsed:.2f}s)", file=sys.stderr)

    # Step 2: Generate narration script
    if verbose:
        print("  [2/5] Generating narration script...", file=sys.stderr)
    segments = _generate_narration_script(cfg, repo_info, verbose=verbose)
    print(f"  Script: {len(segments)} segments, ~{sum(len(s.text.split()) for s in segments)} words")

    # Step 3: Render TTS for each segment
    if verbose:
        print("  [3/5] Rendering narration audio...", file=sys.stderr)

    tmpdir = tempfile.mkdtemp(prefix="gcm_explain_")
    audio_dir = os.path.join(tmpdir, "audio")
    slides_dir = os.path.join(tmpdir, "slides")
    os.makedirs(audio_dir)
    os.makedirs(slides_dir)

    durations = []
    for i, seg in enumerate(segments):
        audio_path = os.path.join(audio_dir, f"audio_{i:02d}.wav")
        dur = _render_tts(cfg, seg.text, audio_path, verbose=verbose)
        durations.append(dur)

    total_duration = sum(durations)
    print(f"  Audio: {total_duration:.1f}s total")

    # Step 4: Render slides
    if verbose:
        print("  [4/5] Rendering slides...", file=sys.stderr)

    slides = []
    for i, (seg, dur) in enumerate(zip(segments, durations)):
        slide = Slide(
            title=seg.slide_title,
            content=seg.slide_content,
            is_code=seg.is_code,
            duration=dur,
        )
        slides.append(slide)

        slide_path = os.path.join(slides_dir, f"slide_{i:02d}.png")
        _render_slide(slide, i + 1, len(segments), repo_id, slide_path)

    # Step 5: Composite into video
    if verbose:
        print("  [5/5] Compositing video...", file=sys.stderr)

    output_abs = os.path.abspath(output_path)
    _composite_video(slides_dir, audio_dir, segments, durations, output_abs, verbose=verbose)

    # Cleanup temp dir
    try:
        shutil.rmtree(tmpdir)
    except Exception:
        pass

    total_time = time.perf_counter() - t0
    print(f"  Done! {output_abs} ({total_duration:.0f}s video, generated in {total_time:.1f}s)")

    return output_abs
