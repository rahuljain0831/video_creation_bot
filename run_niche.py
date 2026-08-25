"""
Niche-driven video entry point.

Usage:
    python run_niche.py                               # interactive niche menu
    python run_niche.py mythology                     # niche id direct
    python run_niche.py mythology "story of Medusa"  # niche + story seed
    python run_niche.py mythology --dry-run           # script only, no images
    python run_niche.py mythology --no-telegram       # assemble but skip Telegram

Pipeline:
    niche selection
      → script_gen: LLM writes 8-15 scene script (narration + image_prompt per scene)
                    logs decisions to DB before image lookup
      → image_library: finds best matching image per scene from user-provided library
      → tts: synthesize combined narration
      → ffmpeg_assembler: Ken Burns each image → concat → captions → audio → 9:16 mp4
      → Telegram: send for review
"""

import argparse
import json
import logging
import re
import sqlite3
import sys
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
        if niche_arg.isdigit():
            idx = int(niche_arg) - 1
            if 0 <= idx < len(niches):
                return niches[idx]
        log.error("Unknown niche id: %r. Available: %s", niche_arg, [n["id"] for n in niches])
        sys.exit(1)

    print("\nSelect a niche:")
    for i, n in enumerate(niches, 1):
        print(f"  {i}. {n['label']} — {n['tone']}")
    while True:
        raw = input("Enter number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(niches):
            return niches[int(raw) - 1]
        print(f"  Enter 1-{len(niches)}")


def _make_slug(niche_id: str, story_title: str, video_id: int) -> str:
    """Build a human-readable run identifier: niche_story-title_id."""
    title = re.sub(r"[^\w\s-]", "", story_title).strip()
    title = re.sub(r"[\s_]+", "-", title).lower()[:40].strip("-")
    return f"{niche_id}_{title}_{video_id}"


def _save_script(
    script: dict,
    niche: dict,
    story_seed: str,
    video_id: int,
    run_slug: str,
    cfg,
) -> None:
    """Save full script + run metadata to output/scripts/ for later reference."""
    scripts_dir = Path(cfg.paths["scripts"])
    scripts_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "video_id":   video_id,
        "niche":      niche,
        "story_seed": story_seed or f"[random {niche['id']}]",
        "script":     script,
    }
    out = scripts_dir / f"{run_slug}.json"
    # encoding is explicit: ensure_ascii=False emits curly quotes and dashes,
    # and Path.write_text would otherwise use the Windows ANSI codepage, making
    # the file unreadable by anything expecting UTF-8 (including this pipeline).
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Script saved: %s", out)


