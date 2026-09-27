"""
Eye-exercise Reel generator — independent of the story pipeline.

Usage:
    python run_skillstotraineyes.py                       # seed + family chosen for you
    python run_skillstotraineyes.py --family tracking     # force a drill family
    python run_skillstotraineyes.py --level god           # force a difficulty level
    python run_skillstotraineyes.py --mode image          # hidden-object feed post
    python run_skillstotraineyes.py --seed 42             # reproducible
    python run_skillstotraineyes.py --dry-run             # build + validate the drill only
    python run_skillstotraineyes.py --no-publish          # render, skip Drive/schedule
    python run_skillstotraineyes.py --no-llm              # default wording, no LLM call

Pipeline:
    pick family (never the same twice in a row) -> build drill (seeded, physics-validated)
    -> uniqueness gate -> wording (LLM, validated) -> audio bed (+ voiceover)
    -> render -> videos row -> pipeline.publisher.publish() to Instagram only.
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("run_skillstotraineyes")

NICHE_ID = "skillstotraineyes"
OUT_ROOT = Path("output") / NICHE_ID
RECENT_N = 20


def _load_niche(cfg) -> dict:
    # Looked up directly: the niche is enabled:false so run_niche.py never lists it.
    niche = next((n for n in cfg.niches if n["id"] == NICHE_ID), None)
    if niche is None:
        log.error("Niche %r missing from settings.json", NICHE_ID)
        sys.exit(1)
    return niche


def _recent(conn: sqlite3.Connection, n: int = RECENT_N) -> list[dict]:
    """variation_params of this niche's latest videos, newest first."""
    rows = conn.execute(
        "SELECT variation_params FROM videos WHERE niche_id=? AND variation_params IS NOT NULL "
        "ORDER BY id DESC LIMIT ?", (NICHE_ID, n),
    ).fetchall()
    out = []
    for (raw,) in rows:
        try:
            out.append(json.loads(raw))
        except (TypeError, json.JSONDecodeError):
            continue
    return out


def _only_drills(recent: list[dict]) -> list[dict]:
    """Old rows carry no `kind`; they are drills."""
    return [r for r in recent if r.get("kind") != "image"]


def _only_images(recent: list[dict]) -> list[dict]:
    return [r for r in recent if r.get("kind") == "image"]


def _build_unique(family: str, seed: int, text: dict | None, recent: list[dict],
                  level: str | None = None, allow_preset: bool = False):
    """Re-seed until the drill's key params differ from every recent one."""
    from skillstotraineyes.drills import build, too_close
    same_family = [r for r in recent if (r.get("drill_id") or r.get("family")) == family]
    for bump in range(20):
        drill = build(family, seed + bump, text, level=level, allow_preset=allow_preset)
        if not too_close(drill.params, same_family):
            return drill, seed + bump
    log.warning("uniqueness gate exhausted 20 re-seeds for %s; accepting the last", family)
    return drill, seed + 19


def _variation(seed, family, drill, hook, question, caption_body, mode) -> dict:
    from skillstotraineyes.drills import params_key
    return {"seed": seed, "family": family, "drill_id": family, "level": drill.level,
            "params": drill.params, "key": params_key(drill.params),
            "hook": hook, "question": question,
            "caption": caption_body, "audio_mode": mode}


def _hashtags(bank_id: str, seed: int) -> dict[str, list[str]]:
    """Per-platform picks; schedule_video() takes the one for the platform it rotates to."""
    from pipeline.social_captions import pick_hashtags
    return {p: pick_hashtags(bank_id, p, seed) for p in ("instagram", "facebook")}


def _make_audio(drill, niche: dict, mode: str, seed: int, video_id: int, cfg):
    """Returns (bed_path, [(start_s, voice_path)])."""
    from pipeline.horror_audio import build_ambience

    audio_dir = OUT_ROOT / "audio" / str(video_id)
    audio_dir.mkdir(parents=True, exist_ok=True)
    bed, bed_name = build_ambience(
        drill.duration, audio_dir / "bed.wav", seed=seed,
        bed_pool=tuple(niche.get("bed_pool") or ()) or None,
    )
    voice: list[tuple[float, str]] = []
    if mode == "voiceover":
        from pipeline.tts import synthesize
        for i, (start, line) in enumerate(drill.voice):
            path, _dur = synthesize(line, str(audio_dir / f"voice_{i}"), video_id, cfg=cfg, niche=niche)
            voice.append((start, path))
    log.info("Audio: mode=%s bed=%s voice_lines=%d", mode, bed_name, len(voice))
    return bed, voice


