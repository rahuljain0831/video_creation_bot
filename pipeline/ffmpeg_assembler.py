"""
ffmpeg assembler — design-v3 pivot.

Assembles final 9:16 video from per-scene AI images + narration audio using pure ffmpeg.
Each image gets a Ken Burns (pan/zoom) effect, then clips are concatenated, captions burned,
and narration audio mixed in.

assemble_from_images(scene_images, audio_path, output_path, scenes, cfg) -> str
"""

import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_RES = (1080, 1920)
_DEFAULT_FPS = 30


def _ffmpeg(*args, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["ffmpeg", "-y", "-loglevel", "error"] + list(args)
    log.debug("ffmpeg: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=check)


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


def _scale_clip(src: str, dest: str, width: int, height: int, fps: int) -> None:
    """Scale + pad clip to target resolution, maintaining aspect ratio."""
    _ffmpeg(
        "-i", src,
        "-vf", (
            f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2,"
            f"fps={fps}"
        ),
        "-an",          # strip audio from clip
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        dest,
    )


def _fit_clip_to_duration(
    src: str, dest: str, target_duration: float, width: int, height: int, fps: int,
) -> None:
    """Scale+crop clip to target resolution AND force it to exactly target_duration.
    Trims if the source clip is longer; loops if shorter."""
    src_dur = _get_duration(src)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2,"
        f"fps={fps}"
    )
    if src_dur >= target_duration or src_dur <= 0:
        _ffmpeg(
            "-i", src, "-t", str(target_duration),
            "-vf", vf, "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            dest,
        )
    else:
        _ffmpeg(
            "-stream_loop", "-1", "-i", src, "-t", str(target_duration),
            "-vf", vf, "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            dest,
        )


def _ken_burns_image(
    img_path: str,
    dest: str,
    duration: float,
    width: int,
    height: int,
    fps: int,
    direction: str = "zoom_in",
) -> None:
    """
    Convert a static image to a video clip with Ken Burns (pan/zoom) effect.

    Scales the image to 1.4× the target before applying zoompan so there are
    spare pixels to zoom and pan without hitting the edge.

    direction choices: zoom_in | zoom_out | pan_right | pan_left
    """
    frames = max(int(duration * fps), 1)
    pad_w  = int(width  * 1.4)
    pad_h  = int(height * 1.4)

    max_zoom      = 1.3
    zoom_increment = (max_zoom - 1.0) / frames

    if direction == "zoom_in":
        zoom_expr = f"min(zoom+{zoom_increment:.6f},{max_zoom})"
        x_expr    = "iw/2-(iw/zoom/2)"
        y_expr    = "ih/2-(ih/zoom/2)"
    elif direction == "zoom_out":
        zoom_expr = f"if(eq(on\\,1)\\,{max_zoom}\\,max(zoom-{zoom_increment:.6f}\\,1.0))"
        x_expr    = "iw/2-(iw/zoom/2)"
        y_expr    = "ih/2-(ih/zoom/2)"
    elif direction == "pan_right":
        zoom_expr = "1.2"
        x_expr    = f"min(x+{(pad_w - width) / frames:.4f}\\,iw-iw/zoom)"
        y_expr    = "ih/2-(ih/zoom/2)"
    elif direction == "pan_left":
        zoom_expr = "1.2"
        x_expr    = f"max(x-{(pad_w - width) / frames:.4f}\\,0)"
        y_expr    = "ih/2-(ih/zoom/2)"
    else:
        zoom_expr = f"min(zoom+{zoom_increment:.6f},{max_zoom})"
        x_expr    = "iw/2-(iw/zoom/2)"
        y_expr    = "ih/2-(ih/zoom/2)"

    vf = (
        f"scale={pad_w}:{pad_h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={pad_w}:{pad_h}:(iw-{pad_w})/2:(ih-{pad_h})/2,"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}'"
        f":d={frames}:s={width}x{height},"
        f"fps={fps},"
        f"setsar=1"
    )

    _ffmpeg(
        "-loop", "1",
        "-i", img_path,
        "-vf", vf,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        dest,
    )


