"""
Trial: space_science videos with SD1.5+ESRGAN local image gen.
No Telegram, no upload. Outputs to output/video/.

Run: python scripts/run_space_sd.py
"""
import re, sqlite3, sys, logging, time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv; load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
log = logging.getLogger("run_space_sd")

STORIES = [
    "How black holes bend time and light",
    "The life cycle of a massive star from nebula to supernova",
    "Journey to the edge of the observable universe",
]

# Scene prompts containing these words get 3 progressive images instead of 1.
# Each stage suffix shifts the composition to simulate motion/change.
_PROGRESSIVE_KEYWORDS = (
    "collaps", "shrink", "explod", "supernova", "form", "birth",
    "expand", "grow", "accret", "spiral", "jet", "eject",
)
_STAGE_SUFFIXES = [
    "early stage, beginning of process",
    "mid stage, process underway",
    "final stage, end result",
]


def _is_progressive(prompt: str) -> bool:
    p = prompt.lower()
    return any(kw in p for kw in _PROGRESSIVE_KEYWORDS)


def main():
    from config import cfg
    from pipeline.script_gen import generate_script
    from pipeline.tts import synthesize
    from pipeline.ffmpeg_assembler import assemble_from_images
    from pipeline.local_sd_gen import build_generator, generate_image

    niche = next(n for n in cfg.niches if n["id"] == "space_science")

    # Merge niche caption_style into cfg so assembler picks it up
    if "caption_style" in niche:
        cfg.video.setdefault("caption_style", {}).update(niche["caption_style"])

    conn = sqlite3.connect(ROOT / "output/db/agent.db")

    # Load SD + ESRGAN once for all videos
    pipe, upsampler = build_generator()

    for story in STORIES:
        log.info("=== %s ===", story)

        cur = conn.execute(
            "INSERT INTO videos (niche_id, prompt, status) VALUES (?,?,?)",
            (niche["id"], story, "queued"),
        )
        conn.commit()
        video_id = cur.lastrowid

        slug = f"space_sd_{re.sub(r'[^a-z0-9]+', '-', story.lower())[:40]}_{video_id}"
        images_dir = ROOT / "output/images" / slug
        audio_dir  = ROOT / "output/audio"  / slug
        video_path = ROOT / "output/video"  / f"{slug}.mp4"

        script = generate_script(niche, story, conn, video_id, cfg)
        scenes = script["scenes"]
        log.info("%d scenes", len(scenes))

        # Images — progressive scenes get 3 images (shown at 1/3 duration each)
        image_paths = []
        expanded_scenes = []  # scenes list mirroring image_paths for captions

        for i, scene in enumerate(scenes):
            prompt = scene["image_prompt"]
            if _is_progressive(prompt):
                for j, suffix in enumerate(_STAGE_SUFFIXES):
                    out = str(images_dir / f"scene_{i:02d}_stage{j}.png")
                    t0 = time.time()
                    generate_image(
                        prompt=f"{prompt}, {suffix}",
                        output_path=out,
                        pipe=pipe,
                        upsampler=upsampler,
                        seed=video_id * 1000 + i * 10 + j,
                    )
                    log.info("  scene %d/%d stage %d done (%.1fs)", i+1, len(scenes), j+1, time.time()-t0)
                    image_paths.append(out)
                    expanded_scenes.append(scene)
            else:
                out = str(images_dir / f"scene_{i:02d}.png")
                t0 = time.time()
                generate_image(
                    prompt=prompt,
                    output_path=out,
                    pipe=pipe,
                    upsampler=upsampler,
                    seed=video_id * 1000 + i,
                )
                log.info("  scene %d/%d done (%.1fs)", i+1, len(scenes), time.time()-t0)
                image_paths.append(out)
                expanded_scenes.append(scene)

        conn.execute("UPDATE videos SET status='bg_ready' WHERE id=?", (video_id,))
        conn.commit()

        narration = " ".join(s["narration"] for s in scenes)
        audio_path, duration = synthesize(narration, str(audio_dir), video_id, cfg, niche)
        log.info("Audio: %.1fs", duration)

        conn.execute("UPDATE videos SET status='voice_ready' WHERE id=?", (video_id,))
        conn.commit()

        video_path.parent.mkdir(parents=True, exist_ok=True)
        assemble_from_images(
            image_paths, audio_path, str(video_path),
            scenes=expanded_scenes, cfg=cfg,
        )

        conn.execute(
            "UPDATE videos SET status='assembled', file_path=? WHERE id=?",
            (str(video_path.relative_to(ROOT)), video_id),
        )
        conn.commit()
        log.info("Video: %s", video_path)

    conn.close()
    log.info("All done. Check output/video/")


if __name__ == "__main__":
    main()
