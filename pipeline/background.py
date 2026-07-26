"""
Phase 2 — Background compositor.
Picks a clip from the library and applies randomized variation.
Fallback: Ken Burns effect on a static gradient image if library is empty.
"""
import json
import logging
import random
import sqlite3
from pathlib import Path

import numpy as np
import PIL.Image
# MoviePy 1.0.3 uses ANTIALIAS which Pillow 10+ removed
if not hasattr(PIL.Image, "ANTIALIAS"):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

from moviepy.editor import VideoFileClip, ColorClip, CompositeVideoClip, ImageClip
from moviepy.video.fx import all as vfx

log = logging.getLogger(__name__)

TARGET_W, TARGET_H = 1080, 1920  # 9:16
TARGET_DURATION = 30.0            # seconds; assembler will trim/loop to quote audio length


# ── Clip picker ───────────────────────────────────────────────────────────────

def pick_clip(conn: sqlite3.Connection, mood_hint: str = "") -> dict | None:
    """
    Pick a background clip from the library.
    Prefers matching mood, then least-used, then random.
    Returns row dict or None if library empty.
    """
    if mood_hint:
        row = conn.execute(
            """SELECT id, file_path, mood_tag, color_tag, duration
               FROM background_clips
               WHERE mood_tag = ?
               ORDER BY times_used ASC, RANDOM()
               LIMIT 1""",
            (mood_hint,),
        ).fetchone()
        if row:
            return _row_to_dict(row)

    row = conn.execute(
        """SELECT id, file_path, mood_tag, color_tag, duration
           FROM background_clips
           ORDER BY times_used ASC, RANDOM()
           LIMIT 1"""
    ).fetchone()
    return _row_to_dict(row) if row else None


def _row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "file_path": row[1],
        "mood_tag": row[2],
        "color_tag": row[3],
        "duration": row[4],
    }


# ── Variation engine ──────────────────────────────────────────────────────────

def _random_variation() -> dict:
    """Generate randomized variation parameters."""
    return {
        "crop_x": random.uniform(0.0, 0.15),        # left crop fraction
        "crop_y": random.uniform(0.0, 0.10),        # top crop fraction
        "speed": random.choice([0.75, 0.85, 1.0, 1.15, 1.25]),
        "tint": random.choice([None, "warm", "cool", "muted", "vivid"]),
        "overlay": random.choice([None, "grain", "vignette", "gradient"]),
        "start_offset": random.uniform(0.0, 0.3),   # fraction of clip to skip at start
    }


def _apply_tint(clip: VideoFileClip, tint: str | None) -> VideoFileClip:
    if tint is None:
        return clip
    if tint == "warm":
        return clip.fl_image(lambda f: np.clip(f * [1.1, 0.95, 0.85], 0, 255).astype(np.uint8))
    if tint == "cool":
        return clip.fl_image(lambda f: np.clip(f * [0.85, 0.95, 1.15], 0, 255).astype(np.uint8))
    if tint == "muted":
        return clip.fl_image(lambda f: (f * 0.75 + 30).clip(0, 255).astype(np.uint8))
    if tint == "vivid":
        return clip.fl_image(lambda f: np.clip(f * 1.2 - 10, 0, 255).astype(np.uint8))
    return clip


def _apply_overlay(clip: VideoFileClip, overlay: str | None) -> VideoFileClip:
    if overlay is None:
        return clip

    w, h = clip.size

    if overlay == "vignette":
        # Dark circular vignette
        x = np.linspace(-1, 1, w)
        y = np.linspace(-1, 1, h)
        xx, yy = np.meshgrid(x, y)
        mask = np.clip(1.0 - (xx**2 + yy**2) * 0.6, 0, 1)
        mask_rgb = np.stack([mask] * 3, axis=-1)
        def apply_vignette(frame):
            return (frame * mask_rgb).astype(np.uint8)
        return clip.fl_image(apply_vignette)

    if overlay == "grain":
        def apply_grain(frame):
            noise = np.random.randint(-18, 18, frame.shape, dtype=np.int16)
            return np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        return clip.fl_image(apply_grain)

    if overlay == "gradient":
        # Vertical dark gradient at bottom (helps text readability)
        gradient = np.zeros((h, w, 3), dtype=np.uint8)
        for i in range(h):
            alpha = max(0, (i - h * 0.5) / (h * 0.5))
            gradient[i] = int(alpha * 120)
        grad_clip = ImageClip(gradient, ismask=False).set_duration(clip.duration)
        return CompositeVideoClip([clip, grad_clip.set_opacity(0.6)])

    return clip


