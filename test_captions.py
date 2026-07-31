"""
test_captions.py — generate 3 test videos comparing caption font sizes 8, 10, 12.

fontsize here is a "480p reference" value:
  actual pixels on screen = fontsize * (video_height / 480)
  fontsize 8  → 32px at 1920p
  fontsize 10 → 40px at 1920p
  fontsize 12 → 48px at 1920p

Usage:
  python test_captions.py
Outputs to output/test_captions/:
  test_s8.mp4   — size 8
  test_s10.mp4  — size 10
  test_s12.mp4  — size 12
"""

import asyncio
import json
import logging
import subprocess
import tempfile
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

TEST_TEXT = (
    "In the beginning, when darkness covered the face of the deep, "
    "a great light appeared across the heavens. "
    "The gods watched from above as creation unfolded before them, "
    "each moment more magnificent than the last. "
    "And so the story of the world began."
)
VOICE = "en-US-GuyNeural"
SIZES = [14, 16]
WIDTH, HEIGHT = 1080, 1920
FPS = 30
OUT_DIR = Path("output/test_captions")


async def _edge_tts_async(text: str, voice: str, out_path: str) -> list[dict]:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, boundary="WordBoundary")
    word_boundaries: list[dict] = []
    audio_chunks: list[bytes] = []
    async for event in communicate.stream():
        if event["type"] == "audio":
            audio_chunks.append(event["data"])
        elif event["type"] == "WordBoundary":
            word_boundaries.append({
                "text": event["text"],
                "offset": event["offset"],
                "duration": event["duration"],
            })
    with open(out_path, "wb") as f:
        for chunk in audio_chunks:
            f.write(chunk)
    return word_boundaries


def _ffmpeg(*args):
    cmd = ["ffmpeg", "-y", "-loglevel", "error"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[:400]}")
    return result


def _get_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _seconds_to_ass_time(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h}:{m:02d}:{sec:05.2f}"


def _sanitize_ass(text: str) -> str:
    _MAP = {
        "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"', "\u2026": "...",
    }
    for char, replacement in _MAP.items():
        text = text.replace(char, replacement)
    text = text.replace("{", "").replace("}", "")
    return text.replace("\n", " ").replace("\r", "").strip()


def build_ass(word_timings: list[dict], fontsize_ref: int, chunk_size: int = 4) -> str:
    """Build ASS with fontsize scaled from 480p reference to actual HEIGHT."""
    fontsize = max(int(fontsize_ref * HEIGHT / 480), 6)
    margin_v = int(HEIGHT * 5 / 100)  # 5% from bottom

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {WIDTH}\n"
        f"PlayResY: {HEIGHT}\n"
        "WrapStyle: 1\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,{fontsize},"
        f"&H00FFFFFF,&H00FFFF00,&H00000000,&HA0000000,"
        f"0,0,0,0,100,100,0,0,1,2,1,2,20,20,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    chunks: list[list[dict]] = [
        word_timings[i:i + chunk_size]
        for i in range(0, len(word_timings), chunk_size)
    ]

    events: list[str] = []
    for chunk_idx, chunk in enumerate(chunks):
        words = [_sanitize_ass(w["text"]) for w in chunk]
        for word_idx, word_data in enumerate(chunk):
            start_s = word_data["offset"] / 10_000_000
            dur_s   = word_data["duration"] / 10_000_000
            flat_idx = chunk_idx * chunk_size + word_idx
            if flat_idx + 1 < len(word_timings):
                next_start = word_timings[flat_idx + 1]["offset"] / 10_000_000
                end_s = max(start_s + max(dur_s, 0.05), next_start)
            else:
                end_s = start_s + max(dur_s, 0.3)

            parts = []
            for i, word in enumerate(words):
                if i == word_idx:
                    parts.append(r"{\c&H00FFFF&\b1}" + word + r"{\b0\c&HFFFFFF&}")
                else:
                    parts.append(word)

            text      = " ".join(parts)
            start_str = _seconds_to_ass_time(start_s)
            end_str   = _seconds_to_ass_time(end_s)
            events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text}")

    return header + "\n".join(events) + "\n"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Generate TTS
    audio_path = str(OUT_DIR / "test_voice.mp3")
    timings_path = str(OUT_DIR / "word_timings.json")
    log.info("Generating TTS with Edge TTS...")
    word_boundaries = asyncio.run(_edge_tts_async(TEST_TEXT, VOICE, audio_path))
    with open(timings_path, "w", encoding="utf-8") as f:
        json.dump(word_boundaries, f)
    log.info("Word timings: %d words", len(word_boundaries))

    # Step 2: Get audio duration and build black background video
    audio_dur = _get_duration(audio_path)
    log.info("Audio duration: %.2fs", audio_dur)
    bg_path = str(OUT_DIR / "bg.mp4")
    log.info("Generating black background video...")
    _ffmpeg(
        "-f", "lavfi",
        "-i", f"color=c=0x1a1a2e:s={WIDTH}x{HEIGHT}:d={audio_dur}:r={FPS}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        bg_path,
    )

    # Step 3: Burn captions for each size
    for size in SIZES:
        log.info("Rendering size %d (→ %dpx at %dp)...", size, int(size * HEIGHT / 480), HEIGHT)
        ass_content = build_ass(word_boundaries, fontsize_ref=size)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ass", delete=False, encoding="utf-8", dir=str(OUT_DIR)
        ) as f:
            ass_path = f.name
            f.write(ass_content)

        out_path = str(OUT_DIR / f"test_s{size}.mp4")
        ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:")
        _ffmpeg(
            "-i", bg_path,
            "-i", audio_path,
            "-map", "0:v", "-map", "1:a",
            "-vf", f"subtitles='{ass_escaped}'",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(audio_dur),
            "-aspect", "9:16",
            out_path,
        )

        Path(ass_path).unlink(missing_ok=True)
        log.info("  → %s  (fontsize_ref=%d, actual=%dpx)", out_path, size, int(size * HEIGHT / 480))

    log.info("Done. Videos in %s", OUT_DIR)
    for size in SIZES:
        actual_px = int(size * HEIGHT / 480)
        print(f"  test_s{size}.mp4  — fontsize_ref={size}  actual={actual_px}px")


if __name__ == "__main__":
    main()
