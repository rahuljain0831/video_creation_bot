"""
pipeline/retry.py — Re-run full pipeline for a rejected video.

Called from review/telegram_bot.py in a ThreadPoolExecutor thread.
Loads original niche from DB, runs script_gen → images → TTS → ffmpeg → Telegram.
"""
import logging
import re
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

MAX_RETRIES = 3


def _make_slug(niche_id: str, story_title: str, video_id: int) -> str:
    title = re.sub(r"[^\w\s-]", "", story_title).strip()
    title = re.sub(r"[\s_]+", "-", title).lower()[:40].strip("-")
    return f"{niche_id}_{title}_{video_id}"


def run_retry(original_video_id: int, feedback: str, cfg) -> int:
    """
    Re-run the full pipeline for a rejected video using user feedback as new story seed.

    Args:
        original_video_id: ID of the rejected video (or its parent chain).
        feedback:           User's rejection feedback text (new story direction).
        cfg:                Config singleton.

    Returns:
        New video_id on success.

    Raises:
        RuntimeError on pipeline failure.
    """
    from pipeline.script_gen import generate_script
    from pipeline.tts import synthesize
    from pipeline.scene_timing import compute_scene_durations
    from pipeline.audio_bg import fetch_bg_audio
    from pipeline.ffmpeg_assembler import assemble_from_images
    from pipeline.image_library import get_library_image, LibraryEmptyError
    from pipeline.pexels_library import get_pexels_image, PexelsError
    from pipeline.deity_map import find_best_image_for_scene
    from review.telegram_bot import send_for_review
    from pipeline.social_captions import generate_social_captions, format_telegram_message

    db_path = cfg.paths["db"]
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        # ── Load original video context ──────────────────────────────────────
        row = conn.execute(
            "SELECT niche_id, retry_count FROM videos WHERE id=?",
            (original_video_id,)
        ).fetchone()
        if not row:
            raise RuntimeError(f"run_retry: video_id={original_video_id} not found")

        niche_id, current_retry = row
        new_retry_count = current_retry + 1

        # ── Look up niche config ─────────────────────────────────────────────
        niche = next((n for n in cfg.niches if n["id"] == niche_id), None)
        if not niche:
            raise RuntimeError(f"run_retry: niche_id={niche_id!r} not found in settings")

        log.info(
            "run_retry: video_id=%d niche=%s retry=%d/%d feedback=%r",
            original_video_id, niche_id, new_retry_count, MAX_RETRIES, feedback[:80],
        )

        # ── Create new video row ─────────────────────────────────────────────
        conn.execute(
            """INSERT INTO videos (status, prompt, niche_id, retry_count, parent_video_id, rejection_feedback)
               VALUES ('queued', ?, ?, ?, ?, ?)""",
            (feedback, niche_id, new_retry_count, original_video_id, feedback),
        )
        conn.commit()
        video_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        log.info("run_retry: new video row id=%d", video_id)

        # ── Step 1: Generate script ──────────────────────────────────────────
        script = generate_script(
            story_seed=feedback,
            niche=niche,
            conn=conn,
            video_id=video_id,
            cfg=cfg,
        )
        log.info("run_retry: script title=%r scenes=%d", script["story_title"], script["scene_count"])

        run_slug = _make_slug(niche_id, script["story_title"], video_id)

        # ── Step 2: Images ───────────────────────────────────────────────────
        images_dir = str(Path(cfg.paths["images"]) / run_slug)
        scene_image_paths = []
        use_pexels = niche.get("image_source", "library") == "pexels"
        used_library_ids: set[int] = set()   # dedup: library image IDs used this video
        used_pexels_ids: set[int] = set()    # dedup: pexels photo IDs used this video

        for i, scene in enumerate(script["scenes"]):
            try:
                if use_pexels:
                    fallback_q = cfg.pexels_library.get("fallback_query", "abstract cinematic background")
                    img_path = get_pexels_image(
                        image_prompt=scene["image_prompt"],
                        niche=niche,
                        output_dir=images_dir,
                        scene_index=i,
                        fallback_query=fallback_q,
                        used_photo_ids=used_pexels_ids,
                    )
                else:
                    img_row = find_best_image_for_scene(
                        scene["image_prompt"], niche, conn, cfg,
                        exclude_ids=used_library_ids,
                    )
                    img_path = get_library_image(
                        image_prompt=scene["image_prompt"],
                        niche=niche,
                        conn=conn,
                        output_dir=images_dir,
                        scene_index=i,
                        video_id=video_id,
                        cfg=cfg,
                        preselected_row=img_row if img_row else None,
                        used_image_ids=used_library_ids,
                    )
            except (LibraryEmptyError, PexelsError) as e:
                raise RuntimeError(f"Image fetch failed scene {i}: {e}") from e
            scene_image_paths.append(img_path)

        conn.execute("UPDATE videos SET status='bg_ready' WHERE id=?", (video_id,))
        conn.commit()

        # ── Step 3: TTS ──────────────────────────────────────────────────────
        full_narration = "  ".join(s["narration"] for s in script["scenes"])
        audio_dir = str(Path(cfg.paths["audio"]) / run_slug)
        audio_path, audio_dur = synthesize(full_narration, audio_dir, video_id, cfg=cfg, niche=niche)

        word_timings_path = str(Path(audio_dir) / "word_timings.json")
        scene_durations = compute_scene_durations(script["scenes"], word_timings_path, audio_dur)

        conn.execute(
            "UPDATE videos SET status='voice_ready', voice_provider='edge_tts' WHERE id=?",
            (video_id,),
        )
        conn.commit()

        # ── Step 3.5: Background audio (niche config overrides global) ────────
        niche_bg = niche.get("background_audio", {})
        bg_enabled = niche_bg.get("enabled", cfg.background_audio.get("enabled", False))
        bg_audio_path = None
        if bg_enabled:
            from pipeline.audio_bg import fetch_bg_audio
            bg_urls = niche_bg.get("urls") or cfg.background_audio.get("urls", [])
            bg_query = niche_bg.get("query") or cfg.background_audio.get("query", "ambient")
            bg_volume = niche_bg.get("volume", cfg.background_audio.get("volume", 0.20))
            _orig_bg = cfg.background_audio.copy()
            cfg.background_audio["enabled"] = True
            cfg.background_audio["urls"] = bg_urls
            cfg.background_audio["query"] = bg_query
            cfg.background_audio["volume"] = bg_volume
            bg_audio_path = fetch_bg_audio(
                query=bg_query,
                duration_secs=audio_dur,
                cfg=cfg,
            )
            cfg.background_audio.update(_orig_bg)

        # ── Step 4: Assemble ─────────────────────────────────────────────────
        output_path = str(Path(cfg.paths["video"]) / f"{run_slug}.mp4")
        assemble_from_images(
            scene_images=scene_image_paths,
            audio_path=audio_path,
            output_path=output_path,
            scenes=script["scenes"],
            cfg=cfg,
            bg_audio_path=bg_audio_path,
            word_timings_path=word_timings_path,
            scene_durations=scene_durations,
        )

        conn.execute(
            "UPDATE videos SET status='assembled', file_path=? WHERE id=?",
            (output_path, video_id),
        )
        conn.commit()

        # ── Step 5: Send to Telegram ─────────────────────────────────────────
        caption = (
            f"*Retry {new_retry_count}/{MAX_RETRIES}*\n"
            f"*Niche:* {niche['label']}\n"
            f"*Story:* {script['story_title']}\n"
            f"*Feedback used:* {feedback[:100]}"
        )
        send_for_review(video_id=video_id, file_path=output_path, quote_text=caption, conn=conn)
        conn.execute("UPDATE videos SET status='sent' WHERE id=?", (video_id,))
        conn.commit()

        # ── Step 6: Social captions ──────────────────────────────────────────
        try:
            import asyncio
            from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
            from telegram.request import HTTPXRequest

            social_caps = generate_social_captions(script, niche, cfg)
            if social_caps:
                msg_text = format_telegram_message(script["story_title"], social_caps)
                if len(msg_text) > 4000:
                    msg_text = msg_text[:4000] + "\n...(truncated)"
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve:{video_id}"),
                    InlineKeyboardButton("❌ Reject",  callback_data=f"reject:{video_id}"),
                ]])

                async def _send():
                    async with Bot(
                        token=cfg.TELEGRAM_BOT_TOKEN,
                        request=HTTPXRequest(connect_timeout=30, read_timeout=60),
                    ) as bot:
                        await bot.send_message(
                            chat_id=cfg.TELEGRAM_CHAT_ID,
                            text=msg_text,
                            reply_markup=keyboard,
                        )
                asyncio.run(_send())
        except Exception as e:
            log.warning("run_retry: social captions send failed (non-fatal): %s", e)

        log.info("run_retry: done. new video_id=%d", video_id)
        return video_id

    except Exception:
        conn.execute("UPDATE videos SET status='rejected' WHERE id=?", (video_id,) if 'video_id' in dir() else (original_video_id,))
        conn.commit()
        raise
    finally:
        conn.close()