def _fetch_scene_images(
    script: dict,
    niche: dict,
    conn: sqlite3.Connection,
    cfg,
    video_id: int,
    run_slug: str,
    image_source: str,
    is_generated: bool,
) -> list[str] | None:
    """
    Source one image per scene. Returns None when the run should be abandoned —
    the caller marks the video rejected.

    Not reached at all for procedural niches, which have no image stage.
    """
    from pipeline.image_library import get_library_image, LibraryEmptyError
    from pipeline.deity_map import find_best_image_for_scene
    from pipeline.pexels_library import get_pexels_image, PexelsError
    from pipeline.image_gen import build_style_token, generate_image, ImageGenError
    from pipeline.image_policy import LocalGenerationBlocked
    from pipeline.image_post import ensure_render_size, is_worth_cutting_to

    images_dir = str(Path(cfg.paths["images"]) / run_slug)
    render_w, render_h = (cfg.video.get("resolution") or [1080, 1920])[:2]

    # One look for the whole video, one seed per scene derived from the run's.
    style_token = build_style_token(video_id) if is_generated else ""
    if style_token:
        log.info("  Style token: %s", style_token)

    scene_image_paths: list[str] = []
    _used_library_ids: set[int] = set()   # dedup: library image IDs used this video
    _used_pexels_ids: set[int] = set()    # dedup: pexels photo IDs used this video

    for i, scene in enumerate(script["scenes"]):
        log.info("  Image lookup %d/%d...", i + 1, script["scene_count"])
        try:
            if is_generated:
                img_path = generate_image(
                    image_prompt=scene["image_prompt"],
                    niche=niche,
                    output_dir=images_dir,
                    scene_index=i,
                    cfg=cfg,
                    local_only=(image_source == "comfyui"),
                    shot=scene.get("shot", ""),
                    style_token=style_token,
                    seed=video_id * 1000 + i,
                )
            elif image_source == "pexels":
                fallback_q = cfg.pexels_library.get("fallback_query", "abstract cinematic background")
                img_path = get_pexels_image(
                    image_prompt=scene["image_prompt"],
                    niche=niche,
                    output_dir=images_dir,
                    scene_index=i,
                    fallback_query=fallback_q,
                    used_photo_ids=_used_pexels_ids,
                )
            else:
                img_row = find_best_image_for_scene(
                    scene["image_prompt"], niche, conn, cfg,
                    exclude_ids=_used_library_ids,
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
                    used_image_ids=_used_library_ids,
                )
        except LibraryEmptyError as e:
            log.error("Image library empty for scene %d: %s", i, e)
            log.error("Run: python ingest_library.py --folder <path_to_images>")
            return None
        except PexelsError as e:
            log.error("Pexels image fetch failed for scene %d: %s", i, e)
            return None
        except LocalGenerationBlocked as e:
            log.error("Image generation blocked by niche policy for scene %d: %s", i, e)
            return None
        except ImageGenError as e:
            log.error("Image generation failed for scene %d: %s", i, e)
            log.error("Configure providers in image_keys.json (see image_keys.example.json) "
                      "or start ComfyUI for the local fallback.")
            return None

        # Whatever the provider actually returned, the renderer gets the render
        # resolution. Otherwise Remotion upscales a 576-wide source by ~2.2x.
        #
        # Generated images only. Library and Pexels images are already full-size
        # and have their own framing; centre-cropping a curated deity image to
        # 9:16 here would cut the subject out of it.
        if is_generated:
            img_path = ensure_render_size(img_path, render_w, render_h)

        scene_image_paths.append(img_path)
        log.info("  Scene %d → %s", i + 1, img_path)

        # Second image for this beat: gives the mid-beat cut a real cut rather
        # than a crop change. Optional by design — a failure here costs one hard
        # cut, never the run.
        reveal_prompt = scene.get("reveal_prompt", "") if is_generated else ""
        if reveal_prompt:
            try:
                b_path = generate_image(
                    image_prompt=reveal_prompt,
                    niche=niche,
                    output_dir=images_dir,
                    scene_index=i,
                    cfg=cfg,
                    local_only=(image_source == "comfyui"),
                    shot="detail",
                    style_token=style_token,
                    seed=video_id * 1000 + i + 500,
                    name_suffix="_b",
                )
                b_path = ensure_render_size(b_path, render_w, render_h)
                # Vetted, not trusted: this image lands mid-beat on impact,
                # reveal and scare, and an empty one turns the payoff shot into
                # a few seconds of fog. A tighter crop of the primary beats
                # cutting to nothing.
                if is_worth_cutting_to(b_path, img_path):
                    scene["_image_b"] = b_path
                    log.info("  Scene %d reveal → %s", i + 1, b_path)
            except (ImageGenError, LocalGenerationBlocked) as e:
                log.warning("  Scene %d reveal image failed (%s) — single image beat", i + 1, e)

    return scene_image_paths


