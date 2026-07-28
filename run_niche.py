"""
Niche-driven video entry point — design-v3 pivot.

Usage:
    python run_niche.py                               # interactive niche menu
    python run_niche.py mythology                     # niche id direct
    python run_niche.py mythology "story of Medusa"  # niche + story seed
    python run_niche.py mythology --dry-run           # script only, no image gen
    python run_niche.py mythology --no-telegram       # assemble but skip Telegram

Pipeline:
    niche selection
      → script_gen: LLM writes 8-15 scene script (narration + image_prompt per scene)
                    logs decisions to DB BEFORE image gen
      → image_gen: per-scene AI image (HF → Google AI Studio → Pollinations)
      → tts: synthesize combined narration
      → ffmpeg_assembler: Ken Burns each image → concat → captions → audio → 9:16 mp4
      → Telegram: send for review
"""

import argparse
import asyncio
import json
import logging
import random
import sqlite3
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("run_niche")


def _select_niche(niches: list[dict], niche_arg: str | None) -> dict:
    """Return niche dict from arg or interactive menu."""
    if niche_arg:
        match = next((n for n in niches if n["id"] == niche_arg), None)
        if match:
            return match
        # Maybe they passed a number
        if niche_arg.isdigit():
            idx = int(niche_arg) - 1
            if 0 <= idx < len(niches):
                return niches[idx]
        log.error("Unknown niche id: %r. Available: %s", niche_arg, [n["id"] for n in niches])
        sys.exit(1)

    # Interactive
    print("\nSelect a niche:")
    for i, n in enumerate(niches, 1):
        print(f"  {i}. {n['label']} — {n['tone']}")
    while True:
        raw = input("Enter number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(niches):
            return niches[int(raw) - 1]
        print(f"  Enter 1-{len(niches)}")


def _pick_seed(cfg) -> int:
    """Pick one fixed seed for all scenes in this video run."""
    pool_size = 50
    try:
        pool_size = cfg.image_provider.get("seed_pool_size_per_niche", 50)
    except Exception:
        pass
    return random.randint(0, pool_size - 1)


def _save_script(
    script: dict,
    niche: dict,
    story_seed: str,
    image_seed: int,
    video_id: int,
    cfg,
) -> None:
    """Save full script + run metadata to output/scripts/ for later reference."""
    scripts_dir = Path(cfg.paths["scripts"])
    scripts_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "video_id":   video_id,
        "niche":      niche,
        "story_seed": story_seed or f"[random {niche['id']}]",
        "image_seed": image_seed,
        "script":     script,
    }
    out = scripts_dir / f"script_{video_id}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    log.info("Script saved: %s", out)


def _startup_quota_reset_and_retry(conn: sqlite3.Connection, cfg) -> None:
    """
    Run at startup before generating new videos:
    1. Reset quota for any provider whose interval has passed.
    2. Retry any videos stuck in 'waiting_quota' state.

    This ensures quota resets even on days when no new video is generated,
    and recovers waiting videos automatically after reset.
    """
    from pipeline.quota_tracker import load_quota_config, reset_quota_for_provider

    quota_cfg = load_quota_config()
    for provider in quota_cfg["providers"]:
        reset_quota_for_provider(provider, conn)

    waiting = conn.execute(
        "SELECT id, prompt, niche_id FROM videos WHERE status='waiting_quota'"
    ).fetchall()

    if not waiting:
        return

    log.info("Retrying %d waiting_quota video(s)...", len(waiting))

    from pipeline.image_gen import generate_scene_image, QuotaExhaustedError
    import json

    for video_id, prompt, niche_id in waiting:
        # Load saved script if available
        scripts_dir = cfg.paths["scripts"]
        script_path = (cfg.paths["scripts"] and
                       __import__("pathlib").Path(scripts_dir) / f"script_{video_id}.json")

        if not (script_path and script_path.exists()):
            log.warning("Retry: no saved script for video_id=%d — skipping", video_id)
            continue

        script_data = json.loads(script_path.read_text())
        script = script_data.get("script", {})
        niche  = script_data.get("niche", {})
        image_seed = script_data.get("image_seed", 0)

        images_dir = cfg.paths["images"]
        scene_image_paths: list[str] = []
        first_image_path: str | None = None
        quota_hit = False

        try:
            for i, scene in enumerate(script.get("scenes", [])):
                img_path = generate_scene_image(
                    image_prompt=scene["image_prompt"],
                    art_style_suffix=niche.get("art_style_prompt_suffix", ""),
                    seed=image_seed,
                    output_dir=images_dir,
                    scene_index=i,
                    video_id=video_id,
                    cfg=cfg,
                    conn=conn,
                    reference_image_path=first_image_path,
                )
                scene_image_paths.append(img_path)
                if first_image_path is None:
                    first_image_path = img_path
        except QuotaExhaustedError:
            log.info("Retry video_id=%d: still quota-capped — staying in waiting_quota", video_id)
            quota_hit = True
        except Exception as e:
            log.warning("Retry video_id=%d: image gen failed: %s", video_id, e)
            quota_hit = True

        if not quota_hit and scene_image_paths:
            conn.execute("UPDATE videos SET status='bg_ready' WHERE id=?", (video_id,))
            conn.commit()
            log.info("Retry video_id=%d: recovered → bg_ready", video_id)


