"""
Post-processing for generated scene images: guarantee the render resolution.

Providers do not honour the requested size. Pollinations snaps to its own grid
(a 720x1280 request comes back 576x1024), Gemini returns whatever its imageSize
tier produces, and ComfyUI upscales to its own chain's output. Remotion then
scales whatever it gets to 1080x1920 *and* overscans it by ~1.18x for the Ken
Burns move, so a 576-wide source is shown at roughly 2.2x — which is why the
frames looked soft.

Doing the resize here rather than in the browser buys two things: ffmpeg's
lanczos kernel with an unsharp pass afterwards is much better than a CSS
upscale, and it happens once per image instead of once per frame.

Failure posture matches horror_audio: warn and keep the original. A slightly
soft video still publishes; a crashed pipeline does not.

Entry point:
    ensure_render_size(path, width, height) -> str
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_SIZE = (1080, 1920)


def probe_size(path: str | Path) -> tuple[int, int] | None:
    """Pixel dimensions of an image, or None if they can't be read."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        stream = json.loads(proc.stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError,
            KeyError, IndexError, ValueError) as e:
        log.warning("image_post: could not probe %s (%s)", path, e)
        return None


def detail_score(path: str | Path) -> float:
    """
    How much visible structure an image has. Higher is busier.

    Mean absolute difference between horizontally adjacent pixels on a small
    greyscale copy — cheap, no model, and it separates a real photograph from a
    smooth gradient. A blurred fog frame with one light in it scores near 2; a
    corridor with edges and texture scores 5 or more.

    Only meaningful *relative to another frame from the same run* — a
    legitimately minimal composition can score as low as a bad one, so this must
    never be used as an absolute pass/fail.
    """
    try:
        from PIL import Image
    except ImportError:
        return 0.0

    try:
        im = Image.open(path).convert("L").resize((96, 96))
    except OSError as e:
        log.warning("image_post: could not score %s (%s)", path, e)
        return 0.0

    px = list(im.getdata())
    width = 96
    diffs = [abs(px[i] - px[i + 1]) for i in range(len(px) - 1) if (i + 1) % width]
    return sum(diffs) / max(1, len(diffs))


# A beat's second image is only worth cutting to if it carries at least this
# share of the primary's detail. Measured: a content-free fog frame that landed
# in the middle of a scare beat scored 44% of its primary, while every genuinely
# different second image in the same run scored above 65%.
_MIN_RELATIVE_DETAIL = 0.55


def is_worth_cutting_to(candidate: str | Path, primary: str | Path) -> bool:
    """
    True when a beat's second image adds something over its first.

    The second image is generated blind and lands in the middle of the beat that
    matters most — impact, reveal, scare. When the generator returns an empty
    gradient for it, the payoff shot becomes a few seconds of fog. Falling back
    to a tighter crop of the primary is strictly better than cutting to nothing.
    """
    cand = detail_score(candidate)
    base = detail_score(primary)
    if base <= 0:
        return True          # nothing to compare against; keep it

    ratio = cand / base
    if ratio < _MIN_RELATIVE_DETAIL:
        log.info("image_post: %s has %.0f%% of the primary's detail — not cutting to it",
                 Path(candidate).name, ratio * 100)
        return False
    return True


def ensure_render_size(
    path: str | Path,
    width: int = _DEFAULT_SIZE[0],
    height: int = _DEFAULT_SIZE[1],
) -> str:
    """
    Resize an image to exactly width x height, in place, preserving subject framing.

    Aspect mismatches are centre-cropped rather than letterboxed — a black bar in
    a full-bleed vertical video reads as a bug. `increase` scales the short edge
    to cover, then the crop takes the middle.

    The unsharp pass only runs when upscaling; sharpening a downscale just adds
    halos to an image that is already crisp.

    Returns the path (unchanged on any failure, so callers can use it directly).
    """
    src = Path(path)
    size = probe_size(src)
    if size == (width, height):
        return str(src)

    upscaling = size is None or size[0] < width
    chain = (
        f"scale={width}:{height}:flags=lanczos:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )
    if upscaling:
        chain += ",unsharp=5:5:0.6:5:5:0.0"

    tmp = src.with_name(f"{src.stem}.resized{src.suffix}")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(src),
             "-vf", chain, "-frames:v", "1", str(tmp)],
            capture_output=True, text=True, check=True,
        )
        tmp.replace(src)
        log.info("image_post: %s %s -> %dx%d", src.name, size or "?", width, height)
    except (OSError, subprocess.CalledProcessError) as e:
        log.warning("image_post: resize of %s failed (%s) — keeping the original", src, e)
        tmp.unlink(missing_ok=True)

    return str(src)
