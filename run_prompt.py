"""
Prompt-driven video entry point — design-v3, Phase 1.

Usage:
    python run_prompt.py "two cats cooking a meal"
    python run_prompt.py "broken heart plant care" --dry-run
    python run_prompt.py "why arteries clog" --no-telegram

Pipeline (per design-v3):
    prompt
      → script_gen: LLM writes tone/ending/topic/scenes, logs decisions to DB
      → per scene: scene_fetcher (Veo → Kling → Pexels → library → ken_burns)
      → TTS: synthesize combined narration
      → ffmpeg_assembler: assemble 9:16 video
      → Telegram: send for review
"""

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("run_prompt")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a video from a text prompt.")
    parser.add_argument("prompt", help="One-line video prompt")
    parser.add_argument("--dry-run", action="store_true",
                        help="Stop after script generation (no API calls, no video)")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Skip Telegram send (video still assembled)")
    args = parser.parse_args()

    prompt = args.prompt.strip()
    if not prompt:
        parser.error("Prompt cannot be empty")

    log.info("=" * 60)
    log.info("Prompt: %s", prompt)
    log.info("=" * 60)

    # ── Load config ───────────────────────────────────────────────────────────
    from config import cfg

    db_path = cfg.paths["db"]
    clips_dir = cfg.paths["clips"]
    audio_dir = cfg.paths["audio"]
    output_dir = cfg.paths["output"]

    # ── Init DB ───────────────────────────────────────────────────────────────
    from db.init_db import init_db
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")

    # ── Insert video row ──────────────────────────────────────────────────────
    conn.execute(
        "INSERT INTO videos (status, prompt) VALUES ('queued', ?)",
        (prompt,),
    )
    conn.commit()
    video_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    log.info("Created video row: id=%d", video_id)

    try:
        # ── Step 1: Generate script (logs decisions BEFORE any API call) ──────
        log.info("[1/5] Generating script...")
        from pipeline.script_gen import generate_script
        script = generate_script(prompt, conn, video_id, cfg=cfg)

        log.info(
            "Script: tone=%s ending=%s topic=%s scenes=%d",
            script["tone"], script["ending_type"], script["topic"], script["scene_count"],
        )
        for i, scene in enumerate(script["scenes"], 1):
            log.info("  Scene %d: %s", i, scene["video_prompt"][:80])
            log.info("  Narration: %s", scene["narration"])

        if args.dry_run:
            log.info("--dry-run: stopping after script generation. Decisions logged to DB.")
            _print_decisions(conn, video_id)
            conn.execute("UPDATE videos SET status='screened' WHERE id=?", (video_id,))
            conn.commit()
            return

        # ── Step 2: Fetch scene clips ─────────────────────────────────────────
        log.info("[2/5] Fetching scene clips...")
        from pipeline.scene_fetcher import fetch_scene_clip

        scene_clip_paths = []
        for i, scene in enumerate(script["scenes"], 1):
            log.info("  Fetching clip for scene %d...", i)
            clip = fetch_scene_clip(
                scene,
                conn=conn,
                clips_dir=clips_dir,
                veo_key=cfg.GOOGLE_AI_STUDIO_API_KEY,
                kling_key=cfg.KLING_API_KEY,
                pexels_key=cfg.PEXELS_API_KEY,
                target_duration=8.0,
            )
            # scene_fetcher returns a VideoFileClip — save it to disk for ffmpeg
            clip_path = str(Path(clips_dir) / f"scene_{video_id}_{i}.mp4")
            clip.write_videofile(clip_path, fps=cfg.video.get("fps", 30),
                                 codec="libx264", audio=False, logger=None)
            clip.close()
            scene_clip_paths.append(clip_path)
            log.info("  Clip %d saved: %s", i, clip_path)

        conn.execute("UPDATE videos SET status='bg_ready' WHERE id=?", (video_id,))
        conn.commit()

        # ── Step 3: TTS ───────────────────────────────────────────────────────
        log.info("[3/5] Synthesizing narration...")
        from pipeline.tts import synthesize

        # Combine all scene narration lines with a short pause separator
        full_narration = "  ".join(s["narration"] for s in script["scenes"])
        audio_path, audio_dur = synthesize(full_narration, audio_dir, video_id, cfg=cfg)
        log.info("TTS done: %s (%.2fs)", audio_path, audio_dur)

        conn.execute(
            "UPDATE videos SET status='voice_ready', voice_provider=? WHERE id=?",
            ("edge_tts", video_id),
        )
        conn.commit()

        # ── Step 4: Assemble video ────────────────────────────────────────────
        log.info("[4/5] Assembling video with ffmpeg...")
        from pipeline.ffmpeg_assembler import assemble_video

        output_path = str(Path(output_dir) / f"video_{video_id}_{int(time.time())}.mp4")
        assemble_video(
            scene_clips=scene_clip_paths,
            audio_path=audio_path,
            output_path=output_path,
            scenes=script["scenes"],
            cfg=cfg,
        )
        log.info("Video assembled: %s", output_path)

        conn.execute(
            "UPDATE videos SET status='assembled', file_path=? WHERE id=?",
            (output_path, video_id),
        )
        conn.commit()

        # ── Step 5: Telegram review ───────────────────────────────────────────
        if args.no_telegram:
            log.info("[5/5] --no-telegram: skipping Telegram send.")
            log.info("Final video: %s", output_path)
        else:
            log.info("[5/5] Sending to Telegram for review...")
            _send_telegram(video_id, output_path, script, conn, cfg)

        log.info("=" * 60)
        log.info("Done. video_id=%d", video_id)
        log.info("=" * 60)

    except Exception as e:
        log.error("Pipeline failed at video_id=%d: %s", video_id, e, exc_info=True)
        conn.execute("UPDATE videos SET status='rejected' WHERE id=?", (video_id,))
        conn.commit()
        sys.exit(1)
    finally:
        conn.close()


def _send_telegram(
    video_id: int,
    video_path: str,
    script: dict,
    conn: sqlite3.Connection,
    cfg,
) -> None:
    import asyncio
    from review.telegram_bot import send_for_review

    caption = (
        f"*Prompt:* {conn.execute('SELECT prompt FROM videos WHERE id=?', (video_id,)).fetchone()[0]}\n"
        f"*Tone:* {script['tone']}  |  *Ending:* {script['ending_type']}\n"
        f"*Topic:* {script['topic']}"
    )
    asyncio.run(
        send_for_review(
            video_path=video_path,
            video_id=video_id,
            caption=caption,
            bot_token=cfg.TELEGRAM_BOT_TOKEN,
            chat_id=cfg.TELEGRAM_CHAT_ID,
        )
    )
    conn.execute("UPDATE videos SET status='sent' WHERE id=?", (video_id,))
    conn.commit()
    log.info("Sent to Telegram.")


def _print_decisions(conn: sqlite3.Connection, video_id: int) -> None:
    rows = conn.execute(
        "SELECT decision_point, chosen_option, reasoning FROM decisions WHERE video_id=? ORDER BY id",
        (video_id,),
    ).fetchall()
    log.info("Decisions logged for video_id=%d:", video_id)
    for point, option, reasoning in rows:
        log.info("  [%s] %s — %s", point, option, reasoning[:100])


if __name__ == "__main__":
    main()