def _print_decisions(conn: sqlite3.Connection, video_id: int) -> None:
    rows = conn.execute(
        "SELECT decision_point, chosen_option, reasoning FROM decisions WHERE video_id=? ORDER BY id",
        (video_id,),
    ).fetchall()
    log.info("Decisions for video_id=%d:", video_id)
    for point, option, reasoning in rows:
        log.info("  [%s] %s — %s", point, option, (reasoning or "")[:120])


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a niche story video.")
    parser.add_argument("niche", nargs="?", help="Niche id (e.g. mythology)")
    parser.add_argument("story_seed", nargs="?", default="",
                        help="One-line story seed (optional)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Stop after script generation (no image gen, no video)")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Skip Telegram send (video still assembled)")
    args = parser.parse_args()

    # ── Load config ───────────────────────────────────────────────────────────
    from config import cfg

    niches = cfg.niches
    if not niches:
        log.error("No niches defined in settings.json")
        sys.exit(1)

    niche     = _select_niche(niches, args.niche)
    story_seed = args.story_seed.strip()

    if not story_seed and not args.niche:
        story_seed = input(f"Story seed for {niche['label']} (or Enter for random): ").strip()

    log.info("=" * 60)
    log.info("Niche: %s  |  Tone: %s", niche["label"], niche["tone"])
    log.info("Seed:  %s", story_seed or "[random]")
    log.info("=" * 60)

    # ── Init DB ───────────────────────────────────────────────────────────────
    from db.init_db import init_db
    init_db(cfg.paths["db"])
    conn = sqlite3.connect(cfg.paths["db"])
    conn.execute("PRAGMA foreign_keys=ON")

    # ── Startup: reset quotas + retry waiting_quota videos ───────────────────
    _startup_quota_reset_and_retry(conn, cfg)

    # ── Insert video row ──────────────────────────────────────────────────────
    conn.execute(
        "INSERT INTO videos (status, prompt, niche_id) VALUES ('queued', ?, ?)",
        (story_seed or f"[random {niche['id']}]", niche["id"]),
    )
    conn.commit()
    video_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    log.info("Created video row: id=%d", video_id)

    # One fixed seed for style consistency across all scenes in this video
    image_seed = _pick_seed(cfg)
    log.info("Image seed: %d", image_seed)

    try:
        # ── Step 1: Generate script ───────────────────────────────────────────
        log.info("[1/5] Generating script...")
        from pipeline.script_gen import generate_script
        script = generate_script(story_seed=story_seed, niche=niche, conn=conn, video_id=video_id, cfg=cfg)

        log.info("Script: title=%r scenes=%d", script["story_title"], script["scene_count"])
        for i, scene in enumerate(script["scenes"], 1):
            log.info("  Scene %d narration : %s", i, scene["narration"])
            log.info("  Scene %d image_prompt: %s", i, scene["image_prompt"][:80])

        # Save script JSON regardless of dry-run
        _save_script(script, niche, story_seed, image_seed, video_id, cfg)

        if args.dry_run:
            log.info("--dry-run: stopping after script. Decisions logged to DB.")
            _print_decisions(conn, video_id)
            conn.execute("UPDATE videos SET status='screened' WHERE id=?", (video_id,))
            conn.commit()
            return

        # ── Step 2: Generate images ───────────────────────────────────────────
        log.info("[2/5] Generating scene images...")
        from pipeline.image_gen import generate_scene_image, QuotaExhaustedError

        images_dir = cfg.paths["images"]
        scene_image_paths: list[str] = []
        first_image_path: str | None = None

        try:
            for i, scene in enumerate(script["scenes"]):
                log.info("  Generating image %d/%d...", i + 1, script["scene_count"])
                img_path = generate_scene_image(
                    image_prompt=scene["image_prompt"],
                    art_style_suffix=niche.get("art_style_prompt_suffix", ""),
                    seed=image_seed,
                    output_dir=images_dir,
                    scene_index=i,
                    video_id=video_id,
                    cfg=cfg,
                    conn=conn,
                    reference_image_path=first_image_path,
                )
                scene_image_paths.append(img_path)
                if first_image_path is None:
                    first_image_path = img_path
                log.info("  Image %d saved: %s", i + 1, img_path)
        except QuotaExhaustedError as e:
            log.warning("All image providers quota-capped: %s", e)
            conn.execute("UPDATE videos SET status='waiting_quota' WHERE id=?", (video_id,))
            conn.commit()
            log.info("Video %d → waiting_quota (will retry tomorrow after quota reset)", video_id)
            return

        conn.execute("UPDATE videos SET status='bg_ready' WHERE id=?", (video_id,))
        conn.commit()

        # ── Step 3: TTS ───────────────────────────────────────────────────────
        log.info("[3/5] Synthesizing narration...")
        from pipeline.tts import synthesize

        full_narration = "  ".join(s["narration"] for s in script["scenes"])
        audio_path, audio_dur = synthesize(full_narration, cfg.paths["audio"], video_id, cfg=cfg)
        log.info("TTS done: %s (%.2fs)", audio_path, audio_dur)

        conn.execute(
            "UPDATE videos SET status='voice_ready', voice_provider='edge_tts' WHERE id=?",
            (video_id,),
        )
        conn.commit()

        # ── Step 3.5: Background audio ────────────────────────────────────────
        bg_audio_path = None
        if not args.dry_run and cfg.background_audio.get("enabled", False):
            log.info("[3.5/5] Fetching background audio...")
            from pipeline.audio_bg import fetch_bg_audio
            bg_audio_path = fetch_bg_audio(
                query=cfg.background_audio.get("query", "chanting meditation"),
                duration_secs=audio_dur,
                cfg=cfg,
            )
            if bg_audio_path:
                log.info("Background audio: %s", bg_audio_path)
            else:
                log.info("Background audio unavailable — continuing without it")

        # ── Step 4: Assemble video ────────────────────────────────────────────
        log.info("[4/5] Assembling video...")
        from pipeline.ffmpeg_assembler import assemble_from_images

        output_path = str(Path(cfg.paths["video"]) / f"video_{video_id}_{int(time.time())}.mp4")
        assemble_from_images(
            scene_images=scene_image_paths,
            audio_path=audio_path,
            output_path=output_path,
            scenes=script["scenes"],
            cfg=cfg,
            bg_audio_path=bg_audio_path,
        )
        log.info("Video assembled: %s", output_path)

        conn.execute(
            "UPDATE videos SET status='assembled', file_path=? WHERE id=?",
            (output_path, video_id),
        )
        conn.commit()

        # ── Step 5: Telegram ──────────────────────────────────────────────────
        if args.no_telegram:
            log.info("[5/5] --no-telegram: skipping.")
            log.info("Final video: %s", output_path)
        else:
            log.info("[5/5] Sending to Telegram...")
            caption = (
                f"*Niche:* {niche['label']}\n"
                f"*Story:* {script['story_title']}\n"
                f"*Scenes:* {script['scene_count']}"
            )
            from review.telegram_bot import send_for_review
            send_for_review(
                video_id=video_id,
                file_path=output_path,
                quote_text=caption,
                conn=conn,
            )
            conn.execute("UPDATE videos SET status='sent' WHERE id=?", (video_id,))
            conn.commit()
            log.info("Sent to Telegram.")

        log.info("=" * 60)
        log.info("Done. video_id=%d  file=%s", video_id, output_path)
        log.info("=" * 60)

    except Exception as e:
        log.error("Pipeline failed at video_id=%d: %s", video_id, e, exc_info=True)
        conn.execute("UPDATE videos SET status='rejected' WHERE id=?", (video_id,))
        conn.commit()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
