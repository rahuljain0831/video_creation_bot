"""
Scene clip fetcher — acquires one VideoFileClip per story scene.
Fallback chain: Veo → Kling → Pexels (cache-first) → library → ken_burns
"""
import logging
import sqlite3
import time
from pathlib import Path

import requests

import PIL.Image
if not hasattr(PIL.Image, "ANTIALIAS"):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

from moviepy.editor import VideoFileClip

# Reuse existing implementations
from library_builder.builder import (
    _veo_generate, _veo_poll,
    _kling_generate, _kling_poll,
    _get_duration, _quota_remaining, _track_quota,
    CLIPS_DIR,
)
from pipeline.background import composite, pick_clip, ken_burns_fallback

log = logging.getLogger(__name__)


# ── Pexels per-scene fetch ────────────────────────────────────────────────────

def _pexels_fetch(
    search_term: str,
    api_key: str,
    conn: sqlite3.Connection,
    clips_dir: Path,
    min_duration: int = 8,
) -> dict | None:
    """
    Cache-first Pexels fetch for a single search term.
    Returns clip_meta dict or None.
    """
    # Cache check
    row = conn.execute(
        """SELECT id, file_path, mood_tag, color_tag, duration
           FROM background_clips
           WHERE keyword_tags LIKE ?
           ORDER BY times_used ASC, RANDOM()
           LIMIT 1""",
        (f"%{search_term[:30]}%",),
    ).fetchone()
    if row:
        log.info("Pexels cache hit: %s", search_term)
        return {"id": row[0], "file_path": row[1], "mood_tag": row[2],
                "color_tag": row[3], "duration": row[4]}

    if not api_key:
        return None

    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": api_key},
            params={"query": search_term, "per_page": 10, "orientation": "portrait"},
            timeout=15,
        )
        r.raise_for_status()
        videos = r.json().get("videos", [])

        for video in videos:
            if video.get("duration", 0) < min_duration:
                continue

            files = video.get("video_files", [])
            portrait = [f for f in files if f.get("width", 1) < f.get("height", 1)]
            candidates = portrait or files
            if not candidates:
                continue
            best = max(candidates, key=lambda f: f.get("width", 0) * f.get("height", 0))
            url = best.get("link")
            if not url:
                continue

            # Skip if already downloaded
            already = conn.execute(
                "SELECT 1 FROM background_clips WHERE file_path LIKE ?",
                (f"%pexels_{video['id']}%",),
            ).fetchone()
            if already:
                continue

            filename = f"pexels_{video['id']}.mp4"
            dest = clips_dir / filename
            log.info("Pexels: downloading %s — %s", filename, search_term)

            dl = requests.get(url, timeout=120, stream=True)
            dl.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in dl.iter_content(chunk_size=65536):
                    f.write(chunk)

            duration = _get_duration(str(dest))
            conn.execute(
                """INSERT INTO background_clips
                   (file_path, provider, mood_tag, color_tag, duration, keyword_tags)
                   VALUES (?, 'static', '', '', ?, ?)""",
                (str(dest), duration, search_term),
            )
            conn.commit()
            clip_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            log.info("Pexels: saved %s (%.1fs)", filename, duration or 0)
            return {"id": clip_id, "file_path": str(dest), "mood_tag": "",
                    "color_tag": "", "duration": duration}

    except Exception as e:
        log.warning("Pexels fetch failed for '%s': %s", search_term, e)

    return None


# ── Veo per-scene fetch ───────────────────────────────────────────────────────

