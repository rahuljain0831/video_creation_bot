"""
ffmpeg assembler — design-v3.

Assembles final 9:16 video from scene clips + narration audio using pure ffmpeg.
No MoviePy dependency. Requires ffmpeg on PATH.

assemble_video(scene_clips, audio_path, output_path, narration_lines, cfg) -> str
"""

import logging
import os
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
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"fps={fps}"
        ),
        "-an",          # strip audio from clip
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
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


def _burn_captions(
    video_path: str,
    dest: str,
    scenes: list[dict],
    scene_durations: list[float],
    width: int,
    height: int,
) -> None:
    """
    Burn narration captions into video using ffmpeg drawtext.
    Each scene's narration is shown for its clip duration.
    """
    if not scenes:
        # No captions — just copy
        _ffmpeg("-i", video_path, "-c", "copy", dest)
        return

    # Build drawtext filter chain — one per scene, timed with enable=
    filters = []
    t = 0.0
    for i, (scene, dur) in enumerate(zip(scenes, scene_durations)):
        narration = scene.get("narration", "").replace("'", "\\'").replace(":", "\\:")
        if not narration:
            t += dur
            continue

        # Word wrap: max ~35 chars per line (drawtext doesn't auto-wrap)
        lines = _wrap(narration, 35)
        # Stack lines: each line offset by 50px
        line_height = 52
        base_y = height - 280  # position near bottom

        for j, line in enumerate(lines[:4]):  # max 4 lines
            y = base_y + j * line_height
            line_escaped = line.replace("'", "\\'").replace(":", "\\:")
            filters.append(
                f"drawtext=text='{line_escaped}'"
                f":fontsize=44:fontcolor=white:borderw=3:bordercolor=black"
                f":x=(w-text_w)/2:y={y}"
                f":enable='between(t,{t:.3f},{t + dur:.3f})'"
            )
        t += dur

    if not filters:
        _ffmpeg("-i", video_path, "-c", "copy", dest)
        return

    vf = ",".join(filters)
    _ffmpeg("-i", video_path, "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "23", dest)


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


def assemble_video(
    scene_clips: list[str],
    audio_path: str,
    output_path: str,
    scenes: list[dict] | None = None,
    cfg=None,
) -> str:
    """
    Assemble final video from scene clips + narration audio.

    Args:
        scene_clips:  List of video file paths (one per scene). Must be non-empty.
        audio_path:   Path to narration audio file (mp3/wav).
        output_path:  Destination mp4 path.
        scenes:       Scene dicts (for caption text). If None, no captions burned.
        cfg:          Config object (optional, for resolution/fps).

    Returns:
        output_path on success.

    Raises:
        RuntimeError on ffmpeg failure.
    """
    if not scene_clips:
        raise ValueError("assemble_video: scene_clips is empty")

    width, height = _DEFAULT_RES
    fps = _DEFAULT_FPS
    if cfg:
        res = cfg.video.get("resolution", _DEFAULT_RES)
        width, height = res[0], res[1]
        fps = cfg.video.get("fps", _DEFAULT_FPS)

    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # Step 1: Scale all clips to target resolution
        scaled = []
        for i, clip_path in enumerate(scene_clips):
            scaled_path = str(tmp / f"scaled_{i}.mp4")
            log.info("Scaling clip %d/%d: %s", i + 1, len(scene_clips), clip_path)
            _scale_clip(clip_path, scaled_path, width, height, fps)
            scaled.append(scaled_path)

        # Step 2: Concat (or use single clip directly)
        if len(scaled) == 1:
            concat_path = scaled[0]
        else:
            concat_path = str(tmp / "concat.mp4")
            log.info("Concatenating %d clips", len(scaled))
            _concat_clips(scaled, concat_path, fps)

        # Step 3: Get per-scene durations (for caption timing)
        scene_durations = [_get_duration(p) for p in scaled]
        total_video_dur = sum(scene_durations)
        log.info("Total video duration: %.2fs", total_video_dur)

        # Step 4: Burn captions
        if scenes:
            captioned_path = str(tmp / "captioned.mp4")
            log.info("Burning captions")
            _burn_captions(concat_path, captioned_path, scenes, scene_durations, width, height)
        else:
            captioned_path = concat_path

        # Step 5: Mix audio — pad/trim audio to match video length
        audio_dur = _get_duration(audio_path)
        log.info("Audio duration: %.2fs", audio_dur)

        if audio_dur <= 0:
            # No audio — just copy video
            _ffmpeg("-i", captioned_path, "-c", "copy", output_path)
        else:
            # Trim video or audio to match shorter one
            max_dur = max(total_video_dur, audio_dur)
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