def composite(
    clip_meta: dict,
    variation: dict | None = None,
    target_duration: float = TARGET_DURATION,
) -> tuple[VideoFileClip, dict]:
    """
    Apply variation to a clip and return composited VideoFileClip + variation params used.
    """
    if variation is None:
        variation = _random_variation()

    path = clip_meta["file_path"]
    if not Path(path).exists():
        raise FileNotFoundError(f"Clip not found: {path}")

    clip = VideoFileClip(path)

    # Start offset
    start = variation["start_offset"] * clip.duration
    clip = clip.subclip(start)

    # Speed
    speed = variation["speed"]
    if speed != 1.0:
        clip = clip.fx(vfx.speedx, speed)

    # Loop to fill target duration
    if clip.duration < target_duration:
        loops = int(target_duration / clip.duration) + 1
        from moviepy.editor import concatenate_videoclips
        clip = concatenate_videoclips([clip] * loops)
    clip = clip.subclip(0, target_duration)

    # Crop to 9:16, removing crop_x / crop_y fraction
    w, h = clip.size
    cx = int(w * variation["crop_x"])
    cy = int(h * variation["crop_y"])
    crop_w = w - 2 * cx
    crop_h = h - 2 * cy
    clip = clip.crop(x1=cx, y1=cy, x2=cx + crop_w, y2=cy + crop_h)

    # Resize to 9:16
    clip = clip.resize((TARGET_W, TARGET_H))

    # Tint
    clip = _apply_tint(clip, variation["tint"])

    # Overlay
    clip = _apply_overlay(clip, variation["overlay"])

    return clip, variation


# ── Ken Burns fallback ────────────────────────────────────────────────────────

def ken_burns_fallback(duration: float = TARGET_DURATION) -> VideoFileClip:
    """
    Generate a Ken Burns motion on a gradient background.
    Used when the clip library is empty.
    """
    # Dark gradient: deep blue → near black
    gradient = np.zeros((TARGET_H, TARGET_W, 3), dtype=np.uint8)
    for i in range(TARGET_H):
        t = i / TARGET_H
        gradient[i] = [int(10 + 30 * (1 - t)), int(10 + 20 * (1 - t)), int(30 + 60 * (1 - t))]

    base = ImageClip(gradient).set_duration(duration)

    # Slow zoom in
    def zoom(t):
        scale = 1.0 + 0.1 * (t / duration)
        return scale

    clip = base.resize(zoom)
    clip = clip.crop(
        x_center=TARGET_W / 2,
        y_center=TARGET_H / 2,
        width=TARGET_W,
        height=TARGET_H,
    )
    return clip


# ── Public interface ──────────────────────────────────────────────────────────

def get_background(
    conn: sqlite3.Connection,
    mood_hint: str = "",
    target_duration: float = TARGET_DURATION,
) -> tuple[VideoFileClip, dict]:
    """
    Main entry point for the assembler.
    Returns (composited_clip, variation_params).
    """
    clip_meta = pick_clip(conn, mood_hint)

    if clip_meta is None:
        log.warning("Library empty — using Ken Burns fallback")
        return ken_burns_fallback(target_duration), {"fallback": "ken_burns"}

    variation = _random_variation()
    log.info(
        "Background: clip_id=%d mood=%s speed=%.2f tint=%s overlay=%s",
        clip_meta["id"], clip_meta["mood_tag"],
        variation["speed"], variation["tint"], variation["overlay"],
    )

    try:
        clip, variation = composite(clip_meta, variation, target_duration)
        # Increment usage counter
        conn.execute(
            "UPDATE background_clips SET times_used = times_used + 1 WHERE id = ?",
            (clip_meta["id"],),
        )
        conn.commit()
        return clip, {**variation, "clip_id": clip_meta["id"]}
    except Exception as e:
        log.error("Compositor failed for clip %d: %s — falling back", clip_meta["id"], e)
        return ken_burns_fallback(target_duration), {"fallback": "ken_burns", "error": str(e)}