def _startup_quota_reset(conn: sqlite3.Connection) -> None:
    """Reset LLM quota counters if their daily interval has passed."""
    from pipeline.quota_tracker import load_quota_config, reset_quota_for_provider

    quota_cfg = load_quota_config()
    for provider in quota_cfg["providers"]:
        reset_quota_for_provider(provider, conn)


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
                        help="Stop after script generation (no images, no video)")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Skip Telegram send (video still assembled)")
    parser.add_argument("--myth-type", default=None,
                        choices=["hindu", "norse", "egypt", "greek"],
                        help="Mythology sub-type (only for mythology niche)")
    parser.add_argument("--content-type", default="story",
                        choices=["story", "teachings", "facts"],
                        help="Content format: story (default), teachings, or facts")
    args = parser.parse_args()

    # ── Load config ───────────────────────────────────────────────────────────
    from config import cfg

    niches = [n for n in cfg.niches if n.get("enabled", True)]
    if not niches:
        log.error("No enabled niches in settings.json")
        sys.exit(1)

    niche        = _select_niche(niches, args.niche)
    story_seed   = args.story_seed.strip()
    myth_type    = args.myth_type if niche["id"] == "mythology" else None
    content_type = args.content_type

    # Apply mythology sub-type art style override
    if myth_type and niche["id"] == "mythology":
        sub = niche.get("sub_types", {}).get(myth_type)
        if sub:
            niche = dict(niche)
            niche["art_style_prompt_suffix"] = sub["art_style_prompt_suffix"]
            niche["tone"]  = sub.get("tone", niche["tone"])
            niche["label"] = sub.get("label", niche["label"])

    if not story_seed and not args.niche:
        story_seed = input(f"Story seed for {niche['label']} (or Enter for random): ").strip()

    log.info("=" * 60)
    log.info("Niche: %s  |  Tone: %s", niche["label"], niche["tone"])
    if myth_type:
        log.info("Myth type: %s  |  Content: %s", myth_type, content_type)
    log.info("Seed:  %s", story_seed or "[random]")
    log.info("=" * 60)

    # ── Init DB ───────────────────────────────────────────────────────────────
    from db.init_db import init_db
    init_db(cfg.paths["db"])
    conn = sqlite3.connect(cfg.paths["db"])
    conn.execute("PRAGMA foreign_keys=ON")

    # ── Startup: reset LLM quotas ─────────────────────────────────────────────
    _startup_quota_reset(conn)

    # ── Insert video row ──────────────────────────────────────────────────────
    conn.execute(
        "INSERT INTO videos (status, prompt, niche_id) VALUES ('queued', ?, ?)",
        (story_seed or f"[random {niche['id']}]", niche["id"]),
    )
    conn.commit()
    video_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    log.info("Created video row: id=%d", video_id)

    try:
        # ── Step 1: Generate script ───────────────────────────────────────────
        log.info("[1/5] Generating script...")
        from pipeline.script_gen import generate_script
        script = generate_script(
            story_seed=story_seed, niche=niche, conn=conn,
            video_id=video_id, cfg=cfg, myth_type=myth_type, content_type=content_type,
        )

        log.info("Script: title=%r scenes=%d", script["story_title"], script["scene_count"])
        for i, scene in enumerate(script["scenes"], 1):
            log.info("  Scene %d narration : %s", i, scene["narration"])
            if "visual" in scene:
                log.info("  Scene %d visual     : %s %s",
                         i, scene["visual"], (scene.get("accent") or "").strip())
            else:
                log.info("  Scene %d image_prompt: %s", i, scene.get("image_prompt", "")[:80])

        run_slug = _make_slug(niche["id"], script["story_title"], video_id)
        log.info("Run slug: %s", run_slug)

        _save_script(script, niche, story_seed, video_id, run_slug, cfg)

        if args.dry_run:
            log.info("--dry-run: stopping after script. Decisions logged to DB.")
            _print_decisions(conn, video_id)
            conn.execute("UPDATE videos SET status='screened' WHERE id=?", (video_id,))
            conn.commit()
            return

        # ── Resolve image source (policy-checked) ────────────────────────────
        from pipeline.image_policy import (
            GENERATED_SOURCES, is_procedural, resolve_image_source,
        )
        _procedural   = is_procedural(niche)
        _image_source = resolve_image_source(niche)
        _is_generated = (not _procedural) and _image_source in GENERATED_SOURCES

        if _procedural:
            # The renderer draws every frame. No image stage at all.
            log.info("[2/5] Procedural visuals (renderer=%s) — skipping image stage.",
                     niche.get("renderer", "ffmpeg"))
            scene_image_paths = []
        else:
            # ── Step 1.5: Refine image prompts (optional) ────────────────────
            if cfg.prompt_refiner.get("enabled", False):
                log.info("[1.5/5] Refining image prompts...")
                from pipeline.prompt_refiner import refine_image_prompts
                _refine_target = "generation" if _is_generated else "search"
                script["scenes"] = refine_image_prompts(
                    script["scenes"], niche, target=_refine_target, cfg=cfg,
                )

            # ── Step 2: Fetch images ─────────────────────────────────────────
            log.info("[2/5] Fetching scene images (source=%s)...", _image_source)
            scene_image_paths = _fetch_scene_images(
                script=script, niche=niche, conn=conn, cfg=cfg,
                video_id=video_id, run_slug=run_slug,
                image_source=_image_source, is_generated=_is_generated,
            )
            if scene_image_paths is None:
                conn.execute("UPDATE videos SET status='rejected' WHERE id=?", (video_id,))
                conn.commit()
                return

        # Status write stays outside the branch — queries elsewhere filter on the
        # full queued → bg_ready → voice_ready → assembled → sent lifecycle.
        conn.execute("UPDATE videos SET status='bg_ready' WHERE id=?", (video_id,))
        conn.commit()

        # ── Step 3: TTS ───────────────────────────────────────────────────────
        log.info("[3/5] Synthesizing narration...")
        from pipeline.tts import synthesize, synthesize_scenes

        audio_dir = str(Path(cfg.paths["audio"]) / run_slug)

        # Schema-driven: the scary templates carry a per-beat delivery, so those
        # scenes are synthesized one at a time. Everything else keeps the
        # single-pass path it has always used.
        if script.get("script_schema") == "cinematic_scary":
            audio_path, audio_dur, exact_durations = synthesize_scenes(
                script["scenes"], audio_dir, video_id, cfg=cfg, niche=niche,
            )
        else:
            full_narration = "  ".join(s["narration"] for s in script["scenes"])
            audio_path, audio_dur = synthesize(
                full_narration, audio_dir, video_id, cfg=cfg, niche=niche,
            )
            exact_durations = []
        log.info("TTS done: %s (%.2fs)", audio_path, audio_dur)

        word_timings_path = str(Path(audio_dir) / "word_timings.json")

        if exact_durations:
            # Measured per beat, so there is nothing to infer.
            scene_durations = exact_durations
        else:
            from pipeline.scene_timing import compute_scene_durations
            scene_durations = compute_scene_durations(
                script["scenes"], word_timings_path, audio_dur,
            )
        log.info("Scene durations: %s", [f"{d:.2f}s" for d in scene_durations])

        conn.execute(
            "UPDATE videos SET status='voice_ready', voice_provider='edge_tts' WHERE id=?",
            (video_id,),
        )
        conn.commit()

        # ── Step 4: Assemble video ────────────────────────────────────────────
        log.info("[4/5] Assembling video (renderer=%s)...", niche.get("renderer", "ffmpeg"))
        from pipeline.renderer_dispatch import get_assembler

        assemble = get_assembler(
            niche, cfg, title=script["story_title"], seed=video_id,
        )
        output_path = str(Path(cfg.paths["video"]) / f"{run_slug}.mp4")
        assemble(
            scene_images=scene_image_paths,
            audio_path=audio_path,
            output_path=output_path,
            scenes=script["scenes"],
            cfg=cfg,
            word_timings_path=word_timings_path,
            scene_durations=scene_durations,
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

            # Send per-platform social captions as follow-up message with Approve/Reject buttons
            try:
                from pipeline.social_captions import generate_social_captions, format_telegram_message
                import asyncio
                from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
                from telegram.request import HTTPXRequest

                log.info("[5/5] Generating social captions...")
                social_caps = generate_social_captions(script, niche, cfg)
                if social_caps:
                    msg_text = format_telegram_message(script["story_title"], social_caps)
                    if len(msg_text) > 4000:
                        msg_text = msg_text[:4000] + "\n...(truncated)"
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Approve", callback_data=f"approve:{video_id}"),
                        InlineKeyboardButton("❌ Reject",  callback_data=f"reject:{video_id}"),
                    ]])
                    async def _send_captions():
                        async with Bot(
                            token=cfg.TELEGRAM_BOT_TOKEN,
                            request=HTTPXRequest(connect_timeout=30, read_timeout=60),
                        ) as bot:
                            await bot.send_message(
                                chat_id=cfg.TELEGRAM_CHAT_ID,
                                text=msg_text,
                                reply_markup=keyboard,
                            )
                    asyncio.run(_send_captions())
                    log.info("Social captions sent to Telegram with Approve/Reject buttons.")
                else:
                    log.warning("Social captions empty — skipping follow-up message.")
            except Exception as e:
                log.warning("Social captions send failed (non-fatal): %s", e)

        log.info("=" * 60)
        log.info("Done. video_id=%d  file=%s", video_id, output_path)
        log.info("=" * 60)

    except Exception as e:
        log.error("Pipeline failed at video_id=%d: %s", video_id, e, exc_info=True)
        conn.execute("UPDATE videos SET status='rejected' WHERE id=?", (video_id,))
        conn.commit()
        sys.exit(1)
    finally:
        # Runs on success and on failure — a run that burned quota then died is
        # exactly when you want to see the numbers. Never let reporting mask the
        # real exception.
        try:
            from pipeline.quota_tracker import format_quota_report
            report = format_quota_report(conn)
            if report:
                log.info("\n%s", report)
        except Exception as e:
            log.warning("Quota report failed (non-fatal): %s", e)
        conn.close()


if __name__ == "__main__":
    main()
