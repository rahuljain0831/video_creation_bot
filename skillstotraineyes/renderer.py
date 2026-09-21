"""
Draws a Drill's ops with Pillow and pipes raw frames to ffmpeg, muxing audio in
the same pass.

Speed: circles are pasted from cached, supersampled alpha masks rather than
drawn per frame, so shapes are anti-aliased without paying 4x per frame.
Frames go to ffmpeg over stdin as rawvideo — no intermediate PNGs.

Audio (all optional): an ambience bed, voiceover lines placed at their times,
and a short sine chime at each reveal. Everything is trimmed to the video's
exact length so audio duration == video duration.
"""

import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from skillstotraineyes.drills import BG, H, W, Drill

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
_SS = 4                       # supersampling factor for shape masks
_TEXT_MAX_W = 880             # keeps text inside the side gutters
BED_VOLUME = 0.9      # bed must reach ~-29 dBFS RMS or it is inaudible on a phone speaker
VOICE_VOLUME = 1.0
CHIME_VOLUME = 3.0    # lavfi sine is -18 dBFS and the fade averages it lower; this lands ~12 dB over the bed


class RenderError(RuntimeError):
    pass


@lru_cache(maxsize=32)
def _font(size: int):
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


@lru_cache(maxsize=256)
def _disc_mask(r: int) -> Image.Image:
    d = 2 * r + 2
    m = Image.new("L", (d * _SS, d * _SS), 0)
    ImageDraw.Draw(m).ellipse([_SS, _SS, (2 * r + 1) * _SS, (2 * r + 1) * _SS], fill=255)
    return m.resize((d, d), Image.LANCZOS)


@lru_cache(maxsize=256)
def _ring_mask(r: int, w: int) -> Image.Image:
    d = 2 * r + 2
    m = Image.new("L", (d * _SS, d * _SS), 0)
    dr = ImageDraw.Draw(m)
    dr.ellipse([_SS, _SS, (2 * r + 1) * _SS, (2 * r + 1) * _SS], fill=255)
    dr.ellipse([(1 + w) * _SS, (1 + w) * _SS, (2 * r + 1 - w) * _SS, (2 * r + 1 - w) * _SS], fill=0)
    return m.resize((d, d), Image.LANCZOS)


@lru_cache(maxsize=16)
def _dotted_mask(r: int) -> Image.Image:
    import math
    d = 2 * r + 20
    m = Image.new("L", (d * _SS, d * _SS), 0)
    dr = ImageDraw.Draw(m)
    n = max(24, int(2 * math.pi * r / 26))
    c = d * _SS / 2
    for k in range(n):
        a = 2 * math.pi * k / n
        x, y = c + r * _SS * math.cos(a), c + r * _SS * math.sin(a)
        dr.ellipse([x - 4 * _SS, y - 4 * _SS, x + 4 * _SS, y + 4 * _SS], fill=255)
    return m.resize((d, d), Image.LANCZOS)


def _paste_mask(img: Image.Image, mask: Image.Image, cx: float, cy: float, color) -> None:
    img.paste(color, (round(cx - mask.width / 2), round(cy - mask.height / 2)), mask)


def _wrap(s: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in s.split():
        trial = f"{cur} {word}".strip()
        if cur and font.getlength(trial) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur] if cur else lines