def _veo_fetch(
    description: str,
    veo_key: str,
    conn: sqlite3.Connection,
    clips_dir: Path,
) -> dict | None:
    if not veo_key or _quota_remaining(conn, "veo", 25) < 1:
        return None
    try:
        import base64
        op = _veo_generate(description, veo_key)
        if not op:
            return None
        b64_data, _ = _veo_poll(op, veo_key, max_wait=360)
        if not b64_data:
            return None

        filename = f"veo_{int(time.time())}.mp4"
        dest = clips_dir / filename
        dest.write_bytes(base64.b64decode(b64_data))
        duration = _get_duration(str(dest))

        conn.execute(
            """INSERT INTO background_clips
               (file_path, provider, mood_tag, color_tag, duration, keyword_tags)
               VALUES (?, 'veo', '', '', ?, ?)""",
            (str(dest), duration, description[:80]),
        )
        conn.commit()
        _track_quota(conn, "veo", 1, 25)
        clip_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        log.info("Veo: saved %s (%.1fs)", filename, duration or 0)
        return {"id": clip_id, "file_path": str(dest), "mood_tag": "",
                "color_tag": "", "duration": duration}
    except Exception as e:
        log.warning("Veo fetch failed: %s", e)
        return None


# ── Kling per-scene fetch ─────────────────────────────────────────────────────

def _kling_fetch(
    search_term: str,
    kling_key: str,
    conn: sqlite3.Connection,
    clips_dir: Path,
) -> dict | None:
    if not kling_key or _quota_remaining(conn, "kling", 5) < 1:
        return None
    try:
        task_id = _kling_generate(search_term, kling_key)
        if not task_id:
            return None
        video_url = _kling_poll(task_id, kling_key, max_wait=360)
        if not video_url:
            return None

        filename = f"kling_{int(time.time())}.mp4"
        dest = clips_dir / filename
        r = requests.get(video_url, timeout=120, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)

        duration = _get_duration(str(dest))
        conn.execute(
            """INSERT INTO background_clips
               (file_path, provider, mood_tag, color_tag, duration, keyword_tags)
               VALUES (?, 'kling', '', '', ?, ?)""",
            (str(dest), duration, search_term),
        )
        conn.commit()
        _track_quota(conn, "kling", 1, 5)
        clip_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        log.info("Kling: saved %s (%.1fs)", filename, duration or 0)
        return {"id": clip_id, "file_path": str(dest), "mood_tag": "",
                "color_tag": "", "duration": duration}
    except Exception as e:
        log.warning("Kling fetch failed: %s", e)
        return None


# ── Public interface ──────────────────────────────────────────────────────────

def fetch_scene_clip(
    scene: dict,
    conn: sqlite3.Connection,
    clips_dir: str,
    veo_key: str = "",
    kling_key: str = "",
    pexels_key: str = "",
    target_duration: float = 6.0,
) -> VideoFileClip:
    """
    Returns a composited VideoFileClip for one scene.
    Fallback chain: Veo → Kling → Pexels → library → ken_burns. Never raises.
    """
    cdir = Path(clips_dir)
    cdir.mkdir(parents=True, exist_ok=True)

    search   = scene.get("search", "")
    mood     = scene.get("mood", "calm")
    desc     = scene.get("description", search)

    clip_meta = None

    # 1. Veo
    if not clip_meta and veo_key:
        clip_meta = _veo_fetch(desc or search, veo_key, conn, cdir)

    # 2. Kling
    if not clip_meta and kling_key:
        clip_meta = _kling_fetch(search, kling_key, conn, cdir)

    # 3. Pexels (cache-first)
    if not clip_meta and search:
        clip_meta = _pexels_fetch(search, pexels_key, conn, cdir)

    # 4. Existing library by mood
    if not clip_meta:
        clip_meta = pick_clip(conn, mood_hint=mood)
        if clip_meta:
            log.info("Scene '%s': using library clip id=%d", search, clip_meta["id"])

    # 5. Ken Burns fallback
    if not clip_meta:
        log.warning("Scene '%s': all sources failed — using Ken Burns", search)
        return ken_burns_fallback(target_duration)

    # Composite the clip (apply variation effects)
    try:
        clip, _ = composite(clip_meta, target_duration=target_duration)
        # Update usage counter
        conn.execute(
            "UPDATE background_clips SET times_used = times_used + 1 WHERE id = ?",
            (clip_meta["id"],),
        )
        conn.commit()
        return clip
    except Exception as e:
        log.warning("composite() failed for clip %d: %s — Ken Burns fallback", clip_meta["id"], e)
        return ken_burns_fallback(target_duration)
