"""
Background clip library builder.
Runs independently of the video pipeline — accumulates clips on free-tier quotas.
Providers: Google AI Studio (Veo), Kling AI. HF deferred (auth broken).
"""
import json
import logging
import sqlite3
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

CLIPS_DIR = Path("data/clips")
CLIPS_DIR.mkdir(parents=True, exist_ok=True)

# Mood prompts for abstract/ambient clips — varied to fill library with range
CLIP_PROMPTS = [
    {"prompt": "Abstract flowing light particles in dark space, slow motion, cinematic",
     "mood": "energetic", "color": "blue", "motion": "flowing"},
    {"prompt": "Golden sunset over misty mountains, timelapse clouds moving gently",
     "mood": "calm", "color": "golden", "motion": "slow"},
    {"prompt": "Dark storm clouds with lightning flashes, dramatic sky, no people",
     "mood": "intense", "color": "dark", "motion": "dynamic"},
    {"prompt": "Soft bokeh lights floating upward, warm tones, dreamy atmosphere",
     "mood": "uplifting", "color": "warm", "motion": "floating"},
    {"prompt": "Ocean waves crashing on rocks at dawn, misty spray, wide shot",
     "mood": "calm", "color": "blue", "motion": "rhythmic"},
    {"prompt": "City lights at night from above, slow zoom out, urban glow",
     "mood": "ambitious", "color": "multi", "motion": "zoom"},
    {"prompt": "Forest path in morning light, rays through trees, no people",
     "mood": "peaceful", "color": "green", "motion": "static"},
    {"prompt": "Abstract ink drops spreading in water, macro close-up, slow motion",
     "mood": "creative", "color": "dark", "motion": "flowing"},
    {"prompt": "Stars rotating over dark landscape, timelapse milky way",
     "mood": "inspiring", "color": "blue", "motion": "rotating"},
    {"prompt": "Candle flame burning close-up, warm flicker, black background",
     "mood": "focused", "color": "warm", "motion": "flicker"},
]


# ── Veo (Google AI Studio) ────────────────────────────────────────────────────

