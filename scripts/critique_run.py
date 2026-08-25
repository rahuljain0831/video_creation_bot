"""
Audit the images of a finished run with the local vision model.

Runs *after* the fact, on purpose. The pipeline accepts whatever the generator
returns and gets on with building the video; this looks at the results
afterwards and writes down what to fix, so the next run's prompts start better.
Nothing here blocks a video, and skipping it costs only the learning.

    python scripts/critique_run.py --latest
    python scripts/critique_run.py --slug scary_stories_the-inside-lock_135
    python scripts/critique_run.py --latest --regenerate
    python scripts/critique_run.py --notes

`--regenerate` re-generates only the scenes whose subject the model says is
missing, through the normal free-tier provider chain. Re-render afterwards with:

    python scripts/test_remotion_render.py --slug <slug>
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import cfg  # noqa: E402
from pipeline.image_critic import critique_run  # noqa: E402
from pipeline.prompt_notes import summary  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("critique_run")

_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _latest_slug() -> str:
    scripts_dir = Path(cfg.paths["scripts"])
    saved = sorted(scripts_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not saved:
        raise SystemExit(f"No saved scripts in {scripts_dir}")
    return saved[-1].stem


def _load(slug: str) -> tuple[dict, dict, list[str]]:
    script_path = Path(cfg.paths["scripts"]) / f"{slug}.json"
    if not script_path.is_file():
        raise SystemExit(f"No saved script at {script_path}")

    payload = json.loads(script_path.read_text(encoding="utf-8"))
    script = payload["script"]
    niche = payload.get("niche") or {}

    img_dir = Path(cfg.paths["images"]) / slug
    images = [
        str(p) for p in sorted(img_dir.glob("scene_*"))
        if p.suffix.lower() in _EXTS and not p.stem.endswith("_b")
    ]
    if not images:
        raise SystemExit(f"No scene images in {img_dir}")

    return script, niche, images


def _regenerate(critiques, script, niche, slug) -> None:
    """Re-generate the scenes whose subject the model could not find."""
    from pipeline.image_gen import build_style_token, generate_image, ImageGenError
    from pipeline.image_post import ensure_render_size

    targets = [c for c in critiques if "subject_missed" in c.flaws]
    if not targets:
        log.info("Nothing to regenerate — no scene missed its subject.")
        return

    video_id = int(slug.rsplit("_", 1)[-1]) if slug.rsplit("_", 1)[-1].isdigit() else 0
    style_token = build_style_token(video_id)
    width, height = (cfg.video.get("resolution") or [1080, 1920])[:2]
    images_dir = str(Path(cfg.paths["images"]) / slug)

    for critique in targets:
        i = critique.scene_index
        scene = script["scenes"][i]
        log.info("Regenerating scene %d (saw %r)", i, critique.what_it_shows)
        try:
            path = generate_image(
                image_prompt=scene["image_prompt"],
                niche=niche,
                output_dir=images_dir,
                scene_index=i,
                cfg=cfg,
                shot=scene.get("shot", ""),
                style_token=style_token,
                # A different seed: the same one reproduces the same miss.
                seed=video_id * 1000 + i + 7717,
            )
            ensure_render_size(path, width, height)
            log.info("  → %s", path)
        except ImageGenError as e:
            log.warning("  scene %d could not be regenerated: %s", i, e)

    log.info("Re-render with: python scripts/test_remotion_render.py --slug %s", slug)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug", help="run to audit")
    ap.add_argument("--latest", action="store_true", help="audit the most recent run")
    ap.add_argument("--regenerate", action="store_true",
                    help="re-generate scenes that missed their subject")
    ap.add_argument("--no-record", action="store_true",
                    help="report only; do not update prompt_notes.json")
    ap.add_argument("--notes", action="store_true",
                    help="print what the critic has learned so far and exit")
    args = ap.parse_args()

    if args.notes:
        print(summary())
        return

    if not args.slug and not args.latest:
        ap.error("pass --slug, --latest, or --notes")

    slug = args.slug or _latest_slug()
    log.info("Auditing %s", slug)

    script, niche, images = _load(slug)
    critiques = critique_run(
        script["scenes"], images, niche, cfg=cfg, record=not args.no_record,
    )

    if args.regenerate:
        _regenerate(critiques, script, niche, slug)

    if not args.no_record:
        print()
        print(summary())


if __name__ == "__main__":
    main()
