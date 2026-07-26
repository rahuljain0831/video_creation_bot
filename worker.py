"""
Persistent worker — polls job queue and processes videos end-to-end.
"""
import json
import sqlite3
import time
import logging
from pathlib import Path

from config import cfg
from pipeline import quote_gen, tts, assembler
from pipeline.story_arc import generate_story_arc
from pipeline.scene_fetcher import fetch_scene_clip
from review.telegram_bot import send_for_review

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(cfg.paths["logs"] + "/worker.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("worker")


def get_next_job(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT id, status FROM videos WHERE status = 'queued' ORDER BY created_at LIMIT 1"
    ).fetchone()
    return {"id": row[0], "status": row[1]} if row else None


def set_status(conn: sqlite3.Connection, video_id: int, status: str) -> None:
    conn.execute("UPDATE videos SET status = ? WHERE id = ?", (status, video_id))
    conn.commit()
    log.info("video %d → %s", video_id, status)


def _ensure_quotes(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM quotes WHERE used = 0").fetchone()[0]
    if count < 5:
        log.info("Quote pool low (%d) — generating batch", count)
        quote_gen.generate_batch(conn, model=cfg.quote["model_primary"], count=10)


def _pick_quote(conn: sqlite3.Connection) -> tuple[int, str] | None:
    row = conn.execute(
        "SELECT id, quote_text FROM quotes WHERE used = 0 ORDER BY RANDOM() LIMIT 1"
    ).fetchone()
    return (row[0], row[1]) if row else None


def process_video(conn: sqlite3.Connection, job: dict) -> None:
    vid = job["id"]

    # ── Step 1: Select quote ──────────────────────────────────────────────────
    _ensure_quotes(conn)
    result = _pick_quote(conn)
    if result is None:
        log.error("No unused quotes — rejecting video %d", vid)
        set_status(conn, vid, "rejected")
        return

    quote_id, quote_text = result
    conn.execute("UPDATE videos SET quote_id = ? WHERE id = ?", (quote_id, vid))
    conn.execute("UPDATE quotes SET used = 1, video_id = ? WHERE id = ?", (vid, quote_id))
    conn.commit()
    log.info("video %d — quote_id=%d: %s", vid, quote_id, quote_text[:80])
    set_status(conn, vid, "quote_selected")

    # ── Step 2: TTS ───────────────────────────────────────────────────────────
    try:
        audio_path, voice_duration = tts.synthesize(
            text=quote_text,
            output_dir=cfg.paths["audio"],
            video_id=vid,
            cfg=cfg,
        )
    except Exception:
        log.exception("TTS failed for video %d", vid)
        set_status(conn, vid, "rejected")
        return

    voice_provider = "edge_tts" if audio_path.endswith(".mp3") else "piper"
    conn.execute("UPDATE videos SET voice_provider = ? WHERE id = ?", (voice_provider, vid))
    conn.commit()
    set_status(conn, vid, "voice_ready")

    # ── Step 3: Story arc ─────────────────────────────────────────────────────
    scenes = generate_story_arc(
        quote_text,
        ollama_base_url=cfg.ollama["base_url"],
        model=cfg.quote["model_primary"],
        num_gpu=cfg.ollama.get("num_gpu", 0),
    )
    if not scenes:
        log.warning("Story arc failed for video %d — using single generic scene", vid)
        scenes = [
            {"search": "inspiring nature landscape", "mood": "uplifting", "description": quote_text},
            {"search": "sunrise golden light",       "mood": "inspiring", "description": "Golden light breaks through"},
            {"search": "peaceful sky clouds",        "mood": "calm",      "description": "Vast open sky"},
        ]
    log.info("video %d story arc: %s", vid, [(s["mood"], s["search"]) for s in scenes])

    # ── Step 4: Fetch scene clips ─────────────────────────────────────────────
    min_dur      = float(cfg.video["min_duration_sec"])
    scene_dur    = max(min_dur / len(scenes), 5.0)
    scene_clips  = []
    variation    = {"scenes": [s["search"] for s in scenes]}

    for i, scene in enumerate(scenes):
        log.info("video %d — fetching scene %d/%d: %s", vid, i + 1, len(scenes), scene["search"])
        clip = fetch_scene_clip(
            scene=scene,
            conn=conn,
            clips_dir=cfg.paths["clips"],
            veo_key=cfg.GOOGLE_AI_STUDIO_API_KEY,
            kling_key=cfg.KLING_API_KEY,
            pexels_key=cfg.PEXELS_API_KEY,
            target_duration=scene_dur,
        )
        scene_clips.append(clip)

    conn.execute(
        "UPDATE videos SET variation_params = ? WHERE id = ?",
        (json.dumps(variation), vid),
    )
    conn.commit()
    set_status(conn, vid, "bg_ready")

    # ── Step 5: Assemble ──────────────────────────────────────────────────────
    try:
        file_path = assembler.assemble(
            video_id=vid,
            quote_text=quote_text,
            bg_clip=scene_clips[0],
            audio_path=audio_path,
            output_dir=cfg.paths["output"],
            min_duration=min_dur,
            scene_clips=scene_clips,
        )
    except Exception:
        log.exception("Assembler failed for video %d", vid)
        set_status(conn, vid, "rejected")
        return

    conn.execute("UPDATE videos SET file_path = ? WHERE id = ?", (file_path, vid))
    conn.commit()
    set_status(conn, vid, "assembled")

    # ── Step 6: Pre-screen (Phase 6b — future) ───────────────────────────────
    if cfg.review.get("prescreen_enabled"):
        log.info("Pre-screener not yet implemented — bypassing for video %d", vid)

    set_status(conn, vid, "screened")

    # ── Step 7: Send to Telegram ──────────────────────────────────────────────
    try:
        send_for_review(vid, file_path, quote_text)
        log.info("video %d sent to Telegram", vid)
    except Exception:
        log.exception("Telegram send failed for video %d — video still assembled", vid)

    log.info("video %d complete → %s", vid, file_path)


def enqueue(conn: sqlite3.Connection, account_id: str = "") -> int:
    conn.execute(
        "INSERT INTO videos (account_id, status) VALUES (?, 'queued')", (account_id,)
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def run() -> None:
    Path(cfg.paths["logs"]).mkdir(parents=True, exist_ok=True)
    db_path = cfg.paths["db"]
    log.info("Worker starting. DB: %s", db_path)
    conn = sqlite3.connect(db_path, check_same_thread=False)

    while True:
        job = get_next_job(conn)
        if job:
            log.info("Processing job: video_id=%d", job["id"])
            try:
                process_video(conn, job)
            except Exception:
                log.exception("Job %d failed unexpectedly", job["id"])
                try:
                    set_status(conn, job["id"], "rejected")
                except Exception:
                    pass
        else:
            time.sleep(5)


if __name__ == "__main__":
    run()