def _run_image_post(args, cfg, niche, conn) -> None:
    """Generate and publish one hidden-object feed image. No drill, no render."""
    from skillstotraineyes.hidden_object import HiddenObjectError, build_prompt, generate, invent_scene
    from skillstotraineyes.wording import hidden_object_caption

    seed = args.seed if args.seed is not None else int(time.time()) % 1_000_000
    recent = _only_images(_recent(conn))
    scene = invent_scene(seed, recent, cfg, use_llm=not args.no_llm)
    log.info("Hidden object: %s in %s", scene["target"], scene["environment"])

    if args.dry_run:
        log.info("--dry-run: prompt would be: %s", build_prompt(scene))
        return

    caption_body = (f"Somewhere in this picture: {scene['target']}. Can you find it?"
                    if args.no_llm else hidden_object_caption(scene, recent, cfg))
    caption = f"{caption_body}\n\n{niche['disclaimer']}"
    variation = {"seed": seed, "kind": "image", "target": scene["target"],
                 "environment": scene["environment"], "caption": caption_body}
    conn.execute(
        "INSERT INTO videos (status, prompt, niche_id, variation_params) "
        "VALUES ('queued', ?, ?, ?)",
        (f"[hidden_object] {scene['target']}", NICHE_ID, json.dumps(variation)),
    )
    conn.commit()
    video_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    try:
        out_dir = OUT_ROOT / "images"
        image_path = generate(scene, out_dir, seed, niche, cfg)
        conn.execute("UPDATE videos SET status='assembled', file_path=? WHERE id=?",
                     (image_path, video_id))
        conn.commit()

        if args.no_publish:
            log.info("--no-publish: image left at %s", image_path)
            return
        from pipeline.publisher import publish
        publish(
            video_id, image_path, niche, conn, cfg,
            platforms=["instagram"], schedule_time=args.schedule_time,
            title=f"Find the {scene['target']}", caption=caption,
            hashtags=_hashtags("skillstotraineyes_hidden", seed),
            media_type="image",
            notify_text=f"{niche['label']}: hidden object ({scene['target']}), scheduled",
        )
        log.info("Done. video_id=%d image=%s", video_id, image_path)
    except Exception as e:
        # Expected failures (an unusable provider result) log plainly; anything else keeps its traceback.
        from pipeline.image_gen import ImageGenError
        expected = isinstance(e, (HiddenObjectError, ImageGenError))
        log.error("Image post failed at video_id=%d: %s", video_id, e, exc_info=not expected)
        conn.execute("UPDATE videos SET status='rejected' WHERE id=?", (video_id,))
        conn.commit()
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an eye-exercise Reel")
    parser.add_argument("--family", default=None, help="Force a drill family")
    parser.add_argument("--level", default=None,
                        help="Force a difficulty level (easy/medium/hard/expert/god)")
    parser.add_argument("--mode", choices=["drill", "image"], default="drill",
                        help="drill: a Reel. image: a hidden-object feed post.")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--audio-mode", choices=["muted", "voiceover"], default=None)
    parser.add_argument("--dry-run", action="store_true", help="Build + validate only")
    parser.add_argument("--no-publish", action="store_true", help="Render but skip Drive/schedule")
    parser.add_argument("--no-llm", action="store_true", help="Use default wording")
    parser.add_argument("--schedule-time", default=None, help="Force upload time, IST HH:MM")
    args = parser.parse_args()

    from config import cfg
    from db.init_db import init_db
    from skillstotraineyes.difficulty import LEVELS, LEVEL_LABEL, pick_level
    from skillstotraineyes.drills import FAMILIES, build, entry, pick_family

    niche = _load_niche(cfg)
    if args.family and args.family not in FAMILIES:
        log.error("Unknown drill %r. Options: %s", args.family, sorted(FAMILIES))
        sys.exit(1)
    if args.level and args.level not in LEVELS:
        log.error("Unknown level %r. Options: %s", args.level, list(LEVELS))
        sys.exit(1)

    init_db(cfg.paths["db"])
    conn = sqlite3.connect(cfg.paths["db"])
    conn.execute("PRAGMA foreign_keys=ON")
    if args.mode == "image":
        try:
            _run_image_post(args, cfg, niche, conn)
        finally:
            conn.close()
        return

    seed = args.seed if args.seed is not None else int(time.time()) % 1_000_000
    recent = _only_drills(_recent(conn))
    if args.family:
        family = args.family
    elif args.no_llm:
        prev = (recent[0].get("drill_id") or recent[0].get("family")) if recent else None
        family = pick_family(seed, [prev] if prev else [])
    else:
        from skillstotraineyes.wording import pick_drill
        family = pick_drill(seed, recent, cfg)

    recent_levels = [r["level"] for r in recent if r.get("level")]
    level = args.level or pick_level(seed, recent_levels)

    # Wording first, so the gate checks the drill that will actually be rendered.
    e = entry(family)
    allow_preset = args.level is None
    base = build(family, seed, level=level, allow_preset=allow_preset)
    defaults = {"hook": base.voice[0][1], "question": base.voice[1][1],
                "name": e["name"], "about": e["about"], "level_label": LEVEL_LABEL[level]}
    wording = {}
    if not args.no_llm:
        from skillstotraineyes.wording import generate_wording
        wording = generate_wording(family, defaults, recent, cfg)
    text = {k: wording[k] for k in ("hook", "question") if k in wording}
    drill, seed = _build_unique(family, seed, text or None, recent, level, allow_preset)

    level = drill.level  # a preset may have replaced the picked level
    log.info("Drill=%s level=%s seed=%d duration=%.1fs params=%s",
             family, level, seed, drill.duration, drill.params)
    if args.dry_run:
        log.info("--dry-run: stopping before render. Wording=%s", wording or "defaults")
        conn.close()
        return

    modes = niche.get("audio_modes") or ["muted"]
    mode = args.audio_mode or modes[seed % len(modes)]
    hook, question = drill.voice[0][1], drill.voice[1][1]
    if "caption" in wording:
        caption_body = wording["caption"]
    else:
        from skillstotraineyes.wording import FALLBACK_CAPTIONS
        caption_body = FALLBACK_CAPTIONS[seed % len(FALLBACK_CAPTIONS)]
    caption = f"{caption_body}\n\n{niche['disclaimer']}"

    variation = _variation(seed, family, drill, hook, question, caption_body, mode)
    conn.execute(
        "INSERT INTO videos (status, prompt, niche_id, variation_params) VALUES ('queued', ?, ?, ?)",
        (f"[{family}]", NICHE_ID, json.dumps(variation)),
    )
    conn.commit()
    video_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    slug = f"{NICHE_ID}_{family}_{level}_{video_id}"
    log.info("Created video row: id=%d slug=%s", video_id, slug)

    try:
        specs = OUT_ROOT / "specs"
        specs.mkdir(parents=True, exist_ok=True)
        (specs / f"{slug}.json").write_text(
            json.dumps({**variation, "duration": drill.duration}, indent=2), encoding="utf-8")
        conn.execute("UPDATE videos SET status='bg_ready' WHERE id=?", (video_id,))
        conn.commit()

        bed, voice = _make_audio(drill, niche, mode, seed, video_id, cfg)
        conn.execute("UPDATE videos SET status='voice_ready', voice_provider=? WHERE id=?",
                     ("edge_tts" if voice else "none", video_id))
        conn.commit()

        from skillstotraineyes.renderer import render
        output_path = str(OUT_ROOT / "video" / f"{slug}.mp4")
        t0 = time.time()
        render(drill, output_path, bed=bed, voice=voice)
        log.info("Rendered %s in %.0fs", output_path, time.time() - t0)
        conn.execute("UPDATE videos SET status='assembled', file_path=? WHERE id=?",
                     (output_path, video_id))
        conn.commit()

        if args.no_publish:
            log.info("--no-publish: skipping Drive/schedule. Final video: %s", output_path)
        else:
            from pipeline.publisher import publish
            platforms = niche.get("publish_platforms") or ["instagram"]
            publish(
                video_id, output_path, niche, conn, cfg,
                platforms=platforms, schedule_time=args.schedule_time,
                title=hook, caption=caption,
                hashtags=_hashtags("skillstotraineyes", seed),
                notify_text=f"{niche['label']}: {family} ({drill.duration:.0f}s), "
                            f"scheduled on {', '.join(platforms)}",
            )
        log.info("Done. video_id=%d  file=%s", video_id, output_path)
    except Exception as e:
        log.error("Pipeline failed at video_id=%d: %s", video_id, e, exc_info=True)
        conn.execute("UPDATE videos SET status='rejected' WHERE id=?", (video_id,))
        conn.commit()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