def _concat_clips(clip_paths: list[str], dest: str, fps: int) -> None:
    """Concat clips via ffmpeg concat demuxer (no re-encode if same codec)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        list_path = f.name
        for p in clip_paths:
            f.write(f"file '{p}'\n")
    try:
        _ffmpeg(
            "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-fps_mode", "vfr",
            dest,
        )
    finally:
        os.unlink(list_path)


def _sanitize_srt(text: str) -> str:
    """Normalize LLM text for SRT subtitles — strip control chars, fix common Unicode."""
    _UNICODE_MAP = {
        "\u2014": "-",    # em dash
        "\u2013": "-",    # en dash
        "\u2018": "'",    # left single quote
        "\u2019": "'",    # right single quote
        "\u201c": '"',    # left double quote
        "\u201d": '"',    # right double quote
        "\u2026": "...",  # ellipsis
    }
    for char, replacement in _UNICODE_MAP.items():
        text = text.replace(char, replacement)
    # Drop non-printable control chars (keep printable Unicode — SRT supports it)
    return "".join(c for c in text if c >= " " or c in "\n\r")


def _seconds_to_srt_time(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    ms = int((s % 1) * 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def _seconds_to_ass_time(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h}:{m:02d}:{sec:05.2f}"


def _sanitize_ass(text: str) -> str:
    """Strip characters that break ASS format."""
    _MAP = {
        "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"', "\u2026": "...",
    }
    for char, replacement in _MAP.items():
        text = text.replace(char, replacement)
    # Braces are ASS control delimiters — remove bare ones from text
    text = text.replace("{", "").replace("}", "")
    return text.replace("\n", " ").replace("\r", "").strip()


def _build_ass_karaoke(
    word_timings: list[dict],
    chunk_size: int,
    width: int,
    height: int,
    cfg=None,
) -> str:
    """
    Build an ASS subtitle file with karaoke-style word highlighting.

    Words grouped into chunks of `chunk_size`. For each word, one Dialogue line
    shows the full chunk with the active word highlighted bold+yellow and the
    rest in white. Timings come from Edge TTS word boundary events (100-ns units).

    fontsize in settings is a "480p reference" value: actual ASS fontsize =
    fontsize * (height / 480). So fontsize=10 → 40px on a 1920-tall video.
    """
    caption_cfg = {}
    if cfg and hasattr(cfg, "video"):
        caption_cfg = cfg.video.get("caption_style", {})
    fontsize_ref = caption_cfg.get("fontsize", 10)
    alpha        = caption_cfg.get("alpha", 0.0)
    margin_pct   = caption_cfg.get("margin_bottom_percent", 5)
    # Scale fontsize from 480p reference to actual video height
    fontsize  = max(int(fontsize_ref * height / 480), 6)
    # ASS &HAABBGGRR — alpha=0.0 → AA=00 (fully opaque)
    alpha_hex = hex(int(alpha * 255))[2:].upper().zfill(2)
    margin_v  = int(height * margin_pct / 100)

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 1\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,{fontsize},"
        f"&H{alpha_hex}FFFFFF,&H{alpha_hex}FFFF00,&H00000000,&HA0000000,"
        f"0,0,0,0,100,100,0,0,1,2,1,2,20,20,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    # Group into chunks
    chunks: list[list[dict]] = [
        word_timings[i:i + chunk_size]
        for i in range(0, len(word_timings), chunk_size)
    ]
    # Flatten to get next-chunk start for seamless end-of-chunk extension
    flat = word_timings

    events: list[str] = []
    for chunk_idx, chunk in enumerate(chunks):
        words = [_sanitize_ass(w["text"]) for w in chunk]
        for word_idx, word_data in enumerate(chunk):
            start_s = word_data["offset"] / 10_000_000
            dur_s   = word_data["duration"] / 10_000_000
            # Extend this word's event to next word's start (no subtitle gap)
            flat_idx = chunk_idx * chunk_size + word_idx
            if flat_idx + 1 < len(flat):
                next_start = flat[flat_idx + 1]["offset"] / 10_000_000
                end_s = max(start_s + max(dur_s, 0.05), next_start)
            else:
                end_s = start_s + max(dur_s, 0.3)

            # Current word: bold + yellow. Others: white.
            parts = []
            for i, word in enumerate(words):
                if i == word_idx:
                    # {\c&HBBGGRR&} — yellow in BGR = &H00FFFF
                    parts.append(r"{\c&H00FFFF&\b1}" + word + r"{\b0\c&HFFFFFF&}")
                else:
                    parts.append(word)

            text      = " ".join(parts)
            start_str = _seconds_to_ass_time(start_s)
            end_str   = _seconds_to_ass_time(end_s)
            events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text}")

    return header + "\n".join(events) + "\n"


def _split_sentences(text: str) -> list[str]:
    """Split narration into individual sentences on .  !  ? boundaries."""
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]


def _write_srt(scenes: list[dict], scene_durations: list[float], path: str) -> None:
    """Write an SRT file — one subtitle per sentence, time-sliced within each scene."""
    lines = []
    t = 0.0
    idx = 1
    for scene, dur in zip(scenes, scene_durations):
        text = _sanitize_srt(scene.get("narration", "").strip())
        if not text:
            t += dur
            continue
        sentences = _split_sentences(text)
        if not sentences:
            t += dur
            continue
        time_per = dur / len(sentences)
        st = t
        for sentence in sentences:
            start = _seconds_to_srt_time(st)
            end   = _seconds_to_srt_time(st + time_per)
            lines.append(f"{idx}\n{start} --> {end}\n{sentence}\n")
            idx += 1
            st += time_per
        t += dur
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _burn_captions(
    video_path: str,
    dest: str,
    scenes: list[dict],
    scene_durations: list[float],
    width: int,
    height: int,
    cfg=None,
    word_timings_path: str | None = None,
) -> None:
    """
    Burn narration captions into video.

    When word_timings_path is provided (Edge TTS word boundary data), generates
    ASS karaoke subtitles: 7-8 word chunks, active word highlighted bold+yellow.
    Falls back to SRT (one subtitle per scene) when timings are unavailable.
    """
    if not scenes:
        _ffmpeg("-i", video_path, "-c", "copy", dest)
        return

    caption_cfg = {}
    if cfg and hasattr(cfg, "video"):
        caption_cfg = cfg.video.get("caption_style", {})

    use_karaoke = word_timings_path is not None and Path(word_timings_path).exists()

    suffix = ".ass" if use_karaoke else ".srt"
    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8") as f:
        sub_path = f.name

    try:
        if use_karaoke:
            with open(word_timings_path, encoding="utf-8") as f:
                word_timings = json.load(f)
            chunk_size = caption_cfg.get("chunk_size", 7)
            ass_content = _build_ass_karaoke(word_timings, chunk_size, width, height, cfg)
            with open(sub_path, "w", encoding="utf-8") as f:
                f.write(ass_content)
            log.info("Karaoke captions: %d words, chunk_size=%d", len(word_timings), chunk_size)
            sub_escaped = sub_path.replace("\\", "/").replace(":", "\\:")
            vf = f"subtitles='{sub_escaped}'"
        else:
            # SRT fallback
            fontsize_ref = caption_cfg.get("fontsize", 10)
            alpha        = caption_cfg.get("alpha", 0.0)
            margin_pct   = caption_cfg.get("margin_bottom_percent", 5)
            alpha_hex    = hex(int(alpha * 255))[2:].upper().zfill(2)
            # Scale fontsize from 480p reference to actual video height
            fontsize     = max(int(fontsize_ref * height / 480), 6)
            margin_v     = int(height * margin_pct / 100)
            _write_srt(scenes, scene_durations, sub_path)
            style = (
                f"Fontsize={fontsize},Fontname=Arial,"
                f"PrimaryColour=&H{alpha_hex}FFFFFF,"
                f"OutlineColour=&H000000,Outline=2,Shadow=1,"
                f"Alignment=2,MarginV={margin_v}"
            )
            sub_escaped = sub_path.replace("\\", "/").replace(":", "\\:")
            vf = f"subtitles='{sub_escaped}':force_style='{style}'"

        result = _ffmpeg(
            "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            dest,
            check=False,
        )
        if result.returncode != 0:
            log.warning(
                "subtitles filter failed (returncode=%d) — copying without captions. stderr: %s",
                result.returncode, result.stderr[:300],
            )
            _ffmpeg("-i", video_path, "-c", "copy", dest)
    finally:
        os.unlink(sub_path)


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines, current = [], []
    for word in words:
        if sum(len(w) for w in current) + len(current) + len(word) > width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


_KB_DIRECTIONS = ["zoom_in", "zoom_out", "pan_right", "pan_left"]


def assemble_from_images(
    scene_images: list[str],
    audio_path: str,
    output_path: str,
    scenes: list[dict] | None = None,
    cfg=None,
    scene_duration: float | None = None,
    bg_audio_path: str | None = None,
    word_timings_path: str | None = None,
    scene_durations: list[float] | None = None,
) -> str:
    """
    Assemble final video from per-scene AI images + narration audio.

    Each image is animated with a Ken Burns effect (alternating directions),
    clips are concatenated, captions burned, and narration audio mixed in.

    Args:
        scene_images:  List of image file paths (one per scene). Must be non-empty.
        audio_path:    Path to narration audio file (mp3/wav).
        output_path:   Destination mp4 path.
        scenes:        Scene dicts with "narration" key (for captions).
        cfg:           Config object (optional, for resolution/fps).
        scene_duration: Seconds per scene. If None, derived from audio length / scenes.
        bg_audio_path: Optional path to background audio (mp3). Mixed at low volume
                       under narration using ffmpeg filter_complex. Loops to video length.

    Returns:
        output_path on success.

    Raises:
        RuntimeError on ffmpeg failure.
    """
    if not scene_images:
        raise ValueError("assemble_from_images: scene_images is empty")

    width, height = _DEFAULT_RES
    fps = _DEFAULT_FPS
    if cfg:
        res = cfg.video.get("resolution", _DEFAULT_RES)
        width, height = res[0], res[1]
        fps = cfg.video.get("fps", _DEFAULT_FPS)

    # Derive per-scene duration from audio length if not specified
    if scene_duration is None:
        audio_dur = _get_duration(audio_path)
        if audio_dur > 0:
            scene_duration = max(audio_dur / len(scene_images), 3.0)
        else:
            scene_duration = 5.0

    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # Step 1: Ken Burns each image → video clip
        clip_paths = []
        for i, img_path in enumerate(scene_images):
            direction   = _KB_DIRECTIONS[i % len(_KB_DIRECTIONS)]
            clip_dest   = str(tmp / f"kb_{i}.mp4")
            dur = scene_durations[i] if scene_durations else scene_duration
            log.info("Ken Burns: scene %d/%d (%s) — %s", i + 1, len(scene_images), direction, img_path)
            _ken_burns_image(img_path, clip_dest, dur, width, height, fps, direction)
            clip_paths.append(clip_dest)

        # Step 2: Concat clips
        if len(clip_paths) == 1:
            concat_path = clip_paths[0]
        else:
            concat_path = str(tmp / "concat.mp4")
            log.info("Concatenating %d clips", len(clip_paths))
            _concat_clips(clip_paths, concat_path, fps)

        # Step 3: Per-scene durations for caption timing
        scene_durations = [_get_duration(p) for p in clip_paths]
        total_video_dur = sum(scene_durations)
        log.info("Total video duration: %.2fs", total_video_dur)

        # Step 4: Burn captions
        if scenes:
            captioned_path = str(tmp / "captioned.mp4")
            log.info("Burning captions")
            _burn_captions(concat_path, captioned_path, scenes, scene_durations, width, height, cfg, word_timings_path)
        else:
            captioned_path = concat_path

        # Step 5: Mix audio
        audio_dur = _get_duration(audio_path)
        log.info("Audio duration: %.2fs", audio_dur)

        if audio_dur <= 0:
            _ffmpeg("-i", captioned_path, "-c", "copy", "-aspect", "9:16", output_path)

        elif bg_audio_path and Path(bg_audio_path).exists():
            bg_volume = 0.12
            if cfg:
                bg_volume = cfg.background_audio.get("volume", 0.12)
            min_dur = min(total_video_dur, audio_dur)
            log.info("Mixing bg audio: %s volume=%.2f", bg_audio_path, bg_volume)
            _ffmpeg(
                "-i", captioned_path,
                "-i", audio_path,
                "-i", bg_audio_path,
                "-filter_complex",
                (
                    f"[2:a]volume={bg_volume},aloop=loop=-1:size=2000000000,"
                    f"atrim=duration={min_dur}[bg];"
                    f"[1:a][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
                ),
                "-map", "0:v:0",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k",
                "-t", str(min_dur),
                "-aspect", "9:16",
                "-movflags", "+faststart",
                output_path,
            )

        else:
            min_dur = min(total_video_dur, audio_dur)
            _ffmpeg(
                "-i", captioned_path,
                "-i", audio_path,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k",
                "-t", str(min_dur),
                "-aspect", "9:16",
                "-movflags", "+faststart",
                output_path,
            )

    log.info("ffmpeg assembly complete: %s", output_path)
    return output_path


def assemble_video(
    scene_clips: list[str],
    audio_path: str,
    output_path: str,
    scenes: list[dict] | None = None,
    cfg=None,
    word_timings_path: str | None = None,
    scene_durations: list[float] | None = None,
) -> str:
    """Legacy entry point — assembles from pre-rendered video clips (not images)."""
    if not scene_clips:
        raise ValueError("assemble_video: scene_clips is empty")

    width, height = _DEFAULT_RES
    fps = _DEFAULT_FPS
    if cfg:
        res = cfg.video.get("resolution", _DEFAULT_RES)
        width, height = res[0], res[1]
        fps = cfg.video.get("fps", _DEFAULT_FPS)

    if scene_durations is None:
        audio_dur = _get_duration(audio_path)
        scene_durations = [max(audio_dur / len(scene_clips), 3.0)] * len(scene_clips)

    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # Step 1: Scale+trim/loop all clips to target resolution and duration
        scaled = []
        for i, clip_path in enumerate(scene_clips):
            scaled_path = str(tmp / f"scaled_{i}.mp4")
            log.info("Fitting clip %d/%d: %s", i + 1, len(scene_clips), clip_path)
            _fit_clip_to_duration(clip_path, scaled_path, scene_durations[i], width, height, fps)
            scaled.append(scaled_path)

        # Step 2: Concat (or use single clip directly)
        if len(scaled) == 1:
            concat_path = scaled[0]
        else:
            concat_path = str(tmp / "concat.mp4")
            log.info("Concatenating %d clips", len(scaled))
            _concat_clips(scaled, concat_path, fps)

        total_video_dur = sum(scene_durations)
        log.info("Total video duration: %.2fs", total_video_dur)

        # Step 3: Burn captions
        if scenes:
            captioned_path = str(tmp / "captioned.mp4")
            log.info("Burning captions")
            _burn_captions(concat_path, captioned_path, scenes, scene_durations, width, height, cfg, word_timings_path)
        else:
            captioned_path = concat_path

        # Step 4: Mix audio — pad/trim audio to match video length
        audio_dur = _get_duration(audio_path)
        log.info("Audio duration: %.2fs", audio_dur)

        if audio_dur <= 0:
            # No audio — just copy video
            _ffmpeg("-i", captioned_path, "-c", "copy", output_path)
        else:
            # Trim video or audio to match shorter one
            min_dur = min(total_video_dur, audio_dur)

            _ffmpeg(
                "-i", captioned_path,
                "-i", audio_path,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k",
                "-t", str(min_dur),    # trim to shorter of video/audio
                "-movflags", "+faststart",
                output_path,
            )

    log.info("ffmpeg assembly complete: %s", output_path)
    return output_path