def draw_frame(ops: list, base: Image.Image) -> Image.Image:
    img = base.copy()
    dr = ImageDraw.Draw(img)
    for op in ops:
        kind = op[0]
        if kind == "disc":
            _, x, y, r, col = op
            _paste_mask(img, _disc_mask(int(r)), x, y, col)
        elif kind == "ring":
            _, x, y, r, col, w = op
            _paste_mask(img, _ring_mask(int(r), int(w)), x, y, col)
        elif kind == "dotted":
            _, x, y, r, col = op
            _paste_mask(img, _dotted_mask(int(r)), x, y, col)
        elif kind == "cross":
            _, x, y, half, col, w = op
            dr.rectangle([x - half, y - w // 2, x + half, y + w // 2], fill=col)
            dr.rectangle([x - w // 2, y - half, x + w // 2, y + half], fill=col)
        elif kind == "square":
            _, x, y, half, col = op
            dr.rectangle([x - half, y - half, x + half, y + half], fill=col)
        elif kind == "rect":
            _, x, y, hw, hh, col = op
            dr.rectangle([x - hw, y - hh, x + hw, y + hh], fill=col)
        elif kind == "text":
            _, s, x, y, size, col, *anchor = op
            font = _font(int(size))
            lines = _wrap(s, font, _TEXT_MAX_W)
            lh = int(size * 1.2)
            top = y if anchor else y - lh * (len(lines) - 1) / 2
            for i, line in enumerate(lines):
                dr.text((x, top + i * lh), line, font=font, fill=col, anchor="mm",
                        stroke_width=4, stroke_fill=(0, 0, 0))
    return img


def _audio_args(drill: Drill, bed: str | None, voice: list[tuple[float, str]],
                first_audio_idx: int) -> tuple[list[str], str, str | None]:
    """Build extra ffmpeg inputs and the filter graph that mixes them.

    Returns (input_args, filter_complex, output_label). `voice` is
    [(start_seconds, wav_path)].
    """
    inputs: list[str] = []
    labels: list[str] = []
    graph: list[str] = []
    idx = first_audio_idx

    if bed:
        inputs += ["-i", bed]
        graph.append(f"[{idx}:a]volume={BED_VOLUME}[a{idx}]")
        labels.append(f"[a{idx}]")
        idx += 1
    for start, path in voice:
        inputs += ["-i", path]
        ms = int(start * 1000)
        graph.append(f"[{idx}:a]adelay={ms}|{ms},volume={VOICE_VOLUME}[a{idx}]")
        labels.append(f"[a{idx}]")
        idx += 1
    for t in drill.chimes:
        inputs += ["-f", "lavfi", "-i", "sine=frequency=1175:duration=0.6:sample_rate=48000"]
        ms = int(t * 1000)
        graph.append(
            f"[{idx}:a]afade=t=out:st=0.05:d=0.55,volume={CHIME_VOLUME},"
            f"aformat=channel_layouts=stereo,adelay={ms}|{ms}[a{idx}]"
        )
        labels.append(f"[a{idx}]")
        idx += 1

    if not labels:
        return [], "", None
    graph.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest,"
        f"apad[aout]"
    )
    return inputs, ";".join(graph), "[aout]"


def render(drill: Drill, out_path: str | Path, bed: str | None = None,
           voice: list[tuple[float, str]] | None = None, max_frames: int | None = None) -> str:
    """Render `drill` to an mp4. `max_frames` truncates (for smoke tests)."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = min(drill.frames, max_frames) if max_frames else drill.frames
    dur = frames / drill.fps

    a_inputs, graph, a_label = _audio_args(drill, bed, voice or [], first_audio_idx=1)
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(drill.fps),
           "-i", "-", *a_inputs]
    if a_label:
        cmd += ["-filter_complex", graph, "-map", "0:v", "-map", a_label,
                "-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
    else:
        cmd += ["-an"]
    cmd += ["-t", f"{dur:.4f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
            "-preset", "veryfast", "-movflags", "+faststart", str(out)]

    base = Image.new("RGB", (W, H), BG)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for f in range(frames):
            proc.stdin.write(draw_frame(drill.ops(f), base).tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait() != 0:
            raise RenderError(f"ffmpeg failed:\n{err[-1500:]}")
    except BrokenPipeError:
        err = proc.stderr.read().decode("utf-8", "replace")
        raise RenderError(f"ffmpeg closed the pipe early:\n{err[-1500:]}")
    finally:
        if proc.poll() is None:
            proc.kill()
    return str(out)
