"""
Eye-exercise Reel generator — independent of the story pipeline.

Usage:
    python run_skillstotraineyes.py                       # seed + family chosen for you
    python run_skillstotraineyes.py --family tracking     # force a drill family
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


def _build_unique(family: str, seed: int, text: dict | None, recent: list[dict]):
    """Re-seed until the drill's key params differ from every recent one."""
    from skillstotraineyes.drills import build, too_close
    same_family = [r for r in recent if r.get("family") == family]
    for bump in range(20):
        drill = build(family, seed + bump, text)
        if not too_close(drill.params, same_family):
            return drill, seed + bump
    log.warning("uniqueness gate exhausted 20 re-seeds for %s; accepting the last", family)
    return drill, seed + 19


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an eye-exercise Reel")
    parser.add_argument("--family", default=None, help="Force a drill family")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--audio-mode", choices=["muted", "voiceover"], default=None)
    parser.add_argument("--dry-run", action="store_true", help="Build + validate only")
    parser.add_argument("--no-publish", action="store_true", help="Render but skip Drive/schedule")
    parser.add_argument("--no-llm", action="store_true", help="Use default wording")
    parser.add_argument("--schedule-time", default=None, help="Force upload time, IST HH:MM")
    args = parser.parse_args()

    from config import cfg
    from db.init_db import init_db
    from skillstotraineyes.drills import BUILDERS, build, pick_family

    niche = _load_niche(cfg)
    if args.family and args.family not in BUILDERS:
        log.error("Unknown family %r. Options: %s", args.family, sorted(BUILDERS))
        sys.exit(1)

    init_db(cfg.paths["db"])
    conn = sqlite3.connect(cfg.paths["db"])
    conn.execute("PRAGMA foreign_keys=ON")

    seed = args.seed if args.seed is not None else int(time.time()) % 1_000_000
    recent = _recent(conn)
    last_family = [recent[0]["family"]] if recent and recent[0].get("family") else []  # newest first
    family = args.family or pick_family(seed, last_family)

    # Wording first, so the gate checks the drill that will actually be rendered.
    base = build(family, seed)
    defaults = {"hook": base.voice[0][1], "question": base.voice[1][1]}
    wording = {}
    if not args.no_llm:
        from skillstotraineyes.wording import generate_wording
        wording = generate_wording(family, defaults, recent, cfg)
    text = {k: wording[k] for k in ("hook", "question") if k in wording}
    drill, seed = _build_unique(family, seed, text or None, recent)

    log.info("Family=%s seed=%d duration=%.1fs params=%s", family, seed, drill.duration, drill.params)
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

    from skillstotraineyes.drills import params_key
    variation = {"seed": seed, "family": family, "params": drill.params,
                 "key": params_key(drill.params), "hook": hook, "question": question,
                 "caption": caption_body, "audio_mode": mode}
    conn.execute(
        "INSERT INTO videos (status, prompt, niche_id, variation_params) VALUES ('queued', ?, ?, ?)",
        (f"[{family}]", NICHE_ID, json.dumps(variation)),
    )
    conn.commit()
    video_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    slug = f"{NICHE_ID}_{family}_{video_id}"
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
                hashtags=[f"#{t}" for t in niche.get("hashtags", [])],
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
