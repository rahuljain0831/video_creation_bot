"""
Phase 4 — MoviePy 9:16 assembler with Pillow text overlay.

Flow:
  1. Receive scene_clips (list) or single bg_clip + audio path
  2. Stitch scene clips with crossfade (or loop single clip)
  3. Pad audio with silence to enforce minimum duration
  4. Render semi-transparent quote text via Pillow
  5. Composite: bg + text overlay + audio → write mp4
"""
import logging
from pathlib import Path

import numpy as np
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont

# MoviePy 1.0.3 / Pillow 10+ compat patch
if not hasattr(PIL.Image, "ANTIALIAS"):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

from moviepy.audio.AudioClip import AudioArrayClip
from moviepy.editor import (
    AudioFileClip,
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
    concatenate_audioclips,
    concatenate_videoclips,
)

log = logging.getLogger(__name__)

TARGET_W, TARGET_H = 1080, 1920
FPS = 30
INTRO_SILENCE = 1.5   # seconds of silence before voice starts
AUDIO_FPS     = 44100

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "C:/Windows/Fonts/verdanab.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
]


# ── Text rendering ─────────────────────────────────────────────────────────────

def _find_font(size: int) -> PIL.ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return PIL.ImageFont.truetype(path, size)
    return PIL.ImageFont.load_default()


def _auto_font_size(char_count: int) -> int:
    # Scale down for longer quotes; slightly smaller overall (supporting role)
    if char_count <= 60:
        return 62
    if char_count <= 100:
        return 52
    if char_count <= 150:
        return 43
    return 36


def _wrap_text(text: str, font: PIL.ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines, current = [], []
    dummy = PIL.Image.new("RGBA", (1, 1))
    draw  = PIL.ImageDraw.Draw(dummy)
    for word in words:
        test = " ".join(current + [word])
        if draw.textbbox((0, 0), test, font=font)[2] > max_width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def render_text_overlay(
    text: str,
    width: int = TARGET_W,
    height: int = TARGET_H,
) -> np.ndarray:
    """
    Semi-transparent white quote text on transparent RGBA canvas.
    Text is subtle — supporting the visual, not dominating it.
    """
    font_size = _auto_font_size(len(text))
    font      = _find_font(font_size)
    max_w     = int(width * 0.80)

    lines       = _wrap_text(text, font, max_w)
    line_height = int(font_size * 1.45)
    total_h     = len(lines) * line_height

    img  = PIL.Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = PIL.ImageDraw.Draw(img)

    # Center text in lower third (60%–90% of frame height)
    zone_top    = int(height * 0.60)
    zone_bottom = int(height * 0.90)
    zone_h      = zone_bottom - zone_top
    y_start     = zone_top + (zone_h - total_h) // 2
    shadow_off  = max(2, font_size // 28)

    for i, line in enumerate(lines):
        bbox   = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        x = (width - line_w) // 2
        y = y_start + i * line_height

        # Subtle shadow
        draw.text((x + shadow_off, y + shadow_off), line, font=font, fill=(0, 0, 0, 120))
        # Semi-transparent main text (160/255 ≈ 63% opacity)
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 160))

    return np.array(img)


# ── Audio padding ──────────────────────────────────────────────────────────────

def _pad_audio(audio: AudioFileClip, min_duration: float) -> tuple[AudioFileClip, float]:
    """
    Wrap voice audio with intro silence and pad end to reach min_duration.
    Returns (padded_audio_clip, total_duration).
    """
    voice_dur   = audio.duration
    outro_dur   = max(min_duration - INTRO_SILENCE - voice_dur, 1.0)
    total       = INTRO_SILENCE + voice_dur + outro_dur

    intro_arr   = np.zeros((int(AUDIO_FPS * INTRO_SILENCE), 2), dtype=np.float32)
    outro_arr   = np.zeros((int(AUDIO_FPS * outro_dur),      2), dtype=np.float32)

    padded = concatenate_audioclips([
        AudioArrayClip(intro_arr, fps=AUDIO_FPS),
        audio,
        AudioArrayClip(outro_arr, fps=AUDIO_FPS),
    ])
    return padded, total


# ── Assembly ───────────────────────────────────────────────────────────────────

def assemble(
    video_id,
    quote_text: str,
    bg_clip: VideoFileClip,
    audio_path: str,
    output_dir: str,
    min_duration: float = 15.0,
    scene_clips: list | None = None,
) -> str:
    """
    Composite background + text + audio into final 9:16 mp4.
    If scene_clips provided, stitches them with crossfade instead of using bg_clip.
    Returns output file path.
    """
    out_dir  = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(out_dir / f"video_{video_id}.mp4")

    # ── Build background ──────────────────────────────────────────────────────
    if scene_clips and len(scene_clips) > 1:
        fade = 0.4
        faded = [scene_clips[0]] + [c.crossfadein(fade) for c in scene_clips[1:]]
        bg = concatenate_videoclips(faded, padding=-fade, method="compose")
        log.info("Stitched %d scene clips (crossfade=%.1fs)", len(scene_clips), fade)
    else:
        bg = bg_clip

    # ── Load + pad audio ─────────────────────────────────────────────────────
    audio_raw        = AudioFileClip(audio_path)
    audio_pad, total = _pad_audio(audio_raw, min_duration)
    duration         = total
    log.info(
        "video_id=%s  voice=%.2fs  padded=%.2fs  quote=%s",
        video_id, audio_raw.duration, duration, quote_text[:60],
    )

    # ── Trim/loop background to padded duration ───────────────────────────────
    if bg.duration < duration:
        loops = int(duration / bg.duration) + 1
        bg    = concatenate_videoclips([bg] * loops)
    bg = bg.subclip(0, duration)

    # ── Text overlay ──────────────────────────────────────────────────────────
    overlay_arr = render_text_overlay(quote_text, TARGET_W, TARGET_H)
    text_clip   = ImageClip(overlay_arr, ismask=False).set_duration(duration)

    # ── Composite + write ─────────────────────────────────────────────────────
    final = CompositeVideoClip(
        [bg, text_clip],
        size=(TARGET_W, TARGET_H),
    ).set_audio(audio_pad)

    log.info("Writing %s", out_path)
    final.write_videofile(
        out_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="fast",
        ffmpeg_params=["-crf", "28", "-b:a", "96k"],  # ~8-15MB for 15s
        threads=4,
        logger=None,
    )

    audio_raw.close()
    bg.close()

    log.info("Done: %s", out_path)
    return out_path


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    from pipeline.background import ken_burns_fallback

    quote   = sys.argv[1] if len(sys.argv) > 1 else "Every step forward is a victory worth celebrating."
    audio   = sys.argv[2] if len(sys.argv) > 2 else "data/audio/voice_0.mp3"
    out_dir = sys.argv[3] if len(sys.argv) > 3 else "data/output"

    bg = ken_burns_fallback(30.0)
    result = assemble("test", quote, bg, audio, out_dir, min_duration=15.0)
    print(f"Result: {result}")