def _veo_generate(prompt: str, api_key: str) -> str | None:
    """Submit Veo generation job. Returns operation name or None on failure."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/veo-3.1-generate-preview:predictLongRunning?key={api_key}"
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {
            "aspectRatio": "9:16",
            "durationSeconds": 6,
            "sampleCount": 1,
        },
    }
    try:
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()
        return r.json().get("name")
    except Exception as e:
        log.error("Veo generate failed: %s", e)
        return None


def _veo_poll(operation_name: str, api_key: str, max_wait: int = 300) -> str | None:
    """Poll until done. Returns video bytes URL or None."""
    url = f"https://generativelanguage.googleapis.com/v1beta/{operation_name}?key={api_key}"
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            if data.get("done"):
                videos = data.get("response", {}).get("videos", [])
                if videos:
                    return videos[0].get("bytesBase64Encoded"), videos[0].get("mimeType", "video/mp4")
                log.error("Veo done but no video in response: %s", data)
                return None, None
            log.debug("Veo still processing...")
        except Exception as e:
            log.warning("Veo poll error: %s", e)
        time.sleep(15)
    log.error("Veo timed out after %ds", max_wait)
    return None, None


def fetch_veo_clip(prompt_data: dict, api_key: str, conn: sqlite3.Connection) -> bool:
    """Generate one clip via Veo, save to disk, record in DB."""
    prompt = prompt_data["prompt"]
    log.info("Veo: generating — %s", prompt[:60])

    op = _veo_generate(prompt, api_key)
    if not op:
        return False

    b64_data, mime_type = _veo_poll(op, api_key)
    if not b64_data:
        return False

    import base64
    video_bytes = base64.b64decode(b64_data)
    filename = f"veo_{int(time.time())}.mp4"
    path = CLIPS_DIR / filename
    path.write_bytes(video_bytes)

    # Get duration via moviepy
    duration = _get_duration(str(path))

    conn.execute(
        """INSERT INTO background_clips (file_path, provider, mood_tag, color_tag, duration)
           VALUES (?, 'veo', ?, ?, ?)""",
        (str(path), prompt_data["mood"], prompt_data["color"], duration),
    )
    conn.commit()
    _track_quota(conn, "veo", 1, daily_limit=25)
    log.info("Veo: saved %s (%.1fs)", filename, duration or 0)
    return True


# ── Kling AI ──────────────────────────────────────────────────────────────────

def _kling_generate(prompt: str, api_key: str) -> str | None:
    """Submit Kling generation job. Returns task_id or None."""
    url = "https://api.klingai.com/v1/videos/text2video"
    payload = {
        "prompt": prompt,
        "model_name": "kling-v1",
        "duration": "5",
        "mode": "std",
        "aspect_ratio": "9:16",
    }
    try:
        r = requests.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") == 0:
            return data["data"]["task_id"]
        log.error("Kling generate error: %s", data)
        return None
    except Exception as e:
        log.error("Kling generate failed: %s", e)
        return None


def _kling_poll(task_id: str, api_key: str, max_wait: int = 300) -> str | None:
    """Poll until done. Returns video URL or None."""
    url = f"https://api.klingai.com/v1/videos/text2video/{task_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            data = r.json()
            if data.get("code") == 0:
                status = data["data"]["task_status"]
                if status == "succeed":
                    videos = data["data"].get("task_result", {}).get("videos", [])
                    return videos[0]["url"] if videos else None
                if status == "failed":
                    log.error("Kling task failed: %s", data)
                    return None
            log.debug("Kling status: %s", data.get("data", {}).get("task_status"))
        except Exception as e:
            log.warning("Kling poll error: %s", e)
        time.sleep(15)
    log.error("Kling timed out after %ds", max_wait)
    return None


def fetch_kling_clip(prompt_data: dict, api_key: str, conn: sqlite3.Connection) -> bool:
    """Generate one clip via Kling, download, record in DB."""
    prompt = prompt_data["prompt"]
    log.info("Kling: generating — %s", prompt[:60])

    task_id = _kling_generate(prompt, api_key)
    if not task_id:
        return False

    video_url = _kling_poll(task_id, api_key)
    if not video_url:
        return False

    filename = f"kling_{int(time.time())}.mp4"
    path = CLIPS_DIR / filename
    r = requests.get(video_url, timeout=60, stream=True)
    r.raise_for_status()
    with open(path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)

    duration = _get_duration(str(path))
    conn.execute(
        """INSERT INTO background_clips (file_path, provider, mood_tag, color_tag, duration)
           VALUES (?, 'kling', ?, ?, ?)""",
        (str(path), prompt_data["mood"], prompt_data["color"], duration),
    )
    conn.commit()
    _track_quota(conn, "kling", 1, daily_limit=5)
    log.info("Kling: saved %s (%.1fs)", filename, duration or 0)
    return True


# ── Pexels stock footage ──────────────────────────────────────────────────────

# Search queries mapped to mood — visually engaging, no people, works as background
PEXELS_QUERIES = [
    {"query": "ocean waves crashing shore",        "mood": "calm",      "color": "blue"},
    {"query": "fire flames close up",              "mood": "intense",   "color": "warm"},
    {"query": "milky way stars night sky",         "mood": "inspiring", "color": "blue"},
    {"query": "sunset clouds timelapse",           "mood": "uplifting", "color": "golden"},
    {"query": "rain drops window",                 "mood": "calm",      "color": "dark"},
    {"query": "forest trees sunlight",             "mood": "peaceful",  "color": "green"},
    {"query": "city skyline night lights",         "mood": "ambitious", "color": "multi"},
    {"query": "waterfall nature",                  "mood": "energetic", "color": "blue"},
    {"query": "abstract neon lights bokeh",        "mood": "creative",  "color": "multi"},
    {"query": "mountain peak clouds aerial",       "mood": "inspiring", "color": "blue"},
    {"query": "candle flame dark background",      "mood": "focused",   "color": "warm"},
    {"query": "storm lightning dramatic sky",      "mood": "intense",   "color": "dark"},
    {"query": "sunrise horizon golden hour",       "mood": "uplifting", "color": "golden"},
    {"query": "flowing river rocks nature",        "mood": "calm",      "color": "green"},
    {"query": "smoke fog dark atmospheric",        "mood": "creative",  "color": "dark"},
]


def fetch_pexels_clips(
    api_key: str,
    conn: sqlite3.Connection,
    count: int = 5,
    min_duration: int = 10,
) -> int:
    """
    Download `count` clips from Pexels. Rotates through PEXELS_QUERIES.
    Returns number of clips successfully saved.
    """
    existing = conn.execute("SELECT COUNT(*) FROM background_clips").fetchone()[0]
    query_index = existing % len(PEXELS_QUERIES)

    saved = 0
    attempts = 0
    headers = {"Authorization": api_key}

    while saved < count and attempts < len(PEXELS_QUERIES):
        q = PEXELS_QUERIES[(query_index + attempts) % len(PEXELS_QUERIES)]
        attempts += 1

        try:
            # Search Pexels videos
            r = requests.get(
                "https://api.pexels.com/videos/search",
                headers=headers,
                params={"query": q["query"], "per_page": 10, "orientation": "portrait"},
                timeout=15,
            )
            r.raise_for_status()
            videos = r.json().get("videos", [])

            for video in videos:
                duration = video.get("duration", 0)
                if duration < min_duration:
                    continue

                # Pick best portrait file (prefer HD)
                files = video.get("video_files", [])
                portrait = [
                    f for f in files
                    if f.get("width", 0) < f.get("height", 0)  # portrait
                ]
                if not portrait:
                    portrait = files  # fallback to any

                # Pick highest resolution portrait file
                best = max(portrait, key=lambda f: f.get("width", 0) * f.get("height", 0))
                url = best.get("link")
                if not url:
                    continue

                # Check not already downloaded
                already = conn.execute(
                    "SELECT 1 FROM background_clips WHERE file_path LIKE ?",
                    (f"%pexels_{video['id']}%",),
                ).fetchone()
                if already:
                    continue

                # Download
                filename = f"pexels_{video['id']}.mp4"
                dest = CLIPS_DIR / filename
                log.info("Pexels: downloading %s — %s", filename, q["query"])

                dl = requests.get(url, timeout=120, stream=True)
                dl.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in dl.iter_content(chunk_size=65536):
                        f.write(chunk)

                actual_duration = _get_duration(str(dest))
                conn.execute(
                    """INSERT INTO background_clips (file_path, provider, mood_tag, color_tag, duration, keyword_tags)
                       VALUES (?, 'static', ?, ?, ?, ?)""",
                    (str(dest), q["mood"], q["color"], actual_duration, q["query"]),
                )
                conn.commit()
                log.info("Saved: %s  mood=%s  duration=%.1fs", filename, q["mood"], actual_duration or 0)
                saved += 1
                break  # one clip per query, move on

        except Exception as e:
            log.warning("Pexels query '%s' failed: %s", q["query"], e)

    log.info("Pexels: fetched %d/%d clips", saved, count)
    return saved


# ── Static / local import ─────────────────────────────────────────────────────

def import_local_clip(
    path: str,
    conn: sqlite3.Connection,
    mood_tag: str = "calm",
    color_tag: str = "",
) -> bool:
    """
    Register an existing local video file in the clip library.
    Useful for seeding the library with stock footage you already have.
    """
    p = Path(path).resolve()
    if not p.exists():
        log.error("File not found: %s", path)
        return False

    # Skip if already registered
    exists = conn.execute(
        "SELECT 1 FROM background_clips WHERE file_path = ?", (str(p),)
    ).fetchone()
    if exists:
        log.info("Already in library: %s", p.name)
        return False

    duration = _get_duration(str(p))
    conn.execute(
        """INSERT INTO background_clips (file_path, provider, mood_tag, color_tag, duration)
           VALUES (?, 'static', ?, ?, ?)""",
        (str(p), mood_tag, color_tag, duration),
    )
    conn.commit()
    log.info("Imported: %s  mood=%s  duration=%.1fs", p.name, mood_tag, duration or 0)
    return True


def scan_and_import(
    directory: str,
    conn: sqlite3.Connection,
    mood_tag: str = "calm",
) -> int:
    """
    Scan a directory for .mp4/.mov/.avi files and import all into the library.
    Returns count of newly added clips.
    """
    exts = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    added = 0
    for p in Path(directory).iterdir():
        if p.suffix.lower() in exts:
            if import_local_clip(str(p), conn, mood_tag=mood_tag):
                added += 1
    log.info("Scan complete — added %d clips from %s", added, directory)
    return added


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_duration(path: str) -> float | None:
    try:
        from moviepy.editor import VideoFileClip
        with VideoFileClip(path) as clip:
            return clip.duration
    except Exception:
        return None


def _track_quota(conn: sqlite3.Connection, provider: str, units: int, daily_limit: int) -> None:
    from datetime import date
    today = date.today().isoformat()
    conn.execute(
        """INSERT INTO quota_usage (provider, date, units_used, unit_limit)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(provider, date) DO UPDATE SET units_used = units_used + excluded.units_used""",
        (provider, today, units, daily_limit),
    )
    conn.commit()


def _quota_remaining(conn: sqlite3.Connection, provider: str, daily_limit: int) -> int:
    from datetime import date
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT units_used FROM quota_usage WHERE provider = ? AND date = ?",
        (provider, today),
    ).fetchone()
    used = row[0] if row else 0
    return max(0, daily_limit - used)


# ── Main builder loop ─────────────────────────────────────────────────────────

def run_builder(
    db_path: str,
    veo_key: str = "",
    kling_key: str = "",
    pexels_key: str = "",
    pexels_count: int = 5,
) -> None:
    """
    Run one cycle of the library builder.
    Pulls clips from available providers. Safe to run repeatedly.
    """
    conn = sqlite3.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM background_clips").fetchone()[0]
    log.info("Library: %d clips currently", total)

    prompt_index = total % len(CLIP_PROMPTS)

    # Pexels (free, no quota limit beyond rate limiting)
    if pexels_key:
        fetch_pexels_clips(pexels_key, conn, count=pexels_count)

    # Veo
    if veo_key and _quota_remaining(conn, "veo", 25) > 0:
        prompt_data = CLIP_PROMPTS[prompt_index % len(CLIP_PROMPTS)]
        fetch_veo_clip(prompt_data, veo_key, conn)
        prompt_index += 1

    # Kling
    if kling_key and _quota_remaining(conn, "kling", 5) > 0:
        prompt_data = CLIP_PROMPTS[prompt_index % len(CLIP_PROMPTS)]
        fetch_kling_clip(prompt_data, kling_key, conn)

    total_after = conn.execute("SELECT COUNT(*) FROM background_clips").fetchone()[0]
    print(f"Library: {total_after} clips total (+{total_after - total} new)")
    conn.close()


if __name__ == "__main__":
    import argparse
    import os
    from dotenv import load_dotenv

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    load_dotenv()

    parser = argparse.ArgumentParser(description="Background clip library builder")
    sub = parser.add_subparsers(dest="cmd")

    # generate: pull clips from Veo / Kling / Pexels
    p_gen = sub.add_parser("generate", help="Pull clips from AI providers + Pexels")
    p_gen.add_argument("--pexels-count", type=int, default=5, help="Clips to fetch from Pexels")

    # import: add a single local file
    p_import = sub.add_parser("import", help="Import a local video file")
    p_import.add_argument("file", help="Path to video file")
    p_import.add_argument("--mood", default="calm")
    p_import.add_argument("--color", default="")

    # scan: import all videos in a directory
    p_scan = sub.add_parser("scan", help="Import all videos from a directory")
    p_scan.add_argument("directory", help="Directory to scan")
    p_scan.add_argument("--mood", default="calm")

    # status: print library stats
    sub.add_parser("status", help="Show library and quota status")

    args = parser.parse_args()
    db_path = "data/db/agent.db"
    conn = sqlite3.connect(db_path)

    if args.cmd == "generate":
        run_builder(
            db_path=db_path,
            veo_key=os.getenv("GOOGLE_AI_STUDIO_API_KEY", ""),
            kling_key=os.getenv("KLING_API_KEY", ""),
            pexels_key=os.getenv("PEXELS_API_KEY", ""),
            pexels_count=args.pexels_count,
        )

    elif args.cmd == "import":
        ok = import_local_clip(args.file, conn, mood_tag=args.mood, color_tag=args.color)
        print("Imported." if ok else "Skipped (already exists or not found).")

    elif args.cmd == "scan":
        n = scan_and_import(args.directory, conn, mood_tag=args.mood)
        print(f"Added {n} clips.")

    elif args.cmd == "status":
        total = conn.execute("SELECT COUNT(*) FROM background_clips").fetchone()[0]
        by_provider = conn.execute(
            "SELECT provider, COUNT(*) FROM background_clips GROUP BY provider"
        ).fetchall()
        by_mood = conn.execute(
            "SELECT mood_tag, COUNT(*) FROM background_clips GROUP BY mood_tag"
        ).fetchall()
        print(f"\nLibrary: {total} clips total")
        print("By provider:", dict(by_provider))
        print("By mood:    ", dict(by_mood))

        from datetime import date
        today = date.today().isoformat()
        quotas = conn.execute(
            "SELECT provider, units_used, unit_limit FROM quota_usage WHERE date = ?", (today,)
        ).fetchall()
        if quotas:
            print(f"\nQuota today ({today}):")
            for provider, used, limit in quotas:
                print(f"  {provider}: {used}/{limit}")

    else:
        parser.print_help()

    conn.close()
